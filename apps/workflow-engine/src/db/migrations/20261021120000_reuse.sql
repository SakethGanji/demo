-- 20261021120000_reuse
-- Stage 8 (DESIGN-STAGE8-REUSE section 10): sub-workflow lineage on executions, the workflow_refs reference index,
-- and template installs. Additive except the parent FK (orphans are cleared first). Sorts after Stage 6's 20261020120000.

-- migrate:up
SET search_path TO "workflow-app";

-- 1. Executions: lineage for sub-workflow runs (parent_execution_id / parent_node_name / depth already exist).
ALTER TABLE executions ADD COLUMN IF NOT EXISTS root_execution_id text NULL;
CREATE INDEX IF NOT EXISTS ix_executions_root ON executions (root_execution_id);
-- no writer existed before Stage 8, so this should touch nothing; it makes the FK safe to add
UPDATE executions SET parent_execution_id = NULL
  WHERE parent_execution_id IS NOT NULL AND parent_execution_id NOT IN (SELECT id FROM executions);
ALTER TABLE executions DROP CONSTRAINT IF EXISTS fk_executions_parent;
ALTER TABLE executions ADD CONSTRAINT fk_executions_parent
  FOREIGN KEY (parent_execution_id) REFERENCES executions (id) ON DELETE CASCADE;
-- node_outputs already cascade from executions (baseline FK node_outputs.execution_id ... ON DELETE CASCADE).

-- 2. Reference index for cycle detection, "used by", publish impact and delete protection.
CREATE TABLE IF NOT EXISTS workflow_refs (
  id                 bigserial PRIMARY KEY,
  project_id         text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  parent_workflow_id text NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
  parent_version_id  integer NULL REFERENCES workflow_versions (id) ON DELETE CASCADE,  -- NULL = draft
  node_name          text NOT NULL,
  child_workflow_id  text NOT NULL,          -- no FK: a dangling ref is a WF006 finding, not a database error
  version_spec       text NOT NULL,          -- 'draft' | 'latest' | '<n>'
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_refs_node
  ON workflow_refs (parent_workflow_id, COALESCE(parent_version_id, 0), node_name);
CREATE INDEX IF NOT EXISTS ix_workflow_refs_child ON workflow_refs (child_workflow_id);
CREATE INDEX IF NOT EXISTS ix_workflow_refs_project ON workflow_refs (project_id);

-- 3. Template installs (idempotency + id mapping).
CREATE TABLE IF NOT EXISTS template_installs (
  id                text PRIMARY KEY,                     -- 'tpi_' + uuid
  project_id        text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  template_slug     text NOT NULL,
  template_version  text NOT NULL,
  bundle_hash       text NOT NULL,
  install_name      text NOT NULL,                         -- slug, or slug-2.. for copies
  status            text NOT NULL CHECK (status IN ('installed')),   -- failed installs roll back; seam for 'upgrading'
  mapping           jsonb NOT NULL DEFAULT '{}'::jsonb,
  needs             jsonb NOT NULL DEFAULT '[]'::jsonb,
  depends_on        jsonb NOT NULL DEFAULT '[]'::jsonb,    -- [install_id]
  installed_by      text NULL,                             -- seam: user id once login exists
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, template_slug, install_name)
);

-- migrate:down
SET search_path TO "workflow-app";
DROP TABLE IF EXISTS template_installs;
DROP TABLE IF EXISTS workflow_refs;
ALTER TABLE executions DROP CONSTRAINT IF EXISTS fk_executions_parent;
DROP INDEX IF EXISTS ix_executions_root;
ALTER TABLE executions DROP COLUMN IF EXISTS root_execution_id;
