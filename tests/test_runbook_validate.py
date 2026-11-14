"""Tests for scripts/runbook_validate.py: the runbook schema checks (#130 step b).

Each test perturbs one thing in a copy of the example runbook and asserts which check catches it, so the suite documents
the schema rather than main(). Exit codes matter as much as the printed lines - 0 clean, 1 for any FAIL, 2 for unreadable.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
VALIDATE: Path = REPO / "scripts" / "runbook_validate.py"
EXAMPLE: Path = REPO / "benchmarks" / "runbooks" / "example" / "region-average.json"
BASE: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runbook_validate = load(VALIDATE, "runbook_validate")


def drop(doc: dict[str, Any], key: str, sub: str | None = None) -> None:
    """Delete a top-level key, or one key of `source` / of the first entry of a list field."""
    target = doc if sub is None else (doc[key] if isinstance(doc[key], dict) else doc[key][0])
    target.pop(key if sub is None else sub)


def run(tmp_path: Path, *docs: Any, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Write the docs as runbook-0.json... and validate them together; return (exit code, stdout)."""
    paths = [str(tmp_path / f"runbook-{i}.json") for i in range(len(docs))]
    for path, doc in zip(paths, docs):
        Path(path).write_text(json.dumps(doc), encoding="utf-8")
    capsys.readouterr()
    return runbook_validate.main(paths), capsys.readouterr().out


def fails(tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any) -> str:
    """One mutated copy of the example runbook must exit 1; return what it printed."""
    doc = copy.deepcopy(BASE)
    mutate(doc)
    code, printed = run(tmp_path, doc, capsys=capsys)
    assert code == 1, printed
    return printed


def test_the_example_runbook_passes(capsys: pytest.CaptureFixture[str]) -> None:
    """The shipped example is the reference: five PASS lines, a clean count, exit 0."""
    assert runbook_validate.main([str(EXAMPLE)]) == 0
    checks = ("required", "placeholders", "steps", "manual_refs", "unique_ids")
    assert capsys.readouterr().out == "".join(f"PASS {n}\n" for n in checks) + "runbook-validate: 1 file(s), 0 failure(s)\n"


@pytest.mark.parametrize("key", list(BASE))
def test_every_top_level_key_is_required(tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str) -> None:
    """Delete any one of the nine keys: FAIL required names it and, by design, nothing else runs."""
    printed = fails(tmp_path, capsys, lambda doc: drop(doc, key))
    assert f"FAIL required: {tmp_path / 'runbook-0.json'} missing required key {key!r}" in printed
    assert "PASS" not in printed


@pytest.mark.parametrize("key,sub", [("source", "adapter"), ("source", "run_id"), ("source", "journal_sha256"),
                                     ("parameters", "name"), ("steps", "cmd"), ("manual_refs", "section")])
def test_sub_keys_are_required(tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, sub: str) -> None:
    """source and the entries of the three list fields carry their own required keys."""
    printed = fails(tmp_path, capsys, lambda doc: drop(doc, key, sub))
    at = "source" if isinstance(BASE[key], dict) else f"{key}[0]"
    assert f"FAIL required: {tmp_path / 'runbook-0.json'} {at} missing {sub}" in printed


@pytest.mark.parametrize("mutate,detail", [
    (lambda d: d.__setitem__("id", 7), "id is int, not a string"),
    (lambda d: d.__setitem__("status", "pending"), "status is 'pending', not one of draft/accepted/rejected"),
    (lambda d: d["source"].__setitem__("adapter", ["bsl"]), "source.adapter is list, not a string"),
    (lambda d: d["source"].__setitem__("journal_sha256", "not-a-hash"), "source.journal_sha256 is not 64 lowercase hex"),
    (lambda d: d.__setitem__("parameters", {}), "parameters is dict, not a list"),
    (lambda d: d["parameters"][0].__setitem__("type", "boolean"), "parameters[0].type is 'boolean', not one of string/integer/number"),
    (lambda d: d.__setitem__("steps", "run the query"), "steps is str, not a list"),
    (lambda d: d.__setitem__("manual_refs", ["nemosis-dispatch-data"]), "manual_refs[0] is str, not an object"),
    (lambda d: d.__setitem__("notes", "one long note"), "notes is str, not a list"),
    (lambda d: d.__setitem__("author", "brian"), "unknown top-level key 'author'"),
])
def test_a_value_of_the_wrong_shape_fails_required(tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any, detail: str) -> None:
    assert f"FAIL required: {tmp_path / 'runbook-0.json'} {detail}" in fails(tmp_path, capsys, mutate)


@pytest.mark.parametrize("mutate,detail", [
    (lambda d: d.__setitem__("sql_template", BASE["sql_template"] + " AND region = {region_id}"),
     "sql_template uses undeclared slot(s): {region_id}"),
    (lambda d: d["parameters"].append({"name": "limit_rows", "type": "integer", "description": "unused"}),
     "declared parameter(s) never used in sql_template: limit_rows"),
])
def test_placeholders_match_parameters_both_ways(tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any, detail: str) -> None:
    """An undeclared slot and an unused parameter are both errors; the shape checks still print PASS."""
    printed = fails(tmp_path, capsys, mutate)
    assert f"FAIL placeholders: {tmp_path / 'runbook-0.json'} {detail}" in printed
    assert "PASS required" in printed


def test_a_step_needs_both_cmd_and_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Whitespace is no command - the cmd/pass pair the D2 generator draws must both carry text - and steps cannot be empty."""
    printed = fails(tmp_path, capsys, lambda d: d["steps"][0].__setitem__("pass", "   "))
    assert f"FAIL steps: {tmp_path / 'runbook-0.json'} steps[0].pass is empty" in printed
    assert "PASS placeholders" in printed
    assert "steps is empty" in fails(tmp_path, capsys, lambda d: d.__setitem__("steps", []))


@pytest.mark.parametrize("sha", ["", "0" * 63, "0" * 63 + "A", "z" * 64])
def test_a_manual_ref_hash_must_be_64_lowercase_hex(tmp_path: Path, capsys: pytest.CaptureFixture[str], sha: str) -> None:
    printed = fails(tmp_path, capsys, lambda d: d["manual_refs"][0].__setitem__("sha256", sha))
    assert f"FAIL manual_refs: {tmp_path / 'runbook-0.json'} manual_refs[0].sha256 is not 64 lowercase hex: {sha!r}" in printed


def test_the_same_manual_section_twice_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two refs to one document+section is one ref too many, whoever wrote the second copy."""
    printed = fails(tmp_path, capsys, lambda d: d["manual_refs"].append(copy.deepcopy(BASE["manual_refs"][0])))
    assert "manual_refs[1] repeats nemosis-dispatch-data#interval-timestamps" in printed


@pytest.mark.parametrize("second_id,fail", [("region-sum", False), ("region-average", True)])
def test_ids_must_be_unique_across_the_files(tmp_path: Path, capsys: pytest.CaptureFixture[str], second_id: str, fail: bool) -> None:
    """unique_ids blames the reuse, not the first user: runbook-1 names runbook-0, and a distinct id is clean."""
    other = copy.deepcopy(BASE)
    other["id"] = second_id
    code, printed = run(tmp_path, BASE, other, capsys=capsys)
    assert code == (1 if fail else 0), printed
    assert (f"FAIL unique_ids: {tmp_path / 'runbook-1.json'} id {second_id!r} already used by" in printed) == fail
    assert f"runbook-validate: 2 file(s), {1 if fail else 0} failure(s)" in printed


def test_a_file_that_cannot_be_read_or_parsed_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Exit 2, not 1: an unreadable file and a file that is not JSON are input errors, not schema failures."""
    missing = tmp_path / "nope.json"
    assert runbook_validate.main([str(missing)]) == 2
    assert f"ERROR runbook-validate: cannot read {missing}" in capsys.readouterr().err
    broken = tmp_path / "broken.json"
    broken.write_text('{"id": }', encoding="utf-8")
    assert runbook_validate.main([str(broken)]) == 2
    assert "ERROR runbook-validate: " in (err := capsys.readouterr().err) and "is not valid JSON" in err
