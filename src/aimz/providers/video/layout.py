"""Where text may go in a vertical video, so the platform's own buttons and labels never cover it.

Measured on a YouTube Shorts screenshot of this channel (owner, 2026-09-30), in fractions of the 1080x1920
frame: the top icons (back, search, menu) reach about 11% down; the like/comment/share column covers the
right ~15% of the width from 54% to 90% down; the channel name and Subscribe button start about 80% down,
with the title, description and comment bar below them. TikTok's overlay is similar. These are platform
limits (legibility), not style: every layout the AI can choose stays inside them.
"""

from __future__ import annotations

TOP_UI = 0.12  # nothing readable above this
LABEL_Y = 0.13  # corner labels (sources label, beat counter)
CARD_TEXT_TOP = 0.19
CARD_TEXT_BOTTOM = 0.50
CREDITS_Y = 0.505  # image credit and AI disclosure lines (up to four, 36 px each)
CAPTION_TOP = 0.58  # burned captions never rise above this
BOTTOM_UI = 0.22  # burned captions stay above the channel row, title and description
SIDE_UI_PX = 170  # caption side margins at 1080 px wide: clear of the right-hand button column
