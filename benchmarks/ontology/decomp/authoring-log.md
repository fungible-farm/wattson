RECONSTRUCTED LOG - no live authoring log was kept for this candidate. Every timestamp below is a filesystem mtime
(UTC, taken with `stat`/`date -u -r` at freeze time 2026-11-10T03:29:40Z); the local timezone is UTC+11, so 02:20Z is
13:20 local. An mtime is the moment a file was last written, not the start or end of the thinking that produced it.
Read the caveats at the bottom before using authoring_minutes.

earliest mtime of any frozen file (scripts/ontology_eval/decomp_setup.py) 2026-11-10T02:20:00Z
views.sql written 2026-11-10T02:20:51Z (same minute as decomp_setup.py - the two are written together: setup runs this file)
adapter_decomp.py last edit 2026-11-10T03:12:27Z (out/nem/nem.duckdb is touched 03:13:00Z, consistent with
  decomp_setup.py being run against the container right after that edit; the mtime does not prove a scored dev run)
gold-specs.json last written 2026-11-10T03:22:30Z - i.e. in the freeze session itself, ~7 min before the tag, not earlier
freeze (frozen_at in FREEZE.json) 2026-11-10T03:29:40Z

authoring_minutes = 70 = span first frozen-file mtime (02:20:00Z) -> frozen_at (03:29:40Z).

CAVEATS (why this is weaker evidence than the malloy arm's log):
- Session structure: authoring proceeded across several pi sessions interrupted by context-overflow restarts. The count
  and duration of those restarts are operator-reported, not measured here - there are no pi session logs for this
  worktree under ~/.pi/agent/sessions, so a restart could be hiding in any of the ~52 min gap between the views.sql
  mtime (02:20:51Z) and the adapter_decomp.py mtime (03:12:27Z).
- The 70 min is a file-touch window, not measured model time. It mixes writing, container/data work and queue or restart
  waits, and cannot be decomposed.
- It also under-counts: predecessor scratch work in this worktree predates the first frozen mtime (out/nem/nem.duckdb
  and out/nemosis/*.parquet 2026-11-10T01:34Z, scratch out/nem/vtest.duckdb 01:45Z, benchmarks/ontology/ dir created
  01:54Z), roughly 26-46 min of data prep and exploration that never touched a frozen file and is excluded above.
- No run results are recorded because none were captured: this arm has no logged dev run, no accuracy numbers and no
  parameter-change history. The malloy arm logs run1 5/4/4 and a max_tokens raise; there is no equivalent evidence for
  the decomp arm, so none is claimed here.
- Chain-of-thought for the gold specs is not recoverable from mtimes. gold-specs.json carries only its own _readme
  claim (plans written from the question text plus the views.sql column comments, never from model output); mtime alone
  cannot confirm that, so treat the Path B provenance claim for this arm as asserted, not evidenced.

2026-11-10T03:47Z (refreeze, supersedes the tag freeze/decomp) — first dev run of this arm and the defect it found.
The first dev run (--repeats 3 over the 10 development questions) was killed at ~10 minutes, in rep2 of 3. rep0 scored
1/10 correct: nine of ten questions returned 'no rows' at a uniform ~48 s each, the flat latency being the signature of a
fixed ceiling rather than real planning work. Cause, measured from the run: scripts/ontology_eval/adapter_decomp.py _chat
sent max_tokens 2000, and the reasoning model consumed that budget thinking and returned empty content, so parse_plan saw an
empty reply and every one of those nine failed at the plan stage. This supersedes the caveat above that "this arm has no
logged dev run" — a run exists; its 1/10 is a token-budget artifact, not a score for the candidate.
Fix applied (2026-11-10T03:47Z, this commit): max_tokens 2000 -> 8000 and urlopen timeout 280 -> 290 — the same defect the
metricflow (3000 -> 8000), bsl (3000 -> 8000) and malloy (2500 -> 8000) arms had already fixed the same way. _chat now also
records finish_reason in the module-level LAST_FINISH, and answer() returns an explicit note 'empty reply,
finish_reason=length (model budget exhausted)' when the reply is empty and finish_reason was 'length', instead of the
misleading 'no usable plan (no JSON object in the reply)'. Model, prompt, views.sql, decomp_setup.py and gold-specs.json are
unchanged; FREEZE.json now lists the new sha256 for scripts/ontology_eval/adapter_decomp.py (old
281a2bace0f7da061f618bc975c8f5a79ed41a55b2ce613a3571c1fe91ea076a -> new
4f088674c60c8e3fb93d0497a1553f81395b244488f50f9bd3a1d0bd48fb6838). The tag freeze/decomp points at the 2000-token adapter and
is superseded: the held-out run must use freeze/decomp2, since --verify-freeze compares the listed hashes against the working
tree. Post-fix dev rerun numbers are not recorded here yet; --selftest is unchanged by the fix (same two pre-existing
parse_plan assertion failures in the refuse/clarify cases before and after — the test asserts parse_plan returns None for
those, while answer() is what turns a refuse/clarify dict into a note).
