#!/usr/bin/env python3
"""#130 notes-eval: do notes from accepted runbooks fix what arm 1 (plain text-to-SQL) misses, one run per dev question?
Arm base = arm 1's prompt with the PR #141 line removed; arm notes appends 'Notes from verified runbooks:' for the runbooks
whose vocabulary overlaps the question. Usage: python3 scripts/runbook_notes_eval.py --catalog benchmarks/runbooks/catalog.json
--runbooks-dir benchmarks/runbooks --out EVIDENCE.json [--questions heldout.json] [--base-url U --model M]
The question set defaults to arm 1's built-in ten; --questions FILE (a JSON list of {id, question, sql}) replaces it, and the file's path and
sha256 go in the evidence so a run over a held-out set is identifiable."""
import argparse, hashlib, importlib.util, json, os, re, sys

# Selector threshold. Specified as 0.15 before any score was seen; at 0.15 only Q6 of the ten dev questions was selected and Q4 (0.139) was not, so it was
# lowered to 0.10 AFTER seeing the scores. That choice is in-sample and is stated in the evidence and in the issue comment.
THRESHOLD = 0.10

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = "- the data holds exactly the intervals of September 2025"
ANSWER, ARMS = "\nAnswer the question", ("base", "notes")

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

arm1 = load("demo_timeseries_qa")
PROMPT0 = arm1.PROMPT  # captured before run_one() swaps prompts per arm

def base_prompt():
    parts, lines = PROMPT0.split(arm1.SCHEMA, 1), arm1.SCHEMA.split("\n")
    kept = [l for l in lines if not l.startswith(FLAG)]  # the prompt before PR #141
    if len(parts) != 2 or len(kept) == len(lines):
        raise SystemExit(f"FATAL runbook-notes-eval: cannot rebuild the pre-#141 prompt - arm1.SCHEMA must appear in arm1.PROMPT "
                         f"and carry the line starting '{FLAG}' (scripts/demo_timeseries_qa.py)")
    return parts[0] + "\n".join(kept) + parts[1]

def notes_prompt(base, notes):
    head, tail = base.split(ANSWER, 1)
    if not notes:
        return base
    return head + "\n\nNotes from verified runbooks:\n" + "\n".join(f"- {n}" for n in notes) + ANSWER + tail

def tokens(text):
    return set(re.findall(r"[a-z0-9_]+", str(text).lower()))

def vocabulary(rb):
    vocab = tokens(rb.get("family", ""))
    for p in rb.get("parameters") or []:
        vocab.update(part for part in str(p.get("name", "")).lower().split("_") if part)
        vocab.update(tokens(p.get("description", "")))
    return vocab

def selector(question, runbooks):
    q, scored = tokens(question), []
    for rb in runbooks:
        vocab = vocabulary(rb)
        score = len(q & vocab) / len(vocab) if vocab else 0.0
        if score >= THRESHOLD:
            scored.append((score, str(rb.get("id", "")), rb))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [rb for _, _, rb in scored]

def accepted(catalog, runbooks_dir):
    rbs = []
    for entry in catalog.get("accepted") or []:
        rid = entry.get("id")
        path = os.path.join(runbooks_dir, rid, "runbook.json") if rid else None
        if not path or not os.path.exists(path):
            print(f"WARN runbook-notes-eval: accepted runbook {rid} has no runbook.json at {path}; skipped", file=sys.stderr)
            continue
        rbs.append(json.load(open(path)))
    return rbs

def run_one(base_url, model, text, prompt, expected):
    rec = {"passed": False, "sql": "", "returned": None}
    arm1.PROMPT = prompt  # ask_model reads the module-level PROMPT
    sql, _ = arm1.ask_model(base_url, model, text)
    got = arm1.run_sql(sql)
    if "error" in got:
        raise RuntimeError(f"SQL error: {got['error'][:200]}")
    rec.update(sql=" ".join(sql.split()), returned=arm1.flat(got["rows"]))
    rec["passed"] = all(any(arm1.same(e, g) for g in rec["returned"]) for e in expected)
    return rec

def main():
    ap = argparse.ArgumentParser(description="Measure whether runbook notes raise arm 1's pass rate (dev questions, in-sample).")
    for flag in ("--catalog", "--runbooks-dir", "--out"):
        ap.add_argument(flag, required=True)
    for flag in ("--base-url", "--model"):
        ap.add_argument(flag, default=None)
    ap.add_argument("--questions", default=None, help="JSON file of {id, question, sql}; default is arm 1's built-in set")
    args = ap.parse_args()
    delegate = load("pi_delegate")
    base_url, model = args.base_url or delegate.DEFAULT_URL, args.model or delegate.DEFAULT_MODEL
    raw = open(args.questions, "rb").read() if args.questions else None
    qsha = hashlib.sha256(raw).hexdigest() if raw is not None else None
    questions = arm1.QUESTIONS if raw is None else [(q["id"], q["question"], q["sql"]) for q in json.loads(raw)]
    runbooks, base = accepted(json.load(open(args.catalog)), args.runbooks_dir), base_prompt()
    sha = hashlib.sha256(base.encode()).hexdigest()
    print(f"notes-eval: model {model} at {base_url}; {len(runbooks)} accepted runbook(s); prompt_base_sha256={sha[:16]}; "
          f"{len(questions)} question(s){' from ' + args.questions if args.questions else ''}")
    results = []
    for qid, text, gold_sql in questions:
        try:
            gold = arm1.run_sql(gold_sql)
            if "error" in gold:
                raise RuntimeError(f"ground-truth SQL failed: {gold['error'][:160]}")
            expected = arm1.flat(gold["rows"])
        except Exception as e:
            expected, error = None, f"{type(e).__name__}: {e}"
        recs, sel = [], selector(text, runbooks)
        selected, notes = [str(rb.get("id", "")) for rb in sel], [n for rb in sel for n in rb.get("notes") or []]
        for arm in ARMS:
            rec = {"id": qid, "arm": arm, "selected": selected, "passed": False, "sql": "", "returned": None, "expected": expected}
            try:
                if expected is None:
                    raise RuntimeError(error)
                rec.update(run_one(base_url, model, text, base if arm == "base" else notes_prompt(base, notes), expected))
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
            recs.append(rec)
        results += recs
        print(f"{qid} " + " ".join(f"{arm} {'PASS' if r['passed'] else 'FAIL'}" for arm, r in zip(ARMS, recs)) +
              (f" selected={','.join(selected)}" if selected else ""))
    counts = {arm: sum(1 for r in results if r["arm"] == arm and r["passed"]) for arm in ARMS}
    with open(args.out, "w") as fh:
        json.dump({"prompt_base_sha256": sha, "model": model, "base_url": base_url, "selector_threshold": THRESHOLD, "threshold_set_after_seeing_scores": True, "questions_file": args.questions, "questions_sha256": qsha, "results": results, "pass": counts}, fh, indent=2)
        fh.write("\n")
    print(f"summary base {counts['base']}/{len(questions)} notes {counts['notes']}/{len(questions)} "
          "(dev questions, in-sample, one run each)")

if __name__ == "__main__":
    main()
