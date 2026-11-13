# Ontology candidate: NVIDIA Auto Ontology (ao): BLOCKED, no development run

Status: **no question was answered; configuration-only fix not found; NOT frozen (no FREEZE.json, no tag).** All numbers below are measurements from this branch; nothing from the harness SUMMARY exists because the adapter never obtained a result row.

## Versions, licence, install
- Auto Ontology pinned commit `2a24f5d34ef6b6e168a7f22d308504d3ab36a198` (justfile `ao_sha`), Apache-2.0, `.tools/auto-ontology/LICENSE` sha256 `f30c028cc160cd27972bf845c9e44c703201903d226cecaf827b39fbf8f61d5e` (the image contains no LICENSE file; `/app/pyproject.toml` only carries the SPDX header).
- Install: `just ao-fetch; just ao-build` (129 s wall here, layers partly cached), `just retrieval-up; just embed2048-up; WATTSON_EMBED_PORT=8083 WATTSON_EMBED_MODEL=jina-embeddings-v4 WATTSON_CHAT_MODEL=qwen3.8-flash-next just ao-up; just ao-shim-check (PASS); just ao-load-nemosis; just ao-enable-semantic`.
- Bring-up wall time (retrieval-up + embed2048 download 1.84 GB + ao-up healthy): 195 s measured to the end of the first `ao-up` health pass (build overlapped). Semantic compile: 819.5 s (5 tables, 0 semantic FK edges, 0 SqlAttributes).
- Image 942 MB (942354620 B), arm64; pod memory at idle after compile: postgres 124 MB, backend 559 MB, ingestion 248 MB (podman stats); embed2048 2.38 GB, rerank 339 MB, embed 96 MB.
- Model calls per question: only one partial question was observed (M1). Agent nodes executed in the backend log (`benchmarks/ontology/ao/attempt1-backend.log`): question_intent 2, question_extraction 2, candidate_retrieval 1, candidate_preparation 1, evidence_refinement 1, sql_from_semantic 1 (+3 immediate retries), sql_unconstructable 1. Not an exact model-call count (AO does not log one per call).
- Seed: cannot be sent; ChatNVIDIA calls are built inside the backend. `PROMPT` in the adapter states this.

## Gate evidence (scripts/ontology_eval/gates.sh, verbatim)
```
host_arch=aarch64  podman_rootless=true
image=localhost/auto-ontology:2a24f5d34ef6b6e168a7f22d308504d3ab36a198 id=fb7043c6a5b754e5a5df955caad164eb718d2dc98149eaed58f4fde5d05218c5 arch=arm64/linux size_bytes=942354620
--- rootless, offline run (--network=none, uid/gid mapped to the invoking user)
uid=1000 machine=aarch64 interfaces=lo
--- licence file /app/pyproject.toml   (no LICENSE in image)
13db3800f6ffcb5f422e02f5a96c11928b1382ed607c05dbdc02675371c424e9  /app/pyproject.toml
--- pinned dependencies (subset, importlib.metadata): fastapi==0.141.1 langchain-core==1.6.1 langchain-nvidia-ai-endpoints==1.4.3 langchain-openai==1.3.4 langchain-postgres==0.0.17 openai==2.44.0 psycopg==3.3.4 psycopg-pool==3.3.1 pydantic==2.13.5 uvicorn==0.52.4
```
Other images (podman image inspect): pgvector `sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8` arm64; llama.cpp `ghcr.io/ggml-org/llama.cpp@sha256:c0a11067c72bb1eb330b8bddbf9869274c195e596bf4fae55fd527102e644b85` (arch not re-inspected; runs on this aarch64 host); pause 3.10. Postgres data is ephemeral (emptyDir).

## Attempts (configuration only)
1. **WATTSON_CHAT_MODEL=qwen3.8-flash-next.** Previously documented failure (extraction returning None on nex-n2.5-mini) did NOT recur: `question_extraction` succeeded (log: "Extracted question:"). Two other findings: (a) each reasoning call takes 1 to 4 minutes (question_intent 125 s, extraction ~4 min, evidence_refinement 156 s) with the model shared by other agents (llama-swap showed 2 to 3 busy slots at 20 to 28 t/s), so a question takes >12 min against the harness 300 s limit; (b) `sql_from_semantic` fails instantly 3 times:
   `[500] ... raise_exception('System message must be at the beginning...  Jinja Exception: System message must be at the beginning.` (backend log line 249 of `attempt1-backend.log`). Cause: AO sends system, user, system, user; the Qwen3.8 chat template rejects a system message after a user message. Confirmed outside AO with curl against llama-swap: interleaved system/user -> HTTP 500 with that error; system messages first -> HTTP 200 `"ok"`. AO has this reordering (`_system_messages_first` in `utils/llm_invoke.py`) but enables it only when the model NAME contains `claude` or `anthropic` (`_is_claude`). Result: stage `compile` fails for every question; AO ends `unconstructable` ("LLM failed to produce a response").
2. **granite-4.2-8b.** llama-swap routes it to `proxy: http://127.0.0.1:18102` with `cmd: sleep infinity`; nothing listens (curl: 000). Not startable by me (host service). **Side effect I caused:** my probe left llama-swap in state `starting granite` and blocked all models ~15 min (other agents saw 499s). I cleared it with `GET /unload`, after which flash-next reloaded. Flagging loudly.
3. **Not run.** Remaining config-only levers both need a change to the shared llama-swap config (an alias whose name contains `anthropic`/`claude`, e.g. `anthropic/qwen3.8-flash-next`, which would trigger AO's own reorder but also forces `method=function_calling, tool_choice=None`; or a relaxed `--chat-template-file`). I did not edit the shared service config. qwen3.8-27b-uncensored shares the template family and loading it evicts flash-next for everyone; not tried.

## Verdict on the boundary
Not 'requires a fork': a one-line alias in llama-swap (model side) is the candidate fix, untested. But AO also needs a 2 to 4 minute per call reasoning budget cut (thinking disabled) to fit 300 s, which is a model-serving change for all users of flash-next.

## Requirements (development, M1 only)
R1 NOT-SHOWN (AO builds terms by LLM; compile gave 0 FK edges). R2/R3 NOT-SHOWN. R4/R5 NOT-SHOWN (no run). R6 NOT-SHOWN. R9 PASS (arm64, rootless; gate output above). R10 FAIL (per-question >12 min observed; M1 not completed). R11: compile 819 s for 5 tables / 153k rows.
Harness: Path B not run (no spec layer). Failure stage for M1: compile (sql_from_semantic). Candidate retrieval for M1 also omitted `dispatchload` (tables from candidates: dudetailsummary, dispatchinterconnectorres, dispatchprice, dispatchregionsum), i.e. a represent-layer defect; M7/M8 and all others: not run.

## Files
`scripts/ontology_eval/adapter_ao.py` (adapter: SSE ask, rows from sql_response_from_db, stage and node counts from the backend log), `benchmarks/ontology/ao/attempt1-backend.log`, `authoring-log.md`.
