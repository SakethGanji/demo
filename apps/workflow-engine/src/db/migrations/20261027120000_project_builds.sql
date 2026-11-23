-- 20261027120000_project_builds
-- Build runs (PLAN-STEP orchestrator): one row per "Build it" on an approved plan. The coordinator runs the backend
-- generator per module in build order, pauses once for the user's input (secrets, starting rows), runs the app builder
-- per planned app, then verifies against the plan. ``steps`` is the live timeline and is rewritten on every change;
-- ``report`` is set when the run ends. At most one active (queued / running / waiting_for_input) build per project
-- (partial unique index). Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS project_builds (
  id           TEXT PRIMARY KEY,                       -- 'build_' + uuid
  project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  plan_version INTEGER NOT NULL,
  app_key      TEXT,                                   -- build only this plan app (modules are still built)
  rebuild      BOOLEAN NOT NULL DEFAULT false,         -- rebuild modules already built against this plan version
  status       TEXT NOT NULL DEFAULT 'queued'
               CHECK (status IN ('queued','running','waiting_for_input','done','partial','failed','cancelled')),
  reason       TEXT,
  steps        JSONB NOT NULL DEFAULT '[]'::jsonb,
  report       JSONB,
  created_by   TEXT,
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  started_at   TIMESTAMP,
  finished_at  TIMESTAMP,
  updated_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_project_builds_active ON project_builds (project_id)
  WHERE status IN ('queued','running','waiting_for_input');
CREATE INDEX IF NOT EXISTS ix_project_builds_project ON project_builds (project_id, created_at DESC);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS project_builds;
