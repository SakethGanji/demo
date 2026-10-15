-- Phase 4 (BACKEND-DESIGN §2.2 M2): broker call log (parameters stored redacted).
CREATE TABLE IF NOT EXISTS broker_calls (
    id              TEXT PRIMARY KEY,
    execution_id    TEXT,
    project_id      TEXT,
    environment     TEXT,
    origin          TEXT,
    node_type       TEXT,
    params_redacted JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL,
    error_code      TEXT,
    error           TEXT,
    duration_ms     REAL,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_broker_calls_execution ON broker_calls (execution_id);
CREATE INDEX IF NOT EXISTS ix_broker_calls_project_created ON broker_calls (project_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS broker_calls;
