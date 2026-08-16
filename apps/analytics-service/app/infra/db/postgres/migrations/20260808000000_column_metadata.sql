-- 20260808000000_column_metadata
-- Wave 3 §15: column-level data dictionary. Keyed on the logical sheet
-- identity (not the name-derived sheet_key), so entries survive confirmed
-- renames with no rewriting. column_name is the NORMALIZED name.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS dataset_column_metadata (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id        UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    logical_sheet_id  UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE,
    column_name       TEXT NOT NULL,
    business_name     TEXT,
    description       TEXT,
    semantic_type     TEXT,
    unit              TEXT,
    sensitivity       TEXT,
    allowed_values    JSONB,
    updated_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (logical_sheet_id, column_name)
);
CREATE INDEX IF NOT EXISTS ix_dataset_column_metadata_dataset
    ON dataset_column_metadata (dataset_id);

-- migrate:down
SET search_path TO "accelerator";

DROP TABLE IF EXISTS dataset_column_metadata;
