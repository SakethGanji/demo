-- Phase 5 (BACKEND-DESIGN §2.2 M3): API routes, per-project API keys, spec revisions.
CREATE TABLE IF NOT EXISTS api_routes (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id),
    name                TEXT NOT NULL,
    method              TEXT NOT NULL CHECK (method IN ('GET','POST','PUT','PATCH','DELETE')),
    path                TEXT NOT NULL,
    path_shape          TEXT NOT NULL,
    auth                TEXT NOT NULL DEFAULT 'none' CHECK (auth IN ('none','app_token','api_key')),
    description         TEXT,
    request_schema      JSONB,
    response_schema     JSONB,
    response_mode       TEXT NOT NULL DEFAULT 'auto' CHECK (response_mode IN ('auto','first_item','all_items')),
    validate_request    BOOLEAN NOT NULL DEFAULT false,
    handler_type        TEXT NOT NULL CHECK (handler_type IN ('workflow','python','stub')),
    handler_workflow_id TEXT REFERENCES workflows(id),
    handler_module_id   TEXT,
    handler_function    TEXT,
    source              TEXT NOT NULL DEFAULT 'ui' CHECK (source IN ('ui','code','app_stub')),
    timeout_s           INT NOT NULL DEFAULT 30,
    created_by          TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP NOT NULL DEFAULT now(),
    CHECK ((handler_type = 'workflow') = (handler_workflow_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_routes_shape ON api_routes (project_id, method, path_shape);
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_routes_name ON api_routes (project_id, name);
CREATE INDEX IF NOT EXISTS ix_api_routes_workflow ON api_routes (handler_workflow_id);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id),
    environment  TEXT NOT NULL,
    name         TEXT NOT NULL,
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    created_by   TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    last_used_at TIMESTAMP,
    revoked_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_api_keys_project ON api_keys (project_id);

CREATE TABLE IF NOT EXISTS api_spec_revisions (
    id          SERIAL PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    revision    INT NOT NULL,
    spec_hash   TEXT NOT NULL,
    spec        JSONB NOT NULL,
    changes     JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (project_id, revision)
);

-- migrate:down
DROP TABLE IF EXISTS api_spec_revisions;
DROP TABLE IF EXISTS api_keys;
DROP TABLE IF EXISTS api_routes;
