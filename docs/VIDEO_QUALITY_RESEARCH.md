# Video quality research: why the Shorts read as "AI slop" (2026-10-07)

This report is research only; no code or prompts have been changed. It has two parts:
- an audit of what the pipeline actually renders today;
- outside evidence on what makes faceless Shorts engaging, and on the risks under YouTube's policies.

Sources are tagged:
- **[V]** verified from a primary source or a named dataset;
- **[R]** reported by a reputable outlet;
- **[F]** creator folk wisdom, with no controlled evidence.

## 1. What our videos look like now (audit)

I used contact sheets of `vid_20261006T220452_86e1e8ee` (Misti volcano) and `vid_20261007T130319_5be99b81` (Mars "Turquoise Bay"), plus stats from the 25 most recent `timeline.json` files and the `metrics` table.

| Problem | Evidence | Owner of the fix |
|---|---|---|
| **Half the frame is empty.** The default `card` template puts the photo in the top 50%, and the bottom half is a flat navy gradient holding only a caption. | `layout.py`: `CARD_TEXT_BOTTOM = 0.50`, `CAPTION_TOP = 0.58`; visible on every image frame | Renderer default (engineering) |
| **Images don't match the narration.** The Mars video shows a Stanford building, a Croatian lake and a Vancouver Island fault map while the narration talks about Mars. | Contact sheet `sheet2.png` | Asset selection (engineering: there is no relevance check) |
| **Slideshow pacing.** About 6–8 s per visual; one image held through 3 caption changes. | Mean of 25 videos: 5–8.5 s per scene; every video has only 3–10 visuals | Partly creative (beat count), partly engineering (one asset per beat) |
| **Many videos have no picture at all.** They are text cards only. | e.g. "Space Station Crew" is 10/10 text cards; "NASA's CSDA Program" 6/6; "Warren Commission" 7/7 | Asset supply (engineering) |
| **Three blocks of text compete.** The headline restates the narration, then the credit line, then the sentence caption. | Every frame | Mix of renderer default and AI choice (`headline_on_cards`) |
| **Attribution is burned mid-frame at reading size.** Filenames like "Clark Center Stanford October 2019 HDR 2.jpg — King of Hearts — CC BY-SA 4.0" | `cards.py` credits block at `CREDITS_Y` | Engineering / compliance |
| **Captions are long sentence fragments.** Up to about 12 words, not 1–4-word chunks. | `captions.srt` | AI setting `caption_max_words` (never tried; always default) |
| **Piper medium voice, no music.** Music is `off` on 11 of the 13 videos measured. | `config/strategy.md` settings evidence | AI settings / provider |
| **Encyclopedic scripts.** "Misti is a volcano in the southern Peruvian Andes…" Many topics are rewrites of NASA press releases. | Titles and narration | Creative (AI-owned) |

**Current performance:** the latest YouTube metrics cover 44 Shorts. They average 86 views, 41% viewed, and 15.7 s average watch time.

## 2. Outside evidence

### YouTube policy is now the biggest risk, not just taste
- [V] The YPP "inauthentic content" policy (renamed July 15, 2025) does not monetize three kinds of content:
  - "Image slideshows, templated storylines, or scrolling text with minimal or no narrative";
  - "AI-generated content made with generic or unoriginal templates";
  - content that "exclusively features readings of other materials you did not originally create".

  https://support.google.com/youtube/answer/1311392
- [R] On July 16, 2026, Matt Halprin (YouTube Trust & Safety) clarified the policy. Reviewers judge at **channel level** whether videos feel interchangeable when watched in a row, for example "the same script structure re-rendered with different nouns". (Netinfluencer and AndroidHeadlines, 2026-07-21; secondary sources.)
- [V] AI disclosure under Help 14328491 is for *realistic* synthetic content. TTS narration over real photos doesn't appear to need the label. Our description disclosure already goes beyond it.

Our current template (same layout, same voice, same Ken Burns, Wikipedia-style read) matches the policy's own description of slop. This matters for monetization later, not only for views.

### What viewers notice
- [V-survey] Animoto, Jan 2026 (n≈460) asked viewers what gives AI video away. "Unnatural voices" was named by 55% and "lack of emotional tone" by 51%. The voice is the best-evidenced tell that applies to us.
- [F] Other common tells: visuals unconnected to the words, uniform slideshow rhythm, on-screen text duplicating narration, and an encyclopedic tone with no stakes.

### Retention
- [V] YouTube ranks Shorts on "viewed vs swiped away" and publishes no thresholds. Its advice is "prioritize storytelling over a specific length".
- [V-dataset] Quso.ai looked at 108k Shorts (2025). Median views by length: 901 for 11–20 s, 347 for 21–30 s, 188 for 31–45 s, 186 for 46–60 s. This is correlational, but our 35–70 s range sits in the weak bands.
- [V-academic] Dost & Huang (2026, n=242) found that faster cutting *reduced* sustained engagement. Change visuals when the meaning changes, not on a timer.
- [F] Folk advice says the first second must show the most striking image or claim, with motion in frame 1. It also says to avoid any title-card wind-up.

### Captions
- [V] 69% of viewers watch with the sound off in public (Verizon/Publicis 2019), so burned-in captions are worth keeping.
- [V-academic] Word-highlight captions help learning but distract in everyday viewing (MUM 2024). The safe default is short 1–4-word chunks. The evidence for karaoke-style highlighting is weak.

### Voice: free, local, commercially licensed upgrades over Piper

| Model | License | Runs on CPU | Note |
|---|---|---|---|
| **Kokoro-82M** | Apache-2.0 | Yes, real-time | Safest drop-in; `pip install kokoro` or `kokoro-onnx`; 54 voices |
| **Chatterbox (Nano)** | MIT | Yes (Nano) | Most expressive on CPU (`exaggeration` control); watermarked |
| **Pocket TTS** (Kyutai) | MIT code, CC-BY-4.0 weights | Yes | Credit in the description |
| **NeuTTS Air** | Apache-2.0 | Yes (GGUF/ONNX) | ~0.5B |

**Avoid** these:
- F5-TTS (non-commercial);
- XTTS-v2 (non-commercial);
- VibeVoice (Microsoft says research-only, and the repo has been pulled);
- KittenTTS (weights under a custom license, unchecked).

### Visuals
- [V] Free sources of moving footage:
  - NASA video (public domain, but no logo use or implied endorsement);
  - the Pexels video API (free, no attribution required, rate-limited);
  - the Pixabay video API (attribution where results are shown; no "systematic mass downloads").
- [V] 2.5D parallax from a still is possible with Depth-Anything V2 *Small* or DA3-Small/Base (Apache-2.0, CPU). The larger checkpoints and "3D Ken Burns" are non-commercial.
- **Relevance gate:**
  - [V] LAION dropped image–text pairs below a CLIP ViT-B/32 cosine of 0.28.
  - For us, a SigLIP 2 model (Apache-2.0, CPU) can score each candidate image against a concrete visual query and reject the off-topic ones.
  - Thresholds must be calibrated on about 50 of our own hand-labelled pairs.
  - CLIP is weak on dates and named people, so keep the metadata check for those.
- **Calibration (2026-10-08).** SigLIP 2 base, 8-bit ONNX, scoring a 330 px preview (Wikimedia's served size) against the beat.
  - Data: 50 distinct picture/narration pairs from 48 past videos (`scripts/calibrate_relevance.py build`), labelled
    by Claude: 34 match, 16 mismatch, no synthetic mismatches. Rule: "match" if the picture shows the kind of thing
    the sentence is about; a wrong named person or date still counts as a match, because this model cannot judge
    those. Files: `data/calibration/relevance/` (pairs.csv, contact-sheet.png, result.json).
  - Best single signal: the narration (balanced accuracy 0.75). The AI's `visual_query` was worse, often because it
    asks for something other than what the narration says (e.g. "map of New Zealand" for a beetle).
  - Scores overlap heavily: many good pictures score very low when the narration is abstract. The clearly unrelated
    ones (a pop concert for a NASA "Mars" story, a statue for a nebula) sit at the bottom.
  - The balanced-accuracy pass mark (0.0012) rejected 13 of 34 good pictures, which would turn many beats into text
    cards. **Chosen instead: a floor of 0.00005** (keeps 31/34 good, rejects 6/16 bad outright). The main gain is
    ranking: the best-scoring candidate of the first search that has one above the floor (up to 6 per source) is
    used instead of the first download.
  - Revisit after a few weeks of run notes (`relevance` entries: top scores, text-card rate).

### Attribution
- [V] Under CC 4.0, attribution may be given "in any reasonable manner based on the medium". Full TASL credit (title, author, source, license) in the description is defensible for Shorts **if nothing else is credited on screen**. CC BY-SA 3.0 requires credits to be "at least as prominent" as any other credits shown.
- Risk, low certainty: cropping a BY-SA image may make the video an adaptation, which would then have to be offered under BY-SA itself. Prefer PD, CC0 and CC BY images and down-rank BY-SA.

### Sound
- [R] YouTube normalizes playback to about −14 LUFS and only turns loud audio down. Master to −14 LUFS with true peak ≤ −1 dBTP.
- [V] Keep the music bed roughly 15–20 dB under speech (WCAG 1.4.7 / broadcast practice).
- [V] Self-sourced PD/CC0 music avoids splitting Shorts revenue with label-claimed music.

## 3. What could be done

### A. Engineering and capability fixes
These don't tell the AI what content to make, so they fit the creative-autonomy rule.
1. **Asset relevance gate.** Add SigLIP scoring and reject images that don't match their beat. This would have caught the Mars video.
2. **More visuals per beat, and moving footage.** Add NASA, Pexels and Pixabay video providers, and offer several assets per beat so a long beat can cut between them.
3. **Better voices in the toolbox.** Add Kokoro (and optionally Chatterbox-Nano) as voice options next to Piper. The AI keeps choosing the voice.
4. **Attribution to the description.** Remove the on-screen credit lines and put full TASL credit in the description. This needs your sign-off because it touches compliance.
5. **Loudness mastering.** Master every render to −14 LUFS.
6. **Parallax motion.** Add 2.5D parallax as an additional `motion_style` option.

### B. Defaults that shape the look
These are borderline, and your call.
- The `card` template leaves half the frame empty. Options:
  - make `full_bleed` the renderer default;
  - or leave the default alone and just surface to the AI that its settings evidence shows the default has never been varied.

### C. Creative findings
Per the constitution these are the AI's to decide. The options are:
- hand them to the strategist as optional data (like `starting_genres`);
- or keep them out of the pipeline.

The findings are:
- shorter runtimes (about 20–45 s);
- a hook in the first second;
- an original angle rather than reading the encyclopedia;
- short caption chunks;
- music on;
- channel-level variety in structure.
