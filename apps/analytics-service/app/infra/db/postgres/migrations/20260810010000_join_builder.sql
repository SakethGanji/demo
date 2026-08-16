-- 20260810010000_join_builder
-- Wave 5 §23: the guided join builder is a new analytics KIND, not a new
-- table — it rides analytics_definitions/analytics_runs, artifacts, and
-- dataset_lineage exactly as pivots do. That is three CHECK widenings
-- (pivot's migration is the template).
--
-- A join has TWO parents, so publishing writes two `joined_from` lineage rows
-- against the same new version; the relation itself needs no extra structure.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE analytics_definitions DROP CONSTRAINT analytics_definitions_kind_check;
ALTER TABLE analytics_definitions ADD CONSTRAINT analytics_definitions_kind_check
    CHECK (kind IN ('sample', 'aggregate', 'profile', 'pivot', 'join'));

ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output', 'join_output'));

ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from', 'pivoted_from',
                        'transformed_from', 'joined_from'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM dataset_lineage WHERE relation = 'joined_from';
ALTER TABLE dataset_lineage DROP CONSTRAINT dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from', 'pivoted_from',
                        'transformed_from'));

DELETE FROM artifacts WHERE artifact_type = 'join_output';
ALTER TABLE artifacts DROP CONSTRAINT artifacts_artifact_type_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_artifact_type_check
    CHECK (artifact_type IN ('sample_output', 'aggregation_output',
                             'published_source', 'export', 'query_output',
                             'pivot_output', 'transform_output'));

DELETE FROM analytics_definitions WHERE kind = 'join';
ALTER TABLE analytics_definitions DROP CONSTRAINT analytics_definitions_kind_check;
ALTER TABLE analytics_definitions ADD CONSTRAINT analytics_definitions_kind_check
    CHECK (kind IN ('sample', 'aggregate', 'profile', 'pivot'));
