"""Run inside an Auto Ontology container (`podman exec -i <c> python - < scripts/ao_shim_check.py`): print the timeout a
chat-completions POST sent with timeout=60 would actually use. No network: the HTTP adapter is replaced before anything is sent."""
import requests
from requests.adapters import HTTPAdapter

seen = {}


def send(self, request, **kw):
    seen["t"] = kw.get("timeout")
    raise RuntimeError("stop")


HTTPAdapter.send = send
try:
    requests.Session().post("http://x.invalid/v1/chat/completions", json={}, timeout=60)
except RuntimeError:
    pass
print(seen["t"])
