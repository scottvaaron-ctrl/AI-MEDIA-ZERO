# Spec: Kokoro voice engine and -14 LUFS loudness (plan part 2, piece C)

_Owner-approved design, 2026-10-08/09. Part 2 of `docs/PLAN_GUIDELINE.md`, piece C of A-D (A script checks and B
picture check: merged). Research: `docs/VIDEO_QUALITY_RESEARCH.md` sections "Voice" and "Sound"._

## 1. Why

The owner called the narration robotic. Kokoro-82M (Apache-2.0, CPU, about real time on this machine) sounds far
more natural than Piper. The owner listened to six Kokoro voices against the current Piper voice
(`data/tests/2026-10-08-voice-samples/`) and chose **Bella (`af_bella`)** "for now" without a deep listen. Separately,
nothing levels loudness today; YouTube plays back at about -14 LUFS and only turns loud audio down.

Success: new videos are narrated by Bella; every final video measures -14 LUFS (±1 LU) with true peak ≤ -1 dBTP.

## 2. Owner decisions (2026-10-08)

1. Voices are cleared by ear. Cleared Kokoro voices: `af_bella` only. Others can be cleared later.
2. Bella becomes the channel's voice now (owner-set), not one option among many.
3. Approach: one TTS service that routes by voice name; loudness by two-pass `loudnorm` in the final mix.

## 3. Kokoro engine

- Package `kokoro-onnx` (installs on Python 3.14; pulls espeak-ng via `espeakng-loader`, GPL, used locally as
  Piper already does — no effect on the audio's licence). Optional extra `voice = ["kokoro-onnx>=0.4"]`; `all`
  includes it. Lazy import.
- Model files from the kokoro-onnx release `model-files-v1.0`: `kokoro-v1.0.int8.onnx` (92 MB) and `voices-v1.0.bin`
  (28 MB), in `KOKORO_MODEL_DIR` (default `<data_dir>/models/kokoro`, shared with instances like the SigLIP dir),
  auto-download flag `KOKORO_AUTO_DOWNLOAD` (default true), `.part`-then-rename downloads, fetched by
  `aimz doctor --fix`. Tests never download or load the model (conftest guard, as for SigLIP).
- `src/aimz/providers/tts/kokoro.py`, `KokoroTTSProvider(TTSProvider)`:
  - Voice names are namespaced `kokoro:<id>`. `COMMERCIAL_SAFE_KOKORO_VOICES = {"kokoro:af_bella": "<licence note>"}`;
    an uncleared id raises `ProviderUnavailable` (same rule as Piper).
  - Same text path as Piper: `clean_for_tts`, `split_sentences`, `apply_pronunciations` (config/pronunciations.yaml),
    per-chunk synthesis, `sentence_pause_s` silence after each chunk, `result.chunks` timings, `duration_s` including
    pauses. Output 16-bit mono WAV at Kokoro's 24 kHz (the WAV helpers read the rate from the file).
  - Speed: `speed = 1 / length_scale` (clamped to Kokoro's 0.5-2.0). Language: `en-us` for `a*` voices, `en-gb` for
    `b*`.
  - `health()`: package, model files, cleared voice present in the voices file.
- `src/aimz/providers/tts/router.py`, `VoiceRouterTTS(TTSProvider)`: holds Piper and Kokoro; `synthesize` sends
  `options.voice` starting `kokoro:` to Kokoro, anything else to Piper. `voice`/`length_scale`/`sentence_pause_s`
  attributes come from Piper (defaults unchanged). Health is ok when Piper is ok; Kokoro problems are reported but do
  not make the service unhealthy. The registry builds the router when `TTS_PROVIDER` is `piper` (default) and keeps
  `silent` for tests.
- **Fallback:** if Kokoro fails on any beat (missing package/model, inference error), the router raises
  `VoiceUnavailable`. The producer catches it, re-runs narration for the **whole video** with the fallback voice
  `en_US-kristin-medium` (one video never mixes voices), records `voice_fallback` in the video's settings, logs one
  warning per run, and sets state `voice_unavailable`, which `attention()` lists ("Kokoro voice unavailable:
  <reason>", fix from health). The state is cleared when Kokoro next synthesises successfully.

## 4. Voice catalogue and the channel voice

- The `voice` setting's choices become Piper's cleared voices plus `kokoro:af_bella` (catalogue built from both
  cleared lists; `tests/test_dials.py` updated accordingly). `speech_length_scale` description no longer says
  "Piper's".
- Owner-set channel voice: new CLI `python -m aimz voice set <voice>` (cleared voices only) writes the identity's
  `voice` and an `owner_voice: true` flag with a history entry. While `owner_voice` is set, `identity.propose` keeps the
  owner's voice (an AI proposal that changes it is recorded as refused with "the owner set the voice"). `aimz voice
  show` prints it. Run once after merge: `aimz voice set kokoro:af_bella`.
- A guideline lock of `voice` still outranks the identity (unchanged priority).

## 5. Loudness

- In `FFmpegRenderer` final stage (`_audio_mix` and the concat call): the audio always goes through a filter chain
  (also when there is no music or sfx). Two passes:
  1. Measure: run the final audio graph with `loudnorm=I=-14:TP=-1:LRA=11:print_format=json` to a null output and
     parse `input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`.
  2. Apply: the same graph ending in `loudnorm=I=-14:TP=-1:LRA=11:measured_I=..:measured_TP=..:measured_LRA=..:
     measured_thresh=..:offset=..:linear=true`, then `-ar 48000`; AAC 128k unchanged.
- If pass 1 fails or its JSON can't be parsed, fall back to single-pass `loudnorm=I=-14:TP=-1:LRA=11` and log it.
- After the render, measure the output (`ebur128` or a `loudnorm` print pass) and record `loudness_lufs` and
  `true_peak_dbtp` in the video's render notes; outside -14 ±1 LU or peak above -1 dBTP is a logged warning, not a
  failure.
- `music_volume_db` description: the -16 dB narrator reference stays (relative mix unchanged; final level is set by
  loudnorm).

## 6. Tests

- Kokoro provider with a fake `Kokoro` object: WAV format (16-bit mono, 24 kHz), chunk timings, pauses, speed mapping,
  language by prefix, pronunciations applied, uncleared voice refused.
- Router: `kokoro:` to Kokoro, others to Piper; Kokoro failure → `VoiceUnavailable`; producer re-runs the video with
  the fallback voice, records `voice_fallback`, sets/clears `voice_unavailable`, attention item.
- Catalogue: choices = Piper cleared + `kokoro:af_bella`.
- `aimz voice set`: refuses uncleared voices; sets identity voice + `owner_voice`; AI proposal cannot change it.
- Download: model files via `httpx.MockTransport`; partial download not "present"; tests never download (guard).
- Loudness (ffmpeg required, else skip): render a two-scene video from a quiet tone and from a loud tone, with and
  without music → measured -14 ±1 LUFS, true peak ≤ -1 dBTP; pass-1 failure → single-pass fallback.

## 7. Out of scope

Clearing more Kokoro voices (owner listens later); Chatterbox/other engines; changing the music mix; the channel's
`card` template in its identity (the part 3 guideline locks the layout).
