"""Each assistant turn must be supervised once, on exactly the tokens after the served prompt."""

from __future__ import annotations

from typing import Any

import pytest

from wattson.training.agent_sft import encode, schedule, supervised_segments, turn_segments

MESSAGES: list[dict[str, Any]] = [
    {"role": "system", "content": "S"}, {"role": "user", "content": "U"},
    {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "t", "arguments": {}}}]},
    {"role": "tool", "content": "R"}, {"role": "assistant", "content": "A"},
]


def body(message: dict[str, Any]) -> str:
    """The visible part of an assistant turn in both fake templates."""
    return message["content"] or "<call>"


def qwen_like(messages: list[dict[str, Any]], generation: bool) -> str:
    """History and generation prompt agree: '<think>\\n' then '\\n</think>' + body."""
    text = "".join(f"<a><think>\n\n</think>{body(m)}|" if m["role"] == "assistant" else f"[{m['role']}]{m['content']}|"
                   for m in messages)
    return text + ("<a><think>\n" if generation else "")


def granite_like(messages: list[dict[str, Any]], generation: bool) -> str:
    """Generation prompt ends '<think>\\n'; a rendered turn starts '<think></think>'."""
    text = "".join(f"<a><think></think>{body(m)}|" if m["role"] == "assistant" else f"[{m['role']}]{m['content']}|"
                   for m in messages)
    return text + ("<a><think>\n" if generation else "")


def test_prefix_consistent_template_targets_continue_the_served_prompt() -> None:
    """Qwen-like: each target starts right after the served '<think>\\n'."""
    segments = turn_segments(MESSAGES, qwen_like)
    assert [target for _, target in segments] == ["\n</think><call>|", "\n</think>A|"]
    assert all(prompt.endswith("<a><think>\n") for prompt, _ in segments)


def test_think_marker_mismatch_targets_start_at_the_closing_marker() -> None:
    """Granite-like: the target starts '</think>' after the served '<think>\\n'."""
    segments = turn_segments(MESSAGES, granite_like)
    assert [target for _, target in segments] == ["</think><call>|", "</think>A|"]
    assert all(prompt.endswith("<a><think>\n") for prompt, _ in segments)


def test_a_template_that_changes_more_than_whitespace_is_refused() -> None:
    """A generation prompt the rendered turn does not continue cannot be trained exactly."""
    def mismatched(messages: list[dict[str, Any]], generation: bool) -> str:
        return granite_like(messages, False) + ("<a><reasoning>\n" if generation else "")

    with pytest.raises(ValueError, match="beyond whitespace"):
        turn_segments(MESSAGES, mismatched)


def test_prompt_and_target_are_tokenised_separately() -> None:
    """A tokenizer that merges '\\n\\n' must not swallow the prompt's final token (the r6 defect)."""
    class MergingTokenizer:
        def __call__(self, text: str, add_special_tokens: bool = False) -> dict:
            return {"input_ids": [ord(c) for c in text.replace("\n\n", "\x00")]}

    prompt, target = "<think>\n", "\n</think>"
    features = encode(prompt, target, MergingTokenizer(), 100)
    assert features["input_ids"] == [ord(c) for c in prompt + target]
    assert features["labels"] == [-100] * len(prompt) + [ord(c) for c in target]


def test_schedule_gives_30_steps_for_one_epoch() -> None:
    """r6 numbers: 238 conversations as 1,457 turn sequences, 8 conversations a step."""
    assert schedule(238, 1457, 8, 1) == (49, 30)


def test_an_injected_mistake_is_context_never_a_target() -> None:
    """Recovery variants train the correction, not the mistake that precedes it."""
    # Stored trajectories carry tool-call arguments as JSON strings.
    stored = [{**m, "tool_calls": [{"function": {"name": "t", "arguments": "{}"}}]} if m.get("tool_calls") else m
              for m in MESSAGES]
    plain = {"fault": None, "messages": stored}
    faulty = {"fault": "none_string", "messages": stored}
    assert len(supervised_segments(plain, qwen_like)) == 2
    [(prompt, target)] = supervised_segments(faulty, qwen_like)
    assert target == "\n</think>A|" and "<call>" in prompt


def test_earlier_reasoning_is_not_in_the_served_prompt_but_the_target_keeps_its_own() -> None:
    """The harness drops each turn's reasoning when it re-sends history, so training prompts must too (backlog 0041)."""
    def render(messages: list[dict], generation: bool) -> str:
        out = ""
        for message in messages:
            out += f"<{message['role']}>"
            if message.get("reasoning_content"):
                out += f"<think>{message['reasoning_content']}</think>"
            out += str(message.get("content") or "") + f"</{message['role']}>"
        return out + ("<assistant><think>" if generation else "")

    conversation = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "call", "reasoning_content": "first thought"},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "answer", "reasoning_content": "second thought"},
    ]
    (first_prompt, first_target), (second_prompt, second_target) = turn_segments(conversation, render)
    assert "first thought" not in second_prompt, "the served history has no earlier reasoning"
    assert "second thought" in second_target and "first thought" in first_target
