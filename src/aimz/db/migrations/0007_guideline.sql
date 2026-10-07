-- Per-channel guideline: version store and owner-approved change proposals.
CREATE TABLE IF NOT EXISTS guideline_versions (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guideline_proposals (
    id TEXT PRIMARY KEY,
    experiment_id TEXT,
    item TEXT NOT NULL,
    before TEXT,
    after TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

ALTER TABLE experiments ADD COLUMN guideline_version INTEGER;
ALTER TABLE ideas ADD COLUMN guideline_breaks TEXT;
