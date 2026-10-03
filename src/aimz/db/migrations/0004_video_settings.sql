-- Per-video settings: every video records every setting it was made with (plan stage 1, 2026-09-30).
--
-- setting_space   the dials the AI may test. Definitions and bounds come from code
--                 (aimz/experiments/settings.py CATALOG) and are synced on use; status, values and
--                 locked_value are the AI's. status: off (renderer default) | open (values tested per
--                 video) | locked (always locked_value). Values are JSON.
-- video_settings  one row per video and variable: the value used and where it came from
--                 (random = exploration floor, bandit = Thompson sampling, locked, default = off,
--                 ai = a content choice the AI made for the idea, recorded as a control).
-- niche_aliases   niche labels the strategist merged into another (alias -> canonical).
-- ideas.angle     the kind of story within the niche (content_family is the niche).

CREATE TABLE IF NOT EXISTS setting_space (
  variable      TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,              -- production | content | distribution
  description   TEXT NOT NULL,
  value_type    TEXT NOT NULL,              -- number | choice
  values_json   TEXT NOT NULL DEFAULT '[]',
  bounds_json   TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'off',
  locked_value  TEXT,
  updated_by    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_settings (
  video_id    TEXT NOT NULL,
  variable    TEXT NOT NULL,
  value       TEXT NOT NULL,
  source      TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (video_id, variable)
);
CREATE INDEX IF NOT EXISTS ix_video_settings_variable ON video_settings(variable, value);

CREATE TABLE IF NOT EXISTS niche_aliases (
  alias       TEXT PRIMARY KEY,
  canonical   TEXT NOT NULL,
  reason      TEXT,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

ALTER TABLE ideas ADD COLUMN angle TEXT;
