"""Caption fonts the AI may choose: only free-licensed files shipped in ``assets/fonts``.

``assets/fonts/licenses.json`` lists each font: ``{"family": ..., "file": ..., "license": ..., "source": ...}``.
``family`` must be the font's internal family name (libass matches captions to fonts by it). A listed
font whose file is missing is ignored, so the dial only ever offers fonts that can render.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FONTS_DIR = Path(__file__).resolve().parents[4] / "assets" / "fonts"
FREE_LICENSES = {"OFL-1.1", "Apache-2.0", "UFL-1.0", "CC0-1.0"}


def shipped_fonts(fonts_dir: Path = FONTS_DIR) -> dict[str, dict[str, Any]]:
    manifest = fonts_dir / "licenses.json"
    if not manifest.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in json.loads(manifest.read_text(encoding="utf-8")):
        path = fonts_dir / str(entry.get("file", ""))
        if entry.get("license") in FREE_LICENSES and path.is_file():
            out[str(entry["family"])] = {**entry, "path": path}
    return out
