-- Phase 1 (BACKEND-DESIGN §2.2 M1): projects as the unit of ownership.
-- Safe for the old engine: every new NOT NULL column has a default.

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

INSERT INTO projects (id, team_id, name, slug, environments, default_environment, live_environment)
SELECT 'proj_default', 'default', 'Default project', 'default',
       (SELECT jsonb_agg(jsonb_build_object('name', e, 'kind', 'draft') ORDER BY e)
          FROM (SELECT 'default' AS e UNION SELECT DISTINCT environment FROM variables) s),
       'default', 'default'
ON CONFLICT (id) DO NOTHING;

ALTER TABLE workflows   ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE apps        ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE data_tables ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE folders     ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);

ALTER TABLE executions ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE executions ADD COLUMN IF NOT EXISTS environment TEXT;
UPDATE executions e SET project_id = w.project_id FROM workflows w WHERE w.id = e.workflow_id AND e.project_id IS NULL;
UPDATE executions SET project_id = 'proj_default' WHERE project_id IS NULL;

ALTER TABLE variables ADD COLUMN IF NOT EXISTS project_id TEXT REFERENCES projects(id);
ALTER TABLE variables ADD COLUMN IF NOT EXISTS allowed_hosts JSONB;
DROP INDEX IF EXISTS idx_variables_team_env_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_variables_team_scope ON variables (team_id, environment, key) WHERE project_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_variables_project_scope ON variables (project_id, environment, key) WHERE project_id IS NOT NULL;

DROP INDEX IF EXISTS idx_data_tables_team_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_tables_project_name ON data_tables (project_id, name);

CREATE INDEX IF NOT EXISTS ix_workflows_project ON workflows (project_id);
CREATE INDEX IF NOT EXISTS ix_apps_project ON apps (project_id);
CREATE INDEX IF NOT EXISTS ix_folders_project ON folders (project_id);
CREATE INDEX IF NOT EXISTS ix_executions_project_start ON executions (project_id, start_time DESC);

-- migrate:down
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM variables WHERE project_id IS NOT NULL) THEN
        RAISE EXCEPTION 'project-scoped variables exist; delete them before rolling back';
    END IF;
END $$;
DROP INDEX IF EXISTS ix_executions_project_start;
DROP INDEX IF EXISTS ix_folders_project;
DROP INDEX IF EXISTS ix_apps_project;
DROP INDEX IF EXISTS ix_workflows_project;
DROP INDEX IF EXISTS idx_data_tables_project_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_tables_team_name ON data_tables (team_id, name);
DROP INDEX IF EXISTS idx_variables_project_scope;
DROP INDEX IF EXISTS idx_variables_team_scope;
CREATE UNIQUE INDEX IF NOT EXISTS idx_variables_team_env_key ON variables (team_id, environment, key);
ALTER TABLE variables DROP COLUMN IF EXISTS allowed_hosts;
ALTER TABLE variables DROP COLUMN IF EXISTS project_id;
ALTER TABLE executions DROP COLUMN IF EXISTS environment;
ALTER TABLE executions DROP COLUMN IF EXISTS project_id;
ALTER TABLE folders DROP COLUMN IF EXISTS project_id;
ALTER TABLE data_tables DROP COLUMN IF EXISTS project_id;
ALTER TABLE apps DROP COLUMN IF EXISTS project_id;
ALTER TABLE workflows DROP COLUMN IF EXISTS project_id;
DROP TABLE IF EXISTS projects;
