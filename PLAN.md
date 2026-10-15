# PLAN — One studio: build UI + backend together, connected automatically

Written 2026-10-14 against commit `5684ba5` (branch `revert-to-5684ba5`). Supersedes
`PLAN-ENVIRONMENTS.md`. Nothing is ported from older branches.

## Goal
A team creates a **project**. In it they build a **backend** (API routes, automations, data —
on the canvas or in Python, using nodes-as-functions) and **UIs** (AI app builder). The two
connect **automatically**: no copied URLs, no hand-written clients, no guessed response shapes.

## Principles
1. **The project is the unit.** Apps, API routes, automations, tables, secrets, runs, releases
   all belong to one project.
2. **The API contract is the seam.** Every route has method, path, schemas, auth → the project's
   OpenAPI spec is generated, and the app builder consumes it.
3. **One node library, three surfaces.** Every node: draggable on canvas, callable as a Python
   function, usable by an AI agent. Generated from `node_registry` — add once, appears everywhere.
4. **Automatic by default.** If the platform can work it out, the user never types it.
5. **Credentials never reach user code.** Secrets stay in the engine; all node calls go through the broker.

## What "automatic" means
| What | How |
|---|---|
| Node functions in Python | generated from the registry |
| Route request/response schemas | inferred from a test run (existing `services/schema_inference.py`), editable |
| Project OpenAPI spec | regenerated on every route save |
| App's API client | regenerated when the spec changes; app builder AI always sees current routes |
| Routes for a UI | endpoints the AI's UI needs become route stubs ("not implemented") |
| "Used by" | existing `services/tsx_parser.py` scans app source for route calls |
| Breaking changes | spec diff on save → which apps/calls break |
| Missing secrets | node/function needs a token that doesn't exist → inline "create `$vars.X`?" |
| Release safety | release blocked if an app calls a missing/changed route |
| Display hints | existing `services/schema_analyzer.py` labels response fields |

## Information architecture
```
Home = Projects grid  (name · apps · routes · last run · health)
  └─ /p/$projectId                                   env: [dev ▾]  [Release]
       Overview · Apps · API · Automations · Data · Secrets · Runs · Settings
```
Old `/editor` and `/builder` URLs redirect into the project shell.

## Phases
1. **Projects foundation** — `projects` table; `project_id` on workflows, apps, data_tables;
   nullable `project_id` on variables (null = team-shared). Existing rows → "Default project";
   folders stay as organisation inside a project. Projects CRUD + summary. Studio: home = projects
   grid, `/p/$projectId` shell, editor/builder inside it.
2. **Secrets** — project Secrets page; project value overrides team value; write-only values
   (already enforced, `routes/variables.py:66`); inline "create missing secret".
3. **Published apps run real workflows** — replace the stub at `routes/public_apps.py:399` with a
   real run via `ExecutionService`, keeping app token scope + workflow allowlist.
4. **Nodes as functions** — `src/sdk/generate.py` (registry → `studio` package) + `src/broker/`
   (`POST /api/broker/call`: run-scoped token, one node via `WorkflowRunner.run`, environment-bound
   credentials only — fixes the ownership gap at `engine/workflow_runner.py:962` — host allowlist,
   full-response redaction, call log). Usable from Code node + AI agent (labelled unsandboxed until 8).
5. **API section** — `api_routes` (project, method, path with `{params}`, auth none/app-token/
   API key, request/response schema, handler type + id); router `/api/p/{project}/{path}` passing
   `req.path/query/body/headers`; status/headers from handler; per-project API keys; OpenAPI per
   project; schema inference from a test run.
6. **App builder ↔ API** — routes listed for apps; typed client generated from the spec; app
   builder AI gets the spec (replacing the single attached endpoint, `services/app_builder_ai_service.py:798`);
   "generate UI for this API"; endpoint stubs declared from UI side; "used by"; breaking-change warnings.
7. **Data** — make `data_tables` usable: CRUD API, Table node, `studio.db.table()`.
8. **Python handlers in a sandbox** — `SandboxBackend` interface + bubblewrap impl (no network;
   broker over a Unix socket; no env secrets/.env/host files); route handler type "python"
   (`@api.get(...)`); `PythonStep` node; existing Code node moved into the sandbox (today it
   `exec`s in-process, `nodes/data/code.py:203`); per-request call trace.
9. **Releases** — snapshot app versions + API version + secret bindings per environment;
   promote dev→prod; automatic checks; one-click rollback.
10. **Project AI assistant** — knows the whole project; drafts table → routes/handlers → UI.
    Needs an LLM key (none configured) — deferred.

## Cross-cutting
- Tests from day one (28 exist today). No auth yet: engine trusts `X-User-Id`; fine locally, required
  before exposure. Never push this branch or `main` (history has live Google API keys).
  `engine/llm_provider.py:100-104` hardcodes an internal-looking proxy hostname — review before sharing.

## Decisions made
New `projects` table (folders stay inside projects) · secrets at project + team level, project wins ·
canvas and Python handlers both first-class, one node library · bubblewrap sandbox now behind an interface.
