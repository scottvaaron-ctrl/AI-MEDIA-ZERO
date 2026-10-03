# Strategy Memory (AI-editable within the constitution)

_Version 43 — updated 2026-10-02T22:08:10+00:00 — confidence 0.10_

## Current audience model
Cold start: curious, general-interest short-form viewers with minimal historical knowledge. Prioritize clarity and relevance. Longer formats (35-60s) show higher engagement than shorter videos (<35s).

## Exploration ratio
50% of production slots explore.

## Winning content families
_None_

## Losing / retired content families
_None_

## Families under test
- strange_business_history (n=2, score=0.217)
- science_history_explainers (n=7, score=0.213)
- unusual_historical_events (n=3, score=0.264)
- history (n=2, score=0.232)
- forgotten_history (n=2, score=0.229)
- corporate_failures (n=2, score=0.201)
- internet_history (n=1, score=0.221)
- local_history (n=2, score=0.298)
- maps_data_stories (n=1, score=0.383)

## Untested hypotheses
_None_

## Strong hooks
- visual_hook

## Weak hooks
- narrative_hook
- numeric_hook

## Runtime observations
35-60s videos have higher mean scores than <35s videos, suggesting longer formats may perform better.

## Source quality observations
Wikipedia and Ars Technica provide the most ideas, but no video ideas have been converted yet. Sources like Hacker News and r/todayilearned have high item counts but no video ideas.

## Production bottlenecks
Script quality issues persist with 10 rejected scripts and 18 QA failed scripts. Production time averages 48.8 seconds, but script rejections remain a key bottleneck.

## Recurring audience requests
_None_

## Active experiments
- exp_20260923T220649_d519d660: Narrative vs Numeric Hook Experiment

## Retired experiments
- exp_20260907T210518_cb2d0407: Numeric vs narrative hook on retention - retired by owner 2026-09-23: human-designed experiment; experiments are the AI's own
- exp_20260907T205059_f72b53d9: Script quality improvement - retired by strategy update

## Settings evidence
- beat_pause_s [off (renderer default); bounds 0.0-1.2]: Silence at the end of each beat before the next visual, in seconds.
- caption_color [off (renderer default); bounds white/yellow/cyan/orange]: Caption text colour (black outline).
- caption_font [off (renderer default); bounds default/Poppins/Anton/Bebas Neue/Archivo Black]: Font of the burned narration captions ('default' is the renderer's own; others are free-licensed and shipped).
- caption_max_words [off (renderer default); bounds 2-14]: Most words shown in one caption at a time; longer spoken chunks are split and timed by length.
- caption_outline [off (renderer default); bounds 2-8]: Caption outline width in pixels.
- caption_position [off (renderer default); bounds bottom/low/mid_low]: Caption height above the bottom edge: bottom (10%), low (17%), mid_low (24%). All stay below the card text.
- caption_size [off (renderer default); bounds 0.028-0.06]: Caption text height as a fraction of the frame height (0.036 is about 69 px on 1920).
- ending [off (renderer default); bounds plain/loop]: plain, or loop: the video ends on its opening frame so a replay starts seamlessly.
- headline_on_cards [off (renderer default); bounds on/off]: Whether the beat's caption is printed on photo cards (cards without a photo always show it).
- max_scene_s [off (renderer default); bounds 2.5-15.0]: Longest time one visual stays on screen; a longer beat is cut into shots of equal length with a new camera move.
- motion_style [off (renderer default); bounds alternate/in/out/none]: Camera move on each visual when the script does not set one for the beat: alternate (in, out, in...), in, out or none.
- music [off (renderer default); bounds off/bright/calm/tense]: Background music bed by mood (original, rights-free), ducked under the narration; off = no music.
- music_volume_db [off (renderer default); bounds -30.0--6.0]: Music gain in dB before ducking. Measured under a narrator at about -16 dB RMS: -30 gives about -43 dB (barely there), -14 about -27 dB (a quiet bed), -6 about -19 dB (clearly present).
- palette_hue [off (renderer default); bounds 0.0-1.0]: Hue of the card background and accent colours (0-1 around the colour wheel). Off: a different hue per video.
- progress_counter [off (renderer default); bounds off/on]: A '3/7' beat counter in the top corner of every card.
- sentence_pause_s [off (renderer default); bounds 0.0-1.0]: Silence between sentences inside a beat, in seconds.
- sfx [off (renderer default); bounds off/on]: A soft whoosh at every cut between visuals.
- speech_length_scale [off (renderer default); bounds 0.75-1.35]: Narration speed as Piper's length_scale: 1.0 is the voice's normal pace, above 1 is slower, below 1 faster.
- template [off (renderer default); bounds card/full_bleed/ranking]: Visual layout: card (photo in the top part over a colour card), full_bleed (photo fills the frame, text over a dark gradient), ranking (card with a big #N for each beat).
- voice [off (renderer default); bounds en_US-kristin-medium/en_US-john-medium/en_US-norman-medium/en_US-ljspeech-medium/en_GB-cori-high]: Narration voice. Only licence-cleared voices (public-domain recordings, commercial use allowed). There is no default: until you open or lock it, every cleared voice is tried.
Regression: not yet (22 scored videos; runs from 30).

## Notes

