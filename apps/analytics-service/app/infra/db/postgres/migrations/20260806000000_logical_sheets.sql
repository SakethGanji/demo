-- 20260806000000_logical_sheets
-- ROADMAP §1: logical sheet identity.
--   dataset_sheets holds one row per logical sheet of a dataset; per-version
--   sheet rows, sheet metadata, and quality rules reference it so a confirmed
--   rename re-points identity instead of silently detaching keyed state.
--   Backfill encodes today's implicit identity: same sheet_key = same sheet.

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- 1. Logical sheet registry
-- ============================================================

CREATE TABLE IF NOT EXISTS dataset_sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    -- Latest known name-derived key / display name; history stays on the
    -- per-version rows. Partial unique: retired sheets free their key.
    current_sheet_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    first_seen_version_id UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_dataset_sheets_live_key
    ON dataset_sheets (dataset_id, current_sheet_key) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_dataset_sheets_dataset ON dataset_sheets (dataset_id);

-- ============================================================
-- 2. References from version sheets + keyed state
-- ============================================================

ALTER TABLE dataset_version_sheets
    ADD COLUMN IF NOT EXISTS logical_sheet_id UUID REFERENCES dataset_sheets(id);
ALTER TABLE dataset_sheet_metadata
    ADD COLUMN IF NOT EXISTS logical_sheet_id UUID REFERENCES dataset_sheets(id) ON DELETE SET NULL;
ALTER TABLE quality_rules
    ADD COLUMN IF NOT EXISTS logical_sheet_id UUID REFERENCES dataset_sheets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_dvs_logical_sheet ON dataset_version_sheets (logical_sheet_id);

-- ============================================================
-- 3. Backfill: one logical sheet per (dataset, sheet_key)
-- ============================================================

WITH first_seen AS (
    SELECT DISTINCT ON (dv.dataset_id, s.sheet_key)
           dv.dataset_id, s.sheet_key, s.dataset_version_id AS first_vid
    FROM dataset_version_sheets s
    JOIN dataset_versions dv ON dv.id = s.dataset_version_id
    ORDER BY dv.dataset_id, s.sheet_key, dv.version_number ASC
),
latest AS (
    SELECT DISTINCT ON (dv.dataset_id, s.sheet_key)
           dv.dataset_id, s.sheet_key, s.sheet_name
    FROM dataset_version_sheets s
    JOIN dataset_versions dv ON dv.id = s.dataset_version_id
    ORDER BY dv.dataset_id, s.sheet_key, dv.version_number DESC
)
INSERT INTO dataset_sheets (dataset_id, current_sheet_key, display_name, first_seen_version_id)
SELECT f.dataset_id, f.sheet_key, l.sheet_name, f.first_vid
FROM first_seen f
JOIN latest l ON l.dataset_id = f.dataset_id AND l.sheet_key = f.sheet_key
ON CONFLICT (dataset_id, current_sheet_key) WHERE retired_at IS NULL DO NOTHING;

UPDATE dataset_version_sheets s
SET logical_sheet_id = ls.id
FROM dataset_versions dv, dataset_sheets ls
WHERE dv.id = s.dataset_version_id
  AND ls.dataset_id = dv.dataset_id
  AND ls.current_sheet_key = s.sheet_key
  AND s.logical_sheet_id IS NULL;

ALTER TABLE dataset_version_sheets ALTER COLUMN logical_sheet_id SET NOT NULL;

UPDATE dataset_sheet_metadata m
SET logical_sheet_id = ls.id
FROM dataset_sheets ls
WHERE ls.dataset_id = m.dataset_id
  AND ls.current_sheet_key = m.sheet_key
  AND ls.retired_at IS NULL
  AND m.logical_sheet_id IS NULL;

UPDATE quality_rules r
SET logical_sheet_id = ls.id
FROM dataset_sheets ls
WHERE ls.dataset_id = r.dataset_id
  AND ls.current_sheet_key = r.sheet_selector
  AND ls.retired_at IS NULL
  AND r.logical_sheet_id IS NULL;

-- migrate:down

SET search_path TO "accelerator";

ALTER TABLE quality_rules DROP COLUMN IF EXISTS logical_sheet_id;
ALTER TABLE dataset_sheet_metadata DROP COLUMN IF EXISTS logical_sheet_id;
DROP INDEX IF EXISTS ix_dvs_logical_sheet;
ALTER TABLE dataset_version_sheets DROP COLUMN IF EXISTS logical_sheet_id;
DROP INDEX IF EXISTS ix_dataset_sheets_dataset;
DROP INDEX IF EXISTS ux_dataset_sheets_live_key;
DROP TABLE IF EXISTS dataset_sheets;
