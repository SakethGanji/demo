-- 20261031120000_notebooks
-- Notebooks: an append-only list of typed cells (query, http, workflow, rule, change, approval, data, store, browser,
-- for_each, ask, note) run top to bottom. notebooks.active_run_id is the concurrency lock (NULL = free). Results keep
-- only a preview (<=50 rows); full row sets live in file storage (output_ref). notebook_writes records every applied
-- write so a rerun can stop before it and apply stays idempotent. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS notebooks (
  id             TEXT PRIMARY KEY,                       -- 'nb_' + hex
  project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  team_id        TEXT,
  title          TEXT NOT NULL,
  description    TEXT,
  status         TEXT NOT NULL DEFAULT 'idle',           -- idle | running | needs_you | failed | done
  environment    TEXT,
  template_slug  TEXT,
  run_as         TEXT,
  created_by     TEXT,
  active_run_id  TEXT,                                   -- NULL = free; set by a conditional UPDATE (the run lock)
  kept           JSONB NOT NULL DEFAULT '[]'::jsonb,     -- [{target, workflow_id, at, by}]
  created_at     TIMESTAMP NOT NULL DEFAULT now(),
  updated_at     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notebooks_project ON notebooks (project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS notebook_cells (
  id              TEXT PRIMARY KEY,                      -- 'cell_' + hex
  notebook_id     TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  parent_cell_id  TEXT REFERENCES notebook_cells(id) ON DELETE CASCADE,  -- for_each body
  position        INTEGER NOT NULL,
  name            TEXT NOT NULL,                         -- c1, c2, ... (references); unique per notebook
  kind            TEXT NOT NULL,
  title           TEXT,
  spec            JSONB NOT NULL DEFAULT '{}'::jsonb,
  guard           JSONB,
  created_by      TEXT,
  created_via     TEXT NOT NULL DEFAULT 'user',          -- user | agent | template
  created_at      TIMESTAMP NOT NULL DEFAULT now(),
  updated_at      TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (notebook_id, name)
);
-- position is unique per (notebook, parent); top-level cells have parent NULL, which a plain UNIQUE treats as distinct
CREATE UNIQUE INDEX IF NOT EXISTS ux_notebook_cells_position
  ON notebook_cells (notebook_id, COALESCE(parent_cell_id, ''), position);

CREATE TABLE IF NOT EXISTS notebook_runs (
  id             TEXT PRIMARY KEY,                       -- 'nbr_' + hex
  notebook_id    TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
  n              INTEGER NOT NULL,                       -- run number within the notebook
  from_position  INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'running',        -- running | done | failed | stopped_at_write | needs_you | cancelled | interrupted
  stop           JSONB,                                  -- {reason, cell_id, applied_at, applied_by}
  on_applied     TEXT NOT NULL DEFAULT 'stop',           -- stop | skip
  reapply        JSONB NOT NULL DEFAULT '[]'::jsonb,     -- [cell_id] explicitly allowed to write again
  triggered_by   TEXT,
  run_as         TEXT,
  via_agent      TEXT,
  heartbeat_at   TIMESTAMP,
  started_at     TIMESTAMP NOT NULL DEFAULT now(),
  finished_at    TIMESTAMP,
  UNIQUE (notebook_id, n)
);

CREATE TABLE IF NOT EXISTS notebook_cell_results (
  id            TEXT PRIMARY KEY,                        -- 'nbres_' + hex
  run_id        TEXT NOT NULL REFERENCES notebook_runs(id) ON DELETE CASCADE,
  cell_id       TEXT NOT NULL REFERENCES notebook_cells(id) ON DELETE CASCADE,
  row_index     INTEGER,                                 -- for_each body rows; NULL for the cell itself
  status        TEXT NOT NULL,
  output        JSONB,                                   -- preview: <=50 rows + row_count + columns, or a summary
  output_ref    TEXT,                                    -- file-storage key of the full rows
  error         JSONB,                                   -- {code, message}
  execution_id  TEXT,
  job_id        TEXT,
  ms            INTEGER,
  created_at    TIMESTAMP NOT NULL DEFAULT now()
);
-- unique on (run_id, cell_id, row_index) with NULL row_index counted as one value
CREATE UNIQUE INDEX IF NOT EXISTS ux_notebook_cell_results_run_cell_row
  ON notebook_cell_results (run_id, cell_id, COALESCE(row_index, -1));
CREATE INDEX IF NOT EXISTS ix_notebook_cell_results_cell ON notebook_cell_results (cell_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notebook_writes (
  id               TEXT PRIMARY KEY,                     -- 'nbw_' + hex
  cell_id          TEXT NOT NULL REFERENCES notebook_cells(id) ON DELETE CASCADE,  -- the service refuses DELETE of an applied cell (409)
  run_id           TEXT REFERENCES notebook_runs(id) ON DELETE SET NULL,
  row_index        INTEGER,
  idempotency_key  TEXT NOT NULL UNIQUE,
  before           JSONB,
  after            JSONB,
  applied_by       TEXT,
  applied_at       TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notebook_writes_cell ON notebook_writes (cell_id, applied_at DESC);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS notebook_writes;
DROP TABLE IF EXISTS notebook_cell_results;
DROP TABLE IF EXISTS notebook_runs;
DROP TABLE IF EXISTS notebook_cells;
DROP TABLE IF EXISTS notebooks;
