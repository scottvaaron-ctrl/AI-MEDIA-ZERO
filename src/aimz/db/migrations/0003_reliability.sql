-- Reliability and measurement bookkeeping (2026-09-28).
--
-- publications.next_attempt_at   when a failed upload may be retried automatically; NULL = no retry scheduled
-- publications.failure_kind      transient | uncertain | permanent: whether the platform may already hold the post
-- publications.metrics_closed_at when metric collection for this publication ended (the measurement window closed)
-- ideas.tech_failures            technical (not editorial) failures while writing; the idea is retried, not rejected
-- videos.measured_at             when every publication of the video finished its measurement window
--
-- New status values (TEXT columns, no constraint change needed):
--   runs.status          abandoned | interrupted
--   publications.status  abandoned  (closed out after its retries; the owner can still requeue it)
--   ideas.status         parked     (set aside after repeated technical failures; the owner can requeue it)
--   scripts.status       interrupted | parked
--   videos.status        measured | superseded (a failed render the owner re-queued past)

ALTER TABLE publications ADD COLUMN next_attempt_at TEXT;
ALTER TABLE publications ADD COLUMN failure_kind TEXT;
ALTER TABLE publications ADD COLUMN metrics_closed_at TEXT;
ALTER TABLE ideas ADD COLUMN tech_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE videos ADD COLUMN measured_at TEXT;

CREATE INDEX IF NOT EXISTS ix_publications_status ON publications(status, next_attempt_at);
