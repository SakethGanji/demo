-- 20260804040000_reuse
-- Phase 3 (reuse): generalized artifacts, saved analytics definitions + runs,
-- publish lineage.

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- Artifacts — generalized references to stored outputs
-- ============================================================

CREATE TABLE IF NOT EXISTS artifacts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_key    TEXT NOT NULL,
    artifact_type  TEXT NOT NULL
                       CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                                                'published_source', 'export')),
    format         TEXT,
    media_type     TEXT,
    size_bytes     BIGINT,
    checksum       TEXT,
    created_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_artifacts_type ON artifacts (artifact_type);

-- ============================================================
-- Saved analytics — reusable definitions + durable run history
-- ============================================================

CREATE TABLE IF NOT EXISTS analytics_definitions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id       UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    description      TEXT,
    kind             TEXT NOT NULL CHECK (kind IN ('sample', 'aggregate', 'profile')),
    version_selector JSONB NOT NULL DEFAULT '{"mode": "current"}'::jsonb,
    sheet            TEXT,     -- sheet name/key; NULL for single-table datasets
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_definitions_dataset_name
    ON analytics_definitions (dataset_id, name);

CREATE TABLE IF NOT EXISTS analytics_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    definition_id       UUID NOT NULL REFERENCES analytics_definitions(id) ON DELETE CASCADE,
    dataset_version_id  UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    job_id              UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed')),
    result_summary      JSONB,
    artifact_id         UUID REFERENCES artifacts(id) ON DELETE SET NULL,
    triggered_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS ix_analytics_runs_definition
    ON analytics_runs (definition_id, started_at DESC);

-- ============================================================
-- Lineage — version- and sheet-level parents
-- ============================================================

CREATE TABLE IF NOT EXISTS dataset_lineage (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id          UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version_id  UUID NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    parent_dataset_id   UUID REFERENCES datasets(id) ON DELETE SET NULL,
    parent_version_id   UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    parent_sheet_key    TEXT,
    relation            TEXT NOT NULL
                            CHECK (relation IN ('published_from', 'sheet_replaced_from')),
    -- Denormalized labels so lineage stays readable after parent pruning.
    parent_dataset_name TEXT,
    parent_version_number INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dataset_lineage_version ON dataset_lineage (dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_dataset_lineage_parent ON dataset_lineage (parent_dataset_id);

-- migrate:down

SET search_path TO "accelerator";

DROP TABLE IF EXISTS dataset_lineage;
DROP TABLE IF EXISTS analytics_runs;
DROP TABLE IF EXISTS analytics_definitions;
DROP TABLE IF EXISTS artifacts;
