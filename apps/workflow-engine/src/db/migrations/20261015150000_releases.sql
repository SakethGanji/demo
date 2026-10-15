-- Phase 9 (BACKEND-DESIGN §2.2 M6): releases = immutable per-environment snapshots.
CREATE TABLE IF NOT EXISTS releases (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    environment       TEXT NOT NULL,
    number            INT NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT false,
    status            TEXT NOT NULL CHECK (status IN ('active','superseded','rolled_back','blocked')),
    snapshot          JSONB NOT NULL,
    checks            JSONB NOT NULL DEFAULT '[]',
    source_release_id TEXT REFERENCES releases(id),
    notes             TEXT,
    created_by        TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (project_id, environment, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_releases_active ON releases (project_id, environment) WHERE is_active;

-- migrate:down
DROP TABLE IF EXISTS releases;
