# Porting guide: `0fc9039` → `4f776cb`

For an AI agent porting this work into a copy of the repo that was taken at `0fc9039` and has since had small local
edits. Tests, fixtures and `apps/test-builder/` (QA scratch apps) are left out on purpose.

- **Base** (already ported): `0fc9039`, 2026-10-18, "Studio: real frosted glass on every app page and the workflow editor"
- **Target**: `4f776cb`, 2026-10-21, "Studio + builder: one-app overview, plan in the app builder, icy frost backdrop"
- Branch: `revert-to-5684ba5` on `origin`

## How to port

1. Fetch this branch alongside yours: `git fetch <this-remote> revert-to-5684ba5`.
2. **Added files (A)** — copy as is: `git checkout 4f776cb -- <path>`. They didn't exist at the base, so local edits can't clash.
3. **Modified files (M)** — don't copy over. Take the hunks: `git diff 0fc9039 4f776cb -- <path>` and apply them to the
   local version (`git apply -3` first; by hand where it fails). Most are small wiring changes, listed below with what
   each hunk does.
4. **Deleted files (D)** — delete, then fix any local imports of them (the replacement is named below).
5. Port in the order of the sections: engine data layer → engine services → engine routes/wiring → studio API →
   studio features → studio routes/shell → styling.
6. Check: engine imports (`python -c "import src.main"` from `apps/workflow-engine`), apply the migrations, studio
   `npx tsc -b` 0 errors.

---

## 1. Engine (`apps/workflow-engine/src/`)

### 1.1 Database: new tables (all A)

Apply the migrations in timestamp order; each has an ORM model.

| Migration (`db/migrations/`) | Model (`db/`) | What it stores |
|---|---|---|
| `20261024120000_project_knowledge.sql` | `knowledge_models.py` | per-project knowledge store for the builders |
| `20261025120000_module_schedules.sql` | `schedule_models.py` | cron-scheduled module functions |
| `20261026120000_project_plans.sql` | `plan_models.py` | versioned project plan |
| `20261026130000_data_table_migrations.sql` | `schema_models.py` | additive data-table schema migrations |
| `20261027120000_project_builds.sql` | `build_models.py` | builds of an approved plan |
| `20261028120000_project_file_changes.sql` | `file_change_models.py` | project file change log |
| `20261029120000_project_connections.sql` | `connection_models.py` | external DB connections (bring-your-own DB) |

### 1.2 The app builder: one-shot builder, scaffold, app kit

The core of the builder work.

- **A** `services/oneshot_builder.py` — one LLM call writes the whole app (backend modules + UI files) from the plan.
- **A** `services/app_scaffold.py` — deterministic scaffold: CRUD backend + UI generated from the plan; the model only fills app-specific slots.
- **A** `services/app_kit/` (`ui.tsx`, `resource.tsx`, `DataTable.tsx`, `charts.tsx`, `hooks.ts`, `live.tsx`, `review.tsx`, `index.ts`) — the component kit copied into every generated app as `kit/*`.
- **A** `services/app_validate.py` — static checks on generated app files.
- **A** `services/app_autotest.py` — automatic post-build checks of a generated app.
- **A** `services/backend_templates/` (whole folder, incl. `templates/*.py.tpl`) — backend module templates, scaffold, self-test, gap/quality checks, patching.
- **A** `services/builder_gates.py` — gates run on saved modules/apps (see route below).
- **M** `services/app_builder_ai_service.py` (+2196/−103) — **largest change**. The chat endpoint now routes new apps to the
  one-shot builder and edits through the scaffold/kit path; it adds plan awareness, auto-tests and checks events.
  If the local file has few edits, take the target version whole and re-apply the local edits on top; that's
  easier than merging 2k lines of hunks.
- **M** `services/module_generator.py` (+1197/−176) — same advice: take the target, then re-apply local edits. Adds table
  declarations, templates and gates to module generation.
- **M** `services/app_service.py` (+24) — app save hooks for the kit/scaffold.
- **M** `schemas/app_builder.py` (+1) — one new request field.

### 1.3 Plan, builds, files, knowledge

- **A** `services/project_plan.py`, `plan_clarify.py` (one-round requirement checklists), `plan_checklist.py`, `plan_compact.py`
- **A** `services/build_coordinator.py` — runs a build of the approved plan (modules, then the app); resumable.
- **A** `services/project_files.py` — one file tree over everything the builders wrote, plus history.
- **A** `services/project_knowledge.py`, `services/platform_index.py` — retrieval context for the builders.

### 1.4 Data, connections, schedules, jobs

- **A** `services/external_db.py`, `services/connection_service.py` — external Postgres connections + schema introspection.
- **A** `services/schema_migrations.py` — additive table migrations, used by `data_table_service`.
- **M** `services/data_table_service.py` (+402) — declared tables: create/additively migrate on module save; system columns + row timestamps.
- **M** `services/code_module_service.py` (+389) — module save accepts `tables`, runs gates, records file changes.
- **A** `services/module_schedules.py` + **M** `engine/cron_scheduler.py` (+27) — schedule module functions.
- **A** `broker/jobs.py` + **M** `broker/policy.py`, `broker/service.py` — `studio.jobs.*` broker calls to the Jobs service.
- **M** `nodes/integrations/postgres.py` (+100) — the Postgres node can use a project connection.
- **M** smaller: `route_dispatcher.py` (+73), `python_handler.py`, `secret_service.py`, `draft_runner.py`,
  `engine/llm_provider.py` (+63, provider options), `engine/tool_schema.py`, `engine/workflow_runner.py`,
  `nodes/ai/ai_agent.py`, `nodes/data/data_table.py`, `core/public_tokens.py`.

### 1.5 SDK available to generated module code

- **A** `sdk/static/resources.py` (resource SDK used by scaffolded CRUD), `sdk/static/jobs.py`, `sdk/static/connections.py`
- **M** `sdk/static/db.py` (+40), `sdk/static/api.py` (+25), `sdk/generate.py` (+51) — expose the new SDK modules.

### 1.6 Routes and wiring

- **A** routes: `routes/project_plan.py`, `builds.py`, `project_files.py`, `project_knowledge.py`, `module_schedules.py`,
  `builder_gates.py`, `project_connections.py`
- **M** `routes/__init__.py` — import and `include_router` the seven routers above. **Order matters**: `builder_gates_router` is
  included before `apps_router` (comment in the file).
- **M** `main.py` (+8) — lifespan: `build_coordinator.startup_sweep(...)` on start, `build_coordinator.shutdown()` on stop.
- **M** `core/config.py` (+17) — `jobs_service_url`, `jobs_service_token`, `jobs_dev`, `jobs_call_timeout_s` (env `JOBS_SERVICE_URL`, …).
- **M** `routes/modules.py`, `routes/tables.py`, `routes/api_routes.py` — pass `tables` through; small additions.

## 2. Jobs service (`apps/jobs-service/`, all A)

A new standalone Node service (port 8095) for long browser/API test runs, AI triage and exploration. Copy the folder
(skip `tests/`); `README.md` explains how to run it. The engine reaches it only through `broker/jobs.py`.

## 3. Studio (`apps/workflow-studio/src/`)

### 3.1 API layer

- **M** `shared/api/endpoints.ts` — `schedulesApi`; modules `create`/`update` accept `tables`.
- **M** `shared/api/types.ts` (+22) — `ModuleSchedule`, `DeclaredTable`, etc.
- **M** `shared/api/queryKeys.ts` (+1).

### 3.2 New feature folders (all A — copy whole)

- `features/plan/` — plan flow (questions → document → approval), build timeline, build view, project docs.
- `features/build-studio/` — live build view: code pane, preview pane, ready card.
- `features/code/` — project file tree, viewer/editor, history, change log.
- `features/connections/` — connect a database, schema browser, sources picker.
- `features/project/overview/home/` + `overview/lib/{pulse,stats,useWidth}.ts` — the new one-app overview: launcher row
  of app/workflow preview cards (`AppSketch`), Usage band (`Widgets`), Data section, Builds.
- `features/project/components/HomeNav.tsx`, `WorkbenchIndex.tsx`, `WorkbenchSwitcher.tsx`, `hooks/useMissingCount.ts`,
  `hooks/useProjectPulse.ts`, `lib/entities.ts`, `lib/places.ts`.
- `features/backend/BackendIndex.tsx`, `features/workflows-list/workflowMeta.ts`.
- `features/workflow-editor/components/ndv/extensions/PostgresConnectionExtension.tsx`.

### 3.3 App builder (`features/app-builder/`)

- **A** `components/ChecksChip.tsx`, `NewAppBuilder.tsx`, `ViewAsMenu.tsx` (preview as a given user);
  `lib/autotest.ts`, `chatEvents.ts`, `clientGate.ts`, `previewErrors.ts` (collect preview errors + "Fix with AI"),
  `previewUsers.ts`, `restoreChat.ts`.
- **M** `components/AppBuilderShell.tsx` (+151) — Plan tab (`?view=plan` deep link), auto-fix of preview errors, checks chip.
- **M** `components/AppPreviewPanel.tsx` (+70) — View-as menu, errors bar with "Fix with AI".
- **M** `components/AppBuilderChatPanel.tsx`, `hooks/useAppBuilderChat.ts`, `stores/appBuilderChatStore.ts` — chat events, restore.
- **M** `sandbox/IframeSandbox.tsx`, `sandbox-runtime.ts`, `requestPolicy.ts`, `esbuild-bundler.ts`, `bridge.ts` — error
  reporting from the iframe, kit bundling, request policy for the preview.
- **M** small: `ProjectAppBuilder.tsx`, `ApiTesterPanel.tsx`, `PublishDialog.tsx`, `VersionHistory.tsx`, `panels.tsx`.

### 3.4 Routes and project shell (no sidebar any more)

- **M** `app/routes/project.tsx` — new routes `backend` (BackendIndex), `build/$build` (BuildView), `code` (CodeView); `plan`
  now redirects home; old `apps`/`workflows` list URLs redirect to the Overview.
- **D** `features/project/components/ProjectOutline.tsx` → replaced by `HomeNav` (way home + jump menu in the top bar).
- **D** `features/workflows-list/WorkflowsListPage.tsx`, `WorkflowCard.tsx` → workflows are listed on the Overview; helpers moved to `workflowMeta.ts`.
- **M** `ProjectLayout.tsx` — mounts `WorkspaceOverlays` (moved here from WorkspaceLayout); fixes the slug-redirect deep-link bug.
- **M** `WorkspaceLayout.tsx` — outline/rail removed, full-window sheet 8px from the edges; shortcut `g h` = home; `[` and `g w` removed.
- **M** `ProjectTopBar.tsx`, `OverviewView.tsx`, `SetupGuide.tsx`, `setup/parts.tsx`, `overview/{Backend,Releases}Tile.tsx`,
  `BuildingBlocks.tsx`, `story/*` — follow the new shell.
- **M** small edits elsewhere: `backend/{BackendView,NewModuleDialog,useSaveModule}`, `data/DataView`, `knowledge/*`,
  `api-catalog/ApiCatalogView`, `ai-builder/*`, `releases/ReleaseSheet`, `runs/RunSheet`, `settings/SettingsSheet`,
  `workflow-editor/` (page, bottom panel, proposal pill, env modal, logs, history, node creator, navbar, NodeExtensions).

### 3.5 Styling: light backdrop, glass, icons (`index.css` + primitives)

All of it is in **M** `index.css` (+110/−31) and a few one-line primitive changes. The glass panes themselves are unchanged.

- **Icy frost backdrop**: the light backdrop gradient (block under "Every app page but the landing … icy frost") swaps the
  blue/violet colour pools for a near-neutral cool off-white with a soft grey middle.
- **White cards inside glass**: `:root` tokens `--well`, `--inner-fill-hover`, `--raised-fill`, `--raised-ring`,
  `--raised-contact`, `--inner-elevation` become near-solid white (light) / faint white (dark). The old `--glass-fill*`,
  `--chrome-*`, `--glass-blur` overrides in that block are removed (they fall back to the base glass values).
- **Icons**: `@utility icon-tile` becomes a small glass "gem" (tinted gradient, top gleam, lit rim, hue glow).
  `shared/components/ui/surface.tsx` adds `icon-tile` to hue-less header icons.
- **Glass icon buttons**: new `@utility glass-bubble`, auto-applied to ghost icon buttons in headers/strips (selector right
  after the utility). `ui/button.tsx`: `outline` and `secondary` variants become `glass-bubble text-foreground`, and the button
  sets `data-size`, which that selector needs.
- **Stepper**: new `@utility glass-orb` / `glass-orb-clear`; `ui/stepper.tsx` uses them for done/current/pending/error.
- **Primary buttons**: `.brand-fill` flat brand gradient + glow; **badges**: hairline edge in their own hue.
- **Etched text on glass**: `text-shadow` on text inside `.glass, .tile, .glass-editor, .glass-chrome, .glass-strip,
  .glass-overlay, header`, turned off for solid fills, inputs and code.
- Small: `ui/sheet.tsx`, `ui/code-editor.tsx`, `ai-elements/message.tsx` (one-liners following the above).

## 4. Not in this commit

The visual editor for generated apps (`features/app-builder/visual/`, the in-place edit runtime, the theme panel) was in
progress and **uncommitted** when this was pushed. It will come as a separate commit with its own section here.
