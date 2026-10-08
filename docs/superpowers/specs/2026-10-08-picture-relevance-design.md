# Spec: picture-matching check, credits off screen, full-bleed default (plan part 2, piece B)

_Owner-approved design, 2026-10-08. Part 2 of `docs/PLAN_GUIDELINE.md`, piece B of A-D (A script checks: merged;
C sound; D stock video). Research: `docs/VIDEO_QUALITY_RESEARCH.md` sections "Visuals" and "Attribution"._

## 1. Why

Pictures are chosen with no relevance check: for each beat, the first search result that downloads wins
(`ProducerAgent._find_asset`, `src/aimz/agents/producer.py`). The 7 Oct test video showed an Earth-from-space photo
under narration about an observatory in India; recent queries ("Airport infrastructure changes") fetched loosely
related files. Every card also prints an image credit and, on the first card, an AI-disclosure line, adding text
clutter the research flags as part of the "AI slop" look.

Success: a picture that does not match its beat is not used; when no candidate matches, the beat is a text card;
no credit or disclosure text appears in the frame; full TASL credit stays in the description.

## 2. Owner decisions (2026-10-08)

1. Calibration labels: Claude labels ~50 pairs; the owner spot-checks a contact sheet and may overturn any label.
2. No candidate passes: plain text card (no picture) for that beat.
3. AI disclosure: description and platform labels only, not on screen. The constitution requires disclosure "where
   the platform supports it, and in the description regardless": YouTube sets `containsSyntheticMedia`, TikTok
   packages recommend `is_aigc`, Bluesky post text already carries the disclosure line (`build_post_text`).
4. Runtime: ONNX (`onnxruntime`, already installed via piper-tts) + `tokenizers`, SigLIP 2 base patch16-224 8-bit.

## 3. Relevance scorer

### 3.1 Model

- Source: Hugging Face `onnx-community/siglip2-base-patch16-224-ONNX`, a conversion of `google/siglip2-base-patch16-224`
  (Apache-2.0). Files: `onnx/model_quantized.onnx` (378 MB), `tokenizer.json`, `preprocessor_config.json`.
- Location: `data/models/siglip2/` (setting `SIGLIP_MODEL_DIR`, default `<data_dir>/models/siglip2`; instances share
  the main one, like `PIPER_VOICES_DIR`). Downloaded on first use when `SIGLIP_AUTO_DOWNLOAD` (default true), and
  by `aimz doctor --fix`.
- New optional dependency extra `vision = ["tokenizers>=0.20", "onnxruntime>=1.17"]` in `pyproject.toml`; `all`
  includes it. Imports are lazy.

### 3.2 Module `src/aimz/providers/image/relevance.py`

- `RelevanceScorer.score(image: PIL.Image.Image | Path, text: str) -> float` returns `sigmoid(logits_per_image)`, 0-1.
  `score_many(images, text) -> list[float]` batches one text against several images.
- Image preprocessing per `preprocessor_config.json`: RGB, resize to 224x224 bilinear, rescale 1/255, normalise mean
  0.5 / std 0.5, NCHW float32. Text: the model's tokenizer, padded/truncated to 64 tokens (SigLIP is trained with
  max-length padding).
- `health() -> HealthStatus` (ok / missing package / missing model, with the fix command).
- A capability string `relevance_gate` is added in `experiments/settings.capabilities` when the scorer is healthy.
- Tests never load the real model: the scorer takes an injectable session/tokenizer; a fake returns fixed logits.

## 4. Choosing a picture

In `ProducerAgent` (replacing the first-download-wins loop when the gate is available):

1. For each of the beat's queries (`asset_queries`, unchanged, up to 4), search as today (up to
   `assets.wikimedia.max_results`, default 6 candidates).
2. Download each candidate's **preview** (about 320 px), score it, discard the preview. `AssetCandidate` gains
   `preview_url: str = ""`. Wikimedia: the thumb URL with the width segment replaced (`/1280px-` -> `/320px-`), else
   `file_url`. Pexels: `src.small`. Pixabay: `previewURL` / `webformatURL`. Owner library: the local file.
3. Candidates scoring at or above `assets.relevance.threshold` pass. Rank by score, minus
   `assets.relevance.by_sa_penalty` (default 0.05) for licences starting with "CC BY-SA". `fetch()` the top one
   (only the winner becomes an `assets` row). If its fetch fails, try the next passing candidate.
4. No passing candidate: next query. No query passes: `asset = None`, so the beat renders as a text card (the
   existing no-image path). This applies to the owner library too.
5. Recorded per beat on the run (`run.note("relevance", ...)`): query, candidate count, top score, chosen asset or
   `text_card`. Counts are summarised in the producer's run note.
6. Gate unavailable (package/model missing, or the scorer raises): today's behaviour (first download wins), one
   warning per run, and `aimz status` `needs_attention` lists "picture-matching check unavailable" with the fix.

`quote_card` beats are unchanged (no picture). Clip search (`_find_clip`) is out of scope (piece D).

## 5. What the picture is matched against: decided by calibration

Two candidate texts: the beat's `narration` and its `visual_query`. Calibration (section 6) scores every labelled
pair with each, and with `max(narration, visual_query)`, and picks the signal with the best balanced accuracy at
its best threshold. The plan's calibration task records the winning signal in `assets.relevance.text`
(`narration` | `visual_query` | `max`) and the threshold in `assets.relevance.threshold`; the producer reads both.
Until calibration has run, `assets.relevance.enabled` stays `false`.

## 6. Calibration

- `scripts/calibrate_relevance.py build`: selects ~50 distinct pairs (dedupe by asset `sha256`) from
  `scenes JOIN assets`, spread across videos, writing `data/calibration/relevance/pairs.csv`
  (`pair_id, video_id, narration, visual_query, image_path, label`) and a contact sheet
  `contact-sheet.png` (image + narration + pair id per tile). `visual_query` comes from `scripts.beats_json` by
  beat index; empty if not recoverable.
- Claude fills `label` (`match` / `mismatch`) by viewing each image; the owner skims the sheet and may overturn.
- `scripts/calibrate_relevance.py fit`: scores each pair with each signal, chooses signal and threshold maximising
  balanced accuracy (ties: higher threshold), prints the confusion counts, and writes them to
  `data/calibration/relevance/result.json`. A human copies the values into `config/config.yaml` and records them in
  `docs/VIDEO_QUALITY_RESEARCH.md` (new "Calibration" subsection).
- If there are fewer than 10 mismatches among the labels, add pairs (deliberately mismatched: narration of one video
  with a picture from another) until there are, and say so in the recorded result.

## 7. On-screen text and defaults

- `src/aimz/providers/image/cards.py`: remove drawing of `attribution` and `disclosure` in both the plain card and
  the layered path. The spec keys stay (harmless). `layout.CREDITS_Y` is removed if nothing else reads it.
- `Timeline.attributions` and the description's "Image credits:" list are unchanged; every asset used still has its
  TASL line there (already true).
- `docs/COMPLIANCE.md`: the disclosure is in the description and platform labels; credits are in the description;
  no on-screen credits (CC 4.0 "reasonable manner"; BY-SA 3.0 "at least as prominent" satisfied because nothing else
  is credited on screen). Record the model source and licence.
- `ProducerAgent.default_settings()` and the `template` fallback: `full_bleed` instead of `card`. Only matters when
  no guideline locks `template`.

## 8. Config

```yaml
assets:
  relevance:
    enabled: false        # true after calibration
    text: narration       # narration | visual_query | max, set by calibration
    threshold: 0.0        # set by calibration
    by_sa_penalty: 0.05
    preview_width: 320
```

## 9. Tests

- Scorer: preprocessing shape/normalisation; sigmoid of fake logits; tokenizer padded to 64; health states.
- Choosing: best passing candidate wins; BY-SA penalty flips a near-tie; only the winner is fetched; a failed fetch
  falls to the next passing candidate; no pass on query 1 then pass on query 2; no pass at all gives a text card;
  gate unavailable gives first-download-wins plus the warning; disabled config gives today's behaviour.
- Preview URL derivation for Wikimedia, Pexels, Pixabay, owner library.
- Cards: no attribution or disclosure pixels drawn (render a card with long attribution text and compare against
  the same card without it).
- Default template is `full_bleed` without a guideline.
- Calibration script: pair building dedupes by sha256; `fit` picks the expected signal/threshold on a small synthetic
  table.

## 10. Out of scope

Clips and stock-video providers (piece D); parallax; metadata checks for dates and named people (CLIP-family weakness
noted in research; revisit if calibration shows it); changing how search queries are built.
