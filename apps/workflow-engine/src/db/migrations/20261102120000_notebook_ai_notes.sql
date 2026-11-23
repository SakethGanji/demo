-- 20261102120000_notebook_ai_notes
-- Ask's running notes per notebook (the model rewrites them in its plan_cells call; shown at the top of every Ask
-- prompt; capped at 2000 chars), and a run option to keep going after a failing cell. Additive: a nullable column and
-- a column with a default; old code never reads them.

-- migrate:up
SET search_path TO "workflow-app";

ALTER TABLE notebooks ADD COLUMN IF NOT EXISTS ai_notes TEXT;
ALTER TABLE notebook_runs ADD COLUMN IF NOT EXISTS continue_on_fail BOOLEAN NOT NULL DEFAULT FALSE;

-- migrate:down
SET search_path TO "workflow-app";

ALTER TABLE notebook_runs DROP COLUMN IF EXISTS continue_on_fail;
ALTER TABLE notebooks DROP COLUMN IF EXISTS ai_notes;
