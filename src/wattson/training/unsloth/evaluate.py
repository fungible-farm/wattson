"""M06 evaluation: base vs fine-tuned pass@1 on held-out solver-verified tasks.

Loads Qwen3.5-4B (base) and Qwen3.5-4B + LoRA adapter (fine-tuned), runs each on
an audited held-out set, and measures pass@1. Fit scales are independently
re-solved against the task's physical target; other answers are compared with
solver-generated references. Domain improvement alone does not establish G5.

The critical value is extracted per subfamily:
  - fit subfamilies: scale factor s, checked by a fresh physical solver run.
  - pf-report: iterations, min/max bus voltage, max line loading, violations.
  - mcp-trajectory: final status + iterations.

Usage (inside the unsloth-gb10 container, on the GPU):
    python training/unsloth/evaluate.py \
        --adapter training/unsloth/out/qwen3.5-4b-lora \
        --splits benchmarks/power-agent-bench-lite/results/domain-held-out-20261020/examples.jsonl \
        --exclude wattson/datasets/smoke/examples.jsonl \
        --out training/unsloth/out/eval_results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from wattson.datasets.audit import audit_rows, load_rows
from wattson.training.unsloth.runtime import load_checkpoint

# --- Tolerances for pass@1 comparison (per subfamily) -----------------------
# Reports print voltages to four decimals and loading to two. Allow one last
# printed digit; iteration and violation counts must match exactly.
REPORT_VOLT_TOL: float = 1e-4
REPORT_LOAD_TOL: float = 0.01
# mcp-trajectory: status exact, iterations exact.

# Subfamilies that report a single scale factor s.
FIT_SUBFAMILIES = frozenset(
    {"pf-load-scale-fit", "pf-line-rating-fit", "pf-gen-dispatch-fit"}
)

# M09 uncertainty reporting: 95% intervals, 9,999 paired bootstrap resamples.
# Percentile intervals avoid undefined BCa acceleration for identical outcomes.
CONFIDENCE_LEVEL: float = 0.95
BOOTSTRAP_RESAMPLES: int = 9999
BOOTSTRAP_SEED: int = 42
DOMAIN_IMPROVEMENT_THRESHOLD: float = 0.10  # MISSION.md G5: +10 pp.

SYSTEM_PROMPT = (
    "You are a power-systems engineer with access to an AC power flow solver and "
    "the PowerMCP tools. Solve the task exactly as stated and report the result "
    "in the same format as the reference solution."
)


def load_model(model_name: str, adapter_path: str | None, dtype: str = "bfloat16"):
    """Load the base model or the base + LoRA adapter.

    Args:
        model_name: HuggingFace model id (e.g. Qwen/Qwen3.5-4B).
        adapter_path: Path to the LoRA adapter (None for base).
        dtype: Load dtype.

    Returns:
        (model, text_tokenizer) where text_tokenizer is the plain text tokenizer
        (Qwen3.5-4B is a VL model; FastLanguageModel returns a Qwen3VLProcessor,
        and we use its .tokenizer for text).
    """
    model, processor = load_checkpoint(model_name, adapter_path, dtype)
    text_tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    model.eval()
    return model, text_tokenizer


def generate_answer(model, tokenizer, prompt: str, max_new_tokens: int = 512) -> str:
    """Generate the model's answer for a task prompt."""
    import torch

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer([text], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,  # greedy for deterministic pass@1
        )
    # Strip the input prompt tokens.
    generated = output_ids[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def extract_scale(text: str) -> float | None:
    """Extract the scale factor s from a fit answer (e.g. 's = 1.062500')."""
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    matches = re.findall(rf"(?<!\w)s\s*=\s*({number})(?![\w.])", text)
    if matches:
        values = {float(value) for value in matches}
        return values.pop() if len(values) == 1 else None
    # Fit tasks explicitly request only the scale factor, so bare numbers are
    # valid answers. Other prose without an unambiguous s assignment is not.
    return float(text.strip()) if re.fullmatch(number, text.strip()) else None


def extract_report(text: str) -> dict | None:
    """Extract the pf-report critical fields from an answer."""
    out: dict = {}
    m = re.search(r"converged in (\d+) iterations", text)
    if m:
        out["iterations"] = int(m.group(1))
    m = re.search(r"[Mm]in bus voltage\s*=\s*([0-9]*\.?[0-9]+)\s*pu", text)
    if m:
        out["min_v"] = float(m.group(1))
    m = re.search(r"[Mm]ax bus voltage\s*=\s*([0-9]*\.?[0-9]+)\s*pu", text)
    if m:
        out["max_v"] = float(m.group(1))
    m = re.search(r"max line loading\s*=\s*([0-9]*\.?[0-9]+)\s*%", text)
    if m:
        out["max_load"] = float(m.group(1))
    m = re.search(r"voltage violations\s*=\s*(\d+)", text)
    if m:
        out["violations"] = int(m.group(1))
    out["converged"] = bool(re.search(r"\bconverged in \d+ iterations\b", text))
    if re.search(r"failed to execute", text, re.IGNORECASE):
        out["converged"] = False
        out["outcome"] = "execution_failure"
    elif re.search(r"did not converge|non[- ]convergen", text, re.IGNORECASE):
        out["converged"] = False
        out["outcome"] = "non_convergence"
    elif out["converged"]:
        out["outcome"] = "converged"
    else:
        return None
    return out


def extract_trajectory(text: str) -> dict | None:
    """Extract the mcp-trajectory critical fields (final status + iterations)."""
    out: dict = {}
    statuses = re.findall(r"status=(\w+)", text)
    if statuses:
        out["final_status"] = statuses[-1]
    m = re.search(r"'iterations':\s*(\d+)", text)
    if m:
        out["iterations"] = int(m.group(1))
    return out if out else None


def compare(subfamily: str, model_text: str, expected_text: str) -> bool:
    """Return True if the model's answer matches the reference within tolerance."""
    if subfamily in FIT_SUBFAMILIES:
        raise ValueError("Fit correctness requires score_answer and a physical solver check")
    if subfamily == "pf-report":
        mv = extract_report(model_text)
        ev = extract_report(expected_text)
        if mv is None or ev is None:
            return False
        if mv["outcome"] != ev["outcome"]:
            return False
        if "iterations" in ev and mv.get("iterations") != ev["iterations"]:
            return False
        for key, tol in (("min_v", REPORT_VOLT_TOL), ("max_v", REPORT_VOLT_TOL), ("max_load", REPORT_LOAD_TOL)):
            if key in ev and (key not in mv or abs(mv[key] - ev[key]) > tol):
                return False
        return "violations" not in ev or mv.get("violations") == ev["violations"]
    if subfamily == "mcp-trajectory":
        mv = extract_trajectory(model_text)
        ev = extract_trajectory(expected_text)
        if mv is None or ev is None:
            return False
        if mv.get("final_status") != ev.get("final_status"):
            return False
        return "iterations" not in ev or mv.get("iterations") == ev["iterations"]
    if subfamily == "pf-worst-contingency":
        # Judgement suite (wattson/datasets/judgement.py): same line, loading within tolerance.
        from wattson.datasets.judgement import WORST_ANSWER

        mv, ev = WORST_ANSWER.search(model_text), WORST_ANSWER.search(expected_text)
        return (mv is not None and ev is not None and int(mv.group(1)) == int(ev.group(1))
                and abs(float(mv.group(2)) - float(ev.group(2))) <= REPORT_LOAD_TOL)
    # Unknown subfamily: fall back to exact string match.
    return model_text.strip() == expected_text.strip()


def score_answer(task: dict, answer: str) -> tuple[bool, dict, list[str]]:
    """Re-solve numerical fit candidates with M05's independent solver checker.

    A fixed scale-factor tolerance cannot establish that the physical target
    was met. Other families retain reference matching and are labelled as such
    in their raw results; they do not establish tool execution or evidence use.
    """
    if task["subfamily"] in FIT_SUBFAMILIES:
        scale = extract_scale(answer)
        inputs = task["task"]["inputs"]
        if scale is None or not math.isfinite(scale):
            return False, {}, ["Missing or non-finite scale factor"]
        if not inputs["scale_lo"] <= scale <= inputs["scale_hi"]:
            return False, {"scale_factor": scale}, ["Scale factor outside task interval"]
        from wattson.distillation.filter import _check_fit

        return _check_fit(task, {"scale_factor": scale})
    if task["subfamily"] == "pf-derived":
        # A sum, difference, mean or ranking over several solver results, graded by value, whatever the wording.
        from wattson.datasets.derived import grade as grade_derived

        return grade_derived(task, answer)
    return (
        compare(task["subfamily"], answer, task["expected"]["answer"]),
        {"verification": "solver-generated reference matching"},
        [],
    )


def uncertainty(base_results: list[dict], tuned_results: list[dict]) -> dict:
    """Report Wilson pass-rate intervals and a paired percentile delta interval.

    Resample matching task indices together using scipy.stats.bootstrap:
    https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html
    """
    import numpy as np
    from scipy.stats import binomtest, bootstrap

    if len(base_results) < 2 or len(base_results) != len(tuned_results):
        raise ValueError("Need at least two paired task results")
    if [row["example_id"] for row in base_results] != [row["example_id"] for row in tuned_results]:
        raise ValueError("Base and tuned result IDs must match in order")
    base = np.array([row["pass"] for row in base_results], dtype=float)
    tuned = np.array([row["pass"] for row in tuned_results], dtype=float)

    def delta(first, second, axis):
        """Use the same paired task indices for both models' pass indicators."""
        return np.mean(second - first, axis=axis)

    interval = bootstrap(
        (base, tuned), delta, paired=True, vectorized=True,
        n_resamples=BOOTSTRAP_RESAMPLES, method="percentile",
        confidence_level=CONFIDENCE_LEVEL, rng=np.random.default_rng(BOOTSTRAP_SEED),
    ).confidence_interval
    report = {
        "confidence_level": CONFIDENCE_LEVEL,
        "delta_method": "paired percentile bootstrap",
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "delta": [float(interval.low), float(interval.high)],
    }
    for label, values in (("base", base), ("fine_tuned", tuned)):
        ci = binomtest(int(values.sum()), len(values)).proportion_ci(
            confidence_level=CONFIDENCE_LEVEL, method="wilson"
        )
        report[label] = [float(ci.low), float(ci.high)]
    return report


def load_tasks(split_paths: list[str]) -> list[dict]:
    return [row for path in split_paths for row in load_rows(Path(path))]


def evaluate_model(
    label: str, model, tokenizer, tasks: list[dict], raw_path: Path | None = None
) -> dict:
    """Generate and score each task, retaining completed raw rows on disk."""
    if raw_path is not None:
        # A new run must not silently mix with an interrupted or older run.
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.open("x").close()
    results = []
    n_pass = 0
    t0 = time.time()
    for i, task in enumerate(tasks):
        prompt = task["task"]["prompt"]
        expected = task["expected"]["answer"]
        subfamily = task["subfamily"]
        ans = generate_answer(model, tokenizer, prompt)
        ok, evidence, diagnostics = score_answer(task, ans)
        n_pass += int(ok)
        results.append(
            {
                "example_id": task["example_id"],
                "subfamily": subfamily,
                "pass": ok,
                "model_answer": ans,
                "expected_answer": expected,
                "verification_evidence": evidence,
                "diagnostics": diagnostics,
            }
        )
        if raw_path is not None:
            with raw_path.open("a") as stream:
                stream.write(json.dumps(results[-1]) + "\n")
        if (i + 1) % 10 == 0:
            print(f"[{label}] {i+1}/{len(tasks)} pass={n_pass}", flush=True)
    wall = time.time() - t0
    return {
        "label": label,
        "n_tasks": len(tasks),
        "n_pass": n_pass,
        "pass_at_1": n_pass / len(tasks) if tasks else 0.0,
        "wall_s": wall,
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-4B")
    ap.add_argument("--adapter", required=True, help="Path to the LoRA adapter")
    ap.add_argument("--splits", nargs="+", required=True, help="Held-out split files")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument(
        "--exclude", nargs="+",
        default=["wattson/datasets/smoke/splits/train.jsonl"],
        help="Training and teacher-correction sources to check for contamination",
    )
    args = ap.parse_args()

    tasks = load_tasks(args.splits)
    excluded = [row for path in args.exclude for row in load_rows(Path(path))]
    audit = audit_rows(tasks, excluded)
    if not audit["clean"]:
        raise ValueError(f"Held-out independence audit failed: {audit}")
    print(f"Loaded {len(tasks)} held-out tasks", flush=True)
    source_hashes = {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in args.splits + args.exclude
    }
    raw_dir = Path(args.out).parent / (Path(args.out).stem + "-raw")
    raw_dir.mkdir(parents=True, exist_ok=False)
    (raw_dir / "inputs.json").write_text(json.dumps(source_hashes, indent=2))
    import torch

    # Base model.
    print("=== Evaluating BASE model ===", flush=True)
    base_model, base_tok = load_model(args.model, adapter_path=None)
    base_revision = getattr(base_model.config, "_commit_hash", None)
    base_res = evaluate_model("base", base_model, base_tok, tasks, raw_dir / "base.jsonl")
    del base_model
    torch.cuda.empty_cache()

    # Fine-tuned model.
    print("=== Evaluating FINE-TUNED model ===", flush=True)
    ft_model, ft_tok = load_model(args.model, adapter_path=args.adapter)
    tuned_base_revision = getattr(ft_model.config, "_commit_hash", None)
    if base_revision and tuned_base_revision and base_revision != tuned_base_revision:
        raise ValueError("Base and adapter runs resolved different base model revisions")
    if (
        base_tok.get_vocab() != ft_tok.get_vocab()
        or base_tok.chat_template != ft_tok.chat_template
        or base_tok.all_special_ids != ft_tok.all_special_ids
    ):
        raise ValueError("Base and tuned tokenizer/template conditions differ")
    ft_res = evaluate_model("fine_tuned", ft_model, ft_tok, tasks, raw_dir / "fine_tuned.jsonl")

    delta = ft_res["pass_at_1"] - base_res["pass_at_1"]
    report = {
        "model": args.model,
        "adapter": args.adapter,
        "base_revision": base_revision,
        "tuned_base_revision": tuned_base_revision,
        "n_tasks": len(tasks),
        "independence_audit": audit,
        "source_sha256": source_hashes,
        "raw_directory": str(raw_dir),
        "runtime": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0),
            "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
            "device": str(ft_model.device),
        },
        "adapter_sha256": hashlib.sha256(
            (Path(args.adapter) / "adapter_model.safetensors").read_bytes()
        ).hexdigest(),
        "base": {k: base_res[k] for k in ("n_tasks", "n_pass", "pass_at_1", "wall_s")},
        "fine_tuned": {k: ft_res[k] for k in ("n_tasks", "n_pass", "pass_at_1", "wall_s")},
        "delta_pass_at_1": delta,
        "g5_threshold": DOMAIN_IMPROVEMENT_THRESHOLD,
        "domain_threshold_met": delta >= DOMAIN_IMPROVEMENT_THRESHOLD,
        "g5_met": False,
        "g5_pending": "General Python regression and critical fabrication suites are required separately",
        "uncertainty": uncertainty(base_res["results"], ft_res["results"]),
        "base_results": base_res["results"],
        "fine_tuned_results": ft_res["results"],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nBASE pass@1: {base_res['pass_at_1']:.4f} ({base_res['n_pass']}/{base_res['n_tasks']})")
    print(f"FT   pass@1: {ft_res['pass_at_1']:.4f} ({ft_res['n_pass']}/{ft_res['n_tasks']})")
    print(f"DELTA: {delta:+.4f} (domain threshold +0.10) -> {'MET' if report['domain_threshold_met'] else 'NOT MET'}")
    print("G5 remains pending the general Python and critical fabrication suites")
    print(f"EVAL PASS (results written to {args.out})")


if __name__ == "__main__":
    main()
