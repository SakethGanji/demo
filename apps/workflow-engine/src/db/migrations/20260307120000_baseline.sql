-- 20260307120000_baseline
-- For existing databases, insert this version into schema_migrations manually:
--   INSERT INTO schema_migrations (version, filename, checksum)
--   VALUES ('20260307120000', '20260307120000_baseline.sql', '<checksum>');

-- migrate:up

CREATE SCHEMA IF NOT EXISTS "workflow-app";
SET search_path TO "workflow-app";

-- Identity & access
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    display_name TEXT,
    avatar_url  TEXT,
    sso_provider TEXT,
    last_login_at TIMESTAMP,
    disabled    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- Seed default team (required by FK defaults below)
INSERT INTO teams (id, name, slug) VALUES ('default', 'Default', 'default')
    ON CONFLICT (id) DO NOTHING;

-- Projects: the unit of ownership (apps, workflows, routes, tables, secrets, releases)
CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    team_id             TEXT NOT NULL REFERENCES teams(id),
    name                TEXT NOT NULL,
    slug                TEXT NOT NULL,
    description         TEXT,
    environments        JSONB NOT NULL DEFAULT '[{"name":"dev","kind":"draft"},{"name":"prod","kind":"release"}]',
    default_environment TEXT NOT NULL DEFAULT 'dev',
    live_environment    TEXT NOT NULL DEFAULT 'prod',
    settings            JSONB NOT NULL DEFAULT '{}',
    created_by          TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_team_slug ON projects (team_id, lower(slug));

-- Seed default project (required by project_id defaults below)
INSERT INTO projects (id, team_id, name, slug, environments, default_environment, live_environment)
VALUES ('proj_default', 'default', 'Default project', 'default',
        '[{"name":"default","kind":"draft"}]', 'default', 'default')
    ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS team_members (
    id          SERIAL PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'viewer',
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_team_members_team_id ON team_members (team_id);
CREATE INDEX IF NOT EXISTS ix_team_members_user_id ON team_members (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_unique ON team_members (team_id, user_id);

-- Organization
CREATE TABLE IF NOT EXISTS folders (
    id              TEXT PRIMARY KEY,
    team_id         TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    parent_folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id),
    name            TEXT NOT NULL,
    created_by      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_folders_team_id ON folders (team_id);
CREATE INDEX IF NOT EXISTS ix_folders_project ON folders (project_id);
CREATE INDEX IF NOT EXISTS idx_folders_team_parent ON folders (team_id, parent_folder_id);

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tags_team_id ON tags (team_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_team_name ON tags (team_id, name);

-- Workflows
CREATE TABLE IF NOT EXISTS workflows (
    id                   TEXT PRIMARY KEY,
    team_id              TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    folder_id            TEXT REFERENCES folders(id) ON DELETE SET NULL,
    project_id           TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id),
    name                 TEXT NOT NULL,
    description          TEXT,
    active               BOOLEAN NOT NULL DEFAULT FALSE,
    draft_definition     JSONB,
    published_version_id INTEGER,
    settings             JSONB,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    updated_at           TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_workflows_project ON workflows (project_id);
CREATE INDEX IF NOT EXISTS ix_workflows_name ON workflows (name);
CREATE INDEX IF NOT EXISTS ix_workflows_active ON workflows (active);
CREATE INDEX IF NOT EXISTS ix_workflows_team_id ON workflows (team_id);
CREATE INDEX IF NOT EXISTS ix_workflows_folder_id ON workflows (folder_id);

CREATE TABLE IF NOT EXISTS workflow_tags (
    id          SERIAL PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    tag_id      TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_workflow_tags_workflow_id ON workflow_tags (workflow_id);
CREATE INDEX IF NOT EXISTS ix_workflow_tags_tag_id ON workflow_tags (tag_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_tags_pk ON workflow_tags (workflow_id, tag_id);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id              SERIAL PRIMARY KEY,
    workflow_id     TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    definition      JSONB NOT NULL,
    message         TEXT,
    created_by      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_workflow ON workflow_versions (workflow_id, version_number);

-- FK: workflows.published_version_id -> workflow_versions(id)
-- Added after workflow_versions exists to avoid circular dependency
ALTER TABLE workflows
    ADD CONSTRAINT fk_workflows_published_version
    FOREIGN KEY (published_version_id) REFERENCES workflow_versions(id) ON DELETE SET NULL;

-- Apps (visual UI builder — separate from workflows)
CREATE TABLE IF NOT EXISTS apps (
    id                   TEXT PRIMARY KEY,
    team_id              TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    folder_id            TEXT REFERENCES folders(id) ON DELETE SET NULL,
    project_id           TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id),
    name                 TEXT NOT NULL,
    description          TEXT,
    slug                 TEXT UNIQUE,
    active               BOOLEAN NOT NULL DEFAULT FALSE,
    draft_definition     JSONB,
    draft_source_code    TEXT,
    current_version_id   INTEGER,
    published_version_id INTEGER,
    settings             JSONB,
    access               TEXT NOT NULL DEFAULT 'private',
    access_password_hash TEXT,
    published_at         TIMESTAMP,
    embed_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    workflow_ids         JSONB NOT NULL DEFAULT '[]'::jsonb,
    api_execution_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    updated_at           TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_apps_team_id ON apps (team_id);
CREATE INDEX IF NOT EXISTS ix_apps_project ON apps (project_id);
CREATE INDEX IF NOT EXISTS ix_apps_folder_id ON apps (folder_id);
CREATE INDEX IF NOT EXISTS ix_apps_slug ON apps (slug) WHERE slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS app_versions (
    id                SERIAL PRIMARY KEY,
    app_id            TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    version_number    INTEGER NOT NULL,
    parent_version_id INTEGER REFERENCES app_versions(id) ON DELETE SET NULL,
    definition        JSONB NOT NULL,
    source_code       TEXT NOT NULL,
    trigger           TEXT NOT NULL DEFAULT 'ai',
    label             TEXT,
    prompt            TEXT,
    message           TEXT,
    created_by        TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    -- Production bundle artifact (populated on publish). Pluggable storage:
    -- Postgres TEXT today, swap to S3 later via BundleStorageBackend.
    bundle_js         TEXT,
    bundle_css        TEXT,
    bundle_hash       TEXT,
    bundled_at        TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_versions_app ON app_versions (app_id, version_number);
CREATE INDEX IF NOT EXISTS idx_app_versions_parent ON app_versions (parent_version_id);
CREATE INDEX IF NOT EXISTS idx_app_versions_bundle_hash
    ON app_versions (bundle_hash) WHERE bundle_hash IS NOT NULL;

ALTER TABLE apps
    ADD CONSTRAINT fk_apps_published_version
    FOREIGN KEY (published_version_id) REFERENCES app_versions(id) ON DELETE SET NULL;

ALTER TABLE apps
    ADD CONSTRAINT fk_apps_current_version
    FOREIGN KEY (current_version_id) REFERENCES app_versions(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS app_files (
    id          SERIAL PRIMARY KEY,
    version_id  INTEGER NOT NULL REFERENCES app_versions(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    content     TEXT NOT NULL,
    file_type   TEXT NOT NULL DEFAULT 'tsx',
    parsed_index JSONB,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_files_version ON app_files (version_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_files_version_path ON app_files (version_id, path);

-- Executions
CREATE TABLE IF NOT EXISTS executions (
    id                    TEXT PRIMARY KEY,
    workflow_id           TEXT NOT NULL,
    workflow_version_id   INTEGER,
    workflow_name         TEXT NOT NULL,
    team_id               TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    project_id            TEXT,
    environment           TEXT,
    status                TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled','waiting')),
    mode                  TEXT NOT NULL CONSTRAINT executions_mode_check
                          CHECK (mode IN ('manual','webhook','cron','interval','retry','sub_workflow','app','api','sandbox')),
    total_nodes           INTEGER,
    completed_nodes       INTEGER NOT NULL DEFAULT 0,
    parent_execution_id   TEXT,
    parent_node_name      TEXT,
    depth                 INTEGER NOT NULL DEFAULT 0,
    retry_of_execution_id TEXT,
    cancelled_at          TIMESTAMP,
    start_time            TIMESTAMP NOT NULL DEFAULT now(),
    end_time              TIMESTAMP,
    resume_at             TIMESTAMP,
    error_count           INTEGER NOT NULL DEFAULT 0,
    metadata              JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_executions_workflow_id ON executions (workflow_id);
CREATE INDEX IF NOT EXISTS ix_executions_status ON executions (status);
CREATE INDEX IF NOT EXISTS ix_executions_team_id ON executions (team_id);
CREATE INDEX IF NOT EXISTS ix_executions_start_time ON executions (start_time);
CREATE INDEX IF NOT EXISTS ix_executions_parent ON executions (parent_execution_id);
CREATE INDEX IF NOT EXISTS ix_executions_resume_at ON executions (resume_at) WHERE status = 'waiting';
CREATE INDEX IF NOT EXISTS ix_executions_team_start ON executions (team_id, start_time DESC);
CREATE INDEX IF NOT EXISTS ix_executions_project_start ON executions (project_id, start_time DESC);

CREATE TABLE IF NOT EXISTS node_outputs (
    id           SERIAL PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    node_name    TEXT NOT NULL,
    output       JSONB NOT NULL,
    metrics      JSONB,
    status       TEXT NOT NULL,
    error        TEXT,
    run_index    INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_node_outputs_exec ON node_outputs (execution_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_outputs_unique ON node_outputs (execution_id, node_name, run_index);

-- Triggers
CREATE TABLE IF NOT EXISTS active_triggers (
    id                  SERIAL PRIMARY KEY,
    workflow_id         TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    workflow_version_id INTEGER,
    team_id             TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    node_name           TEXT NOT NULL,
    type                TEXT NOT NULL,
    webhook_path        TEXT,
    config              JSONB NOT NULL DEFAULT '{}',
    state               JSONB NOT NULL DEFAULT '{}',
    next_run_at         TIMESTAMP,
    last_run_at         TIMESTAMP,
    error_count         INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trigger_workflow ON active_triggers (workflow_id);
CREATE INDEX IF NOT EXISTS idx_trigger_type_enabled ON active_triggers (type, enabled);
CREATE INDEX IF NOT EXISTS idx_trigger_next_run ON active_triggers (next_run_at) WHERE enabled = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_trigger_webhook_path ON active_triggers (webhook_path) WHERE webhook_path IS NOT NULL;

-- Credentials
CREATE TABLE IF NOT EXISTS credentials (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_credentials_team_id ON credentials (team_id);

CREATE TABLE IF NOT EXISTS shared_credentials (
    id              SERIAL PRIMARY KEY,
    credential_id   TEXT NOT NULL REFERENCES credentials(id) ON DELETE CASCADE,
    share_type      TEXT NOT NULL,
    share_target_id TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    created_by      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_shared_creds_cred ON shared_credentials (credential_id);
CREATE INDEX IF NOT EXISTS idx_shared_creds_target ON shared_credentials (share_type, share_target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_creds_unique ON shared_credentials (credential_id, share_type, share_target_id);

-- Variables (scoped per environment: 'default', 'dev', 'uat', 'prod', or any string)
-- Secret-typed values are Fernet-encrypted at the app layer; same key as credentials.
CREATE TABLE IF NOT EXISTS variables (
    id          SERIAL PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    project_id  TEXT REFERENCES projects(id),          -- NULL = team-shared; project value wins
    environment TEXT NOT NULL DEFAULT 'default',
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'string',
    description TEXT,
    allowed_hosts JSONB,                                -- broker host allowlist for this secret
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_variables_team_id ON variables (team_id);
CREATE INDEX IF NOT EXISTS ix_variables_team_env ON variables (team_id, environment);
CREATE UNIQUE INDEX IF NOT EXISTS idx_variables_team_scope ON variables (team_id, environment, key) WHERE project_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_variables_project_scope ON variables (project_id, environment, key) WHERE project_id IS NOT NULL;

-- Data tables
CREATE TABLE IF NOT EXISTS data_tables (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    project_id  TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id),
    name        TEXT NOT NULL,
    description TEXT,
    columns     JSONB NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_data_tables_team_id ON data_tables (team_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_tables_project_name ON data_tables (project_id, name);

CREATE TABLE IF NOT EXISTS data_table_rows (
    id          BIGSERIAL PRIMARY KEY,
    table_id    TEXT NOT NULL REFERENCES data_tables(id) ON DELETE CASCADE,
    data        JSONB NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_data_table_rows_table ON data_table_rows (table_id);
CREATE INDEX IF NOT EXISTS idx_data_table_rows_gin ON data_table_rows USING GIN (data jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_data_table_rows_table_created ON data_table_rows (table_id, id DESC);

-- API tester captured executions — used as LLM context for the app builder
-- and replayed by published apps via the public-app proxy.
CREATE TABLE IF NOT EXISTS api_test_executions (
    id                     TEXT PRIMARY KEY,
    team_id                TEXT NOT NULL DEFAULT 'default' REFERENCES teams(id) ON DELETE CASCADE,
    name                   TEXT,
    method                 TEXT NOT NULL,
    url                    TEXT NOT NULL,
    request_headers        JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_body_text      TEXT,
    -- Multipart upload metadata (no bytes): list of
    -- {field, filename, size, content_type}. Null when the request is not
    -- multipart. Replays require the user to re-attach files.
    request_files          JSONB,
    response_status        INTEGER,
    response_headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_content_type  TEXT,
    response_size          INTEGER NOT NULL DEFAULT 0,
    response_body_b64      TEXT,
    response_truncated     BOOLEAN NOT NULL DEFAULT FALSE,
    -- Pre-computed compact view of the response (schema/template/snippet)
    -- for the LLM context renderer — avoids re-decoding b64 each chat turn.
    -- See services.schema_inference.summarize_response for the shape.
    response_summary       JSONB,
    latency_ms             DOUBLE PRECISION,
    error                  TEXT,
    created_at             TIMESTAMP NOT NULL DEFAULT now(),
    updated_at             TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_test_executions_team_id ON api_test_executions (team_id);
CREATE INDEX IF NOT EXISTS ix_api_test_executions_created_at ON api_test_executions (created_at DESC);

-- Broker call log (nodes-as-functions); parameters stored redacted
CREATE TABLE IF NOT EXISTS broker_calls (
    id              TEXT PRIMARY KEY,
    execution_id    TEXT,
    project_id      TEXT,
    environment     TEXT,
    origin          TEXT,
    node_type       TEXT,
    params_redacted JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL,
    error_code      TEXT,
    error           TEXT,
    duration_ms     REAL,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_broker_calls_execution ON broker_calls (execution_id);
CREATE INDEX IF NOT EXISTS ix_broker_calls_project_created ON broker_calls (project_id, created_at DESC);

-- Python modules (route handlers / PythonStep functions)
CREATE TABLE IF NOT EXISTS code_modules (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    source_sha  TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

-- API routes, per-project API keys, spec revisions
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
    handler_module_id   TEXT CONSTRAINT fk_route_module REFERENCES code_modules(id),
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

-- Releases: immutable per-environment snapshots
CREATE TABLE IF NOT EXISTS releases (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    environment       TEXT NOT NULL,
    number            INT NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT false,
    status            TEXT NOT NULL CHECK (status IN ('active','superseded','rolled_back','blocked')),
    snapshot          JSONB NOT NULL,
    checks            JSONB NOT NULL DEFAULT '[]',
    source_release_id TEXT REFERENCES releases(id),
    notes             TEXT,
    created_by        TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (project_id, environment, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_releases_active ON releases (project_id, environment) WHERE is_active;

-- migrate:down
DROP SCHEMA IF EXISTS "workflow-app" CASCADE;
