# UI-INTEGRATION — studio ↔ new backend (PLAN phases 1–9)

Written 2026-10-14 against `5684ba5` (branch `revert-to-5684ba5`), from `PLAN.md` and
`BACKEND-DESIGN.md` §3 (including the §9 decision-4 **CHANGED** `validate_request` field). Paths
are relative to `apps/workflow-studio/src/` unless they start with `engine:` (=
`apps/workflow-engine/src/`) or are top-level docs. "§x.y" means a BACKEND-DESIGN section.
Everything here is documentation; no code was changed.

Placeholders used throughout: `P` = project id (`proj_…`), `S` = project slug, `W` = workflow id,
`A` = app id, `R` = route id, `T` = table id, `M` = module id, `REL` = release id, `X` = execution id,
`UP` = the harness echo upstream base URL (`http://127.0.0.1:<port>`, `tests/integration/conftest.py:220`).
`SET_WF(expr, field="answer")` = the harness workflow `start_set_workflow`
(`engine:../tests/integration/conftest.py:312-326`): `Start → Set{mode:"manual", fields:[{name:field, value:expr}], keepOnlySet:true}`.

---

## 0. Facts about the current studio that shape this spec (verified)

| # | Fact | Evidence | Consequence |
|---|---|---|---|
| F1 | Routing is code-based (`createRoute` + `addChildren`), no file-route plugin. | `app/routes/index.ts:8`; `vite.config.ts` has only react + tailwind plugins | New routes are new `createRoute` modules registered in `app/routes/index.ts`. |
| F2 | The root layout decides "full-screen" by matching `/editor`, `/builder`, `/projects`. | `app/routes/__root.tsx:16-33` | `/p/*` must be added to the full-screen match or the shell gets the fallback layout. |
| F3 | The `QueryClient` is created inside a component's `useState`. | `app/providers/QueryProvider.tsx:15-29` | Redirect `beforeLoad`s need the client → hoist it to `app/queryClient.ts` and pass it as router context. |
| F4 | `apiFetch` reads `errorData.error` on failure; FastAPI puts errors in `detail`. So almost every studio error today reads `HTTP 4xx`. | `shared/lib/api.ts:77-79` | New `ApiError` parser (§3.1 below). Keeps `Error.message`, so existing `toast(error.message)` call sites keep working and get better text. |
| F5 | Many calls bypass `apiFetch` with raw `fetch`: editor load, variables, SSE, AI chat, app chat, file browse, preview bridge. | `app/routes/editor.tsx:134`; `features/workflow-editor/hooks/useVariablesApi.ts:42-101`; `useExecutionStream.ts:134`; `useAIChat.ts:79`; `features/app-builder/hooks/useAppBuilderChat.ts:49`; `ndv/FilePathField.tsx:59`; `sandbox/IframeSandbox.tsx:110` | The GETs/DELETE in `useVariablesApi.ts:44,54,101` and `editor.tsx:134` send **no** `Content-Type` header, contradicting §0/§7.1 ("sent on every call including GET and DELETE"). Harmless, but the harness should mirror each call site (gap G31). |
| F6 | The selected environment is one global localStorage value, default `'default'`, and it is what SSE runs send. | `stores/envProfilesStore.ts:12-13,33-39`; `useExecutionStream.ts:113-131` | New projects have `dev`/`prod` only (§9 decision 2). Environment must become **per project**, defaulting to `project.default_environment`. |
| F7 | The env dropdown merges a hard-coded fallback `['default','dev','uat','prod']` with `/api/variables/environments`. | `components/env-profiles/EnvProfileSelector.tsx:17-30` | Inside a project the options are exactly `project.environments[].name`. |
| F8 | The NDV "execute" button uses `run-adhoc` **without** `environment`, so it always resolves `$vars` from `default`. | `ndv/NodeDetailsModal.tsx:71,100` → `hooks/useWorkflowApi.ts:181-211` → `shared/lib/api.ts:137-142` | Existing bug. Fixed by sending `environment` + `project_id` (§5). |
| F9 | `useWorkflows` does `GET /api/workflows` + N×`GET /api/workflows/{id}`; `WorkflowSelectorField` reuses it. | `features/projects/hooks/useWorkflows.ts:55-63`; `ndv/WorkflowSelectorField.tsx:35` | Project lists use `GET /api/projects/{P}/workflows`; the ExecuteWorkflow selector lists project workflows only. |
| F10 | Navigation to `/editor`, `/builder`, `/projects` is hard-coded in 10 places. | `projects.tsx:115`; `useWorkflowActions.ts:17`; `useWorkflowApi.ts:77-81,152-156`; `useAppActions.ts:106,127,235`; `landing.tsx:160,166,203,210`; `WorkflowNavbar.tsx:128`; `AppBuilderShell.tsx:255` | All become project-relative links (§1.4). |
| F11 | An app's `workflow_ids` allow-list is only ever set by import; there is no UI for it. | `features/projects/hooks/useAppActions.ts:223-231` (only writer) | Phase 3 makes `/a/{slug}/api/workflow/{id}/run` real, so the app Settings panel must edit `workflow_ids` (§2.4). |
| F12 | The preview bridge proxies every `apiRequest` message without checking `event.source`. | `sandbox/IframeSandbox.tsx:63-67,92-93` | Before the parent injects preview tokens, it must ignore messages not from its own iframe. |
| F13 | The preview bundles **only the stored files**; there is no place for a generated file. | `IframeSandbox.tsx:37-43`; `sandbox/esbuild-bundler.ts:85` (`fileMap` from `files`) | `api/client.ts` is merged in by the shell at preview time and never written to the document store. |
| F14 | `sandbox-runtime` forwards `opts.headers` as-is through `postMessage`; a `Headers` instance is not structured-cloneable, so such a fetch throws in the iframe. | `sandbox/sandbox-runtime.ts:55-63` | Normalise headers to a plain object inside the runtime (§4.7). |
| F15 | The shared CodeMirror wrapper supports only `json` and `javascript`. | `shared/components/ui/code-editor.tsx:16,100` | The Python module editor needs `@codemirror/lang-python` (new dependency) and a `'python'` language option. |
| F16 | Existing query keys: `['workflows']`, `['apps']`, `['latest-executions']`, `['nodes']`, `['variables', env]`, `['variable-environments']`, `['api-tester','executions']`. | `useWorkflows.ts:68`; `useApps.ts:27`; `useLatestExecutions.ts:30`; `useNodeTypes.ts:18`; `useVariablesApi.ts:27-28`; `ApiTesterPanel.tsx:423` | Kept. New project keys live under `['project', P, …]` (§3.3). |
| F17 | App-builder AI auto-saves a version after every `code` event and picks `App.tsx` as the entry. | `hooks/useAppBuilderChat.ts:90-112` | This is where `api-usage` gets refreshed after AI turns, and where `api/client.ts` must be stripped from saved files. |
| F18 | SSE event types the editor consumes: `execution:start`, `node:start`, `node:complete`, `node:error`, `execution:complete`, `execution:result`, `execution:error`. App-builder chat: `text`, `code`, `thinking`, `tool_call`, `tool_result`, `error`, `plan`. | `useExecutionStream.ts:195-288`; `useAppBuilderChat.ts:78-155` | Unchanged; the `declare_endpoint` tool arrives as `tool_call`/`tool_result` with `tool:"declare_endpoint"`. |
| F19 | Published runtime reads `window.__APP_SLUG`/`__APP_TOKEN` and rewrites `/api/…` to `'/a/'+slug+'/api'+url`. | `engine:routes/public_apps.py:130,151-156,223` | The generated client's `(window as any).__APP_TOKEN` (§3.6) lines up with the published page. The rewrite bug is §4.8. |

---

## 1. Route map

### 1.1 Layout

```
__root (ThemeProvider, ErrorBoundary, Toaster)          app/routes/__root.tsx   (modify: full-screen match adds '/p')
├── /                     LandingPage                    app/routes/indexRoute.ts (links changed, F10)
├── /projects             ProjectsHome (projects grid)   app/routes/projects.tsx  (rewritten)
├── /editor               redirect only                  app/routes/editor.tsx    (body moves to project editor route)
├── /builder              redirect only                  app/routes/builder.tsx
└── /p/$projectId         ProjectShell (layout route)    app/routes/project.tsx   (new)
    ├── /                 Overview
    ├── /apps             AppsList
    ├── /apps/$appId      AppBuilder (existing AppBuilderShell inside the shell)
    ├── /api              ApiRoutesList
    ├── /api/new          NewRouteDialog over ApiRoutesList
    ├── /api/keys         ApiKeys
    ├── /api/spec         SpecView (OpenAPI · revisions · diff)
    ├── /api/code/$moduleId  ModuleEditor (phase 8)
    ├── /api/$routeId     RouteDetail
    ├── /automations      AutomationsList
    ├── /automations/new  WorkflowEditor (unsaved)
    ├── /automations/$workflowId  WorkflowEditor (existing editor inside the shell)
    ├── /data             TablesList
    ├── /data/$tableId    TableDetail
    ├── /secrets          Secrets
    ├── /runs             RunsList
    ├── /runs/$executionId  RunDetail
    ├── /releases         Releases (reached from the header Release button and Overview; not a tab)
    ├── /releases/$releaseId  ReleaseDetail
    └── /settings         Settings
```

Decision: "Home = Projects grid" is `/projects` (today's "projects" page already lives there and is
what the landing page links to). `/` stays the landing page.

### 1.2 Route definitions

All new files are under `app/routes/`. Each exports a `createRoute`; `index.ts` builds the tree
(`rootRoute.addChildren([indexRoute, projectsRoute, editorRedirectRoute, builderRedirectRoute, projectRoute.addChildren([...])])`).

| File | Path | Params | Search params (`validateSearch`) | Component |
|---|---|---|---|---|
| `projects.tsx` (rewrite) | `/projects` | – | `q?: string`, `sort?: 'name'\|'recent'\|'health'` | `features/project/components/ProjectsHome.tsx` |
| `editor.tsx` (rewrite) | `/editor` | – | `workflowId?: string` (unchanged shape) | redirect in `beforeLoad` (§1.3) |
| `builder.tsx` (rewrite) | `/builder` | – | `appId?: string` (unchanged shape) | redirect in `beforeLoad` |
| `project.tsx` | `/p/$projectId` | `projectId` (id **or** slug, §3 convention) | – | `ProjectShell` |
| `project.overview.tsx` | `/p/$projectId/` | – | – | `ProjectOverview` |
| `project.apps.tsx` | `/p/$projectId/apps` | – | `q?: string` | `AppsList` |
| `project.app.tsx` | `/p/$projectId/apps/$appId` | `appId` | `panel?: 'chat'\|'api'\|'history'\|'tester'\|'settings'`, `routes?: string` (comma-separated route ids pre-attached for "generate UI for this API") | `AppBuilderShell` (modified) |
| `project.api.tsx` | `/p/$projectId/api` | – | `filter?: 'all'\|'stubs'\|'unused'\|'code'` | `ApiRoutesList` |
| `project.api.new.tsx` | `/p/$projectId/api/new` | – | `method?`, `path?`, `handler?: 'create'\|'workflow'\|'stub'\|'python'`, `workflowId?` | `NewRouteDialog` |
| `project.api.keys.tsx` | `/p/$projectId/api/keys` | – | – | `ApiKeysPage` |
| `project.api.spec.tsx` | `/p/$projectId/api/spec` | – | `env?: string`, `from?: number`, `to?: number` | `SpecView` |
| `project.api.code.tsx` | `/p/$projectId/api/code/$moduleId` | `moduleId` (`new` = unsaved) | `line?: number` | `ModuleEditor` |
| `project.api.route.tsx` | `/p/$projectId/api/$routeId` | `routeId` | `tab?: 'handler'\|'schema'\|'test'\|'usedby'\|'runs'` | `RouteDetail` |
| `project.automations.tsx` | `/p/$projectId/automations` | – | `q?: string` | `AutomationsList` |
| `project.editor.tsx` | `/p/$projectId/automations/$workflowId` | `workflowId` (`new` = unsaved) | `node?: string` (open NDV on node name) | `WorkflowEditorPage` (body of today's `editor.tsx:88-195`) |
| `project.data.tsx` | `/p/$projectId/data` | – | – | `TablesList` |
| `project.table.tsx` | `/p/$projectId/data/$tableId` | `tableId` | `filter?: string` (JSON), `sort?: string` (default `-created_at`) | `TableDetail` |
| `project.secrets.tsx` | `/p/$projectId/secrets` | – | `env?: string`, `create?: string` (pre-fill key: deep link for "create `$vars.X`") | `SecretsPage` |
| `project.runs.tsx` | `/p/$projectId/runs` | – | `mode?: string`, `status?: string` | `RunsList` |
| `project.run.tsx` | `/p/$projectId/runs/$executionId` | `executionId` | – | `RunDetail` |
| `project.releases.tsx` | `/p/$projectId/releases` | – | `env?: string` | `ReleasesPage` |
| `project.release.tsx` | `/p/$projectId/releases/$releaseId` | `releaseId` | `against?: string` (default `active`) | `ReleaseDetail` |
| `project.settings.tsx` | `/p/$projectId/settings` | – | `section?: 'general'\|'environments'\|'egress'\|'developer'\|'danger'` | `ProjectSettings` |

`/api/new`, `/api/keys`, `/api/spec`, `/api/code/$moduleId` are registered **before** `/api/$routeId`
so they win (TanStack ranks static segments first anyway).

### 1.3 Redirects for old URLs

| Old URL | `beforeLoad` behaviour |
|---|---|
| `/editor` (no id) | `throw redirect({to:'/p/$projectId/automations/$workflowId', params:{projectId:'proj_default', workflowId:'new'}})` |
| `/editor?workflowId=W` | `GET /api/workflows/W` via `queryClient.fetchQuery(qk.workflow(W))` → `redirect` to `/p/{project_id}/automations/W`. 404 → `redirect({to:'/projects'})` + `toast.error('Workflow not found')`. |
| `/builder` (no id) | `redirect` to `/p/proj_default/apps` |
| `/builder?appId=A` | `GET /api/apps/A` → `redirect` to `/p/{project_id}/apps/A`. 404 → `/projects`. |
| `/p/{slug}/…` | After `GET /api/projects/{slug}`, if `params.projectId !== project.id` the shell does `navigate({replace:true, params:{projectId: project.id}})` so every cache key uses the id (§3.3). |
| `/p/P/automations/W` where `W.project_id !== P` | Editor page `navigate({replace:true})` to the workflow's real project. Same for apps. |

### 1.4 Link changes (F10)

| Location | New target |
|---|---|
| `landing.tsx:160,203` | `/projects` (unchanged) |
| `landing.tsx:166,210` | `/p/proj_default/automations/new` |
| `projects.tsx:115` (`handleNewWorkflow`) | removed — creating workflows moves into the project (§2.11) |
| `useWorkflowActions.ts:17` | `/p/${workflow.projectId}/automations/${id}` (hook gets `projectId` from `useProjectId()`) |
| `useWorkflowApi.ts:77-81,152-156` | `navigate({to:'/p/$projectId/automations/$workflowId', params:{projectId, workflowId:result.id}, replace:true})` |
| `useAppActions.ts:106,127,235` | `/p/$projectId/apps/$appId` |
| `WorkflowNavbar.tsx:128` (back link) | `/p/$projectId/automations` |
| `AppBuilderShell.tsx:255` (back link) | `/p/$projectId/apps` |

### 1.5 Shell structure (`ProjectShell`)

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ luna ▸ [Project ▾]   Overview Apps API Automations Data Secrets Runs Settings   [env: dev ▾] [Release] │  h-11, floating panel style (projects.tsx:73,176)
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ <Outlet/>   — list pages scroll inside; editor/builder get h-[calc(100vh-44px-1.5rem)]       │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Provides `ProjectContext {project, projectId, environment, setEnvironment}` via
  `features/project/context/ProjectContext.tsx`, and mirrors it into the zustand store
  `features/project/stores/activeProjectStore.ts` for non-React callers (`useExecutionStream.ts:113`
  reads stores via `getState()`).
- On editor/builder routes the header collapses to one row with the tabs as icons; the editor's own
  floating navbar (`editor.tsx:159-161`) and the builder toolbar stay as they are, below it.
- Header badges: `Secrets` tab shows a dot when `summary.missing_secrets > 0`; `API` tab shows a count
  of stub routes. Both come from `GET /api/projects/{P}/summary` (polled, §3.5).

---

## 2. Screens and components

Convention for every API table: **Res** lists only the response fields the UI reads. Errors use
`ApiError` (§3.1): `status`, `code` (from `detail.code`, router `error`, or broker `error.code`),
`message`, `body`. Unless noted, query errors render `<QueryError error retry/>` (new,
`shared/components/QueryError.tsx`), and mutation errors show `toast.error(title, {description: err.message})`
(the existing pattern, e.g. `useAppActions.ts:108-110`).

### 2.1 ProjectsHome — `/projects`

Shows a grid (and list toggle) of projects: name, slug, counts (apps · workflows · routes · tables),
last run (status dot + relative time), health pill (`ok|degraded|failing|unknown`). "New project"
button; search; sort.

| Create | Reuse / modify |
|---|---|
| `features/project/components/ProjectsHome.tsx`, `ProjectCard.tsx`, `ProjectListRow.tsx`, `NewProjectDialog.tsx`, `HealthPill.tsx` | Navbar chrome, search, sort dropdown, grid/list toggle, theme, pagination from `app/routes/projects.tsx:172-308,453-489`; `Skeleton` (`shared/components/ui/skeleton.tsx`); `Dialog`; `formatDate` (`features/projects/lib/formatDate.ts`). The workflow/app cards (`features/projects/components/*`) move to Automations/Apps lists. Import dropdown (`projects.tsx:265-288`) moves into the project (imports land in the current project). |

| Call | When | Body | Res used | Errors |
|---|---|---|---|---|
| `GET /api/projects` | mount | – | `[].id,name,slug,description,counts.{apps,workflows,routes,tables},last_run.{status,start_time},health.{status,runs_24h,failed_24h},updated_at` | page error panel (reuse `projects.tsx:356-362` style) |
| `POST /api/projects` | dialog submit | `{name, slug?, description?}` | `id` → navigate `/p/{id}` | `409 slug_taken` → inline under slug field "Slug already used"; `422` (FastAPI array) → inline per `loc`; else toast |

Loading: 6 skeleton cards (`projects.tsx:315-343`). Empty: "No projects yet" + New project button
(the Default project always exists after M1, so empty only means the API returned `[]`).
Slug field: live-derived from name client-side (lowercase, `[^a-z0-9]+`→`-`, same as
`PublishDialog.tsx:35`) but sent only if the user edited it, so the server's `slug_utils` wins otherwise.

### 2.2 ProjectShell — `/p/$projectId`

| Create | Reuse / modify |
|---|---|
| `features/project/components/ProjectShell.tsx`, `ProjectSwitcher.tsx`, `ProjectTabs.tsx`, `EnvSelector.tsx`, `ReleaseButton.tsx`; `features/project/context/ProjectContext.tsx`; `features/project/stores/activeProjectStore.ts` | `EnvProfileSelector.tsx` logic (dropdown + "Manage variables…") becomes `EnvSelector`, options from `project.environments`; the existing component stays exported for the editor navbar (`WorkflowNavbar.tsx:256`) but reads options from context when inside a project. `envProfilesStore.ts` keyed per project (§3.4). |

| Call | When | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{projectId}` | mount / param change | `id, name, slug, environments[].{name,kind}, default_environment, live_environment, settings.egress_allowlist` | `404` → full-page "Project not found" + link to `/projects` |
| `GET /api/projects` | switcher open | `[].id,name` | inline in menu |
| `GET /api/projects/{P}/summary` | mount, `refetchInterval: 30_000` | `missing_secrets`, `counts` | silent (badges hidden) |

Env selector: options = `project.environments`; each item shows a `draft`/`release` badge. Selecting a
`release` env shows a one-line warning in the menu: "Studio runs use draft automations with prod
secrets." Value persisted per project (§3.4). Default = `project.default_environment`.

Release button: opens `ReleaseDialog` (§2.17) for `environment = default_environment`; disabled with
tooltip when the project has no `kind:"release"` environment.

### 2.3 Overview — `/p/$projectId/`

Shows: counts tiles (apps, automations, routes, tables, stub routes), health (runs_24h, failed_24h),
last run (link to run), "Missing secrets" card (list with inline create), environment cards (one per
environment: kind, active release number + date for release envs, "Open API docs" for that env),
recent runs (5).

| Call | Body/Query | Res used | Errors / empty |
|---|---|---|---|
| `GET /api/projects/{P}/summary` | – | `counts, last_run, health, missing_secrets` | error panel |
| `GET /api/projects/{P}/secrets/missing` | (no `environment` → all envs; gap G2) | `[].key, missing_in[], referenced_by[]` | card hidden when `[]` |
| `GET /api/projects/{P}/releases?environment={env}` | one per `kind:"release"` env | `[0]` where `is_active` → `number, created_at, id` | "No release yet — serving drafts" (§3.9 last bullet) |
| `GET /api/projects/{P}/runs?limit=5` | – | `[].id, status, mode, environment, start_time, workflow_name` | "No runs yet" |

### 2.4 Apps list — `/p/$projectId/apps`

Grid/list of apps in the project (reuse `AppCard.tsx`/`AppListRow.tsx`), plus per-app chips: published
slug (link), API usage status (`ok`/`missing`/`changed` counts). New app, Import app, Delete, Export,
"Move to project…".

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/apps` | – | `[].id,name,created_at,updated_at,project_id` (+ `slug, active` if G5 lands) | error panel; empty: "No apps yet" + New app |
| `POST /api/projects/{P}/apps` | `{name:'Untitled App', definition:{sourceCode:null}}` (same body as `useAppActions.ts:101-104`) | `id` → `/p/P/apps/{id}` | toast |
| `DELETE /api/apps/{A}` | – | `success` | toast (existing `useAppActions.ts:130-147`) |
| `GET /api/apps/{A}` | export | full detail (existing `buildAppExportPayload`, `useAppActions.ts:61-76`) | toast |
| Import: `POST /api/projects/{P}/apps` then `PUT /api/apps/{A}` | `{description, workflow_ids, api_execution_ids, source_code, files, create_version:true, version_trigger:'import'}` (`useAppActions.ts:223-231`) | `id` | toast. `workflow_ids` from another project are kept (they then fail at run time with `403 workflow_not_in_project`; G22). |
| `PUT /api/apps/{A}` (move) | `{project_id: P2}` | `project_id` | toast |
| `GET /api/apps/{A}/api-usage` | per card, lazy (`enabled` when card visible) | `[].status` counts | chip hidden on error |

Modify: `useCreateApp`/`useImportApp`/`useAppActions` (`features/projects/hooks/useAppActions.ts`) take
`projectId` and call `projectsApi.createApp`; invalidations per §3.3.

### 2.5 App builder — `/p/$projectId/apps/$appId`

Existing `AppBuilderShell` (`features/app-builder/components/AppBuilderShell.tsx:51`) gets `projectId`
and `appId` props instead of `appId` only. New panels and behaviours:

| Create | Reuse / modify |
|---|---|
| `features/app-builder/components/ApiPanel.tsx` (routes list + copy snippet + "Generate UI for this API" + missing/changed usage), `MissingEndpointsBanner.tsx`, `AppSettingsPanel.tsx` (allowed workflows = `workflow_ids`), `features/app-builder/hooks/useProjectClient.ts`, `usePreviewToken.ts`, `useApiUsage.ts` | `IframeSandbox.tsx` (auth injection, source check, merged files — §4.7); `sandbox-runtime.ts` (headers normalisation); `AppPreviewPanel.tsx` (receives merged files so the file tree shows `api/client.ts` read-only, badge "generated"); `useAppBuilderChat.ts` (body gains `project_id`, `route_ids`; strip `api/client.ts` before auto-save; invalidate on `declare_endpoint`); `AppBuilderShell.tsx:132-154` save strips `api/client.ts`; `PublishDialog.tsx` (release-env note) |

| Call | When | Body | Res used | Errors / states |
|---|---|---|---|---|
| `GET /api/apps/{A}` | mount (existing `AppBuilderShell.tsx:109`) | – | existing fields + `project_id` | existing error view (`:180-189`); `project_id !== P` → redirect |
| `GET /api/projects/{P}/client.ts?environment={default_environment}` | mount; refetch on `qk.clientTs` invalidation | – | text | on error: preview still renders; console entry `[api] client.ts unavailable: …` and the API panel shows the error |
| `POST /api/projects/{P}/apps/{A}/preview-token` | mount; refresh 5 min before `expires_at`; once on any `401` from `/api/p/` in preview | – | `token, expires_at, environment` | failure: banner "Preview can't call project API (token): {message}" + retry |
| `GET /api/projects/{P}/routes` | API panel | – | `[].id,name,method,path,auth,handler.type,used_by_count,description,url` | panel error; empty: "No routes yet — Create route" (link `/p/P/api/new`) and "Ask AI to declare the endpoints it needs" |
| `GET /api/apps/{A}/api-usage` | mount; after save/AI auto-save success; after route mutations | – | `[].operation,method,path,file,line,route_id,status` | banner hidden on error |
| `POST /api/projects/{P}/routes/stubs` | banner "Create N stub routes" | `{app_id:A, routes:[{method, path, name: operation}]}` (only usage rows with non-null `method`+`path`, G17) | `created[], existing[]` → toast "Created 2 stubs (1 already existed)" | toast; `422 invalid_path` names the path |
| `PUT /api/apps/{A}` | save (existing `:137-142`), AI auto-save (`useAppBuilderChat.ts:96-103`), rename (`:85`), allow-list (`AppBuilderChatPanel.tsx:173`), settings `workflow_ids` | as today, files **without** `api/client.ts`; settings: `{workflow_ids:[…]}` | `current_version` | existing toasts |
| `POST /api/ai/app-builder/chat` (SSE) | send | `{message, app_id:A, project_id:P, route_ids:[…], current_version_id, api_execution_ids, conversation_history}` | events F18 | existing (`useAppBuilderChat.ts:56-63`) |
| `GET /api/projects/{P}/workflows` | settings panel (allowed workflows picker) | – | `[].id,name` | inline |
| existing versions/publish/unpublish/revert/label calls | unchanged | | | |

"Generate UI for this API": API panel → select routes → sets `route_ids` and sends the canned message
"Build a UI for these endpoints: getOrder, listOrders" through the existing chat panel.

Publish dialog (`PublishDialog.tsx`): unchanged calls. When `project.environments` has a `release` env
that already has an active release, show "Promoting a release to prod replaces the published version"
(§3.9: promotion sets `apps.published_version_id`). Gap G27 asks whether direct publish is allowed then.

### 2.6 API routes list — `/p/$projectId/api`

Table: method badge, path, name (operationId), auth badge (`none|app_token|api_key`), handler
(`workflow` → workflow name link / `stub` → "not implemented" amber badge / `python` → `module:function`),
source (`ui|code|app_stub`), used-by count, `validate_request` icon. Toolbar: filter
(all/stubs/unused/code), "New route", "API keys", "OpenAPI", base URL copy
(`${backends.workflow}/api/p/${S}`).

| Call | Res used | Errors / empty |
|---|---|---|
| `GET /api/projects/{P}/routes` | `[].id,name,method,path,auth,handler,source,used_by_count,validate_request,url` | error panel; empty: three cards "Create route with new automation" / "Stub for later" / "Write Python handler" (phase 8, disabled when `sandbox.available=false`) |
| `GET /api/projects/{P}/workflows` | id→name map for handler links | names fall back to id |
| `GET /api/sandbox/status` (phase 8) | `available, reason` | treated as unavailable |
| `DELETE /api/projects/{P}/routes/{R}` | row menu → confirm dialog listing `used-by` first (G9) | `breaking_changes` → toast "Deleted. 1 app now calls a missing route" with link |
| `GET /api/projects/{P}/routes/{R}/used-by` | confirm dialog body | `[].app_name,file,line` |

### 2.7 New route dialog — `/p/$projectId/api/new`

Fields: method select, path input with live grammar check (the regex in §3.5, mirrored client-side),
name (placeholder = client-computed default, same rule as §3.5 "camelCase of method plus path"; sent
only if edited), auth select, description, handler radio: **New automation** (`{type:"workflow",create:true}`,
default), **Existing automation** (picker from `GET …/workflows`), **Stub** (`{type:"stub"}`),
**Python** (module + function pickers from `GET …/modules`, phase 8), `response_mode`, `validate_request`
checkbox (default off; disabled until a request schema exists).

| Call | Body | Res used | Errors |
|---|---|---|---|
| `POST /api/projects/{P}/routes` | `RouteCreate` (§3.5) | `route.id` → navigate `/p/P/api/{id}?tab=test`; `route.handler.workflow_id` | `409 route_conflict` → inline under path "Conflicts with {detail.message}"; `409 name_taken` → inline under name; `422 invalid_path` → inline path; `422 handler_not_in_project` → inline handler; FastAPI 422 → per-field |

### 2.8 Route detail — `/p/$projectId/api/$routeId`

Header: method + full URL (`${backends.workflow}${route.url}`) with copy, curl copy (adds
`-H "Authorization: Bearer sk_…"` placeholder for `api_key` routes), auth/handler badges.

Tabs:
- **Handler** — workflow: thumbnail (reuse `WorkflowThumbnail.tsx`) + "Open in editor"
  (`/p/P/automations/{wid}`); stub: "Implement with new automation" (PUT `handler:{type:"workflow",create:true}`)
  / "Use existing automation"; python: module/function + "Open code" (`/p/P/api/code/{mid}?line=`).
- **Schema** — two JSON editors (reuse `shared/components/ui/code-editor.tsx` with `language="json"`)
  for `request_schema` and `response_schema`, `response_mode` select, `validate_request` toggle, field-hint
  preview. Save runs the breaking-change flow (§4.4).
- **Test** — params inputs generated from `{name}` path segments, query key/value rows, headers rows,
  JSON body editor, environment select (draft envs only), "Apply inferred schemas" checkbox. Shows
  status, duration, headers, body (reuse `shared/components/ui/json-viewer.tsx`), link to the run, and
  an "Inferred vs current" side-by-side with "Apply" button.
- **Used by** — table app · file:line · operation, rows link to `/p/P/apps/{app_id}`.
- **Runs** — `GET …/runs?mode=api` filtered client-side by route (G3 asks for `route_id`).

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/routes/{R}` | – | all `Route` fields | `404` → "Route not found" + back link |
| `PUT /api/projects/{P}/routes/{R}?dry_run=true` | partial `RouteCreate` | `breaking_changes[]` | as POST |
| `PUT /api/projects/{P}/routes/{R}` | partial `RouteCreate` | `route, spec_revision, breaking_changes` → toast "Saved · spec rev {n}" (+ "· 1 breaking change" warning variant) | as POST |
| `POST /api/projects/{P}/routes/{R}/test` | `{params, query, headers, body, environment, apply_schemas}` | `status, headers, body, duration_ms, execution_id, inferred.{request_schema,response_schema}, applied` | transport errors toast; handler failures arrive as data (G8) |
| `GET /api/projects/{P}/routes/{R}/used-by` | – | `[].app_id,app_name,version_id,file,line,operation` | tab error |
| `GET /api/projects/{P}/runs?mode=api&limit=50` | – | `[].id,status,environment,start_time` | tab error |
| `GET /api/projects/{P}/workflows` | handler picker | `[].id,name` | inline |

### 2.9 API keys — `/p/$projectId/api/keys`

Table: name, environment, prefix, created, last used, revoked. "Create key" dialog (name + environment);
on success a one-time reveal panel with copy button and "You won't see this again".

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/api-keys` | – | `[].id,name,environment,prefix,created_at,last_used_at,revoked_at` (shape G15) | error panel; empty "No keys" |
| `POST /api/projects/{P}/api-keys` | `{name, environment}` | `key` (shown once), `prefix, id` | `422` (unknown env) inline |
| `DELETE /api/projects/{P}/api-keys/{id}` | – | – | toast |

The key is held only in component state and cleared on dialog close; never cached by React Query
(the mutation's `onSuccess` writes a **redacted** row into the list cache: `setQueryData` without `key`).

### 2.10 Spec view — `/p/$projectId/api/spec`

Environment select; OpenAPI rendered as an operations list (method, path, operationId, schemas via
`json-viewer`), "Download openapi.json", "Download client.ts"; Revisions list (revision, created, change
count, breaking count); selecting two revisions shows the diff.

| Call | Query | Res used |
|---|---|---|
| `GET /api/projects/{P}/openapi.json` | `environment` | `openapi, info.version, paths, components.schemas, components.securitySchemes` |
| `GET /api/projects/{P}/client.ts` | `environment` | text (download/preview) |
| `GET /api/projects/{P}/spec/revisions` | – | `[].revision, spec_hash, created_at, changes[]` |
| `GET /api/projects/{P}/spec/diff` | `from, to` | `changes[].kind, route, operation, field, breaking, affected[]` |

Empty: "No routes yet — the spec is generated from routes." Errors: panel per section.

### 2.11 Automations list and editor — `/p/$projectId/automations[/$workflowId]`

List: reuse `WorkflowCard.tsx`/`WorkflowListRow.tsx` + latest-run status (from `GET …/runs`), badge
"handles GET /orders/{id}" when a route uses it (computed client-side from `GET …/routes` where
`handler.workflow_id === id`). New automation → `/p/P/automations/new`.

Editor: today's `EditorPage` (`app/routes/editor.tsx:88-195`) moved verbatim to
`project.editor.tsx`, with these changes:
1. Load via `useQuery(qk.workflow(W))` instead of the raw `fetch` at `editor.tsx:132-144`; same
   `fromBackendWorkflow` + `loadWorkflow`. If `data.project_id !== projectId` → redirect.
2. `workflowId === 'new'` → `resetWorkflow()` (today's no-id branch, `editor.tsx:117-123`).
3. Save (`useWorkflowApi.ts:47-97`): create calls `POST /api/projects/{P}/workflows` (same
   `BackendWorkflow` body, plus `settings:{}`); update unchanged (`PUT /api/workflows/{W}`).
4. Runs: SSE bodies and `run-adhoc` carry `environment` from the project env store and `project_id`
   for adhoc (§5).
5. `WorkflowSelectorField` lists `GET /api/projects/{P}/workflows` (F9).
6. Navbar shows "Serves: GET /orders/{id}" chips (same client-side join) linking to the route.
7. Expression editor (`ndv/ExpressionEditor.tsx`): `$vars.` completions come from
   `GET /api/projects/{P}/secrets?environment={env}` (keys only); an unknown `$vars.X` shows the inline
   create chip (§4.5).

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/workflows` | – | `[].id,name,active,node_count,updated_at,project_id` | panel; empty "No automations" |
| `GET /api/workflows/{W}` | – | as today + `project_id` | `404` → "Automation not found" |
| `POST /api/projects/{P}/workflows` | `BackendWorkflow` | `id, name` | toast (existing `useWorkflowApi.ts:88-93`) |
| `PUT /api/workflows/{W}` | `BackendWorkflow` | `name` | toast; `409 workflow_in_use` only on moves |
| `POST /execution-stream/{W}` | `{input_data, environment}` | SSE | existing |
| `POST /execution-stream/adhoc` | `{...BackendWorkflow, input_data, environment, project_id:P}` | SSE | existing |
| `POST /api/workflows/run-adhoc` | `{...BackendWorkflow, environment, project_id:P}` | `status, execution_id, data, errors` | existing |
| `POST /api/workflows/{W}/publish` / `unpublish` / `run`, `DELETE /api/workflows/{W}` | unchanged | | |
| `GET /api/projects/{P}/routes` | – | `handler.workflow_id, method, path, id` | chips hidden |
| `GET /api/projects/{P}/secrets?environment={env}` | – | `[].key, type, scope` | completions fall back to none |

### 2.12 Data — `/p/$projectId/data[/$tableId]`

Tables list (name, columns count, rows count if G16, description); "New table" dialog with a column
editor (name, type select `string|number|integer|boolean|datetime|json`, required, default).
Table detail: column header bar with "Edit columns"; filter builder (column · operator · value →
compiled to the §3.7 grammar), sort toggle per column (`created_at` and `id` always), paged grid with
"Load more" (cursor), inline cell edit (PATCH), row JSON drawer (PUT), add row form, bulk "Import JSON"
(≤1000 rows), delete row/table.

| Call | Body / Query | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/tables` | – | `[].id,name,description,columns` | panel; empty "No tables" |
| `POST /api/projects/{P}/tables` | `{name, description?, columns:[{name,type,required?,default?}]}` | `id` → navigate | `409 name_taken` inline name; `422 invalid_column` inline on the column (`detail.column` if present) |
| `GET /api/projects/{P}/tables/{T}` | – | `name, description, columns` | `404` page |
| `PUT /api/projects/{P}/tables/{T}` | `{name?, description?, columns?}` | table | `409 rows_incompatible` → dialog "{detail.count} rows violate this change"; `409 name_taken` |
| `DELETE /api/projects/{P}/tables/{T}` | – | – | toast |
| `GET …/tables/{T}/rows` | `limit=50&cursor=&filter=<encodeURIComponent(JSON)>&sort=-created_at` | `rows[].id,data,created_at,updated_at`, `next_cursor` | `422 unknown_column` → filter chip turns red |
| `POST …/tables/{T}/rows` | `{data}` or `{rows:[…]}` | `Row` / `inserted, rows` | `422 row_invalid` → per-row/column errors from `detail.errors[]` (G1 for the envelope) |
| `PATCH …/rows/{id}` | `{data:{col:v}}` (merge) | `Row` | optimistic with rollback (§3.6) |
| `PUT …/rows/{id}` | `{data}` | `Row` | inline |
| `DELETE …/rows/{id}` | – | – | toast |
| `GET …/rows/{id}` | drawer open | `Row` | drawer error |

The `PATCH`/`PUT` body shape is not spelled out in §3.7; this spec assumes `{data}` like POST (G15).

### 2.13 Secrets — `/p/$projectId/secrets`

Matrix: rows = keys, columns = project environments; each cell shows scope (project / team /
missing), `overrides_team`, `overridden`, `type` icon (secret values show `••••` and are never
fetched), `allowed_hosts` chips. Row actions: set value (per env), edit description, edit
allowed hosts, delete (project rows only). Team rows are read-only here with "Edit team variable" which
opens the existing `EnvVariablesModal` for that environment. "Missing" section on top with one-click
create per (key, env). `?create=KEY&env=dev` opens the create dialog pre-filled.

| Create | Reuse |
|---|---|
| `features/secrets/components/SecretsPage.tsx`, `SecretCell.tsx`, `CreateSecretDialog.tsx`, `CreateSecretInline.tsx` (shared, §4.5), `AllowedHostsEditor.tsx` | `EnvVariablesModal.tsx` (team rows), `KEY_RE` style validation (`EnvVariablesModal.tsx:28`, but widened to the backend grammar `^[A-Za-z_][A-Za-z0-9_]*$`) |

| Call | Body / Query | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/secrets?environment={env}` | one query per env (until G2) | `[].id,key,environment,type,value,description,scope,overrides_team,overridden,allowed_hosts,updated_at` | panel |
| `GET /api/projects/{P}/secrets/missing` | – | `[].key,missing_in,referenced_by[]` | section hidden |
| `POST /api/projects/{P}/secrets` | `{key, value, environment, type, description?, allowed_hosts?}` | `EffectiveVar` | `409 exists` → "Already set in {env} — use Replace"; `422 unknown_environment`; `422 invalid_key` inline |
| `PUT /api/projects/{P}/secrets/{id}` | only changed fields among `{value, description, allowed_hosts}` | `EffectiveVar` | `404` (team row id) cannot happen from this UI (team rows use `/api/variables`) |
| `DELETE /api/projects/{P}/secrets/{id}` | – | – | toast |
| team rows: `PUT /api/variables/{id}` / `DELETE /api/variables/{id}` | via `EnvVariablesModal` as today (`useVariablesApi.ts:79-111`) | | |

### 2.14 Runs — `/p/$projectId/runs[/$executionId]`

List: status, mode (`manual|api|webhook|cron|…`), environment, workflow or route, start, duration,
error count; filters mode/status; auto-refresh while any row is `running`.
Detail: header (status, env, mode, ids), errors, node data (reuse the execution trace components
`features/workflow-editor/components/execution-logs/ExecutionTrace.tsx` + `buildTraceTree.ts`, fed
from `node_data`/`node_metrics`), sandbox trace (stdout/stderr panes with `truncated` banner, exit
code, duration), broker calls table (function, status, error_code, duration, redacted params).

| Call | Query | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/runs` | `limit=50&mode=&status=` | `[].id,workflow_id,workflow_name,status,mode,environment,trigger,start_time,end_time,error_count` | panel; empty "No runs yet" |
| `GET /api/projects/{P}/runs/{X}` | – | `status,mode,environment,errors[],node_data,node_metrics,trace.sandbox.{duration_ms,exit_code,stdout,stderr,truncated},trace.broker_calls[]` | `404` page |
| `GET /api/projects/{P}/broker-calls?execution_id={X}` | when `trace.broker_calls` are ids not rows | `[].id,node_type,status,error_code,error,duration_ms,params_redacted,created_at` | section error |

### 2.15 Python modules — `/p/$projectId/api/code/$moduleId` (phase 8)

CodeMirror Python editor (F15), save button, gutter markers from `errors[].line`, right panel with the
routes the module declares (from the save response and `GET …/routes` filtered by
`handler.module_id`), "Test" link per route, sandbox status banner.

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/sandbox/status` | – | `available, backend, version, reason` | banner "Python handlers unavailable: {reason}" |
| `GET /api/projects/{P}/modules` | – | `[].id,name,updated_at` (shape G15) | list error |
| `GET /api/projects/{P}/modules/{M}` | – | `name, source, source_sha` | `404` page |
| `POST /api/projects/{P}/modules` | `{name, source}` | `module.id, routes.{created,updated,removed}, errors[]` → toast "3 routes synced" | `422 syntax_error` → gutter markers from `detail.errors[]`, nothing saved; `409 route_conflict` → banner naming the clashing route |
| `PUT /api/projects/{P}/modules/{M}` | `{source}` | same | same |
| `DELETE /api/projects/{P}/modules/{M}` | – | – | confirm lists the module's routes first |

### 2.16 Settings — `/p/$projectId/settings`

Sections: **General** (name, slug with warning "changes every /api/p URL and generated client; N apps
call this API" from `routes[].used_by_count` sum — G26, description); **Environments** (list
name/kind, add, remove, default and live selects); **Egress** (allowlist chips, `*.` wildcard hint);
**Developer** (functions catalogue, SDK download, "Get dev broker token" for draft envs with copy,
`STUDIO_BROKER_URL`/`STUDIO_TOKEN` snippet); **Danger** (delete project).

| Call | Body | Res used | Errors |
|---|---|---|---|
| `PUT /api/projects/{P}` | any subset of `{name, description, slug, environments, default_environment, live_environment, settings}` | `Project` → `setQueryData(qk.project(P))` | `409 slug_taken`; `409 environment_in_use` → "Remove secrets and API keys in {env} first" (with link `/secrets?env=`); `422` default/live not in environments |
| `DELETE /api/projects/{P}` | – | `success` → navigate `/projects` | `409 project_not_empty` → "Delete or move its automations, apps, routes and tables first"; `409 default_project` → button hidden for `proj_default` |
| `GET /api/sdk/functions` | – | `[].function,node_type,description,parameters,returns,egress` | section error |
| `GET /api/sdk/python` | – | `version`, file names (preview) | – |
| `GET /api/sdk/python.zip` | – | blob → download `studio-sdk-{version8}.zip` | toast |
| `POST /api/projects/{P}/broker-tokens` | `{environment}` | `token, expires_at` (state only, never cached) | `403` → "Dev tokens are only for draft environments" |

### 2.17 Releases — `/p/$projectId/releases[/$releaseId]` + `ReleaseDialog` + `PromoteDialog`

Releases page: per environment, a timeline of releases (number, status pill
`active|superseded|rolled_back|blocked`, notes, created), actions: "Promote to {env}" (from a draft
env's release), "Roll back to this" (release env, non-active), "View".
Release detail: snapshot summary (apps+version, workflows+version, routes, modules, secret bindings
keys only), checks list, diff vs active.

| Create | Reuse |
|---|---|
| `features/releases/components/ReleasesPage.tsx`, `ReleaseDialog.tsx`, `PromoteDialog.tsx`, `RollbackDialog.tsx`, `ChecksList.tsx`, `ReleaseDiff.tsx` | `ChecksList` also used by the 422 path; `CreateSecretInline` for `secrets_present` fixes; `BreakingChangesList` (§4.4) for `breaking_vs_active.details.changes` and diff `routes` |

| Call | Body | Res used | Errors |
|---|---|---|---|
| `GET /api/projects/{P}/releases?environment={env}` | – | `[].id,number,environment,status,is_active,notes,created_at,checks` | panel; empty "No releases" |
| `GET /api/projects/{P}/releases/{REL}` | – | + `snapshot.{apps,workflows,routes,modules,secret_bindings,spec_hash}` | `404` |
| `POST /api/projects/{P}/releases` | `{environment, notes?}` | `Release` → dialog step 2 shows checks (warns in amber) + "Promote to {live_environment}" | `422 checks_failed` → `ChecksList(detail.checks)` with fixes (§4.6); nothing activated |
| `POST /api/projects/{P}/releases/{REL}/promote` | `{to_environment}` | `Release` → toast "prod is now release #{n}" | `422 checks_failed` as above |
| `POST /api/projects/{P}/environments/{env}/rollback` | `{release_id?}` | `Release` + its `checks` (warnings) → toast with warning count | toast |
| `GET /api/projects/{P}/releases/{REL}/diff?against=active` | – | `routes[] (Change), apps[].{app_id,from_version,to_version}, workflows[], secrets[].{key,change}` | section error |

---

## 3. Data layer

### 3.1 Files and the HTTP core

```
shared/api/http.ts          request<T>(), requestText(), requestBlob(), ApiError, parseError()
shared/api/types.ts         every type in §3.2
shared/api/queryKeys.ts     qk factory (§3.3)
shared/api/projects.ts      projectsApi        (§3.1 of the contract)
shared/api/secrets.ts       secretsApi         (§3.2)
shared/api/sdk.ts           sdkApi, brokerApi  (§3.4)
shared/api/routes.ts        routesApi, specApi, apiKeysApi (§3.5)
shared/api/appApi.ts        appApiApi          (§3.6: client.ts, preview-token, api-usage, used-by, stubs)
shared/api/tables.ts        tablesApi          (§3.7)
shared/api/modules.ts       modulesApi, sandboxApi (§3.8)
shared/api/releases.ts      releasesApi        (§3.9)
shared/api/runs.ts          runsApi            (§3.1 runs + broker-calls)
shared/lib/api.ts           existing; apiFetch delegates to request() (same signature), workflow/app
                            types gain project_id
```
Hooks live with their feature: `features/project/hooks/useProject.ts`, `useProjects.ts`,
`useProjectSummary.ts`; `features/secrets/hooks/useSecrets.ts`; `features/api/hooks/useRoutes.ts`,
`useRouteMutations.ts`, `useSpec.ts`, `useApiKeys.ts`; `features/app-builder/hooks/useProjectClient.ts`,
`usePreviewToken.ts`, `useApiUsage.ts`; `features/data/hooks/*`; `features/runs/hooks/*`;
`features/releases/hooks/*`.

```ts
// shared/api/http.ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string | null,          // detail.code | error (router) | error.code (broker)
    message: string,                     // detail.message | detail (string) | FastAPI msg[0] | `HTTP ${status}`
    public body: unknown,                // parsed JSON or text
    public fieldErrors: Array<{ loc: (string | number)[]; msg: string }> = [],  // FastAPI 422 array
  ) { super(message) }
  get details(): Record<string, unknown> { /* detail object minus code/message, or {} */ }
}
export function parseError(status: number, body: unknown): ApiError   // handles the 5 shapes in G1
export async function request<T>(path: string, init?: RequestInit & { query?: Record<string, string | number | boolean | undefined> }): Promise<T>
  // url = `${backends.workflow}${path.startsWith('/api') || path.startsWith('/a/') ? '' : '/api'}${path}`
  // headers: {'Content-Type':'application/json', ...init.headers}   (matches apiFetch, api.ts:71-74)
  // 204/empty body → undefined
export async function requestText(path: string, init?): Promise<string>
export async function requestBlob(path: string, init?): Promise<Blob>
```
`apiFetch` (`shared/lib/api.ts:63-83`) becomes `return request<T>(endpoint, options)`; because `ApiError`
extends `Error` the 30+ `error instanceof Error ? error.message` call sites are unaffected.

### 3.2 Types (mirror §3; additive fields on existing types)

```ts
// shared/api/types.ts
export type EnvKind = 'draft' | 'release'
export interface ProjectEnvironment { name: string; kind: EnvKind }
export interface Project {
  id: string; name: string; slug: string; description: string | null
  environments: ProjectEnvironment[]; default_environment: string; live_environment: string
  settings: { egress_allowlist?: string[]; [k: string]: unknown }
  created_at: string; updated_at: string
}
export type HealthStatus = 'ok' | 'degraded' | 'failing' | 'unknown'
export interface ProjectCounts { apps: number; workflows: number; routes: number; tables: number }
export interface LastRun { execution_id: string; status: string; mode: string; start_time: string }
export interface ProjectHealth { status: HealthStatus; runs_24h: number; failed_24h: number }
export interface ProjectListItem extends Project { counts: ProjectCounts; last_run: LastRun | null; health: ProjectHealth }
export interface ProjectSummary { counts: ProjectCounts; last_run: LastRun | null; health: ProjectHealth; missing_secrets: number /* G4 */ }
export interface ProjectCreate { name: string; slug?: string; description?: string }
export type ProjectUpdate = Partial<Pick<Project, 'name' | 'description' | 'slug' | 'environments' | 'default_environment' | 'live_environment' | 'settings'>>

// runs (ExecutionListItem + environment/mode/trigger; ExecutionDetailResponse + trace)
export interface RunListItem {
  id: string; workflow_id: string; workflow_name: string; status: string; mode: string
  start_time: string; end_time: string | null; error_count: number
  environment: string | null; trigger: string | null
}
export interface RunTrace {
  sandbox?: { duration_ms: number; exit_code: number; stdout: string; stderr: string; truncated: boolean }
  broker_calls?: Array<string | BrokerCall>
}
export interface RunDetail {
  id: string; workflow_id: string; workflow_name: string; status: string; mode: string
  start_time: string; end_time: string | null
  errors: Array<{ node_name: string; error: string; timestamp?: string }>
  node_data: Record<string, unknown>; node_metrics: Record<string, unknown>
  environment?: string | null; trace: RunTrace | null
}

// secrets
export type VarType = 'secret' | 'string' | 'number'
export interface EffectiveVar {
  id: number; key: string; environment: string; type: VarType; value: string | null
  description: string | null; scope: 'project' | 'team'; overrides_team: boolean; overridden: boolean
  allowed_hosts: string[] | null; updated_at: string
}
export interface SecretCreate { key: string; value: string; environment: string; type: VarType; description?: string; allowed_hosts?: string[] }
export interface SecretUpdate { value?: string; description?: string; allowed_hosts?: string[] }
export type SecretRef = { kind: 'workflow'; id: string; name: string; node: string } | { kind: 'module'; id: string; name: string; line: number }
export interface MissingSecret { key: string; missing_in: string[]; referenced_by: SecretRef[] }

// sdk + broker
export interface SdkFunction { function: string; node_type: string; description: string; parameters: JsonSchema; returns: JsonSchema; egress: boolean }
export interface SdkPython { version: string; files: Record<string, string> }
export interface BrokerToken { token: string; expires_at: string }
export interface BrokerCall { id: string; execution_id: string | null; project_id: string; environment: string; origin: string; node_type: string; params_redacted: unknown; status: 'ok' | 'denied' | 'error'; error_code: string | null; error: string | null; duration_ms: number; created_at: string } // from M2 columns; G15
export type BrokerResult = { ok: true; call_id: string; items: unknown[]; metadata: Record<string, unknown> } | { ok: false; call_id: string; error: { code: string; message: string; [k: string]: unknown } }

// routes
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type RouteAuth = 'none' | 'app_token' | 'api_key'
export type ResponseMode = 'auto' | 'first_item' | 'all_items'
export type RouteHandler =
  | { type: 'workflow'; workflow_id: string }
  | { type: 'workflow'; create: true }
  | { type: 'stub' }
  | { type: 'python'; module_id: string; function: string }
export interface RouteCreate {
  method: HttpMethod; path: string; name?: string; auth?: RouteAuth; description?: string | null
  request_schema?: JsonSchema | null; response_schema?: JsonSchema | null
  response_mode?: ResponseMode; validate_request?: boolean; handler: RouteHandler
}
export interface Route extends Required<Omit<RouteCreate, 'handler'>> {
  id: string; project_id: string; handler: Exclude<RouteHandler, { create: true }>
  source: 'ui' | 'code' | 'app_stub'; used_by_count: number; url: string
  created_at: string; updated_at: string
}
export interface ChangeAffected { app_id: string; app_name: string; file: string; line: number }
export interface Change { kind: string; route: string; operation: string; field?: string; breaking: boolean; affected: ChangeAffected[] }
export interface RouteWriteResult { route: Route; spec_revision: number; breaking_changes: Change[] }
export interface RouteDeleteResult { spec_revision: number; breaking_changes: Change[] }
export interface RouteTestRequest { params: Record<string, string>; query: Record<string, string>; headers: Record<string, string>; body: unknown; environment: string; apply_schemas: boolean }
export interface RouteTestResult {
  status: number; headers: Record<string, string>; body: unknown; duration_ms: number; execution_id: string | null
  inferred: { request_schema: JsonSchema; response_schema: JsonSchema }; applied: boolean
}
export interface SpecRevision { revision: number; spec_hash: string; created_at: string; changes: Change[] }
export interface ApiKey { id: string; name: string; environment: string; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
export interface ApiKeyCreated extends Pick<ApiKey, 'id' | 'name' | 'environment' | 'prefix' | 'created_at'> { key: string }

// app ↔ api
export interface PreviewToken { token: string; expires_at: string; environment: string }
export interface ApiUsage { operation: string; method: string | null; path: string | null; file: string; line: number; route_id: string | null; status: 'ok' | 'missing' | 'changed' }
export interface UsedBy { app_id: string; app_name: string; version_id: number; file: string; line: number; operation: string }
export interface StubSpec { method: HttpMethod; path: string; name?: string; description?: string; request_schema?: JsonSchema; response_schema?: JsonSchema }
export interface StubsResult { created: Route[]; existing: Route[] }

// data
export type ColumnType = 'string' | 'number' | 'integer' | 'boolean' | 'datetime' | 'json'
export interface Column { name: string; type: ColumnType; required?: boolean; default?: unknown }
export interface Table { id: string; project_id: string; name: string; description: string | null; columns: Column[]; created_at: string; updated_at: string } // G15
export interface Row { id: string; data: Record<string, unknown>; created_at: string; updated_at: string }
export interface RowPage { rows: Row[]; next_cursor: string | null }
export type RowFilter = Record<string, unknown>   // §3.7 grammar

// modules
export interface CodeModule { id: string; project_id: string; name: string; source: string; source_sha: string; created_at: string; updated_at: string } // G15
export interface ModuleWriteResult { module: CodeModule; routes: { created: Route[]; updated: Route[]; removed: Route[] }; errors: Array<{ line: number; message: string }> }
export type SandboxStatus = { backend: string; available: true; version: string } | { backend: 'disabled'; available: false; reason: string }

// releases
export type ReleaseStatus = 'active' | 'superseded' | 'rolled_back' | 'blocked'
export interface ReleaseCheck { id: string; status: 'pass' | 'warn' | 'fail'; message?: string; details?: Record<string, unknown> }
export interface ReleaseSnapshot {
  apps: Array<{ app_id: string; version_id: number }>
  workflows: Array<{ workflow_id: string; version_id: number }>
  modules: Array<{ module_id: string; name: string; source_sha: string; source: string }>
  routes: Array<Omit<Route, 'project_id' | 'source' | 'used_by_count' | 'url' | 'created_at' | 'updated_at'> & { handler: RouteHandler & { version_id?: number } }>
  spec: Record<string, unknown>; spec_hash: string
  secret_bindings: Array<{ key: string; scope: 'project' | 'team'; variable_id: number; fingerprint: string }>
}
export interface Release { id: string; project_id: string; environment: string; number: number; is_active: boolean; status: ReleaseStatus; checks: ReleaseCheck[]; source_release_id: string | null; notes: string | null; created_by: string | null; created_at: string; snapshot?: ReleaseSnapshot }
export interface ReleaseDiff { routes: Change[]; apps: Array<{ app_id: string; from_version: number | null; to_version: number | null }>; workflows: Array<{ workflow_id: string; from_version: number | null; to_version: number | null }>; secrets: Array<{ key: string; change: 'added' | 'removed' | 'value_changed' }> }

export type JsonSchema = Record<string, unknown>
```
Existing types gain `project_id: string`: `ApiWorkflowSummary`, `ApiCreateResponse`,
`ApiWorkflowDetail` (`shared/lib/backendTypes.ts:45-128`), `ApiAppListItem`, `ApiAppDetail`
(`shared/lib/api.ts:149-200`); `BackendWorkflow` gains optional `settings`, `environment`,
`project_id` (request-only); `ApiExecutionListItem` gains optional `environment`, `trigger`.

API functions (all return `Promise<…>` of the type named):
```ts
projectsApi = {
  list(): ProjectListItem[];                          get(p): Project;           create(b: ProjectCreate): Project
  update(p, b: ProjectUpdate): Project;               remove(p): {success: true}; summary(p): ProjectSummary
  listWorkflows(p): ApiWorkflowSummary[];             createWorkflow(p, b: BackendWorkflow): ApiCreateResponse
  listApps(p): ApiAppListItem[];                      createApp(p, b: {name; definition; description?; workflow_ids?; api_execution_ids?}): ApiAppDetail
}
runsApi = { list(p, q?: {limit?: number; mode?: string; status?: string}): RunListItem[]; get(p, x): RunDetail; brokerCalls(p, executionId?): BrokerCall[] }
secretsApi = { list(p, environment?): EffectiveVar[]; create(p, b: SecretCreate): EffectiveVar; update(p, id, b: SecretUpdate): EffectiveVar; remove(p, id): {success: true}; missing(p, environment?): MissingSecret[] }
sdkApi = { functions(): SdkFunction[]; python(): SdkPython; pythonZip(): Blob }
brokerApi = { mintToken(p, environment): BrokerToken; call(token, b: {function?: string; node?: string; parameters: object; items?: object[]}): BrokerResult }   // call() never throws on 4xx: returns the body
routesApi = { list(p): Route[]; get(p, r): Route; create(p, b: RouteCreate): RouteWriteResult; update(p, r, b: Partial<RouteCreate>, opts?: {dryRun?: boolean}): RouteWriteResult; remove(p, r): RouteDeleteResult; test(p, r, b: RouteTestRequest): RouteTestResult; usedBy(p, r): UsedBy[]; createStubs(p, b: {app_id?: string; routes: StubSpec[]}): StubsResult }
specApi = { openapi(p, environment?): Record<string, unknown>; clientTs(p, environment?): string; revisions(p): SpecRevision[]; diff(p, from: number, to: number): {changes: Change[]} }
apiKeysApi = { list(p): ApiKey[]; create(p, b: {name: string; environment: string}): ApiKeyCreated; revoke(p, id): unknown }
appApiApi = { previewToken(p, appId): PreviewToken; apiUsage(appId): ApiUsage[] }
tablesApi = { list(p): Table[]; get(p, t): Table; create(p, b): Table; update(p, t, b): Table; remove(p, t): unknown
  rows(p, t, q: {limit?: number; cursor?: string; filter?: RowFilter; sort?: string}): RowPage   // filter → encodeURIComponent(JSON.stringify(filter))
  insert(p, t, data): Row; insertMany(p, t, rows): {inserted: number; rows: Row[]}; getRow(p, t, id): Row
  patchRow(p, t, id, data): Row; putRow(p, t, id, data): Row; deleteRow(p, t, id): unknown }
modulesApi = { list(p): CodeModule[]; get(p, m): CodeModule; create(p, b: {name: string; source: string}): ModuleWriteResult; update(p, m, b: {source: string}): ModuleWriteResult; remove(p, m): unknown }
sandboxApi = { status(): SandboxStatus }
releasesApi = { list(p, environment?): Release[]; get(p, rel): Release; create(p, b: {environment: string; notes?: string}): Release; promote(p, rel, b: {to_environment: string}): Release; rollback(p, env, b: {release_id?: string}): Release; diff(p, rel, against = 'active'): ReleaseDiff }
```

### 3.3 Query keys

`p` in keys is always the **project id** (the shell canonicalises slugs, §1.3), so `['project', p]`
is a prefix that invalidates everything about a project.

```ts
export const qk = {
  projects:        ['projects'] as const,
  project:   (p) => ['project', p] as const,                       // prefix
  projectDetail:(p)=> ['project', p, 'detail'] as const,
  summary:   (p) => ['project', p, 'summary'] as const,
  workflows: (p) => ['project', p, 'workflows'] as const,
  apps:      (p) => ['project', p, 'apps'] as const,
  runs:   (p, f) => ['project', p, 'runs', f] as const,              // f = {limit, mode, status}
  run:    (p, x) => ['project', p, 'run', x] as const,
  brokerCalls:(p, x) => ['project', p, 'broker-calls', x ?? 'all'] as const,
  secrets:(p, env)=> ['project', p, 'secrets', env ?? 'all'] as const,
  missing:(p, env)=> ['project', p, 'secrets-missing', env ?? 'all'] as const,
  routes:    (p) => ['project', p, 'routes'] as const,
  route:  (p, r) => ['project', p, 'route', r] as const,
  usedBy: (p, r) => ['project', p, 'route', r, 'used-by'] as const,
  spec:      (p) => ['project', p, 'spec'] as const,                 // prefix for the 4 below
  openapi:(p, env)=> ['project', p, 'spec', 'openapi', env] as const,
  clientTs:(p, env)=>['project', p, 'spec', 'client-ts', env] as const,
  revisions: (p) => ['project', p, 'spec', 'revisions'] as const,
  specDiff:(p, a, b)=>['project', p, 'spec', 'diff', a, b] as const,
  apiKeys:   (p) => ['project', p, 'api-keys'] as const,
  tables:    (p) => ['project', p, 'tables'] as const,
  table:  (p, t) => ['project', p, 'table', t] as const,
  rows:(p, t, q) => ['project', p, 'table', t, 'rows', q] as const,  // useInfiniteQuery, pageParam = cursor
  row:(p, t, id) => ['project', p, 'table', t, 'row', id] as const,
  modules:   (p) => ['project', p, 'modules'] as const,
  module: (p, m) => ['project', p, 'module', m] as const,
  releases:(p, env)=>['project', p, 'releases', env ?? 'all'] as const,
  release:(p, rel)=> ['project', p, 'release', rel] as const,
  releaseDiff:(p, rel, against) => ['project', p, 'release', rel, 'diff', against] as const,
  workflow:  (w) => ['workflow', w] as const,
  app:       (a) => ['app', a] as const,
  apiUsage:  (a) => ['app', a, 'api-usage'] as const,
  previewToken:(a)=> ['app', a, 'preview-token'] as const,
  sdkFunctions:    ['sdk', 'functions'] as const,
  sdkPython:       ['sdk', 'python'] as const,
  sandbox:         ['sandbox', 'status'] as const,
  // existing, kept: ['workflows'], ['apps'], ['latest-executions'], ['nodes'], ['variables', env],
  // ['variable-environments'], ['api-tester','executions']
}
```

### 3.4 Client state (zustand)

- `features/project/stores/activeProjectStore.ts`: `{ projectId: string | null; setProjectId(id) }`.
- `features/workflow-editor/stores/envProfilesStore.ts` (modify): keep the
  `activeEnvironment`/`setActiveEnvironment` API that `useExecutionStream.ts:113` reads, but persist
  under `workflow-studio:active-environment:<projectId>`; add `hydrateForProject(projectId,
  defaultEnv, validEnvs)` called by the shell: stored value if it is in `validEnvs`, else `defaultEnv`.
  For `proj_default`, fall back to the legacy key `workflow-studio:active-environment`
  (`envProfilesStore.ts:12`) so existing users keep their selection.
- No secret, key or token is ever stored in zustand or localStorage. Preview tokens live in the Query
  cache only (memory).

### 3.5 Mutations → invalidations

| Mutation | Invalidate (prefix match) | Also |
|---|---|---|
| project create | `projects` | navigate |
| project update | `projects`, `projectDetail(p)`, `spec(p)` (slug change alters client BASE) | `setQueryData(projectDetail)` |
| project delete | `projects`; `removeQueries(project(p))` | navigate `/projects` |
| workflow create/update/delete/publish/unpublish (project-aware hooks) | existing `['workflows']`, `workflows(p)`, `summary(p)`, `missing(p,*)` | update also `routes(p)` when a route uses it (handler name display) and `workflow(w)` |
| workflow move (`project_id`) | `workflows(old)`, `workflows(new)`, `summary` both | |
| app create/delete/import/move | existing `['apps']`, `apps(p)`, `summary(p)` | |
| app save / AI auto-save / revert | `apiUsage(a)`, `routes(p)` (`used_by_count`), `usedBy(p,*)` | `app(a)` |
| app publish/unpublish | `app(a)`, `apps(p)` | |
| secret create/update/delete | `secrets(p,*)`, `missing(p,*)`, `summary(p)` | team-row edits via `/api/variables` also invalidate `secrets(p,*)` |
| route create/update(non-dry)/delete, stubs create, test with `apply_schemas` | `routes(p)`, `route(p,r)`, `spec(p)`, `summary(p)`, `apiUsage(*)` (all apps of p: `predicate: q => q.queryKey[0]==='app' && q.queryKey[2]==='api-usage'`) | route create with `handler.create` also `workflows(p)` |
| route test (no apply) | `runs(p,*)` | |
| api key create/revoke | `apiKeys(p)` | create: `setQueryData` with redacted row |
| table create/update/delete | `tables(p)`, `table(p,t)`, `summary(p)` | |
| row insert/put/delete | `rows(p,t,*)` | |
| row patch | optimistic (§3.6) then `rows(p,t,*)` on settle | |
| module create/update/delete | `modules(p)`, `module(p,m)`, `routes(p)`, `spec(p)`, `missing(p,*)` | |
| release create/promote/rollback (incl. 422) | `releases(p,*)`, `release(p,*)`, `summary(p)`, `spec(p)` (release envs serve the snapshot spec) | promote/rollback into live env: `app(*)` + `apps(p)` (published version changes) |
| AI `tool_result` with `tool==='declare_endpoint'` | same as stubs create | |

### 3.6 Optimistic updates, polling, streaming

- Optimistic: **only** table-row PATCH (cell edit): `onMutate` patches the row in every cached
  page of `rows(p,t,*)`, `onError` restores the snapshot and toasts. Everything that changes the spec
  (routes, modules, schemas) waits for the server, because the response carries `spec_revision` and
  `breaking_changes` the UI must show.
- Polling: `summary(p)` every 30 s while the shell is mounted; `runs(p,f)` every 5 s while the Runs page
  is visible **and** any listed run has `status === 'running'`; `previewToken(a)` with
  `staleTime = expires_at - now - 5 min` and `refetchInterval` of the same; `apiUsage(a)` refetch on window
  focus inside the builder (other tabs may change routes). Nothing else polls.
- Streaming: unchanged SSE for `/execution-stream/*`, `/api/ai/chat`, `/api/ai/app-builder/chat`. The
  contract adds no new SSE; the router has none either.
- `QueryProvider` defaults stay (`staleTime 30s`, `retry 1`, `QueryProvider.tsx:19-27`) but `retry`
  becomes `(n, e) => !(e instanceof ApiError && e.status < 500) && n < 1` so 404/409/422 are not retried.

---

## 4. Cross-builder "automatic" behaviours

### 4.1 How the app builder gets the project API

1. `useProjectClient(P, project.default_environment)` → `GET /api/projects/{P}/client.ts?environment=dev`
   (string). Preview always uses `default_environment` because preview tokens are draft-only (§5.4).
2. `AppBuilderShell` computes `previewFiles = [...files.filter(f => f.path !== 'api/client.ts'), {path:'api/client.ts', content: clientTs}]`
   and passes `previewFiles` to `AppPreviewPanel`/`IframeSandbox`. The document store
   (`features/app-builder/stores.ts:66-90`) never contains the generated file.
3. The bundler resolves `./api/client` from root `App.tsx` and `../api/client` from `src/App.tsx`
   through its virtual FS (`esbuild-bundler.ts:68-113`); no bundler change needed. G18 asks the backend to
   pin this layout for `_files_for_publish` and for the AI prompt.
4. `IframeSandbox`'s re-bundle hash (`hashFiles`, `IframeSandbox.tsx:21-23`) runs over `previewFiles`,
   so any spec change (new `client.ts` text after `spec(p)` invalidation) re-bundles automatically.
5. Strip on save: `AppBuilderShell.handleSave` (`:136-142`) and the AI auto-save
   (`useAppBuilderChat.ts:96-103`) send `files.filter(f => f.path !== 'api/client.ts')`. If the AI emits
   an `api/client.ts` in a `code` event, the studio drops it from `setFiles` and logs
   `[api] ignored generated file from AI` to the console panel.
6. The AI sees the spec server-side; the studio adds `project_id` and `route_ids` to the chat body
   (§3.6 changed endpoint). The API panel's "Copy usage" button copies
   `const order = await api.getOrder({ id })` for the operation.

### 4.2 Endpoint stubs from the UI side

Three entry points, all ending in `POST /api/projects/{P}/routes/stubs` or a stub route:
1. **AI declares** — the agent's `declare_endpoint` tool runs server-side. The studio renders the
   `tool_call`/`tool_result` entries as today (`useAppBuilderChat.ts:119-150`); on `tool_result` with
   `tool === 'declare_endpoint'` it invalidates per §3.5 and shows an inline chip "Declared POST /refunds
   (stub)" linking to the route.
2. **Missing-endpoints banner** — `GET /api/apps/{A}/api-usage` rows with `status:"missing"` and non-null
   `method`/`path` → banner "This app calls 2 endpoints that don't exist: POST /refunds, GET /customers"
   → "Create stubs" → `POST …/routes/stubs {app_id, routes:[{method, path, name: operation}]}`. Rows with
   `method/path === null` (a call `api.foo(` with no matching operation) are listed as "Unknown
   operation `foo` — ask AI to declare it" (G17).
3. **API page** — New route → handler "Stub".
Stubs render everywhere with an amber "not implemented" badge (`handler.type === 'stub'`); calling one
returns `501` which the preview console shows as `[app] HTTP 501 not_implemented getRefunds`. The route
detail's Handler tab offers "Implement with new automation" → `PUT …/routes/{R} {handler:{type:"workflow",create:true}}`.

### 4.3 "Used by"

- Route list column `used_by_count`; route detail "Used by" tab (`GET …/routes/{R}/used-by`).
- App side: `GET /api/apps/{A}/api-usage` drives the API panel's per-call status and the app card chips.
- Both are scans of the **latest saved version** (§3.6 "results are cached at version creation"), so
  the builder refetches `apiUsage(A)` after each save/auto-save success (`useAppBuilderChat.ts:104-108`
  `then` branch, `AppBuilderShell.tsx:143-146`).

### 4.4 Breaking-change warnings

Save flow for any route edit (Schema tab, Settings, auth change, path/method change):
1. `PUT …/routes/{R}?dry_run=true` with the partial body.
2. If `breaking_changes.some(c => c.breaking)`: `BreakingChangesDialog` lists each change
   (`kind`, `route`, `field`) with affected `app_name · file:line` links → "Save anyway" / "Cancel".
   Non-breaking changes are shown as a small "2 additive changes" note and do not prompt.
3. `PUT …/routes/{R}` (no dry run) → toast; if breaking, the toast variant is warning with
   "1 app affected".
Delete: the dialog lists `used-by` rows before calling DELETE (G9 asks for `DELETE ?dry_run=true`).
Module save: the response gives `routes.removed[]`; when non-empty and any removed route has
`used_by_count > 0`, the editor shows the same dialog **after** the fact (no dry run exists; G32).
App side: api-usage `status:"changed"` rows show an amber chip "GET /orders/{id} changed since this
version — check `total`" and the release check `app_calls_resolve` reports them (§4.6).

### 4.5 Inline "create missing secret"

`CreateSecretInline` (`features/secrets/components/CreateSecretInline.tsx`): props
`{projectId, keyName, environment, defaultType?: VarType, suggestHosts?: string[]}`; renders a chip
"`$vars.KEY` is missing in dev — Create" that expands into value input + type select + (for `secret`)
allowed-hosts input pre-filled from `suggestHosts`; submit = `POST /api/projects/{P}/secrets`.
Mounted in:
1. Overview "Missing secrets" card and Secrets page "Missing" section — from `GET …/secrets/missing`,
   one chip per `(key, env in missing_in)`.
2. Expression editor (`ndv/ExpressionEditor.tsx`) — when the expression contains `$vars.KEY` (regex
   `\$vars\.(\w+)`, the same loose pattern the backend uses, §3.2) and `KEY` is not in
   `secrets(P, env)`; `suggestHosts` = host of the HttpRequest node's URL parameter when it is a literal.
3. Release `secrets_present` failure — one chip per `details.missing[]` key in the **target** env.
4. Broker `missing_secret` results in the Developer "Try function" panel (`error.key`, `error.environment`).
5. Run detail / NDV error output — when a node error message matches `missing_secret` (string match until
   G24 gives structured codes).

### 4.6 Release checks UX

`ReleaseDialog` (header Release button):
1. Step 1: environment (default `default_environment`, any env selectable), notes, and a preview of
   what will be snapshotted (counts from `summary`). G10 asks for a dry run so checks can be shown first.
2. Submit `POST …/releases`. `201` → step 2: `ChecksList(release.checks)`; warnings
   (`stub_routes`, `breaking_vs_active`) in amber; button "Promote to {live_environment}" →
   `PromoteDialog`.
3. `422 checks_failed` → `ChecksList(err.details.checks)` with a fix action per failing check:

| Check id | Fix UI |
|---|---|
| `secrets_present` (`details.missing[]`) | `CreateSecretInline` per key for the target env, then "Retry" re-POSTs |
| `app_calls_resolve` | list of app · operation; "Open app" link + "Create stubs" (same as §4.2.2) |
| `handlers_exist` | link to each route's Handler tab |
| `modules_parse` | link to module editor at `line` |
| `stub_routes` (warn) | link to `/api?filter=stubs` |
| `breaking_vs_active` (warn) | `BreakingChangesList(details.changes)` |
| unknown id | generic row with `message` and JSON `details` |

`PromoteDialog`: `POST …/releases/{REL}/promote {to_environment}` with the same 201/422 handling.
`RollbackDialog`: shows `GET …/releases/{target}/diff?against=active` ("what changes if you roll back"),
then `POST …/environments/{env}/rollback {release_id}`; warnings from the returned `checks` are shown
in the success toast ("Rolled back · 1 warning") — rollback is never blocked (§3.9).

### 4.7 Sandbox / bridge changes for `/api/p/…`

`features/app-builder/sandbox/IframeSandbox.tsx`:
1. Props become `{ files: AppFile[] | null; projectId?: string; appId?: string; getPreviewToken?: () => Promise<string | null> }`.
2. `handleMessage` (`:63-97`): `if (event.source !== iframeRef.current?.contentWindow) return` before
   `isIframeMessage` (F12) — required now that the parent attaches credentials.
3. `handleApiRequest` (`:102-140`): compute `fullUrl` as today; `const u = new URL(fullUrl)`; if
   `u.origin === new URL(backends.workflow || location.origin).origin && u.pathname.startsWith('/api/p/')`
   and the request has no `authorization` header (case-insensitive), add
   `Authorization: Bearer ${await getPreviewToken()}`. If the response is `401` and a token was injected,
   refetch the token once (`queryClient.invalidateQueries(qk.previewToken(appId))`) and retry once.
   Never attach the token to any other origin or path.
4. Log every `/api/p/` call to the console store as `[api] GET /api/p/shop/orders/42 → 200 (31 ms)`, and
   `501`s with the operation name.
5. Do **not** set `window.__APP_TOKEN` inside the preview iframe: the parent injects, so the generated
   client's `token` branch is simply skipped in preview.

`sandbox-runtime.ts:55-59`: `headers: opts.headers ? Object.fromEntries(new Headers(opts.headers).entries()) : undefined`
(F14). `bridge.ts` types unchanged.

### 4.8 The published-app URL bug

`engine:routes/public_apps.py:151-156` rewrites `/api/…` to `'/a/'+slug+'/api'+url`, so
`/api/workflow/{id}/run` becomes `/a/{slug}/api/api/workflow/{id}/run`, which never matches the proxy route
`/a/{slug}/api/workflow/{id}/run` (`public_apps.py:373`). Phase 3 fixes it backend-side (§3.3 runtime fix:
strip the leading `/api`; pass `/api/p/…` through unchanged with `Authorization: Bearer <app token>`).
Studio consequences:
- Nothing in the studio rewrites URLs for published apps; no studio change is needed for the fix.
- The **preview** has no equivalent of `/api/workflow/{id}/run`: the bridge sends it to
  `${backends.workflow}/api/workflow/{id}/run`, which does not exist on the engine (it has
  `/api/workflows/{id}/run`, plural, with no body, `api.ts:131-135`). So a generated app using that pattern
  works published but 404s in preview. G20 proposes a preview endpoint; until then the API panel steers
  apps to project routes (`/api/p/…`), which behave identically in preview and published.

---

## 5. Existing flows that must keep working

| Flow | Today | Change in API usage |
|---|---|---|
| Projects page (items grid) | `GET /api/workflows` + N×GET, `GET /api/apps`, `GET /api/executions` (`projects.tsx:76-78`) | Replaced by `/projects` (projects grid) + project lists. The old calls remain valid; `useWorkflows`/`useApps` stay for any non-project caller. |
| Editor load | raw `fetch GET /api/workflows/{id}` (`editor.tsx:134`) | Same endpoint via `request()` with `Content-Type` (harmless). Response gains `project_id`. |
| Editor save (create) | `POST /api/workflows` (`useWorkflowApi.ts:33`) | `POST /api/projects/{P}/workflows` (same body, contract §3.1). For `proj_default` either is equivalent. |
| Editor save (update) | `PUT /api/workflows/{id}` | unchanged |
| Run (bottom panel, SSE) | `POST /execution-stream/{id}` `{input_data, environment}` / `…/adhoc` `{...wf, input_data, environment}` (`useExecutionStream.ts:115-131`) | `environment` = project env store value (not the global one). Adhoc adds `project_id: P`. Saved-workflow body unchanged (project comes from the row, §3.1). |
| NDV execute | `POST /api/workflows/run-adhoc` (no env) | add `environment` and `project_id` (fixes F8). Body keys already accepted by `WorkflowCreateRequest` (`engine:schemas/workflow.py:103`); `project_id` per §3.1. |
| Publish/unpublish/run/delete workflow | `api.ts:112-135` | unchanged |
| Executions (latest run per workflow) | `GET /api/executions` (`useLatestExecutions.ts:11`) | project lists use `GET /api/projects/{P}/runs`; old hook kept for compatibility, unused by new pages. |
| Env variables modal | `/api/variables*` (`useVariablesApi.ts`) | unchanged calls; still team-scoped (§3.2 last paragraph). Opened from the project env selector ("Team variables…") and from Secrets team rows. The modal's title gains "(team-shared)". |
| Env dropdown options | `/api/variables/environments` + fallbacks | inside a project: `project.environments`. `/api/variables/environments` only used by the modal for team envs. |
| Node palette | `GET /api/nodes` | unchanged; `Respond`, `DataTable`, `PythonStep` appear automatically. |
| ExecuteWorkflow selector | `useWorkflows()` | `GET /api/projects/{P}/workflows` |
| AI workflow chat | `POST /api/ai/chat` | unchanged |
| App builder load/save/rename/versions/revert/label | `api.ts:217-308` | unchanged, plus files exclude `api/client.ts`. |
| App builder chat | `POST /api/ai/app-builder/chat` | body adds `project_id`, `route_ids` (additive, §3.6). |
| App allow-list (`api_execution_ids`) + API tester | `AppBuilderChatPanel.tsx:150-176`; `/api/api-tester/*` | unchanged |
| Preview | bridge proxies all fetches (`IframeSandbox.tsx:102-140`) | + source check, + preview token for `/api/p/` only, + header normalisation, + merged `api/client.ts`. Other URLs (webhooks, absolute external URLs) behave exactly as today. |
| Publish / unpublish app | `POST /api/apps/{id}/publish|unpublish` (`PublishDialog.tsx:44,81`) | unchanged; backend now injects `api/client.ts` at publish (§3.6). |
| Published apps `/a/{slug}` | engine-only | Not a studio flow; link built by `buildPublicUrl` (`AppBuilderShell.tsx:34-37`) unchanged. |
| Webhook URLs in NDV | `NodeExtensions.tsx:36,38` | unchanged |
| File browse | relative `/api/files/browse` (`FilePathField.tsx:59`) | out of scope (existing bug, §1) |

---

## 6. Journeys for integration tests

Rules for turning these into tests:
- Calls are made with the `StudioClient` (`tests/integration/conftest.py:167`): `/api` prefix and
  `Content-Type: application/json` on every call. Router (`/api/p/…`), broker (`/api/broker/call`) and
  public (`/a/…`) calls use `raw` with only the headers listed.
- `→ 201 {…}` lists the **key** fields asserted; "contains" means the list has an element matching.
  Error codes are asserted as `detail.code` for management endpoints (§3 shared conventions), `error`
  for router responses, `error.code` for broker responses (G1 covers the envelope ambiguity).
- Each journey creates its own project with a unique slug `S = "<prefix>-" + uniq()`.
- Gates: **[esbuild]** steps need `esbuild` on PATH (§7.1); **[bwrap]** needs the sandbox;
  **[LLM]** steps only assert the request is accepted (not `422`) — no LLM is configured.

### Phase coverage

| Journey | Phases |
|---|---|
| J1 Flagship: project → secret → route → test → schema → OpenAPI → app → release → rollback | 1, 2, 5, 6, 9 (+3 publish) |
| J2 Legacy editor in the Default project | 0→1 compat, 1 |
| J3 Legacy app builder + published app | 0→1 compat, 1, 6 (client injection) |
| J4 Team variables modal + environments | 0→1 compat, 2 |
| J5 API tester + AI chats | 0 compat, 6 (chat body) |
| J6 Projects home, shell, redirects | 1 |
| J7 Project settings, environments, moves, delete | 1, 2 |
| J8 Secrets: precedence, write-only, missing scan, inline create | 2 |
| J9 Published app runs a real workflow | 3 |
| J10 SDK catalogue + dev broker token + broker policy | 4 |
| J11 Router semantics, Respond node, stubs, failures, runs | 5 |
| J12 API keys and auth modes | 5, 6 (client excludes api_key) |
| J13 Schema editing + `validate_request` | 5 (§9 decision 4) |
| J14 App ↔ API: usage, stubs from UI, breaking changes, preview token, AI body | 6 |
| J15 Data tables + DataTable node | 7 |
| J16 Python modules + sandbox trace | 8 |
| J17 Release gates, explicit rollback, snapshot detail | 9 |

---

#### J1 — Flagship [phases 1, 2, 5, 6, 9; publish is esbuild-gated]

1. `GET /api/projects` → `200`; contains `{id:"proj_default"}` with keys `counts.{apps,workflows,routes,tables}`, `health.status ∈ {ok,degraded,failing,unknown}`, `last_run` (object or null).
2. `POST /api/projects` `{name:"Shop "+uniq, slug:S}` → `201` `{id: startswith "proj_", slug:S, environments:[{name:"dev",kind:"draft"},{name:"prod",kind:"release"}], default_environment:"dev", live_environment:"prod", settings.egress_allowlist:[] }` → `P`.
3. `GET /api/projects/{P}` → `200` same `id`, `slug`.
4. `GET /api/projects/{S}` → `200` `id == P` (slug resolution).
5. `GET /api/projects/{P}/summary` → `200` `counts == {apps:0,workflows:0,routes:0,tables:0}`, `last_run: null`, `health.status:"unknown"`.
6. `POST /api/projects/{P}/secrets` `{key:"GREETING", value:"hello-dev", environment:"dev", type:"string"}` → `201` `{key:"GREETING", environment:"dev", type:"string", value:"hello-dev", scope:"project", overrides_team:false, overridden:false}`.
7. `GET /api/projects/{P}/secrets?environment=dev` → `200` contains `{key:"GREETING", scope:"project"}`.
8. `POST /api/projects/{P}/routes` `{method:"GET", path:"/orders/{id}", auth:"none", handler:{type:"workflow", create:true}}` → `201` `{route:{id: startswith "route_", name:"getOrdersById", method:"GET", path:"/orders/{id}", auth:"none", source:"ui", response_mode:"auto", validate_request:false, used_by_count:0, url:"/api/p/"+S+"/orders/{id}", handler:{type:"workflow", workflow_id: W}}, spec_revision: R1 (int), breaking_changes:[]}` → `R`, `W`.
9. `GET /api/projects/{P}/workflows` → `200` contains `{id:W, name:"GET /orders/{id}", project_id:P}`.
10. `GET /api/workflows/{W}` → `200` `project_id:P`; `definition.nodes[].type` ⊇ `{"Start","Respond"}`.
11. Router (no credential; live env `prod` has no release yet, so drafts are served): `raw GET /api/p/{S}/orders/42` → `200` body `{"id":"42"}`; header `X-Execution-Id` present.
12. Editor edits the handler: `PUT /api/workflows/{W}` `{name:"GET /orders/{id}", nodes:[{name:"Start",type:"Start",parameters:{},position:{x:0,y:0}}, {name:"Set",type:"Set",parameters:{mode:"manual", fields:[{name:"id",value:"{{ $json.params.id }}"},{name:"greeting",value:"{{ $vars.GREETING }}"}], keepOnlySet:true}, position:{x:200,y:0}}], connections:[{source_node:"Start",target_node:"Set",source_output:"main",target_input:"main"}], settings:{}}` → `200`.
13. `POST /api/projects/{P}/routes/{R}/test` `{params:{id:"42"}, query:{}, headers:{}, body:null, environment:"dev", apply_schemas:false}` → `200` `{status:200, body:{id:"42", greeting:"hello-dev"}, execution_id: startswith "exec_", inferred.response_schema.properties.id.type:"string", inferred.response_schema.properties.greeting.type:"string", applied:false}`; `duration_ms` is a number.
14. `GET /api/projects/{P}/routes/{R}` → `200` `response_schema` is null (test without apply saved nothing).
15. Same test call with `apply_schemas:true` → `200` `applied:true`.
16. `GET /api/projects/{P}/routes/{R}` → `200` `response_schema.properties` has keys `id`, `greeting`; `validate_request:false`.
17. `GET /api/projects/{P}/spec/revisions` → `200`; max `revision == R1 + 1`; every item has `spec_hash`, `created_at`, `changes` (list).
18. `GET /api/projects/{P}/spec/diff?from={R1}&to={R1+1}` → `200` `changes` non-empty and every `breaking == false` (optional additions are non-breaking, §3.5).
19. `GET /api/projects/{P}/openapi.json?environment=dev` → `200` `openapi` startswith `"3.1"`; `paths["/orders/{id}"].get.operationId == "getOrdersById"`; the `200` response schema has property `greeting`.
20. `GET /api/projects/{P}/client.ts?environment=dev` → `200`, `content-type` startswith `text/typescript`; body contains `getOrdersById`, `'/api/p/` + S + `'`, and `@generated`.
21. `POST /api/projects/{P}/apps` `{name:"Orders UI", definition:{sourceCode:null}}` → `201` `{id: A, project_id:P}`.
22. `POST /api/projects/{P}/apps/{A}/preview-token` → `201` `{token, expires_at, environment:"dev"}` → `PT`.
23. `raw GET /api/p/{S}/orders/42` header `Authorization: Bearer {PT}` → `200` `{id:"42", greeting:"hello-dev"}`.
24. `PUT /api/apps/{A}` `{source_code: APP_SRC, files:[{path:"App.tsx", content: APP_SRC}], create_version:true, version_trigger:"manual"}` with `APP_SRC = "import { api } from './api/client'\nexport default function App() {\n  const load = () => api.getOrdersById({ id: '42' })\n  return <button onClick={load}>Load</button>\n}\n"` → `200` `current_version.version_number == 1`.
25. `GET /api/apps/{A}/api-usage` → `200` contains `{operation:"getOrdersById", method:"GET", path:"/orders/{id}", file:"App.tsx", line:3, route_id:R, status:"ok"}`.
26. `GET /api/projects/{P}/routes/{R}/used-by` → `200` contains `{app_id:A, app_name:"Orders UI", file:"App.tsx", line:3, operation:"getOrdersById"}`.
27. `GET /api/projects/{P}/routes` → `200` contains `{id:R, used_by_count:1}`.
28. **[esbuild]** `POST /api/apps/{A}/publish` `{slug:S+"-ui", access:"public"}` → `200` `{active:true, slug:S+"-ui"}`, `public_url` endswith `/a/`+S+`-ui`.
29. `POST /api/projects/{P}/releases` `{environment:"dev", notes:"r1"}` → `201` `{id: startswith "rel_", environment:"dev", number:1, is_active:true, status:"active"}`; every `checks[].status ∈ {pass,warn}`; contains `{id:"app_calls_resolve", status:"pass"}` → `REL1`.
30. `POST /api/projects/{P}/releases/{REL1}/promote` `{to_environment:"prod"}` → `422` `detail.code:"checks_failed"`; `detail.checks` contains `{id:"secrets_present", status:"fail", details.missing:["GREETING"]}`.
31. `GET /api/projects/{P}/releases?environment=prod` → `200` contains `{status:"blocked", is_active:false}`; no item has `is_active:true`.
32. Inline create (fix from the checks list): `POST /api/projects/{P}/secrets` `{key:"GREETING", value:"hello-prod", environment:"prod", type:"string"}` → `201`.
33. `POST /api/projects/{P}/releases/{REL1}/promote` `{to_environment:"prod"}` → `201` `{environment:"prod", is_active:true, status:"active", source_release_id:REL1}` → `PROD1`.
34. `raw GET /api/p/{S}/orders/42` (no credential → prod) → `200` `{id:"42", greeting:"hello-prod"}`.
35. Draft edit after release: `PUT /api/workflows/{W}` with step 12's body plus field `{name:"v", value:"2"}` → `200`.
36. `raw GET /api/p/{S}/orders/42` → `200` body has **no** key `v` (prod pinned).
37. `POST /api/projects/{P}/routes/{R}/test` (step 13 body) → `200` `body.v == "2"` (dev serves drafts).
38. **[esbuild]** `raw GET /a/{S}-ui` → `200` HTML contains `window.__APP_TOKEN=`; extract the token string → `AT`.
39. **[esbuild]** `raw GET /api/p/{S}/orders/42` header `Authorization: Bearer {AT}` → `200` `greeting:"hello-prod"` (app token → `live_environment`).
40. `POST /api/projects/{P}/releases` `{environment:"dev", notes:"r2"}` → `201` `{number:2, is_active:true}` → `REL2`.
41. `POST /api/projects/{P}/releases/{REL2}/promote` `{to_environment:"prod"}` → `201` `is_active:true` → `PROD2`.
42. `raw GET /api/p/{S}/orders/42` → `200` `v == "2"`.
43. Rollback preview: `GET /api/projects/{P}/releases/{PROD1}/diff?against=active` → `200` keys `routes, apps, workflows, secrets`; `workflows` contains `{workflow_id:W}`.
44. `POST /api/projects/{P}/environments/prod/rollback` `{}` → `200` `{environment:"prod", is_active:true}` (G12: response id expected `PROD1`).
45. `raw GET /api/p/{S}/orders/42` → `200` no key `v`.
46. `GET /api/projects/{P}/releases?environment=prod` → exactly one `is_active:true`; contains `{id:PROD2, status:"rolled_back"}`.
47. `GET /api/projects/{P}/releases/{PROD1}` → `200` `snapshot.routes` contains `{name:"getOrdersById", handler.type:"workflow", handler.workflow_id:W}`; `snapshot.apps` contains `{app_id:A}`; `snapshot.spec_hash` non-empty; `snapshot.secret_bindings` contains `{key:"GREETING", scope:"project"}`.
48. `GET /api/projects/{P}/summary` → `200` `counts == {apps:1, workflows:1, routes:1, tables:0}`; `last_run.mode == "api"`.
49. `GET /api/projects/{P}/runs?mode=api&limit=50` → `200` non-empty; every item `mode:"api"`, `environment ∈ {"dev","prod"}`.

#### J2 — Legacy editor in the Default project [compat, 1]

1. `GET /api/workflows` → `200` list; every item has `project_id`.
2. `POST /api/workflows` `SET_WF("{{ 20 + 22 }}")` → `201` `{id:W, active:false, webhook_url:"/webhook/"+W, project_id:"proj_default"}`.
3. `GET /api/workflows/{W}` → `200` `project_id:"proj_default"`, node names `["Start","Set"]`.
4. `GET /api/workflows?project_id=proj_default` → contains `W`; `GET /api/projects/proj_default/workflows` → contains `{id:W, project_id:"proj_default"}`.
5. `GET /api/projects/proj_default` → `200` `default_environment:"default"`, `live_environment:"default"`, `environments` contains `{name:"default"}`.
6. `PUT /api/workflows/{W}` same body with `fields[0].value:"{{ 6 * 7 }}"` → `200`.
7. `POST /execution-stream/{W}` `{input_data:null, environment:"default"}` → SSE; first event `type:"execution:start"`, contains `{type:"node:complete", nodeName:"Set"}`, last event `type:"execution:complete"`.
8. `POST /execution-stream/adhoc` `{...SET_WF("{{ 1 + 1 }}"), input_data:null, environment:"default", project_id:"proj_default"}` → SSE, same event order.
9. `POST /api/workflows/run-adhoc` `{...SET_WF("{{ 1 + 1 }}"), environment:"default", project_id:"proj_default"}` → `200` `{status:"success", execution_id}`; `errors:[]`.
10. `POST /api/workflows/{W}/run` (no body) → `200` `{status:"success", execution_id: X}`.
11. `GET /api/executions?workflow_id={W}` → `200` ≥ 2 items with `workflow_id:W`.
12. `GET /api/projects/proj_default/runs?limit=50` → contains `{id:X, environment:"default"}`.
13. `GET /api/projects/proj_default/runs/{X}` → `200` `status:"success"`; `node_data` has key `Set`; key `trace` present.
14. `POST /api/workflows/{W}/publish` `{message:"v1"}` → `200` `{active:true}`, `version_id` int.
15. `POST /api/workflows/{W}/unpublish` → `200` `{active:false}`.
16. `GET /api/nodes` → `200` types ⊇ `{"Start","Set","Respond"}` (and `DataTable` from phase 7, `PythonStep` from phase 8); `GET /api/nodes/Respond` → `200` keys ⊇ `{type, displayName, description, properties}`.
17. Old URL redirect lookup: `GET /api/workflows/{W}` → `project_id` used for `/p/proj_default/automations/{W}` (same as step 3).
18. `DELETE /api/workflows/{W}` → `200` `{success:true}`.

#### J3 — Legacy app builder + published app [compat, 1, 6]

1. `POST /api/apps` `{name:uniq("app"), definition:{sourceCode:null}}` → `201` `{id:A, project_id:"proj_default"}`.
2. `GET /api/apps` → contains `{id:A}`; `GET /api/apps?project_id=proj_default` → contains `A`.
3. `PUT /api/apps/{A}` `{name:"Renamed"}` → `200` `name:"Renamed"`.
4. `PUT /api/apps/{A}` `{source_code:SRC, files:[{path:"App.tsx",content:SRC}], create_version:true, version_trigger:"manual"}` (SRC = `export default function App(){return <div>hi</div>}`) → `200` `current_version.version_number:1`.
5. Same with `version_trigger:"ai", version_prompt:"make it blue"` → `200` `current_version.version_number:2`.
6. `GET /api/apps/{A}/versions` → `200` 2 items; keys ⊇ `{id, version_number, trigger, label, prompt, created_at}` → `V1` = version 1's `id`.
7. `PATCH /api/apps/{A}/versions/{V1}` `{label:"first"}` → `200` `label:"first"`.
8. `GET /api/apps/{A}/versions/{V1}` → `200` `source_code == SRC`.
9. `POST /api/apps/{A}/versions` `{source_code:SRC, trigger:"manual"}` → `201`.
10. `POST /api/apps/{A}/versions/{V1}/revert` → `200` `current_version` present.
11. `PUT /api/apps/{A}` `{api_execution_ids:[]}` → `200` (chat panel allow-list write, `AppBuilderChatPanel.tsx:173`).
12. **[esbuild]** `POST /api/apps/{A}/publish` `{slug:uniq("pub"), access:"public"}` → `200` `{active:true, version_id:int, bundle_hash, public_url}`.
13. **[esbuild]** `GET /api/apps/{A}` → `200` `active:true`, `published_version` present, `slug` set.
14. **[esbuild]** `raw GET /a/{slug}` → `200` HTML contains `__APP_TOKEN`.
15. **[esbuild]** `POST /api/apps/{A}/unpublish` → `200` `active:false`; `raw GET /a/{slug}` → `404`.
16. `DELETE /api/apps/{A}` → `200` `{success:true}`.

#### J4 — Team variables modal + environments [compat, 2]

1. `GET /api/variables/environments` → `200` list of strings.
2. `POST /api/variables` `{team_id:"default", key:K=uniq upper, value:"v1", environment:"default", type:"secret"}` → `201` `value:null`.
3. `GET /api/variables?environment=default` → contains `{key:K, value:null, type:"secret"}`.
4. `PUT /api/variables/{id}` `{value:"v2", description:null}` → `200`.
5. `POST /api/variables` same key/env → `409`.
6. Project row must not leak into the modal: `POST /api/projects` → `P`; `POST /api/projects/{P}/secrets` `{key:K, value:"p", environment:"dev", type:"secret"}` → `201`; `GET /api/variables?environment=dev` → does **not** contain key `K`.
7. `DELETE /api/variables/{id}` → `200` `{success:true}`.

#### J5 — API tester + AI chats [compat, 6]

1. `POST /api/api-tester/execute` `{name:null, method:"GET", url:UP+"/echo?x=1", headers:{}}` → `201` `{id:E, response_status:200, error:null}`.
2. `GET /api/api-tester/executions` → contains `E`; `GET /api/api-tester/executions/{E}` → `200` `method:"GET"`.
3. `PATCH /api/api-tester/executions/{E}` `{name:"echo"}` → `200` `name:"echo"`.
4. `DELETE /api/api-tester/executions/{E}` → `200`.
5. **[LLM]** `POST /api/ai/chat` `{message:"hi", session_id:uniq, workflow_context:null, conversation_history:[], mode_hint:null}` → status ≠ `422`.
6. **[LLM]** `POST /api/ai/app-builder/chat` `{message:"hi", api_execution_ids:[], conversation_history:[]}` (legacy body) → status ≠ `422`.
7. **[LLM]** same with `{…, app_id:A, project_id:P, route_ids:[R]}` (A, P, R from a fresh project with one route) → status ≠ `422`.

#### J6 — Projects home, shell, redirects [1]

1. `POST /api/projects` `{name:"Billing "+uniq}` (no slug) → `201` `slug` non-empty, lowercase → `P`, `S`.
2. `POST /api/projects` `{name:"Other", slug:S}` → `409` `detail.code:"slug_taken"`.
3. `POST /api/projects` `{name:""}` → `422`.
4. `GET /api/projects/proj_doesnotexist` → `404`.
5. `POST /api/projects/{P}/workflows` `SET_WF("{{ 1 }}")` → `201` `{id:W, project_id:P}`.
6. `GET /api/projects/{P}/workflows` → exactly `[W]`; `GET /api/workflows?project_id={P}` → exactly `[W]`; `GET /api/workflows` → contains `W` and more.
7. `POST /api/projects/{P}/apps` `{name:"Billing UI", definition:{sourceCode:null}}` → `201` `{id:A, project_id:P}`; `GET /api/projects/{P}/apps` → exactly `[A]`; `GET /api/apps?project_id={P}` → exactly `[A]`.
8. Redirects: `GET /api/workflows/{W}` → `project_id:P`; `GET /api/apps/{A}` → `project_id:P`.
9. `POST /api/workflows/{W}/run` → `200` `execution_id:X`.
10. `GET /api/projects/{P}/summary` → `counts.workflows:1, counts.apps:1`, `last_run.execution_id:X`.
11. `GET /api/projects` → contains `{id:P, counts.workflows:1, last_run.execution_id:X}`.
12. Cross-project isolation: `POST /api/projects` → `Q`; `GET /api/projects/{Q}/runs/{X}` → `404`.

#### J7 — Project settings, environments, moves, delete [1, 2]

1. `POST /api/projects` → `P`; `POST /api/projects` → `P2`.
2. `PUT /api/projects/{P}` `{name:"Renamed", description:"d"}` → `200` `name:"Renamed"`, `description:"d"`.
3. `PUT /api/projects/{P}` `{environments:[{name:"dev",kind:"draft"},{name:"staging",kind:"draft"},{name:"prod",kind:"release"}]}` → `200` 3 environments.
4. `PUT /api/projects/{P}` `{default_environment:"nope"}` → `422`.
5. `POST /api/projects/{P}/secrets` `{key:"STG", value:"x", environment:"staging", type:"string"}` → `201` → `SID`.
6. `PUT /api/projects/{P}` `{environments:[{name:"dev",kind:"draft"},{name:"prod",kind:"release"}]}` → `409` `detail.code:"environment_in_use"`.
7. `DELETE /api/projects/{P}/secrets/{SID}` → `200`; repeat step 6 → `200` 2 environments.
8. `PUT /api/projects/{P}` `{settings:{egress_allowlist:["127.0.0.1"]}}` → `200` `settings.egress_allowlist:["127.0.0.1"]`.
9. `POST /api/projects/{P}/workflows` `SET_WF("{{ 1 }}")` → `W`; `POST /api/projects/{P}/routes` `{method:"GET", path:"/ping", handler:{type:"workflow", workflow_id:W}}` → `201`.
10. Move refused: `PUT /api/workflows/{W}` `{project_id:P2}` → `409` `detail.code:"workflow_in_use"`.
11. `POST /api/projects/{P}/workflows` `SET_WF("{{ 2 }}")` → `W2`; `PUT /api/workflows/{W2}` `{project_id:P2}` → `200` `project_id:P2`; `GET /api/projects/{P2}/workflows` contains `W2`.
12. `POST /api/projects/{P}/apps` → `A`; `PUT /api/apps/{A}` `{project_id:P2}` → `200` `project_id:P2`.
13. `DELETE /api/projects/{P}` → `409` `detail.code:"project_not_empty"`.
14. `DELETE /api/projects/proj_default` → `409` `detail.code:"default_project"`.
15. Empty P: `DELETE /api/projects/{P}/routes/{route}` → `200`; `DELETE /api/workflows/{W}` → `200`; `DELETE /api/projects/{P}` → `200` `{success:true}`; `GET /api/projects/{P}` → `404`.

#### J8 — Secrets [2]

1. `POST /api/projects` → `P`; `POST /api/variables` `{team_id:"default", key:"TOKEN_"+u, value:"a", environment:"dev", type:"string"}` → `201` → `TID` (team row).
2. `POST /api/projects/{P}/secrets` `{key:"TOKEN_"+u, value:"b", environment:"dev", type:"string"}` → `201` `{scope:"project", overrides_team:true}` → `PID`.
3. `GET /api/projects/{P}/secrets?environment=dev` → contains `{id:PID, scope:"project", overrides_team:true, value:"b"}` and `{id:TID, scope:"team", overridden:true}`.
4. `POST /api/projects/{P}/workflows` `SET_WF("{{ $vars.TOKEN_"+u+" }}")` → `W`; `POST /execution-stream/{W}` `{input_data:null, environment:"dev"}` → `node:complete` for `Set` whose output item `answer == "b"`.
5. `POST /api/workflows` `SET_WF("{{ $vars.TOKEN_"+u+" }}")` (Default project) → `WD`; `POST /execution-stream/{WD}` `{input_data:null, environment:"dev"}` → `answer == "a"`.
6. `POST /api/projects/{P}/secrets` `{key:"SK_"+u, value:"s3cret-value", environment:"dev", type:"secret", description:"d"}` → `201` `value:null` → `SKID`; list → `{key:"SK_"+u, value:null}`.
7. `PUT /api/projects/{P}/secrets/{SKID}` `{value:"rotated-value"}` → `200` `description:"d"` (no null-clobber).
8. `PUT /api/projects/{P}/secrets/{SKID}` `{allowed_hosts:["api.example.com"]}` → `200` `allowed_hosts:["api.example.com"]`.
9. `PUT /api/projects/{P}/secrets/{TID}` `{value:"x"}` → `404`.
10. `POST /api/projects/{P}/secrets` step 2 body again → `409` `detail.code:"exists"`.
11. `POST …/secrets` `{key:"1BAD", value:"x", environment:"dev", type:"string"}` → `422` `detail.code:"invalid_key"`.
12. `POST …/secrets` `{key:"OK", value:"x", environment:"nope", type:"string"}` → `422` `detail.code:"unknown_environment"`.
13. `POST /api/projects/{P}/workflows` `SET_WF("{{ $vars.NOPE_"+u+" }}")` → `W2`; `GET /api/projects/{P}/secrets/missing` → contains `{key:"NOPE_"+u, missing_in:["dev","prod"], referenced_by:[{kind:"workflow", id:W2, node:"Set"}]}`; `GET …/secrets/missing?environment=dev` → contains it with `missing_in:["dev"]`.
14. `GET /api/projects/{P}/summary` → `missing_secrets ≥ 1`.
15. Inline create: `POST …/secrets` `{key:"NOPE_"+u, value:"y", environment:"dev", type:"string"}` → `201`; missing → `{key:"NOPE_"+u, missing_in:["prod"]}`.
16. `DELETE /api/projects/{P}/secrets/{PID}` → `200`; step 4's run again → `answer == "a"` (team value now visible).
17. `GET /api/variables?environment=dev` → contains `TID`, not `PID`/`SKID`. Cleanup `DELETE /api/variables/{TID}`.

#### J9 — Published app runs a real workflow [3; esbuild-gated]

1. `POST /api/projects` → `P`; `POST /api/projects` → `Q`.
2. `POST /api/projects/{P}/workflows` `SET_WF("{{ $json.x }}")` → `W1`; `SET_WF("{{ $vars.SV }}")` → `W2`; `SET_WF("{{ 3 }}")` → `W3`; `POST /api/projects/{Q}/workflows` `SET_WF("{{ 4 }}")` → `WQ`.
3. `POST /api/projects/{P}/secrets` `{key:"SV", value:"pa55word-xyz", environment:"prod", type:"secret"}` → `201` (prod = live env).
4. `POST /api/projects/{P}/apps` → `A`; `PUT /api/apps/{A}` `{workflow_ids:[W1,W2,WQ]}` → `200` `workflow_ids` (app Settings panel; G22).
5. `PUT /api/apps/{A}` `{source_code:SRC, files:[{path:"App.tsx",content:SRC}], create_version:true, version_trigger:"manual"}` → `200`.
6. `POST /api/apps/{A}/publish` `{slug:uniq("run"), access:"public"}` → `200` → `slug`.
7. `raw GET /a/{slug}` → `200`; extract `__APP_TOKEN` → `AT`.
8. `raw POST /a/{slug}/api/workflow/{W1}/run` `Authorization: Bearer {AT}`, body `{"x":5}` → `200` `{status:"success", execution_id: startswith "exec_", errors:[]}`, `data[0].answer == 5`.
9. Same with body `{"x":"{{ $vars.SV }}"}` → `200` `data[0].answer == "{{ $vars.SV }}"` (not expanded).
10. `raw POST /a/{slug}/api/workflow/{W2}/run` body `{}` → `200` `data[0].answer == "***"` (redacted).
11. `raw POST /a/{slug}/api/workflow/{WQ}/run` → `403` `detail.code:"workflow_not_in_project"`.
12. `raw POST /a/{slug}/api/workflow/{W3}/run` (not in allow-list) → `403`.
13. `raw POST /a/{slug}/api/workflow/{W1}/run` `Authorization: Bearer garbage` → `401`; no `Authorization` → `401`.
14. `GET /api/projects/{P}/runs?limit=50` → contains `{id: <step 8 execution_id>, environment:"prod"}`.

#### J10 — SDK catalogue + dev broker token + broker policy [4]

1. `GET /api/sdk/functions` → `200` contains `{function:"http_request", node_type:"HttpRequest", egress:true}`; no item with `node_type ∈ {"Webhook","If"}`; every item has `parameters.type:"object"`.
2. `GET /api/sdk/python` → `200` `version` 64 hex chars; `files` has key `studio/__init__.py`.
3. `GET /api/sdk/python.zip` → `200` body starts with `PK`.
4. `POST /api/projects` → `P`; `POST /api/projects/{P}/broker-tokens` `{environment:"dev"}` → `201` `{token: startswith "brk_", expires_at}` → `BT`.
5. `POST /api/projects/{P}/broker-tokens` `{environment:"prod"}` → `403`.
6. `raw POST /api/broker/call` (no auth) `{function:"http_request", parameters:{method:"GET", url:UP+"/x"}}` → `401` `{ok:false, error.code:"invalid_token"}`.
7. `raw POST /api/broker/call` `Authorization: Bearer {BT}` `{function:"http_request", parameters:{method:"GET", url:UP+"/x", headers:[{name:"Authorization", value:"Bearer {{ $vars.UP_KEY }}"}]}, items:[{}]}` → `422` `{ok:false, call_id, error:{code:"missing_secret", key:"UP_KEY", environment:"dev"}}`.
8. Inline create: `POST /api/projects/{P}/secrets` `{key:"UP_KEY", value:"tok-abc-123456", environment:"dev", type:"secret"}` → `201` → `KID`.
9. Step 7 call → `403` `error.code:"secret_not_bound"`.
10. `PUT /api/projects/{P}/secrets/{KID}` `{allowed_hosts:["127.0.0.1"]}` → `200`.
11. Step 7 call → `200` `{ok:true, call_id: startswith "bcall_"}`, `items[0].status == 200`, `items[0].body.headers.authorization == "Bearer ***"`.
12. Same but `url: "http://localhost:<port>/x"` → `403` `error.code:"host_not_allowed"`, `error.host:"localhost"`.
13. `{function:"http_request", parameters:{method:"GET", url:UP+"/x", credentialId:"cred_x"}}` → `422` `error.code:"credential_not_allowed"`.
14. Header value `"Bearer {{ $vars.UP_KEY.toUpperCase() }}"` → `422` `error.code:"secret_expression_not_allowed"`.
15. `{function:"webhook", parameters:{}}` → `403` `error.code:"function_not_allowed"`.
16. `GET /api/projects/{P}/broker-calls` → `200` ≥ 6 rows; no row's JSON contains `tok-abc-123456`; contains `{status:"denied", error_code:"host_not_allowed"}` and `{status:"ok"}`.

#### J11 — Router semantics, Respond node, stubs, failures, runs [5]

1. `POST /api/projects` → `P`, `S`.
2. `POST /api/projects/{P}/routes` `{method:"GET", path:"/items", handler:{type:"stub"}}` → `201` `{route.name:"getItems", route.handler.type:"stub", route.source:"ui"}` → `RS`.
3. `raw GET /api/p/{S}/items` → `501` `{error:"not_implemented", route:"GET /items", operation:"getItems"}`.
4. `raw POST /api/p/{S}/items` → `405` `{error:"method_not_allowed"}`, header `Allow` contains `GET`.
5. `raw GET /api/p/{S}/nothing` → `404` `{error:"route_not_found"}`; `raw GET /api/p/no-such-project-{u}/x` → `404` `{error:"project_not_found"}`.
6. `POST …/routes` `{method:"GET", path:"/items", handler:{type:"stub"}}` → `409` `detail.code:"route_conflict"`; `{method:"GET", path:"/a/{x}", handler:{type:"stub"}}` → `201`, then `{method:"GET", path:"/a/{y}", handler:{type:"stub"}}` → `409` `route_conflict` (same path shape).
7. `{method:"POST", path:"/other", name:"getItems", handler:{type:"stub"}}` → `409` `detail.code:"name_taken"`; `{method:"GET", path:"items", handler:{type:"stub"}}` → `422` `detail.code:"invalid_path"`.
8. `POST /api/projects` → `Q`; `POST /api/projects/{Q}/workflows` `SET_WF("{{ 1 }}")` → `WQ`; `POST /api/projects/{P}/routes` `{method:"GET", path:"/q", handler:{type:"workflow", workflow_id:WQ}}` → `422` `detail.code:"handler_not_in_project"`.
9. Respond node: `POST /api/projects/{P}/workflows` `{name:"respond", nodes:[Start, {name:"Respond", type:"Respond", parameters:{statusCode:201, headers:[{name:"X-Demo", value:"yes"}], body:"{{ $json.params }}", contentType:"application/json"}, position:{x:200,y:0}}], connections:[Start→Respond], settings:{}}` → `WR` (header item shape per `GET /api/nodes/Respond`).
10. `PUT /api/projects/{P}/routes/{RS}` `{handler:{type:"workflow", workflow_id:WR}}` → `200` `route.handler.workflow_id:WR`, `breaking_changes:[]`.
11. `raw GET /api/p/{S}/items` → `201`, header `X-Demo: yes`, header `X-Execution-Id`.
12. Response modes: `POST /api/projects/{P}/workflows` `SET_WF("{{ 42 }}")` → `WS`; `POST …/routes` `{method:"GET", path:"/one", response_mode:"all_items", handler:{type:"workflow", workflow_id:WS}}` → `201`; `raw GET /api/p/{S}/one` → `200` `[{"answer":42}]`; `PUT` that route `{response_mode:"first_item"}` → `200`; `raw GET` → `{"answer":42}`.
13. Handler failure: `POST /api/projects/{P}/workflows` `{name:"boom", nodes:[Start, {name:"Stop", type:"StopAndError", parameters:{errorMessage:"boom"}, position:{x:200,y:0}}], connections:[Start→Stop], settings:{}}` → `WE`; `POST …/routes` `{method:"GET", path:"/boom", handler:{type:"workflow", workflow_id:WE}}` → `201`; `raw GET /api/p/{S}/boom` → `502` `{error:"handler_failed", execution_id: XE}`, header `X-Execution-Id == XE`.
14. `GET /api/projects/{P}/runs?mode=api` → contains `{id:XE, mode:"api"}` with `status != "success"`; `GET /api/projects/{P}/runs/{XE}` → `200` `errors` non-empty.
15. `POST /api/projects/{P}/routes/{RS}/test` `{params:{}, query:{}, headers:{}, body:null, environment:"dev", apply_schemas:false}` → `200` `status:201`.
16. `DELETE /api/projects/{P}/routes/{RS}` → `200` `{spec_revision:int, breaking_changes}` contains `{kind:"route_removed", breaking:true}`; `raw GET /api/p/{S}/items` → `404`.

#### J12 — API keys and auth modes [5, 6]

1. `POST /api/projects` → `P`, `S`.
2. `POST …/routes` `{method:"GET", path:"/admin/stats", auth:"api_key", handler:{type:"workflow", create:true}}` → `201` → `RK`; `{method:"GET", path:"/me", auth:"app_token", handler:{type:"workflow", create:true}}` → `201` → `RA`.
3. `raw GET /api/p/{S}/admin/stats` → `401` `{error:"unauthorized"}`; `raw GET /api/p/{S}/me` → `401`.
4. `POST /api/projects/{P}/api-keys` `{name:"ci", environment:"dev"}` → `201` `{id:KID, name:"ci", environment:"dev", prefix: startswith "sk_dev_", key: startswith "sk_dev_"}` → `KEY`.
5. `GET /api/projects/{P}/api-keys` → contains `{id:KID, prefix}`; no item has key `key`.
6. `raw GET /api/p/{S}/admin/stats` `Authorization: Bearer {KEY}` → `200`; header `X-Execution-Id` → `XK`; `GET /api/projects/{P}/runs/{XK}` → `environment:"dev"`.
7. `raw GET /api/p/{S}/admin/stats` `X-API-Key: {KEY}` → `200`.
8. `raw GET /api/p/{S}/me` `Authorization: Bearer {KEY}` → `200` (app_token routes accept a key).
9. `POST /api/projects/{P}/apps` → `A`; `POST …/apps/{A}/preview-token` → `PT`; `raw GET /api/p/{S}/me` `Bearer {PT}` → `200`; `raw GET /api/p/{S}/admin/stats` `Bearer {PT}` → `401`.
10. `GET /api/projects/{P}/client.ts` → contains `getMe`, does **not** contain `getAdminStats`.
11. `POST …/api-keys` `{name:"prod", environment:"prod"}` → `201` `key` startswith `sk_prod_` → `PKEY`; `raw GET /api/p/{S}/admin/stats` `Bearer {PKEY}` → `200`, run `environment:"prod"`.
12. `DELETE /api/projects/{P}/api-keys/{KID}` → `200`; `GET …/api-keys` → `{id:KID, revoked_at: non-null}`; `raw GET …/admin/stats` `Bearer {KEY}` → `401`.

#### J13 — Schema editing + `validate_request` [5]

1. `POST /api/projects` → `P`, `S`; `POST …/routes` `{method:"POST", path:"/orders", auth:"none", request_schema:{type:"object", properties:{body:{type:"object", properties:{total:{type:"number"}}, required:["total"]}}}, handler:{type:"workflow", create:true}}` → `201` `route.validate_request:false` → `R`.
2. `raw POST /api/p/{S}/orders` body `{"total":"x"}` → `200` (not enforced by default).
3. Schema tab save: `PUT …/routes/{R}?dry_run=true` `{validate_request:true}` → `200` `breaking_changes` (assert list); `GET …/routes/{R}` → `validate_request:false` (dry run saved nothing).
4. `PUT …/routes/{R}` `{validate_request:true}` → `200` `route.validate_request:true`.
5. `raw POST /api/p/{S}/orders` `{"total":"x"}` → `422` `{error:"request_invalid", errors:[{path:"body.total"}]}`; `{"total":9.5}` → `200`; `{}` → `422`.
6. `POST …/routes/{R}/test` `{params:{}, query:{}, headers:{}, body:{"total":9.5}, environment:"dev", apply_schemas:true}` → `200` `applied:true`; `GET …/routes/{R}` → `validate_request:true` (not flipped).
7. `PUT …/routes/{R}?dry_run=true` `{request_schema:{type:"object", properties:{body:{type:"object", properties:{total:{type:"number"}, currency:{type:"string"}}, required:["total","currency"]}}}}` → `200` `breaking_changes` contains an item with `breaking:true`, `field` mentioning `currency`.

#### J14 — App ↔ API: usage, stubs, breaking changes, preview, AI body [6]

1. `POST /api/projects` → `P`, `S`; `POST …/routes` `{method:"GET", path:"/orders/{id}", response_schema:{type:"object", properties:{id:{type:"string"}, total:{type:"number"}}}, handler:{type:"workflow", create:true}}` → `R`.
2. `POST /api/projects/{P}/apps` → `A`; `PUT /api/apps/{A}` save version with `App.tsx` =
   ```
   import { api } from './api/client'
   export default function App() {
     const a = () => api.getOrdersById({ id: '1' })
     const b = () => fetch('/api/p/<S>/refunds', { method: 'POST' })
     const c = () => api.listCustomers()
     return <div onClick={() => { a(); b(); c() }} />
   }
   ```
   → `200`.
3. `GET /api/apps/{A}/api-usage` → contains `{operation:"getOrdersById", line:3, route_id:R, status:"ok"}`, `{method:"POST", path:"/refunds", line:4, route_id:null, status:"missing"}`, `{operation:"listCustomers", line:5, status:"missing"}`.
4. `POST /api/projects/{P}/routes/stubs` `{app_id:A, routes:[{method:"POST", path:"/refunds", name:"createRefund"}]}` → `201` `created` = 1 item `{name:"createRefund", source:"app_stub", handler.type:"stub"}`, `existing:[]` → `RST`.
5. Same call → `201` `created:[]`, `existing` = 1 item with `id:RST` (idempotent).
6. `GET /api/apps/{A}/api-usage` → the `/refunds` row now `{route_id:RST, status:"ok"}`.
7. `raw POST /api/p/{S}/refunds` → `501` `{operation:"createRefund"}`; `GET …/client.ts` → contains `createRefund`.
8. Implement the stub: `PUT …/routes/{RST}` `{handler:{type:"workflow", create:true}}` → `200` `route.handler.type:"workflow"`; `raw POST /api/p/{S}/refunds` → `200`.
9. `PUT …/routes/{R}?dry_run=true` `{response_schema:{type:"object", properties:{id:{type:"string"}}}}` → `200` `breaking_changes` contains `{kind:"response_field_removed", field:"total", operation:"getOrdersById", breaking:true, affected:[{app_id:A, file:"App.tsx", line:3}]}`; `GET …/routes/{R}` still has `total`.
10. `PUT …/routes/{R}` same body → `200` `breaking_changes` same; `spec_revision` = previous + 1.
11. `GET /api/projects/{P}/spec/revisions` → latest `changes` contains `{kind:"response_field_removed", breaking:true}`; `GET …/spec/diff?from={prev}&to={latest}` → same change.
12. `GET /api/apps/{A}/api-usage` → `getOrdersById` row `status:"changed"` (G17 defines "changed").
13. `POST /api/projects/{P}/apps/{A}/preview-token` → `201` `environment:"dev"` → `PT`; `raw GET /api/p/{S}/orders/1` `Bearer {PT}` → `200`.
14. **[LLM]** `POST /api/ai/app-builder/chat` `{message:"Build a UI for getOrdersById", app_id:A, project_id:P, route_ids:[R], api_execution_ids:[], conversation_history:[]}` → status ≠ `422`.
15. **[esbuild]** `POST /api/apps/{A}/publish` `{slug:uniq("cl"), access:"public"}` → `200`; the published bundle contains the `@generated` client header with the current `spec_hash` (backend-side assertion on the stored bundle).

#### J15 — Data tables + DataTable node [7]

1. `POST /api/projects` → `P`, `S`; `POST /api/projects/{P}/tables` `{name:"orders", columns:[{name:"email",type:"string",required:true},{name:"total",type:"number"},{name:"status",type:"string",default:"open"}]}` → `201` `{id:T, name:"orders", columns: 3 items}`.
2. Same name → `409` `detail.code:"name_taken"`; `{name:"bad", columns:[{name:"x",type:"float"}]}` → `422` `detail.code:"invalid_column"`.
3. `GET /api/projects/{P}/tables` → contains `T`; `GET …/tables/{T}` → `200` `columns[2].default:"open"`.
4. `POST …/tables/{T}/rows` `{data:{email:"a@x.io", total:10}}` → `201` `{id:R1, data:{email:"a@x.io", total:10, status:"open"}}`.
5. `POST …/rows` `{rows:[{email:"b@x.io",total:3},{email:"c@x.io",total:7.5,status:"paid"},{email:"d@x.io",total:1}]}` → `201` `inserted:3`.
6. `POST …/rows` `{data:{total:"x"}}` → `422` `detail.code:"row_invalid"`, `detail.errors` contains `{row:0, column:"email"}` and `{row:0, column:"total"}`.
7. `GET …/rows?limit=2&sort=-created_at` → `200` 2 rows, `next_cursor` non-null; `GET …/rows?limit=2&sort=-created_at&cursor={c}` → 2 rows, `next_cursor:null`.
8. `GET …/rows?filter=` + urlencode(`{"total":{"$gt":5}}`) → emails exactly `{a@x.io, c@x.io}`; `{"$or":[{"status":"paid"},{"email":{"$contains":"b@"}}]}` → `{b@x.io, c@x.io}`; `{"status":{"$in":["open"]}}` → 3 rows; `{"nope":1}` → `422` `detail.code:"unknown_column"`.
9. `GET …/rows/{R1}` → `200`; `PATCH …/rows/{R1}` `{data:{status:"paid"}}` → `200` `data.total:10, data.status:"paid"`; `PUT …/rows/{R1}` `{data:{email:"a@x.io", total:11}}` → `200` `data.total:11`; `DELETE …/rows/{R1}` → `200`; `GET …/rows/{R1}` → `404`.
10. `PUT …/tables/{T}` `{columns:[{name:"email",type:"string",required:true},{name:"total",type:"integer"},{name:"status",type:"string",default:"open"}]}` → `409` `detail.code:"rows_incompatible"`, `detail.count:1` (the 7.5 row).
11. Isolation: `POST /api/projects` → `Q`; `GET /api/projects/{Q}/tables/{T}` → `404`.
12. DataTable node: `POST /api/projects/{P}/workflows` `{name:"list", nodes:[Start, {name:"Orders", type:"DataTable", parameters:{table:"orders", operation:"list", limit:10}, position:{x:200,y:0}}], connections:[Start→Orders], settings:{}}` → `WT`; `POST …/routes` `{method:"GET", path:"/orders", response_mode:"all_items", handler:{type:"workflow", workflow_id:WT}}` → `201`; `raw GET /api/p/{S}/orders` → `200` array of 3, each item has keys `id`, `email`.
13. `DELETE /api/projects/{P}/tables/{T}` → `200`; `GET …/tables` → no `T`.

#### J16 — Python modules + sandbox trace [8; bwrap-gated]

1. `GET /api/sandbox/status` → `200` `{available:true, backend:"bwrap"}`.
2. `POST /api/projects` → `P`, `S`; `POST /api/projects/{P}/modules` `{name:"orders", source:SRC1}` with
   ```python
   from studio import api, Response, HTTPError
   import studio

   @api.get("/py/orders/{id}", auth="none", name="getPyOrder")
   def get_order(req):
       if req.params["id"] == "404":
           raise HTTPError(404, {"error": "not found"})
       return {"id": req.params["id"], "via": "python"}

   @api.post("/py/orders", auth="none", name="createPyOrder")
   def create_order(req):
       key = studio.secret("PYKEY")
       return Response(status=201, body={"ok": True})
   ```
   → `201` `{module.id: startswith "mod_", routes.created: 2 items, routes.updated:[], routes.removed:[], errors:[]}` → `M`.
3. `GET /api/projects/{P}/modules` → contains `{id:M, name:"orders"}`; `GET …/modules/{M}` → `source == SRC1`.
4. `GET …/routes` → contains `{name:"getPyOrder", source:"code", handler:{type:"python", module_id:M, function:"get_order"}}`.
5. `raw GET /api/p/{S}/py/orders/7` → `200` `{id:"7", via:"python"}`, header `X-Execution-Id` → `XP`; `raw GET /api/p/{S}/py/orders/404` → `404` `{error:"not found"}`; `raw POST /api/p/{S}/py/orders` → `201` `{ok:true}`.
6. `GET /api/projects/{P}/runs/{XP}` → `trace.sandbox.exit_code:0`, `trace.sandbox.truncated:false`, `trace.broker_calls` is a list.
7. `GET /api/projects/{P}/secrets/missing` → contains `{key:"PYKEY", referenced_by:[{kind:"module", id:M, name:"orders"}]}`.
8. `PUT …/modules/{M}` `{source: SRC1 + "\ndef broken(:\n"}` → `422` `detail.code:"syntax_error"`, `detail.errors[0].line` int; `GET …/modules/{M}` → `source == SRC1`.
9. `PUT …/modules/{M}` `{source: SRC1 without the @api.post block}` → `200` `routes.removed` = 1 item `{name:"createPyOrder"}`; `raw POST /api/p/{S}/py/orders` → `404` or `405`.
10. `POST …/routes` `{method:"GET", path:"/ui/{id}", handler:{type:"stub"}}` → `201`; `PUT …/modules/{M}` with an added `@api.get("/ui/{x}")` decorator → `409` `detail.code:"route_conflict"`; module unchanged.
11. `POST /api/projects/{P}/routes/{getPyOrder id}/test` `{params:{id:"9"}, query:{}, headers:{}, body:null, environment:"dev", apply_schemas:false}` → `200` `status:200`, `body.via:"python"`.
12. `DELETE /api/projects/{P}/modules/{M}` → `200`; `GET …/routes` has no `source:"code"` routes (G32).

#### J17 — Release gates, explicit rollback, snapshot detail [9]

1. `POST /api/projects` → `P`, `S`; `POST …/routes` `{method:"GET", path:"/a", handler:{type:"workflow", create:true}}` → `RA`.
2. `POST /api/projects/{P}/apps` → `A`; save version with `App.tsx` calling `api.getA()` and `fetch('/api/p/<S>/missing')`.
3. `POST /api/projects/{P}/releases` `{environment:"dev"}` → `422` `detail.code:"checks_failed"`; `detail.checks` contains `{id:"app_calls_resolve", status:"fail"}`.
4. `GET …/releases?environment=dev` → contains `{status:"blocked", is_active:false}`; none active.
5. Fix with a stub: `POST …/routes/stubs` `{app_id:A, routes:[{method:"GET", path:"/missing"}]}` → `201`.
6. `POST …/releases` `{environment:"dev", notes:"with stub"}` → `201` `is_active:true`; `checks` contains `{id:"stub_routes", status:"warn", details.routes:["GET /missing"]}` and `{id:"app_calls_resolve", status:"pass"}` → `D1`.
7. `GET …/releases/{D1}` → `snapshot.routes` 2 items; `snapshot.apps` contains `{app_id:A, version_id:int}`; `snapshot.workflows` contains one item with `version_id:int`.
8. `POST …/releases/{D1}/promote` `{to_environment:"prod"}` → `201` → `P1`.
9. Breaking change: `PUT …/routes/{RA}` `{auth:"api_key"}` → `200` `breaking_changes` contains `{breaking:true}`.
10. `POST …/releases` `{environment:"dev"}` → `201` → `D2` (checks may warn); `POST …/releases/{D2}/promote` `{to_environment:"prod"}` → `201` `checks` contains `{id:"breaking_vs_active", status:"warn"}` → `P2`. (If `app_calls_resolve` treats the auth change as failing, expect `422` instead — G28 must settle which.)
11. `raw GET /api/p/{S}/a` → `401` (prod now requires a key).
12. `GET …/releases/{P2}/diff?against={P1}` → `200` `routes` contains `{kind:"auth_changed"…, breaking:true}` (kind name per G13/G28).
13. `POST /api/projects/{P}/environments/prod/rollback` `{release_id:P1}` → `200` `{id:P1, is_active:true}`; `checks` present (warnings only).
14. `raw GET /api/p/{S}/a` → `200`.
15. `GET …/releases?environment=prod` → exactly one active (`P1`); `P2` has `status:"rolled_back"`.

### Endpoint coverage

| Endpoint (§) | Journeys |
|---|---|
| `GET /api/projects` (3.1) | J1.1, J6.11 |
| `POST /api/projects` | J1.2, J6.1-3 |
| `GET /api/projects/{project}` (id + slug) | J1.3-4, J2.5, J6.4, J7.15 |
| `PUT /api/projects/{project}` | J7.2-8 |
| `DELETE /api/projects/{project}` | J7.13-15 |
| `GET …/summary` | J1.5, J1.48, J6.10, J8.14 |
| `GET/POST …/workflows` | J1.9, J6.5-6, J7.9, J11.9 |
| `GET/POST …/apps` | J1.21, J6.7 |
| `GET …/runs` | J1.49, J2.12, J9.14, J11.14 |
| `GET …/runs/{id}` | J2.13, J6.12, J11.14, J12.6, J16.6 |
| `GET /api/workflows?project_id=`, `GET /api/apps?project_id=` | J2.4, J3.2, J6.6-7 |
| `PUT /api/workflows/{id}` `project_id` (move, 409) | J7.10-11 |
| `PUT /api/apps/{id}` `project_id` | J7.12 |
| run/stream `project_id` | J2.8-9 |
| `GET/POST …/secrets`, `PUT/DELETE …/secrets/{id}`, `GET …/secrets/missing` (3.2) | J1.6-7, J1.32, J7.5-7, J8 (all), J10.8-10, J16.7 |
| `POST /a/{slug}/api/workflow/{id}/run` (3.3) | J9.8-13 |
| `GET /api/sdk/functions`, `/api/sdk/python`, `/api/sdk/python.zip` (3.4) | J10.1-3 |
| `POST …/broker-tokens` | J10.4-5 |
| `POST /api/broker/call` | J10.6-15 |
| `GET …/broker-calls` | J10.16 |
| `GET/POST …/routes`, `GET/PUT/DELETE …/routes/{rid}` incl. `dry_run` (3.5) | J1.8, J1.14-16, J1.27, J11 (all), J13, J14.9-10 |
| `POST …/routes/{rid}/test` | J1.13-15, J1.37, J11.15, J13.6, J16.11 |
| `GET …/openapi.json` | J1.19 |
| `GET …/spec/revisions`, `GET …/spec/diff` | J1.17-18, J14.11 |
| `GET/POST/DELETE …/api-keys[/{id}]` | J12.4-5, J12.11-12 |
| `ANY /api/p/{project}/{path}` | J1.11, J1.23, J1.34-45, J11, J12, J13, J14, J15.12, J16, J17 |
| `GET …/client.ts` (3.6) | J1.20, J12.10, J14.7 |
| `POST …/apps/{app}/preview-token` | J1.22, J12.9, J14.13 |
| `GET /api/apps/{id}/api-usage` | J1.25, J14.3, J14.6, J14.12 |
| `GET …/routes/{rid}/used-by` | J1.26 |
| `POST …/routes/stubs` | J14.4-5, J17.5 |
| `POST /api/ai/app-builder/chat` (changed) | J5.6-7, J14.14 |
| tables, table, rows, row (3.7) | J15 |
| modules, module, `GET /api/sandbox/status` (3.8) | J16 |
| releases list/create/get/promote/rollback/diff (3.9) | J1.29-47, J17 |
| Existing §1 rows: workflows CRUD/publish/unpublish/run/run-adhoc, both SSE, apps CRUD/publish/unpublish/versions (list/get/create/revert/label), executions, nodes (+`/nodes/{type}`), api-tester (5 calls), ai chat, app-builder chat, variables (5 calls) | J2, J3, J4, J5 |
| `/api/files/browse` | not covered (existing bug, out of scope per §1) |

---

## 7. Contract gaps (with proposed additions)

| # | Gap | Proposal |
|---|---|---|
| G1 | Error envelopes differ: management `{"detail":{"code","message"}}` (§3 conventions), but §3.7 `row_invalid`, §3.8 `syntax_error`, §3.9 `checks_failed` are written as top-level `{"code":…}`; router `{"error":…}`; broker `{"ok":false,"error":{…}}`; existing string `detail`; FastAPI 422 `detail:[…]`. §3.3's `403 workflow_not_in_project` and §3.4's broker-token `403` don't say which shape. | State that **all** `/api/projects/…`, `/api/apps/…`, `/a/{slug}/api/…` errors are `{"detail":{"code","message",…extra}}` (so `detail.errors`, `detail.checks`, `detail.count`, `detail.release_id`), and that router/broker keep their documented runtime shapes. The studio parser (§3.1) handles all five regardless. |
| G2 | `GET …/secrets` and `…/secrets/missing` take `?environment=`; the Secrets matrix needs all environments in one call. | Make `environment` optional: omitted → all project environments, each row carries `environment`. |
| G3 | `GET …/runs` has `limit` but no cursor, and rows carry no route/app identity. Route-handler runs show only `workflow_name`; python-handler runs have no workflow at all. | Add `before=<execution_id>` → response stays a list, plus `X-Next-Cursor` header (or switch to `{runs, next_cursor}` for the new endpoint only). Add filters `workflow_id`, `route_id`, `environment`. Add fields `route_id`, `route` (`"GET /orders/{id}"`), `app_id`, `duration_ms`; enumerate `mode`/`trigger` values (`manual`, `stream`, `api`, `public_app`, `webhook`, `cron`). Allow `workflow_id: null` for python routes (G30). |
| G4 | `summary.missing_secrets` type is unspecified; the header needs stub-route and active-release info. | `missing_secrets: number` (distinct keys), `counts.stub_routes`, `counts.api_keys`, `active_releases: {<env>: {release_id, number, created_at} \| null}`. |
| G5 | `AppListItem` is `{id,name,created_at,updated_at}` (`engine:schemas/app.py:83-89`) and `WorkflowListItem` has no route link; list pages would need N+1 `GET`s for publish state and "serves GET /x" badges. | Additive: `AppListItem += {project_id, slug, active, published_at, current_version_number}`; `WorkflowListItem += {project_id, description, handler_for:[{route_id, method, path}]}`. |
| G6 | After `handler:{type:"workflow",create:true}` the echoed `Route.handler` shape isn't stated; `spec_revision` type isn't stated. | `Route.handler` is always the resolved form `{type:"workflow", workflow_id}`; `spec_revision: int`; add `spec_hash` to `RouteWriteResult`/`RouteDeleteResult` (client cache key). |
| G7 | `/test` with `apply_schemas:true` saves "through the normal PUT path" but returns only `applied:true`. | Also return `route`, `spec_revision`, `breaking_changes` when applied. |
| G8 | `/test` body has `environment`, but the text says it "runs the handler in default_environment"; behaviour for a release env is undefined. Handler failure (502/504/501) during `/test` isn't specified as HTTP status vs data. | `environment` must be a draft env else `422 environment_not_draft`; default = `default_environment`. `/test` always returns HTTP `200` with `status` = the router status and `body` = the router body (incl. `handler_failed`), so the Test tab renders failures uniformly. |
| G9 | `DELETE …/routes/{rid}` has no dry run, so the UI can't preview the breaking changes it returns. | `DELETE …?dry_run=true` → `200 {spec_revision:null, breaking_changes}` without deleting. |
| G10 | Release creation activates immediately; there's no way to show checks/diff before committing (the "release safety" UX). | `POST …/releases?dry_run=true` and `POST …/releases/{rid}/promote?dry_run=true` → `200 {checks, diff}` with nothing stored. |
| G11 | `422 checks_failed` stores a `blocked` release but the response doesn't return its id; numbering for blocked releases is unstated. | Include `detail.release_id` and `detail.number`; state that blocked releases consume a number (so J1 step 33's prod release is #2). |
| G12 | Rollback: reactivate the target row or create a new row with `source_release_id`? Which status does the previously active release get? Where are the warn-only checks returned? Behaviour with no previous release? | Reactivate the target row (`is_active:true, status:"active"`); previous active → `rolled_back`; response = target `Release` whose `checks` are the re-run results; no previous release → `409 no_previous_release`. |
| G13 | `diff?against=` only shows `active`; rollback preview needs "this release vs that env's active", promote preview needs "dev release vs prod active". Change `kind` values beyond `response_field_removed` are not enumerated. | `against = active \| <release_id>`, plus optional `environment=` (default: the release's env). Enumerate `Change.kind`: `route_removed, route_added, method_changed, path_changed, auth_changed, request_required_added, request_field_type_changed, response_field_type_changed, response_field_removed, field_added`. |
| G14 | `GET …/releases` returns "Release without the snapshot body" but `Release` fields are only implied by M6. | Document `Release = {id, project_id, environment, number, is_active, status, checks, source_release_id, notes, created_by, created_at}` (+`snapshot` on detail). |
| G15 | Response shapes not defined: `Table`, `Row` PATCH/PUT bodies, `CodeModule` list item, `ApiKey` list item, DELETE responses for api-keys/tables/rows/modules, `BrokerCall` row. | Use the types in §3.2 (`Table`, `CodeModule`, `ApiKey`, `BrokerCall` mirror their migrations); PATCH/PUT rows take `{data}`; every DELETE returns `200 {"success":true}`. |
| G16 | Rows list has no total and tables list has no row counts. | `GET …/rows?count=true` adds `"count": n`; `Table += row_count`. |
| G17 | `api-usage` rows for `api.<op>(` calls whose op doesn't exist can't know `method`/`path`; `status:"changed"` is undefined. | `method`/`path` nullable with `kind:"client_call"\|"literal"`; define `changed` = the route had a breaking `Change` affecting this call since the scanned app version was created. Optional: `POST /api/apps/{id}/api-usage {files}` to scan unsaved files. |
| G18 | Where the generated client lives relative to the entry file, and what happens to an `api/client.ts` present in saved files. | Fixed path `api/client.ts` at the files root; entries import `./api/client` (root `App.tsx`) or `../api/client` (`src/App.tsx`); backend ignores/overwrites any saved `api/client.ts` at publish and in the AI working set; the AI system prompt states the import path. |
| G19 | The §3.6 client sample calls `qs()` but never defines it; there's no cache validator. | Emit `qs()`; put `spec_revision` + `spec_hash` in the header comment (J14.15 relies on it); send `ETag: "<spec_hash>"` on `client.ts`/`openapi.json`. |
| G20 | Preview has no equivalent of the published `/api/workflow/{id}/run`; preview 404s where published works (§4.8). | `POST /api/apps/{app_id}/workflow/{workflow_id}/run` accepting a preview token, same body/response/allow-list as §3.3, environment = `default_environment`. The preview bridge maps `/api/workflow/{id}/run` there. |
| G21 | Preview/app-token expiry is indistinguishable from "no credential" (`401 {"error":"unauthorized"}`). Preview tokens for apps with no published version aren't explicitly allowed. | `401 {"error":"token_expired"}` for expired tokens (bridge refreshes only then); state that preview tokens don't need a published version. |
| G22 | `PUT /api/apps/{id} {workflow_ids}` accepts workflows from other projects; the failure only shows at run time (`403 workflow_not_in_project`). | Validate on write: `422 {"detail":{"code":"workflow_not_in_project","workflow_ids":[…]}}` (and on project move, drop or reject). J9.11 then moves to the write step. |
| G23 | Studio runs (`/execution-stream/*`, `run-adhoc`) accept any `environment` string; in a project an unknown env silently resolves no `$vars`. | For non-default projects, `422 unknown_environment` when `environment ∉ project.environments`. |
| G24 | Broker failures inside canvas runs reach the UI only as `node:error` message strings, so "create missing secret" from a run needs string matching. | `node:error` SSE events and `ExecutionDetailResponse.errors[]` gain optional `error_code` and `details` (e.g. `{"key":"STRIPE_KEY","environment":"dev"}`). |
| G25 | Workflow AI chat (`/api/ai/chat`) has no project context, so it can't suggest project secrets/tables/routes. | Optional `project_id` in the body (additive). Not needed until phase 10. |
| G26 | Changing a project slug silently breaks every `/api/p/{slug}` literal, published bundle and generated client BASE. | `PUT /api/projects/{p}?dry_run=true` returns affected apps; or keep `previous_slugs` and have the router 308-redirect old slugs. |
| G27 | After a release is active in the live env, direct `POST /api/apps/{id}/publish` still changes `published_version_id`, and the next promote overwrites it. | Decide: either refuse direct publish in projects with an active live release (`409 managed_by_release`), or document that direct publish is a hotfix the next promote replaces. The UI shows a note either way (§2.5). |
| G28 | Moving a route's `auth` stricter is listed as breaking, but which release check fails or warns on it is unclear (J17.10). | State: `breaking_vs_active` warns; `app_calls_resolve` fails only for missing operations/paths, not for auth changes. |
| G29 | Team-variable PUT requires `description` (`engine:schemas/variable.py:17`), so the modal always sends `null` (`useVariablesApi.ts:84-88`), which clears descriptions; the Secrets page editing team rows inherits that. | Make `VariableUpdateRequest.description` optional and apply with `exclude_unset` (backward compatible: sending `null` still clears). |
| G30 | `ExecutionListItem.workflow_id`/`workflow_name` are required strings (`engine:schemas/execution.py:43-45`); python-handler and broker-only runs have no workflow. | Allow `null` (new fields additive) and add `route`/`route_id` (G3). |
| G31 | §0/§7.1 say the studio sends `Content-Type` on every call; `useVariablesApi.ts:44,54,101` and `editor.tsx:134` don't. | Harness `StudioClient.raw` already exists; use it for those call sites (the J4 GETs). After this UI work all calls go through `request()` and the claim becomes true. |
| G32 | Module `DELETE` doesn't say it removes the module's `source:"code"` routes or returns breaking changes; module saves have no dry run. | `DELETE …/modules/{mid}` → `200 {routes:{removed}, spec_revision, breaking_changes}`; `PUT …/modules/{mid}?dry_run=true` → same shape without saving. |
| G33 | `GET …/broker-calls` has only `execution_id`; the Developer page wants the project log. | Add `limit`, `before`, `status`; without `execution_id` return the latest 100 for the project. |
| G34 | OpenAPI `servers` is unspecified, so downloaded specs don't know the base path. | `servers:[{"url":"/api/p/{slug}"}]`; paths are route paths. |


---

## 8. UI build order (vertical slices)

Each slice ships behind no flag, keeps every §5 flow working, and is testable with the journeys named.

| # | Slice | Contents | Journeys |
|---|---|---|---|
| 1 | HTTP core | `shared/api/http.ts` (`ApiError`), `apiFetch` delegating, `queryKeys.ts`, hoisted `QueryClient` + router context, `retry` rule | J2–J5 still green |
| 2 | Projects home + shell skeleton | `/projects` grid, `NewProjectDialog`, `/p/$projectId` shell with tabs (empty pages), project env store, root full-screen match | J6 |
| 3 | Editor and builder inside the shell | move `EditorPage`, `AppBuilderShell` under `/p/…`; `/editor`, `/builder` redirects; link changes (§1.4); Automations and Apps lists; project-aware create/import/delete; env + `project_id` on runs (fixes F8) | J2, J3, J6 |
| 4 | Settings + Overview | general/environments/egress/danger sections, summary tiles, recent runs | J7 |
| 5 | Secrets | Secrets page, `CreateSecretInline`, missing card, expression-editor chip, team rows via existing modal | J8, J4 |
| 6 | Runs | Runs list + detail (reuse execution-trace components) | J2.12-13, J11.14 |
| 7 | App settings: allowed workflows | `workflow_ids` picker; public-run console logging in preview | J9 |
| 8 | API routes core | routes list, New route dialog, Route detail Handler + Test tabs, router URL/curl copy | J1.8-16, J11 |
| 9 | Schemas, spec, keys | Schema tab + breaking-change dialog, Spec view (OpenAPI, revisions, diff), API keys page | J12, J13, J1.17-19 |
| 10 | App builder ↔ API | `client.ts` merge into preview, preview token + bridge changes (§4.7), API panel, api-usage + missing-endpoints banner + stubs, used-by tab, chat body `project_id`/`route_ids`, `declare_endpoint` invalidation | J1.20-27, J14 |
| 11 | Developer tools | functions catalogue, SDK download, dev broker token, broker-calls on run detail | J10 |
| 12 | Data | tables list/detail, filter builder, cursor paging, row edit (optimistic PATCH) | J15 |
| 13 | Python modules | `@codemirror/lang-python`, module editor, sandbox banner, route sync display, run trace panes | J16 |
| 14 | Releases | Release button + dialog, checks list with fixes, promote, releases page, diff, rollback | J1.29-47, J17 |
