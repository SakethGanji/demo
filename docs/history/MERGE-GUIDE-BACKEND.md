# Merge guide: backend

This branch (`revert-to-5684ba5`) has the latest backend updates. This guide lists what changed and where, so they can
be merged into our code. The UI side is in `MERGE-GUIDE-UI.md`. Tests are not covered.

**Skip these two folders entirely.** Nothing in the engine or studio needs them:
- `apps/test-builder/`: QA scaffolding (sample apps and scripts used to test the builder), not product code.
- `apps/jobs-service/`: an optional sidecar for long test runs (section 7). Without it the engine runs normally; only
  `studio.jobs.*` calls return `jobs_not_configured`.

- **From**: `0fc9039`, "Studio: real frosted glass on every app page and the workflow editor"
- **To**: `4f776cb`, "Studio + builder: one-app overview, plan in the app builder, icy frost backdrop"

## How to merge

1. **Added files (A)**: bring them over as they are: `git checkout 4f776cb -- <path>`.
2. **Modified files (M)**: merge only the changes, keeping ours: `git diff 0fc9039 4f776cb -- <path>`, then apply the hunks
   (`git apply -3` first, by hand where it fails). The notes below say what each change does.
3. Go in section order: data layer → services → routes and wiring.
4. Check: `python -c "import src.main"` from `apps/workflow-engine` imports cleanly, and the migrations apply.

## 1. Database: new tables (all A)

Apply the migrations in timestamp order; each has an ORM model. All paths are under `apps/workflow-engine/src/`.

| Migration (`db/migrations/`) | Model (`db/`) | What it stores |
|---|---|---|
| `20261024120000_project_knowledge.sql` | `knowledge_models.py` | per-project knowledge store for the builders |
| `20261025120000_module_schedules.sql` | `schedule_models.py` | cron-scheduled module functions |
| `20261026120000_project_plans.sql` | `plan_models.py` | versioned project plan |
| `20261026130000_data_table_migrations.sql` | `schema_models.py` | additive data-table schema migrations |
| `20261027120000_project_builds.sql` | `build_models.py` | builds of an approved plan |
| `20261028120000_project_file_changes.sql` | `file_change_models.py` | project file change log |
| `20261029120000_project_connections.sql` | `connection_models.py` | external DB connections (bring-your-own DB) |

## 2. App builder: one-shot builder, scaffold, app kit

The core of the builder work.

- **A** `services/oneshot_builder.py`: one LLM call writes the whole app (backend modules and UI files) from the plan.
- **A** `services/app_scaffold.py`: a deterministic scaffold that generates the CRUD backend and UI from the plan. The model only fills the app-specific parts.
- **A** `services/app_kit/` (`ui.tsx`, `resource.tsx`, `DataTable.tsx`, `charts.tsx`, `hooks.ts`, `live.tsx`, `review.tsx`, `index.ts`): the component kit copied into every generated app as `kit/*`.
- **A** `services/app_validate.py`: static checks on generated app files.
- **A** `services/app_autotest.py`: automatic checks of a generated app after a build.
- **A** `services/backend_templates/` (whole folder, including `templates/*.py.tpl`): backend module templates, scaffold, self-test, gap and quality checks, patching.
- **A** `services/builder_gates.py`: checks ("gates") run on saved modules and apps. The route is in section 6.
- **M** `services/app_builder_ai_service.py` (+2196/−103): **the largest change**. The chat endpoint now sends new apps to the
  one-shot builder and edits through the scaffold/kit path. It also adds plan awareness, auto-tests and checks events.
  Because the change is so large, it's easier to take the new version whole and re-apply our own edits on top than to
  merge the hunks.
- **M** `services/module_generator.py` (+1197/−176): the same advice applies. It adds table declarations, templates and
  gates to module generation.
- **M** `services/app_service.py` (+24): app save hooks for the kit and scaffold.
- **M** `schemas/app_builder.py` (+1): one new request field.

## 3. Plan, builds, files, knowledge (all A)

- `services/project_plan.py`, `plan_clarify.py` (one-round requirement checklists), `plan_checklist.py`, `plan_compact.py`
- `services/build_coordinator.py`: runs a build of the approved plan (modules, then the app). Builds can be resumed.
- `services/project_files.py`: one file tree over everything the builders wrote, plus history.
- `services/project_knowledge.py`, `services/platform_index.py`: retrieval context for the builders.

## 4. Data, connections, schedules, jobs

- **A** `services/external_db.py`, `services/connection_service.py`: external Postgres connections and schema introspection.
- **A** `services/schema_migrations.py`: additive table migrations, used by `data_table_service`.
- **M** `services/data_table_service.py` (+402): declared tables are created or additively migrated when a module is saved; adds system columns and row timestamps.
- **M** `services/code_module_service.py` (+389): module save accepts `tables`, runs the gates and records file changes.
- **A** `services/module_schedules.py` + **M** `engine/cron_scheduler.py` (+27): scheduled module functions.
- **A** `broker/jobs.py` + **M** `broker/policy.py`, `broker/service.py`: `studio.jobs.*` broker calls to the Jobs service.
- **M** `nodes/integrations/postgres.py` (+100): the Postgres node can use a project connection.
- **M** smaller changes:
  - services: `route_dispatcher.py` (+73), `python_handler.py`, `secret_service.py`, `draft_runner.py`
  - engine: `llm_provider.py` (+63, provider options), `tool_schema.py`, `workflow_runner.py`
  - nodes: `nodes/ai/ai_agent.py`, `nodes/data/data_table.py`
  - core: `core/public_tokens.py`

## 5. SDK available to generated module code

- **A** `sdk/static/resources.py` (the resource SDK used by scaffolded CRUD), `sdk/static/jobs.py`, `sdk/static/connections.py`
- **M** `sdk/static/db.py` (+40), `sdk/static/api.py` (+25), `sdk/generate.py` (+51): expose the new SDK modules.

## 6. Routes and wiring

- **A** routes: `routes/project_plan.py`, `builds.py`, `project_files.py`, `project_knowledge.py`, `module_schedules.py`,
  `builder_gates.py`, `project_connections.py`
- **M** `routes/__init__.py`: import the seven routers above and call `include_router` for each. **Order matters**:
  `builder_gates_router` must be included before `apps_router` (there is a comment in the file).
- **M** `main.py` (+8): in the lifespan, call `build_coordinator.startup_sweep(...)` on start and `build_coordinator.shutdown()` on stop.
- **M** `core/config.py` (+17): new settings `jobs_service_url`, `jobs_service_token`, `jobs_dev`, `jobs_call_timeout_s` (env `JOBS_SERVICE_URL`, …).
- **M** `routes/modules.py`, `routes/tables.py`, `routes/api_routes.py`: pass `tables` through, plus small additions.

## 7. Jobs service (`apps/jobs-service/`): optional, skip

Not needed. It's a standalone Node service for long browser/API test runs and AI triage, used only by generated apps
that run test jobs. The builder's self-tests replace it with a fake. The engine-side pieces (`broker/jobs.py`,
`sdk/static/jobs.py`, the `jobs_*` settings in `core/config.py`) are harmless to merge, and simplest to keep because
`backend_templates/selftest.py` refers to `studio.jobs`.
