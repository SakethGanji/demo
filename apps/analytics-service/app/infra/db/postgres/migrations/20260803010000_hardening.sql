-- 20260803010000_hardening
-- Bank-production hardening: append-only audit log + dataset classification.

-- migrate:up

SET search_path TO "accelerator";

-- --- Data classification on datasets ------------------------------------------
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL
    DEFAULT 'internal'
    CHECK (classification IN ('public', 'internal', 'confidential', 'restricted'));

-- --- Append-only audit log ----------------------------------------------------
-- One row per security-relevant action (writes, auth events, data egress).
CREATE TABLE IF NOT EXISTS audit_log (
    id             BIGSERIAL PRIMARY KEY,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id  UUID,                 -- null for anonymous/failed-auth attempts
    actor_email    TEXT,
    team_id        UUID,
    action         TEXT NOT NULL,        -- e.g. "POST /api/v1/upload"
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    status_code    INTEGER NOT NULL,
    resource_type  TEXT,
    resource_id    TEXT,
    ip             TEXT,
    user_agent     TEXT,
    request_id     TEXT,
    metadata       JSONB
);
CREATE INDEX IF NOT EXISTS ix_audit_log_occurred_at ON audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_actor ON audit_log (actor_user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_team ON audit_log (team_id);

-- Append-only guard: forbid UPDATE/DELETE on the audit log at the DB level.
CREATE OR REPLACE FUNCTION accelerator.audit_log_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION accelerator.audit_log_immutable();

-- migrate:down

SET search_path TO "accelerator";

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
DROP FUNCTION IF EXISTS accelerator.audit_log_immutable();
DROP TABLE IF EXISTS audit_log;
ALTER TABLE datasets DROP COLUMN IF EXISTS classification;
