#!/usr/bin/env python3
"""Validate and optionally render the four gateway registries.

Validates each registry file against its JSON Schema, checks name uniqueness,
verifies referenced file paths exist, ensures hosted backends have spend caps,
and rejects literal secrets in any field.

Usage: python3 scripts/registry_check.py [--registry-dir registries] [--render]
Exit 0 on success, 1 on failure.
"""
import argparse
import json
import os
import sys

import yaml
from jsonschema import Draft202012Validator

REGISTRIES = ["backends", "tools", "skills", "prompts"]
SECRET_FIELDS = {"api_key", "apiKey", "token", "secret"}


def load_registry(registry_dir, name):
    path = os.path.join(registry_dir, f"{name}.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_schema(registry_dir, name):
    path = os.path.join(registry_dir, "schema", f"{name}.schema.json")
    with open(path, "r") as f:
        return json.load(f)


ROOT = None  # set from --root; default is the registry dir's parent


def repo_root(registry_dir):
    """Referenced paths are repo-relative; the repo root is --root or the registry dir's parent."""
    return ROOT or os.path.dirname(os.path.abspath(registry_dir))


def validate_registry(registry_dir, name):
    """Validate a single registry. Returns list of (file, reason) failures."""
    failures = []
    registry = load_registry(registry_dir, name)
    schema = load_schema(registry_dir, name)

    # JSON Schema validation
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(registry), key=lambda e: e.path)
    for error in errors:
        failures.append((f"{name}.yaml", f"schema error at {'/'.join(str(p) for p in error.path)}: {error.message}"))

    entries = registry.get("entries", [])

    # Name uniqueness
    names = [e.get("name") for e in entries]
    seen = set()
    for n in names:
        if n in seen:
            failures.append((f"{name}.yaml", f"duplicate entry name: {n}"))
        seen.add(n)

    # Secret field detection
    for i, entry in enumerate(entries):
        for field in SECRET_FIELDS:
            if field in entry:
                failures.append((f"{name}.yaml", f"entry {i} ({entry.get('name', '?')}): literal secret field '{field}'"))

    # Hosted backends must have spend_cap_usd
    if name == "backends":
        for i, entry in enumerate(entries):
            if entry.get("kind") == "hosted" and "spend_cap_usd" not in entry:
                failures.append((f"{name}.yaml", f"entry {i} ({entry.get('name', '?')}): hosted backend missing spend_cap_usd"))

    # Path existence checks
    if name in ("skills", "prompts"):
        for i, entry in enumerate(entries):
            path = entry.get("path", "")
            if not os.path.exists(os.path.join(repo_root(registry_dir), path)):
                failures.append((f"{name}.yaml", f"entry {i} ({entry.get('name', '?')}): path does not exist: {path}"))

    return failures


def render_tools(registry_dir):
    """Render tools registry to agentgateway mcp targets config."""
    registry = load_registry(registry_dir, "tools")
    entries = sorted(registry.get("entries", []), key=lambda e: e.get("name", ""))
    targets = []
    for entry in entries:
        # agentgateway takes the full MCP URL (scheme and path included) as `host`.
        targets.append({"name": entry["name"], "mcp": {"host": entry["mcp_url"]}})
    return {"mcp": {"targets": targets}}


def render_backends(registry_dir):
    """Render backends registry to model list hint."""
    registry = load_registry(registry_dir, "backends")
    entries = sorted(registry.get("entries", []), key=lambda e: e.get("name", ""))
    models = []
    for entry in entries:
        models.append({"name": entry["name"], "kind": entry["kind"], "url": entry["base_url"]})
    return {"llm": {"models": models}}


def main():
    parser = argparse.ArgumentParser(description="Validate gateway registries")
    parser.add_argument("--registry-dir", default="registries", help="Directory containing registry files")
    parser.add_argument("--render", action="store_true", help="Render gateway config fragments")
    parser.add_argument("--root", default=None, help="Repo root that skill/prompt paths are relative to (default: parent of --registry-dir)")
    args = parser.parse_args()
    global ROOT
    ROOT = os.path.abspath(args.root) if args.root else None

    all_failures = []
    counts = {}

    for name in REGISTRIES:
        failures = validate_registry(args.registry_dir, name)
        all_failures.extend(failures)
        registry = load_registry(args.registry_dir, name)
        counts[name] = len(registry.get("entries", []))

    if all_failures:
        for file, reason in all_failures:
            print(f"FAIL {file}: {reason}")
        sys.exit(1)

    print(f"PASS registries: backends={counts['backends']} tools={counts['tools']} skills={counts['skills']} prompts={counts['prompts']}")

    if args.render:
        print("--- mcp targets ---")
        print(yaml.dump(render_tools(args.registry_dir), default_flow_style=False))
        print("--- llm models ---")
        print(yaml.dump(render_backends(args.registry_dir), default_flow_style=False))


if __name__ == "__main__":
    main()
