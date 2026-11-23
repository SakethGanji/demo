-- 20261015130000_release_legacy_dev_snapshot
-- Audit #7: releases created in a draft environment before D10 are still "active". Make them snapshots so
-- baseline()/rollback treat them like new ones. (draft env = a project environment whose kind is "draft".)

-- migrate:up
SET search_path TO "workflow-app";
UPDATE releases r SET status = 'snapshot', is_active = false
 WHERE r.status = 'active'
   AND EXISTS (SELECT 1 FROM projects p, jsonb_array_elements(p.environments) e
                WHERE p.id = r.project_id AND e->>'name' = r.environment AND coalesce(e->>'kind', 'draft') = 'draft');

-- migrate:down
-- data fix; nothing to undo (the old "active" flag on draft-env releases was the bug)
SELECT 1;
