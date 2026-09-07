# Troubleshooting

Run `aimz doctor` first; it names the missing piece and the fix. Logs: `data/logs/aimz.jsonl`
(structured) and the `errors` table (`/runs` in the dashboard).

## Install

| Symptom | Cause / fix |
|---|---|
| `aimz` is not recognized | Activate the venv: `.\.venv\Scripts\Activate.ps1`, or call `.\.venv\Scripts\aimz.exe` |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `pip install` fails building a wheel | Use Python 3.12–3.14 64-bit; all dependencies ship wheels for those |
| `ffmpeg not found` after winget install | Restart the terminal (PATH refresh). `pip install imageio-ffmpeg` also bundles a working binary; `doctor` finds it automatically |
| `Ollama not reachable` | Start it: `ollama serve` (or launch the Ollama app). Check `http://127.0.0.1:11434/api/tags` in a browser |
| `Model qwen3:4b not found` | `ollama pull qwen3:4b` (or set `OLLAMA_MODEL` to a model you have) |
| Piper voice missing | `aimz doctor --fix` downloads it; or `python -m piper.download_voices en_US-lessac-medium --download-dir data\voices` |
| `piper-tts not importable` | `pip install piper-tts`; on Python 3.14 make sure `onnxruntime` installed as a wheel (it does for 3.14 x64) |

## Running

| Symptom | Cause / fix |
|---|---|
| Cycle "succeeds" but no ideas | Feeds returned nothing new (all duplicates) or Ollama returned invalid JSON three times. Check `errors` and `agent_runs`; try a larger model |
| `LLM output failed validation` | Small models sometimes miss required fields. Ollama structured outputs are used, but if it persists set `OLLAMA_MODEL=qwen3:8b` or `llama3.1:8b` |
| Scripts always `rejected` | The Critic threshold (`pipeline.qa_pass_threshold`, default 70) may be high for a 4B model; lower to 60 while cold-starting, or check `qa_json` for a recurring required revision |
| `needs_owner_review` scripts pile up | Elevated-review keywords matched (real people, medical, legal, politics…). Approve or reject them on `/scripts/<id>` |
| Video status `failed` with ffmpeg error | Open `data/videos/<id>/work/` – the scene PNG/WAV pairs are there. Common cause: a corrupt Wikimedia download (already guarded) or a font issue. Set `FONT_FILE` to a valid TTF |
| Captions not burned | The bundled ffmpeg must have libass (both winget Gyan builds and imageio-ffmpeg do). `doctor` prints `libass ok` |
| Very slow cycle | First model call loads the model (~10 s on GPU). CPU-only inference for a 4B model is 5–20x slower; reduce `pipeline.ideas_per_cycle` and `selections_per_cycle`, or set `OLLAMA_NUM_CTX=4096` |
| `KillSwitchEngaged` in logs | Intended: `aimz resume` when ready |
| `BudgetDenied` in logs | Intended: a provider estimated a non-zero cost against a $0 budget. Nothing was spent |
| Wikimedia returns no images | Queries are too specific; the producer falls back to generated cards automatically. Network blocks also degrade gracefully |
| YouTube upload `403 / quotaExceeded` | Default is 100 uploads/day; more requires an audit. Wait or reduce cadence |
| YouTube upload `forbidden` / video private | Unverified API projects are locked to private until audited (see COMPLIANCE.md) |
| Dashboard shows stale `.env` values | Restart `aimz dashboard` after editing `.env` (settings are read at startup) |

## Resetting

- Fresh database, keep config: delete `data\aimz.sqlite3*` and run `aimz init`.
- Full reset: delete `data\` (videos, packages, voice, logs) and `.env`, then `aimz init` and
  `aimz doctor --fix`.

## Getting help from the paid assistants you already have

Paste the relevant `data/logs/aimz.jsonl` lines and the `errors` row into ChatGPT/Claude to debug.
That is the intended use of those subscriptions: interactive development help, never runtime.
