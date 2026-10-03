"""Render one short sample per production setting at each of its bounds, and check each automatically.

    .venv\\Scripts\\python.exe scripts\\render_matrix.py [--only caption_color] [--out DIR]

Every sample goes through ``ProducerAgent.assemble`` and the real renderer and TTS, with one setting changed
from the defaults. Numbers are rendered at their min and max, choices at every value. Voices that are not
downloaded yet are skipped (listed as such) so the matrix never downloads on its own. Uses a scratch
database, so the live one is untouched. Exit code 1 if any sample fails its check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

BEATS = [
    {
        "narration": "In 1892 an astronomer found a fifth moon of Jupiter. It had hidden in plain sight for centuries.",
        "caption": "A moon in plain sight",
        "visual_type": "text_card",
        "visual_query": "Jupiter moon",
    },
    {
        "narration": "It circles the planet in half a day, closer than any moon known at the time.",
        "caption": "Half a day per orbit",
        "visual_type": "stat_card",
        "visual_query": "12 hours",
    },
]


def sample_values(var: str, definition: Any, present_voices: set[str]) -> list[tuple[Any, str | None]]:
    """(value, reason it is skipped or None) for every bound of a setting."""
    if definition.value_type == "number":
        return [(definition.bounds["min"], None), (definition.bounds["max"], None)]
    out: list[tuple[Any, str | None]] = []
    for choice in definition.bounds["choices"]:
        skip = "voice not downloaded" if var == "voice" and choice not in present_voices else None
        out.append((choice, skip))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="one setting name")
    ap.add_argument("--out", default="", help="output folder (default: a temp folder)")
    args = ap.parse_args()
    out_root = Path(args.out or tempfile.mkdtemp(prefix="aimz_matrix_"))
    out_root.mkdir(parents=True, exist_ok=True)
    os.environ["AIMZ_DB_PATH"] = str(out_root / "matrix.sqlite3")

    from aimz.agents.producer import ProducerAgent, audio_bed, caption_style
    from aimz.domain.models import Beat, Timeline
    from aimz.experiments.settings import SettingsEngine, capabilities
    from aimz.providers.registry import build_services
    from aimz.providers.video.render_check import check_render

    svc = build_services(quiet_logs=True)
    producer = ProducerAgent(svc, None)
    present = set(getattr(svc.tts, "present_voices", lambda: [])())
    beats = [Beat.model_validate(b) for b in BEATS]
    ffmpeg = getattr(svc.renderer, "ffmpeg", None)
    if not ffmpeg:
        print("ffmpeg not found")
        return 1
    results: list[dict[str, Any]] = []
    catalog = SettingsEngine(svc.db, caps=capabilities(svc)).catalog  # only dials this machine can use
    for var, definition in catalog.items():
        if definition.kind != "production" or (args.only and var != args.only):
            continue
        for value, skip in sample_values(var, definition, present):
            name = f"{var}={value}".replace("/", "_")
            if skip:
                results.append({"sample": name, "ok": None, "problems": [skip]})
                print(f"SKIP {name}: {skip}")
                continue
            chosen = {**producer.default_settings(), var: definition.check(value)[0]}
            out_dir = out_root / name
            try:
                ctx = svc.ctx()
                scenes, _, _, total = producer.assemble(
                    ctx, beats, chosen, out_dir, name, "Render matrix", "AI narration", search_assets=False
                )
                tl = Timeline(
                    video_id=name,
                    title="Render matrix",
                    scenes=scenes,
                    total_duration_s=round(total, 3),
                    caption_style=caption_style(chosen),
                    **audio_bed(chosen, scenes),
                )
                res = svc.renderer.render(ctx, tl, out_dir)
                check = check_render(
                    ffmpeg, Path(res.video_path), Path(res.captions_path), res.duration_s, total
                )
                results.append(
                    {
                        "sample": name,
                        "ok": check.ok,
                        "problems": check.problems,
                        "duration_s": res.duration_s,
                        "timeline_s": total,
                    }
                )
                print(
                    f"{'OK  ' if check.ok else 'FAIL'} {name}: {res.duration_s:.2f}s (timeline {total:.2f}s) {'; '.join(check.problems)}"
                )
            except Exception as exc:  # a crash is a failed sample, not the end of the matrix
                results.append(
                    {"sample": name, "ok": False, "problems": [f"{type(exc).__name__}: {exc}"[:300]]}
                )
                print(f"FAIL {name}: {type(exc).__name__}: {exc}"[:300])
    svc.close()
    (out_root / "matrix.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    failed = [r for r in results if r["ok"] is False]
    skipped = [r for r in results if r["ok"] is None]
    print(
        f"\n{len(results) - len(failed) - len(skipped)} ok, {len(failed)} failed, {len(skipped)} skipped; samples in {out_root}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
