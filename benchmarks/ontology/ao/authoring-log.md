# AO authoring log (UTC)
- 2026-11-09 ~14:06 local (04:06Z) start: worktree, nemosis fetch, image build (129 s), retrieval/embed2048 up
- 03:10Z pod up (ao-up), data loaded, semantic compile 03:12Z-03:26Z (819.5 s, 5 tables, 0 FK edges, 0 SqlAttribute)
- 03:26Z-03:41Z attempt 1 (WATTSON_CHAT_MODEL=qwen3.8-flash-next): M1 fails at sql_from_semantic (Jinja 500)
- ~03:50Z attempt 2 (granite-4.2-8b): backend `sleep infinity`/:18102 not running; wedged llama-swap; cleared with GET /unload
- ~04:00Z root cause confirmed with curl (interleaved system messages -> 500; system-first -> 200). Stopped; no config produced. No FREEZE.
