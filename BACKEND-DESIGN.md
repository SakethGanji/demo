# BACKEND-DESIGN — phases 1–9

Written 2026-10-14 against `5684ba5` (branch `revert-to-5684ba5`). This is the backend for
`PLAN.md` phases 1–9, and it is what I will build. All paths are relative to `apps/workflow-engine/`
unless they start with `studio:`, which means `apps/workflow-studio/src/`. Every claim was checked
against the code, and `file:line` citations point at this commit.

## 0. Facts that shape the design (verified)

| Fact | Evidence | Consequence |
|---|---|---|
| The engine never reads `X-User-Id`, and the studio never sends it. The studio's only header is `Content-Type: application/json`, sent on every call including GET and DELETE. | `grep -ri x-user-id src` finds nothing; `studio:shared/lib/api.ts:71-74` | PLAN's claim that "the engine trusts X-User-Id" is not true at this commit. I add an *optional* actor header used only for `created_by` (§5.7). Tests send exactly the studio's headers. |
| Every run executes the **draft** definition. `publish` only snapshots the draft and enables triggers. | `repositories/workflow_repository.py:85-90,216` (`_to_stored_workflow` reads `draft_definition`); the docstring at `services/workflow_service.py:276-277` is wrong | Releases (phase 9) must load pinned `workflow_versions` explicitly. Nothing else changes. |
| Team is always `"default"`. Workflows and apps never set `team_id`. | `models.py:124,173`; `workflow_repository.py:66-74` | Projects hang off team `default`. Projects add no multi-tenancy. |
| `$vars` are the only secrets that nodes consume. No node reads `credentialId`/`_credentials`, but the runner will decrypt **any** credential id it is given, with no ownership check. | `engine/workflow_runner.py:597-616,962-974`; `grep _credentials src/nodes` finds nothing | The broker accepts secrets only through bare `{{ $vars.KEY }}` and rejects `credentialId`. The runner gains a team check. |
| No node sets `context.webhook_response`, so there is no "respond" node. | `engine/types.py:100`; read only at `services/webhook_service.py:265` | Phase 5 adds a `Respond` node. Webhooks in `lastNode` mode get it for free. |
| `data_tables`/`data_table_rows` exist but have no repository, routes or node. | `models.py:408-439`; no route references them | Phase 7 builds these from scratch on the existing tables. |
| Code node uses `exec` in-process on a thread pool, with `io`/`pandas` reachable. The thread is not killed on timeout. The same pattern appears in the agent's code tool and its `run_script` (PTC). | `nodes/data/code.py:203-214`; `nodes/ai/tools/code_tool.py:153`; `nodes/ai/ai_agent.py:1159` | Phase 8 moves Code and CodeTool into the sandbox. PTC is listed as a risk (§8). |
| `bwrap` 0.11.2 works without root. With `--unshare-all --clearenv` the sandbox has no network ("Network is unreachable"), no `$HOME`, and only `PATH`/`PWD` in the environment. A host Unix socket bind-mounted in is reachable. Startup costs about 17 ms per process. AF_UNIX paths must be ≤108 bytes, so socket dirs go under `/tmp/sbx-*`. | Run in this session (§5.5) | The sandbox design is proven on this machine. |
| The DB role `workflow` is SUPERUSER/CREATEDB on :5433. Databases present: `postgres`, `workflows`. | `pg_roles` query | Tests create a throwaway `workflows_test_<rand>` database. They never touch `workflows`. |
| `session.py` builds the async engine at import time from `WORKFLOW_DB_*`, with schema hardcoded as `"workflow-app"`. | `db/session.py:18-32` | Isolation is done by **database name**, not schema. The test server is a separate process with its own env. |
| The published runtime rewrites `/api/...` to `/a/{slug}/api` + url, giving `/a/{slug}/api/api/...`. That never matches the only proxy route, `/a/{slug}/api/workflow/{id}/run`. | `routes/public_apps.py:132,151-156,373` | Fixed in phase 3 (§6). |
| No `pytest-asyncio`. `anyio`, `httpx` and `pytest` are installed. `esbuild` is on PATH. | `pip list`; `which esbuild` | The harness uses a uvicorn subprocess plus sync `httpx`. No new dependencies are needed. |

---

## 1. Must-not-break inventory (what the studio calls today)

Engine handlers are listed as `file:line`. Studio call sites are under `studio:`. The base URL is
`http://localhost:8000` (`studio:shared/lib/config.ts:16-52`). `apiFetch` prefixes `/api`
(`studio:shared/lib/api.ts:67`).

| Method | Path | Engine handler | Studio call site | Body / notes |
|---|---|---|---|---|
| GET | `/api/workflows` | `routes/workflows.py:37` | `api.ts:91` ← `features/projects/hooks/useWorkflows.ts:56` | Followed by N+1 `GET /{id}` calls |
| GET | `/api/workflows/{id}` | `routes/workflows.py:46` | `api.ts:95`; direct `fetch` at `app/routes/editor.tsx:134`; `useWorkflowApi.ts:145` | |
| POST | `/api/workflows` | `routes/workflows.py:58` | `api.ts:99` ← `useWorkflowApi.ts:33,115`, `useWorkflowActions.ts:66` | `BackendWorkflow` (name, nodes, connections, settings) |
| PUT | `/api/workflows/{id}` | `routes/workflows.py:70` | `api.ts:106` ← `useWorkflowApi.ts:41` | Editor save |
| DELETE | `/api/workflows/{id}` | `routes/workflows.py:85` | `api.ts:113` ← `useWorkflowActions.ts:85` | |
| POST | `/api/workflows/{id}/publish` | `routes/workflows.py:98` | `api.ts:119` ← `useWorkflowApi.ts:295` | `{message?}` |
| POST | `/api/workflows/{id}/unpublish` | `routes/workflows.py:114` | `api.ts:126` ← `useWorkflowApi.ts:302` | |
| POST | `/api/workflows/{id}/run` | `routes/workflows.py:151` | `api.ts:132` ← `useWorkflowActions.ts:26` | No body |
| POST | `/api/workflows/run-adhoc` | `routes/workflows.py:168` | `api.ts:138` ← `useWorkflowApi.ts:183` | |
| POST | `/execution-stream/{workflowId}` (SSE) | `routes/streaming.py:303` | `useExecutionStream.ts:117,134` | `{input_data, environment}` |
| POST | `/execution-stream/adhoc` (SSE) | `routes/streaming.py:164` | `useExecutionStream.ts:126,134` | `{...workflow, input_data, environment}` |
| GET | `/api/apps` | `routes/apps.py:21` | `api.ts:219` ← `useApps.ts:21` | |
| GET | `/api/apps/{id}` | `routes/apps.py:37` | `api.ts:223` ← `AppBuilderShell.tsx:109`, `AppBuilderChatPanel.tsx:157`, `PublishDialog.tsx:49,82`, `useAppActions.ts:153` | |
| POST | `/api/apps` | `routes/apps.py:29` | `api.ts:227` ← `useAppActions.ts:101,218` | `{name, definition}` |
| PUT | `/api/apps/{id}` | `routes/apps.py:48` | `api.ts:252` ← `AppBuilderShell.tsx:85,137`, `AppBuilderChatPanel.tsx:173`, `useAppActions.ts:223` | Partial body: rename, save with `create_version`, `api_execution_ids`, import |
| DELETE | `/api/apps/{id}` | `routes/apps.py:60` | `api.ts:259` ← `useAppActions.ts:134` | |
| POST | `/api/apps/{id}/publish` | `routes/apps.py:70` | `api.ts:265` ← `PublishDialog.tsx:44` | `{slug?, access, access_password?}` |
| POST | `/api/apps/{id}/unpublish` | `routes/apps.py:97` | `api.ts:272` ← `PublishDialog.tsx:81` | |
| GET | `/api/apps/{id}/versions` | `routes/apps.py:114` | `api.ts:280` ← `VersionHistory.tsx:48` | |
| POST | `/api/apps/{id}/versions/{vid}/revert` | `routes/apps.py:165` | `api.ts:298` ← `AppBuilderShell.tsx:160` | |
| PATCH | `/api/apps/{id}/versions/{vid}` | `routes/apps.py:177` | `api.ts:304` ← `VersionHistory.tsx:63` | `{label}` |
| GET / POST | `/api/apps/{id}/versions/{vid}`, `/api/apps/{id}/versions` | `routes/apps.py:153,122` | `api.ts:284,291` (defined, unused) | Kept |
| GET | `/api/executions` | `routes/executions.py:22` | `api.ts:318` ← `useLatestExecutions.ts:11` | |
| GET | `/api/nodes` | `routes/nodes.py:20` | `api.ts:328` ← `useNodeTypes.ts:19` | New nodes appear here automatically |
| POST | `/api/api-tester/execute` | `routes/api_tester.py:82` | `api.ts:397` ← `ApiTesterPanel.tsx:433` | |
| GET / GET / PATCH / DELETE | `/api/api-tester/executions[/{id}]` | `routes/api_tester.py:179,185,193,205` | `api.ts:405,409,413,420` | |
| POST | `/api/ai/chat` (SSE) | `routes/ai_chat.py:21` | `useAIChat.ts:79` | Needs an LLM |
| POST | `/api/ai/app-builder/chat` (SSE) | `routes/app_builder_ai.py:21` | `useAppBuilderChat.ts:49` | `{message, app_id?, current_version_id?, api_execution_ids, conversation_history}` |
| GET | `/api/variables?environment=` | `routes/variables.py:53` | `useVariablesApi.ts:42-44` | Secret values come back as `null` (`:66`) |
| GET | `/api/variables/environments` | `routes/variables.py:24` | `useVariablesApi.ts:54` | |
| POST | `/api/variables` | `routes/variables.py:32` | `useVariablesApi.ts:64` | `{team_id:'default', key, value, environment, type}` |
| PUT | `/api/variables/{id}` | `routes/variables.py:81` | `useVariablesApi.ts:84` | Always sends `description:null`, because the schema requires the field (`schemas/variable.py:17`) |
| DELETE | `/api/variables/{id}` | `routes/variables.py:95` | `useVariablesApi.ts:101` | |
| GET | `/api/files/browse` | `routes/files.py:34` | `FilePathField.tsx:59` (relative URL; goes to Vite in dev) | Existing bug, out of scope |
| any | generated app `fetch` | the URL itself | `IframeSandbox.tsx:108-114` (preview bridge) | Relative URLs get `backends.workflow` prefixed |

Not called by the studio, but kept for external callers: `/webhook/{id}` and `/webhook/p/{path}` for all
methods (`routes/webhooks.py:172-273`; URLs shown at `NodeExtensions.tsx:36,38`), `/a/{slug}`,
`/_assets`, `/_unlock`, `/api/workflow/{id}/run`, and `/api-tester/replay/{id}` (`routes/public_apps.py:247-454`).
Also kept: credentials, folders, executions detail/cancel/retry/clear, workflow versions,
`/execution-stream/{id}` GET, and `/health`.

**Rule:** every row above keeps its path, method, request shape and response keys. New response
fields are additive only.

---

## 2. Data model

### 2.1 Conventions
- New IDs use `generate_id(prefix)` (`utils/ids.py:52`) with prefixes `proj`, `route`, `key`, `rel`,
  `mod`, `bcall`. The Default project uses the fixed ID **`proj_default`**, so backfills and column
  defaults are plain constants.
- Migrations go in `src/db/migrations/2026101x…_*.sql`. Each has a `-- migrate:down` section and is
  applied by `python -m src.db.migrate` (`db/migrate.py:118`).
- **Every migration is safe for the currently running old engine.** New `project_id` columns are
  `NOT NULL DEFAULT 'proj_default'`, so the old code's INSERTs still succeed. No column is renamed or dropped.
  That means migrations can be applied to the :8000 database before the new code is deployed. I will
  do that only with the owner's OK; the tests never do it.
- ORM models in `db/models.py` gain the same columns. `db/__init__.py` exports the new models.

### 2.2 Migrations, by phase

**M1 `20261015090000_projects.sql` (phase 1)**
```sql
CREATE TABLE projects (
  id TEXT PRIMARY KEY, team_id TEXT NOT NULL REFERENCES teams(id),
  name TEXT NOT NULL, slug TEXT NOT NULL, description TEXT,
  environments JSONB NOT NULL DEFAULT '[{"name":"dev","kind":"draft"},{"name":"prod","kind":"release"}]',
  default_environment TEXT NOT NULL DEFAULT 'dev',   -- used by studio runs, cron, previews
  live_environment    TEXT NOT NULL DEFAULT 'prod',  -- what /a/{slug} and the router serve by default
  settings JSONB NOT NULL DEFAULT '{}',              -- {"egress_allowlist": ["api.stripe.com","*.example.com"]}
  created_by TEXT, created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now());
CREATE UNIQUE INDEX idx_projects_team_slug ON projects (team_id, lower(slug));

-- Default project: environments = every environment that already has variables, plus 'default'.
-- Both default_environment and live_environment are 'default', which is exactly today's behaviour.
INSERT INTO projects (id, team_id, name, slug, environments, default_environment, live_environment)
SELECT 'proj_default','default','Default project','default',
       (SELECT jsonb_agg(jsonb_build_object('name',e,'kind','draft') ORDER BY e)
          FROM (SELECT 'default' AS e UNION SELECT DISTINCT environment FROM variables) s),
       'default','default'
ON CONFLICT DO NOTHING;

ALTER TABLE workflows   ADD COLUMN project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE apps        ADD COLUMN project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE data_tables ADD COLUMN project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE folders     ADD COLUMN project_id TEXT NOT NULL DEFAULT 'proj_default' REFERENCES projects(id);
ALTER TABLE executions  ADD COLUMN project_id TEXT, ADD COLUMN environment TEXT;     -- no FK: history outlives rows
UPDATE executions e SET project_id = w.project_id FROM workflows w WHERE w.id = e.workflow_id;
UPDATE executions SET project_id = 'proj_default' WHERE project_id IS NULL;
ALTER TABLE variables   ADD COLUMN project_id TEXT REFERENCES projects(id),           -- NULL = team-shared
                        ADD COLUMN allowed_hosts JSONB;                               -- phase 4 policy
-- Existing variables stay team-shared (project_id NULL). They are NOT moved into the Default project,
-- so resolution for existing data is unchanged.
DROP INDEX IF EXISTS idx_variables_team_env_key;
CREATE UNIQUE INDEX idx_variables_team_scope ON variables (team_id, environment, key) WHERE project_id IS NULL;
CREATE UNIQUE INDEX idx_variables_project_scope ON variables (project_id, environment, key) WHERE project_id IS NOT NULL;
DROP INDEX IF EXISTS idx_data_tables_team_name;
CREATE UNIQUE INDEX idx_data_tables_project_name ON data_tables (project_id, name);
CREATE INDEX ix_workflows_project ON workflows (project_id);
CREATE INDEX ix_apps_project ON apps (project_id);
CREATE INDEX ix_folders_project ON folders (project_id);
CREATE INDEX ix_executions_project_start ON executions (project_id, start_time DESC);
```
The down section drops the columns and indexes, recreates `idx_variables_team_env_key` and
`idx_data_tables_team_name`, and drops `projects`. It refuses (`RAISE EXCEPTION`) if any
project-scoped variable rows exist, because those would collide.

**M2 `…_broker_calls.sql` (phase 4)**: `broker_calls(id TEXT PK, execution_id TEXT, project_id TEXT,
environment TEXT, origin TEXT, node_type TEXT, params_redacted JSONB, status TEXT /* ok|denied|error */,
error_code TEXT, error TEXT, duration_ms REAL, created_at TIMESTAMP)`, plus index `(execution_id)` and
`(project_id, created_at DESC)`. `retention_cleaner` (`engine/retention_cleaner.py`) also prunes it.

**M3 `…_api_routes.sql` (phase 5)**
```sql
CREATE TABLE api_routes (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL,                       -- operationId, camelCase, unique per project
  method TEXT NOT NULL CHECK (method IN ('GET','POST','PUT','PATCH','DELETE')),
  path TEXT NOT NULL,                       -- '/orders/{id}'; leading '/', no trailing '/'
  path_shape TEXT NOT NULL,                 -- '/orders/{}'; used for conflict detection
  auth TEXT NOT NULL DEFAULT 'none' CHECK (auth IN ('none','app_token','api_key')),
  description TEXT,
  request_schema JSONB, response_schema JSONB,   -- JSON Schema; request = {params,query,body}
  response_mode TEXT NOT NULL DEFAULT 'auto' CHECK (response_mode IN ('auto','first_item','all_items')),
  handler_type TEXT NOT NULL CHECK (handler_type IN ('workflow','python','stub')),
  handler_workflow_id TEXT REFERENCES workflows(id),
  handler_module_id TEXT, handler_function TEXT,   -- FK to code_modules is added in M5
  source TEXT NOT NULL DEFAULT 'ui' CHECK (source IN ('ui','code','app_stub')),
  timeout_s INT NOT NULL DEFAULT 30,
  created_by TEXT, created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CHECK ((handler_type='workflow') = (handler_workflow_id IS NOT NULL)));
CREATE UNIQUE INDEX idx_api_routes_shape ON api_routes (project_id, method, path_shape);
CREATE UNIQUE INDEX idx_api_routes_name  ON api_routes (project_id, name);
CREATE TABLE api_keys (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  environment TEXT NOT NULL, name TEXT NOT NULL, prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  created_by TEXT, created_at TIMESTAMP NOT NULL DEFAULT now(), last_used_at TIMESTAMP, revoked_at TIMESTAMP);
CREATE TABLE api_spec_revisions (id SERIAL PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  revision INT NOT NULL, spec_hash TEXT NOT NULL, spec JSONB NOT NULL, changes JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMP NOT NULL DEFAULT now(), UNIQUE (project_id, revision));
```

**M4 `…_data_rows_index.sql` (phase 7)**: `CREATE INDEX idx_data_table_rows_gin ON data_table_rows USING GIN (data jsonb_path_ops);`
and `CREATE INDEX idx_data_table_rows_table_created ON data_table_rows (table_id, id DESC);`

**M5 `…_code_modules.sql` (phase 8)**: `code_modules(id TEXT PK, project_id TEXT NOT NULL REFERENCES projects(id),
name TEXT NOT NULL /* python module name, e.g. orders */, source TEXT NOT NULL, source_sha TEXT NOT NULL,
created_by, created_at, updated_at, UNIQUE(project_id, name))`, plus
`ALTER TABLE api_routes ADD CONSTRAINT fk_route_module FOREIGN KEY (handler_module_id) REFERENCES code_modules(id)`.

**M6 `…_releases.sql` (phase 9)**
```sql
CREATE TABLE releases (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  environment TEXT NOT NULL, number INT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL CHECK (status IN ('active','superseded','rolled_back','blocked')),
  snapshot JSONB NOT NULL, checks JSONB NOT NULL DEFAULT '[]', source_release_id TEXT REFERENCES releases(id),
  notes TEXT, created_by TEXT, created_at TIMESTAMP NOT NULL DEFAULT now(), UNIQUE (project_id, environment, number));
CREATE UNIQUE INDEX idx_releases_active ON releases (project_id, environment) WHERE is_active;
```

### 2.3 Repository and query changes
| Where | Change |
|---|---|
| `engine/types.py:172` `StoredWorkflow` | Add `project_id: str = "proj_default"`. `workflow_repository._to_stored_workflow` (`:210`) sets it. |
| `workflow_repository.py:30 create / :92 list / :101 update` | `create(…, project_id=None)` (None → DB default). `list(folder_id, project_id=None)`. `update` accepts `project_id` (move). Extract the definition→`Workflow` code (`:216-262`) into `definition_to_workflow(definition, name, id, description)` so releases can rebuild a `Workflow` from a `workflow_versions.definition`. |
| `services/app_service.py:63 list_apps / :95 create_app / :113 update_app` | Same `project_id` filter, set and move. `AppListItem`/`AppDetailResponse` gain `project_id`. `get_app_by_slug` is unchanged. |
| `variable_repository.py` | `list(team_id, environment, project_id=None, include_team=True)`. `get_by_key(team_id, key, environment, project_id=None)` matches the scope exactly (`project_id IS NULL` when None). `load_runtime_map(team, env, project_id=None)` = team rows (`project_id IS NULL`) overlaid by project rows, **project wins**. `load_runtime_secret_values` returns the **union** of team and project secret values, including shadowed ones, so redaction is strict. `update()` gains `allowed_hosts`. |
| `services/variable_loader.py:9,22` | Add a `project_id: str \| None = None` parameter. With None the result is team-only, byte-for-byte what it returns today. |
| `execution_repository.py:27 start` | Add `project_id=None, environment=None` and write both columns. `list(workflow_id, project_id=None, mode=None)`. |
| `engine/types.py:63` `ExecutionContext` | Add `project_id: str \| None`, `environment: str \| None`, `team_id: str = "default"`. `WorkflowRunner.run` (`workflow_runner.py:51`) gains matching kwargs and sets them on the context. Defaults keep every existing call valid. |
| `workflow_runner.py:962` `_resolve_credential` | Load the credential and return None unless `cred.team_id == context.team_id`. This is the ownership fix. |
| All 8 `load_run_variables` call sites | Pass the workflow's `project_id`: `workflow_service.py:287,351`, `streaming.py:211,284,323`, `webhook_service.py:119`, `cron_scheduler.py:92`, `execution_service.py:150`. For Default-project workflows the merged map equals today's map, because the Default project has no project-scoped rows until a user creates some. |

---

## 3. API contract

Shared conventions:
- `{project}` in a path accepts a project ID (`proj_…`) **or** a slug.
- Errors from new endpoints use `{"detail": {"code": "...", "message": "...", ...}}` (FastAPI's
  `HTTPException(detail=dict)`). Existing endpoints keep their string `detail`. Codes are stable strings
  that tests assert on.
- Status codes: 400 malformed, 401 missing/invalid credential, 403 not allowed, 404 not found
  (never revealing existence across projects), 409 conflict, 422 validation (FastAPI default plus
  domain codes), 501 stub, 502 handler failure, 503 sandbox unavailable, 504 timeout.

### 3.1 Phase 1: projects

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/projects` | → `200 [ProjectListItem]` |
| POST | `/api/projects` | `{name, slug?, description?}` → `201 Project`. Slug is derived with `services/slug_utils.py` when absent. Errors: `409 slug_taken`, `422`. |
| GET | `/api/projects/{project}` | → `200 Project` / `404` |
| PUT | `/api/projects/{project}` | `{name?, description?, slug?, environments?, default_environment?, live_environment?, settings?}` → `200 Project`. Removing an environment that still has secrets or API keys → `409 environment_in_use`. `default_environment`/`live_environment` must be in `environments` → `422`. |
| DELETE | `/api/projects/{project}` | → `200 {"success":true}`. Returns `409 project_not_empty` if it has workflows, apps, routes or tables, and `409 default_project` for `proj_default`. |
| GET | `/api/projects/{project}/summary` | → `200 {counts, last_run, health, missing_secrets}` |
| GET | `/api/projects/{project}/workflows` | → `200 [WorkflowListItem]` (same shape as `/api/workflows`, plus `project_id`) |
| POST | `/api/projects/{project}/workflows` | `WorkflowCreateRequest` → `201 WorkflowResponse` |
| GET | `/api/projects/{project}/apps` | → `200 [AppListItem]` |
| POST | `/api/projects/{project}/apps` | `AppCreateRequest` → `201 AppDetailResponse` |
| GET | `/api/projects/{project}/runs?limit=50&mode=&status=` | → `200 [ExecutionListItem + environment, mode, trigger]` |
| GET | `/api/projects/{project}/runs/{execution_id}` | → `ExecutionDetailResponse + {trace}` (`trace` filled from phase 4 on) |

Additive changes to existing endpoints (all optional):
- `GET /api/workflows?project_id=` and `GET /api/apps?project_id=`. Without the parameter they list
  everything, as today.
- `WorkflowCreateRequest`, `WorkflowUpdateRequest`, `AppCreateRequest` and `AppUpdateRequest` gain
  `project_id?`. A move is refused with `409 workflow_in_use` if a route in another project references the workflow.
- Run/stream request bodies gain `project_id?`, used for adhoc runs only. Saved workflows take the
  project from their row. Every workflow and app response gains `project_id`.

```json
// Project
{"id":"proj_0195…","name":"Orders","slug":"orders","description":null,
 "environments":[{"name":"dev","kind":"draft"},{"name":"prod","kind":"release"}],
 "default_environment":"dev","live_environment":"prod","settings":{"egress_allowlist":[]},
 "created_at":"2026-10-15T09:00:00","updated_at":"2026-10-15T09:00:00"}
// ProjectListItem = Project + counts/last_run/health
{"…": "…", "counts":{"apps":2,"workflows":5,"routes":7,"tables":1},
 "last_run":{"execution_id":"exec_…","status":"success","mode":"api","start_time":"…"},
 "health":{"status":"ok","runs_24h":40,"failed_24h":1}}   // ok | degraded (>5% failed) | failing (>50%) | unknown (no runs)
```

### 3.2 Phase 2: secrets

Resolution order for a run in project P, environment E: team rows `(team, E, project_id NULL)`, then
project rows `(P, E)`. On a key collision **the project row wins**. Environments never fall back to
each other (same as today). Values of `type="secret"` are write-only on every endpoint
(`routes/variables.py:66` behaviour preserved).

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/projects/{project}/secrets?environment=` | → `200 [EffectiveVar]` (team and project rows merged) |
| POST | `/api/projects/{project}/secrets` | `{key, value, environment, type:"secret"\|"string"\|"number", description?, allowed_hosts?}` → `201 EffectiveVar`. Errors: `409 exists` (same scope), `422 unknown_environment`, `422 invalid_key` (`^[A-Za-z_][A-Za-z0-9_]*$`, the same grammar as `vars_substitution._VARS_RE`). |
| PUT | `/api/projects/{project}/secrets/{id}` | `{value?, description?, allowed_hosts?}`, all truly optional (no null-clobber) → `200`. A team row ID → `404` (team rows are edited through `/api/variables`). |
| DELETE | `/api/projects/{project}/secrets/{id}` | → `200 {"success":true}` |
| GET | `/api/projects/{project}/secrets/missing?environment=` | → `200 [{key, missing_in:["prod"], referenced_by:[{kind:"workflow",id,name,node}\|{kind:"module",id,name,line}]}]` |

```json
// EffectiveVar
{"id":12,"key":"STRIPE_KEY","environment":"dev","type":"secret","value":null,"description":null,
 "scope":"project","overrides_team":true,"overridden":false,"allowed_hosts":["api.stripe.com"],"updated_at":"…"}
```
`missing` scans the project's workflow `draft_definition` parameters, and (from phase 8) module source,
for `$vars.KEY` using `vars_substitution._VARS_RE` plus a looser `\$vars\.(\w+)` pattern for
expressions. It reports each key absent from the merged map of any project environment. The inline
"create `$vars.X`?" prompt is the UI calling POST with the key, triggered either from this list or
from a broker `missing_secret` error (§3.4).

`/api/variables` is unchanged. Its list and duplicate check are scoped to `project_id IS NULL`, so the
editor's Variables modal keeps showing and editing team-shared variables exactly as before.

### 3.3 Phase 3: published apps run real workflows

`POST /a/{slug}/api/workflow/{workflow_id}/run`. Auth, token scope and the allowlist stay exactly as
in `routes/public_apps.py:373-397`.

- Body: any JSON object, used as `input_data`.
- `200 {"status":"success"|"failed","execution_id":"exec_…","data":[{…},…],"errors":[{"node_name":"…","error":"…"}]}`.
  `data` holds the last-completed node's items as plain JSON. If a `Respond` node (phase 5) ran, its
  status, headers and body are returned instead.
- Errors: `401 missing token/token scope mismatch`, `403 workflow not allowed by this app`, `404`,
  `504 {"status":"timeout"}` after 120 s.
- The run happens through a new `ExecutionService.run_saved_workflow(workflow_id, input_data, *,
  environment, project_id, mode)` (§4). The workflow must belong to the app's project, otherwise
  `403 workflow_not_in_project`. Environment = the app's project `live_environment`, which is
  `default` for the Default project.
- **Deliberate difference:** caller payloads from the public surface are *not* passed through
  `substitute_vars`, because an anonymous visitor must not be able to have `{{ $vars.X }}` expanded.
  The response `data` and `errors` go through `redact_deep` (§5.3).
- Runtime fix (`public_apps.py:151`): `/api/workflow/…` now maps to `/a/{slug}/api/workflow/…` (the
  leading `/api` is stripped). `/api/p/…` (phase 6) is passed through unchanged to the same origin
  with `Authorization: Bearer <app token>`. `/webhook/…` and other `/api/…` URLs behave as today.

### 3.4 Phase 4: nodes as functions (SDK + broker)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/sdk/functions` | `200 [{function:"http_request", node_type:"HttpRequest", description, parameters:<JSON Schema>, returns:{type:"array",items:{type:"object"}}, egress:true}]`. This is the catalogue the AI agent and UI docs use. |
| GET | `/api/sdk/python` | `200 {"version":"<sha256 of registry>","files":{"studio/__init__.py":"…","studio/nodes.py":"…",…}}` |
| GET | `/api/sdk/python.zip` | Zip of the same files, for local use |
| POST | `/api/broker/call` | `Authorization: Bearer brk_…` (see below) |
| POST | `/api/projects/{project}/broker-tokens` | `{environment}` → `201 {"token":"brk_…","expires_at":"…"}`. Developer token (TTL 1 h) for running the SDK from a laptop. Only allowed for `kind:"draft"` environments; otherwise `403`. |
| GET | `/api/projects/{project}/broker-calls?execution_id=` | Call log, with parameters already redacted |

`POST /api/broker/call`
```json
// request
{"function":"http_request",             // or "node":"HttpRequest"
 "parameters":{"method":"GET","url":"https://api.stripe.com/v1/charges",
   "headers":[{"name":"Authorization","value":"Bearer {{ $vars.STRIPE_KEY }}"}]},
 "items":[{}]}                          // input items' json; default [{}]
// 200
{"ok":true,"call_id":"bcall_…","items":[{"statusCode":200,"headers":{…},"body":{…}}],"metadata":{"responseStatusCode":200}}  // CHANGED (§11): real HttpRequest item shape
// error (HTTP status in brackets)
{"ok":false,"call_id":"bcall_…","error":{"code":"host_not_allowed","message":"…","host":"evil.com"}}
```
| Code | HTTP | When |
|---|---|---|
| `invalid_token` | 401 | Token missing, unknown, revoked or expired |
| `function_not_allowed` | 403 | Node not function-exposed, or not in the token's scope |
| `credential_not_allowed` | 422 | Parameters contain `credentialId` |
| `secret_expression_not_allowed` | 422 | `$vars` used anywhere except as a bare `{{ $vars.KEY }}` |
| `missing_secret` | 422 | `{"key":"STRIPE_KEY","environment":"dev"}`, which drives the inline-create prompt |
| `secret_not_bound` | 403 | The secret has no `allowed_hosts`, or is used in a node with no egress hook |
| `host_not_allowed` | 403 | Target host not in the project allowlist or the secret's `allowed_hosts` |
| `call_limit_exceeded` | 429 | More than `max_calls` per token (default 500) |
| `node_failed` | 502 | The node raised. The message is redacted. |

Python surface (generated, §4.3):
```python
import studio
r = studio.nodes.http_request(method="GET", url="https://api.stripe.com/v1/charges",
                              headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('STRIPE_KEY')}"}])
# studio.secret('K') returns a SecretRef whose str() is '{{ $vars.K }}'; the value never enters user code.
rows = studio.db.table("orders").list(filter={"status": "open"})     # phase 7
```

### 3.5 Phase 5: API section

Route management (phase 5):

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/projects/{project}/routes` | → `200 [Route]` |
| POST | `/api/projects/{project}/routes` | `RouteCreate` → `201 {route, spec_revision, breaking_changes:[]}`. Errors: `409 route_conflict` (same method + path shape), `409 name_taken`, `422 invalid_path`, `422 handler_not_in_project`. |
| GET | `/api/projects/{project}/routes/{rid}` | → `200 Route` |
| PUT | `/api/projects/{project}/routes/{rid}?dry_run=false` | Partial `RouteCreate` → `200 {route, spec_revision, breaking_changes}`. With `dry_run=true` nothing is saved. Breaking changes **warn**; they do not block saving. |
| DELETE | `/api/projects/{project}/routes/{rid}` | → `200 {spec_revision, breaking_changes}` |
| POST | `/api/projects/{project}/routes/{rid}/test` | Body below; runs the handler in `default_environment` (draft) |
| GET | `/api/projects/{project}/openapi.json?environment=` | OpenAPI 3.1 for that environment. Draft environments use live rows; release environments use the active release snapshot (phase 9). |
| GET | `/api/projects/{project}/spec/revisions` | `[{revision, spec_hash, created_at, changes}]` |
| GET | `/api/projects/{project}/spec/diff?from=&to=` | `{changes:[Change]}` |
| GET / POST / DELETE | `/api/projects/{project}/api-keys[/{id}]` | POST `{name, environment}` → `201 {id,name,environment,prefix:"sk_dev_a1b2",key:"sk_dev_a1b2…",created_at}`. `key` is returned **once**; GET never includes it. DELETE sets `revoked_at`. |

```json
// RouteCreate
{"method":"GET","path":"/orders/{id}","name":"getOrder","auth":"app_token","description":"…",
 "request_schema":{"type":"object","properties":{"params":{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]},"query":{…},"body":{…}}},
 "response_schema":{"type":"object","properties":{"id":{"type":"string"},"total":{"type":"number"}}},
 "response_mode":"auto",
 "validate_request":false,            // CHANGED (decision 4, §9): opt-in runtime validation, default false
 "handler":{"type":"workflow","workflow_id":"wf_…"}   // | {"type":"workflow","create":true} | {"type":"stub"} | {"type":"python","module_id":"mod_…","function":"get_order"}
}
// Route = RouteCreate + {id, project_id, source:"ui"|"code"|"app_stub", used_by_count, created_at, updated_at, url:"/api/p/orders/orders/{id}"}
// Change (breaking-change diff)
{"kind":"response_field_removed","route":"GET /orders/{id}","operation":"getOrder","field":"total","breaking":true,
 "affected":[{"app_id":"app_…","app_name":"Orders UI","file":"src/OrderView.tsx","line":42}]}
```
- `handler.create:true` creates a workflow in the project named `"<METHOD> <path>"`, with a `Start` node
  wired to a `Respond` node that returns `{{ $json.req.params }}`, so a new route works immediately.
- `name` defaults to camelCase of method plus path (`GET /orders/{id}` → `getOrdersById`), deduplicated with a numeric suffix.
- Path grammar: `^(/([A-Za-z0-9._~-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))+$` or `/`.
- Diff rules: route removed; method or path changed; auth changed to stricter; required request field
  added; request or response field type changed; response field removed. All of these are breaking.
  Optional additions are non-breaking.

Test-run and schema inference:
```json
// POST …/routes/{rid}/test
{"params":{"id":"42"},"query":{},"headers":{},"body":null,"environment":"dev","apply_schemas":false}
// 200
{"status":200,"headers":{"content-type":"application/json"},"body":{"id":"42","total":9.5},"duration_ms":31.2,
 "execution_id":"exec_…","inferred":{"request_schema":{…},"response_schema":{"type":"object","properties":{"id":{"type":"string"},"total":{"type":"number"}}}},
 "applied":false}
```
Inference uses `infer_json_schema` (`services/schema_inference.py:23`). It is applied to the
`{params, query, body}` that was sent and to the response body. `apply_schemas:true` saves both schemas
through the normal PUT path, so a spec revision and diff are produced.

**Project router (phase 5): `ANY /api/p/{project}/{path:path}`**
1. Resolve the project by slug or ID → `404 {"error":"project_not_found"}`.
2. Authenticate:
   - API key (`Authorization: Bearer sk_…` or `X-API-Key`) → that key's project and environment.
   - App token (`Bearer <public token>`) → the app must be in this project. The token must be for the
     app's published version, or be a preview token. Environment = `live_environment`, or
     `default_environment` for preview tokens.
   - No credential → `live_environment`.
   - **The caller can never choose the environment.**
3. Match `(method, path)` against the environment's route set (§4.4).
   - No path match → `404 {"error":"route_not_found"}`.
   - Path matches but method does not → `405 {"error":"method_not_allowed"}` with an `Allow` header.
   - The route's `auth` must be satisfied by the credential from step 2, otherwise `401 {"error":"unauthorized"}`.
     `api_key` requires a key; `app_token` requires an app token **or** a key; `none` requires nothing.
4. Dispatch to the handler:
   - `stub` → `501 {"error":"not_implemented","route":"GET /orders/{id}","operation":"getOrder"}`.
   - `workflow` → run through `ExecutionService.run_saved_workflow(mode="api")` with the input item below.
     If a `Respond` node ran, its `WebhookResponse` is used. Otherwise the response is `200` JSON shaped
     by `response_mode`: `auto` returns an object for exactly 1 item and an array otherwise;
     `first_item` returns an object or `null`; `all_items` returns an array.
   - `python` → sandbox (phase 8).
   - Any handler exception → `502 {"error":"handler_failed","execution_id":"…"}`.
   - Timeout (`timeout_s`) → `504 {"error":"timeout","execution_id":"…"}`.
5. The response is built with the existing `_build_response` (`routes/webhooks.py:25`), moved to
   `services/http_response.py`. JSON bodies pass through `redact_deep`. The response carries an
   `X-Execution-Id` header.

Input item given to the handler workflow. Top-level aliases keep it compatible with webhook-style
expressions (`webhook_service.py:126-135`):
```json
{"req":{"method":"GET","path":"/orders/42","route":"/orders/{id}","params":{"id":"42"},"query":{},
        "headers":{"user-agent":"…"},"body":null},
 "params":{"id":"42"},"query":{},"headers":{…},"body":null,"method":"GET","triggeredAt":"…"}
```
`authorization`, `x-api-key` and `cookie` are stripped from `headers`. Inbound payloads are **not**
`$vars`-substituted (deliberate; same reason as §3.3). In v1, request schemas are a contract and
documentation; they are **not enforced** at runtime unless the route has `validate_request: true`
(**CHANGED**, decision 4 in §9). Release checks enforce consistency (§3.9).

### 3.6 Phase 6: app builder ↔ API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects/{project}/client.ts?environment=` | `200 text/typescript`, the typed client. Only `none` and `app_token` routes are included. **`api_key` routes are never put in browser code.** |
| POST | `/api/projects/{project}/apps/{app_id}/preview-token` | → `201 {"token":"…","expires_at":"…","environment":"dev"}`. Draft-environment token for the studio preview iframe. |
| GET | `/api/apps/{app_id}/api-usage` | → `200 [{operation,method,path,file,line,route_id,status:"ok"\|"missing"\|"changed"}]` |
| GET | `/api/projects/{project}/routes/{rid}/used-by` | → `200 [{app_id,app_name,version_id,file,line,operation}]` |
| POST | `/api/projects/{project}/routes/stubs` | `{"app_id?":"app_…","routes":[{method,path,name?,description?,request_schema?,response_schema?}]}` → `201 {"created":[Route],"existing":[Route]}`. Idempotent on method + path shape. Stubs have `source:"app_stub"`. |
| POST | `/api/ai/app-builder/chat` (changed, additive) | Body gains `project_id?` and `route_ids?` (focus routes for "generate UI for this API"). If the app or project is known, the system prompt gets the spec summary and client usage (replacing the single attached endpoint at `services/app_builder_ai_service.py:798`, which stays for `api_execution_ids`), and the agent gets a `declare_endpoint` tool that calls the stub service. |

Generated client (deterministic from the spec):
```ts
// @generated by studio from spec rev 7 (sha …) — do not edit
export interface GetOrderResponse { id: string; total: number }
export class ApiError extends Error { constructor(public status: number, public body: unknown) { super(`HTTP ${status}`) } }
const BASE = '/api/p/orders'
async function call<T>(method: string, path: string, opts: {query?: Record<string, unknown>; body?: unknown} = {}): Promise<T> {
  const token = (window as any).__APP_TOKEN
  const res = await fetch(BASE + path + qs(opts.query), {method,
    headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})},
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body)})
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text()
  if (!res.ok) throw new ApiError(res.status, data)
  return data as T
}
export const api = {
  /** GET /orders/{id} */ getOrder: (params: {id: string}) => call<GetOrderResponse>('GET', `/orders/${encodeURIComponent(params.id)}`),
}
```
- The file lives at `api/client.ts` in app files. It is regenerated at publish time
  (`AppService._files_for_publish`, `app_service.py:505`, called at `:227`) and injected into the AI working set
  (`_resolve_current_files`, `app_builder_ai_service.py:842`). It is never edited by hand. So "client
  regenerated when the spec changes" means every publish and every AI turn sees the current spec.
- Preview (studio change): `IframeSandbox.tsx:108-114` adds `Authorization: Bearer <preview token>`
  for URLs starting with `/api/p/`. Published: runtime passthrough (§3.3).
- Used-by: a tree-sitter scan of each app's latest version files (`app_files`, reusing the parser from
  `services/tsx_parser.py:15-16`). It finds (a) calls `api.<op>(` and (b) string or template literals
  starting with `/api/p/<slug>/`, and maps them to routes. Results are cached in
  `app_files.parsed_index["api_calls"]` when a version is created.

### 3.7 Phase 7: data

| Method | Path | Body → Response |
|---|---|---|
| GET / POST | `/api/projects/{project}/tables` | POST `{name, description?, columns:[{name,type,required?,default?}]}` → `201 Table`. Types: `string,number,integer,boolean,datetime,json`. `409 name_taken`, `422 invalid_column`. |
| GET / PUT / DELETE | `/api/projects/{project}/tables/{tid}` | PUT changes name, description or columns. A type change or `required` that existing rows violate → `409 rows_incompatible {"count":n}`. DELETE also removes rows. |
| GET | `…/tables/{tid}/rows?limit=50&cursor=&filter=<url-encoded JSON>&sort=-created_at` | → `200 {"rows":[{id,data,created_at,updated_at}],"next_cursor":"…"\|null}` |
| POST | `…/tables/{tid}/rows` | `{data}` → `201 Row`, or `{rows:[…≤1000]}` → `201 {"inserted":n,"rows":[…]}`. `422 {"code":"row_invalid","errors":[{"row":0,"column":"total","error":"expected number"}]}` |
| GET / PATCH / PUT / DELETE | `…/tables/{tid}/rows/{row_id}` | PATCH merges, PUT replaces |

Filter grammar: `{"col": v}` for equality, and `{"col": {"$eq|$ne|$gt|$gte|$lt|$lte": v, "$in": [..], "$contains": "s", "$exists": bool}}`.
Top-level `$and`/`$or` take arrays. It compiles to `data->>'col'` / `(data->>'col')::numeric` /
`data @> …` with bound parameters. Column names must exist in the schema, otherwise `422 unknown_column`.
That is the injection guard. `created_at` and `id` are always sortable.

**`DataTable` node** (canvas, function `data_table`, and `studio.db.table(name)`):
- Parameters: `table` (name, resolved in `context.project_id`), `operation` (`insert|get|list|update|delete|upsert`),
  `data`, `rowId`, `filter`, `limit`, `sort`, `matchColumns`.
- Each output item is `{id, ...data}`.
- `studio.db.table("orders").insert({...}) / .get(id) / .list(filter=…, limit=…) / .update(id, {...}) / .delete(id) / .upsert(match=["email"], data={...})`
  maps 1:1 to broker calls of `DataTable`.
- Stretch goal: `POST …/tables/{tid}/generate-routes` creates the five CRUD routes with workflow handlers that use `DataTable`.

### 3.8 Phase 8: Python handlers + sandbox

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/api/projects/{project}/modules` | POST `{name, source}` → `201 {module, routes:{created,updated,removed}, errors:[{line,message}]}` |
| GET / PUT / DELETE | `/api/projects/{project}/modules/{mid}` | PUT `{source}` returns the same shape. A syntax error is **not** saved → `422 {"code":"syntax_error","errors":[…]}`. |
| GET | `/api/sandbox/status` | `{"backend":"bwrap","available":true,"version":"0.11.2"}`, or `{"backend":"disabled","available":false,"reason":"…"}` |

Module source:
```python
from studio import api, Response, HTTPError
import studio

@api.get("/orders/{id}", auth="app_token", name="getOrder")
def get_order(req):
    rows = studio.db.table("orders").list(filter={"id": req.params["id"]}, limit=1)
    if not rows:
        raise HTTPError(404, {"error": "not found"})
    return rows[0]                       # dict/list → 200 JSON; or Response(status=201, headers={...}, body=...)
```
- On save, decorators are discovered by **static `ast` parsing in the engine** (no code runs). The
  module's `source='code'` routes are upserted: created, updated, or deleted when the decorator is
  removed. A clash with a `ui` route → `409 route_conflict` and nothing is saved.
- `req` exposes `method, path, params, query, headers, body`. Async handlers are allowed.
- **`PythonStep` node**: parameters `module` + `function`, **or** inline `code`. The function receives
  `items` (list of dicts) and returns a list or dict, normalised like `CodeNode._normalize_output`
  (`code.py:228`).
- **Code node**: same parameters and globals as today (`items, json_data, input_data, node_data,
  execution, get_item, new_item, log`, plus `json, math, re, random, base64, io, datetime, timedelta, pd`).
  It now runs in the sandbox, with `studio` available, a 5 s timeout and a hard kill.
- Per-request trace. The execution row's `metadata.trace` is
  `{"sandbox":{"duration_ms","exit_code","stdout":"≤64KB","stderr":"≤64KB","truncated":bool},"broker_calls":["bcall_…"]}`,
  served by `GET /api/projects/{project}/runs/{execution_id}` with the broker rows expanded.
- Sandbox unavailable: python routes → `503 {"error":"sandbox_unavailable"}`. PythonStep fails the node
  with the same message. The Code node fallback is covered in §6.

### 3.9 Phase 9: releases

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/projects/{project}/releases?environment=` | `[Release]` without the snapshot body |
| POST | `/api/projects/{project}/releases` | `{environment:"dev", notes?}`: snapshot drafts into a release of that environment, run checks, activate. `201 Release`, or `422 {"code":"checks_failed","checks":[…]}` with nothing activated (stored as `blocked`). |
| GET | `/api/projects/{project}/releases/{rid}` | `Release` including snapshot and checks |
| POST | `/api/projects/{project}/releases/{rid}/promote` | `{to_environment:"prod"}`: copy the snapshot and re-run checks against the target environment (secrets present, etc.). `201 Release` / `422 checks_failed`. |
| POST | `/api/projects/{project}/environments/{env}/rollback` | `{release_id?}` (default = the previous active release) → `200 Release`. Checks run but **only warn**, so rollback is never blocked. |
| GET | `/api/projects/{project}/releases/{rid}/diff?against=active` | `{routes:[Change], apps:[{app_id,from_version,to_version}], workflows:[…], secrets:[{key,change:"added"\|"removed"\|"value_changed"}]}` |

```json
// Release.snapshot
{"apps":[{"app_id":"app_…","version_id":31}],
 "workflows":[{"workflow_id":"wf_…","version_id":88}],
 "modules":[{"module_id":"mod_…","name":"orders","source_sha":"…","source":"…"}],
 "routes":[{"id":"route_…","name":"getOrder","method":"GET","path":"/orders/{id}","auth":"app_token",
            "request_schema":{…},"response_schema":{…},"response_mode":"auto",
            "handler":{"type":"workflow","workflow_id":"wf_…","version_id":88}}],
 "spec":{…OpenAPI…},"spec_hash":"…",
 "secret_bindings":[{"key":"STRIPE_KEY","scope":"project","variable_id":12,"fingerprint":"hmac-sha256…"}]}
// Release.checks
[{"id":"app_calls_resolve","status":"fail","message":"Orders UI calls getOrder (GET /orders/{id}) which is not in this release","details":{…}},
 {"id":"handlers_exist","status":"pass"},{"id":"secrets_present","status":"fail","details":{"missing":["STRIPE_KEY"]}},
 {"id":"modules_parse","status":"pass"},{"id":"stub_routes","status":"warn","details":{"routes":["POST /refunds"]}},
 {"id":"breaking_vs_active","status":"warn","details":{"changes":[…]}}]
```
- Snapshot creation:
  - Each referenced workflow gets a new row via `VersionRepository.create_version`
    (`repositories/version_repository.py:17`) **without** touching `published_version_id`, triggers or `active`.
  - Each project app uses its `current_version_id`. If there is none, one is created from
    `draft_source_code` with trigger `release`.
- Serving:
  - In a `kind:"release"` environment with an active release, the router matches routes from
    `snapshot.routes`. Workflow handlers load the pinned definition (`definition_to_workflow`), and
    python handlers use `snapshot.modules`.
  - Promoting or rolling back into `live_environment` sets `apps.published_version_id` to the snapshot
    version for apps that have a slug, and re-bundles through the existing publish path. So `/a/{slug}`
    serves the released UI without changes to `public_apps.py`.
  - Before a release environment's first release, it serves drafts (compatible with phases 5–8).
- `fingerprint` is HMAC-SHA256 of the value, keyed with `WORKFLOW_ENCRYPTION_KEY`. It detects
  "secret changed since release" without storing values. Secret values are always resolved live, so
  rotating a secret needs no release.

---

## 4. Module layout

### 4.1 New files
| File | Responsibility |
|---|---|
| `src/core/identity.py` | `get_actor()` dependency: optional `X-User-Id`, else `"local"`, used for `created_by` only |
| `src/repositories/project_repository.py`, `api_route_repository.py`, `api_key_repository.py`, `data_table_repository.py`, `code_module_repository.py`, `release_repository.py`, `broker_call_repository.py` | Thin SQLModel CRUD, same style as `variable_repository.py` |
| `src/services/project_service.py` | CRUD, slug/ID resolution (`resolve(project)`), summary and health aggregation, empty-check on delete |
| `src/services/secret_service.py` | Effective (merged) view, scope rules, missing-secret scan, fingerprints |
| `src/services/http_response.py` | `build_response(WebhookResponse\|dict)`, moved from `routes/webhooks.py:25`; webhooks import it from here |
| `src/services/api_route_service.py` | Route CRUD and validation, `path_shape`, default names, spec regeneration plus revision row on every save, used-by lookup |
| `src/services/route_matcher.py` | Compiles the route set to a match table: static-first, then fewest params, then longest. Cached per (project, environment, spec revision or release ID). |
| `src/services/openapi_builder.py` | Route set → OpenAPI 3.1 (paths, parameters, requestBody, 200 responses, securitySchemes `appToken`/`apiKey`) |
| `src/services/spec_diff.py` | Diff of two specs → `[Change]` (rules in §3.5) |
| `src/services/client_codegen.py` | Spec → `client.ts` (JSON Schema → TS types; deterministic output) |
| `src/services/api_usage.py` | Tree-sitter scan for `api.<op>(` and `/api/p/` literals, using `tsx_parser._parser`. Adds `extract_api_calls(content)` next to `parse_tsx_file` (`tsx_parser.py:26`). |
| `src/services/route_dispatcher.py` | Auth resolution, environment selection, building the request item, handler dispatch (stub/workflow/python), response shaping, timeouts, execution row with `mode="api"` |
| `src/services/api_key_service.py` | Generate `sk_{env}_{32B b64url}`, store sha256, verify in constant time, update `last_used_at` |
| `src/services/data_table_service.py` | Table and row CRUD, column validation, filter compiler |
| `src/services/release_service.py` | Snapshot, checks, activate, promote, rollback, diff |
| `src/broker/tokens.py` | In-memory `BrokerTokenRegistry` (sha256(token) → claims), mint, verify, revoke |
| `src/broker/policy.py` | Function exposure, `credentialId` rejection, bare-`$vars` rule, secret→host binding, project allowlist, per-node `egress_hosts(params)` hooks |
| `src/broker/service.py` | `BrokerService.call(claims, function, parameters, items)` runs one node via `WorkflowRunner.run` (`workflow_runner.py:51`) on a one-node `Workflow`, using `start_node_name="fn"`, `db_session_factory=None` (no `node_outputs` rows), and the run's `variables` and `secret_values`; then redacts and logs |
| `src/broker/uds_server.py` | Per-sandbox-run `asyncio.start_unix_server`; newline-delimited JSON frames `{token, function, parameters, items}` → `BrokerService.call` |
| `src/sdk/generate.py` | Registry → `studio` package source. Function names are snake_case of the node type, parameters are snake_case mapped back to property names, type hints and docstrings come from `NodeProperty`. Output is cached by registry hash and written to `<tmp>/studio-sdk/<hash>/`. |
| `src/sdk/static/` | Hand-written parts copied into the package: `_transport.py` (InProcess, UDS and HTTP transports), `secret.py` (`SecretRef`), `db.py`, `api.py` (decorators, `Response`, `HTTPError`) |
| `src/sandbox/backend.py` | `SandboxBackend` Protocol, `SandboxJob`, `SandboxResult`, `get_backend()` (from `WORKFLOW_SANDBOX_BACKEND=bwrap\|disabled`, auto-detected) |
| `src/sandbox/bwrap.py` | `BwrapBackend.run(job)`: argv build (§5.5), rlimits, wall timeout, process-group kill, capped stdout/stderr |
| `src/sandbox/service.py` | `SandboxService.run_python(project_id, environment, execution_id, entry, payload)`: job dir, broker token, UDS server, backend, result |
| `src/sandbox/runtime/runner.py` | Runs **inside** the sandbox: reads `/run/job/input.json`, imports modules from `/run/job/modules`, calls the entry, writes `/run/job/out.json`. Also hosts the Code-node compatibility shim. |
| `src/nodes/flow/respond.py` | `Respond` node: `statusCode`, `headers[]`, `body` (expression; default `{{ $json }}`), `contentType` → sets `context.webhook_response` |
| `src/nodes/data/data_table.py` | `DataTable` node (§3.7) |
| `src/nodes/data/python_step.py` | `PythonStep` node (§3.8) |
| `src/nodes/ai/tools/studio_function_tool.py` | Agent tool type `studio_functions`: parameter `functions: [..]` exposes catalogue entries as tools that call `BrokerService` in-process with the execution's token |
| `src/routes/projects.py`, `project_secrets.py`, `api_routes.py`, `api_keys.py`, `project_router.py`, `tables.py`, `modules.py`, `releases.py`, `broker.py`, `sdk.py` | Thin HTTP layers over the services. Registered in `routes/__init__.py`. `project_router` is a separate `APIRouter(prefix="/api/p")` included in `main.py:106-110`. |

### 4.2 Changed files
| File | Change |
|---|---|
| `src/db/models.py` | New columns and models from §2 |
| `src/engine/types.py` | `ExecutionContext.project_id/environment/team_id`; `StoredWorkflow.project_id` |
| `src/engine/workflow_runner.py` | `run(..., project_id, environment, team_id)` kwargs; team check in `_resolve_credential` (`:962`); `find_start_node` (`:947`) unchanged |
| `src/engine/node_registry.py:187` | Register `Respond`, `DataTable`, `PythonStep`. Add `function_exposed` derivation: excludes group `trigger`, graph-only flow nodes (If, Switch, Merge, Loop, Wait, Poll, StopAndError, ExecuteWorkflow), `ChatInput`, `Code`, `PythonStep`. A class attribute overrides it. |
| `src/services/execution_service.py` | **New `run_saved_workflow(...)`**, extracted from `WorkflowService.run_workflow` (`workflow_service.py:270-340`): load workflow (draft, or a pinned version via `version_id`), merged variables and secrets, execution row, `runner.run`, complete. Options: `substitute_inputs: bool`, `initial_item`, `timeout_s`. `WorkflowService.run_workflow` becomes a one-line delegate with identical output. |
| `src/services/webhook_service.py` | Pass `project_id` to the loaders. `_build_response` import moves. |
| `src/services/vars_substitution.py` | Add `redact_deep(value, secrets)` (walks JSON and applies `redact_secrets` from `:50` per string) and `expand_secret_variants(secrets)` (adds base64, base64url, URL-quoted and JSON-escaped forms) |
| `src/routes/public_apps.py` | Stub at `:399-411` → `ExecutionService.run_saved_workflow`; runtime rewrite fix at `:151`; `/api/p/` passthrough |
| `src/core/public_tokens.py:65,77` | `issue_token(app_id, version_id, ttl, *, env=None, preview=False)`; `PublicTokenClaims` gains optional `env`/`preview`. Tokens without them still verify. |
| `src/services/app_service.py` | `project_id` plumbing; client injection in `_files_for_publish`; `api_calls` cached in `parsed_index` at version creation |
| `src/services/app_builder_ai_service.py:753` | `_build_agent_system_prompt(api_executions, spec_summary=None)`; `declare_endpoint` tool added to `_build_agent_tools` (`:471`) |
| `src/nodes/data/code.py:184-214` | Delegates to `SandboxService` when the sandbox is available (§6) |
| `src/nodes/ai/tools/code_tool.py:153` | Same move into the sandbox |
| `src/nodes/ai/inline_config.py` | Register the `studio_functions` tool type |
| `src/main.py` | Include new routers; generate the SDK at startup after `register_all_nodes()` (`:34`) |
| `src/core/config.py` | `sandbox_backend`, `sandbox_timeout_s=30`, `sandbox_memory_mb=512`, `broker_max_calls=500` |

### 4.3 Reuse instead of duplication (explicit)
- Running any workflow (studio, public app, route, release) goes through **one** function,
  `ExecutionService.run_saved_workflow`. One node goes through `WorkflowRunner.run` with
  `start_node_name`. No second executor is written.
- HTTP responses from handlers reuse `WebhookResponse` (`types.py:44`) and `_build_response`.
- Redaction reuses `redact_secrets`. Schemas reuse `infer_json_schema`. Usage scanning reuses the
  tree-sitter parser. Tokens reuse the `public_tokens` HMAC scheme. Encryption reuses
  `variable_crypto`/`credential_service._get_fernet`.

---

## 5. Security design

### 5.1 Broker tokens
- Format `brk_` plus 32 random bytes (b64url). Only `sha256(token)` is kept, in an in-memory registry;
  the engine runs as a single process.
- Claims: `{project_id, team_id, environment, execution_id, origin: code_node|python_step|python_route|agent|dev, allowed_functions: set|None, max_calls, calls_used, expires_at}`.
- Lifetime:
  - Minted per execution (Code, PythonStep or agent node) or per route request.
  - `expires_at` = deadline + 5 s.
  - **Revoked in a `finally`** when the run ends.
  - Dev tokens are limited to draft environments and last 1 h.
- A token can never name a different project or environment. Variables are loaded from its claims,
  never from the request.

### 5.2 Environment-bound credentials only
- Secrets reach nodes only as **bare** `{{ $vars.KEY }}` placeholders, resolved from the claims'
  (project, environment) map. Any other `$vars` use, such as `{{ $vars.K.split('') }}`, is rejected,
  because computed forms would defeat substring redaction.
- `credentialId` anywhere in the parameters → rejected.
- Outside the broker, the runner refuses credentials from another team (`workflow_runner.py:962`).

### 5.3 Host allowlist and full-response redaction
- Each node type declares `egress_hosts(resolved_params) -> set[str] | None`:
  - HttpRequest: URL host.
  - SendEmail: SMTP host.
  - Postgres, Mongo, Neo4j: connection host.
  - Pure-data nodes: empty set.
  - Unknown: None.
- Rules, evaluated after expression resolution:
  1. If `settings.egress_allowlist` is non-empty, every egress host must match it (`*.` wildcard supported).
  2. Any call that references secret K must have its hosts ⊆ `K.allowed_hosts`. If the node's hosts
     are None or empty (no egress hook), or `allowed_hosts` is null → `secret_not_bound`.
- **Redirects.** The runner's shared `httpx.AsyncClient` follows redirects (`workflow_runner.py:156`).
  httpx strips `Authorization` on cross-origin redirects. Broker calls additionally pass
  `follow_redirects=False` via a new `run(..., http_client_options=…)` kwarg.
- **Redaction.** Every broker response is passed through `redact_deep`, along with error messages and
  `broker_calls.params_redacted`, using the union of the environment's secret values plus their
  base64, base64url, URL-quoted and JSON-escaped variants. The same applies to router JSON responses
  and public-app run responses.

### 5.4 API keys and app tokens
- API keys: 32 random bytes; sha256 at rest (high entropy, so no slow hash is needed); compared with
  `hmac.compare_digest`; shown once.
- Each key is bound to one (project, environment) and is revocable.
- Keys are never placed in generated clients or app bundles.
- App tokens keep the existing app + published-version scope (`public_apps.py:386-389`). The router
  additionally requires `app.project_id == route.project_id`. Preview tokens carry `preview:true` and
  `env=<default_environment>`, last 1 h, and are only accepted for draft environments.

### 5.5 Sandbox (bubblewrap)
Command line, verified on this machine (§0):
```
bwrap --unshare-all --die-with-parent --new-session --cap-drop ALL --hostname sandbox --clearenv
      --setenv PATH /usr/bin --setenv HOME /tmp --setenv PYTHONDONTWRITEBYTECODE 1
      --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin
      --proc /proc --dev /dev --size 67108864 --tmpfs /tmp
      --ro-bind <sdk-cache>/<hash> /opt/studio        # generated studio pkg + runner.py
      --ro-bind <venv>/lib/python3.14/site-packages /opt/site   # pandas compatibility for the Code node
      --ro-bind <jobdir>/in /run/job/in  --bind <jobdir>/out /run/job/out
      --bind /tmp/sbx-XXXX /run/broker                 # only broker.sock inside
      --chdir /tmp /usr/bin/python3 -I -S /opt/studio/runner.py
```
- Not mounted: `/home`, `/etc`, the repository, `apps/workflow-engine/.env`, `secrets/`, any socket
  other than the broker.
- **Network:** `--unshare-all` gives a new network namespace, so the broker socket is the only way out.
- **Environment:** `--clearenv` plus explicit variables, so no engine environment or keys leak in.
- **Interpreter:** `-I -S` ignores `PYTHON*` variables and site hooks. The runner appends `/opt/site`
  and `/opt/studio` to `sys.path` explicitly.
- **Limits:**
  - `preexec_fn` sets `RLIMIT_AS=512MB`, `RLIMIT_CPU=timeout+1`, `RLIMIT_FSIZE=16MB`, `RLIMIT_NOFILE=64`.
  - Wall-clock timeout via `asyncio.wait_for`, then `killpg`.
  - stdout and stderr capped at 64 KB each.
  - The output file is capped at 10 MB.
  - Broker calls are capped by token `max_calls`.
- **Protocol:** `in/input.json` = `{kind: "route"|"step"|"code", modules: {name: source}, entry: "orders:get_order", payload: {…}, token}`.
  The runner writes `out/out.json` = `{ok, result | error:{type,message,traceback}}`. The broker token
  is given to the runner through `input.json`, never through the environment. It is useless outside
  the socket and the run window.
- **Interface:** `SandboxBackend.run(job) -> SandboxResult(exit_code, stdout, stderr, output, duration_ms, timed_out)`.
  `BwrapBackend` is the only real implementation. `DisabledBackend` raises `SandboxUnavailable`.
  **There is no silent in-process fallback for python handlers.**

### 5.6 Known non-goals (unchanged trust model)
There is no user authentication: the engine trusts the network, as today. Management endpoints
(`/api/projects/...`) are as open as `/api/workflows` is now. PLAN's cross-cutting section says this
must be fixed before exposure. The new runtime surfaces (router, broker, public apps) are the ones
designed to be exposed, and each has its own credential checks.

### 5.7 Actor header
`X-User-Id` is read if present and stored in `created_by`. It is never used for authorization in this phase set.

---

## 6. Compatibility

| Feature | How it keeps working |
|---|---|
| Existing data | Backfilled to `proj_default`. Variables stay team-shared. The Default project's environments are the existing ones, and both its default and live environment are `default`, so every run resolves the same `$vars` as today. |
| Workflow CRUD, publish and runs (`/api/workflows*`, `/execution-stream/*`) | Same paths and response keys, plus `project_id`. `POST /api/workflows` without `project_id` lands in the Default project through the DB default. Execution output for `/run` is unchanged, which the characterization tests pin. |
| Webhooks (`/webhook/{id}`, `/webhook/p/{path}`) | Unchanged, including inbound `$vars` substitution (`webhook_service.py:115-124`) and `X-Env`. They gain a working `Respond` node in `lastNode` mode. |
| Editor | Unchanged. New node types appear in `/api/nodes`. The Variables modal still edits team variables through `/api/variables`, whose list and duplicate check are scoped to team rows. |
| App builder and AI chat | Unchanged when no project or spec is involved. `api_execution_ids` attached endpoints and replay still work (`public_apps.py:454`). The spec section is only added for projects that have routes. |
| Published apps | `/a/{slug}`, assets, unlock and replay are unchanged. `/a/{slug}/api/workflow/{id}/run` now runs for real; it was a stub, so nothing depended on its old body. |
| Cron | Uses the workflow's project `default_environment`, which is `default` for existing workflows (today's value, hardcoded at `cron_scheduler.py:92`). |

Deliberate behaviour changes:
1. **Public app run returns real results** instead of the echo stub. Inbound `$vars` are *not*
   substituted, and outputs are redacted. Reason: it is an anonymous surface.
2. **Published runtime URL fix.** `/api/workflow/...` now reaches the proxy route. Reason: it was a
   404, so this is a pure fix.
3. **Code node moves into the sandbox (phase 8).** Effects:
   - Same globals and timeout.
   - Full builtins inside the sandbox, which is more permissive but isolated.
   - No filesystem or network. `io`/`pd` can no longer read host files, which is intended.
   - Return values must be JSON-serialisable. That is already required for persistence (`workflow_runner.py:515`).
   - About 20 ms extra per run, more when pandas is imported.
   - If bwrap is unavailable, the Code node falls back to the legacy in-process `exec` **only** when
     `WORKFLOW_CODE_NODE_LEGACY=1` (default: 1 when the backend is disabled, so local setups without
     bwrap keep working). The node's output metadata then carries `"sandboxed": false`. Python routes
     and PythonStep never fall back.
4. **Credential decrypt now checks the team** (`workflow_runner.py:962`). With a single team, nothing
   observable changes.
5. **`data_tables` uniqueness moves from (team, name) to (project, name).** There were no users, since
   there were no endpoints.
6. **Router and public responses are secret-redacted.** A handler that returns a secret value gets `***`.

---

## 7. Test strategy

### 7.1 Harness (`tests/integration/`)
- **Isolated database.** A session fixture connects to `postgres` on `localhost:5433` and runs
  `CREATE DATABASE workflows_test_<8 hex>`, then `CREATE SCHEMA "workflow-app"`. It runs migrations with
  `venv/bin/python -m src.db.migrate apply`, passing env `WORKFLOW_DB_NAME=<test db>`.
  - **Guard:** abort if the name is `workflows`, or if the port is 8000.
  - Teardown: `DROP DATABASE … WITH (FORCE)`, unless `KEEP_TEST_DB=1`.
- **Real server over HTTP.** It starts `venv/bin/python -m uvicorn src.main:app --port <free>` as a
  subprocess with env `WORKFLOW_DB_*` (test DB), `SEED=0`, a generated `WORKFLOW_ENCRYPTION_KEY`, and
  a fixed `WORKFLOW_PUBLIC_TOKEN_SECRET`. It polls `/health` before yielding the base URL.
  - The lifespan, cron, background webhook tasks and SSE all behave as in production, which in-process
    ASGI would not give.
  - Server logs go to `tests/integration/.logs/` and are printed on failure.
- **Studio-identical client.** `StudioClient(httpx.Client)` mirrors `apiFetch`:
  - prefix `/api`;
  - **always** `Content-Type: application/json`, including on GET and DELETE (`api.ts:71-74`);
  - no `X-User-Id`, because the studio sends none;
  - SSE via `client.stream("POST", "/execution-stream/…")` and the same `data:` parsing as `studio:shared/lib/sseParser.ts`.
  Payloads are copied from the call sites in §1. One example: variables PUT always sends `description: null`.
- **Fixtures:**
  - A local upstream HTTP server (`http.server` thread on 127.0.0.1) for HttpRequest, allowlist and
    redaction tests.
  - The `esbuild` presence check gates publish tests (`pytest.skip` with a reason).
  - `bwrap` presence gates phase 8 tests.
  - Factories create their own projects, workflows and apps per test with unique names. There is no
    TRUNCATE. The DB is dropped at the end.
- **Direct DB assertions.** `psycopg2` against the test DB is used for migration and backfill checks only.
- **Markers:** `@pytest.mark.integration`. The existing 28 unit tests are untouched.

### 7.2 Step 0: characterization tests (written first, green on unchanged code)
One test per §1 row, asserting status, response keys and key semantics. Highlights:
- Workflow lifecycle: create → get → update → run (`Start`→`Set` computing `{{ 20 + 22 }}` = 42, as in
  the handoff) → publish → versions → unpublish → delete.
- Run-adhoc, and SSE streaming for both endpoints (event order: execution_start … execution_complete).
- Webhook by ID and by path, in both `onReceived` and `lastNode` modes, inactive → 400, wrong method → 405.
  Also `X-Env` selection and inbound `$vars` substitution.
- Variables: create, list (secret value `null`), `environments`, update with `description:null`,
  duplicate → 409, delete. A `$vars` secret resolves inside a run and `requestUrl` is redacted.
- Apps: create, save with `create_version`, versions, label, revert, publish (`esbuild`), `/a/{slug}`
  HTML contains a token, assets have cache headers, password unlock flow, private → 404, unpublish.
  The public run stub's current response shape is recorded as an `xfail` once phase 3 starts.
- `/api/nodes` contains every registered type. `/api/executions` lists runs.
- API tester execute/list/get/patch/delete against the local upstream, and replay through a published app.

### 7.3 Per-phase journeys (written as the UI will call them)
| Phase | Journeys |
|---|---|
| 0→1 migration | Fresh DB at baseline (`rollback` to `20260307120000`, insert legacy rows, re-`apply`). Assert every workflow, app, table and folder is in `proj_default`; executions are backfilled; variables are unchanged and team-scoped. Then the full characterization suite again. Down-migration round trip. |
| 1 | Projects grid: `GET /api/projects` shows Default with counts. Create "Orders" → create a workflow inside it → `GET …/workflows` shows only it, while `/api/workflows` still shows all → summary counts and health → slug conflict 409 → deleting a non-empty project 409 → move a workflow. |
| 2 | Create a team var `TOKEN=a`, then a project secret `TOKEN=b`. A run in the project echoes via the upstream and sees `b`; a Default-project run sees `a`. The effective list shows `overrides_team`/`overridden`, and values are `null`. The missing scan lists `$vars.NOPE` referenced by a node. PUT without `description` keeps it. |
| 3 | Published app with `workflow_ids=[wf]`. A run through the token returns real `data`. Not in allowlist → 403. A workflow in another project → 403. Bad token → 401. A body containing `{{ $vars.SECRET }}` is **not** expanded. The secret appears as `***` in the response. The runtime JS contains the fixed rewrite. |
| 4 | `GET /api/sdk/functions` includes `http_request`, excludes `Webhook`/`If`. The `studio` package from `/api/sdk/python` imports in a clean interpreter. The broker: no token 401; HttpRequest to the allowed upstream with a bound secret → 200 and the response body echo is redacted (including base64); a disallowed host → 403 `host_not_allowed`; an unbound secret → 403; `credentialId` → 422; `{{ $vars.X.upper() }}` → 422; a missing key → 422 `missing_secret` with the key; the call limit → 429; the call log rows are redacted. A Code node calling `studio.nodes.set(...)` in-process works, and its token is revoked after the run (a replay → 401). The `studio_functions` agent tool is exercised by calling its executor directly (no LLM). |
| 5 | Create a route with `handler.create:true`; call `/api/p/orders/orders/42` → 200 with params. A Respond node sets 201 and a custom header. 404/405 with `Allow`. An `api_key` route without a key → 401, with a key → 200 and the key's environment is used; a revoked key → 401. A stub → 501. `/test` with inference, then `apply_schemas` → spec revision +1. `openapi.json` validates (structure check). A conflicting path → 409. A handler error → 502 with `X-Execution-Id`, and the run appears in `…/runs` with `mode="api"`. |
| 6 | `client.ts` contains `getOrder` and excludes `api_key` routes. An app whose source calls `api.getOrder` → used-by lists file and line. Removing `total` from the response schema with `dry_run` lists the affected app. Stubs from the UI side are idempotent. A preview token works on draft routes. Publish injects the current client (assert the bundle contains the spec hash comment). The prompt builder includes the spec (unit-level, since there is no LLM). |
| 7 | Table CRUD; row validation errors; filter grammar (`$gt`, `$in`, `$contains`, `$or`); an unknown column → 422; cursor pagination; a column change rejected when rows conflict. A canvas `DataTable` node in a route handler inserts, then lists. `studio.db.table()` through the broker. Tables are isolated between projects. |
| 8 | `/api/sandbox/status` reports available. A module with `@api.get` → route auto-created. The python route returns 200, 201 via `Response`, 404 via `HTTPError`. Sandbox escape probes all fail: `socket.create_connection` to the upstream, `open('/home/…/.env')`, `os.environ` holds no `WORKFLOW_*`, `/etc/passwd` absent, a fork bomb or memory hog is killed, a timeout gives 504. Broker access from a handler works and appears in the trace. PythonStep in a canvas workflow works. The legacy Code-node suite runs unchanged inside the sandbox. A syntax error → 422 and nothing saved. |
| 9 | Release dev → active; the router serves the pinned version, and later draft edits do not change prod. A release with an app calling a missing route → 422 `checks_failed`. Promote to prod with a missing prod secret → blocked; add the secret → promote OK; `/a/{slug}` serves the snapshot app version. Rollback → the previous version is served, and warnings are listed. The diff endpoint is checked. |

### 7.4 Running
```bash
cd apps/workflow-engine
export WORKFLOW_DB_HOST=localhost WORKFLOW_DB_PORT=5433 WORKFLOW_DB_USER=workflow WORKFLOW_DB_PASSWORD=workflow
venv/bin/python -m pytest tests -q                          # unit + integration
venv/bin/python -m pytest tests/integration -q -m integration -k phase5   # one phase
KEEP_TEST_DB=1 venv/bin/python -m pytest tests/integration -x            # keep DB for debugging
```
The :8000 engine and the `workflows` database are never touched. The fixture refuses `WORKFLOW_DB_NAME=workflows`.

---

## 8. Implementation order, checkpoints, risks

| # | Step | Checkpoint (all previous tests stay green) |
|---|---|---|
| 0 | Harness + characterization suite | ~40 integration tests green on **unchanged** code; commit before touching `src/` |
| 1 | M1 + models + `project_id` plumbing + `ExecutionService.run_saved_workflow` extraction + projects API | Migration round-trip test; characterization unchanged; phase 1 journeys |
| 2 | Secrets merge + project secrets API + missing scan | Phase 2 journeys; Default-project runs resolve identically |
| 3 | Public app run + runtime fix | Phase 3 journeys |
| 4 | Redaction helpers → broker (tokens, policy, service, log, M2) → SDK generator → in-process transport for Code node → agent tool | Phase 4 journeys, including the negative policy matrix |
| 5 | `Respond` node → M3 → route CRUD/matcher/dispatcher/router → API keys → OpenAPI + revisions + diff → test/inference | Phase 5 journeys |
| 6 | Client codegen → used-by → stubs → preview token + studio iframe header → publish injection → AI prompt and tool | Phase 6 journeys |
| 7 | M4 → table service + filter compiler → `DataTable` node → `studio.db` | Phase 7 journeys |
| 8 | Sandbox backend + runner + UDS broker → modules API (M5) + AST route sync → python dispatch → PythonStep → move Code node and CodeTool | Phase 8 journeys incl. escape probes; Code-node characterization unchanged |
| 9 | M6 → snapshot/checks → release-aware matcher and loader → promote/rollback → app re-publish | Phase 9 journeys; the full suite |

Risks:
- **Draft-vs-version semantics.** Runs use drafts everywhere. Releases are the first code path that
  pins versions. A bug there would silently serve drafts in prod, so tests assert the served version explicitly.
- **In-memory broker tokens and route caches** assume one engine process. Running uvicorn with
  multiple workers or `--reload` breaks them. Startup should fail if workers > 1; moving the registry
  into Postgres is the fix if scaling is needed.
- **Redaction is substring-based.** Bare-reference plus host binding is the real control; redaction is
  defence in depth. Secrets shorter than 4 characters are not redacted (`vars_substitution.py:50`).
- **Existing exfiltration path, unchanged.** `webhook_service.py:115-124` lets an anonymous webhook
  caller get `{{ $vars.X }}` expanded into the payload, which is persisted and possibly echoed in
  `lastNode` mode. I keep it for compatibility and flag it as an open question.
- **AI agent PTC `exec`** (`ai_agent.py:1159`) and `calculator_tool` `eval` stay in-process. PTC is
  out of scope; it needs tool callbacks across the sandbox.
- **bwrap needs unprivileged user namespaces.** These are often disabled in containers. With bwrap
  unavailable, python routes return 503 and the Code node uses the labelled legacy path.
- **Pandas in the sandbox** costs import time (hundreds of ms) and binds the engine venv read-only.
  A dedicated sandbox venv is cleaner later.
- **Inferred schemas are loose** (no `required`, and the first array element only). Breaking-change
  detection is only as good as the schemas, so the UI must let users edit them.
- **The LLM-dependent parts** (app builder spec usage, "generate UI for this API", agent tool use)
  cannot be exercised end to end without a key. Only prompt construction and tool executors are tested.
- **Public app token TTL is 1 h** (`public_tokens.py:30`). A long-open page fails after that. This is
  existing behaviour; a refresh endpoint is a follow-up.

## 9. Decisions (owner answers, 2026-10-14)
1. **Inbound `$vars` expansion** — KEPT for existing `/webhook/...` routes (the seeded *Vars Redaction
   Demo* depends on it) and for `/api/workflows/{id}/run` / streaming; DISABLED for project API routes
   (`/api/p/...`) and public-app runs (as designed in §3.3/§3.5).
2. **Environments for new projects** — `dev` (draft, default) + `prod` (release, live). Default project keeps `default`.
3. **Secret without `allowed_hosts`** — REFUSED from Python/broker (`secret_not_bound`, default-deny). Canvas unaffected.
4. **Request-schema enforcement** — opt-in per route. **CHANGED (§3.5):** `RouteCreate`/`Route` gain
   `"validate_request": false`. When true, the router validates `{params, query, body}` against
   `request_schema` (types + `required`) and returns `422 {"error":"request_invalid","errors":[{"path":"body.total","message":"expected number"}]}`.
   Inferred schemas are never auto-enforced: `/test` with `apply_schemas:true` never flips `validate_request`.
   Migration M3 gains `validate_request BOOLEAN NOT NULL DEFAULT false`.
5. **Sandbox pandas** — read-only bind of the engine venv's site-packages at `/opt/site` (as in §5.5).

## 10. Contract gap decisions (UI-INTEGRATION.md §7, G1–G34)

Rule: existing endpoints change **additively only**; new endpoints may get new shapes. Items
marked **CHANGED** amend §3. Totals: **accept 30 · accept-modified 3 · accept-later 1 · reject 0**.

| # | Decision | What the backend does |
|---|---|---|
| G1 | accept | All **new** management endpoints (`/api/projects/…`, new `/api/apps/{id}/…` endpoints, new `/a/{slug}/api/…` errors) return `{"detail":{"code","message",…extra}}` — incl. `row_invalid` (`detail.errors`), `syntax_error` (`detail.errors`), `checks_failed` (`detail.checks`, `detail.release_id`, `detail.number`), `rows_incompatible` (`detail.count`). **CHANGED** §3.7–3.9 (top-level `code` → `detail.code`). Router keeps `{"error":…}`, broker keeps `{"ok":false,"error":{…}}`. Existing string `detail`s (e.g. public-app `401 missing token`, `403 workflow not allowed by this app`) are unchanged. |
| G2 | accept | `environment` optional on `GET …/secrets` and `…/secrets/missing`; omitted = every project env, each row carries `environment` (already built). |
| G3 | accept | `GET …/runs`: `before=<execution_id>` cursor (response stays a list; `X-Next-Cursor` header when more), filters `workflow_id`, `route_id`, `environment`, `mode`, `status`; items gain `route_id`, `route` (`"GET /orders/{id}"`), `app_id`, `duration_ms`. Mode values: `manual, webhook, cron, retry, app, api, sandbox`. **CHANGED** §3.1. |
| G4 | accept-modified | `summary.missing_secrets: int` (distinct keys). Instead of adding keys to `counts` (J1/J6 assert `counts` equality with 4 keys), summary gains top-level `stub_routes: int`, `api_keys: int` (active), `active_releases: {<env>: {release_id, number, created_at} \| null}`. **CHANGED** §3.1. |
| G5 | accept | `AppListItem += {project_id, slug, active, published_at, current_version_number}`; `WorkflowListItem += {project_id, description, handler_for:[{route_id, method, path}]}`. |
| G6 | accept | `Route.handler` is always resolved (`{type:"workflow", workflow_id}` after `create:true`); `spec_revision: int`; write/delete results add `spec_hash`. |
| G7 | accept | `/test` with `apply_schemas:true` also returns `route`, `spec_revision`, `breaking_changes`. |
| G8 | accept | `/test` `environment` must be a draft env else `422 environment_not_draft`; default `default_environment`; always HTTP `200` with `status`/`headers`/`body` = what the router would have returned (incl. `handler_failed`, `not_implemented`). |
| G9 | accept | `DELETE …/routes/{rid}?dry_run=true` → `200 {spec_revision:null, spec_hash:null, breaking_changes}` without deleting. |
| G10 | accept | `POST …/releases?dry_run=true` and `POST …/releases/{rid}/promote?dry_run=true` → `200 {checks, diff}`, nothing stored. |
| G11 | accept | `422 checks_failed` includes `detail.release_id`, `detail.number`; blocked releases consume a number. |
| G12 | accept | Rollback reactivates the target row (`status:"active"`), previous active → `rolled_back`; response = target Release with re-run (warn-only) checks; no previous release → `409 no_previous_release`. |
| G13 | accept | `diff?against=active\|<release_id>` + optional `environment=`; `Change.kind ∈ {route_removed, route_added, method_changed, path_changed, auth_changed, request_required_added, request_field_type_changed, response_field_type_changed, response_field_removed, field_added}`. |
| G14 | accept | `Release = {id, project_id, environment, number, is_active, status, checks, source_release_id, notes, created_by, created_at}` (+`snapshot` on detail). |
| G15 | accept | Shapes as in UI-INTEGRATION §3.2; PATCH/PUT rows take `{data}`; every DELETE returns `200 {"success":true}` except routes/modules (which return breaking-change info, §3.5/G32). |
| G16 | accept | `GET …/rows?count=true` adds `count`; `Table += row_count`. |
| G17 | accept (+ later) | api-usage rows: `kind:"client_call"\|"literal"`, `method`/`path` nullable; `changed` = a breaking change recorded for that operation in a spec revision newer than the scanned app version. The optional unsaved-files scan (`POST …/api-usage`) is **accept-later** (phase 6 follow-up). |
| G18 | accept | Client always at `api/client.ts`; publish and the AI working set overwrite any saved copy; prompt states the import path. |
| G19 | accept | Client emits `qs()`; header is a legal comment `/*! @generated by studio — spec rev N, spec_hash H */` (survives esbuild); `ETag: "<spec_hash>"` on `client.ts` and `openapi.json`. |
| G20 | accept | `POST /api/apps/{app_id}/workflow/{workflow_id}/run` with a preview token (same body/response/allow-list as §3.3, env = `default_environment`). **CHANGED** §3.6 (new endpoint). |
| G21 | accept | Expired app/preview tokens → `401 {"error":"token_expired"}` on the router; preview tokens never need a published version. |
| G22 | accept-modified | `PUT/POST /api/apps` with `workflow_ids` containing a workflow that **exists in another project** → `422 {"detail":{"code":"workflow_not_in_project","workflow_ids":[…]}}`. Unknown ids stay accepted (compat). The run-time `403 {"detail":{"code":"workflow_not_in_project"}}` remains for workflows moved later. J9 adjusted (§11). |
| G23 | accept | Studio runs (`/run`, `run-adhoc`, both SSE) in a non-Default project with an explicit environment not in `project.environments` → `422 {"detail":{"code":"unknown_environment"}}` (`"default"` still maps to the project default). |
| G24 | accept | `errors[]` items (run responses, execution detail, project run detail) and SSE `node:error` events gain optional `error_code` and `details` (e.g. broker `missing_secret` → `{"key","environment"}`). |
| G25 | accept-later | `project_id` in `/api/ai/chat` body is accepted and ignored until phase 10. |
| G26 | accept | `PUT /api/projects/{p}?dry_run=true` returns `{project, affected_apps:[{app_id, name}]}` (apps whose files reference `/api/p/<old slug>/` or that are published) without saving. |
| G27 | accept (document) | Direct `POST /api/apps/{id}/publish` stays allowed as a hotfix; the next promote into the live env replaces it. No code change. |
| G28 | accept | Auth changes are breaking (`auth_changed`); `breaking_vs_active` warns; `app_calls_resolve` fails only for missing operations/paths. |
| G29 | accept | `VariableUpdateRequest.description` optional, applied only if sent (sending `null` still clears — modal unchanged). |
| G30 | accept-modified | `workflow_id`/`workflow_name` stay non-null strings (DB NOT NULL, existing readers). Python-handler runs use `workflow_id = <route_id>`, `workflow_name = "GET /path"` and carry `route_id`/`route`. |
| G31 | accept | Test-only: harness uses `raw` for the studio's header-less call sites. |
| G32 | accept | `DELETE …/modules/{mid}` → `200 {routes:{removed}, spec_revision, breaking_changes}`; `PUT …/modules/{mid}?dry_run=true` → same shape as save without saving. |
| G33 | accept | `GET …/broker-calls`: `limit` (default 100), `before`, `status`; latest 100 for the project when no `execution_id`. |
| G34 | accept | OpenAPI `servers:[{"url":"/api/p/<slug>"}]`. |

Rejected: none. **G25** is deferred to phase 10, and G17's optional unsaved-files scan is deferred.
For G27 the alternative (refuse direct publish with `409 managed_by_release`) was not chosen.

## 11. Journey ↔ design reconciliations

| Journey step | Disagreement | Resolution |
|---|---|---|
| J10.2 | expects SDK `version` = 64 hex; design said a hash | **CHANGED**: `version` is the full sha256 (64 hex). |
| J10.11 | expects `items[0].status` | The HttpRequest node's output item is `{statusCode, headers, body}` (`nodes/integrations/http_request.py`); §3.4's example was wrong and is corrected — journey asserts `items[0].statusCode`. |
| J9.4 / J9.11 | cross-project `workflow_ids` accepted at write, refused at run | G22: write → `422`; the run-time `403 workflow_not_in_project` is exercised by moving an allow-listed workflow to another project after publishing. |
| J9.8 | `SET_WF("{{ $json.x }}")` | Public runs wrap input like `/api/workflows/{id}/run` and webhooks (`{body, headers, query, method}`); journey uses `{{ $json.body.x }}`. |
| J1.5 / J1.48 | `counts` equality vs G4's extra counts | G4 extras are top-level summary fields (see G4). |
| J5.5 | body `mode_hint:null` → 422 (schema is a Literal, default `"auto"`) | The studio always sends a value (`useAIChat.ts:21`, default `'auto'`); journey uses `"auto"`. Backend unchanged. |
| J1.20 | client must contain `'/api/p/<S>'` (single quotes) | Codegen emits `export const BASE = '/api/p/<slug>'` (slugs are `[a-z0-9-]`, safe unquoted-escape). |
| J6.6 | "`GET /api/workflows` contains W **and more**" depends on test order | The journey creates one extra Default-project workflow first. |

## 12. As-built notes (deviations found while implementing)
- **Environment mapping** (phase 1): the studio always sends `environment:"default"`; for projects
  without a `default` env this means `default_environment` (`project_service.effective_environment`).
- **Extra migration** `20261015100000_execution_modes.sql`: the baseline CHECK on `executions.mode`
  had to admit `app`, `api`, `sandbox`.
- **Executions retention unchanged**: `ExecutionRepository._cleanup` still keeps only the newest 100
  executions globally, so Runs history is short.
- **Broker host policy** is enforced with an httpx request hook on the run's client (every outgoing
  request, after expression resolution), not by pre-parsing parameters; broker runs use
  `follow_redirects=False`. Nodes with opaque egress (SMTP, DB drivers, LLM SDKs) are refused when the
  project allowlist is non-empty or a secret is referenced.
- **Release dry-run** pins nothing: workflows whose draft differs from their latest version are marked
  `version_id: -1` ("would create") in the preview snapshot.
- **Release envs and app allow-lists**: release snapshots pin route-handler workflows and app
  versions. Published-app workflow runs (`/a/{slug}/api/workflow/...`) still run the **draft**
  (as before); only router traffic is pinned.
- **Code paths moved into the sandbox**: Code node, PythonStep, agent `run_code` tool. The agent's PTC
  `run_script` (`ai_agent.py:1159`) and `calculator_tool` `eval` remain in-process (out of scope).
- **Extra diff kinds** beyond G13: `request_field_removed` (non-breaking), `operation_renamed` (breaking).
- **Code routes** (`source:"code"`): method/path/auth/handler are owned by the module; PUT on them only
  accepts schema/description/response_mode/validate_request/timeout fields (`409 route_managed_by_code`).
- **Workflows used as route handlers** cannot be deleted or moved (`409 workflow_in_use`).

## 13. UI-DESIGN-REVIEW.md fixes (2026-10-15) — CHANGED

Fixes for every engine finding in `UI-DESIGN-REVIEW.md` except the generate / run-draft / fake-LLM
endpoints (§8.1/§8.2/E-section; built later from a revised spec). Each item below **amends** the
section it names; all response changes are additive unless marked **BREAKING**.

| Review # | Change (CHANGED) |
|---|---|
| D1 | §3.8: decorator `auth`/`name`/`description` must be **string literals**; `**kwargs`, extra positional args, unknown keywords and an `auth` outside `none\|app_token\|api_key` → `422 invalid_decorator {errors:[{line,column,message}]}`. Never falls back to `auth="none"`. |
| D2 | §3.2: `/api/variables/{id}` GET/PUT/DELETE take `?team_id=` (default `default`) and only reach **team-shared** rows of that team; project secrets and other teams' rows → `404`. |
| D4 | §5.3: the router redacts **body, headers and error messages** of every response centrally (`route_dispatcher.redact_result`, secret values + encoded variants), so Respond-node headers, python-route headers and `502 handler_failed.message` are covered; public-app runs redact Respond headers and the `WorkflowExecutionError` message. |
| D7 | §3.8: `POST …/modules?dry_run=true` → `200` dry-run result, nothing stored. Any write (`POST/PUT/PATCH/DELETE`) with a truthy `dry_run` on an endpoint without a dry-run mode → `422 {"detail":{"code":"dry_run_unsupported"}}` (ASGI `core/dry_run_guard.py`), never a silent real write. |
| D9 | G32: `DELETE …/modules/{mid}?dry_run=true` → dry-run result listing `routes.removed` + `breaking_changes`, nothing deleted. |
| C9 | §3.5/G9/G32: every dry run (route PUT/DELETE, module POST/PUT/DELETE) carries `{dry_run:true, routes:{created,updated,removed}, changes, breaking_changes, spec_revision, spec_hash}` (`Change` = `{kind,route,operation,route_id,field,breaking,affected:[…]}`). Route PUT dry-run keeps `route` and its current `spec_revision`. |
| C1 | CORS `expose_headers = X-Next-Cursor, X-Execution-Id, ETag, Allow`. |
| D17 | §3.3/G24: public-app run `errors[]` items carry `error_code` and `details`. |
| B4 | §3.8: `PUT …/modules/{mid}` accepts `base_source_sha` / `base_updated_at` (or `If-Match: "<source_sha>"`); a mismatch → `409 module_changed {current:{source_sha,updated_at}}` (dry runs too). `GET …/modules/{mid}` sends `ETag: "<source_sha>"`. No base = last write wins (compatible). |
| C7 | §3.8: module-save `409 route_conflict` / `name_taken` carry `{line, errors:[{line,column,message}], existing_route_id, handler:{type,id,name[,function]}}`; the message names the owning workflow. Same-module duplicates carry the line of the second declaration. |
| C8 | §3.8: a module save keeps route-level `description` edited through route settings; the decorator owns it only when it passes `description=` (or when the stored value is still what the previous source produced). Schemas, `timeout_s`, `validate_request`, `response_mode` were already preserved. |
| G-UI-10 | `Route.handler.line` (decorator line) for python routes. |
| D16 | `POST …/routes/{rid}/test` adds `error: {type, message, traceback, line, module, code, details[, node]} \| null` (redacted). Python: `line`/`module` from the innermost `/run/job/in/modules/<name>.py` frame. Never sent by the public router. |
| G-UI-4 | `/test` adds `trace` (same object as `GET …/runs/{id}.trace`, broker calls expanded). |
| B1 | DataTable on a missing table fails with `error_code:"table_not_found"`, `details:{table}` (canvas runs, broker `error.code`, router 502 body `error_code`, `/test` `error.code`). Broker node failures keep the node's error code (e.g. `row_invalid`) instead of `node_failed` when it has one. |
| D11 | G28: **every** `auth_changed` is breaking; it carries `security: "loosened"\|"tightened"`. |
| D13 | New change kind `response_type_changed` (breaking, `field:null`, `before`/`after`) when the root response type changes. |
| D15 | Code node (sandbox and legacy): syntax and runtime errors report the **user's** line (`Line N:` and `details.line`); legacy syntax errors now carry `error_code:"syntax_error"`. |
| G-UI-5 | `/api/sdk/functions[].python = {signature, params:[{name, property, type, default, required, enum, description}]}`. |
| C14 | New `GET /api/sdk/reference` → `{version, entries:[{name, kind, signature, params, doc,…}], rules:{secrets, decorators, responses}}`, generated from `sdk/static/*.py` by `inspect` (one source for the studio reference and the LLM context). `api.get/...` decorators now declare `description=` explicitly (no `**_`). |
| D5 | §5.1: `BrokerClaims.read_only` (default false). When set, the broker refuses (`403 {"error":{"code":"read_only"}}`, logged `denied`) DataTable operations other than literal `get`/`list`, `http_request` with a method other than literal `GET`/`HEAD`, `send_email`, and every opaque-egress function. `allowed_functions` is now **always set**: sandbox runs (`SandboxService.run(read_only=, allowed_functions=)`) and Code/PythonStep tokens get the exposed-function set at mint time; each agent studio-function tool gets a token for exactly its own node (tokens are kept per origin + function set). `POST …/broker-tokens` accepts `functions: [..]` (`422 unknown_function`) and `read_only`, and returns `read_only`, `allowed_functions`, and `expires_at` in **UTC with `Z`**. |
| D8 | §3.9: a release-pinned router run passes the release's module sources to the run (`extras.release_modules`); PythonStep/Code nodes in it import those, not the drafts. |
| §9 align | Aligned with the revised `UI-DESIGN.md §9` contracts: **D11** loosening is kind `auth_loosened` (breaking; tightening stays `auth_changed`); **C7** conflicts also carry `conflicts:[{method,path,line,existing_route_id,handler,message}]`, and a duplicate *inside one module* is `422 invalid_decorator` (was `409 route_conflict`, **BREAKING** for that case only, as the UI contract requires); **B4** body field `base_sha` (aliases `base_source_sha`, `If-Match`), `409 module_changed {current_sha, updated_at}`, and `PUT` accepts `name` (rename; `422 invalid_name` / `409 name_taken`); **D5** read_only errors carry `function` (+ `operation`), and the claim `virtual_tables` (`list → []`, `get → 404 row_not_found`, writes → `404 table_not_found`; also on dev tokens and `SandboxService.run`); `/test` `error.table`; module saves keep `response_mode`. |
