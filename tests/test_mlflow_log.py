import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PY = sys.executable
SCRIPT = Path(__file__).parent.parent / "scripts" / "mlflow_log.py"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def make_run(tmp_path, name, n_tasks, n_pass, enforce=False):
    p = tmp_path / name
    p.mkdir()
    results = []
    for i in range(n_tasks):
        eid = f"ex_{i:04d}"
        journal_path = p / f"{eid}.jsonl"
        journal = [
            {"kind": "turn", "payload": {"turn": 1, "tool": "python"}},
            {"kind": "terminal", "payload": {"state": "answered"}},
        ]
        journal_path.write_text("\n".join(json.dumps(e) for e in journal))
        passed = i < n_pass
        results.append({
            "kind": "result",
            "payload": {
                "example_id": eid,
                "passed": passed,
                "numerical_pass": passed,
                "tool_evidence_pass": passed,
                "provenance_pass": not (i == n_tasks - 1 and not passed),
                "journal_sha256": sha256_file(journal_path),
            },
        })
    (p / "results.jsonl").write_text("\n".join(json.dumps(r) for r in results))
    summary = {
        "model": "test-model",
        "complete": True,
        "independence_audit": {"clean": True, "rows": n_tasks},
        "n_tasks": n_tasks,
        "n_pass": n_pass,
        "pass_at_1": n_pass / n_tasks,
        "enforce_provenance": enforce,
        "temperature": 0.0,
        "max_turns": 12,
        "max_tool_calls": 24,
        "max_tokens": 8192,
        "tool_set": "study+python2",
        "first_turn_tool_choice": "auto",
        "extra_body": None,
        "max_retries": 0,
        "inputs": {"/path/to/derived/input.json": "yes"},
    }
    (p / "summary.json").write_text(json.dumps(summary))
    return p


class FakeMLflowHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if "get-by-name" in self.path:
            name = self.path.split("experiment_name=")[1]
            if name in self.server.experiments:
                self.send_json(200, {"experiment": {"experiment_id": "1", "name": name}})
            else:
                self.send_json(404, {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "not found"})
        else:
            self.send_json(404, {"error_code": "NOT_FOUND"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.server.requests.append(("POST", self.path, body))
        if self.server.fail_next:
            self.server.fail_next = False
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return
        if "runs/create" in self.path:
            self.server.runs.append(body)
            self.send_json(200, {"run": {"info": {"run_id": f"r{len(self.server.runs)}"}, "data": {}}})
        elif "runs/log-batch" in self.path:
            self.server.batches.append(body)
            self.send_json(200, {})
        elif "runs/search" in self.path:
            runs = []
            f = body.get("filter", "")
            for r in self.server.runs:
                if f and "wattson_summary_sha256" in f:
                    sha = f.split("'")[1]
                    tags = {t["key"]: t["value"] for t in r.get("tags", [])}
                    if tags.get("wattson_summary_sha256") != sha:
                        continue
                runs.append({"info": {"run_id": f"r{self.server.runs.index(r)+1}"}})
            self.send_json(200, {"runs": runs} if runs else {})
        elif "runs/update" in self.path:
            self.send_json(200, {"run_info": {"run_id": body["run_id"], "status": "FINISHED"}})
        elif "experiments/create" in self.path:
            self.server.experiments[body["name"]] = True
            self.send_json(200, {"experiment_id": "1"})
        else:
            self.send_json(404, {"error_code": "NOT_FOUND"})

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeMLflowHandler)
    srv.requests = []
    srv.runs = []
    srv.batches = []
    srv.experiments = {}
    srv.fail_next = False
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def run_script(srv, *args):
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    cmd = [PY, str(SCRIPT), "--tracking-url", url] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def test_new_experiment_and_run(tmp_path):
    srv = start_server()
    try:
        d = make_run(tmp_path, "r1", 4, 3)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r.returncode == 0
        assert "logged run_id=" in r.stdout
        assert any("experiments/create" in p for _, p, _ in srv.requests)
        batch = srv.batches[0]
        params = {p["key"]: p["value"] for p in batch["params"]}
        assert params["model"] == "test-model"
        assert params["temperature"] == "0.0"
        assert params["tool_set"] == "study+python2"
        assert params["arm"] == "test"
        assert params["suite"] == "derived"
        metrics = {m["key"]: m["value"] for m in batch["metrics"]}
        assert metrics["n_tasks"] == 4.0
        assert metrics["n_pass"] == 3.0
        assert metrics["pass_at_1"] == 0.75
        tags = {t["key"]: t["value"] for t in batch["tags"]}
        assert tags["wattson_suite"] == "derived"
        assert tags["wattson_arm"] == "test"
        assert "wattson_summary_sha256" in tags
        assert "wattson_git_revision" in tags
        assert srv.runs[0]["run_name"].startswith("test-derived-")
    finally:
        srv.shutdown()


def test_existing_experiment_reused(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        d = make_run(tmp_path, "r1", 4, 3)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r.returncode == 0
        assert not any("experiments/create" in p for _, p, _ in srv.requests)
    finally:
        srv.shutdown()


def test_idempotent_same_dir(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        d = make_run(tmp_path, "r1", 4, 3)
        r1 = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r1.returncode == 0
        assert len(srv.runs) == 1
        r2 = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r2.returncode == 0
        assert "already logged" in r2.stdout
        assert len(srv.runs) == 1
    finally:
        srv.shutdown()


def test_tampered_dir_no_requests(tmp_path):
    srv = start_server()
    try:
        d = make_run(tmp_path, "r1", 4, 3)
        journal = d / "ex_0000.jsonl"
        journal.write_text(journal.read_text() + " ")
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r.returncode == 2
        assert srv.requests == []
    finally:
        srv.shutdown()


def test_nonlocal_url_refused(tmp_path):
    d = make_run(tmp_path, "r1", 4, 3)
    r = subprocess.run(
        [PY, str(SCRIPT), "--tracking-url", "http://example.invalid:1",
         "--experiment", "wattson", "--arm", "test", str(d)],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "not local" in r.stderr


def test_http_500_from_create(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        srv.fail_next = True
        d = make_run(tmp_path, "r1", 4, 3)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r.returncode == 1
        assert "500" in r.stderr
    finally:
        srv.shutdown()


def test_long_param_exits_2(tmp_path):
    d = make_run(tmp_path, "r1", 4, 3)
    summary = json.loads((d / "summary.json").read_text())
    summary["model"] = "x" * 7000
    (d / "summary.json").write_text(json.dumps(summary))
    r = subprocess.run(
        [PY, str(SCRIPT), "--tracking-url", "http://127.0.0.1:1",
         "--experiment", "wattson", "--arm", "test", str(d)],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "6000" in r.stderr


def test_git_revision_tag(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        d = make_run(tmp_path, "r1", 4, 3)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d))
        assert r.returncode == 0
        tags = {t["key"]: t["value"] for t in srv.batches[0]["tags"]}
        expected = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()
        assert tags["wattson_git_revision"] == expected
    finally:
        srv.shutdown()


def test_two_dirs_one_call(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        d1 = make_run(tmp_path, "r1", 4, 3)
        d2 = make_run(tmp_path, "r2", 4, 2)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test", str(d1), str(d2))
        assert r.returncode == 0
        assert len(srv.runs) == 2
        assert "logged run_id=" in r.stdout
        assert r.stdout.count("logged run_id=") == 2
    finally:
        srv.shutdown()


def test_kit_tags(tmp_path):
    srv = start_server()
    try:
        srv.experiments["wattson"] = True
        d = make_run(tmp_path, "r1", 4, 3)
        r = run_script(srv, "--experiment", "wattson", "--arm", "test",
                       "--kit-ref", "kit-abc", "--kit-digest", "sha256:deadbeef", str(d))
        assert r.returncode == 0
        tags = {t["key"]: t["value"] for t in srv.batches[0]["tags"]}
        assert tags["wattson_kit_ref"] == "kit-abc"
        assert tags["wattson_kit_digest"] == "sha256:deadbeef"
    finally:
        srv.shutdown()
