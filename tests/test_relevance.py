"""SigLIP 2 relevance scorer: preprocessing, text encoding, scoring (fakes only, no model)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
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


# ---- task 2: text lowercasing, overflow-safe sigmoid ----------------------------------------------------


def test_encode_text_lowercases() -> None:
    seen: list[str] = []

    class Recording:
        def encode(self, text: str) -> Any:
            seen.append(text)
            return SimpleNamespace(ids=[1, 2])

    encode_text(Recording(), "A Red BARN")
    assert seen == ["a red barn"]


def test_sigmoid_overflow_safe() -> None:
    from aimz.providers.image import relevance

    assert relevance._sigmoid(-1000.0) == 0.0
    assert relevance._sigmoid(1000.0) == 1.0
    assert relevance._sigmoid(0.0) == 0.5


# ---- task 2: model files, download, health, capability ---------------------------------------------------


def _mock_http(fail_on: str | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail_on and request.url.path.endswith(fail_on):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=b"data:" + request.url.path.encode())

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_model_present_requires_all_files(tmp_path: Path) -> None:
    from aimz.providers.image.relevance import MODEL_FILES, model_present

    assert not model_present(tmp_path)
    for name in MODEL_FILES[:2]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    assert not model_present(tmp_path)
    last = tmp_path / MODEL_FILES[2]
    last.parent.mkdir(parents=True, exist_ok=True)
    last.write_bytes(b"x")
    assert model_present(tmp_path)


def test_model_present_ignores_part_file(tmp_path: Path) -> None:
    from aimz.providers.image.relevance import MODEL_FILES, model_present

    for name in MODEL_FILES[:2]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    part = tmp_path / (MODEL_FILES[2] + ".part")
    part.write_bytes(b"x")
    assert not model_present(tmp_path)


def test_download_model_writes_all_files(tmp_path: Path) -> None:
    from aimz.providers.image.relevance import HF_REPO, MODEL_FILES, download_model, model_present

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"abc")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    download_model(tmp_path, http=client)
    assert model_present(tmp_path)
    assert sorted(requested) == sorted(
        f"https://huggingface.co/{HF_REPO}/resolve/main/{n}" for n in MODEL_FILES
    )
    assert not list(tmp_path.rglob("*.part"))


def test_download_interrupted_leaves_not_present(tmp_path: Path) -> None:
    from aimz.providers.image.relevance import MODEL_FILES, download_model, model_present

    with pytest.raises(httpx.HTTPError):
        download_model(tmp_path, http=_mock_http(fail_on=MODEL_FILES[1]))
    assert not model_present(tmp_path)
    assert not (tmp_path / MODEL_FILES[1]).exists()
    assert not list(tmp_path.rglob("*.part"))


def test_health_missing_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    h = relevance.scorer_health(tmp_path)
    assert h.ok is False
    assert "doctor --fix" in h.fix


def test_health_missing_model_with_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    h = relevance.scorer_health(tmp_path)
    assert h.ok is False
    assert "doctor --fix" in h.fix


def test_health_missing_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "packages_importable", lambda: False)
    h = relevance.scorer_health(tmp_path)
    assert h.ok is False
    assert ".[vision]" in h.fix


def test_health_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    monkeypatch.setattr(relevance, "model_present", lambda d: True)
    assert relevance.scorer_health(tmp_path).ok is True


def test_get_scorer_returns_none_when_unhealthy_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "_SCORERS", {})
    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    called: list[Path] = []
    monkeypatch.setattr(relevance, "download_model", lambda d, http=None: called.append(d))
    svc = SimpleNamespace(env=SimpleNamespace(siglip_model_dir=tmp_path, siglip_auto_download=False))
    assert relevance.get_scorer(svc) is None
    assert called == []


def test_get_scorer_downloads_and_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "_SCORERS", {})
    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    present = {"v": False}
    monkeypatch.setattr(relevance, "model_present", lambda d: present["v"])
    calls: list[Path] = []

    def fake_download(d: Path, http: Any = None) -> None:
        calls.append(d)
        present["v"] = True

    monkeypatch.setattr(relevance, "download_model", fake_download)
    svc = SimpleNamespace(env=SimpleNamespace(siglip_model_dir=tmp_path, siglip_auto_download=True))
    s1 = relevance.get_scorer(svc)
    s2 = relevance.get_scorer(svc)
    assert isinstance(s1, RelevanceScorer)
    assert s1 is s2
    assert len(calls) == 1


def test_get_scorer_download_failure_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "_SCORERS", {})
    monkeypatch.setattr(relevance, "_FAILED", set())
    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    attempts: list[Path] = []

    def boom(d: Path, http: Any = None) -> None:
        attempts.append(d)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(relevance, "download_model", boom)
    svc = SimpleNamespace(env=SimpleNamespace(siglip_model_dir=tmp_path, siglip_auto_download=True))
    assert relevance.get_scorer(svc) is None
    assert relevance.get_scorer(svc) is None
    assert len(attempts) == 1


@pytest.mark.parametrize("present", [True, False])
def test_capability_relevance_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    from aimz.experiments import settings as exp_settings
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "model_present", lambda d: present)
    monkeypatch.setattr(relevance, "packages_importable", lambda: True)
    svc = SimpleNamespace(assets=[], env=SimpleNamespace(siglip_model_dir=tmp_path))
    assert ("relevance_gate" in exp_settings.capabilities(svc)) is present


def test_capability_relevance_gate_needs_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.experiments import settings as exp_settings
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "model_present", lambda d: True)
    monkeypatch.setattr(relevance, "packages_importable", lambda: False)
    svc = SimpleNamespace(assets=[], env=SimpleNamespace(siglip_model_dir=tmp_path))
    assert "relevance_gate" not in exp_settings.capabilities(svc)


def test_get_scorer_in_test_env_never_downloads(svc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: with the check enabled and no model, the test environment must not download."""
    import aimz.providers.image.relevance as rel

    def _fail(*_a: Any, **_k: Any) -> None:
        raise AssertionError("download_model must not be called in tests")

    monkeypatch.setattr(rel, "download_model", _fail)
    monkeypatch.setattr(rel, "packages_importable", lambda: True)
    monkeypatch.setattr(rel, "_SCORERS", {})
    monkeypatch.setattr(rel, "_FAILED", set())
    assert svc.env.siglip_auto_download is False
    assert not rel.model_present(Path(svc.env.siglip_model_dir))
    assert rel.get_scorer(svc) is None
