# Auto Ontology: 60 s model timeout (#126)

**Symptom.** Arm 2 (Auto Ontology) timed out on nearly every question at 600 s and again at 1800 s.

**Cause (observed in the backend and llama-swap logs).** The backend builds `ChatNVIDIA` without a `timeout`, so each model call has a 60 s read timeout. The call passes it explicitly (`session.post(timeout=60)`), which beats the backend's own `LLM_INVOKE_TIMEOUT_S` default set with `kwargs.setdefault`. A local model that takes longer than 60 s is disconnected (llama-swap: `POST /v1/chat/completions 499 ... 1m0.04s`, with 1,650-1,700 tokens generated at ~28 tokens/s), retried three times, then retried on the reasoning fallback (the same model).

**Workaround without a fork.** `kube/ao-shim/sitecustomize.py` is mounted read-only at `/opt/wattson-shim` and put on `PYTHONPATH`. With `WATTSON_LLM_READ_TIMEOUT_S` (900 in the pod spec) it raises the timeout of `/chat/completions` POSTs to at least that value; other calls are untouched. `LLM_INVOKE_TIMEOUT_S` is also 900 in the pod spec.

**Observation.** `just ao-shim-check` posts an aborted chat-completions request in each container and requires the timeout it would have sent to be at least 600 s.

**Upstream.** NVIDIA does not accept patches, so the shim is ours to carry. If the image digest changes, re-run `just ao-shim-check`.

## Second cause, found after the shim (validated by a pi agent run, M1 and M2)

With the shim live (`PASS ao-shim-check` twice, no `ReadTimeout` lines in the backend log), model calls now complete (llama-swap: `POST /v1/chat/completions 200`, 38 KB responses in about 150 s) but the question-extraction step still returns nothing: the backend logs `LLM returned None for QuestionExtractionModel` and retries, and M1 still failed at 900 s after 3 agent steps. The reasoning-style local model (`nex-n2.5-mini`, which the backend itself warns is "not known to support structured output") emits long free text instead of the structured result the backend parses.

So the 60 s client timeout was real and is fixed, but it was hiding a model-fit problem: this stack needs a model that reliably returns structured output within a bounded number of tokens. Candidates: a non-thinking instruct model served with a JSON/grammar constraint, or a small model fine-tuned for the backend's schemas. That is a model-selection experiment, not a client fix.
