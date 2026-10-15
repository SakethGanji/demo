-- Phase 8 (BACKEND-DESIGN §2.2 M5): python modules (route handlers / PythonStep functions).
CREATE TABLE IF NOT EXISTS code_modules (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    source_sha  TEXT NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);
ALTER TABLE api_routes ADD CONSTRAINT fk_route_module FOREIGN KEY (handler_module_id) REFERENCES code_modules(id);

-- migrate:down
ALTER TABLE api_routes DROP CONSTRAINT IF EXISTS fk_route_module;
DROP TABLE IF EXISTS code_modules;
