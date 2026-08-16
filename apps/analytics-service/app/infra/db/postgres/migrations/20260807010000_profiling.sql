-- 20260807010000_profiling
-- Wave 1 §8 + §10: persisted profile runs with rule-based insights, and saved
-- views. All three tables key sheet-scoped state on logical_sheet_id (the
-- standing rule since Wave 0), so confirm-rename never has to rewrite them.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS profile_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id          UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version_id  UUID NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    logical_sheet_id    UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    job_id              UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed')),
    algorithm_version   INT  NOT NULL DEFAULT 1,
    profile             JSONB,
    error               TEXT,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    -- Idempotent re-profiling: one run per (version, sheet, engine version).
    UNIQUE (dataset_version_id, logical_sheet_id, algorithm_version)
);
CREATE INDEX IF NOT EXISTS ix_profile_runs_version ON profile_runs (dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_profile_runs_sheet   ON profile_runs (logical_sheet_id);

CREATE TABLE IF NOT EXISTS profile_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_run_id  UUID NOT NULL REFERENCES profile_runs(id) ON DELETE CASCADE,
    rule            TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    column_name     TEXT,
    message         TEXT NOT NULL,
    evidence        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_profile_insights_run ON profile_insights (profile_run_id);

CREATE TABLE IF NOT EXISTS dataset_views (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id        UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    logical_sheet_id  UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    version_selector  JSONB NOT NULL DEFAULT '{"mode": "current"}'::jsonb,
    query             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, name)
);
CREATE INDEX IF NOT EXISTS ix_dataset_views_sheet ON dataset_views (logical_sheet_id);

-- migrate:down
SET search_path TO "accelerator";

DROP TABLE IF EXISTS dataset_views;
DROP TABLE IF EXISTS profile_insights;
DROP TABLE IF EXISTS profile_runs;
