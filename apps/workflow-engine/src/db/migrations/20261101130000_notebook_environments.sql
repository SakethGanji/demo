-- 20261101130000_notebook_environments
-- Named variable sets per notebook for API testing (base_url, ids ...): {active, sets:[{name, vars:[{name, value?,
-- secret?}]}]}. A secret entry stores only a project secret's KEY; requests use it as {{ $vars.KEY }}. Additive.

-- migrate:up
SET search_path TO "workflow-app";

CREATE TABLE IF NOT EXISTS notebook_environments (
  notebook_id  TEXT PRIMARY KEY REFERENCES notebooks(id) ON DELETE CASCADE,
  active       TEXT,
  sets         JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at   TIMESTAMP NOT NULL DEFAULT now()
);

-- migrate:down
SET search_path TO "workflow-app";

DROP TABLE IF EXISTS notebook_environments;
