-- 20260804050000_discovery
-- Phase 4 (discovery): rich dataset metadata, sheet-level grain/PK,
-- favorites. Column search needs no schema — Phase 1 put schemas in Postgres.

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- Rich dataset metadata
-- ============================================================

ALTER TABLE datasets ADD COLUMN IF NOT EXISTS domain TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS source_system TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS refresh_frequency TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS deprecated BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_datasets_domain ON datasets (domain);

-- ============================================================
-- Sheet-level semantic metadata (grain, primary key)
-- ============================================================
-- Dataset-scoped and keyed by sheet_key: semantic facts about a logical sheet
-- ("one row per customer") outlive individual immutable versions.

CREATE TABLE IF NOT EXISTS dataset_sheet_metadata (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id           UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    sheet_key            TEXT NOT NULL,
    grain                TEXT,
    primary_key_columns  JSONB,   -- ["customer_id"] — normalized column names
    description          TEXT,
    updated_by           UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_sheet_metadata_key
    ON dataset_sheet_metadata (dataset_id, sheet_key);

-- ============================================================
-- Favorites
-- ============================================================

CREATE TABLE IF NOT EXISTS dataset_favorites (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dataset_id  UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, dataset_id)
);

-- migrate:down

SET search_path TO "accelerator";

DROP TABLE IF EXISTS dataset_favorites;
DROP TABLE IF EXISTS dataset_sheet_metadata;
DROP INDEX IF EXISTS ix_datasets_domain;
ALTER TABLE datasets DROP COLUMN IF EXISTS metadata;
ALTER TABLE datasets DROP COLUMN IF EXISTS deprecation_reason;
ALTER TABLE datasets DROP COLUMN IF EXISTS deprecated;
ALTER TABLE datasets DROP COLUMN IF EXISTS refresh_frequency;
ALTER TABLE datasets DROP COLUMN IF EXISTS source_system;
ALTER TABLE datasets DROP COLUMN IF EXISTS domain;
