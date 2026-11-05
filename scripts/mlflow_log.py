#!/usr/bin/env python3
"""Log verified evaluation run directories to an MLflow tracking server.

    mlflow_log.py --tracking-url URL --experiment NAME --arm ARMNAME RUN_DIR [RUN_DIR ...]

Each run directory is verified first (via results_table.verify_dir), then logged as
one MLflow run. Idempotent: if a run with the same summary sha256 already exists,
it is skipped.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Import verify_dir from sibling results_table.py
sys.path.insert(0, os.path.dirname(__file__))
from results_table import verify_dir, UNANSWERED


def fail(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def read_json(path):
    with open(path) as f:
        return json.load(f)


class HttpError(Exception):
    """An HTTP error from the tracking server, with its status code and the start of its body."""

    def __init__(self, code, body):
        super().__init__(f"{code} {body}")
        self.code = code
        self.body = body


def http_json(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode()[:200])


def get_or_create_experiment(base_url, name):
    """Return the experiment id. MLflow answers get-by-name with {"experiment": {...}} or a 404 when it does not exist."""
    url = f"{base_url}/api/2.0/mlflow/experiments/get-by-name?experiment_name={urllib.parse.quote(name)}"
    try:
        return http_json("GET", url)["experiment"]["experiment_id"]
    except HttpError as e:
        if e.code != 404:
            raise
    created = http_json("POST", f"{base_url}/api/2.0/mlflow/experiments/create", {"name": name})
    print(f"created experiment {created['experiment_id']} ({name})")
    return created["experiment_id"]


def find_existing_run(base_url, experiment_id, sha):
    """The REST field is 'filter' (the Python client's 'filter_string' would be ignored and match every run)."""
    body = {
        "experiment_ids": [experiment_id],
        "filter": f"tags.wattson_summary_sha256 = '{sha}'",
        "max_results": 1,
    }
    return http_json("POST", f"{base_url}/api/2.0/mlflow/runs/search", body).get("runs", [])


def create_run(base_url, experiment_id, run_name, tags):
    body = {
        "experiment_id": experiment_id,
        "run_name": run_name,
        "start_time": int(time.time() * 1000),
        "tags": [{"key": k, "value": v} for k, v in tags.items()],
    }
    return http_json("POST", f"{base_url}/api/2.0/mlflow/runs/create", body)["run"]


def log_batch(base_url, run_id, metrics, params, tags):
    body = {
        "run_id": run_id,
        "metrics": [{"key": k, "value": v, "timestamp": int(time.time() * 1000), "step": 0} for k, v in metrics.items()],
        "params": [{"key": k, "value": v} for k, v in params.items()],
        "tags": [{"key": k, "value": v} for k, v in tags.items()],
    }
    return http_json("POST", f"{base_url}/api/2.0/mlflow/runs/log-batch", body)


def finish_run(base_url, run_id):
    body = {"run_id": run_id, "status": "FINISHED", "end_time": int(time.time() * 1000)}
    return http_json("POST", f"{base_url}/api/2.0/mlflow/runs/update", body)


def get_repo_root():
    """Find the repo root (directory above scripts/)."""
    scripts_dir = Path(__file__).resolve().parent
    return scripts_dir.parent


def get_git_revision():
    """Get git rev-parse HEAD from the repo root."""
    repo_root = get_repo_root()
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"could not get git revision: {result.stderr.strip()}")
    return result.stdout.strip()


def plan_for(run_dir, arm, kit_ref, kit_digest, git_rev):
    """Verify one run directory (exits 2 on any problem) and return what to log. No network."""
    summary, results, terminal_states = verify_dir(run_dir)
    inputs = summary.get("inputs", {})
    if not inputs:
        fail(f"{run_dir}: summary has no inputs")
    suite = Path(next(iter(inputs))).parent.name
    summary_sha = sha256_file(Path(run_dir) / "summary.json")

    params = {}
    for field in ("model", "temperature", "max_turns", "max_tool_calls", "max_tokens", "tool_set",
                  "first_turn_tool_choice", "enforce_provenance", "concurrency", "attempts_per_task",
                  "max_retries", "api_key_env"):
        if field in summary:
            params[field] = str(summary[field])
    if "extra_body" in summary:
        params["extra_body"] = json.dumps(summary["extra_body"], sort_keys=True)
    params["arm"] = arm
    params["suite"] = suite
    for k, v in params.items():
        if len(v) > 6000:
            fail(f"param {k} is {len(v)} characters (the limit is 6000; nothing is truncated silently)")

    metrics = {
        "n_tasks": float(summary["n_tasks"]),
        "n_pass": float(summary["n_pass"]),
        "pass_at_1": float(summary["pass_at_1"]),
        "protocol_failures": float(sum(1 for t in terminal_states if t in UNANSWERED)),
        "provenance_failures": float(sum(1 for r in results if not r["provenance_pass"])),
    }
    tags = {
        "wattson_summary_sha256": summary_sha,
        "wattson_suite": suite,
        "wattson_arm": arm,
        "wattson_git_revision": git_rev,
    }
    if kit_ref:
        tags["wattson_kit_ref"] = kit_ref
    if kit_digest:
        tags["wattson_kit_digest"] = kit_digest
    return {"dir": run_dir, "name": f"{arm}-{suite}-{summary_sha[:8]}", "sha": summary_sha,
            "params": params, "metrics": metrics, "tags": tags}


def main():
    parser = argparse.ArgumentParser(description="Log verified evaluation runs to MLflow")
    parser.add_argument("--tracking-url", required=True, help="MLflow tracking server URL")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    parser.add_argument("--arm", required=True, help="Arm name")
    parser.add_argument("--kit-ref", default=None, help="Evidence kit reference")
    parser.add_argument("--kit-digest", default=None, help="Evidence kit digest (sha256:...)")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a tracking URL that is not on this machine")
    parser.add_argument("run_dirs", nargs="+", help="Run directories to log")
    args = parser.parse_args()

    url = args.tracking_url.rstrip("/")
    if not (url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        if not args.allow_remote:
            fail(f"tracking URL {url} is not local; pass --allow-remote to log to another machine")
        print(f"warning: logging to the non-local tracking server {url}")

    # Verify EVERYTHING first: an unverifiable directory must not cause a single request, not even creating the experiment.
    git_rev = get_git_revision()
    plans = [plan_for(d, args.arm, args.kit_ref, args.kit_digest, git_rev) for d in args.run_dirs]

    try:
        experiment_id = get_or_create_experiment(url, args.experiment)
        for plan in plans:
            existing = find_existing_run(url, experiment_id, plan["sha"])
            if existing:
                print(f"already logged {existing[0]['info']['run_id']}")
                continue
            run = create_run(url, experiment_id, plan["name"], plan["tags"])
            run_id = run["info"]["run_id"]
            log_batch(url, run_id, plan["metrics"], plan["params"], plan["tags"])
            finish_run(url, run_id)
            print(f"logged run_id={run_id} experiment={experiment_id} run_name={plan['name']}")
    except HttpError as e:
        fail(f"{e.code} {e.body}", code=1)
    except urllib.error.URLError as e:
        fail(f"cannot reach {url}: {e.reason}", code=1)


if __name__ == "__main__":
    main()
