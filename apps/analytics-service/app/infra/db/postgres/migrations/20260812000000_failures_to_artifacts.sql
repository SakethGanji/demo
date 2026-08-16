-- 20260812000000_failures_to_artifacts
-- Postgres is the CONTROL PLANE. It holds identity, config, schema, counts,
-- status, and pointers — never a copy of dataset cell values.
--
-- `validation_rule_results.sample_failures` violated that: it stored literal
-- rows lifted straight out of the dataset as JSONB. Those rows now go to the
-- object store as a `validation_failures` parquet artifact, and this table
-- keeps only the count and a pointer.
--
-- Two things fall out for free: failing rows become downloadable through the
-- existing /samples authorization path, and they become publishable as a
-- dataset in their own right.
--
-- Existing sample_failures are dropped rather than migrated — they are a
-- debugging convenience attached to historical runs, the counts and messages
-- (the part anyone queries) are retained, and re-running a validation
-- regenerates them in the new location.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE validation_rule_results DROP COLUMN IF EXISTS sample_failures;
ALTER TABLE validation_rule_results
    ADD COLUMN IF NOT EXISTS failure_artifact_id UUID
        REFERENCES artifacts(id) ON DELETE SET NULL;

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output', 'join_output',
                             'validation_failures'));

-- migrate:down
SET search_path TO "accelerator";

ALTER TABLE validation_rule_results DROP COLUMN IF EXISTS failure_artifact_id;
ALTER TABLE validation_rule_results ADD COLUMN IF NOT EXISTS sample_failures JSONB;

DELETE FROM artifacts WHERE artifact_type = 'validation_failures';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output', 'join_output'));
