-- 20260804000000_sheets
-- Sheets become first-class: one row per sheet per version, with schema
-- captured in Postgres (enables diffs + column search without file I/O).
-- Also: dataset_tag_history for explicit promote/rollback with reason+actor.

-- migrate:up

SET search_path TO "accelerator";

-- ============================================================
-- Dataset Version Sheets
-- ============================================================
-- One row per sheet per version. Non-Excel versions (CSV/parquet/inline JSON)
-- get a single synthetic sheet named 'data' so every ready version has at
-- least one sheet row and diffs work uniformly.
--
--   sheet_key    normalized, stable identifier — the join key for
--                cross-version diffs (lowercased, non-alnum collapsed to _)
--   storage_key  relative storage key of the sheet parquet;
--                NULL = the version's canonical parquet (dataset_versions.path)
--   schema_json  {"columns": [{"name", "original_name", "normalized_name",
--                 "dtype", "nullable", "position"}]}
--   schema_fingerprint  sha256 over the normalized schema (normalized_name,
--                 dtype, nullable, position) — equal fingerprint ⇒ same shape

CREATE TABLE IF NOT EXISTS dataset_version_sheets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id  UUID NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
    sheet_key           TEXT NOT NULL,
    sheet_name          TEXT NOT NULL,
    sheet_index         INTEGER NOT NULL DEFAULT 0,
    visibility          TEXT NOT NULL DEFAULT 'visible'
                            CHECK (visibility IN ('visible', 'hidden', 'very_hidden')),
    status              TEXT NOT NULL DEFAULT 'ready'
                            CHECK (status IN ('ready', 'failed')),
    is_default          BOOLEAN NOT NULL DEFAULT false,
    storage_key         TEXT,
    row_count           BIGINT,
    column_count        INTEGER,
    size_bytes          BIGINT,
    checksum            TEXT,
    schema_json         JSONB,
    schema_fingerprint  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dataset_version_sheets_version_key
    ON dataset_version_sheets (dataset_version_id, sheet_key);
CREATE INDEX IF NOT EXISTS ix_dataset_version_sheets_version
    ON dataset_version_sheets (dataset_version_id);
CREATE INDEX IF NOT EXISTS ix_dataset_version_sheets_fingerprint
    ON dataset_version_sheets (schema_fingerprint);

-- --- Backfill: multi-sheet Excel versions (sheets recorded in source JSONB) --
-- schema_json/checksum/size are filled lazily by the app on first read
-- (parquet inspection needs DuckDB, not SQL).
INSERT INTO dataset_version_sheets
    (dataset_version_id, sheet_key, sheet_name, sheet_index, is_default,
     storage_key, row_count, column_count, status)
SELECT dv.id,
       COALESCE(NULLIF(btrim(regexp_replace(lower(s.value->>'name'), '[^a-z0-9]+', '_', 'g'), '_'), ''), 'sheet'),
       s.value->>'name',
       (s.ordinality - 1)::integer,
       COALESCE((s.value->>'is_default')::boolean, s.ordinality = 1),
       s.value->>'storage_key',
       (s.value->>'row_count')::bigint,
       (s.value->>'column_count')::integer,
       'ready'
FROM dataset_versions dv,
     jsonb_array_elements(dv.source->'sheets') WITH ORDINALITY AS s(value, ordinality)
WHERE jsonb_typeof(dv.source->'sheets') = 'array'
ON CONFLICT (dataset_version_id, sheet_key) DO NOTHING;

-- --- Backfill: everything else that is ready gets one synthetic sheet --------
-- (CSV/parquet/inline uploads, and legacy single-sheet Excel whose real sheet
-- name was never persisted.)
INSERT INTO dataset_version_sheets
    (dataset_version_id, sheet_key, sheet_name, sheet_index, is_default,
     storage_key, row_count, status)
SELECT dv.id, 'data', 'data', 0, true, NULL, dv.row_count, 'ready'
FROM dataset_versions dv
WHERE dv.status = 'ready' AND dv.path IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM dataset_version_sheets s WHERE s.dataset_version_id = dv.id
  );

-- ============================================================
-- Dataset Tag History
-- ============================================================
-- Every tag mutation (set/promote/rollback/delete) appends a row with the
-- transition, the actor, and an optional reason. Version numbers are
-- denormalized so history stays readable after versions are pruned.

CREATE TABLE IF NOT EXISTS dataset_tag_history (
    id                   BIGSERIAL PRIMARY KEY,
    dataset_id           UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    tag_name             TEXT NOT NULL,
    action               TEXT NOT NULL
                             CHECK (action IN ('set', 'promote', 'rollback', 'delete')),
    from_version_id      UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    from_version_number  INTEGER,
    to_version_id        UUID REFERENCES dataset_versions(id) ON DELETE SET NULL,
    to_version_number    INTEGER,
    reason               TEXT,
    actor_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_email          TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dataset_tag_history_dataset_tag
    ON dataset_tag_history (dataset_id, tag_name, created_at DESC);

-- --- Backfill: existing tags get an initial 'set' entry ----------------------
INSERT INTO dataset_tag_history
    (dataset_id, tag_name, action, to_version_id, to_version_number,
     actor_user_id, created_at)
SELECT t.dataset_id, t.tag_name, 'set', t.version_id, dv.version_number,
       t.created_by, t.updated_at
FROM dataset_version_tags t
JOIN dataset_versions dv ON dv.id = t.version_id;

-- migrate:down

SET search_path TO "accelerator";

DROP TABLE IF EXISTS dataset_tag_history;
DROP TABLE IF EXISTS dataset_version_sheets;
