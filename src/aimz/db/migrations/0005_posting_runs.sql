-- Owner posting limits (plan stage 3, owner decision 2026-09-30).
--
-- posting_runs  one row per run that actually posted to a platform through its API, with the time of
--               its first post and how many posts each platform got. The limits in
--               config.yaml `publishing.limits` are checked against this table:
--               at most 2 posts per platform per run, a run posts only 2.5 h after the previous one,
--               at most 4 posting runs per local calendar day.

CREATE TABLE IF NOT EXISTS posting_runs (
  id           TEXT PRIMARY KEY,
  run_id       TEXT,
  started_at   TEXT NOT NULL,     -- UTC ISO time of the run's first post
  local_day    TEXT NOT NULL,     -- YYYY-MM-DD on the machine's clock, for the per-day cap
  counts_json  TEXT NOT NULL,     -- {"youtube": 2, "bluesky": 2, ...}
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_posting_runs_started ON posting_runs(started_at);
CREATE INDEX IF NOT EXISTS ix_posting_runs_day ON posting_runs(local_day);
