"""Runtime shim for NVIDIA Auto Ontology (mounted read-only; the upstream code and image are not modified).

Why: the backend builds langchain-nvidia's ChatNVIDIA without a `timeout`, so every model call gets a 60 s read timeout that
overrides the backend's own LLM_INVOKE_TIMEOUT_S session default (`session.post(timeout=60)` beats `kwargs.setdefault`).
A local model that needs longer than 60 s is disconnected (llama-swap logs `499 ... 1m0.0s`), retried three times, then retried
on the "reasoning" fallback, so the question never completes. See docs/ao-llm-timeout.md.

What: raise the timeout of chat-completion POSTs to at least WATTSON_LLM_READ_TIMEOUT_S (unset or 0 leaves behaviour unchanged).
Only URLs ending in /chat/completions are touched, so embedding, rerank and health calls keep their own timeouts.
Loaded by Python itself because this directory is on PYTHONPATH; a failure here must never stop the service.
"""
import os

try:
    import requests

    _floor = float(os.environ.get("WATTSON_LLM_READ_TIMEOUT_S", "0") or 0)
    if _floor > 0:
        _original = requests.Session.request

        def _request(self, method, url, **kwargs):
            timeout = kwargs.get("timeout")
            if str(url).rstrip("/").endswith("/chat/completions") and isinstance(timeout, (int, float)) and timeout < _floor:
                kwargs["timeout"] = _floor
            return _original(self, method, url, **kwargs)

        requests.Session.request = _request
        requests.Session._wattson_floor = _floor
except Exception:  # noqa: BLE001 - observability shim, never fatal
    pass
