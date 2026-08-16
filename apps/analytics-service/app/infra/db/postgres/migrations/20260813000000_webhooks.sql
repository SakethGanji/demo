-- 20260813000000_webhooks
-- Outbound notifications on lifecycle events. What turns a passive store into
-- a platform: "dataset X failed validation" reaches a channel instead of
-- waiting for someone to poll.
--
-- Payloads are deliberately THIN — event type, resource ids, and counts. They
-- carry no dataset rows, both because that is the control-plane rule and
-- because a webhook body is the last place sensitive values should end up.
-- Receivers fetch what they need through the authorized API.

-- migrate:up
SET search_path TO "accelerator";

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    -- Shared secret for the HMAC signature. Write-only at the API layer: it is
    -- returned once at creation and never read back.
    secret      TEXT NOT NULL,
    -- Empty means every event; otherwise an allow-list of event types.
    events      JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, name)
);
CREATE INDEX IF NOT EXISTS ix_webhook_subscriptions_team
    ON webhook_subscriptions (team_id, enabled);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id  UUID NOT NULL REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,
    dataset_id       UUID REFERENCES datasets(id) ON DELETE SET NULL,
    -- Thin metadata only: ids and counts, never dataset rows.
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts         INT NOT NULL DEFAULT 0,
    response_status  INT,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_subscription
    ON webhook_deliveries (subscription_id, created_at DESC);

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics', 'relationship_discovery',
                        'webhook_delivery'));

-- migrate:down
SET search_path TO "accelerator";

DELETE FROM jobs WHERE job_type = 'webhook_delivery';
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
    CHECK (job_type IN ('import', 'profiling', 'sampling', 'export', 'transform',
                        'validation', 'analytics', 'relationship_discovery'));

DROP TABLE IF EXISTS webhook_deliveries;
DROP TABLE IF EXISTS webhook_subscriptions;
