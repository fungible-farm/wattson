"""``wattson``: one entry point for the quick start. Each subcommand logs what it does (see ``wattson.log``) and returns a shell exit code.

    wattson doctor [--base-url URL] [--model-dir DIR] [--deep] [--json]   is this environment healthy?
    wattson fetch-data                                                    download the CSIRO case files (checksum-verified)
    wattson verify-data                                                   data/ matches MANIFEST.json
    wattson models pull REPO --dest DIR [--force]                         download a released student, verify its hashes
    wattson models verify DIR
    wattson lab {1,2,3} [--step STEP]                                     run a lab's self-check (deterministic, CPU only)
    wattson ask "QUESTION" --base-url URL --model NAME                    one question through the real tool harness
    wattson eval SUITE --base-url URL --model NAME                        run a frozen suite and grade it by re-solving
    wattson pi-setup --base-url URL --model NAME                          write the pi agent's provider + MCP configuration
    wattson pi [-- PI_ARGS]                                               configure pi and run it the way the students were trained
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from wattson import datacheck, doctor, fetch, log, models
from wattson.mcp_env import server_environment

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
LABS: dict[int, tuple[str, str]] = {
    1: ("benchmarks/lab01-simple-loadflow-fit/run.py", "check"),
    2: ("benchmarks/lab02-medium-interconnection-screening/workflow.py", "check"),
    3: ("benchmarks/lab03-advanced-provider-bakeoff/orchestrator.py", "check"),
}
# Suites the released students were evaluated on (data/suites/<dir>/examples.jsonl); the first is the primary endpoint.
SUITES: dict[str, str] = {
    "heldout": "procedural-heldout-20261024-v4", "smoke": "smoke-validation-20-20261024-v2", "judgement": "judgement-20261024-v2",
    "unsolvable": "unsolvable-20261024-v2", "planning": "planning-heldout-20261029", "derived": "derived-heldout-20261030"}
# Task rows the students were trained on; the harness audits that evaluation tasks do not overlap them.
TRAIN_ROWS: str = "tasks/train-pool-split-20261027/examples.jsonl"
TOOL_FLAGS: list[str] = ["--python-tools", "--python-report-fields"]
DEFAULT_CONCURRENCY: int = 4
PI_PROVIDER: str = "wattson"
_LOG = log.get("cli")


def _stream(command: list[str], component: str) -> int:
    """Run a child process, relay each output line as a log event, return its exit code."""
    child = log.get(component)
    child.info("exec", extra={"command": " ".join(command)})
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO_ROOT)
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip(), flush=True)
    code = process.wait()
    child.info("exit", extra={"code": code, "seconds": round(time.monotonic() - started, 1)})
    return code


def cmd_doctor(a: argparse.Namespace) -> int:
    results = doctor.run(a.base_url, a.model_dir, a.deep)
    print(doctor.to_json(results) if a.json else doctor.render(results))
    return 1 if any(r.status == doctor.FAIL for r in results) else 0


def cmd_fetch(_: argparse.Namespace) -> int:
    try:
        results = fetch.fetch_all()
    except fetch.ChecksumMismatch as error:
        _LOG.error("fetch.failed", extra={"error": str(error)})
        return 1
    print(json.dumps(results))
    return 0


def cmd_verify_data(_: argparse.Namespace) -> int:
    checked, problems = datacheck.verify(datacheck.data_dir())
    for line in problems:
        print(line)
    print(f"{'FAIL' if problems else 'PASS'}: {checked} files checked, {len(problems)} problems")
    return 1 if problems else 0


def cmd_models(a: argparse.Namespace) -> int:
    if a.action == "pull":
        print(json.dumps(models.pull(a.repo, Path(a.dest), a.revision, a.force)))
        return 0
    checked, problems = models.verify(Path(a.repo))
    print(f"{'FAIL' if problems else 'PASS'}: {checked} files, {len(problems)} problems {problems[:5] if problems else ''}")
    return 1 if problems else 0


def cmd_lab(a: argparse.Namespace) -> int:
    script, default_step = LABS[a.number]
    missing = {name: state for name, state in fetch.status().items() if state != "ok"}
    if missing:
        _LOG.info("cases.fetching", extra={"files": missing})
        try:
            fetch.fetch_all()
        except Exception as error:  # no network, or a checksum mismatch: say so plainly rather than let the lab fail obscurely
            _LOG.error("cases.unavailable", extra={"error": f"{type(error).__name__}: {error}", "hint": "the labs need the CSIRO case files; connect once or mount them at WATTSON_CASE_DIR"})
            return 1
    return _stream([sys.executable, script, "--step", a.step or default_step], f"lab{a.number}")


def server_root(url: str) -> str:
    """The harness appends ``/v1/chat/completions`` itself, so it wants the server root; accept the OpenAI-style ``.../v1`` form too."""
    return url.rstrip("/").removesuffix("/v1")


def cmd_eval(a: argparse.Namespace) -> int:
    root = datacheck.data_dir()
    tasks = root / "suites" / SUITES[a.suite] / "examples.jsonl"
    out = Path(a.out) if a.out else Path(os.environ.get("WATTSON_OUT", "out")) / f"eval-{a.suite}-{time.strftime('%Y%m%d-%H%M%S')}"
    command = [sys.executable, "-m", "wattson.inference.evaluate_agent", "--base-url", server_root(a.base_url), "--model", a.model, "--tasks", str(tasks),
               "--exclude", str(root / TRAIN_ROWS), "--out", str(out), "--concurrency", str(a.concurrency), *TOOL_FLAGS,
               *(["--enforce-provenance"] if a.enforce else [])]
    _LOG.info("eval.start", extra={"suite": a.suite, "tasks": str(tasks), "out": str(out)})
    return _stream(command, "eval")


def cmd_ask(a: argparse.Namespace) -> int:
    out = Path(os.environ.get("WATTSON_OUT", "out")) / "ask"
    out.mkdir(parents=True, exist_ok=True)
    journal = out / f"ask-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as prompt:
        prompt.write(a.question)
    command = [sys.executable, "-m", "wattson.inference.tool_probe", "--base-url", server_root(a.base_url), "--model", a.model, "--prompt", prompt.name,
               "--study-tools", "--journal", str(journal), *TOOL_FLAGS, *(["--enforce-provenance"] if a.enforce else [])]
    code = _stream(command, "ask")
    _LOG.info("ask.journal", extra={"path": str(journal)})
    answer, calls = final_answer(journal)
    print(f"\nANSWER ({calls} tool call(s); journal {journal}):\n{answer if answer is not None else '(the model gave no final answer; see the journal)'}")
    return code


def final_answer(journal: Path) -> tuple[str | None, int]:
    """The last assistant message's text and how many tool calls the model made, from a harness journal."""
    answer: str | None = None
    calls = 0
    if not journal.exists():
        return None, 0
    for line in journal.read_text().splitlines():
        event = json.loads(line)
        if event["kind"] == "terminal" and event["payload"].get("state") == "guard_refused":
            return None, calls  # the guard rejected the model's last answer; do not echo the number it refused
        if event["kind"] == "model_response":
            message = event["payload"]["choices"][0]["message"]
            calls += len(message.get("tool_calls") or [])
            if not message.get("tool_calls"):
                answer = message.get("content")
    return answer, calls


def pi_directory() -> Path:
    """Where pi keeps its configuration: its own PI_CODING_AGENT_DIR, else ~/.pi/agent."""
    return Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent"))


def configure_pi(base_url: str, model: str, context: int) -> Path:
    """Point pi at a local OpenAI-compatible server and declare the WattsOn tools to the model as ordinary tools.

    Why ``exposure: direct``: pi's default offers MCP tools only inside a ``codemode`` scripting wrapper, and these students were trained on
    plain function calling with ``run_python`` as a tool. Offered ``bash`` and ``codemode`` instead, they invented a command and then a
    number (measured: 0.985354 for a task whose answer is 1.0625; it failed the re-solve check by 18x the tolerance).
    """
    home = pi_directory()
    home.mkdir(parents=True, exist_ok=True)
    models_file = home / "models.json"
    config = json.loads(models_file.read_text()) if models_file.exists() else {}
    config.setdefault("providers", {})[PI_PROVIDER] = {
        "baseUrl": base_url, "api": "openai-completions", "apiKey": "none",
        "models": [{"id": model, "name": model, "reasoning": False, "contextWindow": context, "maxTokens": 8192, "input": ["text"]}]}
    models_file.write_text(json.dumps(config, indent=2) + "\n")
    mcp_file = home / "mcp.json"
    mcp = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
    mcp.setdefault("mcpServers", {})["wattson"] = {
        "command": sys.executable, "args": ["-m", "wattson.inference.mcp_server", "--study-tools", *TOOL_FLAGS], "cwd": str(REPO_ROOT),
        "env": server_environment(), "exposure": "direct"}
    mcp["autoEnableCodemode"] = False
    mcp_file.write_text(json.dumps(mcp, indent=2) + "\n")
    _LOG.info("pi.configured", extra={"models": str(models_file), "mcp": str(mcp_file), "model": model, "exposure": "direct"})
    return home


def pi_command(model: str, extra: list[str]) -> list[str]:
    """The pi command line the students can work with: no built-in bash/edit/write, the trained system prompt, the WattsOn tools only."""
    from wattson.inference.tool_probe import SYSTEM_PROMPT

    return ["pi", "--provider", PI_PROVIDER, "--model", model, "--no-builtin-tools", "--system-prompt", SYSTEM_PROMPT, *extra]


def cmd_pi_setup(a: argparse.Namespace) -> int:
    home = configure_pi(a.base_url, a.model, a.context)
    print(f"configured pi in {home}: provider '{PI_PROVIDER}' -> {a.base_url} (model {a.model}); MCP server 'wattson' (direct exposure)")
    return 0


def cmd_pi(a: argparse.Namespace) -> int:
    """Configure pi, then replace this process with it (interactive unless pi's own --print is passed)."""
    configure_pi(a.base_url, a.model, a.context)
    command = pi_command(a.model, a.pi_args)
    _LOG.info("pi.exec", extra={"args": [c if len(c) < 60 else c[:57] + "..." for c in command]})
    os.execvp(command[0], command)
    return 1  # unreachable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wattson", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR (or WATTSON_LOG_LEVEL)")
    parser.add_argument("--log-format", choices=["json", "text"], default=None, help="(or WATTSON_LOG_FORMAT)")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="check the environment")
    d.add_argument("--base-url"), d.add_argument("--model-dir", type=Path)
    d.add_argument("--deep", action="store_true", help="also request a short completion from the model server")
    d.add_argument("--json", action="store_true")
    d.set_defaults(run=cmd_doctor)
    sub.add_parser("fetch-data", help="download the CSIRO case files").set_defaults(run=cmd_fetch)
    sub.add_parser("verify-data", help="check data/ against MANIFEST.json").set_defaults(run=cmd_verify_data)
    m = sub.add_parser("models", help="pull or verify a released student")
    ms = m.add_subparsers(dest="action", required=True)
    pull = ms.add_parser("pull")
    pull.add_argument("repo"), pull.add_argument("--dest", required=True), pull.add_argument("--revision"), pull.add_argument("--force", action="store_true")
    ms.add_parser("verify").add_argument("repo", help="directory to verify")
    m.set_defaults(run=cmd_models)
    lab = sub.add_parser("lab", help="run a lab's self-check")
    lab.add_argument("number", type=int, choices=sorted(LABS)), lab.add_argument("--step")
    lab.set_defaults(run=cmd_lab)
    ev = sub.add_parser("eval", help="evaluate a model on a frozen suite")
    ev.add_argument("suite", choices=sorted(SUITES)), ev.add_argument("--base-url", required=True), ev.add_argument("--model", required=True)
    ev.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY), ev.add_argument("--out")
    ev.add_argument("--enforce", action="store_true", help="runtime guard: refuse an answer containing a number no tool produced (a separate arm)")
    ev.set_defaults(run=cmd_eval)
    ask = sub.add_parser("ask", help="one question through the tool harness")
    ask.add_argument("question"), ask.add_argument("--base-url", required=True), ask.add_argument("--model", required=True)
    ask.add_argument("--enforce", action="store_true", help="runtime guard: refuse an answer containing a number no tool produced")
    ask.set_defaults(run=cmd_ask)
    pi = sub.add_parser("pi-setup", help="configure the pi agent")
    pi.add_argument("--base-url", required=True), pi.add_argument("--model", required=True), pi.add_argument("--context", type=int, default=32768)
    pi.set_defaults(run=cmd_pi_setup)
    run_pi = sub.add_parser("pi", help="configure pi, then run it with the trained system prompt and only the WattsOn tools")
    run_pi.add_argument("--base-url", default=os.environ.get("WATTSON_BASE_URL", "http://127.0.0.1:8000/v1"))
    run_pi.add_argument("--model", default=os.environ.get("WATTSON_SERVED_MODEL", "wattson")), run_pi.add_argument("--context", type=int, default=32768)
    run_pi.add_argument("pi_args", nargs=argparse.REMAINDER, help="passed to pi, e.g. --print 'a question'")
    run_pi.set_defaults(run=cmd_pi)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    log.configure(arguments.log_level, arguments.log_format)
    try:
        return arguments.run(arguments)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
