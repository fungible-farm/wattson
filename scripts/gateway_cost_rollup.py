#!/usr/bin/env python3
"""Roll a day of gateway access-log records up to a per-(task, model) cost table (#33).

Usage: python3 scripts/gateway_cost_rollup.py --log FILE [--prices PRICES.json] [--out TABLE.json]
A model call is one `key=value` access-log line with protocol=llm (scripts/gateway_llm_log_check.py measured that
shape, where values may be double-quoted); every other line is skipped and counted, so the skipped total is auditable.
Nothing here runs a model — it adds up what the journal already recorded. `est_cost (estimate)` = (input_tokens *
in_per_mtok + output_tokens * out_per_mtok) / 1e6 at the PRICES.json rate, 6 dp. An unpriced model is `n/a`, never 0 —
and so is a TOTAL over any unpriced group, so an unknown rate cannot read as free. A call with no token counts FAILs
naming its line number: unknown usage must not roll up as zero usage. Exit 0 clean, 1 for that FAIL, 2 for a log or
prices file that cannot be read or parsed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

FIELD = re.compile(r'([A-Za-z][\w.-]*)=("(?:[^"\\]|\\.)*"|[^"\s]*)')
DUR = re.compile(r"\A(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)?\Z")
UNIT = {u: Decimal(10) ** e for u, e in {"ns": -6, "us": -3, "µs": -3, "μs": -3, "ms": 0, "s": 3, "m": 5, "h": 7}.items()}
W = (34, 24, 6, 7, 13, 13, 12, 18)  # column widths: task/model left, numbers right
FMT = " | ".join(f"{{:{'<' if i < 2 else ''}{w}}}" for i, w in enumerate(W))
HEADER_KEYS = ("task", "model", "calls", "errors", "input_tokens", "output_tokens", "duration_ms", "est_cost")
HEADER = [*HEADER_KEYS[:-1], "est_cost (estimate)"]  # an estimate at PRICES.json rates, never an invoice
MILLION, ZERO, MICRO = Decimal(1_000_000), Decimal(0), Decimal("0.000001")


class InputError(Exception):
    """The log or prices file cannot be read, or is not the shape its name promises."""


class UsageError(Exception):
    """A model-call record carries no token counts, so its cost cannot be known (and is not zero)."""


def read_text(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # UnicodeDecodeError carries its own clear message
        raise InputError(f"cannot read {what} {path}: {exc.strerror or exc}") from exc


def parse_fields(line: str) -> dict[str, str]:
    """Every `key=value` on the line with double quotes stripped; later duplicates win."""
    return {key: (val[1:-1] if val[0] == '"' else val) for key, val in FIELD.findall(line)}


def duration_ms(value: str) -> Decimal:
    """`1ms` / `2.5s` / `300us` as milliseconds; absent or unparseable is 0, since duration never feeds cost."""
    m = DUR.match(value.strip())
    return Decimal(m.group(1)) * UNIT[m.group(2) or "ms"] if m else ZERO


def load_prices(path: str | None) -> dict[str, tuple[Decimal, Decimal]]:
    """model -> (in, out) dollars per 1M tokens; a half-priced model is an input error, not a free model."""
    if path is None:
        return {}
    try:
        doc = json.loads(read_text(path, "prices file"))  # .items() below also catches a JSON list or scalar
        return {m: (Decimal(str(e["in_per_mtok"])), Decimal(str(e["out_per_mtok"]))) for m, e in doc.items()}
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc
    except (AttributeError, KeyError, TypeError, InvalidOperation) as exc:
        raise InputError(f"{path}: every model needs in_per_mtok and out_per_mtok") from exc


def roll_up(log_path: str) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    """Group model calls by (task, model); lines without protocol=llm are skipped and counted."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    skipped = 0
    for line_no, line in enumerate(read_text(log_path, "log").splitlines(), start=1):
        f = parse_fields(line)
        if f.get("protocol") != "llm":
            skipped += 1  # a blank line has no protocol either, so it counts as one of the non-llm lines
            continue
        counts = [k for k in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")
                  if not f.get(k, "").lstrip("+").isdigit()]
        if counts:
            raise UsageError(f"line {line_no}: model call without {', '.join(counts)}")
        status = f.get("http.response.status_code", "")
        key = (f.get("task") or "(no task)", f.get("gen_ai.request.model") or "(no model)")
        g = groups.setdefault(key, {"calls": 0, "errors": 0, "in": 0, "out": 0, "ms": ZERO})
        g["calls"] += 1
        if not (status.isdigit() and 200 <= int(status) < 300):
            g["errors"] += 1  # an absent status is not a known 2xx either
        g["in"] += int(f["gen_ai.usage.input_tokens"])
        g["out"] += int(f["gen_ai.usage.output_tokens"])
        g["ms"] += duration_ms(f.get("duration", ""))
    return groups, skipped


def money(tokens_in: int, tokens_out: int, rates: tuple[Decimal, Decimal] | None) -> str:
    return "n/a" if rates is None else str(((Decimal(tokens_in) * rates[0] + Decimal(tokens_out) * rates[1])
                                            / MILLION).quantize(MICRO, rounding=ROUND_HALF_UP))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", required=True, help="gateway access-log file")
    ap.add_argument("--prices", help="PRICES.json: model -> {in_per_mtok, out_per_mtok} in USD per 1M tokens")
    ap.add_argument("--out", help="write the same table as deterministic JSON")
    args = ap.parse_args()
    try:
        prices, (groups, skipped) = load_prices(args.prices), roll_up(args.log)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except UsageError as exc:
        print(f"FAIL gateway-cost-rollup: {exc} — unknown usage is not zero")
        return 1

    table = [{"task": k[0], "model": k[1], "calls": groups[k]["calls"], "errors": groups[k]["errors"],
              "input_tokens": groups[k]["in"], "output_tokens": groups[k]["out"],
              "duration_ms": groups[k]["ms"].quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
              "est_cost": money(groups[k]["in"], groups[k]["out"], prices.get(k[1]))} for k in sorted(groups)]
    counts = ("calls", "errors", "input_tokens", "output_tokens")
    totals: dict[str, Any] = {c: sum((r[c] for r in table), 0) for c in counts}
    totals["duration_ms"] = sum((r["duration_ms"] for r in table), ZERO)
    # n/a propagates through the total: an unpriced model must not hide inside a smaller-looking number
    totals["est_cost"] = ("n/a" if any(r["est_cost"] == "n/a" for r in table) else
                          str(sum((Decimal(r["est_cost"]) for r in table), ZERO).quantize(MICRO)))

    def render(rec: dict[str, Any]) -> list[str]:
        return [str(rec[c]) for c in HEADER_KEYS]

    body, last = [render(r) for r in table], render({**totals, "task": "TOTAL", "model": ""})
    widths = [max(len(r[i]) for r in [HEADER, *body, last]) for i in range(len(HEADER))]

    def pad(row: list[str]) -> str:
        return " | ".join((c.rjust(w) if i > 1 else c.ljust(w)) for i, (c, w) in enumerate(zip(row, widths)))

    rule = "-" * (sum(widths) + 3 * len(widths) - 1)
    print(pad(HEADER))
    print(rule)
    for row in body:
        print(pad(row))
    print(rule)
    print(pad(last), f"skipped {skipped} non-llm line(s)", sep="\n")

    if args.out:
        def num(rec: dict[str, Any]) -> dict[str, Any]:  # est_cost stays a string, never a float
            return {**rec, "duration_ms": float(rec["duration_ms"])}

        doc = {"groups": [num(r) for r in table], "skipped": skipped, "totals": num(totals)}
        try:
            Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc.strerror or exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
