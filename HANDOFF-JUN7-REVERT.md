# Handoff — the June 7 version (branch `revert-to-5684ba5`)

Written 2026-10-14. Scope: **this version only.** Everything below was either
run in the previous session (marked ✅) or only read (marked 👁) or not touched (⬜).

## Context in four lines
- Owner returned after months away, judged the ~200 later commits too sprawling, and
  chose to **restart from commit `5684ba5`** (2026-06-07, PromptLab standalone +
  env-profiles moved into the workflow editor). They want to test and build on it.
- Do **not** bring newer work forward unless asked. They said so explicitly.
- Branch `revert-to-5684ba5` is checked out, tree clean, no code changed.
- Never push this branch or `main`: that history contains live Google API keys.
  Backup tags `backup/clean-main-2026-10-14` and `backup/main-2026-10-14` hold the
  newer work (98 of its commits exist only locally). Leave them alone.

## What this version is
A visual workflow-automation platform ("Command Studio", n8n-style) plus a young
analytics API. Three apps in `apps/`:

| App | Stack | State at this commit |
|---|---|---|
| `workflow-engine` | Python 3.14, FastAPI, async SQLAlchemy/asyncpg, Postgres | The core. ~40 node types, runs workflows over HTTP. |
| `workflow-studio` | React 18, Vite, TanStack Router/Query, Tailwind, React Flow | Landing page, `/projects`, `/editor` canvas, `/builder` (app builder). |
| `analytics-service` | FastAPI, DuckDB, pandas | Dataset/file/transform/quality/relationships APIs. Imports OK; **not wired into the studio** (studio talks only to :8000). No tests. |

**Engine node types** (`src/nodes/`): triggers (Start, Webhook, Cron, ErrorTrigger,
ExecuteWorkflowTrigger); flow (If, Switch, Loop, Merge, Wait, Poll, StopAndError,
ExecuteWorkflow); data (Set, Filter, Code, Aggregate, Sample, Profile, ItemLists,
ObjectStore); integrations (HttpRequest, MongoDB, Neo4j, Postgres, SendEmail);
AI (LlmChat, AiAgent, ChatInput, ~14 memory types, ~15 agent tools).
**Routes** (`src/routes/`): workflows, executions, nodes, webhooks, streaming,
credentials, variables (env-scoped `$vars`), apps + public_apps (app builder),
files, folders, ai_chat, app_builder_ai, api_tester.
**Extras:** PromptLab (agentic prompt-optimisation loop over MongoDB; guide in
`apps/workflow-engine/PROMPTLAB.md`; standalone script `prompt_lab_standalone.py`).
Single baseline migration `20260307120000_baseline.sql` (20 tables).

## What was verified ✅
- Engine starts; `GET /api/workflows` lists the 3 seeded workflows.
- A self-built Start→Set workflow ran with status `success` and computed `{{ 20 + 22 }}` = 42.
- Studio landing page loads with no console errors. `/editor?workflowId=<id>` renders
  the canvas with toolbar (undo/redo, zoom, Publish, Run, Save, env "default"), a
  Nodes/AI side panel, and the nodes. Only console noise: two React
  `validateDOMNesting` warnings (a `<button>` inside a `<button>`, seen on /projects or editor).
- Engine tests: `cd apps/workflow-engine && venv/bin/python -m pytest tests -q`
  → **28 passed** (all in one file, the Mongo query guard). This is essentially no coverage.

## Known not working here (all environmental, not code bugs)
All three seeded workflows **fail when run**, each for a missing external:
- *Vars Redaction Demo* → needs a mock protected API ("All connection attempts failed"; `dummy_excel_api.py` in the engine dir is probably it).
- *Prompt Evaluator* → needs Google credentials: "Your default credentials were not found" (Application Default Credentials / Gemini/Vertex). No LLM key is configured on this machine.
- *PromptLab* → needs MongoDB on :27017 (`docker-compose.yml` defines mongo; nothing running).
So: **no AI/LLM/PromptLab feature has been exercised.**

## Not checked ⬜
Webhook/Cron triggers, publish flow, app builder (`/builder`), AI chat panel, Loop/Switch/Merge,
Postgres/Mongo/Neo4j nodes, SendEmail, the analytics service actually serving requests,
and how credentials/`$vars` behave. The If node returned no output in one smoke run, but
the test used a guessed parameter shape, so that's unknown, not a bug.

## Running it
Currently running (will stop if the machine restarts):
- Studio http://localhost:5173 · API http://localhost:8000 (`/docs` for Swagger)
- Postgres: **scratch** instance on :5433, user/pass/db all `workflow`, schema `"workflow-app"`.
  Data dir is in the previous session's scratchpad; disposable. Docker on this machine is
  dead (`sudo systemctl reset-failed docker && sudo systemctl start docker` fixes it, needs the owner's password),
  after which `docker compose up postgres mongo` in `apps/workflow-engine` is the intended path.

From scratch:
1. Postgres up; `create database workflows;` then `create schema "workflow-app";` (the migrator does **not** create the schema).
2. `cd apps/workflow-engine`; export `WORKFLOW_DB_PASSWORD=workflow WORKFLOW_DB_PORT=5433 WORKFLOW_DB_HOST=localhost WORKFLOW_ENCRYPTION_KEY=<fernet key>` (the password var is mandatory at this commit; defaults differ from newer code; `.env.example` shows the rest).
3. `venv/bin/python -m src.db.migrate apply && venv/bin/python -m src.db.seed`
4. `venv/bin/python -m uvicorn src.main:app --port 8000`
5. `cd ../workflow-studio && npx vite` (port 5173).
Gotchas: `venv/` and `node_modules/` are untracked and were built for the newer code
(worked fine, but reinstall from this commit's `requirements.txt`/lockfile if imports break).
Headless screenshots: Chromium at `/usr/bin/chromium`, driven via `playwright-core` from
`workflow-studio/node_modules`. Engine reads identity from `X-User-Id`; no login needed for the studio.

## Risks to know before building
- Test coverage is near zero. Add tests before refactoring anything.
- The repo root has stray files: `promptlab (2).json`, `promptlab-v2.json`, `test-results/`, `screenshots/`, a sample CSV, and a 20-table baseline migration with no later history.
- Untracked leftovers from the other branches: `apps/analytics-service/web/` (node_modules etc.) and `.coverage`.
- Credentials exist in this version (`routes/credentials.py`, Fernet-encrypted), but nodes can't *reference* them; that capability only exists on the newer branch.

## Suggested next steps (ask the owner which)
1. Start Docker (or keep the scratch Postgres) and bring up **Mongo**, then try PromptLab for real; supply Google ADC or an LLM key for the AI nodes.
2. Make one workflow succeed end to end through the UI (build in `/editor`, Run, inspect execution) to establish a real baseline.
3. Decide scope deliberately: what from the later work, if anything, is wanted. Previously flagged as most valuable: the security fixes and the single graph validator; most questionable: the agent platform and the repeated UI redesigns.
