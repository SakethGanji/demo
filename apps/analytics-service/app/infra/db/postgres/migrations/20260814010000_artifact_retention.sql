-- 20260814010000_artifact_retention
-- Derived artifacts accumulate forever otherwise. Nothing ever deleted a
-- query result, an export, or a diff — the samples area only grew, and the
-- flat prefix made "expire scratch, keep published output" inexpressible.
--
-- Retention is a property of the *kind*, and the kind is now a path segment,
-- so a sweep can be described precisely. `expires_at` makes the decision
-- explicit and auditable per row rather than recomputed from a policy table
-- that may since have changed: a row written under a 7-day policy keeps its
-- original deadline.
--
-- NULL means keep forever — published sources and anything a user pinned.

-- migrate:up
SET search_path TO "accelerator";

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- The GC sweep is "expired, oldest first"; a partial index keeps it off the
-- rows that never expire, which are the ones that pile up.
CREATE INDEX IF NOT EXISTS ix_artifacts_expires_at
    ON artifacts (expires_at) WHERE expires_at IS NOT NULL;

-- The sweep runs as a job so it has the same claim/retry/history as everything
-- else, and so a scheduler can fire it without a second mechanism.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics', 'relationship_discovery',
                        'webhook_delivery', 'artifact_gc'));

-- migrate:down
SET search_path TO "accelerator";

DROP INDEX IF EXISTS ix_artifacts_expires_at;
ALTER TABLE artifacts DROP COLUMN IF EXISTS expires_at;

DELETE FROM jobs WHERE job_type = 'artifact_gc';
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics', 'relationship_discovery',
                        'webhook_delivery'));
