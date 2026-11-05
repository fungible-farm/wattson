# PowerCoder study commands (mounted in the root Justfile as `mod powercoder`).
# `just powercoder` lists them; run as `just powercoder::<recipe>`. Paths are relative to the
# repo root, where these recipes run.
#
# Two rules these recipes encode (AGENTS.md):
# - Never run the full `pytest tests/` while a GPU stage runs: it loads llama-swap's 96 GB model (OOM).
# - Every model-evaluation attempt starts its MCP server from the working tree, so code that a
#   running evaluation imports changes only in a git worktree, merged after the GPU queue.

set working-directory := '..'

out := "training/unsloth/out"
smoke := "training/unsloth/out/smoke-validation-20-20261024-v2/examples.jsonl"
split := "training/unsloth/out/train-split-20261024-v2/examples.jsonl"

# Default: list this module's recipes.
default:
    @just --list powercoder

# GPU queue: the agent-study units, then each run root's cells with finished/total and passes.
status:
    #!/usr/bin/env bash
    date
    systemctl --user list-units 'powercoder-agent-study*' --all --no-legend | cut -c1-110
    for root in {{out}}/agent-study-r6b*; do
        [[ -f "$root/pipeline.jsonl" ]] || continue
        echo "== $root: last event $(tail -1 "$root/pipeline.jsonl" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["kind"])')," \
             "driver errors $(grep -c '"kind": "driver_error"' "$root/pipeline.jsonl")"
        for cell in "$root"/*/; do
            [[ -f "$cell/results.jsonl" ]] || continue
            done=$(grep -o '"example_id"' "$cell/results.jsonl" | wc -l)
            pass=$([[ -f "$cell/summary.json" ]] && python3 -c "import json; print(json.load(open('$cell/summary.json'))['n_pass'])" || echo "-")
            echo "   $(basename "$cell"): $done done, pass $pass"
        done
    done
    grep MemAvailable /proc/meminfo

# One cause per attempt, in a fixed precedence (AGENTS.md failure modes). e.g. run=training/unsloth/out/agent-study-r6b/qwen-student
failure-modes run:
    uv run python -m powercoder.inference.failure_modes {{run}}

# Per-call/per-turn metrics of the strict-tool-calling 2x2 (backlog 0011).
strict-metrics root=(out + "/agent-study-r6b-strict"):
    uv run python -m powercoder.inference.strict_arm_metrics {{root}} --tasks {{smoke}}

# Solver-verified comparison of two runs: v2 (headline) and the field-level v3 (backlog 0010).
scorecard base student dest:
    uv run python -m powercoder.inference.domain_scorecard --base-dir {{base}} --student-dir {{student}} --out {{dest}}

# Build tool-call training trajectories from the training split; namespace=true adds ls/get (0026).
trajectories dest namespace="false":
    uv run python -m powercoder.datasets.trajectories --split {{split}} --out-dir {{dest}} \
        {{ if namespace == "true" { "--namespace-tools" } else { "" } }}

# Namespace walk (backlog 0026), e.g. `just powercoder::ls /case/snem-sa-2000/bus`.
ls path="/" offset="0":
    uv run python -c "import json,sys; from powercoder.inference.namespace import call; print(json.dumps(call('ls', {'path': sys.argv[1], 'offset': int(sys.argv[2])}), indent=1))" {{path}} {{offset}}

# One namespace value, e.g. `just powercoder::get /case/snem-sa-2000/bus/985/vn_kv`.
get path:
    uv run python -c "import json,sys; from powercoder.inference.namespace import call; print(json.dumps(call('get', {'path': sys.argv[1]}), indent=1))" {{path}}

# CIM/CGMES tests: the export, SHACL, round trip, and the profile split (backlog 0018, 0034).
semantic-test:
    uv run python -m pytest -q powercoder/semantic

# CGMES export vs pypowsybl on an x86-64 peer (no ARM64 wheel); exit 1 above 1e-4 pu (0034).
cgmes-peer-check peer="sm3lly.lan" dest="":
    scripts/cgmes_peer_check.sh {{peer}} {{dest}}

# Why: run directories record task paths relative to the tree that produced them, so to score them
# with code from another checkout (e.g. a worktree), run from that tree with -P (cwd off sys.path).
# Run another checkout's code here, e.g. `just powercoder::with-code ../wt -m powercoder.inference.domain_scorecard ...`.
with-code code_dir +cmd:
    PYTHONPATH={{code_dir}} {{code_dir}}/.venv/bin/python -P {{cmd}}

# Leakage-free demand hindcast on the cached AEMO month (backlog 0021), e.g. region=VIC1.
hindcast region="SA1":
    uv run python -m powercoder.forecasting.hindcast --region {{region}}

# Build the 40-task judgement suite (0008: intent, open fit, worst contingency, held-out network), frozen.
judgement-suite dest:
    uv run python -m powercoder.datasets.judgement --out-dir {{dest}}

# The scripted procedure as an agent on a frozen suite (G0 baseline a): its ceiling on that suite.
script-baseline tasks dest:
    uv run python -m powercoder.inference.script_baseline --tasks {{tasks}} --exclude {{split}} --out {{dest}}

# G2 analyses on snem-sa-2000: AC power-flow proof, the Q-V mode, and dV/dQ at its top bus (finite-difference checked).
ac-analysis:
    uv run python -c "import json; from powercoder.inference import powermcp as pm; from powercoder.engineering.ac_analysis import ac_power_flow, qv_modal, vq_sensitivity; net,_=pm._load_network_object('snem-sa-2000'); p=ac_power_flow(net)['proof']; m=qv_modal(net); print(json.dumps({'feasibility': p['feasibility_status'], 'kcl_residual_mva': p['equation_residuals'], 'conditioning': p['conditioning'], 'qv_mode': m, 'dv_dq': vq_sensitivity(net, m['participating_buses'][0][0])}, indent=1, default=str))"

# G3: a frozen pool of load-scale fits from snem-sa-2000 and snem-1803 (discovered elements), for the RL pilot (0017).
procedural-pool dest per_network="50" suites_root="/home/brianh/promptexecution/fungible/nem-poweragent-lab":
    uv run python -m powercoder.datasets.procedural --out-dir {{dest}} --per-network {{per_network}} --suites-root {{suites_root}}

# G1: the Rust command IR's tests, incl. byte-identity of its emitted schema with the served one (0015).
command-ir-test:
    cd rust && cargo test -p command-ir && cargo build -q --release -p command-ir --bin lift
    uv run python -m pytest -q powercoder/inference/test_command_ir.py powercoder/test_commands.py powercoder/inference/test_protocol_suite.py powercoder/inference/test_replay.py

# Execute IR requests (JSON lines on stdin), e.g. the Rust fixture: just powercoder::execute < rust/command-ir/tests/requests.jsonl
execute:
    uv run python -m powercoder.commands

# G1 replay: re-run every recorded tool call in a run root's journals and diff against what the model saw (0015).
replay root:
    uv run python -m powercoder.inference.replay {{root}}/*/ex_*.jsonl | tail -1

# G1 protocol suite: share of each model's run_study_trial calls the Rust IR accepts (exit criterion ≥99%), repairs, tool errors (0015).
protocol-suite root:
    uv run python -m powercoder.inference.protocol_suite {{root}}

# PC-REQ-4: each model's errors by class, recovered cost and unrecoverable lost time (seconds and tokens).
error-ledger root:
    uv run python -m powercoder.inference.error_ledger {{root}}

# Freeze the unsolvable suite (refusal without guessing) from another frozen suite's fits and N-1 reports.
unsolvable-suite source dest:
    uv run python -m powercoder.datasets.unsolvable --from {{source}} --out-dir {{dest}}

# r7: after the r6b queue, fast-forward main, retrain both students on IR 0.2.0 trajectories, run r7 (supervised unit).
r7-chain:
    systemd-run --user --unit=powercoder-r7-chain --working-directory=/home/brianh/promptexecution/fungible/nem-poweragent-lab /bin/bash {{justfile_directory()}}/../scripts/r7_chain.sh

# G3 GRPO pilot (0017): armed behind r7 as unit powercoder-grpo-wait; this launches the waiter.
grpo-wait:
    systemd-run --user --unit=powercoder-grpo-wait --working-directory=/home/brianh/promptexecution/fungible/nem-poweragent-lab /bin/bash {{justfile_directory()}}/../scripts/grpo_wait.sh
