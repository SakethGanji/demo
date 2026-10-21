-- 20261029120000_project_connections
-- External databases connected to a project. The connection URL is NOT stored here: it lives only as a project secret
-- (secret_key, one row per environment in variables). This row holds the non-secret facts (host, database, flags) and the
-- last introspected schema snapshot. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS project_connections (
  id              TEXT PRIMARY KEY,                       -- 'conn_' + uuid
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  key             TEXT NOT NULL,                          -- slug of name, unique per project
  name            TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'postgres',
  host            TEXT NOT NULL,
  port            INTEGER,
  database        TEXT NOT NULL DEFAULT '',
  readonly        BOOLEAN NOT NULL DEFAULT TRUE,
  secret_key      TEXT NOT NULL,                          -- name of the project secret holding the URL
  status          TEXT NOT NULL DEFAULT 'unknown',        -- ok | error | unknown
  error           TEXT,
  introspected_at TIMESTAMP,
  schema_snapshot JSONB,                                  -- {tables: [...]}
  created_at      TIMESTAMP NOT NULL DEFAULT now(),
  updated_at      TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (project_id, key)
);
CREATE INDEX IF NOT EXISTS ix_project_connections_project ON project_connections (project_id);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS project_connections;
