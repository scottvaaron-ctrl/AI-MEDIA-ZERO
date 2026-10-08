"""Calibrate the picture-matching check: build labelled pairs, then fit the text signal and threshold.

    python scripts/calibrate_relevance.py build [--n 50] [--out data/calibration/relevance] [--db PATH]
    python scripts/calibrate_relevance.py fit   [--dir data/calibration/relevance]

``build`` reads the database READ-ONLY, picks ~n distinct pictures (by sha256) spread across videos, and writes
``pairs.csv`` (label column empty) plus ``contact-sheet.png`` to view while labelling (``match`` / ``mismatch``).
``fit`` scores every labelled pair with the local SigLIP model for three signals (the beat narration, its
visual_query, and the max of the two), picks the signal and threshold with the best balanced accuracy
(ties: higher threshold), and writes ``result.json``. Spec sections 5-6.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
import textwrap
from pathlib import Path
from typing import Any

FIELDS = ["pair_id", "video_id", "narration", "visual_query", "image_path", "label"]
SIGNALS = ("narration", "visual_query", "max")
MIN_MISMATCHES = 10
DEFAULT_DIR = Path("data/calibration/relevance")


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


def _query_for(narration: str, beats: list[dict[str, Any]]) -> str:
    """The visual_query of the beat this scene's narration came from ("" if no beat matches)."""
    n = _norm(narration)
    if not n:
        return ""
    for b in beats:
        bn = _norm(str(b.get("narration", "")))
        if bn and (n == bn or n in bn or bn in n):
            return str(b.get("visual_query") or "")
    return ""


def build_pairs(db_path: Path, n: int) -> list[dict[str, Any]]:
    """Up to n pairs (scene narration + its picture), distinct by asset sha256, round-robin across videos."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT s.video_id, s.idx, s.narration, a.id, a.sha256, COALESCE(a.file_path, s.image_path), sc.beats_json "
            "FROM scenes s JOIN assets a ON a.id = s.asset_id "
            "JOIN videos v ON v.id = s.video_id LEFT JOIN scripts sc ON sc.id = v.script_id "
            "WHERE COALESCE(a.file_path, s.image_path) IS NOT NULL "
            "ORDER BY s.video_id, s.idx"
        ).fetchall()
    finally:
        con.close()

    per_video: dict[str, list[tuple[Any, ...]]] = {}
    for r in rows:
        per_video.setdefault(r[0], []).append(r)

    beats_cache: dict[str, list[dict[str, Any]]] = {}
    for vid, vrows in per_video.items():
        try:
            parsed = json.loads(vrows[0][6]) if vrows[0][6] else []
        except (TypeError, ValueError):
            parsed = []
        beats_cache[vid] = [b for b in parsed if isinstance(b, dict)] if isinstance(parsed, list) else []

    pairs: list[dict[str, Any]] = []
    seen: set[str] = set()
    queues = {vid: list(vrows) for vid, vrows in per_video.items()}
    while queues and len(pairs) < n:
        for vid in list(queues):
            if len(pairs) >= n:
                break
            q = queues[vid]
            while q:
                _, idx, narration, asset_id, sha, path, _ = q.pop(0)
                key = sha or f"asset:{asset_id}"
                if key in seen or not path or not Path(path).is_file():
                    continue
                seen.add(key)
                pairs.append(
                    {
                        "pair_id": f"p{len(pairs) + 1:03d}",
                        "video_id": vid,
                        "narration": narration or "",
                        "visual_query": _query_for(narration or "", beats_cache[vid]),
                        "image_path": str(path),
                        "label": "",
                    }
                )
                break
            if not q:
                del queues[vid]
    return pairs


def add_mismatches(pairs: list[dict[str, Any]], k: int, seed: int = 7) -> list[dict[str, Any]]:
    """k deliberately wrong pairs: one video's narration with a picture from a different video."""
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    if k <= 0 or len({p["video_id"] for p in pairs}) < 2:
        return out
    used: set[tuple[int, int]] = set()
    attempts = 0
    while len(out) < k and attempts < k * 50:
        attempts += 1
        i = rng.randrange(len(pairs))
        j = rng.randrange(len(pairs))
        if pairs[i]["video_id"] == pairs[j]["video_id"] or (i, j) in used:
            continue
        used.add((i, j))
        out.append(
            {
                "pair_id": f"x{len(out) + 1:03d}",
                "video_id": pairs[i]["video_id"],
                "narration": pairs[i]["narration"],
                "visual_query": pairs[i]["visual_query"],
                "image_path": pairs[j]["image_path"],
                "label": "mismatch",
            }
        )
    return out


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Best signal and threshold by balanced accuracy. ``score >= threshold`` means "match"."""
    labelled = [r for r in rows if r.get("label") in ("match", "mismatch")]
    skipped = len(rows) - len(labelled)
    n_match = sum(1 for r in labelled if r["label"] == "match")
    n_mis = len(labelled) - n_match
    if n_match == 0 or n_mis == 0:
        raise ValueError(f"need both match and mismatch labels (got {n_match} match, {n_mis} mismatch)")

    def counts(sc: list[float], t: float, rows_: list[dict[str, Any]]) -> tuple[int, int, int, int]:
        tp = sum(1 for r, s in zip(rows_, sc, strict=True) if r["label"] == "match" and s >= t)
        fp = sum(1 for r, s in zip(rows_, sc, strict=True) if r["label"] == "mismatch" and s >= t)
        nm = sum(1 for r in rows_ if r["label"] == "match")
        return tp, fp, nm - tp, len(rows_) - nm - fp

    def bal(tp: int, fp: int, fn: int, tn: int) -> float | None:
        if tp + fn == 0 or tn + fp == 0:
            return None
        return (tp / (tp + fn) + tn / (tn + fp)) / 2

    best: dict[str, Any] | None = None
    for signal in SIGNALS:
        scores = [r["scores"].get(signal) for r in labelled]
        if any(s is None for s in scores):
            continue  # signal unavailable for some rows: skip it entirely
        observed = sorted({float(s) for s in scores})
        cands = []
        for i, edge in enumerate(observed):
            tp, fp, fn, tn = counts(scores, edge, labelled)
            low = observed[i - 1] if i else edge
            cands.append((bal(tp, fp, fn, tn) or 0.0, edge - low, edge, low))
        ba, width, edge, low = max(cands, key=lambda c: (c[0], c[1], c[2]))
        # a signal wins on balanced accuracy, then widest plateau; SIGNALS order breaks the rest
        if best is not None and (ba, width) <= (best["balanced_accuracy"], best["plateau_width"]):
            continue
        thr = (low + edge) / 2
        tp, fp, fn, tn = counts(scores, thr, labelled)
        real_idx = [i for i, r in enumerate(labelled) if not r.get("synthetic")]
        real_rows = [labelled[i] for i in real_idx]
        real_sc = [scores[i] for i in real_idx]
        ba_real = bal(*counts(real_sc, thr, real_rows))
        n_syn = sum(1 for r in labelled if r["label"] == "mismatch" and r.get("synthetic"))
        best = {
            "signal": signal,
            "threshold": thr,
            "plateau_low": low,
            "plateau_high": edge,
            "plateau_width": width,
            "balanced_accuracy": ba,
            "balanced_accuracy_real": ba_real,
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "n_match": n_match,
            "n_mismatch": n_mis,
            "n_mismatch_real": n_mis - n_syn,
            "n_mismatch_synthetic": n_syn,
            "skipped_unlabelled": skipped,
        }
    if best is None:
        raise ValueError("no signal could be scored on every row")
    del best["plateau_width"]
    return best


# ---- outputs ------------------------------------------------------------------------------------------


def _font(size: int) -> Any:
    from PIL import ImageFont

    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def write_contact_sheet(pairs: list[dict[str, Any]], path: Path, cols: int = 4, tile_w: int = 360) -> None:
    from PIL import Image, ImageDraw

    img_h, text_h = 240, 120
    tile_h = img_h + text_h
    rows_n = max(1, -(-len(pairs) // cols))
    sheet = Image.new("RGB", (cols * tile_w, rows_n * tile_h), "white")
    draw = ImageDraw.Draw(sheet)
    big, small = _font(26), _font(15)
    for i, p in enumerate(pairs):
        x, y = (i % cols) * tile_w, (i // cols) * tile_h
        try:
            with Image.open(p["image_path"]) as im:
                im = im.convert("RGB")
                im.thumbnail((tile_w - 8, img_h - 4))
                sheet.paste(im, (x + (tile_w - im.width) // 2, y + 2))
        except OSError:
            draw.text((x + 10, y + 100), "(image unreadable)", fill="red", font=small)
        draw.text((x + 6, y + img_h), p["pair_id"], fill="black", font=big)
        wrapped = textwrap.wrap(p["narration"][:120], width=44)[:3]
        for k, line in enumerate(wrapped):
            draw.text((x + 6, y + img_h + 34 + k * 20), line, fill="black", font=small)
        draw.rectangle([x, y, x + tile_w - 1, y + tile_h - 1], outline="gray")
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def write_pairs_csv(pairs: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: p.get(k, "") for k in FIELDS} for p in pairs)


def write_outputs(pairs: list[dict[str, Any]], out_dir: Path) -> None:
    write_pairs_csv(pairs, out_dir / "pairs.csv")
    write_contact_sheet(pairs, out_dir / "contact-sheet.png")


# ---- CLI ----------------------------------------------------------------------------------------------


def _read_pairs(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: (row.get(k) or "") for k in FIELDS} for row in csv.DictReader(fh)]


def _downscale(image: Any, width: int) -> Any:
    """Same preview the producer scores: width ``width`` px, aspect kept (never enlarged)."""
    if width <= 0 or image.width <= width:
        return image
    from PIL import Image

    return image.resize((width, max(1, round(image.height * width / image.width))), Image.Resampling.BILINEAR)


def score_rows(pairs: list[dict[str, Any]], scorer: Any, preview_width: int = 320) -> list[dict[str, Any]]:
    from PIL import Image

    rows: list[dict[str, Any]] = []
    for p in pairs:
        with Image.open(p["image_path"]) as im:
            image = _downscale(im.convert("RGB"), preview_width)
        n = scorer.score_many([image], p["narration"])[0]
        v = scorer.score_many([image], p["visual_query"])[0] if p["visual_query"] else None
        rows.append(
            {
                "label": p["label"].strip().lower(),
                "synthetic": str(p["pair_id"]).startswith("x"),
                "scores": {"narration": n, "visual_query": v, "max": max(n, v) if v is not None else None},
            }
        )
    return rows


def cmd_build(args: argparse.Namespace) -> int:
    db = Path(args.db) if args.db else None
    if db is None:
        from aimz.settings import load_env_settings

        db = load_env_settings().db_path
    out = Path(args.out)
    existing = out / "pairs.csv"
    if existing.is_file() and not args.force and any(p["label"].strip() for p in _read_pairs(existing)):
        print(f"{existing} already has labels; rerun with --force to overwrite", file=sys.stderr)
        return 1
    pairs = build_pairs(db, args.n)
    write_outputs(pairs, out)
    print(f"{len(pairs)} pairs -> {out / 'pairs.csv'} and {out / 'contact-sheet.png'}")
    print("Fill the label column with match / mismatch, then run: fit")
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    try:
        return _fit_cli(Path(args.dir))
    except (ValueError, FileNotFoundError) as exc:
        print(f"fit failed: {exc}", file=sys.stderr)
        return 1


def _fit_cli(d: Path) -> int:
    from aimz.providers.image.relevance import RelevanceScorer, model_present
    from aimz.settings import load_app_config, load_env_settings

    all_pairs = _read_pairs(d / "pairs.csv")
    pairs = [p for p in all_pairs if p["label"].strip().lower() in ("match", "mismatch")]
    skipped = len(all_pairs) - len(pairs)
    for p in pairs:
        p["label"] = p["label"].strip().lower()
    have = sum(1 for p in pairs if p["label"] == "mismatch")
    synthetic = 0
    if have < MIN_MISMATCHES:
        extra = add_mismatches([p for p in pairs if p["label"] == "match"], MIN_MISMATCHES - have)
        synthetic = len(extra)
        pairs += extra
    env = load_env_settings()
    if not model_present(env.siglip_model_dir):
        print(
            f"SigLIP model files missing in {env.siglip_model_dir}; run: python -m aimz doctor --fix",
            file=sys.stderr,
        )
        return 1
    width = int(load_app_config(env.config_dir).get("assets.relevance.preview_width", 320) or 320)
    result = fit(score_rows(pairs, RelevanceScorer(env.siglip_model_dir), width))
    result["skipped_unlabelled"] = skipped
    result["synthetic_mismatches"] = synthetic
    (d / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="select pairs, write pairs.csv and contact-sheet.png")
    b.add_argument("--n", type=int, default=50)
    b.add_argument("--out", default=str(DEFAULT_DIR))
    b.add_argument("--force", action="store_true", help="overwrite an existing labelled pairs.csv")
    b.add_argument("--db", default="", help="database to read (read-only); default: the live one")
    f = sub.add_parser("fit", help="score labelled pairs, choose signal and threshold")
    f.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args(argv)
    return cmd_build(args) if args.cmd == "build" else cmd_fit(args)


if __name__ == "__main__":
    sys.exit(main())
