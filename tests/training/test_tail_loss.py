"""The tail-only loss equals the default loss on a small random model (run where torch and transformers exist)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from wattson.training.agent_sft import IGNORE_LABEL, tail_loss  # noqa: E402


def tiny_model() -> "transformers.LlamaForCausalLM":
    torch.manual_seed(0)
    config = transformers.LlamaConfig(vocab_size=64, hidden_size=16, intermediate_size=32, num_hidden_layers=2,
                                      num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=64)
    return transformers.LlamaForCausalLM(config).eval()


def batch(prompt: int, target: int) -> dict:
    ids = torch.randint(1, 64, (1, prompt + target))
    labels = torch.cat([torch.full((1, prompt), IGNORE_LABEL), ids[:, prompt:]], dim=1)
    return {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": labels}


@pytest.mark.parametrize("prompt,target", [(5, 3), (20, 9), (1, 30)])
def test_tail_loss_equals_the_default_loss(prompt: int, target: int) -> None:
    model = tiny_model()
    inputs = batch(prompt, target)
    default = model(**{k: v.clone() for k, v in inputs.items()}).loss
    tail = tail_loss(model, {k: v.clone() for k, v in inputs.items()})
    assert torch.allclose(default, tail, atol=1e-5), (default.item(), tail.item())


def test_it_divides_by_num_items_in_batch_when_the_trainer_passes_it() -> None:
    model = tiny_model()
    inputs = batch(6, 4)
    mean = tail_loss(model, {k: v.clone() for k, v in inputs.items()})
    summed = tail_loss(model, {k: v.clone() for k, v in inputs.items()}, num_items_in_batch=8)
    assert torch.allclose(summed, mean * 4 / 8, atol=1e-6), "sum over the 4 supervised tokens divided by 8"
