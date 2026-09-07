-- AI Media Zero initial schema.
-- Conventions: TEXT ids, ISO-8601 TEXT timestamps, JSON as TEXT. Portable to Postgres.

CREATE TABLE IF NOT EXISTS system_state (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,           -- cycle | research | ideas | script | produce | publish | analytics | learn | comments
  status       TEXT NOT NULL,           -- running | ok | failed | killed
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  duration_ms  INTEGER,
  summary_json TEXT,
  error        TEXT
);

CREATE TABLE IF NOT EXISTS sources (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  kind            TEXT NOT NULL,        -- rss | atom | wikipedia_onthisday | wikipedia_featured | reddit_rss | audience | manual
  url             TEXT,
  category        TEXT,
  credibility     REAL NOT NULL DEFAULT 0.5,
  enabled         INTEGER NOT NULL DEFAULT 1,
  last_fetched_at TEXT,
  last_status     TEXT,
  last_error      TEXT,
  items_yielded   INTEGER NOT NULL DEFAULT 0,
  ideas_yielded   INTEGER NOT NULL DEFAULT 0,
  videos_yielded  INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_items (
  id               TEXT PRIMARY KEY,
  source_id        TEXT NOT NULL REFERENCES sources(id),
  url              TEXT NOT NULL,
  url_hash         TEXT NOT NULL,
  title            TEXT NOT NULL,
  summary          TEXT,
  category         TEXT,
  published_at     TEXT,
  ingested_at      TEXT NOT NULL,
  freshness_score  REAL,
  content_angle    TEXT,
  credibility      REAL,
  copyright_notes  TEXT,
  dedupe_key       TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'new',   -- new | used | ignored
  raw_json         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_source_items_url_hash ON source_items(url_hash);
CREATE INDEX IF NOT EXISTS ix_source_items_dedupe ON source_items(dedupe_key);
CREATE INDEX IF NOT EXISTS ix_source_items_status ON source_items(status, ingested_at);

CREATE TABLE IF NOT EXISTS ideas (
  id                     TEXT PRIMARY KEY,
  run_id                 TEXT,
  title                  TEXT NOT NULL,
  premise                TEXT NOT NULL,
  hook                   TEXT NOT NULL,
  hook_type              TEXT,
  content_family         TEXT NOT NULL,
  target_platform        TEXT NOT NULL,          -- tiktok | youtube_shorts | both | youtube_long
  format                 TEXT NOT NULL DEFAULT 'short',
  suggested_runtime_s    INTEGER,
  source_item_ids_json   TEXT NOT NULL,
  production_difficulty  TEXT,
  expected_cost_usd      REAL NOT NULL DEFAULT 0,
  originality_notes      TEXT,
  scores_json            TEXT NOT NULL,
  opportunity_score      REAL NOT NULL,
  rationale              TEXT,
  status                 TEXT NOT NULL DEFAULT 'candidate',  -- candidate | selected | rejected | scripted | produced | published | killed
  allocation_mode        TEXT,                                -- explore | exploit
  experiment_id          TEXT,
  experiment_arm         TEXT,
  eic_notes              TEXT,
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ideas_status ON ideas(status, opportunity_score);

CREATE TABLE IF NOT EXISTS scripts (
  id                   TEXT PRIMARY KEY,
  idea_id              TEXT NOT NULL REFERENCES ideas(id),
  run_id               TEXT,
  version              INTEGER NOT NULL DEFAULT 1,
  title                TEXT NOT NULL,
  hook_line            TEXT NOT NULL,
  beats_json           TEXT NOT NULL,        -- ordered narrative beats with visual notes
  narration_text       TEXT NOT NULL,
  estimated_runtime_s  REAL,
  word_count           INTEGER,
  description          TEXT,
  tags_json            TEXT,
  thumbnail_concept    TEXT,
  status               TEXT NOT NULL DEFAULT 'draft',  -- draft | factchecked | qa_failed | approved | rejected | needs_owner_review
  qa_json              TEXT,
  factcheck_json       TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scripts_idea ON scripts(idea_id, version);

CREATE TABLE IF NOT EXISTS claims (
  id                   TEXT PRIMARY KEY,
  script_id            TEXT NOT NULL REFERENCES scripts(id),
  text                 TEXT NOT NULL,
  claim_type           TEXT,             -- date | number | name | event | general
  source_urls_json     TEXT,
  verification_status  TEXT NOT NULL DEFAULT 'unverified', -- supported | weak | unverifiable | contradicted | softened | removed
  verification_notes   TEXT,
  created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
  id            TEXT PRIMARY KEY,
  provider      TEXT NOT NULL,
  kind          TEXT NOT NULL,        -- image | chart | card | audio
  title         TEXT,
  file_path     TEXT,
  source_url    TEXT,
  page_url      TEXT,
  license       TEXT,
  license_url   TEXT,
  attribution   TEXT,
  author        TEXT,
  width         INTEGER,
  height        INTEGER,
  sha256        TEXT,
  query         TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
  id                   TEXT PRIMARY KEY,
  script_id            TEXT NOT NULL REFERENCES scripts(id),
  idea_id              TEXT NOT NULL,
  run_id               TEXT,
  title                TEXT NOT NULL,
  format               TEXT NOT NULL,
  resolution           TEXT NOT NULL,
  duration_s           REAL,
  file_path            TEXT,
  thumbnail_path       TEXT,
  captions_path        TEXT,
  timeline_json        TEXT,
  package_dir          TEXT,
  production_seconds   REAL,
  production_cost_usd  REAL NOT NULL DEFAULT 0,
  ai_disclosure        TEXT,
  status               TEXT NOT NULL DEFAULT 'planned',  -- planned | rendering | rendered | failed | approved | rejected | published
  error                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenes (
  id            TEXT PRIMARY KEY,
  video_id      TEXT NOT NULL REFERENCES videos(id),
  idx           INTEGER NOT NULL,
  start_s       REAL,
  duration_s    REAL,
  narration     TEXT,
  caption       TEXT,
  visual_type   TEXT,
  asset_id      TEXT,
  crop          TEXT,
  zoom          TEXT,
  animation     TEXT,
  transition    TEXT,
  source        TEXT,
  disclosure    TEXT,
  notes         TEXT,
  audio_path    TEXT,
  image_path    TEXT
);
CREATE INDEX IF NOT EXISTS ix_scenes_video ON scenes(video_id, idx);

CREATE TABLE IF NOT EXISTS publications (
  id                 TEXT PRIMARY KEY,
  video_id           TEXT NOT NULL REFERENCES videos(id),
  platform           TEXT NOT NULL,      -- youtube | tiktok
  publisher          TEXT NOT NULL,
  mode               TEXT NOT NULL,      -- draft | package | private | scheduled | public
  idempotency_key    TEXT NOT NULL,
  platform_video_id  TEXT,
  url                TEXT,
  privacy            TEXT,
  scheduled_for      TEXT,
  package_dir        TEXT,
  status             TEXT NOT NULL,      -- pending | packaged | uploading | uploaded | published | failed | blocked
  attempts           INTEGER NOT NULL DEFAULT 0,
  last_error         TEXT,
  posted_at          TEXT,
  metadata_json      TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_publications_idem ON publications(idempotency_key);

CREATE TABLE IF NOT EXISTS metrics (
  id                  TEXT PRIMARY KEY,
  publication_id      TEXT NOT NULL REFERENCES publications(id),
  video_id            TEXT NOT NULL,
  platform            TEXT NOT NULL,
  captured_at         TEXT NOT NULL,
  source              TEXT NOT NULL,     -- api | manual
  hours_since_post    REAL,
  views               INTEGER,
  impressions         INTEGER,
  ctr                 REAL,
  retention_3s        REAL,
  avg_watch_time_s    REAL,
  avg_percent_viewed  REAL,
  completion_rate     REAL,
  likes               INTEGER,
  comments            INTEGER,
  shares              INTEGER,
  subscribers_gained  INTEGER,
  followers_gained    INTEGER,
  returning_viewers   INTEGER,
  watch_time_minutes  REAL,
  revenue_usd         REAL,
  raw_json            TEXT
);
CREATE INDEX IF NOT EXISTS ix_metrics_pub ON metrics(publication_id, captured_at);

CREATE TABLE IF NOT EXISTS comments (
  id                        TEXT PRIMARY KEY,
  publication_id            TEXT NOT NULL REFERENCES publications(id),
  platform_comment_id       TEXT,
  author                    TEXT,
  text                      TEXT NOT NULL,
  posted_at                 TEXT,
  classification            TEXT,      -- question | correction | content_request | confusion | disagreement | joke | follow_up | audience_signal | spam
  sentiment                 TEXT,
  useful                    INTEGER NOT NULL DEFAULT 0,
  converted_source_item_id  TEXT,
  created_at                TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_comments_platform ON comments(publication_id, platform_comment_id);

CREATE TABLE IF NOT EXISTS experiments (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  hypothesis     TEXT NOT NULL,
  variable       TEXT NOT NULL,
  control        TEXT NOT NULL,
  treatment      TEXT NOT NULL,
  primary_kpi    TEXT NOT NULL,
  secondary_kpi  TEXT,
  min_sample     INTEGER NOT NULL,
  status         TEXT NOT NULL,        -- proposed | running | concluded | retired
  started_at     TEXT,
  concluded_at   TEXT,
  result_json    TEXT,
  confidence     REAL,
  conclusion     TEXT,
  created_by     TEXT NOT NULL,        -- ai | owner
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
  id              TEXT PRIMARY KEY,
  version         INTEGER NOT NULL,
  created_by      TEXT NOT NULL,       -- ai | owner | system
  run_id          TEXT,
  state_json      TEXT NOT NULL,
  markdown        TEXT NOT NULL,
  change_summary  TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_strategy_version ON strategy_versions(version);

CREATE TABLE IF NOT EXISTS agent_runs (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL,
  agent               TEXT NOT NULL,
  operation           TEXT NOT NULL,
  status              TEXT NOT NULL,   -- running | ok | failed | denied | killed
  started_at          TEXT NOT NULL,
  ended_at            TEXT,
  duration_ms         INTEGER,
  input_refs_json     TEXT,
  output_refs_json    TEXT,
  error               TEXT,
  estimated_cost_usd  REAL NOT NULL DEFAULT 0,
  actual_cost_usd     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_agent_runs_run ON agent_runs(run_id, started_at);

CREATE TABLE IF NOT EXISTS provider_usage (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT,
  agent_run_id        TEXT,
  provider            TEXT NOT NULL,
  action              TEXT NOT NULL,
  content_id          TEXT,
  estimated_cost_usd  REAL NOT NULL,
  actual_cost_usd     REAL NOT NULL DEFAULT 0,
  tokens_in           INTEGER,
  tokens_out          INTEGER,
  duration_ms         INTEGER,
  status              TEXT NOT NULL,   -- ok | failed | denied
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_provider_usage_created ON provider_usage(created_at);

CREATE TABLE IF NOT EXISTS ledger (
  id                  TEXT PRIMARY KEY,
  entry_type          TEXT NOT NULL,   -- authorization | denial | spend | revenue | owner_injection | reinvestment
  provider            TEXT,
  action              TEXT,
  content_id          TEXT,
  run_id              TEXT,
  estimated_cost_usd  REAL NOT NULL DEFAULT 0,
  actual_cost_usd     REAL NOT NULL DEFAULT 0,
  amount_usd          REAL NOT NULL DEFAULT 0,
  budget_month        TEXT NOT NULL,
  note                TEXT,
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_month ON ledger(budget_month, entry_type);

CREATE TABLE IF NOT EXISTS errors (
  id          TEXT PRIMARY KEY,
  run_id      TEXT,
  agent       TEXT,
  operation   TEXT,
  error_type  TEXT,
  message     TEXT,
  traceback   TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuration_versions (
  id          TEXT PRIMARY KEY,
  key         TEXT NOT NULL,          -- constitution | config | feeds | budget | youtube_mode | kill_switch
  content     TEXT NOT NULL,
  changed_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
