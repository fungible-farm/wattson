#!/usr/bin/env python3
"""Runbook schema validator (#130 step b) for the format in docs/runbook-replay-design.md.

Usage: python3 scripts/runbook_validate.py FILE [FILE ...]

A runbook is a JSON object with exactly nine keys: id, family, status, source, parameters, sql_template,
steps, notes and manual_refs. manual_refs points at the operations-manual section a runbook relies on by
document, section and sha256 - a pointer plus a hash, never a copy of the manual text, whose licensing is
unresolved. Five checks print `PASS <check>` or `FAIL <check>: <file> <detail>`; any FAIL exits 1 and a
file that cannot be read or parsed exits 2. A file that fails `required` reports nothing further, so it
prints no PASS for the checks it could not run.

  required      the nine keys are present, no others are, and every value has the right shape.
  placeholders  the {slot} names in sql_template (lower-case [a-z_][a-z0-9_]*) are exactly the declared
                parameter names, both directions: an undeclared slot and an unused parameter both fail.
  steps         every step carries a non-empty cmd and a non-empty pass, the pair the D2 generator draws.
  manual_refs   every sha256 is 64 lower-case hex; no two refs name the same document+section.
  unique_ids    no two runbooks on the command line share an id.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
CHECKS = ("required", "placeholders", "steps", "manual_refs", "unique_ids")
TOP = ("id", "family", "status", "source", "parameters", "sql_template", "steps", "notes", "manual_refs")
SUB = {"source": ("adapter", "run_id", "journal_sha256"), "parameters": ("name", "type", "description"),
       "steps": ("cmd", "pass"), "manual_refs": ("document", "section", "sha256")}
STATUSES, PARAM_TYPES = ("draft", "accepted", "rejected"), ("string", "integer", "number")


class InputError(Exception):
    """A runbook file cannot be read, or is not JSON at all."""


def v_field(obj: dict[str, Any], key: str, at: str, errs: list[str], enum: tuple[str, ...] = (),
            hex64: bool = False) -> None:
    """One field is a string, then one of `enum` or 64 lower-case hex; absent keys are `required`'s."""
    if key not in obj:
        return
    if not isinstance(obj[key], str):
        errs.append(f"{at}{key} is {type(obj[key]).__name__}, not a string")
    elif enum and obj[key] not in enum:
        errs.append(f"{at}{key} is {obj[key]!r}, not one of {'/'.join(enum)}")
    elif hex64 and not HEX64.match(obj[key]):
        errs.append(f"{at}{key} is not 64 lowercase hex: {obj[key]!r}")


def v_entry(item: Any, at: str, keys: tuple[str, ...], errs: list[str]) -> dict[str, Any] | None:
    """One element of a list field: an object with exactly `keys`; None when there is nothing to check."""
    if not isinstance(item, dict):
        errs.append(f"{at} is {type(item).__name__}, not an object")
        return None
    for key in keys:
        if key not in item:
            errs.append(f"{at} missing {key}")
    return item


def check_required(doc: dict[str, Any], errs: list[str]) -> None:
    """The nine keys, no others, every value of the right shape; nothing else runs once this reports."""
    for key in doc:
        if key not in TOP:
            errs.append(f"unknown top-level key {key!r}")
    for key in TOP:
        if key not in doc:
            errs.append(f"missing required key {key!r}")
    if errs:
        return
    for key in ("id", "family", "sql_template"):
        v_field(doc, key, "", errs)
    v_field(doc, "status", "", errs, enum=STATUSES)
    if src := v_entry(doc["source"], "source", SUB["source"], errs):
        for key in ("adapter", "run_id"):
            v_field(src, key, "source.", errs)
        v_field(src, "journal_sha256", "source.", errs, hex64=True)
    if not isinstance(doc["notes"], list):
        errs.append(f"notes is {type(doc['notes']).__name__}, not a list")
    else:
        for pos, note in enumerate(doc["notes"]):
            if not isinstance(note, str):
                errs.append(f"notes[{pos}] is {type(note).__name__}, not a string")
    for key in ("parameters", "steps", "manual_refs"):
        check_items(doc, key, errs)


def check_items(doc: dict[str, Any], key: str, errs: list[str]) -> None:
    """parameters/steps/manual_refs: a list - non-empty but for parameters - of objects with SUB's keys."""
    if not isinstance(doc[key], list):
        errs.append(f"{key} is {type(doc[key]).__name__}, not a list")
        return
    if key != "parameters" and not doc[key]:
        errs.append(f"{key} is empty")
    for pos, item in enumerate(doc[key]):
        at = f"{key}[{pos}]"
        entry = v_entry(item, at, SUB[key], errs)
        for sub in SUB[key] if entry else ():
            enum = PARAM_TYPES if (key, sub) == ("parameters", "type") else ()
            v_field(entry, sub, f"{at}.", errs, enum=enum)


def check_content(doc: dict[str, Any]) -> dict[str, list[str]]:
    """placeholders, steps and manual_refs details, collected once the shape check has passed."""
    used = SLOT.findall(doc["sql_template"])
    declared = [param["name"] for param in doc["parameters"]]
    out = {"placeholders": [], "steps": [], "manual_refs": []}
    if extra := sorted(set(used) - set(declared)):
        slots = ", ".join("{" + name + "}" for name in extra)
        out["placeholders"].append(f"sql_template uses undeclared slot(s): {slots}")
    if unused := sorted(set(declared) - set(used)):
        out["placeholders"].append("declared parameter(s) never used in sql_template: " + ", ".join(unused))
    for pos, step in enumerate(doc["steps"]):
        for key in ("cmd", "pass"):
            if not step[key].strip():
                out["steps"].append(f"steps[{pos}].{key} is empty")
    seen = set()
    for pos, ref in enumerate(doc["manual_refs"]):
        v_field(ref, "sha256", f"manual_refs[{pos}].", out["manual_refs"], hex64=True)
        if (pair := (ref["document"], ref["section"])) in seen:
            out["manual_refs"].append(f"manual_refs[{pos}] repeats {pair[0]}#{pair[1]}")
        seen.add(pair)
    return out


def validate(doc: Any) -> dict[str, list[str] | None]:
    """Check one runbook: check -> detail lines, or None where the check could not run."""
    out: dict[str, list[str] | None] = {name: None for name in CHECKS}
    if not isinstance(doc, dict):
        out["required"] = [f"top-level JSON value is {type(doc).__name__}, not an object"]
        return out
    check_required(doc, errs := [])
    out["required"] = errs
    if errs:
        return out
    for name, detail in check_content(doc).items():
        out[name] = detail
    out["unique_ids"] = []
    return out


def load(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError(f"{path} is not valid JSON: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runbook_validate.py",
                                     description="Validate runbook files against the runbook schema (#130).")
    parser.add_argument("files", nargs="+", metavar="FILE")
    args = parser.parse_args(argv)
    try:
        docs = [(path, load(path)) for path in args.files]
    except InputError as exc:
        print(f"ERROR runbook-validate: {exc}", file=sys.stderr)
        return 2
    files = [path for path, _ in docs]
    runs = [validate(doc) for _, doc in docs]

    # A runbook is reported by the file that reuses an id, not by the first file to use it.
    seen: dict[str, int] = {}
    for pos, out in enumerate(runs):
        ident = docs[pos][1].get("id") if out["unique_ids"] is not None else None
        if isinstance(ident, str):
            if ident in seen:
                out["unique_ids"].append(f"id {ident!r} already used by {files[seen[ident]]}")
            else:
                seen[ident] = pos

    failures = 0
    for name in CHECKS:
        lines = [f"FAIL {name}: {path} {d}" for path, out in zip(files, runs) for d in (out[name] or [])]
        failures += len(lines)
        if lines:
            print("\n".join(lines))
        elif any(r[name] is not None for r in runs):
            print(f"PASS {name}")
    print(f"runbook-validate: {len(files)} file(s), {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
