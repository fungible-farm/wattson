"""The evaluation sample must be balanced, interleaved and reproducible."""

from __future__ import annotations

import pytest

from powercoder.datasets.sample_benchmark import equal_allocation, independent, sample

ROWS: list[dict] = [{"example_id": f"{name}-{i:03d}", "subfamily": name}
                    for name, count in (("pf-report", 26), ("pf-load-scale-fit", 120), ("pf-gen-dispatch-fit", 40))
                    for i in range(count)]

EVEN: dict[str, int] = {"pf-report": 25, "pf-load-scale-fit": 25, "pf-gen-dispatch-fit": 25}


def test_every_prefix_covers_every_subfamily_and_the_draw_is_seeded() -> None:
    """Round-robin order: each block of three holds one task per subfamily."""
    first = sample(ROWS, EVEN, 7)
    assert len(first) == 75 and len({row["example_id"] for row in first}) == 75
    for start in range(0, 75, 3):
        assert {row["subfamily"] for row in first[start:start + 3]} == {
            "pf-report", "pf-load-scale-fit", "pf-gen-dispatch-fit"}
    assert sample(list(reversed(ROWS)), EVEN, 7) == first
    assert sample(ROWS, EVEN, 8) != first


def test_a_subfamily_smaller_than_the_allocation_is_refused() -> None:
    """Silently taking fewer tasks would unbalance the sample."""
    with pytest.raises(ValueError, match="pf-report"):
        sample(ROWS, {"pf-report": 27}, 7)


def test_allocation_is_equal_until_a_subfamily_runs_out() -> None:
    """The validation split's 3 dispatch fits give their shortfall to the others."""
    assert equal_allocation({"a": 9, "b": 20, "c": 3, "d": 19}, 20) == {"a": 6, "b": 6, "c": 3, "d": 5}
    with pytest.raises(ValueError):
        equal_allocation({"a": 2}, 3)


def test_rows_shared_with_training_or_repeated_are_dropped() -> None:
    """The validation split's leak: a training report and a repeated base case."""
    def row(example_id: str, prompt: str) -> dict:
        return {"example_id": example_id, "subfamily": "pf-report", "topology": "t",
                "task": {"prompt": prompt, "inputs": {"case": prompt}}}
    kept, overlapping, duplicates = independent(
        [row("a", "base"), row("b", "base"), row("c", "n-1 line 7"), row("d", "trained")], [row("x", "trained")])
    assert [r["example_id"] for r in kept] == ["a", "c"] and (overlapping, duplicates) == (1, 1)
