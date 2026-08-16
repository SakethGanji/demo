-- 20260805000000_ops_refinements
-- Post-review refinements (2026-08-05):
--   1. artifacts gain dataset/team ownership so dataset deletion can clean up
--      artifact rows (FK CASCADE) and their stored blobs (service-side).
--   2. audit_log gains duration_ms (request latency).
--   3. dataset_lineage relations become kind-specific: published sample runs
--      record sampled_from, aggregation runs aggregated_from.

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- 1. Artifact ownership
-- ============================================================

ALTER TABLE artifacts
    ADD COLUMN IF NOT EXISTS dataset_id UUID REFERENCES datasets(id) ON DELETE CASCADE;
ALTER TABLE artifacts
    ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_artifacts_dataset_id ON artifacts (dataset_id);

-- Backfill: every existing artifact was registered by an analytics run whose
-- definition names the dataset; team follows from the dataset.
UPDATE artifacts a
SET dataset_id = d.dataset_id
FROM analytics_runs r
JOIN analytics_definitions d ON d.id = r.definition_id
WHERE r.artifact_id = a.id AND a.dataset_id IS NULL;

UPDATE artifacts a
SET team_id = ds.team_id
FROM datasets ds
WHERE a.dataset_id = ds.id AND a.team_id IS NULL;

-- ============================================================
-- 2. Audit latency
-- ============================================================

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

-- ============================================================
-- 3. Kind-specific lineage relations
-- ============================================================

ALTER TABLE dataset_lineage DROP CONSTRAINT IF EXISTS dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from',
                        'sampled_from', 'aggregated_from'));

-- migrate:down

SET search_path TO "accelerator";

UPDATE dataset_lineage SET relation = 'published_from'
    WHERE relation IN ('sampled_from', 'aggregated_from');
ALTER TABLE dataset_lineage DROP CONSTRAINT IF EXISTS dataset_lineage_relation_check;
ALTER TABLE dataset_lineage ADD CONSTRAINT dataset_lineage_relation_check
    CHECK (relation IN ('published_from', 'sheet_replaced_from'));

ALTER TABLE audit_log DROP COLUMN IF EXISTS duration_ms;

DROP INDEX IF EXISTS ix_artifacts_dataset_id;
ALTER TABLE artifacts DROP COLUMN IF EXISTS team_id;
ALTER TABLE artifacts DROP COLUMN IF EXISTS dataset_id;
