-- 20260803000000_auth
-- Team-scoped RBAC (team_members) + user status/superuser flags.
-- POC identity: callers pass X-User-Id; no password/token storage.

-- migrate:up

SET search_path TO "accelerator";

-- --- Extend users for identity + account state -------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'disabled'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_superuser BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- --- Per-team roles: the RBAC source of truth --------------------------------
-- A user may belong to many teams, each with its own role.
CREATE TABLE IF NOT EXISTS team_members (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_team_user
    ON team_members (team_id, user_id);
CREATE INDEX IF NOT EXISTS ix_team_members_user_id ON team_members (user_id);

-- --- Promote the seeded System user, make it owner of the Default team --------
UPDATE users SET is_superuser = true
    WHERE id = '00000000-0000-0000-0000-000000000001';

INSERT INTO team_members (team_id, user_id, role) VALUES
    ('00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'owner')
ON CONFLICT (team_id, user_id) DO NOTHING;

-- migrate:down

SET search_path TO "accelerator";

DROP TABLE IF EXISTS team_members;

ALTER TABLE users DROP COLUMN IF EXISTS updated_at;
ALTER TABLE users DROP COLUMN IF EXISTS is_superuser;
ALTER TABLE users DROP COLUMN IF EXISTS status;
