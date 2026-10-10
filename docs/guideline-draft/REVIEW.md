# Backhouse Explainers guideline: draft for line-by-line approval

_Drafted 2026-10-09 by Claude (plan part 3). **Not active.** It goes live only when the owner has approved every line
and it is copied to `config/guideline.md`. The first cycle after that retires the AI's running experiment, and from
then on topics, scripts, hooks, titles and the look are checked against it. The candidate file is
`guideline-DRAFT.md` in this folder; the real loader parses it cleanly (12 locked settings, 5 rules, nothing
unavailable)._

Answer each line **yes**, **no**, or **change to …**.

## Decision 1: the genre (the biggest choice)

The channel identity today is "maps and geography oddities". Our own YouTube results (52 measured Shorts) say
that is the weakest topic we make:

| Topic family | Videos | Median views | Avg % viewed |
|---|---|---|---|
| local_history | 4 | 206 | 40% |
| unusual_historical_events | 5 | 138 | 39% |
| internet_history | 3 | 75 | 28% |
| science_history_explainers | 12 | 36.5 | 36% |
| forgotten_history | 7 | 32 | 38% |
| **maps_data_stories** | **8** | **26.5** | **21%** |
| strange_business_history | 7 | 21 | 24% |

Top videos so far: Mariano Rivera's save record (554 views), the Saskatchewan River Delta "island" (403),
Ashton-under-Lyne (390), the first spacecraft to hit the Moon (301). Samples are small, so treat this as a lean, not
proof.

| | Genre line | |
|---|---|---|
| **A (draft)** | "the surprising true stories behind real places: local history, unusual historical events, odd borders and lost places" | Keeps the place-and-map identity, adds the two best performers. **Recommended** |
| B | "maps and geography oddities: borders, enclaves and lost places" | Today's identity; narrower; weakest results |
| C | Your own wording | |

The genre is enforced: each new idea is judged against it by the AI, and off-genre ideas are rejected.

## Locked settings (every video uses these; the AI cannot test them unless you approve a change)

| # | Line | Why |
|---|---|---|
| L1 | `voice: kokoro:af_bella` | Your choice on 9 Oct. "Unnatural voices" is the top AI tell viewers name (55%) |
| L2 | `template: full_bleed` | The photo fills the screen; the old card layout left half the frame empty |
| L3 | `headline_on_cards: off` | The headline repeated the narration; captions already show the words |
| L4 | `progress_counter: off` | A "3/7" counter is noise on a 30 s Short |
| L5 | `caption_max_words: 3` | Research: 1-4 word chunks are the safe default; we never tried short captions |
| L6 | `caption_size: 0.045` | Large enough for a phone; used in the 7 Oct test video |
| L7 | `caption_position: low` | Low on screen, below faces and above YouTube's own buttons |
| L8 | `motion_style: alternate` | Slow zoom in, then out, so still photos do not feel frozen |
| L9 | `max_scene_s: 4.0` | A long beat cuts to another shot at most every 4 s; we were holding one picture for 6-8 s |
| L10 | `music: calm` | Music was off on 11 of 13 measured videos; a quiet bed makes it feel produced. Our music is original (no revenue sharing) |
| L11 | `music_volume_db: -20` | About 17 dB under the voice, inside the 15-20 dB broadcast range |
| L12 | `ending: loop` | Ends on the opening frame so a replay is seamless (replays count as views on Shorts) |

**Left open for the AI to test, one at a time:** caption font, colour and outline, speaking speed, pauses, colour
hue, sound effects, and (after piece D) stock video clips.

## Length

| # | Line | Why |
|---|---|---|
| R1 | `runtime_s: 25 to 45` | Ours: 31-45 s has the best median views (54.5); over 45 s drops to 19.5 and 21% viewed. Outside data (108k Shorts) also favours under 45 s |

## Code checks

| # | Line | Why |
|---|---|---|
| C1 | `banned_openings: Did you know, In this video, Have you ever, Today we, Imagine` | Wind-ups that delay the point. "Imagine" is new versus the 7 Oct draft |
| C2 | `title_max_chars: 70` | Titles longer than this get cut off on phones |
| C3 | `hook_max_words: 15` | The first sentence must land in about 5 seconds |
| C4 | `hook_no_question: true` | A question delays the payoff ("Why did...?") |
| C5 | `hook_no_definition: true` | "X is a..." openings; with the new AI second look, only openings that state a surprise pass |

## Rules (the AI writes to these, and the critic checks them)

| # | Rule | Text |
|---|---|---|
| G1 | **G-topic** (topic) | Pick one named, real place (a town, building, border, island, road or site) and the one surprising true event or fact that makes it worth 30 seconds. The surprise must be in the sources, not invented. Skip press-release news, product launches, listicles, and anything whose only visuals would be text cards. |
| G2 | **G-hook** (script) | The first sentence states the most surprising concrete fact or stake about the place, in 15 words or fewer. Name the place in it. No greeting, no question to the viewer, and no opening that only says what the place is. |
| G3 | **G-structure** (script) | After the hook, give three short beats that each add one new fact a viewer did not expect, in story order. End on a payoff line that answers or sharpens the hook. Each beat names something a photo or map can show. |
| G4 | **G-tone** (script) | Write like a curious person telling a friend: concrete nouns, active verbs, short sentences, dates only when they matter. No textbook phrasing, no filler, no hype words, and nothing the sources do not say. |
| G5 | **G-title** (title) | The title names the place and the surprise, plainly and truthfully, in 70 characters or fewer. No clickbait, no all-caps words, no question marks. |

Not included: a cover/thumbnail rule (the pipeline does not apply cover rules yet), and anything on facts and
sources (the constitution already requires a stored source for every claim).

## After approval

1. The agent copies the approved text to `config/guideline.md` (recorded as guideline version 1, source "owner").
2. Backhouse Explainers' identity genre is updated to match the approved genre.
3. The next scheduled run uses it. The first two or three videos are checked by hand against every line.
