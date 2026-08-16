-- 20260812010000_row_diff
-- Keyed row-level diff between two versions. The full cell-level result is
-- dataset content, so it goes to the object store as a `diff_output` artifact
-- (control-plane rule) — only counts come back inline.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output', 'join_output',
                             'validation_failures', 'diff_output'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM artifacts WHERE artifact_type = 'diff_output';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output', 'join_output',
                             'validation_failures'));
