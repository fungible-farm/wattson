"""SHACL validation of a CGMES-shaped CIM graph with pyshacl (PC-REQ-020).

The shapes live in ``shapes.ttl`` next to this module (a hand-written subset, not the ENTSO-E
CGMES conformity shapes -- see that file's header). ``pyshacl`` (Apache-2.0) is imported lazily so
the rest of the package works without it; adopt it with ``uv add pyshacl``.

Validation runs with ``inference="none"``: the graph carries no CIM RDFS schema, and the shapes
target concrete classes, so no entailment is needed and none is silently assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from rdflib import RDF, Graph, Namespace

SHAPES_PATH: Final[Path] = Path(__file__).with_name("shapes.ttl")
"""The SHACL shapes graph shipped with this package."""

SH: Final[Namespace] = Namespace("http://www.w3.org/ns/shacl#")


@dataclass(frozen=True)
class Violation:
    """One ``sh:ValidationResult``, reduced to comparable strings."""

    focus: str
    path: str
    component: str
    message: str
    severity: str


@dataclass
class ValidationReport:
    """Outcome of a SHACL run: conformance flag, the violations, and pyshacl's text report."""

    conforms: bool
    violations: list[Violation] = field(default_factory=list)
    text: str = ""

    def components(self) -> set[str]:
        """Local names of the violated constraint components (e.g. ``MinCountConstraintComponent``)."""
        return {v.component.rsplit("#", 1)[-1] for v in self.violations}


def load_shapes(path: Path = SHAPES_PATH) -> Graph:
    """Parse the SHACL shapes graph."""
    return Graph().parse(str(path), format="turtle")


def _path_str(results: Graph, node) -> str:  # noqa: ANN001 -- rdflib node
    """Readable ``sh:resultPath``; inverse paths render as ``^<prop>``."""
    if node is None:
        return ""
    inverse = results.value(node, SH.inversePath)
    return f"^{inverse}" if inverse is not None else str(node)


def validate_graph(data: Graph, shapes: Graph | None = None) -> ValidationReport:
    """Validate ``data`` against the shapes; never modifies ``data``.

    Args:
        data: the CIM graph to check.
        shapes: an alternative shapes graph (defaults to ``shapes.ttl``).

    Returns:
        A :class:`ValidationReport`.
    """
    from pyshacl import validate  # Apache-2.0; lazily imported (optional dependency)

    conforms, results, text = validate(
        data, shacl_graph=shapes if shapes is not None else load_shapes(),
        inference="none", abort_on_first=False, allow_warnings=False, inplace=False,
    )
    violations = []
    for r in results.subjects(RDF.type, SH.ValidationResult):
        violations.append(Violation(
            focus=str(results.value(r, SH.focusNode)),
            path=_path_str(results, results.value(r, SH.resultPath)),
            component=str(results.value(r, SH.sourceConstraintComponent)),
            message=str(results.value(r, SH.resultMessage) or ""),
            severity=str(results.value(r, SH.resultSeverity)),
        ))
    return ValidationReport(conforms=bool(conforms), violations=sorted(violations,
                            key=lambda v: (v.focus, v.path, v.component)), text=text)
