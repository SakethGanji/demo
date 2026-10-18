-- 20261017120000_api_specs
-- Stage 3A: the API catalog. One row per registered API (OpenAPI 3 spec, normalised to an operation list).
-- Additive only; safe for the running engine.

-- migrate:up
SET search_path TO "workflow-app";
CREATE TABLE IF NOT EXISTS api_specs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL DEFAULT '',
    spec        JSONB NOT NULL,
    source      TEXT NOT NULL DEFAULT 'paste',
    source_url  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_specs_project_id ON api_specs (project_id);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS api_specs;
