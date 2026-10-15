-- 20261015120000_release_snapshot_status
-- Review D10: releases created in a draft environment are snapshots — stored, numbered, promotable,
-- never "active" (a draft environment always serves the drafts). Adds the 'snapshot' status.
-- Safe for the running engine: it only widens the CHECK.

-- migrate:up
SET search_path TO "workflow-app";
ALTER TABLE releases DROP CONSTRAINT IF EXISTS releases_status_check;
ALTER TABLE releases ADD CONSTRAINT releases_status_check
    CHECK (status IN ('active','superseded','rolled_back','blocked','snapshot'));

-- migrate:down
SET search_path TO "workflow-app";
UPDATE releases SET status = 'superseded' WHERE status = 'snapshot';
ALTER TABLE releases DROP CONSTRAINT IF EXISTS releases_status_check;
ALTER TABLE releases ADD CONSTRAINT releases_status_check
    CHECK (status IN ('active','superseded','rolled_back','blocked'));
