# Getting started (beginner guide, Windows)

You will type commands into **PowerShell**. To open it: press the Windows key, type
`powershell`, press Enter. Copy each command block, paste it into the window, press Enter, and
wait for it to finish before moving on. Lines starting with `#` are comments; you can skip them.

If you are on the laptop where this project was built, tools from Part 1 are already installed.
Jump to **Part 2**.

---

## Part 1 — Install the free tools (one time, ~15 minutes)

### 1.1 Python, FFmpeg, Ollama

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
winget install --id Ollama.Ollama -e
```

If `winget` says it is not recognized, install "App Installer" from the Microsoft Store and try
again. Answer `Y` if asked to accept terms.

**Close PowerShell and open a new one** so it can find the new programs.

### 1.2 Download the AI model (about 5 GB, one time)

```powershell
ollama pull qwen3:8b
```

If your computer has no dedicated graphics card or less than 16 GB of RAM, use the smaller model
instead: `ollama pull qwen3:4b` (you will later put `qwen3:4b` in the `.env` file).

Check that Ollama is running:

```powershell
ollama list
```

You should see `qwen3:8b` in the list. If you get "could not connect", run `ollama serve` in a
second PowerShell window and leave it open.

---

## Part 2 — Set up the project (one time, ~5 minutes)

### 2.1 Go to the project folder

```powershell
cd $HOME\Documents\Agentic_Youtube
```

(On a new computer, first get the code there: `git clone <your-repo-url> $HOME\Documents\Agentic_Youtube`.)

### 2.2 Create the Python environment and install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[all]"
```

If the second line fails with "running scripts is disabled", run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

You will know the environment is active when the prompt starts with `(.venv)`.
**Every time you open a new PowerShell window, run `.\.venv\Scripts\Activate.ps1` first.**

### 2.3 Initialize

```powershell
aimz init
aimz doctor --fix
```

`init` creates your settings file (`.env`), folders, and the database.
`doctor --fix` checks everything and downloads the free voice (63 MB). Every line should say
`OK`. If one says `!!`, it also prints the fix; run the fix and rerun `aimz doctor`.

---

## Part 3 — Run it (every time)

### 3.1 One cycle

```powershell
aimz run
```

This takes 3–6 minutes on a laptop with a graphics card. It reads the news feeds, invents video
ideas, writes and fact-checks a script, records narration, builds the video, and updates its own
strategy. It prints a summary at the end. Do not worry if `approved_scripts` is `0` on some
cycles; the critic rejects weak drafts on purpose.

### 3.2 See what it made

```powershell
aimz dashboard
```

Open your web browser at **http://127.0.0.1:8420**. Click **Videos** to watch the result, **Scripts**
to read the fact-check, **Strategy** to see what the AI learned. Press `Ctrl+C` in PowerShell to
stop the dashboard.

Files are also on disk:

- `data\videos\<id>\video.mp4` – the video
- `data\packages\tiktok\<name>\` – everything to post on TikTok (video, caption, notes)
- `data\packages\youtube\<name>\` – everything to post on YouTube

### 3.3 Post a video (you do this, the AI cannot)

1. Open `data\packages\tiktok\<name>\posting_notes.md` and follow it: upload `video.mp4` in the
   TikTok app, paste `caption.txt`, turn on the **AI-generated content** label, pick the privacy
   level yourself, post.
2. For YouTube, upload `video.mp4` in YouTube Studio, paste the description from
   `metadata.json`, tick **Altered or synthetic content**, and (optionally) upload `captions.srt`.
3. Tell the system you posted it. In the dashboard, open **Publishing** and click **Mark posted**,
   pasting the video's URL.

### 3.4 Feed it results (this is what makes it learn)

A day or two after posting, open the dashboard **Metrics** page, pick the video, and type in the
numbers from the TikTok / YouTube Studio analytics screens: views, average percentage viewed,
shares, new followers. Click **Record**. The next `aimz run` uses those numbers.

---

## Part 4 — Let it run on its own

Make it run twice a day automatically:

1. Create a file `C:\Users\<you>\run-cycle.ps1` containing:
   ```powershell
   Set-Location $HOME\Documents\Agentic_Youtube
   .\.venv\Scripts\aimz.exe run
   ```
2. Press Windows key, type `Task Scheduler`, open it → **Create Basic Task** → name it
   `AI Media Zero` → Daily → pick a time → **Start a program** →
   Program: `powershell.exe`, Arguments: `-File "C:\Users\<you>\run-cycle.ps1"` → Finish.
3. Right-click the task → Properties → Triggers → add a second daily time if you want two runs.

---

## Part 5 — Emergency stop and useful commands

| Want to… | Command |
|---|---|
| Stop it from publishing anything | `aimz kill` |
| Allow it again | `aimz resume` |
| See what is going on | `aimz status` |
| Check money (should always be $0.00) | `aimz budget` |
| Read the AI's current strategy | `aimz strategy show` |
| Get a weekly report | `aimz strategy review weekly` |
| Check installed tools again | `aimz doctor` |

The system cannot spend money: the budget is `$0.00` in `.env` and every paid provider is refused
before it does anything. It also cannot post publicly on its own; you post, or you explicitly turn
on YouTube uploads (see `docs/OWNER_CONTROLS.md`).

---

## If something goes wrong

- `aimz is not recognized` → run `.\.venv\Scripts\Activate.ps1` first.
- `Ollama not reachable` → open a second PowerShell window and run `ollama serve`.
- The cycle finished but no video → look at **Scripts** in the dashboard; the critic's reasons are
  listed. Two or three cycles usually produce one approved video with `qwen3:8b`.
- Anything else → `docs/TROUBLESHOOTING.md`, or paste the error into ChatGPT/Claude (that is what
  your subscriptions are for; the running system never uses them).
