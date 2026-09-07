# Roadmap

## Milestone 1 — validation (current)

Target: ~30 published videos across 3+ content families with enough measured data to answer:

- Did performance improve over time? (`aimz strategy review weekly`: first-half vs second-half score)
- Which hooks worked? Which formats/families? Which topics? Which sources yielded stories?
- Which videos generated subscribers/followers?
- Did the AI make progressively better decisions? (strategy version history, selection mode shifts)
- Is there evidence that spending money would improve results? (bottleneck report)

`aimz analytics` prints the milestone status; the dashboard shows it on the front page.

## Recommended next five improvements

1. **Long-form YouTube support** – `content.long_form.enabled` exists in config; add a
   `LongFormScriptAgent` (thesis, research packet, outline, sections, visual plan, citations) and
   a 1920x1080 timeline profile. The renderer and timeline schema already support arbitrary sizes.
2. **Better visuals at $0** – map/chart/timeline generators (matplotlib is free) driven by
   `visual_type=chart|map|timeline` beats, plus Ken Burns on multiple assets per scene and simple
   `xfade` transitions.
3. **Word-level captions** – Piper 1.8 exposes phoneme alignments (`include_alignments=True`);
   use them for karaoke-style word highlighting in the ASS file.
4. **Posting-time and cadence learning** – add `posting_hour` as an experiment variable and let
   the strategist recommend cadence from returning-viewer data.
5. **TikTok Direct Post publisher** – implement `TikTokDirectPostPublisher` behind the same
   `Publisher` interface (creator_info query → owner selects privacy in the dashboard → consent →
   `FILE_UPLOAD`), enabled only after the owner's app passes TikTok's audit.

## Later

- Local image generation (SDXL-Turbo / FLUX-schnell via a free local runtime) as an
  `ImageProvider` when the GPU allows; keep Wikimedia as the fallback.
- Postgres/Supabase backend (see FUNDED_V1.md).
- Multi-channel / multi-language variants sharing the same strategy memory.
- Retention-curve ingestion from YouTube Analytics (`elapsedVideoTimeRatio`) to score hooks by
  real early drop-off rather than average percentage viewed.
- Owner-approved revenue reinvestment rule (`AI may reinvest up to N% of earned revenue`).
