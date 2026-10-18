-- 20261018120000_drift_smoke
-- Stage 3B: API drift alerts (api_drift), live smoke test config (smoke_configs) and smoke run history (smoke_runs).
-- Additive only; safe for the running engine.

-- migrate:up
SET search_path TO "workflow-app";
CREATE TABLE IF NOT EXISTS api_drift (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id        TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    node_name          TEXT NOT NULL,
    api_ref            JSONB,
    basis              TEXT NOT NULL,
    status_code        INTEGER,
    diffs              JSONB NOT NULL DEFAULT '[]'::jsonb,
    fingerprint        TEXT NOT NULL,
    first_seen         TIMESTAMP NOT NULL DEFAULT now(),
    last_seen          TIMESTAMP NOT NULL DEFAULT now(),
    count              INTEGER NOT NULL DEFAULT 1,
    last_execution_id  TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    resolved_by        TEXT,
    resolved_at        TIMESTAMP,
    CONSTRAINT uq_api_drift_fingerprint UNIQUE (workflow_id, node_name, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_api_drift_project_id ON api_drift (project_id);
CREATE INDEX IF NOT EXISTS ix_api_drift_workflow_id ON api_drift (workflow_id);

CREATE TABLE IF NOT EXISTS smoke_configs (
    workflow_id    TEXT PRIMARY KEY REFERENCES workflows(id) ON DELETE CASCADE,
    enabled        BOOLEAN NOT NULL DEFAULT false,
    test_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    live_nodes     JSONB NOT NULL DEFAULT '[]'::jsonb,
    schedule       TEXT,
    before_release BOOLEAN NOT NULL DEFAULT false,
    next_run_at    TIMESTAMP,
    updated_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS smoke_runs (
    id           TEXT PRIMARY KEY,
    workflow_id  TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    trigger      TEXT NOT NULL DEFAULT 'manual',
    status       TEXT NOT NULL,
    started_at   TIMESTAMP NOT NULL DEFAULT now(),
    finished_at  TIMESTAMP,
    results      JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_smoke_runs_workflow_id ON smoke_runs (workflow_id, started_at DESC);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS smoke_runs;
DROP TABLE IF EXISTS smoke_configs;
DROP TABLE IF EXISTS api_drift;
