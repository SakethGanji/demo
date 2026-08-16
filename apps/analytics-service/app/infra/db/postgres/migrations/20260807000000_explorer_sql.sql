-- 20260807000000_explorer_sql
-- Wave 1 §6b: sandboxed raw-SQL escape hatch persists its results to the
-- samples area — widen the artifact_type CHECK with 'query_output' so those
-- files carry ownership rows (/samples authorization depends on them).

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM artifacts WHERE artifact_type = 'query_output';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export'));
