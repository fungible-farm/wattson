#!/usr/bin/env python3
"""Common harness for comparing ontology / semantic-layer candidates on the same questions, scorer and model (#128).

  python3 scripts/ontology_eval.py --adapter scripts/ontology_eval/adapter_arm1.py --out out/eval/arm1 [--repeats 3] [--timeout 300] [--only M1,M2]

An adapter is a Python file defining `answer(question: str) -> dict` with keys
  rows     list of rows (lists) the candidate's own execution path returned, or None/[] when it declined to answer
  query    the query/spec it executed (audit trail), string
  calls    number of model calls it made for this question (int, 0 if unknown)
  note     free text: refusal reason, clarification or hint, error
  stage    optional, where it stopped: "plan" (model/interface), "represent" (concept not in the semantic model), "compile", "execute"
and optionally `setup()` / `teardown()`, and `run_spec(spec) -> dict` (same keys) which runs a GOLD query specification through the
candidate's own compiler and executor WITHOUT any model call (Path B, see --gold-specs).

Ground truth: each question's `sql` run in the no-network DuckDB container (scripts/demo_timeseries_qa.run_sql), then the strict scorer
`nem_scoring.score` (<= 3 rows, one row holds every expected value, tolerance = half the declared rounding unit).
Adversarial questions (configs/nem-adversarial-questions.proposed.json) PASS only if NO rows are returned.
Outcomes per answer: correct | wrong (answered, scored fail) | refused (no rows, no error) | error (timeout/exception). Coverage = answered / asked,
conditional accuracy = correct / answered: a candidate that refuses everything has coverage 0, not reliability. On answerable questions a refusal is a coverage
miss; on adversarial questions a refusal is the legitimate outcome and an answer is the failure.
Two-path diagnostic (`--gold-specs FILE|sql`): Path A is question -> model -> semantic query -> SQL; Path B hands the exact intended specification straight to the
compiler. B passes and A fails: model planning or interface. B fails: semantic representation (not expressible), compilation, execution or the adapter.
Rows that fail the strict scorer but contain every expected value within 1% are flagged as the scoring layer.
Recorded: per-question verdict, wall time, calls, query, a sha256 of the canonical rows (replay stability across repeats), then
accuracy per repeat, latency p50/p95 and the adversarial pass rate. A measurement, not a gate: exit status is always 0 unless the harness itself fails.
"""
import argparse
import collections
import hashlib
import importlib.util
import json
import os
import signal
import statistics
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402
from nem_scoring import score  # noqa: E402


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def load_adapter(path):
    spec = importlib.util.spec_from_file_location("adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_hash(rows):
    if not rows:
        return None
    norm = sorted(json.dumps([round(v, 6) if isinstance(v, float) else v for v in row], default=str) for row in rows)
    return hashlib.sha256("\n".join(norm).encode()).hexdigest()[:12]


def call(adapter, question, timeout):
    started = time.time()
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        out = adapter.answer(question) or {}
        error = None
    except Timeout:
        out, error = {}, f"timeout after {timeout} s"
    except Exception as e:  # noqa: BLE001 - the verdict records it
        out, error = {}, f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
    return out, error, time.time() - started


def run_metadata(adapter_path, adapter, args):
    """What can still vary between runs even at temperature 0: serving configuration, model file, seed, prompts, code."""
    import urllib.request
    base = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8000/v1")
    meta = dict(model=os.environ.get("EVAL_MODEL", ""), seed=os.environ.get("EVAL_SEED", "0"), base_url=base,
                adapter_sha256=hashlib.sha256(open(adapter_path, "rb").read()).hexdigest()[:12],
                questions_sha256=hashlib.sha256(open(args.questions, "rb").read()).hexdigest()[:12],
                prompt_sha256=(hashlib.sha256(str(getattr(adapter, "PROMPT", "")).encode()).hexdigest()[:12] if getattr(adapter, "PROMPT", None) else "adapter-declares-none"))
    try:
        meta["git"] = os.popen(f"git -C {REPO} rev-parse --short HEAD").read().strip()
        running = json.load(urllib.request.urlopen(base.rsplit("/v1", 1)[0] + "/running", timeout=5)).get("running", [])
        meta["serving"] = [dict(model=x.get("model"), cmd=" ".join(str(x.get("cmd", "")).split())[:600]) for x in running]
    except Exception as e:  # noqa: BLE001 - metadata is best effort and says so
        meta["serving"] = f"unavailable: {type(e).__name__}"
    return meta


def loose_has_all(expected_row, row):
    unused = list(row)
    for want in expected_row:
        hit = next((i for i, got in enumerate(unused) if (isinstance(want, (int, float)) and isinstance(got, (int, float)) and abs(want - got) <= 0.01 * max(1.0, abs(want)))
                    or (not isinstance(want, (int, float)) and str(want).strip().lower() == str(got).strip().lower())), None)
        if hit is None:
            return False
        unused.pop(hit)
    return True


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))] if values else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--questions", default=os.path.join(REPO, "configs", "nem-multitable-questions.v2.proposed.json"))
    ap.add_argument("--adversarial", default=os.path.join(REPO, "configs", "nem-adversarial-questions.proposed.json"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--only", default="")
    ap.add_argument("--vary-seed", action="store_true", help="use seed = EVAL_SEED + repeat index, so repeats are separate samples (at temperature 0 with one seed, repeats are nearly identical)")
    ap.add_argument("--verify-freeze", default="", help="FREEZE.json {files: {path: sha256}, authoring_minutes, frozen_at}: refuse to run if any frozen semantic-model file changed")
    ap.add_argument("--gold-specs", default="", help="JSON {id: spec | {inexpressible: reason}} for Path B, or the word 'sql' to use each question's own SQL")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    freeze = None
    if args.verify_freeze:
        freeze = json.load(open(args.verify_freeze))
        changed = [p for p, h in freeze["files"].items() if hashlib.sha256(open(os.path.join(REPO, p), "rb").read()).hexdigest() != h]
        if changed:
            sys.exit(f"FAIL ontology-eval: frozen files changed after the freeze: {changed}")
        print(f"freeze verified: {len(freeze['files'])} files unchanged since {freeze.get('frozen_at')}, authoring {freeze.get('authoring_minutes')} min", flush=True)
    adapter = load_adapter(args.adapter)
    meta = run_metadata(args.adapter, adapter, args)
    meta["freeze"] = dict(frozen_at=freeze.get("frozen_at"), authoring_minutes=freeze.get("authoring_minutes"), files=len(freeze["files"])) if freeze else "not frozen"
    print("METADATA " + json.dumps(meta), flush=True)
    if hasattr(adapter, "setup"):
        adapter.setup()
    only = [x for x in args.only.split(",") if x]
    questions = [q for q in json.load(open(args.questions)) if not only or q["id"] in only]
    adversarial = [q for q in json.load(open(args.adversarial)) if not only or q["id"] in only]
    truth = {}
    for q in questions:
        r = arm1.run_sql(q["sql"])
        if "error" in r or not r["rows"]:
            sys.exit(f"FAIL ontology-eval: ground truth for {q['id']} failed: {r.get('error', 'no rows')}")
        truth[q["id"]] = r["rows"][0]
    print("ground truth: " + "; ".join(f"{k}={v}" for k, v in truth.items()), flush=True)

    records, accuracy, latencies, hashes = [], [], [], {}
    base_seed = int(os.environ.get("EVAL_SEED", "0"))
    for rep in range(args.repeats):
        if args.vary_seed:
            os.environ["EVAL_SEED"] = str(base_seed + rep)
        passed = 0
        for q in questions:
            out, error, seconds = call(adapter, q["question"], args.timeout)
            rows = out.get("rows") or []
            ok, reason = score(truth[q["id"]], rows, q.get("decimals")) if not error else (False, error)
            outcome = "error" if error else ("refused" if not rows else ("correct" if ok else "wrong"))
            loose = bool(rows) and not ok and any(loose_has_all(truth[q["id"]], row) for row in rows)
            passed += ok
            latencies.append(seconds)
            hashes.setdefault(q["id"], []).append(canonical_hash(rows))
            records.append(dict(kind="answer", repeat=rep, id=q["id"], ok=ok, outcome=outcome, scoring_suspect=loose, stage=out.get("stage"), reason=reason, seconds=round(seconds, 1), calls=out.get("calls", 0),
                                query=str(out.get("query", ""))[:1500], note=str(out.get("note", ""))[:400], rows=rows[:3], hash=hashes[q["id"]][-1]))
            print(f"rep{rep} {q['id']:4} {'PASS' if ok else 'FAIL'} {seconds:6.1f}s calls={out.get('calls', 0)} {reason[:80]}", flush=True)
        accuracy.append(passed)
    adv_pass = 0
    for q in adversarial:
        out, error, seconds = call(adapter, q["question"], args.timeout)
        rows = out.get("rows") or []
        ok = not rows and not error
        adv_pass += ok
        adv_outcome = "error" if error else ("legitimate-refusal" if not rows else "answered")
        records.append(dict(kind="adversarial", id=q["id"], what=q["kind"], ok=ok, outcome=adv_outcome, seconds=round(seconds, 1), calls=out.get("calls", 0),
                            query=str(out.get("query", ""))[:600], note=str(out.get("note", error or ""))[:400], rows=rows[:3]))
        print(f"adv {q['id']:3} {'PASS' if ok else 'FAIL'} {seconds:6.1f}s rows={len(rows)} {q['kind'][:40]} :: {str(out.get('note', error or ''))[:80]}", flush=True)
    gold = {}
    if args.gold_specs == "sql":
        gold = {q["id"]: q["sql"] for q in questions}
    elif args.gold_specs:
        gold = json.load(open(args.gold_specs))
    path_b = {}
    if gold and hasattr(adapter, "run_spec"):
        for q in questions:
            spec = gold.get(q["id"])
            if isinstance(spec, dict) and "inexpressible" in spec:
                path_b[q["id"]] = dict(outcome="inexpressible", ok=False, why=spec["inexpressible"], stage="represent")
                continue
            if spec is None:
                path_b[q["id"]] = dict(outcome="no-gold-spec", ok=False, why="", stage=None)
                continue
            out, error, seconds = call(type("A", (), {"answer": staticmethod(lambda _q, s=spec: adapter.run_spec(s))}), "", args.timeout)
            rows = out.get("rows") or []
            ok, reason = score(truth[q["id"]], rows, q.get("decimals")) if not error else (False, error)
            loose = bool(rows) and not ok and any(loose_has_all(truth[q["id"]], row) for row in rows)
            path_b[q["id"]] = dict(outcome="correct" if ok else ("error" if error else "wrong"), ok=ok, why=reason[:160], stage=out.get("stage") or ("execute" if error else None),
                                   scoring_suspect=loose, query=str(out.get("query", ""))[:800], rows=rows[:3], seconds=round(seconds, 1))
            print(f"pathB {q['id']:4} {'PASS' if ok else 'FAIL'} {seconds:6.1f}s {reason[:80]}", flush=True)
    diagnosis = {}
    for q in questions:
        recs = [x for x in records if x["kind"] == "answer" and x["id"] == q["id"]]
        a_all, a_any = all(x["ok"] for x in recs), any(x["ok"] for x in recs)
        suspect = any(x["scoring_suspect"] for x in recs)
        b = path_b.get(q["id"])
        if a_all:
            layer = "none"
        elif suspect:
            layer = "scoring (the answer holds every expected value within 1%; the strict scorer rejected it)"
        elif b is None:
            layer = "not diagnosed (no Path B)"
        elif b["outcome"] == "inexpressible":
            layer = "semantic representation (not expressible in the candidate's model)"
        elif b["outcome"] == "no-gold-spec":
            layer = "not diagnosed (no gold spec)"
        elif b["ok"] and not a_any:
            layer = "model planning or interface (the gold spec passes)"
        elif b["ok"]:
            layer = "model planning, unstable (passes on some repeats)"
        elif b.get("scoring_suspect"):
            layer = "scoring"
        else:
            layer = {"compile": "compilation", "execute": "execution", "represent": "semantic representation"}.get(b.get("stage"), "compilation, execution or adapter (gold spec fails)")
        diagnosis[q["id"]] = layer
    cov = []
    for rep in range(args.repeats):
        rr = [x for x in records if x["kind"] == "answer" and x["repeat"] == rep]
        correct = sum(x["outcome"] == "correct" for x in rr)
        wrong = sum(x["outcome"] == "wrong" for x in rr)
        refused = sum(x["outcome"] == "refused" for x in rr)
        err = sum(x["outcome"] == "error" for x in rr)
        n = len(rr) or 1
        cov.append(dict(asked=len(rr), correct=correct, wrong=wrong, refused=refused, error=err, coverage=round((correct + wrong) / n, 3),
                        conditional_accuracy=round(correct / (correct + wrong), 3) if correct + wrong else None))
    stable = sum(1 for h in hashes.values() if len(set(h)) == 1 and h[0] is not None)
    # Report per dimension, never one aggregate: a single score would reward general SQL expressiveness and hide semantic correctness.
    by = {"shape": collections.defaultdict(lambda: [0, 0]), "convention": collections.defaultdict(lambda: [0, 0])}
    meta_q = {q["id"]: q for q in questions}
    for rec in records:
        if rec["kind"] == "answer":
            q = meta_q[rec["id"]]
            for dim, key in (("shape", q.get("shape", "unspecified")), ("convention", "tests-a-convention" if q.get("convention") else "no-convention")):
                by[dim][key][0] += rec["ok"]
                by[dim][key][1] += 1
    per_dim = {dim: {k: f"{v[0]}/{v[1]}" for k, v in sorted(d.items())} for dim, d in by.items()}
    failing = sorted({r_["id"] for r_ in records if r_["kind"] == "answer" and not r_["ok"]})
    unstable = sorted(i for i, h in hashes.items() if len(set(h)) > 1)
    summary = dict(adapter=os.path.basename(args.adapter), questions=len(questions), repeats=args.repeats, accuracy_per_repeat=accuracy,
                   accuracy_mean=round(statistics.mean(accuracy) / len(questions), 3) if questions else None,
                   latency_p50=round(pct(latencies, 0.5), 1), latency_p95=round(pct(latencies, 0.95), 1),
                   stable_answers=f"{stable}/{len(questions)}", adversarial_passed=f"{adv_pass}/{len(adversarial)}",
                   accuracy_by_dimension=per_dim, coverage_per_repeat=cov, failure_layer=diagnosis, path_b_passed=(f"{sum(1 for b in path_b.values() if b['ok'])}/{len(path_b)}" if path_b else "not run"), rerun_with_10_repeats=sorted(set(failing) | set(unstable)))
    json.dump(dict(metadata=meta, summary=summary, path_b=path_b, records=records), open(os.path.join(args.out, "results.json"), "w"), indent=1)
    print("SUMMARY " + json.dumps(summary), flush=True)
    if hasattr(adapter, "teardown"):
        adapter.teardown()


if __name__ == "__main__":
    main()
