"""GRPO pilot pieces: advantages, task slicing, token-exact sequences, the harness rollout, the loss."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from wattson.training import grpo


def test_advantages_are_group_relative_and_vanish_without_signal() -> None:
    a = grpo.advantages([1.0, -1.0, 1.0, -1.0])
    assert sum(a) == pytest.approx(0.0) and a[0] > 0 > a[1]
    assert grpo.advantages([0.9, 0.9, 0.9]) == [0.0, 0.0, 0.0]
    assert grpo.advantages([1.0]) == [0.0]


def test_iterations_draw_different_tasks() -> None:
    tasks = [{"example_id": f"ex_{i:03d}"} for i in range(20)]
    first, second = (grpo.select_tasks(tasks, 8, i, seed=7) for i in (0, 1))
    assert len(first) == len(second) == 8 and not {t["example_id"] for t in first} & {t["example_id"] for t in second}


def test_sequences_use_the_journal_s_exact_token_ids(tmp_path: Path) -> None:
    turn = {"kind": "model_response", "payload": {"prompt_token_ids": [1, 2, 3], "choices": [{"token_ids": [4, 5]}]}}
    (tmp_path / "ex_a-0.jsonl").write_text(json.dumps(turn) + "\n")
    (tmp_path / "ex_a-1.jsonl").write_text(json.dumps(turn) + "\n")
    rows = [{"example_id": "a", "journal": "ex_a-0.jsonl", "advantage": 1.0},
            {"example_id": "a", "journal": "ex_a-1.jsonl", "advantage": 0.0}]
    (tmp_path / "rollouts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    data, counts = grpo.sequences(tmp_path, seq_len=8)
    assert data == [{"input_ids": [1, 2, 3, 4, 5], "completion_start": 3, "advantage": 1.0, "group": "a"}]
    assert counts == {"attempts_used": 1, "turns": 1, "skipped_too_long": 0}
    assert grpo.sequences(tmp_path, seq_len=4)[1]["skipped_too_long"] == 1


def test_rejection_sampling_weights_each_graded_pass_by_one(tmp_path: Path) -> None:
    # The Phase 1 control keeps GRPO's data and drops its relative and negative signal.
    turn = {"kind": "model_response", "payload": {"prompt_token_ids": [1, 2, 3], "choices": [{"token_ids": [4, 5]}]}}
    rows = []
    for name, passed, advantage in (("a-0", True, 0.0), ("a-1", True, 0.0), ("b-0", True, 1.2), ("b-1", False, -1.2)):
        (tmp_path / f"ex_{name}.jsonl").write_text(json.dumps(turn) + "\n")
        rows.append({"example_id": name[0], "journal": f"ex_{name}.jsonl", "advantage": advantage, "passed": passed})
    (tmp_path / "rollouts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [d["advantage"] for d in grpo.sequences(tmp_path, seq_len=8, weighting="grpo")[0]] == [1.2, -1.2]
    assert [d["advantage"] for d in grpo.sequences(tmp_path, seq_len=8, weighting="rft")[0]] == [1.0, 1.0, 1.0]


class ScriptedPolicy:
    """Alternates, per attempt, a tool call then an answer with an immediate unsupported answer."""

    first_turns = itertools.count()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> "ScriptedPolicy":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def chat_completion(self, _model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        assert kwargs["extra"] == {"return_token_ids": True} and kwargs["temperature"] == 0.8
        if messages[-1]["role"] == "user" and next(self.first_turns) % 2 == 0:
            arguments = {"network_id": "ieee39", "operation": "none", "max_iter": 50}
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{id(messages)}", "type": "function", "function": {"name": "run_study_trial",
                                                                         "arguments": json.dumps(arguments)}}]}
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": "Min bus voltage = 0.9820 pu.", "tool_calls": None}, "stop"
        return {"choices": [{"message": message, "finish_reason": finish, "token_ids": [7, 8]}],
                "prompt_token_ids": [1, 2, 3], "usage": {"completion_tokens": 2}, "elapsed_seconds": 0.01}


def test_a_rollout_runs_the_real_harness_grades_and_rewards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wattson.inference import tool_probe

    monkeypatch.setattr(tool_probe, "InferenceClient", ScriptedPolicy)
    task = {"example_id": "ex_report", "family": "t", "subfamily": "pf-report", "topology": "ieee39", "seed": 0,
            "task": {"prompt": "Run an AC power flow on case 'ieee39' and report the result.",
                     "inputs": {"case": "ieee39"}},
            "expected": {"answer": ""}, "lifecycle": {"split": "train", "status": "verified"}}
    suite = tmp_path / "pool"
    suite.mkdir()
    (suite / "examples.jsonl").write_text(json.dumps(task) + "\n")
    import hashlib

    (suite / "manifest.json").write_text(json.dumps({"n_tasks": 1, "examples_sha256": hashlib.sha256(
        (suite / "examples.jsonl").read_bytes()).hexdigest()}))
    args = argparse.Namespace(base_url="http://127.0.0.1:1", model="m", tasks=suite / "examples.jsonl",
                              out=tmp_path / "it0", iteration=0, tasks_per_iteration=1, k=2, temperature=0.8, seed=1)
    rollouts = asyncio.run(grpo.rollout(args))
    rewards = sorted(r["reward"] for r in rollouts)
    # Both answer "0.9820 pu" (ieee39's real minimum): without a tool call it is fabricated (-1); after one
    # it is traced but incomplete, so it fails the report check (-0.02 x 1 call).
    assert rewards == [-1.0, pytest.approx(-0.02)] and sum(r["advantage"] for r in rollouts) == pytest.approx(0.0)
    data, _ = grpo.sequences(args.out, seq_len=64)
    assert data and all(s["input_ids"][:3] == [1, 2, 3] for s in data)


def test_a_positive_advantage_raises_the_completion_s_probability() -> None:
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    torch.manual_seed(0)
    model = transformers.GPT2LMHeadModel(transformers.GPT2Config(n_layer=1, n_head=2, n_embd=16, vocab_size=32))
    ids = torch.tensor([[1, 2, 3, 4, 5]])

    def loss(advantage: float):
        return grpo.policy_loss(model(input_ids=ids).logits[:, 2:], ids[:, 3:], advantage)

    def logp() -> float:
        with torch.no_grad():
            return -float(loss(1.0))

    before = logp()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.5)
    loss(1.0).backward()
    optimizer.step()
    assert logp() > before
    for _ in range(2):
        optimizer.zero_grad()
        loss(-1.0).backward()
        optimizer.step()
    assert logp() < before, "a negative advantage lowers it"
