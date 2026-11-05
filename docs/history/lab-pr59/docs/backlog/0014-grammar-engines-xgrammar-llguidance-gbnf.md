# 0014 — Grammar engines: xgrammar for vLLM, GBNF or llguidance for llama.cpp

- **Status:** decided 2026-10-23. Review idea 5 ([review](reviews/2026-10-23-r6-merit-review.md)).

## Decision
**xgrammar is kept, in sync with vLLM.** It is not sunset, because it measurably works. llguidance is the path for llama.cpp, if grammar-constrained llama.cpp serving is ever needed.

## Measured facts
- **vLLM uses xgrammar.** The pinned vLLM 0.31 image bundles xgrammar 0.2.7 (Apache-2.0) as its default structured-output backend. Its `qwen3_coder` tool parser builds xgrammar's model-native Qwen-XML structural tag per request. That is what the strict-tool-calling arm (0011) depends on.
- **vLLM's llguidance backend can't replace it.** The same image bundles llguidance 1.7.6 (upstream: MIT, [guidance-ai/llguidance](https://github.com/guidance-ai/llguidance), tag v1.9.1). That backend accepts only the legacy structural-tag shape (`triggers`/`structures` with JSON-schema bodies), so it can't express the Qwen-XML parameter format. Switching vLLM to it would lose native-format strict tool calling.
- **llama.cpp uses GBNF, with LLGuidance as an option.** llama.cpp constrains with GBNF and JSON schema, and LLGuidance is an optional build (`-DLLAMA_LLGUIDANCE=ON`). The llama-swap `llama-server` here was built without it (no LLGuidance strings in the binary). llama.cpp serves GGUF models on shinyDGX (OPEN-001) and is not part of the PowerCoder study.
- **xgrammar is heavy outside vLLM.** Importing it client-side pulls in torch, transformers and triton.

## Keeping xgrammar in sync
xgrammar is pinned by the inference image's ID, not by this repo. When the image changes, re-run the Qwen-XML check on the new image: does `"required"` still produce native XML? Then update this item and the b00t lesson (`b00t learn xgrammar`).
