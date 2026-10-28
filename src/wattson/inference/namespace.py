"""A walkable path namespace over the case tables: ``ls`` and ``get`` (backlog 0026).

r6b's Qwen student guessed bus ids after a refusal because nothing listed them
(AGENTS.md FM-26). Here every node can be listed, so a valid id is found by walking:

    /                                    -> case, env
    /case                                -> network ids
    /case/snem-sa-2000                   -> element kinds, with counts
    /case/snem-sa-2000/bus               -> bus ids, paged, with the total
    /case/snem-sa-2000/bus/985           -> that bus's attributes
    /case/snem-sa-2000/bus/985/vn_kv     -> one value (get)

The paths are exactly those of ``namespace.lark``. Values are read from the pandapower
tables as ``powermcp._load_network_object`` loads them, so they are the case data,
not results: solved quantities stay with ``run_study_trial``. ``/env`` and its
``as_of=`` segment are reserved for time-indexed data (backlog 0027) and answer
"no data". A ``get`` response always carries the canonical path it read, so an
answer can cite it and the grader can resolve the citation itself.
"""

from __future__ import annotations

import math
import re
from typing import Any

from wattson.inference import powermcp as pm

# pandapower tables a path can name, in listing order.
KINDS: tuple[str, ...] = ("bus", "line", "trafo", "gen", "load", "shunt")
# Ids per ls page. The snemSA bus list (503) takes 11 pages; 50 ids is about 300 characters.
LS_PAGE: int = 50
# Units from pandapower's column-name suffixes (its documented naming convention), longest first.
UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_ohm_per_km", "ohm/km"), ("_nf_per_km", "nF/km"), ("_us_per_km", "uS/km"), ("_percent", "%"),
    ("_degree", "deg"), ("_mvar", "Mvar"), ("_mva", "MVA"), ("_mw", "MW"), ("_kw", "kW"), ("_kv", "kV"),
    ("_ka", "kA"), ("_pu", "pu"), ("_km", "km"),
)
# The same shapes namespace.lark accepts, as one regular expression.
PATH: re.Pattern[str] = re.compile(
    r"/(?:case(?:/(?P<network>[a-z0-9]+(?:-[a-z0-9]+)*)(?:/(?P<kind>" + "|".join(KINDS) + r")"
    r"(?:/(?P<id>0|[1-9][0-9]*)(?:/(?P<attribute>[a-z][a-z0-9_]*))?)?)?)?"
    r"|(?P<env>env)(?:/as_of=(?P<as_of>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z))?)?")


class NamespaceError(ValueError):
    """A path outside the grammar, or one that names nothing."""


def parse(path: str) -> dict[str, str | None]:
    """The path's segments, or NamespaceError if the grammar does not accept it."""
    match = PATH.fullmatch(path)
    if match is None:
        raise NamespaceError(f"Not a namespace path: {path!r}; start at '/' and use ls to walk")
    return match.groupdict()


def unit(attribute: str) -> str | None:
    """The unit pandapower's column name implies, if any."""
    return next((u for suffix, u in UNIT_SUFFIXES if attribute.endswith(suffix)), None)


def plain(value: Any) -> Any:
    """A table cell as a JSON value; NaN and missing become None."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


def _net(network: str) -> Any:
    try:
        net, _ = pm._load_network_object(network)
    except ValueError as error:
        raise NamespaceError(f"Unknown network {network!r}; ls /case lists them") from error
    return net


def ls(path: str, offset: int = 0) -> dict[str, Any]:
    """List a node's children (ids paged by LS_PAGE), always with the total count.

    Raises:
        NamespaceError: for a path outside the grammar or naming nothing.
    """
    p = parse(path)
    if p["env"]:
        return {"path": path, "children": [], "total": 0, "note": "no time-indexed data is loaded"}
    if path == "/":
        return {"path": "/", "children": ["case", "env"], "total": 2}
    if p["network"] is None:
        ids = sorted(pm.CASE_FILES) + sorted(pm.STANDARD_CASES) + sorted(pm.FIXTURE_BUILDERS)
        return {"path": path, "children": ids, "total": len(ids)}
    net = _net(p["network"])
    if p["kind"] is None:
        counts = {kind: len(net[kind]) for kind in KINDS if kind in net}
        return {"path": path, "children": list(counts), "counts": counts, "total": len(counts)}
    table = net[p["kind"]]
    if p["id"] is None:
        ids = sorted(int(i) for i in table.index)
        page = ids[offset:offset + LS_PAGE]
        return {"path": path, "children": page, "offset": offset, "total": len(ids),
                "next_offset": offset + LS_PAGE if offset + LS_PAGE < len(ids) else None}
    element = int(p["id"])
    if element not in table.index:
        raise NamespaceError(f"No {p['kind']} {element} in {p['network']}; ls {path.rsplit('/', 1)[0]} lists them")
    if p["attribute"] is None:
        return {"path": path, "children": list(table.columns), "total": len(table.columns)}
    raise NamespaceError(f"{path} is a value; use get")


def get(path: str) -> dict[str, Any]:
    """One attribute's value, its unit and its canonical path.

    Raises:
        NamespaceError: for a path that does not name one attribute of one element.
    """
    p = parse(path)
    if p["attribute"] is None:
        raise NamespaceError(f"{path} is not a value; use ls to walk to one")
    net = _net(p["network"])
    table = net[p["kind"]]
    element = int(p["id"])
    if element not in table.index:
        raise NamespaceError(f"No {p['kind']} {element} in {p['network']}")
    if p["attribute"] not in table.columns:
        raise NamespaceError(f"{p['kind']} has no attribute {p['attribute']!r}; ls {path.rsplit('/', 1)[0]} lists them")
    canonical = f"/case/{p['network']}/{p['kind']}/{element}/{p['attribute']}"
    return {"path": canonical, "value": plain(table.at[element, p["attribute"]]), "unit": unit(p["attribute"])}


def tool_schemas() -> list[dict[str, Any]]:
    """``ls`` and ``get`` as tool definitions; descriptions kept short (each prompt pays for them)."""
    path = {"type": "string", "description": "A namespace path, e.g. /case/snem-sa-2000/bus"}
    return [
        {"type": "function", "function": {
            "name": "ls",
            "description": "List a namespace node's children (ids paged, with the total). Start at /.",
            "parameters": {"type": "object", "properties": {"path": path, "offset": {"type": "integer", "minimum": 0}},
                           "required": ["path"], "additionalProperties": False}}},
        {"type": "function", "function": {
            "name": "get",
            "description": "Read one case-data value, e.g. /case/snem-sa-2000/bus/985/vn_kv. Cite the returned path.",
            "parameters": {"type": "object", "properties": {"path": path},
                           "required": ["path"], "additionalProperties": False}}},
    ]


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run ``ls`` or ``get`` in the status/data/error shape the other tools return."""
    try:
        data = ls(arguments["path"], int(arguments.get("offset", 0))) if name == "ls" else get(arguments["path"])
        return {"status": "success", "data": data, "error": None}
    except NamespaceError as error:
        return {"status": "execution_failure", "data": {}, "error": f"NamespaceError: {error}"}
