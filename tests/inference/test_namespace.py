"""The ls/get namespace (backlog 0026): one grammar, exact listings, citable values."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from wattson.inference import namespace as ns
from wattson.inference import powermcp as pm
from wattson.inference.mcp_server import tool_definitions
from wattson.inference.provenance import check_fields

GRAMMAR: Path = Path(ns.__file__).with_name("namespace.lark")
CASE: str = "snem-sa-2000"
HAS_CASE: bool = (pm.REPO_ROOT / pm.CASE_FILES[CASE]).exists()
needs_case = pytest.mark.skipif(not HAS_CASE, reason="needs data/ (scripts/fetch_csiro_nem_data.py)")
# Seeded so the mutated corpus is the same every run.
MUTATION_SEED: int = 20261023
MUTATIONS_PER_PATH: int = 5


def corpus() -> list[str]:
    """Valid paths at every depth, from the fixture network (no data/ needed)."""
    net = "fixture-2bus-ok"
    paths = ["/", "/case", "/env", "/env/as_of=2026-10-23T06:00Z", f"/case/{net}"]
    for kind in ns.ls(f"/case/{net}")["children"]:
        paths.append(f"/case/{net}/{kind}")
        for element in ns.ls(f"/case/{net}/{kind}")["children"][:2]:
            paths.append(f"/case/{net}/{kind}/{element}")
            paths += [f"/case/{net}/{kind}/{element}/{a}" for a in ns.ls(f"/case/{net}/{kind}/{element}")["children"][:3]]
    return paths


def mutate(path: str, rng: random.Random) -> str:
    """One random edit: drop, duplicate or replace a character, or add a trailing slash."""
    i = rng.randrange(len(path))
    return rng.choice([path[:i] + path[i + 1:], path[:i] + path[i] + path[i:],
                       path[:i] + rng.choice("/-_=0Ab ") + path[i + 1:], path + "/"])


def test_the_grammar_and_the_resolver_accept_exactly_the_same_paths() -> None:
    """namespace.lark and namespace.PATH must not drift: one decides decoding, the other serving."""
    lark = pytest.importorskip("lark")
    parser = lark.Lark(GRAMMAR.read_text(), start="start", parser="earley")
    rng = random.Random(MUTATION_SEED)
    valid = corpus()
    candidates = valid + [mutate(p, rng) for p in valid for _ in range(MUTATIONS_PER_PATH)]
    disagreements = []
    for path in candidates:
        try:
            parser.parse(path)
            grammar_ok = True
        except lark.exceptions.LarkError:
            grammar_ok = False
        if grammar_ok != (ns.PATH.fullmatch(path) is not None):
            disagreements.append(path)
    assert all(ns.PATH.fullmatch(p) for p in valid)
    assert disagreements == []


@needs_case
def test_ls_lists_every_id_of_every_kind_exactly() -> None:
    net, _ = pm._load_network_object(CASE)
    for kind in ns.KINDS:
        listed, offset = [], 0
        while offset is not None:
            page = ns.ls(f"/case/{CASE}/{kind}", offset)
            listed += page["children"]
            offset = page["next_offset"]
        assert listed == sorted(int(i) for i in net[kind].index) and page["total"] == len(net[kind])


@needs_case
def test_walking_finds_a_bus_the_study_tool_accepts() -> None:
    """FM-26: after a refused id, listing gives a valid one instead of a guess."""
    from wattson.inference.studies import run_study_trial

    first = ns.ls(f"/case/{CASE}/bus")["children"][0]
    result = run_study_trial(network_id=CASE, operation="none", scale=1.0, element_id=0, observation="bus_voltage",
                             observation_id=first, max_iter=50, contingency_line=94)
    assert result.data["converged"]


@needs_case
def test_get_returns_the_table_value_its_unit_and_canonical_path() -> None:
    net, _ = pm._load_network_object(CASE)
    bus = int(min(net.bus.index))
    got = ns.get(f"/case/{CASE}/bus/{bus}/vn_kv")
    assert got == {"path": f"/case/{CASE}/bus/{bus}/vn_kv", "value": float(net.bus.vn_kv[bus]), "unit": "kV"}


def test_mistakes_are_refused_with_a_way_forward() -> None:
    assert ns.call("get", {"path": "/case/fixture-2bus-ok/bus"})["error"].endswith("use ls to walk to one")
    assert "ls /case lists them" in ns.call("ls", {"path": "/case/nowhere"})["error"]
    assert ns.call("ls", {"path": "/case//bus"})["status"] == "execution_failure"


def test_the_default_tool_set_is_unchanged() -> None:
    """r1-r6 prompts were rendered without ls/get; adding them must be opt-in."""
    assert [t.name for t in tool_definitions(True)] == ["load_network", "run_power_flow", "run_study_trial"]
    assert [t.name for t in tool_definitions(True, True)][-2:] == ["ls", "get"]


REPORT: dict = {"subfamily": "pf-report", "task": {"prompt": "", "inputs": {"case": CASE, "contingency_line": None}}}


def listing(children: list[int]) -> list[dict]:
    """A journal holding one ls response."""
    return [{"kind": "tool_response", "payload": {"result": {"content": [{"type": "text", "text": json.dumps(
        {"status": "success", "data": {"path": f"/case/{CASE}/bus", "children": children, "total": 503}})}]}}}]


def test_a_cited_number_must_equal_what_its_path_holds() -> None:
    held = {f"/case/{CASE}/bus/985/vn_kv": {"value": 165.0}}
    resolve = lambda path: held[path]
    ok = check_fields(REPORT, f"Bus 985 is at 165 kV (/case/{CASE}/bus/985/vn_kv).", listing([985]), resolve)
    assert ok["traced"], ok["reasons"]
    wrong = check_fields(REPORT, f"Bus 985 is at 132 kV (/case/{CASE}/bus/985/vn_kv).", listing([985]), resolve)
    assert wrong["reasons"] == [f"132: cited path /case/{CASE}/bus/985/vn_kv holds 165.0"]


def test_listed_ids_are_identifiers_not_measurements() -> None:
    """An id from ls may be named, but a listing's total cannot stand in for a violation count."""
    answer = "Bus 985 exists. Voltage violations = 503."
    result = check_fields(REPORT, answer, listing([985]))
    assert result["untraced"] == ["503"]
