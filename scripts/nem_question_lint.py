#!/usr/bin/env python3
"""Mechanical admission checks for a NEM benchmark question set (runs inside the wattson-nem container, no network, no model).

  python nem_question_lint.py --questions Q.json --claims C.json [--schema S.txt]

Checks, each a deterministic observation that prints PASS or FAIL (REQ-012):
  1. claims      every executable claim about the data returns true (a schema sentence with no passing claim does not ship)
  2. agreement   `sql` and the independently written `sql_alt` give the same answer
  3. mutants     each plausible wrong reading must give a DIFFERENT answer, or the question cannot detect that error (must_differ=false is declared, with a reason)
  4. margin      the winner beats the runner-up by more than the rounding unit x 10 (integers: not tied), so rounding cannot flip it
  5. scorer      the truth passes; an unrounded truth passes; a 200-row dump containing the truth fails; a wrong value fails
  6. schema      the schema text states every fact the claims depend on it to state (keywords listed in the claims file are not needed: schema is checked for the words 'net' and 'one row per DUID')
"""
import argparse
import glob
import json
import os
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nem_scoring import score  # noqa: E402


def connect():
    con = duckdb.connect()
    for path in sorted(glob.glob("/nemosis/*.parquet")):
        con.execute(f"CREATE TEMP VIEW {os.path.basename(path)[:-8].lower()} AS SELECT * FROM read_parquet('{path}')")
    return con


def rows(con, sql):
    return [list(r) for r in con.execute(sql).fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--claims", required=True)
    ap.add_argument("--schema", default=None)
    args = ap.parse_args()
    con = connect()
    failures = []

    def check(ok, label):
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            failures.append(label)

    for c in json.load(open(args.claims)):
        got = rows(con, c["sql"])
        check(got == [[True]], f"claim: {c['claim'][:110]}")

    if args.schema:
        text = open(args.schema).read().lower()
        check("net" in text and "negative" in text, "schema states that TOTALCLEARED can be negative and that cleared energy is NET")
        check("one row per duid" in text, "schema states dudetailsummary has one row per DUID")

    for q in json.load(open(args.questions)):
        qid, dec = q["id"], q.get("decimals")
        truth = rows(con, q["sql"])
        alt = rows(con, q["sql_alt"])
        ok_agree = bool(truth) and bool(alt) and score(truth[0], alt, dec)[0] and score(alt[0], truth, dec)[0]
        check(ok_agree, f"{qid} agreement: sql={truth[:1]} sql_alt={alt[:1]}")
        if not truth:
            continue
        for m in q["mutants"]:
            wrong = rows(con, m["sql"])
            differs = not (wrong and score(truth[0], wrong, dec)[0])
            if m["must_differ"]:
                check(differs, f"{qid} mutant '{m['name']}' gives a different answer ({wrong[:1]})")
            else:
                print(f"INFO {qid} mutant '{m['name']}' is declared insensitive: {m['why']} ({'differs' if differs else 'same answer'}: {wrong[:1]})")
        if q.get("margin_sql"):
            top = [r[0] for r in rows(con, q["margin_sql"])]
            gap = abs(top[0] - top[1]) if len(top) > 1 else float("inf")
            need = 10 * 10 ** (-dec) if dec is not None else 1
            check(gap >= need if dec is not None else gap > 0, f"{qid} margin: winner {top[0]} vs runner-up {top[1] if len(top) > 1 else None} gap {gap:.4g} (need >= {need:.4g})")
        t = truth[0]
        nums = [i for i, v in enumerate(t) if isinstance(v, (int, float))]
        unrounded = list(t)
        wrong_row = list(t)
        if nums:
            step = 0.4 * 10 ** (-dec) if dec is not None else 0.0
            for i in nums:
                unrounded[i] = t[i] + step
                wrong_row[i] = t[i] + (1.0 if dec is None else 3 * 10 ** (-dec) * 10)
        dump = [[f"X{i}", i * 1.0] for i in range(200)]
        dump[150] = list(t)
        check(score(t, [t], dec)[0], f"{qid} scorer: the truth passes")
        if nums:
            check(score(t, [unrounded], dec)[0], f"{qid} scorer: an unrounded correct answer passes")
            check(not score(t, [wrong_row], dec)[0], f"{qid} scorer: a wrong value fails")
        check(not score(t, dump, dec)[0], f"{qid} scorer: a 200-row dump containing the truth fails")

    verdict = "PASS" if not failures else "FAIL"
    print(f"{verdict} nem-question-lint: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
