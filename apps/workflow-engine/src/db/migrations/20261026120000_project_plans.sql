-- 20261026120000_project_plans
-- The ProjectPlan (PLAN-STEP design 2.1): one agreed, versioned plan per project (requirements, data model, modules and
-- route contracts, screens, integrations, NFR, build order) that every builder reads. An approved version is never edited:
-- revising makes a new draft (base_version = the approved one) and approving it supersedes the old. At most one approved
-- version per project (partial unique index). Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS project_plans (
  id           TEXT PRIMARY KEY,                       -- 'plan_' + uuid
  project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version      INTEGER NOT NULL CHECK (version > 0),
  status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','superseded')),
  base_version INTEGER,
  body         JSONB NOT NULL DEFAULT '{}'::jsonb,
  questions    JSONB NOT NULL DEFAULT '[]'::jsonb,     -- open questions of the current clarify round
  round        INTEGER NOT NULL DEFAULT 0,
  created_by   TEXT,
  approved_by  TEXT,                                   -- a typed, unverified name (identity is out of scope)
  approved_at  TIMESTAMP,
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_at   TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (project_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_project_plans_approved ON project_plans (project_id) WHERE status = 'approved';
CREATE INDEX IF NOT EXISTS ix_project_plans_project ON project_plans (project_id, version DESC);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS project_plans;
