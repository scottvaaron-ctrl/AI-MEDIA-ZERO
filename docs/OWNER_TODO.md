# Owner to-do: everything left to make the plan 100% finished

_Written 2026-09-30 at the end of the session that built stages 0-6 of `docs/PLAN_FULL_CONTROL.md`._
_All code is written and tested (252 tests). What remains needs you (A), then an agent to check it (B)._
_Tick items off here; the next agent reads this file._

Commands run from `C:\Users\scott\Documents\Agentic_Youtube` in PowerShell. `py` below means
`.\.venv\Scripts\python.exe` (never the `aimz.exe` shim: Smart App Control blocks it).

---

## A. Your steps, in order

### A0. Answer Google's YouTube API compliance review: **SENT 2 Oct**
Reply sent in the Gmail thread on 2 Oct (channel link, step-by-step upload process, end result).
**Now wait for Google's answer.** The reply promised: no new channel is connected until the review is
complete, and Google gets each new channel's link before any upload to it. If Google asks for a screen
recording, see the optional step below.

_Original task, kept for reference:_
Gmail thread "YouTube API Services: Thank you for your submission" (from youtube-disputes@google.com).
They want (1) a detailed script **or** screencast of the whole upload process and the end result, and
(2) the links of every channel the API client uploads to.
- Owner decision 1 Oct: **contact YouTube first; no new channels until then.** The reply lists Backhouse
  Explainers and says up to three more of your channels may follow, with their links sent before any upload.
- [ ] **Before sending:** `py -m aimz instance consent main` (confirms Backhouse Explainers; the reply says
      consent is recorded per channel this way, and main's consent so far was only the `.env` switch).
- [ ] A Gmail draft reply is in the thread (created 1 Oct): review it, then send.
- [ ] Reply in that thread with the written description `data/audit/upload-process-script.md` (pasted
      or attached) and the channel links. A Gmail draft can be prepared for you to review and send.
- [ ] Optional but stronger: a 2-3 minute screen recording (Win+Alt+R with Xbox Game Bar) showing
      `py -m aimz youtube auth` consent screen → a cycle uploading → the video live on the channel.
- If this lapses, Google refuses the quota increase. Uploads still work on the default quota, which
  covers 4 channels at the current posting limits.

### A1. Today or tomorrow morning (1 Oct): keep YouTube logged in and collect revenue data
- [ ] **Google Cloud Console → APIs & Services → OAuth consent screen → Publishing status: set to
      "In production".** This ends the 7-day login expiry. (A "Google hasn't verified this app" warning
      is expected for your own app; click Advanced → continue.)
- [ ] **Log in to the main channel again**, which adds the revenue permission. Tick **every** box Google
      shows, including "View monetary and non-monetary YouTube Analytics reports":
      `py -m aimz youtube auth`
- [ ] Check it is the right channel: `py -m aimz youtube whoami` → should say **Backhouse Explainers**.
- Deadline: before about **7 Oct**, when the current login expires.

### A2. Add the 3 new YouTube channels (about 5 minutes each)
> **Wait until Google completes the review** (promised in the 2 Oct reply). Then email Google the new
> channels' links before their first upload. (Owner decision 1 Oct was "until YouTube is contacted"; the reply made it stricter.) Then send
> Google the new channels' links before their first upload. The code is ready: stage C items C4 (consent
> per channel), C6 (voice) and C11 (channel identity) are in. A new instance starts with automatic uploads **off** and no voice of its own; each
> channel's AI picks a genre, voice and look different from the other channels on its first runs.

Full guide: `docs/AUTONOMOUS_SETUP.md` Part F. For each channel:
- [ ] Create the channel on YouTube (a brand channel under your Google account is fine).
- [ ] `py -m aimz instance create <short-name> --channel-name "<Channel Name>"`
      (short name: lowercase letters/digits, e.g. `space`, `money`, `history`).
- [ ] `py -m aimz --instance <short-name> youtube auth` → when Google asks, **pick the new channel**,
      tick every box.
- [ ] `py -m aimz --instance <short-name> youtube whoami` → must show the new channel's name.
- [ ] `py -m aimz instance consent <short-name>` → shows the channel and asks you to confirm automatic
      uploads to it (YouTube requires consent per channel). Until then its videos wait for your approval.
- [ ] `py -m aimz --instance <short-name> schedule install --times <times printed by instance create>`
      (two times a day, e.g. `09:15,18:15`)
- [ ] Afterwards: `py -m aimz status --all` lists all 4 channels.

Notes:
- New channels start with TikTok and Bluesky **off**. For Bluesky, create an account and add
  `BLUESKY_ENABLED=true`, `BLUESKY_HANDLE=...`, `BLUESKY_APP_PASSWORD=...` to
  `instances\<short-name>\.env`.
- All channels read the main `config\config.yaml`. Edit it once and every channel follows.
- If you add the Pexels/Pixabay keys (A4) **after** creating the channels, add the same two lines to
  each `instances\<short-name>\.env` too. Channels created after the keys are in the main `.env` copy them.

### A3. Edit the constitution (owner-only file: `config\constitution.md`)
The wording to paste is drafted in `docs/PLAN_FULL_CONTROL.md` **section 8**:
- [ ] Replace §1 Mission: add "find a niche that earns passive ad revenue" and keep "learning first,
      never flood a platform".
- [ ] Replace the media sentence in §5: allows the Pexels and Pixabay licences, other free-for-commercial-use
      licences, and original/CC0 music. **Stock photos and video stay off without this edit.**
- [ ] Add to §8 Creative autonomy: the AI controls voice, speed, captions, visuals, music, editing and
      posting times within your limits; each channel picks its own niches and stays out of another
      channel's.

### A4. Stock photos and video clips (Pexels, Pixabay): after A3
- [ ] Get a free Pexels key: https://www.pexels.com/api/ (sign in → "Your API key").
- [ ] Get a free Pixabay key: https://pixabay.com/api/docs/ (sign in; the key is shown on that page).
- [ ] Add to the main `.env` (and each instance `.env`, see A2):
      `PEXELS_API_KEY=...` and `PIXABAY_API_KEY=...`
- [ ] In `config\config.yaml` set `assets:` → `stock:` → `enabled: true`.
- [ ] Ask an agent to run check B3 before the next scheduled run uses them.

### A5. Decisions only you can make
- [x] **Narration voice licence:** done 1 Oct by your decision "no default voice". The research-only
      lessac voice can no longer be spoken; each video uses one of five cleared voices.
      Tidy-up (optional): delete the line `PIPER_VOICE=en_US-lessac-medium` from `.env` (it is ignored now).
- [x] **Privacy page published** 1 Oct (commit 8b1e650), contact `backhousegroupnj@gmail.com`.
- [ ] **What counts as success.** In `config\config.yaml` → `scoring:` → `weights:`, `watch_time` and
      `revenue` are 0 today, so the score is unchanged. Suggested options:
      - now: `watch_time: 0.15` (rewards videos watched longer);
      - once any channel is monetised: `revenue: 0.30`.
      Every experiment and niche is judged by this score, so it is your call.
- [ ] **Seven scripts waiting for sensitive-topic review** (incl. the Gol Flight 1907 crash, flagged by a since-fixed keyword bug): open the dashboard
      (`py -m aimz dashboard`, http://127.0.0.1:8420) or `py -m aimz approve script <id>`.
- [ ] **The 19 Sep Borneo video** that never reached YouTube: the recommendation is to let it go.
      To send it anyway: `py -m aimz publish retry pub_20260919T153400_2e871c36`.
- [ ] **TikTok**: film the demo video TikTok's app audit needs. Until the audit passes, posts stay private
      and give no data.
- [ ] **Always-on machine** (decision 3): scheduled runs are skipped whenever the laptop is off or asleep.
- [ ] **Record the 30 Sep decisions in the vault** (`60-Registers/Decision Log.md`):
      - plan B+D;
      - posting limits (2 per platform per run, 2.5 h apart, **2 runs a day**, changed 1 Oct), schedule
        09:00 and 18:00;
      - 4 channels in total;
      - "if it's free it's allowed" = free for commercial reuse;
      - 1 Oct: no default voice (AI picks only cleared voices); Unitree video stays up; sources required
        for every claim incl. titles; each channel distinct in genre, voice and look; long-form deferred;
      - original music allowed.

### A6. Git
- [ ] Nothing from 30 Sep is committed yet (stages 0-6, docs, fonts, music). Tell an agent "commit and push"
      when you are happy. `instances\`, `.env`, `secrets\` and `data\` are git-ignored and never
      committed.

---

## B. Checks for an agent after your steps (tell the next session to do section B)

- **B1, after A1 (re-auth):** `py -m aimz analytics --collect`, then confirm the newest YouTube metrics'
  `raw_json` has `revenue` (rows or null) or `revenue_error` (a clean "not monetized"), and that the
  other metrics still arrive. `py -m aimz youtube whoami` → Backhouse Explainers.
- **B2, after A2 (channels):**
  - `py -m aimz instance list` shows a login for all 4, and `whoami` is correct for each.
  - After each channel's first scheduled runs: its `data\aimz.sqlite3` has a `posting_runs` row with at
    most 2 per platform, and `instances\_shared\<name>.json` exists.
  - No channel's task removed another's: `py -m aimz schedule status` lists 4 tasks.
  - **Plan stage 4 is done** when two or more channels run 48 h without touching each other's data.
- **B3, after A4 (stock keys):** a live check of each provider:
  - one photo search, one video search and one download each, with the fields as parsed;
  - the Pexels video endpoint (`https://api.pexels.com/videos/search`);
  - `scripts\render_matrix.py --only clip_scenes`, plus one real cycle that uses a clip;
  - attribution in the video description.
  Then the `clip_scenes` setting becomes available to the AI.
- **B4, any time:**
  - The AI has not yet opened any production setting (it saw them on 30 Sep and chose not to). Note its
    first `setting_changes` (`py -m aimz settings`). Never force it.
  - The first runs on the stage C code (1 Oct 18:00 onwards): the strategist sets a `channel_identity`
    (`py -m aimz status` / strategy.md), videos use a cleared voice, no title revision loops starve the
    pipeline (watch `qa_failed` counts), and `youtube_retention` notes appear from 8 Oct.
  - Check new videos' rendered length still matches the timeline (captions in sync).
  - Check the first new video on a phone after the 30 Sep layout fix (docs/PLAN_FULL_CONTROL.md session log):
    one copy of each sentence, nothing cut mid-word, subtitles and credits visible above YouTube's labels and
    left of its buttons. The owner can simply send a screenshot.

---

## C. Already done on 30 Sep (for reference)

| Stage | What | Live-verified |
|---|---|---|
| 0 | Measurement fixes; optional revenue permission | Metrics collected with the existing login; revenue waits for A1 |
| 1 | Every video records every setting; the AI can open, lock or turn off settings | Strategist saw the controls |
| 2 | 15 production settings (voice, captions, motion, colours, counter, ...); caption-drift bug fixed | 43/43 sample renders; 18:00 run in sync |
| 3 | Your posting limits in code; 4-run schedule installed | 18:00 run posted 2 per platform |
| 4 | One instance per channel, niche claims, shared settings learning | Scratch instance via the real CLI |
| 5 | Music/sfx (original), 3 layouts, loop ending, clip scenes; Pexels/Pixabay built but off | Sample renders; stock waits for A3/A4 |
| 6 | Watch-time/revenue score parts (weight 0), niche value, Partner Program progress (`aimz money`) | 2 subs, 2,697 Shorts views/90 d |
