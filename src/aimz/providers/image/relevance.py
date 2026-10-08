"""SigLIP 2 relevance scorer: how well does a picture match a piece of text? Cost: $0 (local ONNX, CPU).

Model: onnx-community/siglip2-base-patch16-224-ONNX (Apache-2.0), quantised. ``onnxruntime`` and
``tokenizers`` are optional (extra ``vision``) and imported lazily, so this module imports without them
and tests inject fake sessions/tokenizers.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

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
    ids = list(tokenizer.encode(text).ids)[:TEXT_LENGTH]
    ids += [PAD_ID] * (TEXT_LENGTH - len(ids))
    return np.asarray(ids, dtype=np.int64)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


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
