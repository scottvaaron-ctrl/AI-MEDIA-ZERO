"""SigLIP 2 relevance scorer: preprocessing, text encoding, scoring (fakes only, no model)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from aimz.providers.image.relevance import RelevanceScorer, encode_text, preprocess_image


class FakeTokenizer:
    """Minimal protocol: encode(text) -> object with .ids (5 per word, no padding)."""

    def encode(self, text: str) -> Any:
        return SimpleNamespace(ids=[5] * len(text.split()))


class FakeSession:
    def __init__(self, logits: list[list[float]]) -> None:
        self.logits = logits
        self.calls: list[dict[str, np.ndarray]] = []

    def run(self, output_names: Any, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append(inputs)
        return [np.array(self.logits, dtype=np.float32)]


def test_preprocess_shape_and_range() -> None:
    arr = preprocess_image(Image.new("RGB", (300, 200), (255, 0, 0)))
    assert arr.shape == (3, 224, 224)
    assert arr.dtype == np.float32
    assert np.allclose(arr[0], 1.0, atol=1e-3)
    assert np.allclose(arr[1:], -1.0, atol=1e-3)


@pytest.mark.parametrize("mode", ["L", "P", "RGBA", "CMYK"])
def test_preprocess_converts_modes(mode: str) -> None:
    arr = preprocess_image(Image.new(mode, (50, 80)))
    assert arr.shape == (3, 224, 224)


def test_encode_text_pads_and_truncates() -> None:
    short = encode_text(FakeTokenizer(), "two words")
    long = encode_text(FakeTokenizer(), " ".join(["w"] * 200))
    for out in (short, long):
        assert out.shape == (64,)
        assert out.dtype == np.int64
    assert list(short[:2]) == [5, 5]
    assert not short[2:].any()


def test_score_many_sigmoid() -> None:
    scorer = RelevanceScorer(Path("unused"), session=FakeSession([[0.0], [2.0]]), tokenizer=FakeTokenizer())
    imgs = [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))]
    out = scorer.score_many(imgs, "a cat")
    assert out == pytest.approx([0.5, 0.8808], abs=1e-4)


def test_score_single() -> None:
    scorer = RelevanceScorer(Path("unused"), session=FakeSession([[0.0]]), tokenizer=FakeTokenizer())
    assert scorer.score(Image.new("RGB", (10, 10)), "a cat") == pytest.approx(0.5)


def test_session_receives_batch() -> None:
    session = FakeSession([[0.0], [2.0]])
    scorer = RelevanceScorer(Path("unused"), session=session, tokenizer=FakeTokenizer())
    scorer.score_many([Image.new("RGB", (10, 10))] * 2, "a cat")
    inputs = session.calls[0]
    assert inputs["pixel_values"].shape == (2, 3, 224, 224)
    assert inputs["input_ids"].shape == (1, 64)
    assert inputs["input_ids"].dtype == np.int64


def test_score_many_empty() -> None:
    scorer = RelevanceScorer(Path("unused"), session=FakeSession([]), tokenizer=FakeTokenizer())
    assert scorer.score_many([], "x") == []
