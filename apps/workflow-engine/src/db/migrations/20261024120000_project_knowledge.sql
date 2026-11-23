-- 20261024120000_project_knowledge
-- Per-project knowledge store for the AI builders (PLAN-BUILDER section 3, WP-1): requirements, specs, decisions, answered
-- clarify questions and the facts derived from the project's own code (tables, routes, screens, component use, failures).
-- One row per (project, kind, key); full-text search through a stored tsvector with a GIN index. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS project_knowledge (
  id          TEXT PRIMARY KEY,                       -- 'pk_' + 12 hex
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN ('requirement','spec','decision','clarify_answer','table_schema','route',
                                            'screen','component_use','failure','acceptance')),
  key         TEXT NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  body        JSONB NOT NULL DEFAULT '{}'::jsonb,
  text        TEXT NOT NULL DEFAULT '',               -- the searchable rendering of title + body
  status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
  version     INTEGER NOT NULL DEFAULT 1,
  source      TEXT NOT NULL DEFAULT 'human' CHECK (source IN ('design','code','human','failure')),
  source_ref  TEXT,
  tsv         TSVECTOR GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(key, '') || ' ' || coalesce(title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(text, '')), 'B')) STORED,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, kind, key)
);
CREATE INDEX IF NOT EXISTS ix_project_knowledge_tsv ON project_knowledge USING GIN (tsv);
CREATE INDEX IF NOT EXISTS ix_project_knowledge_project ON project_knowledge (project_id, kind);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS project_knowledge;
