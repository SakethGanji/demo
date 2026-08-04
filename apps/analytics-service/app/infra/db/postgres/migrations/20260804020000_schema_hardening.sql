-- 20260804020000_schema_hardening
-- Post-review tightening of the Phase 1 schema:
--   * version summaries made precise (row_count = TOTAL across sheets;
--     sheet_count; source_checksum of the raw upload; manifest_checksum over
--     ordered per-sheet checksums)
--   * datasets.current_version_id guaranteed to reference a version of the
--     SAME dataset (composite FK)
--   * sheet uniqueness on (version, sheet_index) and at most one default sheet
--   * schema metadata provenance (extractor version, backfill timestamp)
--   * tag names normalized lowercase, enforced by CHECK
--   * tag history correlated to requests (request_id)

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- Version summaries
-- ============================================================
-- row_count semantics change: it is now the TOTAL rows across all sheets of
-- the version (previously it was the canonical parquet's count, i.e. the
-- FIRST sheet only for multi-sheet workbooks — misleading).
-- checksum stays: sha256 of the canonical parquet (meaningful for
-- single-table versions). New columns give the precise identities:
--   source_checksum    sha256 of the exact uploaded bytes (reproducibility)
--   manifest_checksum  sha256 over the ordered (sheet_key, sheet_index,
--                      checksum) list — the version's canonical content id

ALTER TABLE dataset_versions ADD COLUMN IF NOT EXISTS sheet_count INTEGER;
ALTER TABLE dataset_versions ADD COLUMN IF NOT EXISTS source_checksum TEXT;
ALTER TABLE dataset_versions ADD COLUMN IF NOT EXISTS manifest_checksum TEXT;

UPDATE dataset_versions dv
SET sheet_count = s.cnt
FROM (
    SELECT dataset_version_id, COUNT(*) AS cnt
    FROM dataset_version_sheets GROUP BY dataset_version_id
) s
WHERE s.dataset_version_id = dv.id;

-- Fix historical multi-sheet row counts (only where every sheet has a count).
UPDATE dataset_versions dv
SET row_count = s.total
FROM (
    SELECT dataset_version_id, SUM(row_count) AS total
    FROM dataset_version_sheets
    GROUP BY dataset_version_id
    HAVING COUNT(*) = COUNT(row_count)
) s
WHERE s.dataset_version_id = dv.id;

-- ============================================================
-- current_version_id must belong to the same dataset
-- ============================================================

ALTER TABLE dataset_versions
    ADD CONSTRAINT uq_dataset_versions_id_dataset UNIQUE (id, dataset_id);

ALTER TABLE datasets DROP CONSTRAINT IF EXISTS fk_datasets_current_version;
ALTER TABLE datasets
    ADD CONSTRAINT fk_datasets_current_version
    FOREIGN KEY (current_version_id, id) REFERENCES dataset_versions (id, dataset_id)
    ON DELETE SET NULL (current_version_id);

-- ============================================================
-- Sheet hardening
-- ============================================================

ALTER TABLE dataset_version_sheets ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE dataset_version_sheets ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
ALTER TABLE dataset_version_sheets ADD COLUMN IF NOT EXISTS schema_extractor_version TEXT;
ALTER TABLE dataset_version_sheets ADD COLUMN IF NOT EXISTS schema_backfilled_at TIMESTAMPTZ;

-- At most one default sheet per version (collapse any duplicates first,
-- keeping the lowest sheet_index).
UPDATE dataset_version_sheets s SET is_default = false
WHERE is_default AND EXISTS (
    SELECT 1 FROM dataset_version_sheets s2
    WHERE s2.dataset_version_id = s.dataset_version_id
      AND s2.is_default AND s2.sheet_index < s.sheet_index
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_version_sheets_one_default
    ON dataset_version_sheets (dataset_version_id) WHERE is_default;

CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_version_sheets_version_index
    ON dataset_version_sheets (dataset_version_id, sheet_index);

-- ============================================================
-- Tag name normalization (lowercase, trimmed)
-- ============================================================
-- 'production' / 'Production' / 'PRODUCTION' must be one tag. Drop case/space
-- duplicates keeping the most recently updated, then lowercase everything and
-- enforce the shape at the DB level.

DELETE FROM dataset_version_tags t
USING dataset_version_tags t2
WHERE t.dataset_id = t2.dataset_id
  AND lower(btrim(t.tag_name)) = lower(btrim(t2.tag_name))
  AND t.id <> t2.id
  AND (t.updated_at < t2.updated_at
       OR (t.updated_at = t2.updated_at AND t.id < t2.id));

UPDATE dataset_version_tags SET tag_name = lower(btrim(tag_name))
WHERE tag_name <> lower(btrim(tag_name));

ALTER TABLE dataset_version_tags
    ADD CONSTRAINT ck_dataset_version_tags_normalized
    CHECK (tag_name = lower(btrim(tag_name)));

UPDATE dataset_tag_history SET tag_name = lower(btrim(tag_name))
WHERE tag_name <> lower(btrim(tag_name));

-- ============================================================
-- Tag history request correlation
-- ============================================================

ALTER TABLE dataset_tag_history ADD COLUMN IF NOT EXISTS request_id TEXT;

-- migrate:down

SET search_path TO "accelerator";

ALTER TABLE dataset_tag_history DROP COLUMN IF EXISTS request_id;
ALTER TABLE dataset_version_tags DROP CONSTRAINT IF EXISTS ck_dataset_version_tags_normalized;

DROP INDEX IF EXISTS idx_dataset_version_sheets_version_index;
DROP INDEX IF EXISTS idx_dataset_version_sheets_one_default;
ALTER TABLE dataset_version_sheets DROP COLUMN IF EXISTS schema_backfilled_at;
ALTER TABLE dataset_version_sheets DROP COLUMN IF EXISTS schema_extractor_version;
ALTER TABLE dataset_version_sheets DROP COLUMN IF EXISTS processed_at;
ALTER TABLE dataset_version_sheets DROP COLUMN IF EXISTS error_message;

ALTER TABLE datasets DROP CONSTRAINT IF EXISTS fk_datasets_current_version;
ALTER TABLE datasets
    ADD CONSTRAINT fk_datasets_current_version
    FOREIGN KEY (current_version_id) REFERENCES dataset_versions (id)
    ON DELETE SET NULL;
ALTER TABLE dataset_versions DROP CONSTRAINT IF EXISTS uq_dataset_versions_id_dataset;

-- row_count backfill is not reversible (old first-sheet-only values are gone).
ALTER TABLE dataset_versions DROP COLUMN IF EXISTS manifest_checksum;
ALTER TABLE dataset_versions DROP COLUMN IF EXISTS source_checksum;
ALTER TABLE dataset_versions DROP COLUMN IF EXISTS sheet_count;
