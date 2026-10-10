# Owner to-do: what is left for AI Media Zero to be 100% done

_Rewritten 2026-10-09 (the 30 Sep version had gone stale). Section A is yours, B is for an agent, C is the
history. Commands run from `C:\Users\scott\Documents\Agentic_Youtube` in PowerShell; `py` means
`.\.venv\Scripts\python.exe` (never the `aimz.exe` shim: Smart App Control blocks it)._

---

## A. Your steps

### A1. YouTube compliance screencast: due Thu 15 Oct
The agent records it (owner OK 9 Oct): Google's consent screen, the channel check, upload consent, the upload
code, a real upload, the saved request and response, the live Short and its YouTube Studio details, analytics.
- [ ] **Watch the recording** when the agent shows it, and say OK (or what to change).
- [ ] The agent then uploads it unlisted and sends the Gmail reply (thread "YouTube API Services: Thank you for
      your submission"). It shows you the email first.
- Then Google completes the review in its own time. Until then: no new channels (promise in the 2 Oct reply).

### A2. Stock video (piece D): done 9 Oct
- [x] Pixabay key added to `.env`; stock photos and clips are live.
- [ ] Optional, later: Pexels has paused new API keys. If https://www.pexels.com/api/key/ offers a key again,
      add `PEXELS_API_KEY=...` to `.env`; it is used automatically.

### A3. Scripts waiting for your sensitive-topic review (11 on 9 Oct)
The rules require your yes or no on scripts that mention, for example, real people and allegations.
- [ ] Ask the agent for the list (one line each), or open the dashboard (`py -m aimz dashboard`,
      http://127.0.0.1:8420), or run `py -m aimz approve script <id>` / `--reject`.

### A4. Three more YouTube channels (after Google completes the review)
For each channel (about 5 minutes; the agent runs the commands and checks):
- [ ] Create the channel on YouTube (a brand channel under your Google account is fine).
- [ ] Approve its login when Google's consent screen opens (pick the new channel, tick every box).
- [ ] Confirm automatic uploads for it when asked (`py -m aimz instance consent <name>`).
- [ ] Approve its guideline line by line (each channel gets its own; genre, voice and look must differ).
- The agent sends Google each channel's link before its first upload. Full guide: `docs/AUTONOMOUS_SETUP.md`
  Part F.

### A5. TikTok goes public
- [ ] Decide whether the agent should try to record TikTok's demo video (as with the YouTube screencast).
      TikTok's review needs a demo of its own login and posting screens in the sandbox, so the YouTube
      recording cannot be reused as it is. Until the review passes, TikTok posts stay private and give no data.

### A6. Keep the laptop on
- The laptop is the always-on host (decision D-015, confirmed 9 Oct). The scheduled task asks Windows to wake it
  for the 09:00 and 18:00 runs and catches up a missed run, but keep it plugged in and on: wake timers can be
  off in Windows power settings.

---

## B. Checks for an agent

- **B1, after the screencast's re-login:** `py -m aimz youtube whoami` → Backhouse Explainers; `py -m aimz
  analytics --collect`, then confirm the newest YouTube metrics' `raw_json` has `revenue` or a clean
  `revenue_error`, and the other metrics still arrive. The login no longer expires weekly (consent screen in
  production since 9 Oct).
- **B2, done 9 Oct for Pixabay; repeat for Pexels if a key is added:** a live check of each provider: one photo search, one video search and one
  download each, with the fields as parsed; the Pexels video endpoint (`https://api.pexels.com/videos/search`);
  `scripts\render_matrix.py --only clip_scenes`, plus one real cycle that uses a clip; attribution in the
  description. Then set `assets.stock.enabled: true`; `clip_scenes` becomes available.
- **B3, first runs under guideline v1 (10 Oct onwards):** check the first two or three videos against every
  line of `config/guideline.md`: genre, Bella voice, 25-45 s, full-screen photos, 3-word captions, calm music,
  loop ending, hook and title checks, the five rules. The first cycle retires the old free-form experiment;
  `py -m aimz experiments` should show at most one guideline experiment afterwards.
- **B4, after A4 (channels):** `py -m aimz instance list` shows a login for all 4 and `whoami` is right for
  each; each channel's first runs have at most 2 posts per platform; `py -m aimz schedule status` lists 4
  tasks; two or more channels run 48 h without touching each other's data.
- **B5, any time:**
  - `py -m aimz status` shows no `loudness_off_target`, `voice_unavailable` or picture-check warnings.
  - Revisit the picture-check floor (0.00005) after a few weeks of run notes; the owner confirms any change.
  - Check a new video on a phone: one copy of each sentence, subtitles above YouTube's labels.

---

## C. Done (for reference)

| When | What |
|---|---|
| 30 Sep | Plan stages 0-6 in code (measurement, per-video settings, posting limits, several channels, music/layouts, revenue score parts) |
| 1 Oct | No default voice; privacy page published; posting limits 2 per platform per run, 2 runs a day |
| 7-8 Oct | Rescope to owner guidelines (part 1); kill and posting switches; constitution sections 1, 3, 4, 8 |
| 8 Oct | Part 2 A (script checks) and B (picture matching, credits off screen); YouTube re-login; test video unlisted |
| 9 Oct | Part 2 C (Bella voice, -14 LUFS); constitution section 5; hook second look; consent screen in production; guideline v1 live; the AI may test any guideline item; watch time in the success score (0.15); the 19 Sep Borneo upload dropped; decision log updated (D-050 to D-065); everything pushed to GitHub |
