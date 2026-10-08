"""SigLIP 2 relevance scorer: how well does a picture match a piece of text? Cost: $0 (local ONNX, CPU).

Model: onnx-community/siglip2-base-patch16-224-ONNX (Apache-2.0), quantised. ``onnxruntime`` and
``tokenizers`` are optional (extra ``vision``) and imported lazily, so this module imports without them
and tests inject fake sessions/tokenizers.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

from aimz.providers.base import HealthStatus

log = logging.getLogger("aimz.relevance")

HF_REPO = "onnx-community/siglip2-base-patch16-224-ONNX"
MODEL_FILES: tuple[str, ...] = ("onnx/model_quantized.onnx", "tokenizer.json", "preprocessor_config.json")

IMAGE_SIZE = 224
TEXT_LENGTH = 64
PAD_ID = 0


def preprocess_image(img: Image.Image) -> np.ndarray:
    """RGB, resize to 224x224 bilinear, rescale 1/255, normalise mean 0.5 / std 0.5, NCHW float32."""
    rgb = img.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    return np.ascontiguousarray(arr.transpose(2, 0, 1), dtype=np.float32)


def encode_text(tokenizer: Any, text: str) -> np.ndarray:
    """Token ids padded/truncated to 64 (SigLIP is trained with max-length padding). int64, shape (64,)."""
    # SigLIP 2 was trained on lowercased text
    ids = list(tokenizer.encode(text.lower()).ids)[:TEXT_LENGTH]
    ids += [PAD_ID] * (TEXT_LENGTH - len(ids))
    return np.asarray(ids, dtype=np.int64)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x) if x > -700 else 0.0  # overflow-safe for large negative logits
    return e / (1.0 + e)


class RelevanceScorer:
    def __init__(self, model_dir: Path, session: Any | None = None, tokenizer: Any | None = None) -> None:
        self.model_dir = Path(model_dir)
        self._session = session
        self._tokenizer = tokenizer

    def _get_session(self) -> Any:
        if self._session is None:
            import onnxruntime  # lazy: optional extra "vision"

            self._session = onnxruntime.InferenceSession(
                str(self.model_dir / "onnx" / "model_quantized.onnx"),
                providers=["CPUExecutionProvider"],
            )
        return self._session

    def _get_tokenizer(self) -> Any:
        if self._tokenizer is None:
            from tokenizers import Tokenizer  # lazy: optional extra "vision"

            tok = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
            tok.enable_padding(length=TEXT_LENGTH, pad_id=PAD_ID, pad_token="<pad>")
            tok.enable_truncation(max_length=TEXT_LENGTH)
            self._tokenizer = tok
        return self._tokenizer

    def score_many(self, images: list[Image.Image], text: str) -> list[float]:
        """Sigmoid of the image-text logit for one text against each image, each in 0-1."""
        if not images:
            return []
        pixel_values = np.stack([preprocess_image(im) for im in images]).astype(np.float32)
        input_ids = encode_text(self._get_tokenizer(), text)[None, :]
        outputs = self._get_session().run(
            ["logits_per_image"], {"input_ids": input_ids, "pixel_values": pixel_values}
        )
        logits = np.asarray(outputs[0], dtype=np.float64)  # (n_images, n_texts)
        return [_sigmoid(float(v)) for v in logits[:, 0]]

    def score(self, image: Image.Image, text: str) -> float:
        return self.score_many([image], text)[0]


# ---- model files, health, shared scorer ------------------------------------------------------------------


def model_present(model_dir: Path) -> bool:
    """True only when every model file exists and is non-empty (a leftover ``.part`` never counts)."""
    d = Path(model_dir)
    return all((d / n).is_file() and (d / n).stat().st_size > 0 for n in MODEL_FILES)


def download_model(model_dir: Path, http: httpx.Client | None = None) -> None:
    """Fetch the model files from Hugging Face. Each goes to ``<name>.part`` first, then is renamed."""
    d = Path(model_dir)
    owns = http is None
    headers = {"User-Agent": "AIMediaZero/0.1"}
    client = http or httpx.Client(follow_redirects=True, timeout=300, headers=headers)
    try:
        for name in MODEL_FILES:
            dest = d / name
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            log.info("downloading SigLIP model file %s", name)
            try:
                with client.stream("GET", f"https://huggingface.co/{HF_REPO}/resolve/main/{name}") as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 16):
                            fh.write(chunk)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(dest)
    finally:
        if owns:
            client.close()


def packages_importable() -> bool:
    """True when the optional ``vision`` extra (onnxruntime, tokenizers) imports."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception:  # ImportError or DLL load failure
        return False
    return True


def scorer_health(model_dir: Path) -> HealthStatus:
    if not packages_importable():
        return HealthStatus(False, "onnxruntime / tokenizers not importable", "pip install -e .[vision]")
    if not model_present(model_dir):
        return HealthStatus(
            False, f"SigLIP model files missing in {model_dir}", "python -m aimz doctor --fix"
        )
    return HealthStatus(True, f"SigLIP 2 model ready ({model_dir})")


_SCORERS: dict[str, RelevanceScorer] = {}


def get_scorer(svc: Any) -> RelevanceScorer | None:
    """The process-wide healthy scorer, auto-downloading the model if allowed; None if unavailable."""
    env = svc.env
    model_dir = Path(env.siglip_model_dir)
    key = str(model_dir)
    if key in _SCORERS:
        return _SCORERS[key]
    if not packages_importable():
        return None
    if not model_present(model_dir):
        if not env.siglip_auto_download:
            return None
        try:
            download_model(model_dir)
        except Exception as exc:
            log.warning("SigLIP model download failed: %s", exc)
            return None
        if not model_present(model_dir):
            return None
    scorer = RelevanceScorer(model_dir)
    _SCORERS[key] = scorer
    return scorer
