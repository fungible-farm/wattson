#!/usr/bin/env python3
"""Deterministic upper bound on token use and dollar cost from existing run journals.

Reads every *.jsonl journal in RUN_DIR, sums prompt/completion/cached tokens and
model call counts, and prints an ESTIMATE upper bound in USD when prices are given.
Never gates pass/fail; always labelled an estimate.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="directory of run journals")
    parser.add_argument("--price-in", type=Decimal, default=None,
                        help="USD per 1M input tokens (e.g. 0.5)")
    parser.add_argument("--price-out", type=Decimal, default=None,
                        help="USD per 1M output tokens (e.g. 1.5)")
    parser.add_argument("--price-cached-in", type=Decimal, default=None,
                        help="USD per 1M cached input tokens (ignored in bound; full input rate used)")
    parser.add_argument("--cells", type=int, default=None,
                        help="target number of cells for scaling")
    parser.add_argument("--tasks-in-run", type=int, default=None,
                        help="tasks per run used for scaling")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir
    if not run_dir.is_dir():
        print(f"error: {run_dir} is not a directory", file=sys.stderr)
        return 2

    journals = sorted(run_dir.glob("*.jsonl"))
    if not journals:
        print(f"error: no *.jsonl journals in {run_dir}", file=sys.stderr)
        return 2

    tokens_in = 0
    tokens_out = 0
    cached_in = 0
    calls = 0
    attempts = len(journals)

    for jpath in journals:
        with jpath.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.rstrip("\n")
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"error: malformed JSON in {jpath.name}:{line_no}: {exc}", file=sys.stderr)
                    return 2
                if not isinstance(event, dict) or "kind" not in event:
                    continue
                if event["kind"] != "model_response":
                    continue
                payload = event.get("payload", {})
                usage = payload.get("usage", {})
                if not isinstance(usage, dict):
                    continue
                calls += 1
                tokens_in += int(usage.get("prompt_tokens", 0))
                tokens_out += int(usage.get("completion_tokens", 0))
                details = usage.get("prompt_tokens_details", {})
                if isinstance(details, dict):
                    cached_in += int(details.get("cached_tokens", 0))

    print(f"tokens_in={tokens_in} tokens_out={tokens_out} cached_in={cached_in} calls={calls} attempts={attempts}")

    if args.price_in is not None and args.price_out is not None:
        # Upper bound: assume no caching benefit; price cached tokens at full input rate
        cost_in = Decimal(tokens_in) * args.price_in / Decimal(1_000_000)
        cost_out = Decimal(tokens_out) * args.price_out / Decimal(1_000_000)
        bound = cost_in + cost_out
        print(f"ESTIMATE upper_bound_usd={bound.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)} "
              "(no prompt caching assumed; cached tokens priced at the full input rate in this bound)")

        if args.cells is not None:
            if args.tasks_in_run is None or args.tasks_in_run == 0:
                print("error: --tasks-in-run must be a positive integer when --cells is given", file=sys.stderr)
                return 2
            if args.price_in is None or args.price_out is None:
                print("error: --price-in and --price-out required when --cells is given", file=sys.stderr)
                return 2
            scaled = (bound / Decimal(args.tasks_in_run)) * Decimal(args.cells)
            print(f"ESTIMATE scaled_upper_bound_usd={scaled.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)} "
                  f"for {args.cells} cells (bound / {args.tasks_in_run} tasks * {args.cells} cells)")
    elif args.cells is not None:
        print("error: --price-in and --price-out required when --cells is given", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
