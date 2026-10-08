# Picture-Matching Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pictures that don't match their beat are never used (text card instead), no credit/disclosure text is drawn on
screen, and `full_bleed` is the default layout.

**Architecture:** A local SigLIP 2 scorer (`providers/image/relevance.py`, ONNX) scores small previews of each search
candidate; the producer keeps the best candidate above a calibrated threshold, fetching only the winner. The gate is
a capability (`relevance_gate`) and config-gated (`assets.relevance.enabled`), falling back to today's behaviour when
unavailable. Calibration is a script plus a labelled pair set.

**Tech Stack:** Python 3.14, onnxruntime (installed), tokenizers (new, optional extra `vision`), Pillow, numpy, httpx.

**Spec:** `docs/superpowers/specs/2026-10-08-picture-relevance-design.md`

## Global Constraints

- Python via `.venv/Scripts/python.exe -m ...`; in a worktree set `PYTHONPATH=<worktree>/src` (editable install points at the live checkout).
- $0 runtime. Only new dependency: `tokenizers>=0.20` in extra `vision = ["tokenizers>=0.20", "onnxruntime>=1.17"]`; `all` includes `vision`.
- Model: HF `onnx-community/siglip2-base-patch16-224-ONNX` files `onnx/model_quantized.onnx`, `tokenizer.json`, `preprocessor_config.json` into `SIGLIP_MODEL_DIR` (default `<data_dir>/models/siglip2`), auto-download flag `SIGLIP_AUTO_DOWNLOAD` (default true).
- Preprocess: RGB, 224x224 bilinear, x/255, (x-0.5)/0.5, NCHW float32. Text padded/truncated to 64 tokens. Score = sigmoid(logits_per_image).
- Config defaults: `assets.relevance: {enabled: false, text: narration, threshold: 0.0, by_sa_penalty: 0.05, preview_width: 320}`.
- Tests never download or load the real model. No test touches the network.
- Without `assets.relevance.enabled: true` and a healthy scorer, picture choice is exactly today's (first download wins).
- ruff check, mypy src clean; no NEW ruff-format failures; full pytest green.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A preview that fails to download or is not a decodable image must count as "not passing", not crash the beat → test in Task 4.
- CMYK / palette / RGBA / grayscale previews must score (converted to RGB) → test in Task 1.
- Narration longer than 64 tokens must be truncated, not error → test in Task 1.
- The scorer raising mid-run (e.g. ONNX error) must drop to today's behaviour for the rest of the run with one warning → test in Task 4.
- A model directory with only some files (interrupted download) must report unhealthy and re-download, not load → test in Task 2.

---

### Task 1: The scorer

**Files:**
- Create: `src/aimz/providers/image/relevance.py`
- Modify: `pyproject.toml` (extra `vision`, `all`)
- Test: `tests/test_relevance.py`

**Interfaces:**
- Produces: `class RelevanceScorer` with `__init__(self, model_dir: Path, session: Any | None = None, tokenizer: Any | None = None)`, `score_many(self, images: list[PIL.Image.Image], text: str) -> list[float]`, `score(self, image, text) -> float`; module functions `preprocess_image(img: PIL.Image.Image) -> numpy.ndarray` (shape (3,224,224) float32) and `encode_text(tokenizer, text: str) -> numpy.ndarray` (shape (64,) int64). Session/tokenizer are created lazily from `model_dir` when not injected. Session input names: `input_ids`, `pixel_values`; output `logits_per_image` (shape (n_images, n_texts)).

- [ ] **Step 1: Failing tests:** `test_preprocess_shape_and_range` (a 300x200 red RGB image → shape (3,224,224), dtype float32, channel 0 values ≈ 1.0, channels 1-2 ≈ -1.0); parametrized `test_preprocess_converts_modes` over modes `L`, `P`, `RGBA`, `CMYK` → shape (3,224,224); `test_encode_text_pads_and_truncates` with a fake tokenizer (returns ids 5 per word) → length 64 for "two words" and for a 200-word text; `test_score_many_sigmoid` with a fake session returning logits `[[0.0],[2.0]]` → `[0.5, ≈0.8808]`; `test_session_receives_batch` (fake records the `pixel_values` shape (2,3,224,224) and `input_ids` shape (1,64)).
- [ ] **Step 2: Run** `pytest tests/test_relevance.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement.** Lazy imports of `onnxruntime` and `tokenizers` inside the loader; tokenizer from `tokenizer.json` via `tokenizers.Tokenizer.from_file`, enable padding/truncation to length 64 with pad id from the tokenizer's `<pad>` (id 0). Session `onnxruntime.InferenceSession(str(model_dir/'onnx'/'model_quantized.onnx'), providers=['CPUExecutionProvider'])`. Add the `vision` extra.
- [ ] **Step 4: Run** the test file → PASS; full suite, ruff, mypy.
- [ ] **Step 5: Commit** `feat: SigLIP 2 relevance scorer (ONNX)`.

### Task 2: Model files, health, capability, doctor

**Files:**
- Modify: `src/aimz/settings.py` (`siglip_model_dir`, `siglip_auto_download`, next to the Piper settings), `src/aimz/instances.py` (share `SIGLIP_MODEL_DIR` like `PIPER_VOICES_DIR`), `src/aimz/providers/image/relevance.py`, `src/aimz/experiments/settings.py` (`capabilities`), `src/aimz/doctor.py`, `.env.example`
- Test: `tests/test_relevance.py`

**Interfaces:**
- Consumes: Task 1 `RelevanceScorer`.
- Produces: `MODEL_FILES: tuple[str, ...] = ("onnx/model_quantized.onnx", "tokenizer.json", "preprocessor_config.json")`; `HF_REPO = "onnx-community/siglip2-base-patch16-224-ONNX"`; `model_present(model_dir: Path) -> bool`; `download_model(model_dir: Path, http: httpx.Client | None = None) -> None` (writes each file to `<name>.part` then renames, so a partial download is never "present"); `scorer_health(model_dir: Path) -> HealthStatus` (missing package → fix `pip install -e .[vision]`; missing model → fix `python -m aimz doctor --fix`); `get_scorer(svc) -> RelevanceScorer | None` (healthy scorer, auto-downloading if `siglip_auto_download`, else None; cached per process). `capabilities(svc)` adds `"relevance_gate"` when `model_present` and both packages import.

- [ ] **Step 1: Failing tests:** `test_model_present_requires_all_files` (only two of three files → False; all three → True; a leftover `.part` file alone → False); `test_download_model_writes_all_files` using `httpx.MockTransport` serving bytes for each URL `https://huggingface.co/{HF_REPO}/resolve/main/{name}` → files exist, no `.part` left; `test_download_interrupted_leaves_not_present` (transport raises on the second file) → `model_present` False and no complete-looking file for the failed one; `test_health_missing_model` → `ok is False` and fix mentions `doctor --fix`; `test_capability_relevance_gate` (monkeypatch `model_present` True and imports ok → `"relevance_gate" in capabilities(svc)`; False → absent).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; doctor `--fix` downloads the model when the `vision` packages import and the model is missing (follow the Piper voice block); doctor's directory list includes the model dir.
- [ ] **Step 4: Run** `pytest tests/test_relevance.py tests/test_doctor*.py -v` and the full suite → PASS.
- [ ] **Step 5: Commit** `feat: SigLIP model download, health and capability`.

### Task 3: Preview URLs and preview loading

**Files:**
- Modify: `src/aimz/domain/models.py` (`AssetCandidate.preview_url: str = ""`), `src/aimz/providers/base.py` (asset provider protocol), `src/aimz/providers/assets/wikimedia.py`, `src/aimz/providers/assets/stock.py`, `src/aimz/providers/assets/owner_library.py`
- Test: `tests/test_assets_preview.py`

**Interfaces:**
- Produces: each asset provider has `preview(self, ctx: ProviderContext, candidate: AssetCandidate, width: int = 320) -> PIL.Image.Image | None` (returns None on any download/decode failure; goes through the provider's existing authorized HTTP path). Preview URL rules: Wikimedia — in `file_url`, replace the first `/<digits>px-` segment with `/<width>px-`; if absent use `file_url`. Pexels — `src.small`, else `file_url`. Pixabay — `webformatURL`, else `previewURL`, else `file_url`. Owner library — open the local file.

- [ ] **Step 1: Failing tests:** `test_wikimedia_preview_url` (`.../thumb/a/ab/X.jpg/1280px-X.jpg` → `.../320px-X.jpg`; a non-thumb URL unchanged); `test_pexels_preview_url_small`; `test_pixabay_preview_url_webformat`; `test_preview_returns_image` (MockTransport serving a tiny PNG → PIL image); `test_preview_bad_bytes_returns_none` (serves `b"not an image"` → None); `test_preview_http_error_returns_none` (500 → None); `test_owner_library_preview_reads_file`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (set `preview_url` in each `search`).
- [ ] **Step 4: Run** the new file + existing asset tests + full suite → PASS.
- [ ] **Step 5: Commit** `feat: asset previews for relevance scoring`.

### Task 4: Choosing pictures with the gate

**Files:**
- Modify: `src/aimz/agents/producer.py` (`_find_asset` and the beat loop in `assemble`), `config/config.yaml` (`assets.relevance` block with the Global Constraints defaults and a comment), `src/aimz/cli.py` or wherever `attention(svc.db)` lives (status needs_attention item)
- Test: `tests/test_producer_relevance.py`

**Interfaces:**
- Consumes: Task 2 `get_scorer(svc)`; Task 3 `provider.preview(...)`, `AssetCandidate.preview_url`.
- Produces: `ProducerAgent._find_asset(self, ctx, query: str, dest: Path, text: str | None = None) -> AssetRecord | None` — `text` is the string to match (None = gate off for this call). Module function `rank_candidates(scored: list[tuple[AssetCandidate, float]], threshold: float, by_sa_penalty: float) -> list[AssetCandidate]` (passing only, best first; penalty subtracted when `license.upper().startswith("CC BY-SA")`). The beat loop picks `text` from `assets.relevance.text`: `narration` → `beat.narration`; `visual_query` → `beat.visual_query`; `max` → score both and use the higher per candidate (implement by passing both strings; `_find_asset` takes `text: str | tuple[str, str] | None`). State key `relevance_unavailable` (DB state) set with the reason when the gate is enabled but no scorer, cleared when it works; `attention()` lists it as "picture-matching check unavailable: <reason>" with fix `python -m aimz doctor --fix`.

- [ ] **Step 1: Failing tests** (fake scorer object with `score_many` returning preset scores per candidate title; fake provider with `search`, `preview`, `fetch` recording calls):
  - `test_rank_candidates_threshold_and_penalty`: scores A=0.30 (CC BY-SA 4.0), B=0.28 (CC BY 4.0), C=0.10, threshold 0.2, penalty 0.05 → `[B, A]`.
  - `test_best_passing_is_only_fetch`: three candidates, the 2nd best → `fetch` called once with it.
  - `test_failed_fetch_tries_next_passing`.
  - `test_unscorable_preview_does_not_pass` (preview returns None for the top candidate → it is skipped).
  - `test_no_pass_first_query_then_second_query`.
  - `test_no_pass_any_query_gives_text_card` (asset None; the scene has `asset_id` None).
  - `test_disabled_config_first_download_wins` (enabled false → `preview` never called).
  - `test_scorer_missing_falls_back_and_flags` (enabled true, `get_scorer` None → first download wins, state `relevance_unavailable` set, `attention()` includes it).
  - `test_scorer_raises_falls_back_for_rest_of_run` (scorer raises on first call → that beat and later beats use first-download-wins; exactly one warning logged).
  - `test_relevance_run_note` (run note `relevance` lists per beat query, candidates, top score, chosen asset id or `"text_card"`).
  - `test_text_signal_visual_query_and_max` (config `text: visual_query` passes the visual_query string to the scorer; `max` passes both and uses the higher).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new file, existing producer/pipeline tests, full suite → PASS.
- [ ] **Step 5: Commit** `feat: picture-matching gate in the producer`.

### Task 5: No on-screen credits or disclosure; full-bleed default; compliance doc

**Files:**
- Modify: `src/aimz/providers/image/cards.py` (both draw paths), `src/aimz/providers/video/layout.py` (remove `CREDITS_Y` if unused), `src/aimz/agents/producer.py` (`default_settings()` and the `template` fallback → `"full_bleed"`), `docs/COMPLIANCE.md` (disclosure/credits rows and the "AI disclosure text is included on the first scene card" line; model source `onnx-community/siglip2-base-patch16-224-ONNX`, converted from `google/siglip2-base-patch16-224`, Apache-2.0)
- Test: `tests/test_cards.py` (or the existing card test file), `tests/test_producer_relevance.py`

- [ ] **Step 1: Failing tests:** `test_card_draws_no_attribution` and `test_layered_card_draws_no_attribution` — render the same spec with `attribution="X — Y — CC BY 4.0 — via Wikimedia Commons"` and `disclosure="AI narration"` and again with both empty → pixel-identical images; `test_default_template_full_bleed` (no guideline → chosen `template == "full_bleed"`). Update any existing test that asserted credits are drawn or `card` is the default.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; edit the compliance doc.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: credits and disclosure off screen; full-bleed default`.

### Task 6: Calibration script

**Files:**
- Create: `scripts/calibrate_relevance.py`
- Test: `tests/test_calibrate_relevance.py`

**Interfaces:**
- Consumes: Task 1 `RelevanceScorer`; Task 2 `get_scorer` / model dir.
- Produces: CLI `python scripts/calibrate_relevance.py build [--n 50] [--out data/calibration/relevance]` and `fit [--dir ...]`. Importable functions `build_pairs(db_path: Path, n: int) -> list[dict]` (keys `pair_id, video_id, narration, visual_query, image_path, label`; distinct by asset `sha256`; round-robin across videos; `visual_query` from `scripts.beats_json` by beat index, "" if not recoverable; `label` ""), `add_mismatches(pairs: list[dict], k: int, seed: int = 7) -> list[dict]` (narration of one video with a picture from another; `label = "mismatch"`, `pair_id` prefixed `x`), `fit(rows: list[dict]) -> dict` where each row has `label` and `scores: {"narration": float, "visual_query": float, "max": float}` → `{"signal", "threshold", "balanced_accuracy", "tp", "fp", "tn", "fn", "n_match", "n_mismatch"}`; threshold chosen over the observed scores maximising balanced accuracy, ties → higher threshold; a signal whose `visual_query` is empty for any row is scored on rows that have it only if all do, else skipped. `build` also writes `contact-sheet.png` (grid tiles: image + pair id + first 120 chars of narration) and `pairs.csv`; `fit` reads `pairs.csv` (labels filled), scores with the real scorer, adds mismatches if fewer than 10 `mismatch` labels (and records `"synthetic_mismatches": k`), writes `result.json` and prints it.

- [ ] **Step 1: Failing tests:** `test_build_pairs_dedupes_sha256` (tiny SQLite with the needed tables: two scenes sharing one asset → one pair); `test_add_mismatches_crosses_videos`; `test_fit_picks_signal_and_threshold` (synthetic rows where `narration` separates perfectly at 0.2 and `visual_query` does not → `signal == "narration"`, `threshold == 0.2`, `balanced_accuracy == 1.0`); `test_fit_tie_prefers_higher_threshold`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite.
- [ ] **Step 5: Commit** `feat: relevance calibration script`.

### Task 7 (controller, not a subagent): Calibrate and switch on

Run in the worktree against a COPY of the live DB (`data/aimz.sqlite3` copied to the scratchpad); never write the live DB.
- [ ] Install the extra (`pip install tokenizers>=0.20`) and download the model with `python -m aimz doctor --fix` (or `download_model`).
- [ ] `build --n 50`; view each image and label `match` / `mismatch` in `pairs.csv`; send the owner the contact sheet with the labels.
- [ ] `fit`; copy `signal`/`threshold` into `config/config.yaml` `assets.relevance` and set `enabled: true`; add a "Calibration (2026-10-08)" subsection to `docs/VIDEO_QUALITY_RESEARCH.md` with the counts; commit `config: calibrated picture-matching check on`.
- [ ] Smoke test: one fixture-LLM dry-run cycle with the real scorer (`LLM_PROVIDER=fixture`, isolated data dir) produces a video, and the run note shows `relevance` entries.

### Task 8: Docs

- [ ] `docs/PLAN_GUIDELINE.md`: piece B done (status table, section 6, session log); `docs/OWNER_TODO.md` B-checks: verify the next live video's run note shows `relevance` entries and no on-screen credits. Commit `docs: part 2 piece B done`.
- [ ] After merge (controller, main checkout): install the `vision` extra into `.venv` and run `python -m aimz doctor --fix` so the scheduler has the model; `docs/CEO_BRIEF.md` status line.
