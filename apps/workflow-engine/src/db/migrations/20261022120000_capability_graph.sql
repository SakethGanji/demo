-- 20261022120000_capability_graph
-- Semantic Capability Graph (PLAN-CAPABILITY-GRAPH section 1.1, frozen in CONTRACTS-CAPGRAPH.md): the typed graph
-- (kg_nodes, kg_edges), its append-only learn log, the multi-part application builds, and three additive columns on
-- ai_proposals plus the new 'clarify' proposal status. Additive; sorts after Stage 8's 20261021120000_reuse.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS kg_nodes (
  id                TEXT PRIMARY KEY,                 -- 'kn_' + 12 hex
  key               TEXT NOT NULL,                    -- natural key (CONTRACTS-CAPGRAPH section 1), unique per scope
  kind              TEXT NOT NULL CHECK (kind IN ('concept','capability','role','pattern','app')),
  subkind           TEXT,
  scope             TEXT NOT NULL CHECK (scope IN ('global','team','project')),
  team_id           TEXT,
  project_id        TEXT REFERENCES projects(id) ON DELETE CASCADE,
  visibility        TEXT NOT NULL DEFAULT 'scope' CHECK (visibility IN ('scope','org')),
  label             TEXT NOT NULL,
  summary           TEXT NOT NULL DEFAULT '',
  aliases           JSONB NOT NULL DEFAULT '[]'::jsonb,
  props             JSONB NOT NULL DEFAULT '{}'::jsonb,
  source            TEXT NOT NULL CHECK (source IN ('seed','extracted','learned','human','llm_suggested')),
  status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','stale','proposed','rejected')),
  validated_count   INTEGER NOT NULL DEFAULT 0,
  last_validated_at TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- a scope names exactly the owner columns it needs
  CONSTRAINT kg_nodes_scope_owner_chk CHECK (
    (scope = 'global'  AND team_id IS NULL AND project_id IS NULL) OR
    (scope = 'team'    AND team_id IS NOT NULL AND project_id IS NULL) OR
    (scope = 'project' AND project_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_kg_nodes_key ON kg_nodes (scope, coalesce(team_id, ''), coalesce(project_id, ''), key);
CREATE INDEX IF NOT EXISTS ix_kg_nodes_project ON kg_nodes (project_id, kind);
CREATE INDEX IF NOT EXISTS ix_kg_nodes_kind ON kg_nodes (kind, status);

CREATE TABLE IF NOT EXISTS kg_edges (
  id          BIGSERIAL PRIMARY KEY,
  src_id      TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  rel         TEXT NOT NULL CHECK (rel IN ('is_a','requires','implements','requires_permission','composes','produces',
                                           'consumes','escalates_to','renders_with','validated_by','derived_from')),
  dst_id      TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  props       JSONB NOT NULL DEFAULT '{}'::jsonb,
  source      TEXT NOT NULL,
  confidence  REAL NOT NULL DEFAULT 1.0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (src_id, rel, dst_id)
);
CREATE INDEX IF NOT EXISTS ix_kg_edges_dst ON kg_edges (dst_id, rel);

-- Append-only "operational memory": what each Keep / rebuild taught the graph (the repository has no UPDATE or DELETE path).
CREATE TABLE IF NOT EXISTS kg_learn_events (
  id          BIGSERIAL PRIMARY KEY,
  project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
  team_id     TEXT,
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor       TEXT,
  trigger     TEXT NOT NULL CHECK (trigger IN ('seed','rebuild','kept_workflow','kept_app','human')),
  proposal_id TEXT,                                   -- or the app_build id
  summary     TEXT NOT NULL,
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_kg_learn_project ON kg_learn_events (project_id, at DESC);

-- Multi-part application builds. Each part is an ordinary ai_proposals row.
CREATE TABLE IF NOT EXISTS app_builds (
  id             TEXT PRIMARY KEY,                    -- 'ab_' + 12 hex
  project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  team_id        TEXT,
  actor          TEXT NOT NULL,
  request        TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  status         TEXT NOT NULL,
  understanding  JSONB,
  answers        JSONB NOT NULL DEFAULT '{}'::jsonb,
  plan           JSONB,
  bundle         JSONB,
  install_id     TEXT,
  kept_by        TEXT,
  kept_at        TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT app_builds_status_chk CHECK (status IN
    ('clarify','planned','specs','building','ready','kept','discarded','failed'))
);
CREATE INDEX IF NOT EXISTS idx_app_builds_project ON app_builds (project_id, created_at DESC, id DESC);

-- ai_proposals: the understanding, the owning app build and the part key (all nullable: old rows and old callers are unaffected).
ALTER TABLE ai_proposals ADD COLUMN IF NOT EXISTS understanding JSONB;
ALTER TABLE ai_proposals ADD COLUMN IF NOT EXISTS app_build_id TEXT REFERENCES app_builds(id) ON DELETE SET NULL;
ALTER TABLE ai_proposals ADD COLUMN IF NOT EXISTS part_key TEXT;
CREATE INDEX IF NOT EXISTS idx_ai_proposals_app_build ON ai_proposals (app_build_id) WHERE app_build_id IS NOT NULL;

-- Stage 6 put a CHECK on the status, so the new 'clarify' status needs the constraint replaced (the plan assumed none).
ALTER TABLE ai_proposals DROP CONSTRAINT IF EXISTS ai_proposals_status_chk;
ALTER TABLE ai_proposals ADD CONSTRAINT ai_proposals_status_chk CHECK (status IN
  ('spec_draft','spec_approved','building','ready','failed','kept','undone','discarded','answered','clarify'));

-- migrate:down
SET search_path TO "workflow-app";
-- a clarify proposal has no spec yet: the safest old status for it is discarded
UPDATE ai_proposals SET status = 'discarded' WHERE status = 'clarify';
ALTER TABLE ai_proposals DROP CONSTRAINT IF EXISTS ai_proposals_status_chk;
ALTER TABLE ai_proposals ADD CONSTRAINT ai_proposals_status_chk CHECK (status IN
  ('spec_draft','spec_approved','building','ready','failed','kept','undone','discarded','answered'));
DROP INDEX IF EXISTS idx_ai_proposals_app_build;
ALTER TABLE ai_proposals DROP COLUMN IF EXISTS part_key;
ALTER TABLE ai_proposals DROP COLUMN IF EXISTS app_build_id;
ALTER TABLE ai_proposals DROP COLUMN IF EXISTS understanding;
DROP TABLE IF EXISTS app_builds;
DROP TABLE IF EXISTS kg_learn_events;
DROP TABLE IF EXISTS kg_edges;
DROP TABLE IF EXISTS kg_nodes;
