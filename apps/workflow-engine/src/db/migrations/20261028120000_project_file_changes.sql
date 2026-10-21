-- 20261028120000_project_file_changes
-- The project's "commit log" over its one file tree (LIVE-FILES §1): one row per changed file, written by the module save,
-- the app version save (one row per changed file), table create / migrate and plan approval. Only the diff stats are kept,
-- never the before / after content (the owning stores hold that). ``build_id`` / ``step_key`` are set when a build step made
-- the change. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS project_file_changes (
  id            TEXT PRIMARY KEY,                       -- 'chg_' + uuid
  project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path          TEXT NOT NULL,                          -- tree path, e.g. backend/runs.py, apps/qa-workbench/App.tsx
  op            TEXT NOT NULL CHECK (op IN ('create','update','delete')),
  actor         TEXT NOT NULL DEFAULT 'user'
                CHECK (actor IN ('backend_builder','ui_builder','user','plan')),
  build_id      TEXT,
  step_key      TEXT,
  summary       TEXT,
  lines_added   INTEGER NOT NULL DEFAULT 0,
  lines_removed INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_project_file_changes_project ON project_file_changes (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_project_file_changes_path ON project_file_changes (project_id, path, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_project_file_changes_build ON project_file_changes (build_id) WHERE build_id IS NOT NULL;

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS project_file_changes;
