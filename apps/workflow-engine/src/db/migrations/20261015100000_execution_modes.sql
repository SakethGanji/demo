-- Phase 3+: new execution modes for published-app runs (app), project API routes (api)
-- and sandboxed python (sandbox). BACKEND-DESIGN §2.2 (added during phase 3).
ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_mode_check;
ALTER TABLE executions ADD CONSTRAINT executions_mode_check
    CHECK (mode IN ('manual','webhook','cron','interval','retry','sub_workflow','app','api','sandbox'));

-- migrate:down
DELETE FROM node_outputs WHERE execution_id IN (SELECT id FROM executions WHERE mode IN ('app','api','sandbox'));
DELETE FROM executions WHERE mode IN ('app','api','sandbox');
ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_mode_check;
ALTER TABLE executions ADD CONSTRAINT executions_mode_check
    CHECK (mode IN ('manual','webhook','cron','interval','retry','sub_workflow'));
