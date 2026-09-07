"""Tolerant JSON extraction for model output (handles fences, <think> blocks, trailing prose)."""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    if text is None:
        raise ValueError("empty model output")
    cleaned = _THINK_RE.sub("", text).strip()
    # 1. direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 2. fenced block
    m = _FENCE_RE.search(cleaned)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 3. first balanced object/array
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        candidate2 = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
                        return json.loads(candidate2)
    raise ValueError("no JSON object found in model output")
