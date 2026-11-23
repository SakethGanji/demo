-- 20261026130000_data_table_migrations
-- One row per schema migration applied to a data table (additive ops only: add_column, add_enum_values, set_unique, ...).
-- UNIQUE (table_id, op_hash) makes re-applying the same ops a recorded no-op. Rows follow the table (ON DELETE CASCADE).
-- Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS data_table_migrations (
  id           TEXT PRIMARY KEY,                       -- 'dtm_' + 12 hex
  project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  table_id     TEXT NOT NULL REFERENCES data_tables(id) ON DELETE CASCADE,
  plan_version INTEGER,                                -- the approved plan version that asked for it, if any
  ops          JSONB NOT NULL,
  op_hash      TEXT NOT NULL,
  applied_by   TEXT,
  applied_at   TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (table_id, op_hash)
);
CREATE INDEX IF NOT EXISTS ix_data_table_migrations_project ON data_table_migrations (project_id, applied_at);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS data_table_migrations;
