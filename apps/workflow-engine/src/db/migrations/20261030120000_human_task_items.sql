-- 20261030120000_human_task_items
-- Approval "One task per item" (per_item): one node-run can open N tasks at once, one per item. They share
-- (execution_id, node_name, run_index) - the group the run waits on - and are told apart by item_index (the item's
-- position in the node's input). Additive: existing rows get item_index 0, so every old task is a group of one.

-- migrate:up
SET search_path TO "workflow-app";

ALTER TABLE human_tasks ADD COLUMN IF NOT EXISTS item_index INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS human_tasks_exec_node_item_idx ON human_tasks (execution_id, node_name, run_index, item_index);
DROP INDEX IF EXISTS human_tasks_exec_node_idx;

-- migrate:down
SET search_path TO "workflow-app";
DELETE FROM human_tasks WHERE item_index <> 0;
CREATE UNIQUE INDEX IF NOT EXISTS human_tasks_exec_node_idx ON human_tasks (execution_id, node_name, run_index);
DROP INDEX IF EXISTS human_tasks_exec_node_item_idx;
ALTER TABLE human_tasks DROP COLUMN IF EXISTS item_index;
