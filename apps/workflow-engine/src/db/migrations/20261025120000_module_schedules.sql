-- 20261025120000_module_schedules
-- Scheduled functions in project code modules (@schedule decorator): one row per (module, function), reconciled from the
-- source on save and removed with the module. The engine's scheduler runs the due ones through the sandbox like a route
-- call and records the outcome here. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS module_schedules (
  id             TEXT PRIMARY KEY,                       -- 'sch_' + 12 hex
  project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  module_id      TEXT NOT NULL REFERENCES code_modules(id) ON DELETE CASCADE,
  function       TEXT NOT NULL,
  cron           TEXT,                                   -- 5-field cron, or
  every_seconds  INTEGER CHECK (every_seconds IS NULL OR every_seconds >= 15),
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  next_run_at    TIMESTAMP,
  running_since  TIMESTAMP,                              -- set while a run is in flight (no-overlap guard)
  last_run_at    TIMESTAMP,
  last_status    TEXT,                                   -- success | failed
  last_error     TEXT,
  duration_ms    INTEGER,
  last_execution_id TEXT,
  line           INTEGER,
  created_at     TIMESTAMP NOT NULL DEFAULT now(),
  updated_at     TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (module_id, function),
  CHECK ((cron IS NULL) <> (every_seconds IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_module_schedules_project ON module_schedules (project_id);
CREATE INDEX IF NOT EXISTS ix_module_schedules_due ON module_schedules (next_run_at) WHERE enabled;

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS module_schedules;
