-- 20260814000000_artifact_layout
-- Derived outputs move from a flat `samples/` prefix to
--     artifacts/{team_id}/{dataset_id}/{kind}/{filename}
--
-- The flat prefix could not express the things a bucket needs to express:
-- retention differs sharply by kind (query/diff scratch vs a published
-- source), tenants want prefix-scoped IAM, and deleting a dataset had to
-- enumerate keys instead of dropping a prefix.
--
-- A reader holding only `/samples/{filename}` knows none of those segments, so
-- `filename` becomes a first-class column and resolution goes through the same
-- row that already governs authorization — which also lets the listing come
-- from Postgres instead of scanning the bucket.
--
-- DEV MIGRATION: existing `samples/` rows are deleted rather than relocated.
-- The blobs are purged separately (scripts/purge_legacy_artifacts.py). Do not
-- reuse this shape against data anyone cares about.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS filename TEXT;

UPDATE artifacts
   SET filename = regexp_replace(storage_key, '^.*/', '')
 WHERE filename IS NULL;

-- Look-ups are by filename now, and it is the public identifier.
CREATE INDEX IF NOT EXISTS ix_artifacts_filename ON artifacts (filename);
CREATE INDEX IF NOT EXISTS ix_artifacts_dataset_kind
    ON artifacts (dataset_id, artifact_type);

-- Legacy rows point at keys that no longer exist under the new scheme.
DELETE FROM artifacts WHERE storage_key LIKE 'samples/%';

-- migrate:down
SET search_path TO "accelerator";

DROP INDEX IF EXISTS ix_artifacts_dataset_kind;
DROP INDEX IF EXISTS ix_artifacts_filename;
ALTER TABLE artifacts DROP COLUMN IF EXISTS filename;
