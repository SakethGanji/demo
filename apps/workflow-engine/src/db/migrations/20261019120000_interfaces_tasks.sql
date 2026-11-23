-- 20261019120000_interfaces_tasks
-- Stage 5 (DESIGN-STAGE5-UIS section 3.2): durable human tasks (approvals), interface submissions, chat sessions and
-- messages, and the live interface slug index. Additive only; safe for the running engine.

-- migrate:up
SET search_path TO "workflow-app";

-- Human tasks (the execution_waits of DESIGN-WORKFLOWS-BACKEND section 6, approval kind only for now)
CREATE TABLE IF NOT EXISTS human_tasks (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id      TEXT NOT NULL,
  workflow_version_id INTEGER,
  execution_id     TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
  node_name        TEXT NOT NULL,
  run_index        INTEGER NOT NULL DEFAULT 0,
  kind             TEXT NOT NULL DEFAULT 'approval',
  title            TEXT NOT NULL,
  context          JSONB NOT NULL DEFAULT '{}'::jsonb,
  form             JSONB NOT NULL DEFAULT '[]'::jsonb,
  assignees        JSONB NOT NULL DEFAULT '[]'::jsonb,
  status           TEXT NOT NULL DEFAULT 'open',
  due_at           TIMESTAMPTZ,
  on_timeout       TEXT NOT NULL DEFAULT 'timed_out',
  decision         JSONB,
  decided_by       TEXT,
  decided_at       TIMESTAMPTZ,
  decide_key       TEXT,
  resume_state     TEXT NOT NULL DEFAULT 'none',
  resume_owner     TEXT,
  resume_heartbeat TIMESTAMPTZ,
  snapshot         JSONB NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT human_tasks_status_chk CHECK (status IN ('open','approved','rejected','timed_out','cancelled')),
  CONSTRAINT human_tasks_resume_chk CHECK (resume_state IN ('none','pending','running','done','failed'))
);
CREATE UNIQUE INDEX IF NOT EXISTS human_tasks_exec_node_idx ON human_tasks (execution_id, node_name, run_index);
CREATE INDEX IF NOT EXISTS human_tasks_inbox_idx    ON human_tasks (project_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS human_tasks_due_idx      ON human_tasks (due_at) WHERE status = 'open' AND due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS human_tasks_resume_idx   ON human_tasks (resume_state) WHERE resume_state IN ('pending','running');
CREATE INDEX IF NOT EXISTS human_tasks_workflow_idx ON human_tasks (workflow_id, status);

-- Interface submissions: dedupe + map submission -> execution for anonymous polling
CREATE TABLE IF NOT EXISTS interface_submissions (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id     TEXT NOT NULL,
  slug            TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  execution_id    TEXT REFERENCES executions(id) ON DELETE SET NULL,
  run_token_hash  TEXT NOT NULL,
  mode            TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS interface_submissions_key_idx ON interface_submissions (project_id, slug, idempotency_key);
CREATE INDEX IF NOT EXISTS interface_submissions_exec_idx ON interface_submissions (execution_id);

-- Chat
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id   TEXT NOT NULL,
  slug          TEXT NOT NULL,
  mode          TEXT NOT NULL,
  user_label    TEXT,
  user_id       TEXT,
  token_hash    TEXT NOT NULL,
  busy_execution_id TEXT,
  last_seq      INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_sessions_wf_idx ON chat_sessions (workflow_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
  id           TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('user','assistant','error')),
  content      TEXT NOT NULL,
  execution_id TEXT,
  client_message_id TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_seq_idx ON chat_messages (session_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_client_idx ON chat_messages (session_id, client_message_id) WHERE client_message_id IS NOT NULL;

-- Live slug index (written at publish, deleted when the workflow is deleted)
CREATE TABLE IF NOT EXISTS interface_slugs (
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  slug        TEXT NOT NULL,
  workflow_id TEXT NOT NULL,
  kind        TEXT NOT NULL,
  version_id  INTEGER NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, slug)
);
CREATE UNIQUE INDEX IF NOT EXISTS interface_slugs_wf_idx ON interface_slugs (workflow_id);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS interface_slugs;
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_sessions;
DROP TABLE IF EXISTS interface_submissions;
DROP TABLE IF EXISTS human_tasks;
