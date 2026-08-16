-- 20260809010000_transformations
-- Wave 4 §19–§21: saved transformation pipelines (a definition + run-history
-- pair, mirroring analytics_definitions/analytics_runs) plus the two CHECK
-- widenings a new output kind needs. Definitions key on logical_sheet_id — the
-- standing rule since Wave 0 — so confirm-rename never has to rewrite them.
-- `transform` is already in the jobs.job_type CHECK (baseline), so no widening
-- there.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS transformation_definitions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id        UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    logical_sheet_id  UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    version_selector  JSONB NOT NULL DEFAULT '{"mode": "current"}'::jsonb,
    steps             JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, name)
);
CREATE INDEX IF NOT EXISTS ix_transformation_definitions_sheet
    ON transformation_definitions (logical_sheet_id);

CREATE TABLE IF NOT EXISTS transformation_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    definition_id       UUID NOT NULL REFERENCES transformation_definitions(id) ON DELETE CASCADE,
    dataset_version_id  UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    job_id              UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed')),
    mode                TEXT NOT NULL DEFAULT 'full'
                            CHECK (mode IN ('full', 'preview')),
    result_summary      JSONB,
    artifact_id         UUID REFERENCES artifacts(id) ON DELETE SET NULL,
    -- §21: the output's own profile and its drift vs the pinned source sheet.
    output_profile      JSONB,
    source_drift        JSONB,
    triggered_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS ix_transformation_runs_definition
    ON transformation_runs (definition_id);
CREATE INDEX IF NOT EXISTS ix_transformation_runs_version
    ON transformation_runs (dataset_version_id);

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output'));

ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from', 'pivoted_from',
                        'transformed_from'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM dataset_lineage WHERE relation = 'transformed_from';
ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from', 'pivoted_from'));

DROP TABLE IF EXISTS transformation_runs;

DELETE FROM artifacts WHERE artifact_type = 'transform_output';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output'));

DROP TABLE IF EXISTS transformation_definitions;
