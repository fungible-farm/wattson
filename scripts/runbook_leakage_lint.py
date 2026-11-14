#!/usr/bin/env python3
"""Runbook leakage lint (#130): a runbook must not carry the gold set it is meant to help answer.

Usage:
    python3 scripts/runbook_leakage_lint.py --runbook FILE [--runbook FILE ...] --gold GOLD.json

GOLD.json is a JSON list of objects `{"id": ..., "question": ..., "sql": ..., "values": [...]}` where
`question`, `sql` and `values` are each optional (configs/nem-multitable-questions.proposed.json is this
shape minus `values`). Three deterministic checks, no model, no network. Each prints `PASS <check>` or
`FAIL <check>: <id> <what leaked>`; any FAIL exits 1, a bad file or gold entry exits 2.

  question   no runbook may contain gold question text. Both sides are case- and whitespace-normalised and
             any 40+ character window of a gold question found in a runbook is a leak, so a rewrapped or
             blockquoted copy of the question fails just like a verbatim one.
  sql        no runbook may contain gold SQL, same window rule after whitespace normalisation (case is
             normalised too: lowercased keywords are the same leak).
  values     no runbook may contain a gold answer value. Strings of 5+ characters are matched as whole
             words, case-insensitively ("Newcastle" leaks, "Newcastleton" does not). Numbers are matched by
             numeric-token equality so 1234.5 leaks as "1,234.5" and as "1234.50", while neither matches
             "12345" or "51234.5" - a number token is read maximally, so digits inside a longer number are
             not a match.

Short values are deliberately NOT checked: 12, 0.5 or "QLD1" occur in ordinary operator prose, and a lint
that failed on them would be turned off rather than worked around. Those values are listed on an INFO line
instead of being silently skipped, so the reader sees what the check did not cover. The same holds for a
gold question or SQL shorter than 40 characters: it has no window to match, and the check cannot say
anything about it. Numbers are gated at 4+ significant digits on both sides, which is what keeps a gold
value of 100.00 from turning every "100" in a runbook into a leak.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

WINDOW = 40  # characters of normalised gold text that count as a quote
MIN_STRING_CHARS = 5  # gold strings shorter than this are too common to gate on
MIN_SIG_DIGITS = 4  # gold numbers with fewer significant digits are too common to gate on

# A number token, read maximally: it may not start inside another number and runs to the last digit.
NUMBER_TOKEN = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d)")
PLAIN_DECIMAL = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

CHECKS = ("question", "sql", "values")


def normalise(text: str) -> str:
    """Case-fold and collapse every run of whitespace, so rewrapped prose still compares equal."""
    return " ".join(text.split()).lower()


def significant_digits(plain: str) -> int:
    """Count of significant digits in a plain decimal literal ("0.0012" -> 2, "1200" -> 4)."""
    return len(plain.lstrip("+-").replace(".", "").lstrip("0"))


def as_plain(value: Decimal) -> str:
    """Decimal without exponent notation, so significant digits can be counted off the string."""
    return f"{value:f}"


def as_decimal(raw: object) -> Decimal | None:
    """The plain decimal a gold value means, or None if it is not a number we can compare."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        text: str | None = str(raw)
    elif isinstance(raw, float):
        text = repr(raw)
    elif isinstance(raw, str):
        candidate = raw.strip()
        # Only plainly-written numeric strings count as numbers, so an id like "1e5" stays a string.
        text = candidate if PLAIN_DECIMAL.fullmatch(candidate) else None
    else:
        text = None
    if text is None:
        return None
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def render(raw: object) -> str:
    """How a gold value is named in a message."""
    return f'"{raw}"' if isinstance(raw, str) else str(raw)


def snip(text: str, limit: int = 72) -> str:
    quoted = f'"{text}"'
    return quoted if len(text) <= limit else f'"{text[:limit]}..."'


def longest_quote(gold: str, hay: str) -> str | None:
    """Longest run of `gold` (>= WINDOW chars) that occurs in `hay`, or None.

    Testing every WINDOW-char window answers "any 40+ char window" outright: a longer shared run necessarily
    contains one. The matched run is then grown in both directions so the message says how much leaked.
    """
    for start in range(0, len(gold) - WINDOW + 1):
        if gold[start : start + WINDOW] not in hay:
            continue
        end = min(start + WINDOW, len(gold))
        while end < len(gold) and gold[start : end + 1] in hay:
            end += 1
        while start > 0 and gold[start - 1 : end] in hay:
            start -= 1
        return gold[start:end]
    return None


def whole_word_hit(term: str, hay: str) -> bool:
    return re.search(rf"(?<![0-9a-z_]){re.escape(term)}(?![0-9a-z_])", hay) is not None


def number_hit(want: Decimal, hay: str) -> str | None:
    """A number token in `hay` equal to `want`, or None (tokens below MIN_SIG_DIGITS are noise)."""
    for match in NUMBER_TOKEN.finditer(hay):
        token = match.group(0)
        if significant_digits(token) < MIN_SIG_DIGITS:
            continue
        try:
            got = Decimal(token.replace(",", ""))
        except InvalidOperation:
            continue
        if got == want:
            return token
    return None


class InputError(Exception):
    """A runbook or gold file cannot be read as the lint was told to read it."""


def load_gold(path: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read gold file {path}: {exc.strerror or exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"gold file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise InputError(f"gold file {path} must hold a JSON list of objects")
    for pos, item in enumerate(data):
        if not isinstance(item, dict):
            raise InputError(f"gold entry {pos} in {path} is not an object")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident.strip():
            raise InputError(f"gold entry {pos} in {path} has no id")
        for field in ("question", "sql"):
            if field in item and not isinstance(item[field], str):
                raise InputError(f'gold entry "{ident}" has a non-string {field}')
        if "values" in item and not isinstance(item["values"], list):
            raise InputError(f'gold entry "{ident}" has non-list values')
    return data


def load_runbooks(paths: list[str]) -> list[tuple[Path, str]]:
    loaded: list[tuple[Path, str]] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise InputError(f"cannot read runbook {path}: {exc.strerror or exc}") from exc
        loaded.append((Path(path), normalise(text)))
    return loaded


def lint_text(
    check: str,
    gold_items: list[dict[str, Any]],
    runbooks: list[tuple[Path, str]],
) -> tuple[list[str], list[str]]:
    """Quote check for one gold text field: `question` or `sql`."""
    failures: list[str] = []
    infos: list[str] = []
    for item in gold_items:
        ident = str(item["id"])
        gold = normalise(item.get(check, ""))
        if len(gold) < WINDOW:
            infos.append(
                f"INFO {check}: {ident} gold {check} is {len(gold)} normalised chars "
                f"(< {WINDOW}): no window to match"
            )
            continue
        for path, hay in runbooks:
            leaked = longest_quote(gold, hay)
            if leaked is not None:
                failures.append(
                    f"FAIL {check}: {ident} quotes {len(leaked)} chars of the gold {check} "
                    f"in {path}: {snip(leaked)}"
                )
    return failures, infos


def lint_values(
    gold_items: list[dict[str, Any]],
    runbooks: list[tuple[Path, str]],
) -> tuple[list[str], list[str]]:
    """Value check: gold strings as whole words, gold numbers as numeric tokens."""
    failures: list[str] = []
    infos: list[str] = []
    for item in gold_items:
        ident = str(item["id"])
        unchecked: list[str] = []
        for raw in item.get("values", []):
            number = as_decimal(raw)
            if number is not None:
                if significant_digits(as_plain(number)) < MIN_SIG_DIGITS:
                    unchecked.append(render(raw))
                    continue
                for path, hay in runbooks:
                    hit = number_hit(number, hay)
                    if hit is not None:
                        failures.append(
                            f'FAIL values: {ident} gold value {render(raw)} appears as "{hit}" in {path}'
                        )
            elif isinstance(raw, str):
                term = raw.strip().lower()
                if len(term) < MIN_STRING_CHARS:
                    unchecked.append(render(raw))
                    continue
                for path, hay in runbooks:
                    if whole_word_hit(term, hay):
                        failures.append(f"FAIL values: {ident} gold value {render(raw)} appears in {path}")
            else:
                unchecked.append(f"{render(raw)} (not a string or number)")
        if unchecked:
            infos.append(
                f"INFO values: {ident} not checked (under {MIN_STRING_CHARS} chars or "
                f"{MIN_SIG_DIGITS} significant digits): {', '.join(unchecked)}"
            )
    return failures, infos


def run_lint(gold_items: list[dict[str, Any]], runbooks: list[tuple[Path, str]]) -> tuple[list[str], int]:
    """Print the three checks; return (failure lines, number of failures)."""
    failures: list[str] = []
    for check in CHECKS:
        found, infos = (
            lint_values(gold_items, runbooks) if check == "values" else lint_text(check, gold_items, runbooks)
        )
        for line in infos:
            print(line)
        if found:
            failures.extend(found)
            for line in found:
                print(line)
        else:
            print(f"PASS {check}")
    return failures, len(failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runbook_leakage_lint.py",
        description="Fail if a runbook quotes the gold questions, SQL or answers it is meant to help with.",
    )
    parser.add_argument("--runbook", action="append", dest="runbooks", required=True, metavar="FILE")
    parser.add_argument("--gold", required=True, metavar="FILE")
    args = parser.parse_args(argv)

    try:
        gold_items = load_gold(args.gold)
        runbooks = load_runbooks(args.runbooks)
    except InputError as exc:
        print(f"ERROR runbook-leakage-lint: {exc}", file=sys.stderr)
        return 2

    failures, count = run_lint(gold_items, runbooks)
    print(f"{'FAIL' if failures else 'PASS'} runbook-leakage-lint: {count} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
