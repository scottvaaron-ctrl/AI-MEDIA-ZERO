from __future__ import annotations

import pytest

from aimz.providers.llm.json_repair import extract_json


def test_plain_json() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_think_block_and_fence() -> None:
    text = '<think>reasoning...</think>\nSure!\n```json\n{"a": [1, 2]}\n```\nDone.'
    assert extract_json(text) == {"a": [1, 2]}


def test_embedded_object_with_trailing_comma() -> None:
    assert extract_json('Result: {"a": 1, "b": {"c": "x}"},}') == {"a": 1, "b": {"c": "x}"}}


def test_no_json_raises() -> None:
    with pytest.raises(ValueError):
        extract_json("nothing here")
