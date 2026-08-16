-- 20260810000000_relationships
-- Wave 5 §22: discovered and declared relationships between sheets.
--
-- Both endpoints carry their own dataset id. Within a workbook the two are the
-- same, but §23's join builder joins across datasets, so a single-dataset
-- table would have had to be widened immediately. `dataset_id` is the OWNING
-- side and is what scopes RBAC.
--
-- Endpoints key on logical_sheet_id (the standing rule since Wave 0), so a
-- confirmed sheet rename needs no rewriting here. Column names are stored
-- NORMALIZED and mapped to physical names at probe/join time.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS dataset_relationships (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id             UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    from_logical_sheet_id  UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    from_column            TEXT NOT NULL,
    to_dataset_id          UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    to_logical_sheet_id    UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    to_column              TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'suggested'
                               CHECK (status IN ('suggested', 'confirmed', 'rejected')),
    method                 TEXT NOT NULL
                               CHECK (method IN ('fk_rule', 'statistical', 'manual')),
    evidence               JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence             DOUBLE PRECISION,
    algorithm_version      INT NOT NULL DEFAULT 1,
    created_by             UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_by            UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One edge per directed (sheet, column) pair — re-running discovery
    -- updates evidence in place rather than accumulating duplicates.
    UNIQUE (from_logical_sheet_id, from_column, to_logical_sheet_id, to_column)
);
CREATE INDEX IF NOT EXISTS ix_dataset_relationships_dataset
    ON dataset_relationships (dataset_id);
CREATE INDEX IF NOT EXISTS ix_dataset_relationships_to_dataset
    ON dataset_relationships (to_dataset_id);
CREATE INDEX IF NOT EXISTS ix_dataset_relationships_status
    ON dataset_relationships (dataset_id, status);

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics', 'relationship_discovery'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM jobs WHERE job_type = 'relationship_discovery';
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics'));

DROP TABLE IF EXISTS dataset_relationships;
