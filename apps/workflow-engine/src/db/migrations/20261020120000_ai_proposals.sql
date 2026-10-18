-- 20261020120000_ai_proposals
-- Stage 6 (DESIGN-STAGE6-AI-BUILDER section 2): AI builder proposals and their append-only audit trail.
-- Additive only; safe for the running engine. Sorts after Stage 5's 20261019120000_interfaces_tasks.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS ai_proposals (
  id                   TEXT PRIMARY KEY,
  project_id           TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  team_id              TEXT,
  workflow_id          TEXT,
  parent_id            TEXT REFERENCES ai_proposals(id) ON DELETE SET NULL,
  kind                 TEXT NOT NULL,
  status               TEXT NOT NULL,
  actor                TEXT NOT NULL,
  request              TEXT NOT NULL,
  request_sha256       TEXT NOT NULL,
  spec                 JSONB,
  spec_approved_by     TEXT,
  spec_approved_at     TIMESTAMPTZ,
  spec_fingerprint     TEXT,
  base_definition      JSONB,
  base_draft_hash      TEXT,
  proposed_definition  JSONB,
  ops_log              JSONB NOT NULL DEFAULT '[]'::jsonb,
  diff                 JSONB,
  report               JSONB,
  test_change_requests JSONB NOT NULL DEFAULT '[]'::jsonb,
  answer               TEXT,
  model                TEXT NOT NULL,
  provider             TEXT NOT NULL,
  region               TEXT,
  llm_calls            INTEGER NOT NULL DEFAULT 0,
  input_tokens         INTEGER NOT NULL DEFAULT 0,
  output_tokens        INTEGER NOT NULL DEFAULT 0,
  cost_usd             NUMERIC(10,5),
  duration_ms          INTEGER NOT NULL DEFAULT 0,
  error_code           TEXT,
  error_message        TEXT,
  kept_by              TEXT,
  kept_at              TIMESTAMPTZ,
  kept_draft_hash      TEXT,
  accepted_failing     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- deferrable: Keep of a NEW workflow claims the proposal (workflow_id set) in the same transaction that creates the workflow
  CONSTRAINT ai_proposals_workflow_fk FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE,
  CONSTRAINT ai_proposals_kind_chk CHECK (kind IN ('build','modify','node_assist','chat')),
  CONSTRAINT ai_proposals_status_chk CHECK (status IN
    ('spec_draft','spec_approved','building','ready','failed','kept','undone','discarded','answered'))
);
CREATE INDEX IF NOT EXISTS idx_ai_proposals_project  ON ai_proposals (project_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_ai_proposals_workflow ON ai_proposals (workflow_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_proposals_parent   ON ai_proposals (parent_id) WHERE parent_id IS NOT NULL;
-- the stale-build reaper scans only the (few) rows that are building
CREATE INDEX IF NOT EXISTS idx_ai_proposals_building ON ai_proposals (updated_at) WHERE status = 'building';

-- Append-only audit trail: the repository has no UPDATE or DELETE path for it.
CREATE TABLE IF NOT EXISTS ai_proposal_events (
  id           BIGSERIAL PRIMARY KEY,
  proposal_id  TEXT NOT NULL REFERENCES ai_proposals(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor        TEXT,
  event        TEXT NOT NULL,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT ai_proposal_events_seq_uq UNIQUE (proposal_id, seq)
);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS ai_proposal_events;
DROP TABLE IF EXISTS ai_proposals;
