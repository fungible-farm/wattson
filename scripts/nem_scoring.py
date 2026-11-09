#!/usr/bin/env python3
"""Answer scoring for the NEM question sets, shared by the benchmark arms and by `nem_question_lint.py`.

A question PASSES only when the returned result is SHAPED like an answer and contains the expected values:
  * at most `max_rows` rows came back (a table dump is not an answer), and
  * ONE returned row contains every expected value, each matched to a distinct cell, and
  * numbers match within half a unit of the declared rounding (an unrounded correct answer passes; a wrong one does not),
    strings match case-insensitively.
This replaces "any expected value appears anywhere in any returned row" and the fixed 0.005 tolerance.
"""

MAX_ROWS = 3


def tolerance(expected, decimals):
    if decimals is None:
        return 1e-9 + 1e-6 * abs(expected)
    return 0.5 * 10 ** (-decimals) + 1e-9


def cell_matches(expected, got, decimals):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(got, (int, float)) and not isinstance(got, bool) and abs(expected - got) <= tolerance(expected, decimals)
    return str(expected).strip().lower() == str(got).strip().lower()


def row_has_all(expected_row, row, decimals):
    unused = list(row)
    for want in expected_row:
        hit = next((i for i, got in enumerate(unused) if cell_matches(want, got, decimals)), None)
        if hit is None:
            return False
        unused.pop(hit)
    return True


def score(expected_row, returned_rows, decimals=None, max_rows=MAX_ROWS):
    """Return (ok, reason). `returned_rows` is a list of rows (lists); `expected_row` the ground-truth row."""
    if not returned_rows:
        return False, "no rows returned"
    if len(returned_rows) > max_rows:
        return False, f"{len(returned_rows)} rows returned (limit {max_rows}): a dump is not an answer"
    if any(row_has_all(expected_row, row, decimals) for row in returned_rows):
        return True, "a returned row contains every expected value"
    return False, "no single returned row contains every expected value"
