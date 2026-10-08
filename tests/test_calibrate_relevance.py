"""Calibration script: pair building, synthetic mismatches, signal/threshold fit (no model, no network)."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image
from scripts import calibrate_relevance as cal


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE scripts (id TEXT PRIMARY KEY, beats_json TEXT);
        CREATE TABLE videos (id TEXT PRIMARY KEY, script_id TEXT);
        CREATE TABLE assets (id TEXT PRIMARY KEY, file_path TEXT, sha256 TEXT);
        CREATE TABLE scenes (id TEXT PRIMARY KEY, video_id TEXT, idx INTEGER, narration TEXT,
                             asset_id TEXT, image_path TEXT);
        """
    )
    beats = [
        {"narration": "Moon one.", "visual_query": "jupiter moon"},
        {"narration": "Moon two.", "visual_query": "io"},
    ]
    con.execute("INSERT INTO scripts VALUES ('s1', ?)", (json.dumps(beats),))
    con.execute("INSERT INTO videos VALUES ('v1', 's1')")
    for name in ("a", "b"):
        Image.new("RGB", (40, 30), "red").save(tmp_path / f"{name}.png")
    con.execute("INSERT INTO assets VALUES ('a1', ?, 'sha-A')", (str(tmp_path / "a.png"),))
    con.execute("INSERT INTO assets VALUES ('a2', ?, 'sha-A')", (str(tmp_path / "b.png"),))  # same picture
    con.execute("INSERT INTO assets VALUES ('a3', ?, 'sha-B')", (str(tmp_path / "gone.png"),))  # missing file
    con.execute("INSERT INTO scenes VALUES ('c1','v1',0,'Moon one.','a1',NULL)")
    con.execute("INSERT INTO scenes VALUES ('c2','v1',1,'Moon two.','a2',NULL)")
    con.execute("INSERT INTO scenes VALUES ('c3','v1',2,'Other.','a3',NULL)")
    con.commit()
    con.close()
    return db


def test_build_pairs_dedupes_sha256(tmp_path: Path) -> None:
    pairs = cal.build_pairs(_make_db(tmp_path), 50)
    assert len(pairs) == 1  # c2 shares c1's picture; c3's file is gone
    p = pairs[0]
    assert p["video_id"] == "v1" and p["narration"] == "Moon one."
    assert p["visual_query"] == "jupiter moon" and p["label"] == ""
    assert set(p) == {"pair_id", "video_id", "narration", "visual_query", "image_path", "label"}


def test_build_pairs_round_robin_and_unmatched_query(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    con = sqlite3.connect(db)
    Image.new("RGB", (8, 8), "blue").save(tmp_path / "c.png")
    con.execute("INSERT INTO assets VALUES ('a4', ?, 'sha-C')", (str(tmp_path / "c.png"),))
    con.execute("INSERT INTO videos VALUES ('v2', 's1')")
    con.execute("INSERT INTO scenes VALUES ('c4','v2',0,'not in script','a4',NULL)")
    con.commit()
    con.close()
    pairs = cal.build_pairs(db, 2)
    assert [p["video_id"] for p in pairs] == ["v1", "v2"]
    assert pairs[1]["visual_query"] == ""


def test_add_mismatches_crosses_videos() -> None:
    pairs = [
        {
            "pair_id": "p1",
            "video_id": "v1",
            "narration": "n1",
            "visual_query": "q1",
            "image_path": "i1",
            "label": "match",
        },
        {
            "pair_id": "p2",
            "video_id": "v2",
            "narration": "n2",
            "visual_query": "q2",
            "image_path": "i2",
            "label": "match",
        },
        {
            "pair_id": "p3",
            "video_id": "v3",
            "narration": "n3",
            "visual_query": "q3",
            "image_path": "i3",
            "label": "match",
        },
    ]
    out = cal.add_mismatches(pairs, 3)
    assert len(out) == 3
    by_id = {p["pair_id"]: p for p in pairs}
    for m in out:
        assert m["label"] == "mismatch" and m["pair_id"].startswith("x")
        src_narr = next(p for p in pairs if p["narration"] == m["narration"])
        pic_owner = next(p for p in pairs if p["image_path"] == m["image_path"])
        assert src_narr["video_id"] != pic_owner["video_id"]
    assert len(by_id) == 3


def test_add_mismatches_single_video_gives_none() -> None:
    pairs = [
        {
            "pair_id": "p1",
            "video_id": "v1",
            "narration": "n",
            "visual_query": "",
            "image_path": "i",
            "label": "match",
        }
    ]
    assert cal.add_mismatches(pairs, 5) == []


def _rows(
    narr_match: list[float], narr_mis: list[float], vq_match: list[float], vq_mis: list[float]
) -> list[dict[str, Any]]:
    rows = []
    for lab, ns, vs in (("match", narr_match, vq_match), ("mismatch", narr_mis, vq_mis)):
        for n, v in zip(ns, vs, strict=True):
            rows.append({"label": lab, "scores": {"narration": n, "visual_query": v, "max": max(n, v)}})
    return rows


def test_fit_picks_signal_and_threshold() -> None:
    rows = _rows([0.3, 0.5, 0.9], [0.05, 0.1, 0.2], [0.1, 0.9, 0.05], [0.5, 0.05, 0.9])
    res = cal.fit(rows)
    assert res["signal"] == "narration"
    assert res["threshold"] == 0.3
    assert res["balanced_accuracy"] == 1.0
    assert (res["tp"], res["fn"], res["tn"], res["fp"]) == (3, 0, 3, 0)
    assert res["n_match"] == 3 and res["n_mismatch"] == 3


def test_fit_threshold_is_observed_score_separating_at_point_two() -> None:
    # matches >= 0.2, mismatches < 0.2: threshold on an observed score means 0.2 (lowest match)
    rows = _rows([0.2, 0.6], [0.1, 0.15], [0.5, 0.1], [0.4, 0.3])
    res = cal.fit(rows)
    assert res["signal"] == "narration" and res["threshold"] == 0.2 and res["balanced_accuracy"] == 1.0


def test_fit_tie_prefers_higher_threshold() -> None:
    # every observed threshold from 0.2 to 0.5 separates perfectly; higher one wins
    rows = _rows([0.5, 0.6], [0.1, 0.2], [0.5, 0.6], [0.1, 0.2])
    res = cal.fit(rows)
    assert res["balanced_accuracy"] == 1.0 and res["threshold"] == 0.5


def test_fit_skips_signal_with_missing_visual_query() -> None:
    rows = _rows([0.9, 0.8], [0.1, 0.2], [0.0, 0.0], [0.0, 0.0])
    rows[0]["scores"]["visual_query"] = None
    rows[0]["scores"]["max"] = None
    res = cal.fit(rows)
    assert res["signal"] == "narration"


def test_fit_ignores_blank_labels() -> None:
    rows = _rows([0.9], [0.1], [0.9], [0.1]) + [
        {"label": "", "scores": {"narration": 0.5, "visual_query": 0.5, "max": 0.5}}
    ]
    res = cal.fit(rows)
    assert res["n_match"] == 1 and res["n_mismatch"] == 1 and res["skipped_unlabelled"] == 1


def test_contact_sheet_and_csv_written(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = tmp_path / "out"
    pairs = cal.build_pairs(db, 5)
    cal.write_outputs(pairs, out)
    assert (out / "contact-sheet.png").is_file()
    with open(out / "pairs.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["pair_id"] == pairs[0]["pair_id"]
