-- 20260807020000_pivot
-- Wave 2 §11: pivots are a new analytics kind riding the existing
-- definitions/runs/artifacts machinery — widen the three CHECKs.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE analytics_definitions DROP CONSTRAINT analytics_definitions_kind_check;
ALTER TABLE analytics_definitions ADD CONSTRAINT analytics_definitions_kind_check
    CHECK (kind IN ('sample', 'aggregate', 'profile', 'pivot'));

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output'));

ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from', 'pivoted_from'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM dataset_lineage WHERE relation = 'pivoted_from';
ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from'));

DELETE FROM artifacts WHERE artifact_type = 'pivot_output';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output'));

DELETE FROM analytics_definitions WHERE kind = 'pivot';
ALTER TABLE analytics_definitions DROP CONSTRAINT analytics_definitions_kind_check;
ALTER TABLE analytics_definitions ADD CONSTRAINT analytics_definitions_kind_check
    CHECK (kind IN ('sample', 'aggregate', 'profile'));
