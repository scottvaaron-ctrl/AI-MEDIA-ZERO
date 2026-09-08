# AI Media Zero

An autonomous, AI-operated short-form media channel (YouTube Shorts, TikTok and Bluesky) that runs
on **$0.00/month** of usage-based software and learns from its own performance data.

The experiment: *can an AI starting with $0 discover what people want to watch, build an
audience, learn from performance data, and evolve its own media strategy?*

```
research -> ideas -> editor selects -> script -> fact-check -> critic -> render -> package/publish
     ^                                                                                   |
     +---------------- metrics -> experiments -> strategy memory <-----------------------+
```

The AI controls creative operations. The owner controls assets, money, credentials, legal
authority, publishing permissions, and the kill switch.

## What runs where (all free, all local)

| Concern | Provider | Cost |
|---|---|---|
| LLM | `OllamaProvider` (any Ollama model, default `qwen3:8b`) | $0 |
| Voice | `PiperTTSProvider` (local neural TTS, MIT voices) | $0 |
| Visuals | `PillowCardProvider` + `WikimediaAssetProvider` (PD / CC0 / CC BY / CC BY-SA only) | $0 |
| Render | `FFmpegRenderer` (1080x1920 H.264, burned captions) | $0 |
| Research | `RSSResearchProvider` (Wikipedia feeds, Reddit RSS, news, archives) | $0 |
| Publish | `YouTubePublisher` (official Data API, `draft` by default), `TikTokPackagePublisher` / `TikTokDirectPostPublisher` (official Content Posting API) | $0 (quota only) |
| Analytics | `SQLiteAnalyticsProvider` (+ manual entry), `YouTubeAnalyticsProvider`, `TikTokAnalyticsProvider` (read-only) | $0 |
| Storage | SQLite with file migrations | $0 |
| Dashboard | FastAPI + Jinja2 on `127.0.0.1` | $0 |

Every provider call passes through `BudgetManager.authorize()` **before** it runs. With the
default `MONTHLY_BUDGET_USD=0.00` any provider that estimates a non-zero cost is denied at the
infrastructure level. The AI cannot change the budget: it lives in `.env`, which agents never write.

---

## Windows setup (primary platform)

Tested on Windows 11, Python 3.14, an RTX 4060 laptop GPU (any recent CPU works, just slower).

### 1. Install the free tools

Open **PowerShell** (not as admin) and run:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
winget install --id Ollama.Ollama -e
```

Close and reopen PowerShell so PATH updates. Then pull a local model (about 5 GB, one time; `qwen3:4b` at 2.5 GB also works but writes thinner scripts):

```powershell
ollama pull qwen3:8b
```

Ollama runs in the background automatically after install (tray icon). If it is not running:

```powershell
ollama serve
```

### 2. Get the code and create a virtual environment

```powershell
cd $HOME\Documents
git clone <your-fork-url> ai-media-zero
cd ai-media-zero
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[all]"
```

If PowerShell refuses to run the activation script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 3. Initialize and check dependencies

```powershell
aimz init
aimz doctor --fix
```

`init` copies `.env.example` to `.env`, creates `data/`, `secrets/`, `assets/owner/`, applies the
database migrations and seeds the cold-start strategy. `doctor --fix` verifies Python, FFmpeg,
Ollama + model, Piper, fonts, database, config, budget, kill switch and publishers, and downloads
the Piper voice (about 63 MB, free, from Hugging Face) on first run.

### 4. Run one autonomous cycle

```powershell
aimz run
```

First cycle on a laptop GPU: roughly 5–15 minutes (model calls dominate). Outputs:

- `data/videos/<video_id>/video.mp4`, `thumbnail.png`, `captions.srt`, `timeline.json`
- `data/packages/tiktok/<name>/` and `data/packages/youtube/<name>/` – owner posting packages
- `data/reports/daily_YYYY-MM-DD.md` – learning summary
- `config/strategy.md` – the AI's rewritten strategy memory

### 5. Open the dashboard

```powershell
aimz dashboard
```

Then open <http://127.0.0.1:8420>.

### 6. Post and record results

V0 never posts publicly by itself. For each package:

1. **TikTok**: upload `video.mp4` in the TikTok app, paste `caption.txt`, turn on the AI-generated
   label, select privacy yourself, post. Then `aimz publish mark-posted <publication_id> --url <url>`.
2. **YouTube**: either upload the package by hand in YouTube Studio (draft mode), or set
   `YOUTUBE_ENABLED=true`, `YOUTUBE_MODE=private`, put your OAuth client JSON at
   `secrets/client_secret.json`, run `aimz youtube auth` once, approve the video in the
   dashboard, and `aimz publish run --approved`.
3. **Bluesky**: the one platform with no gatekeeper. Create an app password in Bluesky settings,
   set `BLUESKY_ENABLED=true`, `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` in `.env`, run
   `aimz bluesky auth` once, and posting and metrics are automatic from then on. No developer
   account and no platform audit. See [docs/AUTONOMOUS_SETUP.md](docs/AUTONOMOUS_SETUP.md) Part C.
4. Enter metrics the APIs cannot provide (impressions, CTR, 3-second retention, all TikTok
   numbers) with `aimz metric add <publication_id> --views ... --avg-percent-viewed ...` or the
   dashboard Metrics page. The learning loop treats manual and API metrics identically.

### 7. Keep it running

Schedule `aimz run` with Task Scheduler (e.g. twice a day). A simple wrapper:

```powershell
# run-cycle.ps1
Set-Location $HOME\Documents\ai-media-zero
.\.venv\Scripts\aimz.exe run
```

Task Scheduler → Create Basic Task → Daily → Action: `powershell.exe -File "...\run-cycle.ps1"`.

---

## macOS / Linux

```bash
brew install ffmpeg ollama        # or apt install ffmpeg + the Ollama install script
ollama pull qwen3:8b
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
aimz init && aimz doctor --fix && aimz run
```

Set `FONT_FILE` in `.env` to a bold TTF (e.g. `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`).

---

## CLI

| Command | Purpose |
|---|---|
| `aimz init` | create `.env`, folders, database, cold-start strategy |
| `aimz doctor [--fix]` | dependency health check; `--fix` downloads the Piper voice |
| `aimz run [--stages a,b] [--limit N]` | one autonomous cycle or a subset of stages |
| `aimz research` | fetch feeds |
| `aimz ideas [--generate] [--select]` | list / generate / select ideas |
| `aimz produce [--write] [--script-id ID]` | write+QA scripts, render approved ones |
| `aimz publish run [VIDEO_ID] [--approved]` | package (and upload if enabled) |
| `aimz publish mark-posted PUB_ID --url URL` | confirm a manual post |
| `aimz publish list` | publication states |
| `aimz approve script\|video ID [--reject]` | owner approval |
| `aimz analytics [--collect]` | performance table + milestone status |
| `aimz metric add PUB_ID --views ...` | manual metrics |
| `aimz strategy show\|learn\|review [daily\|weekly\|monthly]\|edit FILE` | strategy memory |
| `aimz experiments` | experiment table |
| `aimz status` | kill switch, budget, counts, last run |
| `aimz kill [--reason ..]` / `aimz resume` | hard kill switch |
| `aimz budget` | ledger view |
| `aimz dashboard` | local owner console |
| `aimz youtube auth` / `aimz tiktok auth` | owner-only OAuth consent flows |
| `aimz bluesky auth` / `aimz bluesky limits` | sign in with a Bluesky app password; show the daily video allowance |
| `aimz schedule install\|status\|remove` | Task Scheduler jobs for hands-off operation |
| `aimz publish poll` | advance uploads still processing on the platform |

## Configuration

- `.env` – secrets, budget, provider selection, publishing mode (see `.env.example`)
- `config/config.yaml` – pipeline sizes, allocation, experiments, content families, safety
- `config/feeds.yaml` – research feeds (owner-managed)
- `config/constitution.md` – immutable owner rules, injected into every agent prompt
- `config/strategy.md` – rendered strategy memory (AI-editable within the constitution)
- `config/pronunciations.yaml` – TTS overrides

Swap the model by editing `OLLAMA_MODEL` (e.g. `llama3.1:8b`, `qwen3:8b`, `gemma3:4b`). Larger
models write better scripts; 4B-class models are the floor for structured output reliability.

## Offline / test mode

```powershell
$env:LLM_PROVIDER="fixture"; $env:TTS_PROVIDER="silent"; aimz run
pytest
```

The fixture LLM returns deterministic schema-valid answers so the whole pipeline (including
FFmpeg rendering via the bundled `imageio-ffmpeg` binary) runs in CI with no model.

## Documentation

- **[Beginner guide](docs/GETTING_STARTED.md)** (start here) · **[Fully autonomous setup](docs/AUTONOMOUS_SETUP.md)**
- **[Check-in guide](docs/CHECK_IN.md)** — what to run and what to look at when coming back to the data
- [Architecture](docs/ARCHITECTURE.md) · [Database schema](docs/DATABASE.md)
- [Platform compliance](docs/COMPLIANCE.md) · [Owner controls](docs/OWNER_CONTROLS.md)
- [What is autonomous vs human-controlled](docs/AUTONOMY.md)
- [Handoff for adding platforms](docs/HANDOFF.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Roadmap](docs/ROADMAP.md) · [Funded V1 path](docs/FUNDED_V1.md)
- [Dry-run report](docs/DRY_RUN.md) · [Example generated package](examples/huhu_beetle_package/)

## Privacy and terms

The software uses YouTube API Services. See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md).
By using it you agree to the [YouTube Terms of Service](https://www.youtube.com/t/terms); Google's data handling is described in the [Google Privacy Policy](https://policies.google.com/privacy).

## License

MIT for this repository. Piper's engine is GPL-3 (used as a separate installed package);
voices are MIT; FFmpeg builds are GPL/LGPL; Wikimedia assets carry their own licenses, recorded
per asset in the database and in each package's `attributions.txt`.
