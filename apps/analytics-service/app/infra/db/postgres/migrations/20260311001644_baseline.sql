-- 20260311001644_baseline

-- migrate:up

CREATE SCHEMA IF NOT EXISTS "accelerator";
SET search_path TO "accelerator";

-- ============================================================
-- Teams & Users (seeded with defaults, no app auth code yet)
-- ============================================================

CREATE TABLE IF NOT EXISTS teams (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_team_id ON users (team_id);

-- Seed defaults
INSERT INTO teams (id, name) VALUES
    ('00000000-0000-0000-0000-000000000001', 'Default')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, team_id, name, email, role) VALUES
    ('00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'System', 'system@localhost', 'admin')
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- Datasets
-- ============================================================

CREATE TABLE IF NOT EXISTS datasets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id             UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
                            REFERENCES teams(id) ON DELETE CASCADE,
    owner_id            UUID REFERENCES users(id) ON DELETE SET NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    current_version_id  UUID,  -- FK added after dataset_versions
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_datasets_team_id ON datasets (team_id);
CREATE INDEX IF NOT EXISTS ix_datasets_owner_id ON datasets (owner_id);

-- ============================================================
-- Dataset Versions
-- ============================================================

CREATE TABLE IF NOT EXISTS dataset_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id      UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL DEFAULT 1,
    path            TEXT,           -- /tmp/... local path, or s3://bucket/key
    storage_type    TEXT NOT NULL DEFAULT 'temp'
                        CHECK (storage_type IN ('s3', 'temp', 'local')),
    status          TEXT NOT NULL DEFAULT 'uploading'
                        CHECK (status IN ('uploading', 'ready', 'failed')),
    source          JSONB,  -- provenance: {"type": "upload"}, {"type": "workflow", ...}
    size_bytes      BIGINT,
    row_count       BIGINT,
    checksum        TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_dataset_versions_dataset_id ON dataset_versions (dataset_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_versions_dataset_version
    ON dataset_versions (dataset_id, version_number);

-- Deferred FK: datasets.current_version_id -> dataset_versions
ALTER TABLE datasets
    ADD CONSTRAINT fk_datasets_current_version
    FOREIGN KEY (current_version_id) REFERENCES dataset_versions(id)
    ON DELETE SET NULL;

-- ============================================================
-- Dataset Version Tags
-- ============================================================

CREATE TABLE IF NOT EXISTS dataset_version_tags (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id  UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    version_id  UUID NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    tag_name    TEXT NOT NULL,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_version_tags_dataset_tag
    ON dataset_version_tags (dataset_id, tag_name);
CREATE INDEX IF NOT EXISTS ix_dataset_version_tags_version_id
    ON dataset_version_tags (version_id);

-- Multi-sheet support (Excel):
--   No separate sheets table. Each sheet is stored as {path}_{sheetIndex}.parquet.
--   Sheet names stored in dataset_versions.source JSONB, e.g. {"sheets": ["Revenue", "Expenses"]}.
--   Column metadata lives in the parquet files themselves — no need to duplicate in the DB.
--   If we later need fast sheet-level queries (column search, UI listing without file I/O),
--   add a dataset_sheets table then.

-- ============================================================
-- Jobs (background tasks: imports, profiling, etc.)
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001'
                        REFERENCES teams(id) ON DELETE CASCADE,
    job_type        TEXT NOT NULL CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform')),
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    dataset_id      UUID REFERENCES datasets(id) ON DELETE SET NULL,
    dataset_version_id UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    parameters      JSONB,          -- input config for the job
    result          JSONB,          -- output summary on completion
    error           TEXT,           -- error message on failure
    progress        INTEGER DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status) WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS ix_jobs_dataset_id ON jobs (dataset_id);
CREATE INDEX IF NOT EXISTS ix_jobs_team_id ON jobs (team_id);

-- migrate:down
DROP SCHEMA IF EXISTS "accelerator" CASCADE;
