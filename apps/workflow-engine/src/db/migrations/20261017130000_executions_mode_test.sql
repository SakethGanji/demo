-- 20261017130000_executions_mode_test
-- Stage 3A: test runs are saved as executions with mode 'test' (hidden from run lists, purged after 7 days).
-- Safe for the running engine: it only widens the CHECK.

-- migrate:up
SET search_path TO "workflow-app";
ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_mode_check;
ALTER TABLE executions ADD CONSTRAINT executions_mode_check
    CHECK (mode IN ('manual','webhook','cron','interval','retry','sub_workflow','app','api','sandbox','test'));

-- migrate:down
SET search_path TO "workflow-app";
DELETE FROM node_outputs WHERE execution_id IN (SELECT id FROM executions WHERE mode = 'test');
DELETE FROM executions WHERE mode = 'test';
ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_mode_check;
ALTER TABLE executions ADD CONSTRAINT executions_mode_check
    CHECK (mode IN ('manual','webhook','cron','interval','retry','sub_workflow','app','api','sandbox'));
