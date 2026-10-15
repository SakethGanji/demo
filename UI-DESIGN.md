# UI-DESIGN — the luna studio, rebuilt around the project (v2)

Written 2026-10-15 against branch `revert-to-5684ba5` (engine phases 1–9 built). **v2 resolves every
finding in `UI-DESIGN-REVIEW.md`; the "Review resolution" table is in §14.**
Design only: no app code was changed. The implementer works from this document plus the mockups.

- Mockup sources: `design-mockups/*.html`. Open them directly in a browser; they use the studio's
  real tokens. One dataset (`assets/shell.js`) drives every screen's top bar and outline.
- Screenshots: `screenshots/ui-design/`. 25 PNGs are listed in Appendix A, covering 17 screens plus dark mode and the 1280/1024 widths.
- This document **supersedes the structure** of `UI-INTEGRATION.md`. That file's types, query-key and
  invalidation *designs* (§3) and its automatic behaviours (§4) are reused, but **none of that code exists
  yet**. It is scoped as slice 0 (§13, G1).
- Paths: `studio:` = `apps/workflow-studio/src/`, `engine:` = `apps/workflow-engine/src/`.
- "**Contract**" in this document means behaviour the UI relies on. Where the review found the engine
  wrong, §9 states the **corrected** behaviour. A separate backend agent is implementing those fixes now.
  The UI is built against the corrected contract and does **not** work around the old bugs.

---

## 0. Decisions at a glance

| # | Decision | Why |
|---|---|---|
| D1 | **4 pages**: Projects home → Project workspace → Workflows (canvas) → App builder. Everything else is a *view* inside the workspace, a sheet, or a dialog. | Owner: few pages, the project page is where you work. |
| D2 | The workspace is an **IDE-style layout**: a persistent left outline (Backend modules and routes, Data, Apps, Secrets, Runs) and a main area that swaps views. Views have URLs, and the shell never unmounts. | One mental model; no nav sprawl. |
| D3 | **Python is the primary backend.** Generate-with-AI and the Backend view are the headline. Workflows stay first-class but secondary. | Owner: "creating workflows is not ideal". |
| D4 | Visual direction **"floating workbench"**: the studio's light canvas, white panels with hairline borders, Inter 13px, primary blue used sparingly, method colours, and **violet only for AI**. | It is what the studio already looks like, sharpened. |
| D5 | **Light and dark both ship** (existing `ThemeProvider`). Every new token has a `.dark` value and passes the contrast test (§7.3). | Theme support already exists. |
| D6 | **Backend → UI** has two entry points: the *Create UI from backend* dialog on the project page, and the **route picker in the builder** for apps that exist already. Both write `route_ids` that every chat turn sends. | Owner: "select that backend and create a UI from it". |
| D7 | Unsaved code is testable (`run-draft`). Saving creates routes **and the tables the code needs**. | Generate → test → save, without polluting the spec. |
| D8 | AI generation is a new streaming endpoint that **never saves**. All writes go through the modules API. | One write path for spec revisions and breaking changes. |
| D9 | The **fake LLM** works only in an explicit test process: test flag + test DB name + loopback + a token, read from `os.environ` only. It hooks `_call_llm_once`, so the real prompt assembly and tool loops run. | Tests can drive the whole flow, and the fake can never be switched on in a real deployment. |
| D10 | URLs use the project **slug**. Query keys use the resolved project **id**. | Links stay readable, and the cache stays stable across a rename. |
| D11 | **Data is project-wide, not per environment.** The UI says so wherever writes happen (Test panel, Create-UI footer, Data header, Publish pre-flight). Environments separate secrets and releases only. | Matches the engine (`data_tables` has no environment column). |
| D12 | **"Missing" has one meaning:** a secret that code references and that is not set in an environment (amber). A secret that is merely absent where nothing uses it reads "not set" (grey). | Removes the two meanings of "missing" (review B10). |

---

## 1. Design evaluation

### 1.1 What the studio looks like today

| Screen | Observations |
|---|---|
| `/` landing | A dark marketing hero. It stays as is; only its CTAs change (§2.3). |
| `/projects` | A light `bg-muted` canvas with the floating h-11 navbar (`bg-card/80 backdrop-blur-xl rounded-xl shadow-lg border-border/30`, `projects.tsx:73`), the "luna" pill, and a grid of workflow cards. It is item-centric: there is no project, backend or health. |
| `/editor` | Floating bar, dotted React Flow canvas, right node panel. The floating-panel language is worth keeping. |
| `/builder` | Preview and chat cards with empty states. Nothing about the API. |

Tokens (`studio:index.css`): neutral greys, primary `#18a0fb`, semantic success/warning/destructive,
`--chart-2 #7b61ff`, radius 0.5rem, a soft shadow scale, a 13px base, Inter plus JetBrains Mono, and a full `.dark` palette.

**Primitives that already exist (reuse them, don't rebuild):** Button, Badge, Input, Textarea, InputGroup,
ButtonGroup, Select, DropdownMenu, Dialog, AlertDialog, Sheet, Tooltip, HoverCard, Command (cmdk),
ScrollArea, Collapsible, EditorTabs, Toolbar, Spinner, Skeleton, JsonViewer, CodeEditor (json/js);
`PillTabs` (segmented look, `features/app-builder/components/ApiTesterPanel.tsx:924`);
`ConversationEmptyState` (`shared/components/ai-elements/conversation.tsx:37-50`). Base UI 1.3.0 also ships
**tabs, toggle-group, checkbox and switch** primitives that are not wrapped yet (§7.1).

**Console noise today** (all must be fixed in slice 0, because E2E fails on any console warning, §12.6).
Every instance is `<button>` inside `<button>`:
`features/workflow-editor/components/workflow-navbar/WorkflowNavbar.tsx:263,277` (`<TooltipTrigger><button/>`
→ `<TooltipTrigger render={<button/>}>`), `features/projects/components/AppListRow.tsx:55-56`,
`AppCard.tsx:53-54`, `WorkflowCard.tsx:66-67`, and `WorkflowListRow.tsx:94`. Slice 0 also audits the remaining
`TooltipTrigger`/`DropdownMenuTrigger` usages with a grep for `Trigger>\s*<(button|Button)`.

### 1.2 References

| Reference | Taken | Rejected |
|---|---|---|
| Vercel | Overview with health, base URL plus copy, recent activity, one primary CTA | A page per concern |
| Supabase | One project shell; table editor next to auto-generated API docs | A deep 15-item icon rail |
| Linear | Density, quiet chrome, ⌘K, restrained colour | – |
| Windmill | Script-first; inferred I/O schema; test run beside the editor | Auto-generated forms as the only UI |
| Retool | An explicit resource → UI binding (the builder's Backend panel) | Drag-and-drop canvas |
| Cursor / v0 | A prompt composer above the code; drafts you accept, discard or refine | Chat as the only editing surface |

### 1.3 Direction "floating workbench"

- **Canvas and panels**: `--canvas` sits behind everything. Panels are white, radius 12px, with a 1px
  `--panel-border` and `--shadow-xs`. Only the top bar floats (`--shadow-lg`, blur). Panels sit 12px apart and 12px from the window edge.
- **Type scale**: titles 22/28 semibold (−0.015em); section titles 13 semibold; eyebrows 11px uppercase
  +0.06em; body 13/20; meta 12 or 11.5 muted; code/paths JetBrains Mono 12–12.5. **One unit format**:
  `331 ms`, `2m ago`. **One time window**: "· 24h".
- **Colour budget (review H4)**: primary blue = the one primary action per region, plus selection.
  Methods: GET green, POST blue, PUT amber-brown, **PATCH teal**, DELETE red. **Violet = AI only.**
  Python keywords use **magenta** (`--syn-keyword`), not violet. A stub is a **dashed amber outline pill**, so
  it never looks like a PUT badge. Status dots use semantic colours.
- **Contrast**: every text/background pair in §7.3 is ≥ 4.5:1 (body and badges) or ≥ 3:1 (gutter numbers and
  large text). A unit test enforces it.
- **Motion**: 150 ms colour and opacity. New outline rows use `animate-badge-pop`.

---

## 2. Information architecture

### 2.1 Pages (4)

| Page | URL | What it is |
|---|---|---|
| Projects home (`home.png`) | `/projects` | Project cards (health, base URL, first 4 routes, counts, ⋯ menu with Settings/Delete), "New project". |
| **Project workspace** | `/p/$project/…` | Outline + views: Overview, Backend, Data, Secrets, Runs. Settings, Release and API keys open as sheets; dialogs for the rest. |
| Workflows (`workflows.png`) | `/p/$project/workflows`, `/p/$project/workflows/$workflowId` | A list (§6.6) + today's canvas editor, unchanged inside. |
| App builder (`builder.png`) | `/p/$project/apps/$appId` | Builder + Backend panel + route picker + preview-token bridge + publish. |

`/` (landing): the CTAs point to `/projects`, and "Create New Flow" points to `/p/default/workflows/new`.

### 2.2 Workspace views (child routes of one layout route; the shell never unmounts)

| View | URL | Search params |
|---|---|---|
| Overview / Get started | `/p/$project` | `setup=1` forces the guide |
| Backend module | `/p/$project/backend/$module` (module **name**; `~new` = unsaved new module) | `route=<operation>`, `line=<n>`, `prompt=<text>`, `generate=1` |
| Data | `/p/$project/data/$table` | `filter` (JSON), `sort` (default `-created_at`) |
| Secrets | `/p/$project/secrets` | `create=<KEY>&env=<env>` |
| Runs | `/p/$project/runs`, `/p/$project/runs/$executionId` (Sheet over the list) | `trigger`, `status`, `route`, `env` |
| Sheets | any view | `sheet=settings&section=general\|environments\|egress\|keys\|developer\|danger`, `sheet=release` |
| Dialogs | any view | `dialog=create-ui\|new-module\|new-table` |

The module-name grammar is `^[a-z_][a-z0-9_]*$`. The names `new`, `generate` and `default` are reserved:
the New-module dialog rejects them, and the generate analysis renames them (review B9). `~new` cannot collide
because `~` is not a legal identifier character.

Route files (code-based routing): `app/routes/project.tsx` (layout), `project.index.tsx`,
`project.backend.tsx`, `project.data.tsx`, `project.secrets.tsx`, `project.runs.tsx`,
`project.workflows.tsx`, `project.workflow.tsx`, `project.app.tsx`. `__root.tsx` adds `/p` to the full-screen match.

### 2.3 Old URL redirects (`beforeLoad`, using the hoisted `queryClient` from slice 0)

| Old URL | Goes to |
|---|---|
| `/editor` | `/p/default/workflows/new` |
| `/editor?workflowId=W` | `GET /api/workflows/W` → `project_id` → `GET /api/projects/{id}` → `/p/{slug}/workflows/W`. A 404 goes to `/projects` with the toast "Workflow not found". |
| `/builder` | `/p/default?dialog=create-ui` (shows the blank-app variant; Default has no routes) |
| `/builder?appId=A` | `GET /api/apps/A` → `/p/{slug}/apps/A`. A 404 goes to `/projects`. |
| `/p/{proj_id}` | `replace` with `/p/{slug}` |
| `/p/{unknown}/…` | Full-page "Project not found" (`data-testid=project-not-found`) with "Back to projects". A slug renamed in **this** session is followed automatically, because the rename navigates with `replace`. |
| `/p/$project/apps` | `/p/$project` |

### 2.4 Top bar (`ProjectTopBar`)

`[luna] / [● Project ⇕] [Project | Workflows n] … [Search ⌘K] | [● dev | ● prod] [Release (prod #N)] [☀]`

- **Project switcher**: a Command popover with health dots, plus "New project".
- **⌘K** (`command-palette.png`): groups for Routes, Modules, Tables, Apps, Workflows, Runs, Secrets and Actions.
- **Environment switch = *viewing* environment only.** It sets the Runs filter, the Secrets matrix
  highlight, the client.ts/OpenAPI tab environment, and the Release sheet default. In the Workflows editor
  it also sets the run environment (§6.6). **It never changes where route tests, draft tests, the
  self-test or the builder preview run.** Those always run in `project.default_environment` (dev), and each
  says so. The dot after each environment name is green when healthy and **amber when code references a
  secret that is missing there**.
- **Data is shared** across environments (D11). The switch tooltip says: "Viewing dev. Tables are
  shared by all environments."

---

## 3. Project workspace

### 3.1 Outline (`project.png`)

Overview · BACKEND (+) modules with nested routes (method badge + path; while viewing a module all its
routes show; a draft module shows `draft` and its routes show violet `new`) · Generate with AI · DATA (+)
tables with row counts (a table that the draft will create shows violet "created on save") · APPS (+)
(↗ opens the builder) · PROJECT: Secrets (amber dot + "n missing", where n = `summary.missing_secrets` **plus** keys referenced only by open unsaved drafts that are missing in some environment; the drafts are counted by the client scan of §4.5),
Runs ("12 · 24h") · footer: Settings, API keys.

Data sources: `GET …/summary` (30 s poll), `…/modules` (the summary does not count modules, review C17),
`…/routes`, `…/tables`, `…/apps`, `…/secrets/missing`.
At 1024–1279 px the outline becomes a **rail of view icons only** (Overview, Backend, Data, Apps, Secrets,
Runs, Settings). Backend and Data open hover flyouts with module or table names (review H5).
Shortcuts: `g o/b/d/s/r/w`; `[` toggles the outline.

### 3.2 Overview (`project.png`)

1. **Hero**: name and health badge; a copyable `/api/p/{slug}` pill; meta ("Python backend · 2 modules · 5 routes ·
   prod serves the latest saved code (no release yet)"). Actions: `Generate` (outline) and the **one**
   primary button, *Create UI from backend*.
   **Health (`summary.health`, contract; re-review B10):** `failing` if > 50% of the last-24h runs failed;
   else `degraded` if > 5% failed **or** any secret referenced by saved code is missing in `live_environment`;
   else `ok`; `unknown` when there are no runs and nothing is missing. The badge reads "Degraded · secret missing in
   prod" in the secret case. The top-bar project dot, the Home card and the Overview badge all use this one value
   (`home.png`, `project.png`).
2. **Stat strip** (5 cells): Routes; Tables · rows; Apps · published; Runs · 24h + sparkline; Failed · 24h · p50.
3. **Left column**: the Backend card (routes grouped by module: Method, Path, Operation, Auth, "Last call · 24h",
   p50, ▶ Test), then the Apps card (app rows: thumb, name, Published/Draft badge, version, `/a/<slug>`, "Uses
   ordersSummary, createOrder · 2 of 5 operations", api-usage warnings, `Open builder`).
   **Empty state**: one line of text with an inline *Create UI from backend* **text link**. There is no second button,
   and the header `New app` is hidden while the list is empty (review H7).
4. **Right column**: Recent runs (4), Secrets (each key with per-environment badges: green `dev`, amber
   `missing in prod`, grey `not set in prod`, plus the one-line legend for "missing" vs "not set"), and Releases (dev = "Always your
   latest saved code"; prod = "Release #N · 2h ago" or "No release yet · serves latest saved code").
   Draft environments never show an "active" release (review D10).

### 3.3 First-run guide (`project-first-run.png`)

Shown when the project has no modules, no routes and no apps, or when `?setup=1`. Outline item: "Get started n / 4".

| Step | Order | Done when | Body |
|---|---|---|---|
| 1 Add a secret (optional; "I don't need one" skips it) | any | ≥1 project secret, or skipped (`localStorage luna:setup-skip:<projectId>`) | Inline form: Name, Value, Allowed hosts, Environments (dev+prod checked = one POST per environment) |
| 2 Generate your backend | any | ≥1 module | Large composer + 3 starter chips + `Write it myself` |
| 3 Test a route | **gated: needs a route** | any execution with `trigger.type ∈ {route_test, draft_test}` | `Test <first route>` |
| 4 Create a UI | **gated: needs a route** | ≥1 app | Opens the Create-UI dialog |

Steps 3–4 show a grey `🔒 needs a route` pill and are not clickable until a route exists. The reason is
that there is nothing to test or attach yet (review B8). When all 4 are done, a one-time toast appears and
Overview replaces the guide.

### 3.4 Data view (`data.png`)

- **Header**: table name, row count, column chips (`reason string · required`), `Columns`, `Add row`, ⋯
  (rename, delete with type-to-confirm). Info line: "One table for the whole project: dev and prod, tests and
  the published app all read and write these rows."
- **Grid** (`Table` primitive): sticky header; mono id; numbers right-aligned and tabular; JSON cells
  collapsed. Double-click to edit a cell: `PATCH …/rows/{id}`, optimistic, rolled back on error. `Add row` adds an
  inline new row; field errors come from `422 row_invalid` `detail.errors[{row,column,error}]` and are shown at the cell.
  The footer shows the row count, the edit hint and the `studio.db.table("…")` snippet.
- **Filter bar** (`+ Filter`) builds the rows filter from column types, using exactly the engine grammar
  (`data_table_service.py:3-4`): `{"col": v}` (equals) or `{"col": {"$eq|$ne|$gt|$gte|$lt|$lte": v, "$in": [..],
  "$contains": "s", "$exists": bool}}`, combined with top-level `$and`/`$or` arrays. Row ids are integers. `created_at` is
  row metadata, not a user column. **Sort** is the column header.
- **Pagination**: infinite scroll with `GET …/rows?limit=50&cursor=&sort=&count=true`.
- New table dialog: name + column rows. `409 name_taken` and `422 invalid_column` show inline.
  `409 rows_incompatible {count}` appears in the Columns dialog footer ("12 rows violate this change").

### 3.5 Secrets view (`secrets.png`)

- **Matrix**: rows are keys, columns are the project environments (`dev · draft`, `prod · live`), plus Allowed hosts.
  Cell states:
  `set · 2d ago` (green dot); **`missing`** (amber, plus an `Add` button: referenced and absent, from
  `GET …/secrets/missing`); `not set` (grey, ghost `Add`: absent and unreferenced); `team value` (users icon;
  the team-shared variable applies); `set · overrides team`.
  The hosts cell shows chips, or the amber "not bound: Python calls refused" + `Bind host` when `allowed_hosts` is null.
- **Expanded row**: Referenced by (`refunds.py:21 · GET /refunds/stats`, a link to the editor line), Allowed
  hosts (chip input, `PUT …/secrets/{id} {allowed_hosts}`), Value ("•••••••• (write-only)" + `Replace value in dev`).
- **Banner** above the matrix for each missing key: "PAYMENTS_TOKEN is used by refunds.py:21 but isn't set in prod.
  Published apps calling GET /refunds/stats will fail." + `Add to prod`.
- **Team rows** are read-only here, with the legend "team values are edited in Workflows → Variables".
  Project rows can **never** be edited through `/api/variables` (§9, D2).
- `CreateSecretPopover` (shared by the code widget, release checks, publish pre-flight and setup step 1): key
  (read-only when it comes from a reference), value, environment(s), type, allowed hosts (prefilled from literal hosts
  in the referencing module). It makes one `POST …/secrets` per environment. `409 exists` → "Already set in prod: edit
  instead".

### 3.6 Runs view (`runs.png`)

- **List**: status dot, age, method + path (or a workflow icon + name), **trigger type** badge, source
  ("app · Orders dashboard", "test · studio", "AI self-test", "workflow · editor"), environment, duration.
  Filters: environment (follows the switch, plus "All envs"), **trigger type** (not `mode`, review C2), status, route.
  `GET …/runs?limit=50&before=&trigger=&status=&route_id=&environment=`. "Load more" reads
  `X-Next-Cursor`, which needs CORS `expose_headers` (§9 C1). The list polls every 5 s while a visible run is `running`.
  The subtitle says "recent runs (the engine keeps the newest 100)".
- **Run sheet** (Sheet w 580): status pill, method/path, execution id, trigger type, environment, duration, age,
  `Open in editor` (goes to the module line from `error.line`, or `route.handler.line`).
  **Error box** when `error` is set: `type: message`, the redacted traceback, and a button "refunds.py line 19" that
  goes to `error.line` (review D16). **Trace**: a sandbox bar plus broker calls, with denied calls showing
  `error_code` and an inline fix (§4.4). **Request / Response**: from `metadata.request` / `metadata.response`,
  stored redacted and capped at 16 KB (review C3). **stdout / stderr**.
  Release number: `GET …/releases/{release_id}` when `trigger.release_id` is set.
- Empty state: "No runs yet. Test a route to see one here." (`runs-empty`). Pruned history: "Older runs were pruned
  (the engine keeps 100)" when `before` returns an empty page.

### 3.7 Settings sheet (`settings.png`)

One scrolling sheet with section anchors (General · Environments · Egress · API keys · Developer · Danger).
The segmented control at the top highlights the current section and jumps between them.

- **General.** Name, description, and **slug**. Typing a new slug runs `PUT /api/projects/{P}?dry_run=true {slug}` →
  `affected_apps`, which shows a confirm block: "**Published apps break until republished.** They call
  /api/p/payments-ops, which stops resolving after the rename." It lists the affected apps, has a checkbox
  "Republish N apps right after renaming" (default on), and the note "hand-written `fetch('/api/p/old/…')` calls
  are not rewritten". The primary button reads "Rename and republish N apps". It makes `PUT /api/projects/{P} {slug}`,
  then `POST /api/apps/{A}/publish {slug: <app slug>, access: <current>}` for each affected app, with progress
  per app, then navigates (`replace`) to the new slug (review B7).
- **Environments.** List with kind, default/live selectors, and add/remove. Responses: `409 environment_in_use`; the dry run validates the
  same as the real save (D12).
- **Egress allowlist.** Host chips.
- **API keys.** A table (name, environment, prefix, last used, `Revoke`). `New key` asks for a name and an environment, then shows the key **once**
  in a highlighted copy box ("shown once, copy it now"). Revoke uses an AlertDialog; the key then gets `401` on the router.
- **Developer.** SDK version, `Download studio SDK (.zip)`, and `Mint dev broker token (1h)` (shown once).
- **Danger.** Delete project with type-to-confirm. `409 project_not_empty` lists what blocks it.

### 3.8 Release sheet (`release.png`)

- **Environment cards**: dev ("Latest saved code · snapshot #2 taken 3m ago") → prod ("Release #N" or "No
  release yet · serving latest saved code").
- **Create a snapshot** of dev: `POST …/releases?dry_run=true {environment:"dev"}` → checks + diff → `Create
  snapshot`. **Promote**: `POST …/releases/{rid}/promote?dry_run=true {to_environment:"prod"}` → checks →
  confirm → `POST …/promote`.
- **Checks list.** Pass, warn or fail, each fail with a fix: `secrets_present` gets an inline value field +
  `Add to prod` per missing key, then **Re-run checks**. `app_calls_resolve` lists the apps and operations + `Create stubs`.
  `handlers_exist`/`modules_parse` link to the editor line. `stub_routes` and `breaking_vs_active` are amber and never block.
- **"What changes in prod"**: the diff with unchanged items filtered out (review D10).
- **History** per environment: number, age, notes, and a status. `blocked` shows as a dashed amber pill
  ("blocked · secrets"). Releases that are only blocked are never "active".
  `Roll back to #N` is offered on release environments only.
- **Result codes the UI handles** (review D10): `201` release created; `422 checks_failed {checks, release_id,
  number}` (a blocked release is stored, numbered, shown in history and never activated); promote `409
  release_blocked` (the source release is blocked) → "This snapshot was blocked; create a new one"; rollback
  `409 no_previous_release` → disabled button with a tooltip; `422 wrong_environment` → toast. Rollback checks only
  warn; the warnings appear in the toast.
- **CHANGED (coordinator decision, 2026-10-15):** apps with draft source but no saved version **are** versioned into the release snapshot; only apps with **no source at all** fail `app_calls_resolve` with "Save the app once before releasing" (D10).

---

## 4. Backend view (`backend.png`, `states.png`, `dialogs.png`)

### 4.1 Layout

`[outline] [editor panel (flex)] [inspector (404px; 360 below 1360; 340 below 1280)]`

**Editor panel**
1. **File bar** (h-40/44): open module tabs (mono, `●` when dirty; below 1280 only the active tab shows). State
   text: "Unsaved · 3 new routes on save" / "Saved 2m ago" / "1 error, not saved" (hidden below 1360). `SDK`.
   **Save ⌘S**.
2. **AI composer** (`PromptComposer`): avatar, prompt (one line, expands to 4), `Edit`, **Generate**/**Refine ⏎**.
   While a draft is pending, a **result row** shows: `✓ Refined +4 −1 · 3 routes · self-test 2/2 passed · 1 secret missing in prod
   [Discard] [Diff]`. Below 1360 the route count hides and **Diff moves into a ⋯ overflow menu**, so Diff is
   reachable at every width (review H2). An editable **module name** chip appears in the result row when the
   analysis reports a `module_name` conflict (review E3). A **tables row** shows "☑ Create table `refunds` on
   save · reason string · amount number · created_at datetime [Edit columns]" for every
   `analysis.tables[exists=false]` (review B1).
3. **Code**: CodeMirror 6 Python (§4.6), with a lint gutter, AI-diff line tint + left bar, widgets, and horizontal scroll
   inside the editor (code never wraps).
4. **Status bar**: syntax state, warnings, `Ln, Col`, Python version, sandbox badge (`GET /api/sandbox/status`).

**Inspector tabs**: **Test** (§4.7), **Reference** (§4.6), **client.ts** (read-only `GET …/client.ts?environment=<view
env>`, OpenAPI toggle, "Copy usage", spec revision).

### 4.2 Generate → draft → test → save

```
idle ─Generate/Refine─▶ streaming ─result─▶ draft-pending ─Save─▶ saving ─ok─▶ saved
  ▲                       │ error / Stop        │ Discard ─▶ idle (previous source restored)
  └───────────────────────┘                     │ Refine ─▶ streaming (current_source = editor text)
```
1. ⏎ → `POST /api/projects/{P}/modules/generate` (§10.1) with `{prompt, module_id?, module_name?,
   current_source, conversation (last 6 turns), self_test:true}`.
2. **Streaming** (`states.png`, top left): the composer shows **Stop (Esc)**, a shimmer phase line ("Self-testing
   GET /refunds… · step 4 of 4 · attempt 1 · 14 s") and phase chips (Read project ✓, Wrote refunds.py ✓, Syntax
   OK ✓, Self-test). The editor is read-only with a "Read-only while generating" tag. Stop aborts the fetch. The
   server cancels the LLM call and the sandbox jobs (§10.1). The previous source is restored.
3. **`draft` event** → the source is applied as **one CodeMirror transaction** (`userEvent: "ai.draft"`), so
   ⌘Z restores the pre-draft text in one step. The editor instance is **never remounted** across
   draft, refine, save or discard (review B5). Discard is also one transaction.
4. **Draft pending**: diff tint on changed lines. The outline shows `new` routes and "created on save" tables.
   Widgets for secrets appear (§4.5). Self-test results are pre-loaded in Recent tests. `Diff` opens a merge view
   (`@codemirror/merge`).
5. **Test** any draft route at once via `run-draft` (§10.2). Reads from tables that don't exist yet return
   empty; writes report `table_not_found` with a **Create table now** fix (`states.png`, bottom right).
6. **Save ⌘S**:
   1. If the tables row has checked tables: `POST …/tables {name, columns}` for each (`409 name_taken` = it already
      exists → continue).
   2. Saved module: `PUT …/modules/{M}?dry_run=true {source, base_sha}` → if `breaking_changes` has any
      `breaking:true` (including **auth loosening**, §9 D11) → `BreakingChangesDialog` (`dialogs.png`, left).
   3. `POST …/modules {name, source}` (new) or `PUT …/modules/{M} {source, base_sha}`.
   4. `409 module_changed` → the concurrent-edit dialog (`dialogs.png`, right): *Show diff* / *Reload theirs* /
      *Overwrite* (re-sends without `base_sha`) (review B4).
   5. Success → toast "Saved refunds.py · 3 routes created · table refunds created", the outline pills settle, and the
      invalidations of UI-INTEGRATION §3.5 run.
7. **Drafts survive reload.** Source, prompt history and `base_sha` (the saved `source_sha` the draft started
   from) are stored in `sessionStorage` (`luna:draft:<projectId>:<module>`). On restore, if `base_sha` ≠ the current
   `source_sha`, a banner says "refunds.py changed since this draft was made" with *Diff* / *Discard draft* /
   *Keep draft* (review B4).

### 4.3 Manual path

- `Write it myself` / outline `+` opens the **New module dialog** (`dialogs.png`, right, lower half): name (grammar + reserved
  names, §2.2; `409 name_taken` inline), starting template (Empty / Table CRUD / External API).
- **Live analysis while typing** (600 ms debounce): `PUT …/modules/{M}?dry_run=true` for saved modules,
  `POST …/modules?dry_run=true` for new ones. Both return the normalised dry-run shape (§9 C9/D7), which feeds the route
  preview, diagnostics and breaking-change hints. Nothing is created.
- `Tab` = 4 spaces, `Shift-Tab` dedents, `⌘/` comments. No formatter.

### 4.4 Errors (all with `data-testid` = `err-<code>`)

| Error | Contract (§9) | UI |
|---|---|---|
| Syntax error | `422 {"detail":{"code":"syntax_error","errors":[{"line","column":int\|null,"message"}]}}` | A CM diagnostic at line:col; a whole-line diagnostic when `column` is null; red gutter and line tint; status "1 error"; file bar "1 error, not saved"; toast "Not saved: syntax error on line 7". Nothing is persisted (`states.png`, bottom left). |
| Invalid decorator | `422 {"detail":{"code":"invalid_decorator","errors":[{"line","message"}]}}`: a non-literal `auth`/`name`/path, an unknown `auth` value, or a duplicate in the module | A diagnostic on that line: "auth must be a string literal: 'none', 'app_token' or 'api_key'". |
| Route conflict | `409 {"detail":{"code":"route_conflict","conflicts":[{"method","path","line","existing_route_id","handler":{"type","id","name"}}]}}` | A diagnostic on `line`: "POST /orders already exists (workflow 'Create order')" + a link to the handler. |
| Module name taken | `409 {"detail":{"code":"name_taken"}}` | Inline on the name field (New module dialog or the result-row chip). |
| Concurrent edit | `409 {"detail":{"code":"module_changed","current_sha","updated_at"}}` | Concurrent-edit dialog. |
| Generation errors | §10.1 error events | The composer turns red (`states.png`): message + Retry. **`llm_unavailable`** (pre-stream 503) shows the "AI isn't configured on this server (no LLM key)" state with `Start from a template` and a template picker. `generation_in_progress` shows "Another generation for this project is running (another tab?)" + `Retry`; there is no remote cancel (re-review N5). |
| Draft still invalid after repairs | stream `error {code:"generation_invalid", draft, analysis}` | The draft is shown with its diagnostics, and the result row reads "2 problems left: fix before saving". |
| Runtime error in a test | run-draft or `/test` response `error:{type,message,traceback,line,module,code}` (redacted) | Red status pill; error box with `type: message` + a **"Go to line N"** button that jumps to `error.line` when `error.module` is this module (review D16). |
| `table_not_found` | `error.code:"table_not_found"`, `error.table` | A fix card: "Table refunds doesn't exist yet. It's created when you save." + **Create table now** (uses the draft's declared columns). |
| Broker denials | trace `broker_calls[].status:"denied"`, `error_code` | Inline fixes: `missing_secret` → CreateSecretPopover; `secret_not_bound` → "Bind PAYMENTS_TOKEN to payments.internal"; `host_not_allowed` → "Add host to the project allowlist"; `read_only` (self-test only) → informational; `secret_expression_not_allowed` → "Don't transform studio.secret(...) (no .upper(), slicing or hashing)"; `call_limit_exceeded` → informational. |
| Sandbox unavailable | `503 {"error":"sandbox_unavailable"}`; `/api/sandbox/status.available=false` | A red status-bar badge. The Test tab shows an alert: "Python can't run on this server (bubblewrap unavailable). You can still edit and save; routes return 503." Send is disabled. |

### 4.5 Secret widgets

- A client scan on every change: `studio\.secret\(\s*['"]([A-Za-z_]\w*)['"]\s*\)` and `\$vars\.(\w+)`, compared with
  `GET …/secrets`. During a pending draft, `analysis.secrets` is authoritative.
- Rendering: an amber wavy underline on the key; a block widget under the statement. The variants are:
  "missing everywhere [Add secret]", "set in dev, missing in prod [Add to prod]", and "not bound to a host [Bind to <host>]". `×`
  hides the widget for the session. Its fix opens `CreateSecretPopover` anchored to the widget; the hosts are prefilled from literal
  `https?://host` strings in the module.
- Rule text (shown in the Reference tab and given to the AI, review C15): *"Use `studio.secret('K')` in node
  parameters. Embedding it in an f-string or string concatenation is fine. Never transform it (`.upper()`,
  slicing, hashing, `len`)."*

### 4.6 SDK autocomplete and Reference

**Single source (review C14):** `engine:sdk/generate.py` also emits `sdk_reference.json` (served as `GET
/api/sdk/reference`). It contains the static API (decorators, `req`, `Response(status=200, body=None, headers=None)`,
`HTTPError(status, body)`, `studio.db.table(name)` methods with signatures, `studio.secret(name)`,
`studio.nodes.call(node, parameters, items)`) and the rules text (§4.5, sandbox limits, literal decorators).
The studio's autocomplete, its Reference tab, **and** the generation prompt (§10.1) all read this one file.
Node functions come from `GET /api/sdk/functions`, using `python_names` (already returned, review C16) plus the
per-parameter `required`/`default` that §9 adds.

Completion sources:
1. `studio.` → `nodes`, `db`, `secret`, `api`.
2. `studio.nodes.` → functions. **label** = function name; **detail** = keyword-only signature
   `(*, method, url, headers, body, …)`; **type tag** (right-aligned, muted) = node type `HttpRequest`; **info** =
   description + `returns list[dict]`. Accepting it inserts a snippet with tab stops for the required parameters.
3. Inside `studio.nodes.<fn>(`: keyword arguments; enum values.
4. `studio.db.table("` → table names; `.` → the 6 methods.
5. `studio.secret("` → secret names with presence badges.
6. `@api.` → the 5 decorators.

The Reference tab is searchable, grouped (Routes · Data · Secrets · Node functions), with `egress` badges and Insert/Copy.

### 4.7 Test panel

- Route picker (draft routes marked `draft`); Params/Query/Body/Headers tabs; **Send ⌘⏎**. Below 1360 px the
  tab bar and **Send go on two rows, with Send full-width** (review H2).
- Note under the request (always visible): "Runs your **unsaved** code with **dev** secrets. Writes go to your real
  tables (shared by dev and prod)." It adds "refunds doesn't exist yet: reads return empty" when relevant (D11, B3).
- **Response**: status pill, duration, execution id (a link to the run sheet; hidden when `execution_id` is null, e.g.
  a stub's 501, review C10), and `Use as schema`, which is **disabled unless the status is 2xx** and the route is saved.
  Sub-tabs Body/Headers/Schema. The body box wraps (`pre-wrap`) and scrolls vertically inside itself (no clipping).
- **Trace**: a waterfall (sandbox bar plus broker calls). Long detail lines truncate with an ellipsis and a full-text tooltip.
- **Recent tests**: the last 10 in this session for this module; clicking one restores the request and response.

| Editor state | Call |
|---|---|
| Saved, unchanged, route exists | `POST …/routes/{R}/test {params,query,headers,body,environment: default_environment, apply_schemas:false}`. The response includes `trace` and `error` (§9 G-UI-4, D16). |
| Dirty or draft pending | `POST …/modules/run-draft` (§10.2) |
| Use as schema | `/test` with `apply_schemas:true` (2xx only) |

The `environment` sent is always `project.default_environment`. A non-draft environment returns `422 environment_not_draft`;
the UI never sends one.

---

## 5. From backend to UI

### 5.1 Create UI dialog (`create-ui.png`, `create-ui-dark.png`)

Entry points: the Overview hero, the Apps empty-state link, outline Apps `+`, the toast after a save, ⌘K, and the Backend ⋯ menu ("Create
UI from these routes", which preselects that module).

Fields: **App name**; **Address when published** (`/a/<slug>`); **Routes** (a checklist of `none`/`app_token` routes,
`api_key` routes disabled with "server-only"; badges "response shape known" / "no schema yet" + `Test now`);
**Starting point** (Dashboard / Form / Table + CRUD / Blank: this only changes the default prompt); **Describe the UI**.
Footer: "Preview uses dev secrets, published uses prod. Tables are shared: the preview writes real rows." (D11).

Submit:
1. `POST /api/projects/{P}/apps {name, definition: BLANK_DEFINITION, description: prompt.slice(0, 1000)}`. The
   description is capped at 1000 characters (review C18). The full prompt travels only in the URL/chat.
2. Persist the routes selection: `PUT /api/apps/{A} {definition: {...definition, studio: {route_ids:[…]}}}`. The
   route selection lives in the app definition under `definition.studio.route_ids`, so it survives reloads and
   devices (review A2). **It must not be lost on later saves (re-review N4).** The contract is enforced twice:
   (a) the engine's `PUT /api/apps/{A}` copies the stored `draft_definition.studio` into an incoming `definition`
   that lacks a `studio` key (§9 N4); (b) the studio has **one** `saveApp(appId, patch)` used by manual save, AI
   auto-save, import, revert and the route picker, and it always merges the current `studio` block from the query
   cache.
3. Navigate to `/p/{slug}/apps/{A}?prompt=<urlencoded>&slug=<publish slug>`. The builder auto-sends the first
   message once, then removes `prompt` with `navigate({replace:true})`.

### 5.2 App builder (`builder.png`, `builder-1024.png`, `builder-dark.png`)

Layout: `[Backend panel 288] [Preview] [Chat 360]`. Below 1280: the Backend panel becomes a 44px rail (plug icon,
`2/5` usage badge, client.ts icon), chat is 320, and the preview URL pill hides.

- **Top bar**: `luna / Payments Ops / Orders dashboard ⇕` (app switcher), `v3 · saved`, `Preview | Code`, history,
  app settings (§6.4), **Publish**.
- **Backend panel**: a connection card (project, spec revision, base URL); **Operations** (every route) with
  usage from `GET /api/apps/{A}/api-usage`, which detects `api.<op>(` calls (review C11):
  - ✓ green only when **an observed 2xx** happened in this preview session (H6): `App.tsx:14 · 200 · 31 ms in
    preview`;
  - grey dot + "not called yet" for used but not observed;
  - `Use` for available, unused operations;
  - `stub` (dashed amber pill, from `routes[].handler.type == "stub"`);
  - `changed` (amber: "response changed since this version: check `total`");
  - `missing` (red + `Create stub`).

  Below it, an **api/client.ts** preview + `Copy usage`.
- **Route picker (review A2)**: the composer chip "`2 routes ⌃`" opens a popover with every route as a checklist,
  titled "Routes for the next messages · saved with the app". `Done` writes `definition.studio.route_ids` with a
  `PUT /api/apps/{A}`, and every subsequent chat body sends them as `route_ids`. Help text: "The AI always sees every route;
  checked ones are its focus." Apps opened from `/builder` with no routes show the chip "Attach routes".
- **Chat**: the existing panel. Tool rows: Wrote files (+N), Uses ops, and Declared `METHOD /path` (stub) when
  `endpoints_declared` arrives. The mockup shows no stub on the first turn: the corrected backend offers `declare_endpoint`
  on every turn and creates each stub inside its tool call (§9 C6), but nothing in this scenario needs one.
- **Preview**: device toggle, URL pill `preview · /a/<slug>` + `dev` badge, reload, open-in-tab. The **console strip**
  shows the last `/api/p/` call and the error count.

**What the studio sends and does:**

| Concern | Exact behaviour |
|---|---|
| Chat request | `POST /api/ai/app-builder/chat {message, app_id, current_version_id, api_execution_ids: [], project_id, route_ids: definition.studio.route_ids, conversation_history}`. The server builds the "## Project API" section (focus routes marked "← focus"), injects `api/client.ts` into the working set, and offers `declare_endpoint`. |
| SSE events | `message` with `type` ∈ `phase, text, thinking, tool_call, tool_result, code, endpoints_declared, error`, then **always** `done` (§9 C5). The studio drops the obsolete `plan` handler. **A stream that closes without `done` is an error**: "The assistant stopped unexpectedly" + Retry. |
| `code` files | They include `api/client.ts`. The studio drops it before `setFiles` and before every save, and merges the current client into the preview files. |
| **Preview base URL (review C12)** | The generated client uses the relative `BASE = '/api/p/<slug>'`. The IframeSandbox bridge resolves every relative URL against `config.workflowApiUrl` (the engine origin; §11.4), **not** the Vite origin. It then adds `Authorization: Bearer <preview token>` **only if** `new URL(resolved).origin === engineOrigin && pathname.startsWith('/api/p/')` and no authorization header is already present. It handles messages only from its own iframe (F12) and normalises headers to plain objects (F14). |
| Preview token | `POST /api/projects/{P}/apps/{A}/preview-token` → `{token, expires_at (UTC, "Z"), environment}` (review C13). The token is refetched at `expires_at − 5 min`; on a 401 `token_expired` it is refetched once and the request retried. |
| After each AI turn or save | Refetch `api-usage`. |

### 5.3 Publish (`publish.png`)

1. **Fields**: Address `/a/<slug>`; **Access** segmented control **Public** (default) / Password / Private. The UI always sends
   `access` (the API defaults to private, review C4). Private shows the note "Only reachable from the studio
   preview; /a/<slug> returns 404".
2. **Pre-flight** (when the dialog opens):
   - operations exist (from `api-usage`: missing → red, with `Create stub`);
   - secrets: `GET …/secrets/missing?environment=<live>` filtered to the modules behind the operations the app
     calls → amber row + `Add to prod`;
   - release info ("prod has no release, so it serves your latest saved backend. Create a release to pin it", or
     "prod serves release #4; changes saved since then aren't live");
   - "The published app reads and writes the same tables as dev." (D11).

   Warnings never block: "1 warning · you can publish anyway".
3. `POST /api/apps/{A}/publish {slug, access, access_password?}`. `409 {"detail":{"code":"slug_taken"}}` shows inline
   under the address field (review C4). An esbuild failure shows the stderr summary in a collapsible.
4. **Success state**: URL + Copy/Open; "Calls /api/p/payments-ops/… with an app token that refreshes itself";
   `Unpublish` (§6.3).

**Published runtime (contract, §9 D3):** the published page calls `/api/p/<slug>/…` with
`Authorization: Bearer <app token>`. App tokens are bound to the **app** (plus a rotation key), not to a version, so
promote and rollback don't invalidate open pages. On `401 {"error":"token_expired"}` the generated client calls `POST
/a/{slug}/_token` → `{token, expires_at}` once and retries. The router serves `live_environment` (prod).

---

## 6. Other flows (review B2)

| Flow | Entry point | Confirm / UI | API | After |
|---|---|---|---|---|
| **6.1 Delete module** | Outline module ⋯ → Delete; file-bar ⋯ | `dialogs.png` (centre): lists `routes.removed` from `DELETE …/modules/{M}?dry_run=true` and marks routes used by apps (`breaking_changes[].affected`). The copy depends on prod (re-review N10): with no release, "These routes stop working in dev and in prod (prod serves your latest saved code)"; with an active prod release, "These routes stop working in dev; prod keeps serving release #N until you promote". "The `refunds` table and its rows are kept." Type the module name to confirm. | `DELETE …/modules/{M}` | Toast "Deleted refunds.py · 3 routes removed"; invalidate modules/routes/spec/api-usage; go to Overview |
| **Rename module** | Module ⋯ → Rename | Dialog: new name (grammar). "Routes keep their paths; apps are unaffected." | `PUT …/modules/{M} {name}` (§9) | Navigate (`replace`) to the new name |
| **6.2 Delete app** | Overview app row ⋯; builder app switcher ⋯ | AlertDialog "Delete Orders dashboard? Its published link /a/orders-dashboard stops working." | `DELETE /api/apps/{A}` | Toast; go to Overview |
| **6.3 Unpublish** | Publish success state; app settings | AlertDialog "Unpublish? /a/<slug> will return 404." | `POST /api/apps/{A}/unpublish` | Badge → Draft; E2E checks for a 404 |
| **6.4 App settings** | Builder top bar gear | Sheet: name, description, attached routes (same checklist as the route picker), workflow allow-list (`workflow_ids`, F11), access, API Tester ("attach external API"), Delete | `PUT /api/apps/{A}` | – |
| **6.5 API keys** | Settings §3.7 | – | `GET/POST …/api-keys`, `DELETE …/api-keys/{id}` | – |
| **6.6 Workflows list** (`workflows.png`: populated list, ⋯ menu with Delete blocked, empty state; ⌘K is in `command-palette.png`) | Top-bar tab | Table: Name, Trigger (Manual/Webhook/Cron · schedule), Handles route (`GET /x` link), Last run (status, age), Status (Published/Draft), Updated, ⋯ (Open, Duplicate, Move to project, Delete). Delete is blocked with "Used by GET /x" (`409 workflow_in_use`). Empty state: "No workflows. Python routes are the main backend; use a workflow for scheduled or visual automations." + `New workflow`. `Import` accepts JSON. | `GET/POST …/workflows`, `DELETE /api/workflows/{W}`, `PUT /api/workflows/{W} {project_id}` | The editor opens at `/p/$p/workflows/$id`. The editor's environment selector shows `project.environments` and is stored per project (`workflow-studio:active-environment:<projectId>`, default `project.default_environment`; review G3) |
| **6.7 Delete workflow** | List ⋯ | AlertDialog | `DELETE /api/workflows/{W}` | – |

---

## 7. Components and tokens

### 7.1 Reuse, wrap or promote (no parallel primitives, review G2)

| Need | Use |
|---|---|
| Buttons, badges | `ui/button.tsx` (+ variant `ai`); `ui/badge.tsx` (+ variants `success`, `warning`, `ai`, `neutral`, `stub` = dashed amber outline) |
| Segmented control | **Promote** `PillTabs` (`ApiTesterPanel.tsx:924`) to `ui/segmented.tsx` on Base UI `ToggleGroup`, and migrate its call sites |
| Tabs | Wrap Base UI `Tabs` as `ui/tabs.tsx` (underline style = today's `editor-tabs.tsx` look); `editor-tabs.tsx` re-exports it |
| Checkbox, Switch | Wrap Base UI `Checkbox`/`Switch` as `ui/checkbox.tsx`, `ui/switch.tsx` |
| Empty states | Generalise `ConversationEmptyState` into `ui/empty-state.tsx` (icon, title, text, actions); the chat keeps using it |
| Table | **New** `ui/table.tsx` (shadcn API: `Table, TableHeader, TableRow, TableHead, TableCell`; 34px header, 42px rows, `dense` 32/38px) |
| Kbd, StatusDot, MethodBadge | **New** `ui/kbd.tsx`, `ui/status-dot.tsx`, `shared/components/api/MethodBadge.tsx` |
| Dialog, AlertDialog, Sheet, Command, DropdownMenu, Select, Tooltip, HoverCard, ScrollArea, Collapsible, Skeleton, Spinner, JsonViewer, InputGroup, ButtonGroup, Toolbar | Existing, as is (fix the nested-trigger usages, §1.1) |
| Code editor | **Extend** `ui/code-editor.tsx` (review B5): new props `language: 'json'\|'javascript'\|'python'` (+ `@codemirror/lang-python`), `readOnly?: boolean`, `extensions?: Extension[]`, `diagnostics?: Diagnostic[]` (`@codemirror/lint`), `onReady?(view)`; and `editorRef` exposing `applyTransaction`. Language, theme and readOnly switch through **Compartments**; the view is created **once** and never recreated on prop changes (today it is recreated at `:142`). `buildTheme()` (`:25-81`) is rewritten to read `--code-bg`, `--code-gutter`, `--syn-*`. |

**New feature components**: `PythonModuleEditor`, `PromptComposer`, `RouteTestPanel`/`RequestEditor`/`ResponseView`,
`TraceWaterfall`, `SdkReference`, `ProjectTopBar`, `ProjectOutline` (+ rail), `CommandPalette`,
`OverviewView`, `SetupGuide`, `StatStrip`, `RoutesTable`, `DataView`, `RowsGrid`, `FilterBar`, `TableDialog`, `SecretsMatrix`,
`CreateSecretPopover`, `RunsView`, `RunSheet`, `SettingsSheet`, `ReleaseSheet`, `ChecksList`, `BreakingChangesDialog`,
`ModuleChangedDialog`, `NewModuleDialog`, `DeleteModuleDialog`, `CreateUiDialog`, `BackendConnectPanel`, `RoutePicker`,
`PreviewConsoleStrip`, `PublishDialog` (rewritten), `WorkflowsList`. Locations as in v1 (`features/<area>/components/`).

### 7.2 New tokens (add to `index.css` `:root` and `.dark`, and map them in `@theme inline`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--canvas` | `#f1f3f5` | `#0a0a0a` | App background behind panels |
| `--panel-border` | `rgb(0 0 0/.06)` | `rgb(255 255 255/.06)` | Panel hairline |
| `--code-bg` / `--code-gutter` | `#fbfbfc` / `#80878e` | `#141414` / `#6e6e6e` | Editor surface / line numbers |
| `--line-added` | success 9% | success 12% | AI-diff tint |
| `--method-get/post/put/patch/delete` | `#0d7a41 #0a6fb8 #94600a #0f766e #b8300f` | `#34c77b #4db5ff #e0a84f #2dd4bf #ff6b4a` | MethodBadge (text on a 10% tint of itself) |
| `--warning-text`, `--success-text` | `#8f5a07`, `#0d7a41` | `#e0a84f`, `#34c77b` | Amber or green text on tinted badges |
| `--ai`, `--ai-text`, `--ai-soft` | `#7b61ff`, `#6a4fe6`, ai 9% | `#a78bfa`, `#b9a3ff` | AI affordances only |
| `--syn-keyword/string/number/fn/comment/deco` | `#c2185b #0d7a41 #94600a #0b6fc0 #737a82 #94600a` | `#ff7ab6 #5fd394 #e0a84f #6cc0ff #8a8a8a #e0a84f` | Python highlighting (keywords are magenta, not violet) |
| `--focus-ring` | ring 45% | ring 45% | Input halo |

### 7.3 Contrast (measured; pinned by `studio:shared/theme/__tests__/contrast.test.ts`)

| Pair | Light | Dark | Min |
|---|---|---|---|
| Method colour on its own 10% tint (over card) | GET 4.71 · POST 4.59 · PUT 4.66 · PATCH 4.76 · DELETE 5.17 | 6.32 · 6.17 · 6.42 · 7.26 · 5.12 | 4.5 |
| `--warning-text` on warning 13% tint | 4.82 | 6.4 | 4.5 |
| `--ai-text` on `--ai-soft` | 4.72 | ≥ 6 | 4.5 |
| `--syn-keyword` / `--syn-comment` on `--code-bg` | 5.68 / 4.20 | 7.64 / 5.34 | 4.5 / 3.5 (comments are italic) |
| `--code-gutter` on `--code-bg` | 3.52 | 3.61 | 3.0 |

The test parses `index.css`, mixes the tints the same way CSS `color-mix` does, and asserts the minimums.
Existing `--muted-foreground #868e96` is 3.3:1 on white; it is used only for meta text ≥ 12px. Changing it is out of scope (noted).

---

## 8. States, shortcuts, responsiveness

### 8.1 State table per view (review B6). Every state has a `data-testid`.

| View | Loading | Empty | Error | Special |
|---|---|---|---|---|
| Overview | Skeleton stat strip + 3 table rows (`overview-loading`) | First-run guide (`setup-guide`) | Inline card "Couldn't load project summary · Retry" (`overview-error`) | `project-not-found` full page; Healthy/Degraded/Failing badge from `summary.health` |
| Backend | Skeleton editor lines (`backend-loading`) | No modules: large composer + templates (`backend-empty`) | Load error: "Couldn't load refunds.py · Retry" | `composer-streaming`, `composer-error`, `composer-unavailable` (llm_unavailable), `composer-busy` (generation_in_progress), `sandbox-unavailable`, `module-changed`, `draft-restored-stale` |
| Data | Skeleton rows | "No rows yet. Your routes write here: `studio.db.table("refunds").insert(...)`" + Add row (`data-empty`); no tables: "No tables. Tables are created when you save generated code, or here." (`tables-empty`) | Rows fetch error · Retry | `row-invalid` inline, `rows-incompatible` |
| Secrets | Skeleton matrix | "No secrets. Add API keys your backend calls with." + Add (`secrets-empty`) | Retry | `secrets-missing` banner, team rows read-only |
| Runs | Skeleton list | `runs-empty` "No runs yet. Test a route to see one here." | Retry | `runs-pruned` "Older runs were pruned (the engine keeps 100)" |
| Release sheet | Spinner in the checks area | "No snapshots yet. Create one from dev." | `release-blocked` (checks list with fixes), `release-409-blocked-source` | Rollback disabled with tooltip (`no_previous_release`) |
| Builder Backend panel | Skeleton ops | "This project has no routes. Generate a backend first" + link (`builder-no-routes`) | api-usage error · Retry | `builder-stream-error`, `stub`, `changed`, `missing` rows |
| Publish | Pre-flight spinners | – | `publish-slug-taken`, `publish-build-failed` | `publish-private-note` |

Copy for errors always comes from `ApiError.message` (slice 0). Field-level errors render inline **without** a toast.
Success toasts appear only for cross-region effects.

### 8.2 Shortcuts

`⌘K` palette · `⌘S` save module · `⌘⏎` send test or submit dialog · `⏎` generate (composer focused) · `Esc` stop generation
or close · `⌘I` focus composer · `⌘.` toggle inspector · `[` toggle outline · `g o/b/d/s/r/w` · `⌘⇧D` theme ·
`Ctrl-Space` completions.

### 8.3 Responsive

| Width | Behaviour |
|---|---|
| ≥1360 | As the 1440 mockups |
| 1280–1359 | Inspector 360; request tab bar and **Send on two rows (Send full width)**; "Use as schema" icon-only; composer result hides the route count; **Diff in ⋯** |
| 1024–1279 | Outline → view-icon rail; inspector 340; only the active file tab; Overview one column; builder Backend panel → rail, chat 320 |
| <1024 | A banner "luna works best on a wider screen"; no horizontal page scroll |

Verified in `backend-1280.png`, `backend-1024.png` and `builder-1024.png`. Nothing essential is clipped, and the code
editor scrolls horizontally inside itself. E2E checks **per-panel** clipping, not just the page (§12.6).

---

## 9. Backend contracts the UI relies on (corrected behaviour)

The review's backend-fix list is being implemented by a separate agent. The UI is written against these contracts;
each is pinned by an engine test **and** by the E2E scenario named in the last column.

| # | Contract | E2E |
|---|---|---|
| D1 | Decorator `auth`, `name` and path must be **string literals**; `auth ∈ {none, app_token, api_key}`. Anything else is `422 invalid_decorator {errors:[{line,message}]}` (on save, dry run, run-draft and in the generate analysis). | T13 |
| D2 | `/api/variables/{id}` GET/PUT/DELETE match **team-scoped rows only** (`project_id IS NULL`, same team); a project secret id → 404. | T36b |
| D3 | App tokens are bound to the app + a rotation key (not to `published_version_id`); expiry → `401 {"error":"token_expired"}`. **`POST /a/{slug}/_token` authorisation (re-review N3):** `access=public` → issue; `access=password` → requires the valid unlock cookie set by `POST /a/{slug}/_unlock`, else `401 {"error":"unlock_required"}`; `private`, unpublished or unknown slug → `404`; per-IP rate limit 30/min → `429`; the issued token has the same TTL and rotation key as the page token. A test-only per-app `token_ttl_s` is honoured only when `WORKFLOW_TEST_MODE=1` **and** the DB name starts with `workflows_test_` (N7). | T27, T27b, T29 |
| N4 | `PUT /api/apps/{A}` with a `definition` that has no `studio` key keeps the stored `draft_definition.studio`; sending `studio: null` clears it. | T22 |
| D4 | Every outward string is redacted: router bodies, **headers**, error messages (`handler_failed`), Respond-node headers, Python-route headers, public-app errors, traces, stdout/stderr, `metadata.request/response`, SSE payloads (generate, builder), run-draft and self-test output. | T38 |
| D5 | Broker claim `read_only: bool`. When true, `DataTable` ops other than `get`/`list` are denied, as are `send_email`, `http_request` with a method ∉ {GET, HEAD}, and every function whose egress hook is opaque (`postgres`, `mongo_db`, `neo4j`, `llm_chat`, `ai_agent`). The error is `{"ok":false,"error":{"code":"read_only","function","operation?"}}` with HTTP 403. Also the claim `virtual_tables: set[str]`: `list` on one of them → `[]`, `get` → `404 row_not_found`, writes → `table_not_found`. | T3, T5 |
| D6 | Retention on every insert path (including python-route executions); `broker_calls` cascade; an unknown/pruned `before` cursor → empty page (`[]`, no `X-Next-Cursor`). | T37 |
| D7 | `POST …/modules?dry_run=true` returns the dry-run shape and creates nothing; `dry_run` on an endpoint that doesn't support it → `422 dry_run_unsupported`. | T11 |
| D8 | PythonStep in a release-pinned run loads the module from the release snapshot. | engine test |
| D9 | `DELETE …/modules/{M}?dry_run=true` → `{routes:{removed}, breaking_changes}`. | T33 |
| D10 | Release codes as in §3.8; diff filters unchanged items; draft environments have no "active" release; apps with no source at all fail `app_calls_resolve` (CHANGED: draft-only apps are versioned into the release, §3.8). | T29 |
| D11 | Auth loosening (`api_key → app_token/none`, `app_token → none`) is a change of kind `auth_loosened` with `breaking: true`; tightening stays `auth_changed` and breaking. | T32 |
| D12–D15, D17 | Project PUT dry run validates like the real save; root response type change → breaking; `api_calls` cached at version creation; Code-node syntax lines corrected; public-app errors carry `error_code`/`details`. | engine tests |
| D16 | run-draft and `/test` return `error: null \| {type, message, traceback, line, module, code}` (redacted; `line`/`module` refer to the user module, taken from the runner's `out.json` traceback). | T18 |
| C1 | CORS `expose_headers = ["X-Next-Cursor", "X-Execution-Id", "ETag"]`. | T37 |
| C2 | `trigger.type ∈ {manual, webhook, cron, retry, route_test, draft_test, generate_self_test, app, app_preview, api_key, public}` on every execution; `GET …/runs?trigger=` filters by it; `POST /api/workflows/{W}/run` is stored as `manual`. | T8, T40 |
| C3 | `metadata.request` / `metadata.response` stored redacted and capped at 16 KB each (`truncated: true` flag). | T8 |
| C4 | Publish: `409 {"detail":{"code":"slug_taken"}}`; `access` is always sent by the UI. | T26 |
| C5 | App-builder SSE always ends with `error {code,message}` (on failure) and then `done`; generator wrapped in try/finally. | T24 |
| C6 | `declare_endpoint` is offered on every turn; each stub is created inside its tool call with a per-stub result; the client is regenerated before the next model turn. | engine test |
| C7 | Module save errors exactly as §4.4 (`route_conflict` with `conflicts[]` incl. `line`, `existing_route_id`, `handler{type,id,name}`; `name_taken`; `invalid_decorator`; `syntax_error.column` may be null). | T13 |
| C8 | Module save preserves route-level fields the decorator doesn't own (`description`, `request_schema`, `response_schema`, `response_mode`, `validate_request`, `timeout_s`). | T34 |
| C9 | One dry-run shape for routes and modules: `{dry_run:true, routes:{created,updated,removed}, breaking_changes:[{kind,route,operation,field,breaking,affected:[{app_id,app_name,file,line}]}], spec_revision:null, spec_hash:null}`. | T31 |
| C10 | `/test`: non-draft environment → `422 environment_not_draft`; `apply_schemas` is a no-op on non-2xx (`applied:false`); stubs return 501 with `execution_id:null`. | T9 |
| C11 | `api-usage` detects `api.<op>(` against the operation set and returns `status ∈ {ok, changed, missing, stub}`. | T21 |
| C13 | `expires_at` everywhere is UTC ISO-8601 with `Z`. | T23 |
| C14/C16 | `GET /api/sdk/reference` (one source, §4.6); `/api/sdk/functions` adds `required` and `default` per python parameter. | T12 |
| C18 | App `description` ≤ 1000 characters (the UI truncates). | T20 |
| C19 | `client.ts` / `openapi.json` with an unknown environment → `422 unknown_environment`. | engine test |
| B4 | `PUT …/modules/{M}` accepts optional `base_sha`; a mismatch with the current `source_sha` → `409 module_changed {current_sha, updated_at}`. `PUT` also accepts optional `name` (rename). | T17 |
| G-UI-4 | `/routes/{R}/test` returns `trace` (same shape as run detail) and `error` (D16). | T8 |
| G-UI-10 | Python routes expose `handler.line`. | T7 |

---

## 10. New backend pieces (implement exactly as written)

### 10.1 `POST /api/projects/{project}/modules/generate`

**Purpose**: prompt (+ current source) → a validated module draft and the tables it needs. **It never writes**
modules, routes, tables or secrets. The only rows it creates are the self-test execution rows.

**Request** (`ModuleGenerateRequest`, pydantic; unknown fields are rejected):
```json
{"prompt": "string, 1..8000 chars",
 "module_id": "mod_… | null",          // refine an existing module
 "module_name": "refunds | null",      // ^[a-z_][a-z0-9_]*$, not reserved (new, generate, default)
 "current_source": "string | null",    // ≤ 200 000 bytes; wins over the saved source
 "conversation": [{"role": "user|assistant", "content": "≤ 8000 chars"}],   // server keeps the LAST 6
 "self_test": true}
```
`Accept: text/event-stream` (the default) streams; `Accept: application/json` returns a single JSON response.

**Pre-stream HTTP errors (both modes, JSON body `{"detail":{"code","message",…}}`):**

| Status | code | When |
|---|---|---|
| 404 | `project_not_found` / `module_not_found` | – |
| 422 | `invalid_request` | Pydantic: lengths, sizes, unknown fields |
| 422 | `invalid_module_name` | grammar or reserved name |
| 409 | `generation_in_progress` | Another generation is running for this project + actor (`X-User-Id` or `"local"`, client-supplied, so the lock is best-effort per client); the lock is held from request start until the stream ends or is cancelled |
| 503 | `llm_unavailable` | `llm_provider.is_configured(model)` is false. This is the **only** place `llm_unavailable` appears. |

**Model**: `settings.module_gen_model` (env `WORKFLOW_MODULE_GEN_MODEL`, default `claude-sonnet-4-6`).

**Context** (built server-side; the client never supplies context):
1. The system prompt from `sdk_reference.json` (§4.6), including these rules: decorators use string literals only; secrets via
   `studio.secret('K')` (f-strings OK, never transformed); tables via `studio.db.table("<literal>")`; return
   JSON-serialisable values; no file or network access except node functions; 30 s limit; pandas available.
2. The compact catalogue from `/api/sdk/functions`: python name, description, python parameters with
   type/required/default/enum, egress flag.
3. Project facts: slug; tables (name, columns+types, row count); existing routes (method, path, operation, handler
   type, owning module); other module names; **secret names** with environment presence and `allowed_hosts`; the egress
   allowlist. **No secret values, no table rows, no run outputs.**
4. `current_source` (or the saved source of `module_id`) and the conversation (last 6).

**Message layout (pinned; re-review N1). An engine test asserts it exactly:**
1. `system` = items 1–3 above (reference, catalogue, project facts).
2. If there is source: one `user` message whose first line is exactly `Current module <name>.py:`, followed by the
   source in a fenced ```python block; then one `assistant` message `OK.`.
3. The conversation turns (last 6), in order.
4. The **last** message is a `user` message containing **only the prompt, verbatim**.
5. Repair turns append the assistant's tool call, a `tool` result `ok`, then a `user` message starting with the fixed
   repair line (step 3 of the loop). So `last_user_matches` always sees either the prompt or the repair message, never
   source code.

5. The tool `emit_module`:
   ```json
   {"name":"emit_module","parameters":{"type":"object","required":["module_name","source","summary"],"properties":{
     "module_name":{"type":"string"},"source":{"type":"string"},"summary":{"type":"string","maxLength":300},
     "tables":{"type":"array","items":{"type":"object","required":["name","columns"],"properties":{
        "name":{"type":"string"},
        "columns":{"type":"array","items":{"type":"object","required":["name","type"],"properties":{
           "name":{"type":"string"},"type":{"enum":["string","number","integer","boolean","datetime","json"]},
           "required":{"type":"boolean"}}}}}}}}}}
   ```
   The system prompt says: "Declare in `tables` every table your code uses that is not in the project's table list."

**Secret-value scrub (review E4)**: before **every** `call_llm`, the service serialises the outgoing messages and
replaces every occurrence of every decrypted secret value of the project and its team, in all environments, plus the
`expand_secret_variants` forms, length ≥ 4, with `***`. If anything was replaced, `analysis.warnings` gets
`{code:"secret_scrubbed", message:"A secret value in your code or prompt was removed before sending it to the AI"}`.
The value itself is never logged. Self-test output, traces and table rows are **never** put into LLM messages.

**Loop** (one generation = at most 3 attempts; a 120 s wall clock overall):
1. Call the LLM (`call_llm(model, messages, temperature=0.2, tools=[emit_module], max_tokens=8000)`). If the reply has
   no `emit_module` call, take the first fenced ```python block as `source` (other fields default). If there is no
   code at all, that attempt fails with the problem `no_code`.
2. **Analyse** (static, no execution) using `CodeModuleService._plan` (the same path as the dry run):
   - `routes`: `{created, updated, removed}`, each `{method, path, name, function, line, auth}`.
   - `errors`: `[{code: syntax_error|invalid_decorator|route_conflict, line, column|null, message}]`.
   - `conflicts`: `[{kind:"route", method, path, line, existing_route_id, handler}]`, or
     `{kind:"module_name", name}` when `module_id` is null and the name exists.
   - `tables`: the literal names in `studio.db.table("…")`, merged with the declared tables →
     `[{name, exists, columns (existing schema, or declared, or null), lines:[…]}]`.
   - `secrets`: `[{key, present_in:[env], missing_in:[env], allowed_hosts|null, bound, lines}]`.
   - `warnings`: `dynamic_table_name`, `dynamic_secret_name`, `secret_scrubbed`, `public_write_route` (a non-GET route with
     `auth="none"`).
3. **Repair** when `errors`, `conflicts`, an undeclared missing table (`exists:false` and `columns:null`) or `no_code`
   exist, and attempts < 3. Append the assistant turn, then a **user** message whose first line is exactly
   `Fix these problems in the module you emitted:`, followed by one line per problem:
   `- syntax_error line 3: expected ':'`, `- route_conflict line 7: POST /orders already exists (workflow 'Create order')`,
   `- module_name: 'orders_api' is taken, choose another`, `- missing_table refunds: declare it in tables`,
   `- no_code: call emit_module with the full module`. Then go back to step 1.
4. **Self-test** (only if `self_test`, analysis clean and the sandbox available): for each draft route with method GET and
   **no path parameters**, in order, at most 5 routes, run it through the run-draft machinery (§10.2) with broker
   claims `read_only=true`, `virtual_tables = {declared tables with exists:false}`, `max_calls=20`,
   `origin="generate_self_test"`, 10 s per route. Each result is
   `{route:"GET /refunds", ok, status, duration_ms, error_code|null, body_preview (≤2 KB, redacted JSON text),
   execution_id}`. Other routes get `{route, skipped:"writes"|"path_params"|"limit"}`. Self-test failures never
   trigger a repair, and their output is never sent to the LLM.
5. **Cancellation**: the handler polls `request.is_disconnected()` (every 250 ms, in a sibling task). On a disconnect it
   cancels the LLM task, kills running sandbox process groups, writes no further events, and releases the lock
   (`finally`).

**SSE framing** (review E1). Every event is `event: message` with JSON `data` holding `type` and `seq` (1, 2, 3…).
The stream ends with exactly **one terminal event** (`result` **or** `error`), always followed by `event: done`
(`data: {}`).

| `type` | Data |
|---|---|
| `status` | `{phase: "context"\|"generating"\|"analysing"\|"repairing"\|"testing", attempt, message, route?}` |
| `draft` | `{attempt, module_name, source, summary, tables}` |
| `analysis` | `{attempt, routes, errors, conflicts, tables, secrets, warnings}` |
| `self_test` | `{results:[…]}` |
| `result` (terminal) | `{module_name, source, summary, attempts, model, analysis, self_test}`; `self_test` is the **array** of self-test results (the same items as the `self_test` event's `results`), or `null` when no self-test ran |
| `error` (terminal) | `{code, message, draft?: {module_name, source}, analysis?}` with `code ∈ {llm_failed, generation_invalid, timeout, internal}`. `generation_invalid` carries the last draft and its analysis. |

**JSON mode**: `200 <result object>`; `422 {"detail":{"code":"generation_invalid","message","errors","draft"}}`;
`502 {"detail":{"code":"llm_failed","message"}}`; `504 {"detail":{"code":"timeout"}}`; plus the pre-stream table above.

**Limits**: prompt ≤ 8 000 characters; source ≤ 200 KB; conversation: last 6 kept (older turns dropped, not rejected); 3
attempts; 120 s total; self-test ≤ 5 routes, 10 s each, 20 broker calls each; one in-flight generation per project
and actor. Saving a module still caps the source at 200 KB (`413 source_too_large` on POST/PUT modules).

**How tables get created (review B1).** The generator only *declares* them. The UI creates them on Save (§4.2 step 6), or
with "Create table now" from a test. The endpoint never creates tables.

**Engine tests** (fake provider): context contains catalogue names, tables and secret names; a secret value placed
in `current_source` or `conversation` is absent from the call log and `secret_scrubbed` is reported; repair
bad→good (`attempts:2`); 3 failed attempts → `generation_invalid` with the draft; read-only denial per function;
virtual tables; 409 on a concurrent run; 503 when unconfigured; client disconnect cancels (no sandbox process left
behind, lock released); JSON mode equals the SSE `result`; limits rejected with 422.

### 10.2 `POST /api/projects/{project}/modules/run-draft`

**Purpose**: run unsaved module source for one request, in the sandbox, in `default_environment`. Never saves code.
**Writes are real** (D11).

```json
// request (pydantic, unknown fields rejected)
{"source": "≤ 200 000 bytes", "module_name": "refunds", "module_id": "mod_…|null",
 "method": "GET", "path": "/refunds/stats",            // a concrete path, e.g. /refunds/42
 "query": {"since": "2026-10-01"}, "headers": {}, "body": null,   // body ≤ 1 MB serialized
 "virtual_tables": ["refunds"]}                         // the UI sends the draft's declared tables with exists:false
// 200 (always 200 once the handler ran, like /test)
{"status": 200, "headers": {…}, "body": {…}, "duration_ms": 214.0, "execution_id": "exec_…",
 "route": {"method": "GET", "path": "/refunds/stats", "name": "refundStats", "function": "refund_stats", "line": 17},
 "trace": {"sandbox": {"duration_ms", "exit_code", "stdout", "stderr", "truncated", "timed_out"}, "broker_calls": [ … ]},
 "error": null,     // or {type, message, traceback, line, module, code}; code ∈ {handler_failed, table_not_found, timeout, …}
 "inferred": {"request_schema": {…}, "response_schema": {…}}}     // never persisted
```
- **Route resolution**: AST-parse the draft and match `(method, path)` against **only the draft module's** decorators,
  using `route_matcher` rules (static segments first, then fewest params, then longest). Duplicate decorators can't
  reach this point (they are `invalid_decorator`). Path params are extracted by the matcher, and any client-sent
  `params` are ignored.
- **Other modules**: the project's **saved** versions are loaded. Other unsaved tabs are *not* included; the Test
  panel says "Other modules run as saved".
- **Environment**: `default_environment` secrets. Headers `authorization`, `cookie` and `x-api-key` are stripped.
  `$vars` in the payload are not expanded.
- **Auth (re-review N6)**: route `auth` is **not enforced** for drafts. `req` carries no credential, the same as
  `/test`. For `app_token`/`api_key` routes the Test panel notes "Auth isn't checked in tests".
- **Tables**: the broker claim `virtual_tables` (D5) applies to listed names that **do not exist**. Reads return
  empty; writes fail with `table_not_found`, which surfaces as `error.code = "table_not_found"` and `error.table`.
- **Timeout**: the saved route's `timeout_s` if this route already exists, else 30 s. On timeout the **HTTP status is
  200**; the inner response is `status: 504` with `error.code: "timeout"` (re-review N6). The only non-200 responses are
  the HTTP errors listed below, which happen before the handler runs.
- **Execution row**: `mode="sandbox"`, `trigger={type:"draft_test", module_name, route:"GET /refunds/stats"}`,
  environment = draft, redacted `metadata.request/response`.
- **HTTP errors**: `404 project_not_found`; `422 syntax_error | invalid_decorator | invalid_module_name | invalid_request`;
  `404 route_not_in_source {method, path, available:["GET /refunds", …]}`; `413 payload_too_large`;
  `503 sandbox_unavailable`.
- Everything in the response is redacted (D4).

### 10.3 Fake LLM provider (tests only)

**File**: `engine:engine/fake_llm.py`. **Hook**: at the top of `llm_provider._call_llm_once`:
`if fake_llm.ACTIVE is not None: return await fake_llm.ACTIVE.call(model, messages, temperature, tools, **kwargs)`.
Because the hook sits below `call_llm`, the real **retry loop**, the app-builder **agent loop** (tools, escalation,
`declare_endpoint`), and the generate **repair loop** all run unchanged.

**Activation (review E6). `fake_llm.install()` is called in the app lifespan, and *all* of these must hold or startup
aborts with `RuntimeError("fake LLM refused: <reason>")`:**
1. `os.environ.get("WORKFLOW_LLM_PROVIDER") == "fake"`. It is read from `os.environ` **only**: not `_get_env`, not the raw
   `LLM_PROVIDER`, not pydantic settings or `.env`. If the value is not `"fake"`, `install()` does nothing.
2. `os.environ.get("WORKFLOW_TEST_MODE") == "1"`. Test mode itself (which also enables `token_ttl_s` and the
   `/api/_test/*` toggles) is refused at startup unless condition 3 holds, even without the fake LLM (re-review N7).
3. `os.environ["WORKFLOW_DB_NAME"]` starts with `workflows_test_`.
4. `os.environ["WORKFLOW_BIND_HOST"]` ∈ {`127.0.0.1`, `::1`, `localhost`}. This value is self-reported, so it is a
   sanity check only. **The enforcement is the middleware**, which 404s every `/api/_test/*` request whose client
   address is not loopback (re-review N7).
5. `os.environ["WORKFLOW_FAKE_LLM_TOKEN"]` is at least 32 characters.
6. `os.environ.get("WORKFLOW_DISABLE_DOTENV") == "1"`. In addition, `Settings.model_config.env_file` becomes `None` whenever
   this variable is `1`, so nothing can come from `.env`.

An engine test asserts startup **fails** for fake + DB `workflows`, fake + bind `0.0.0.0`, fake without a token, and
fake without test mode. Another asserts `LLM_PROVIDER=fake` (raw) and a `.env` line do nothing.

**Test endpoints** (registered **only** when installed; each requires the header `X-Test-Token: <WORKFLOW_FAKE_LLM_TOKEN>`
and a loopback client, else 404):

| Method | Path | Body / response |
|---|---|---|
| PUT | `/api/_test/fake-llm/script` | a script (below) → `200 {"rules": n}` |
| GET | `/api/_test/fake-llm/calls` | `[{seq, ts, model, rule_id\|null, messages, tools (normalised schemas), temperature, raised?}]` (ring buffer of 500) |
| DELETE | `/api/_test/fake-llm/calls` | clears → `{"success":true}` |
| PUT | `/api/_test/fake-llm/state` | `{"unavailable": bool}` → `llm_provider.is_configured()` returns `not unavailable` (drives the `llm_unavailable` scenario) |
| PUT | `/api/_test/sandbox` | `{"available": bool}` → overrides `/api/sandbox/status` and the python-route dispatch (drives sandbox-unavailable) |
| GET | `/api/_test/health` | `{"fake_llm": true, "db": "<name>"}`: the harness guard |

**Tool normalisation (review E7)**: the fake passes `tools` through the **same** normaliser the adapters use (extract it
from `_call_anthropic`/`_call_openai_compat` into `_normalize_tools(tools) -> list[dict]` if it is inline) and logs
the normalised schemas, so E2E asserts the real `emit_module` and `write_files` schemas. The adapters additionally
get **contract tests** with recorded Anthropic and OpenAI request/response fixtures (tools as callables and dicts,
multiple tool calls, malformed tool-call repair), independent of the fake.

**Retry testing (review E8)**: `{"raise": "retryable", "status": 529}` raises `FakeLLMError` with `status_code=529`
(so `_is_retryable` is true). In fake mode `_RETRY_BASE_DELAY` is 0 (read once at install). `"raise": "fatal"` raises a
non-retryable error; `"raise": "unmatched"` raises `FakeLLMUnmatched`.

**Script format**:
```json
{"rules": [ {"id": "…", "when": {…}, "times": null, "respond": {…}} ], "default": {"raise": "unmatched"}}
```
- `when` (all present keys must match): `tool` (a tool with that name is offered), `last_role`
  (`user|assistant|tool`), `last_user_matches`, `system_matches`, `last_tool_result_matches`, `any_user_matches`
  (regexes, Python `re.search`), `model_matches`, `nth` (1-based count of calls that matched this rule's `when`).
- `times`: how many times the rule may fire (null = unlimited). The first eligible rule wins.
- `respond`: `{"text": "…"}`, or `{"tool_calls": [{"name", "args"}]}`, or `{"raise": "retryable"|"fatal"|"unmatched", "status"?}`.
- In strings: `@file:<path>` (path relative to the script file) loads a file's contents, and `${ENV_VAR}` is
  interpolated in **all** strings, **including file contents loaded via `@file:`** (review E9). An unknown variable is a
  load-time error.
- `default` must be `{"raise": "unmatched"}` in E2E. Teardown fails the run if the call log has any `rule_id: null`
  (review E10).

**The pinned E2E script** (`apps/workflow-studio/e2e/fixtures/llm-script.json`). Every `emit_module` response declares
`tables` explicitly, and `repair` is scoped to the "broken" scenario (re-review N2):
```json
{"rules": [
  {"id": "flaky", "when": {"tool": "emit_module", "last_user_matches": "__llm_flaky__"}, "times": 2,
   "respond": {"raise": "retryable", "status": 529}},
  {"id": "flaky-ok", "when": {"tool": "emit_module", "last_user_matches": "__llm_flaky__"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "flaky", "source": "@file:modules/hello.py",
     "summary": "Hello.", "tables": []}}]}},
  {"id": "fail", "when": {"last_user_matches": "__llm_fail__"}, "respond": {"raise": "fatal"}},
  {"id": "repair", "when": {"tool": "emit_module", "any_user_matches": "__broken__",
     "last_user_matches": "^Fix these problems in the module you emitted:"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "broken", "source": "@file:modules/broken_fixed.py",
     "summary": "Fixed the syntax error.", "tables": []}}]}},
  {"id": "gen-broken", "when": {"tool": "emit_module", "last_user_matches": "__broken__"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "broken", "source": "@file:modules/broken.py",
     "summary": "x", "tables": []}}]}},
  {"id": "gen-scrub", "when": {"tool": "emit_module", "last_user_matches": "__scrub__"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "refunds", "source": "@file:modules/refunds.py",
     "summary": "Unchanged.", "tables": [{"name": "refunds", "columns": [{"name": "reason", "type": "string", "required": true},
                                                                       {"name": "amount", "type": "number", "required": true}]}]}}]}},
  {"id": "gen-secret-missing", "when": {"tool": "emit_module", "last_user_matches": "__webhook__"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "refunds", "source": "@file:modules/refunds_webhook.py",
     "summary": "Adds a webhook notification.", "tables": [{"name": "refunds", "columns": [{"name": "reason", "type": "string", "required": true},
                                                                                          {"name": "amount", "type": "number", "required": true}]}]}}]}},
  {"id": "gen-refunds", "when": {"tool": "emit_module", "last_user_matches": "__refunds__"},
   "respond": {"tool_calls": [{"name": "emit_module", "args": {"module_name": "refunds", "source": "@file:modules/refunds.py",
     "summary": "Refunds table with create, list and stats routes.",
     "tables": [{"name": "refunds", "columns": [{"name": "reason", "type": "string", "required": true},
                                                 {"name": "amount", "type": "number", "required": true}]}]}}]}},
  {"id": "builder-write", "when": {"tool": "write_files", "last_role": "user", "system_matches": "api\\.refundStats"},
   "respond": {"tool_calls": [{"name": "write_files", "args": {"files": [{"path": "App.tsx", "content": "@file:apps/RefundsApp.tsx"}]}}]}},
  {"id": "builder-done", "when": {"tool": "write_files", "last_role": "tool"},
   "respond": {"text": "Built a refunds dashboard on your typed client."}}
 ],
 "default": {"raise": "unmatched"}}
```
- **Markers, not natural words.** Each scenario's prompt contains exactly one marker (`__refunds__`, `__webhook__`,
  `__broken__`, `__scrub__`, `__llm_flaky__`, `__llm_fail__`) after the human-readable text, e.g. "Store refunds in a
  table … __refunds__". Because of the pinned layout (§10.1), only the prompt is matched, never the source. `repair`
  requires both the repair line **and** `__broken__` anywhere in the user turns, so it can never answer another
  scenario's repair. Any other repair hits `default` → `unmatched`, which fails the run loudly.
- **Fixtures and their table use:**

  | Fixture | Uses tables | Uses secrets |
  |---|---|---|
  | `hello.py` | none | none |
  | `broken.py` (no colon on line 7) / `broken_fixed.py` | none | none |
  | `refunds.py` | `refunds` (declared) | `PAYMENTS_TOKEN` via `${E2E_UPSTREAM_URL}/v1/balance` |
  | `refunds_webhook.py` | `refunds` (declared) | `PAYMENTS_TOKEN`, `REFUNDS_WEBHOOK_KEY` |
- **Seeds per scenario:** T3 seeds the project + `PAYMENTS_TOKEN` (dev). T4 seeds as T3 + the saved `refunds` module +
  table. T10 seeds the project + `refunds` table + saved `refunds.py` + `PAYMENTS_TOKEN` (dev) and **no**
  `REFUNDS_WEBHOOK_KEY`. T14 seeds the project only. T15 seeds the project only.
- `fail` has no `tool` condition, so it also covers the app builder (T24). Fixture files live in
  `e2e/fixtures/modules/*.py` and `e2e/fixtures/apps/*.tsx`; `${…}` is interpolated in `@file:` contents.

---

## 11. API mapping

`P` = project id (resolved from the slug), `A` app, `R` route, `M` module, `T` table, `X` execution, `REL` release.
Changed or new contracts are marked §9/§10.

| UI action | Endpoint |
|---|---|
| Projects grid / create / resolve | `GET /api/projects`, `POST /api/projects`, `GET /api/projects/{slug}` |
| Summary, badges | `GET …/summary` (30 s) |
| Outline | `GET …/modules`, `…/routes`, `…/tables`, `…/apps`, `…/workflows`, `…/secrets/missing` |
| Open module | `GET …/modules/{M}` |
| Generate / Refine | `POST …/modules/generate` (§10.1) |
| Live analysis | `PUT …/modules/{M}?dry_run=true {source, base_sha}`; `POST …/modules?dry_run=true {name, source}` (§9 D7) |
| Save | `POST …/tables` (declared tables) → `POST …/modules` / `PUT …/modules/{M} {source, base_sha}` (§9 B4) |
| Rename module | `PUT …/modules/{M} {name}` (§9) |
| Delete module | `DELETE …/modules/{M}?dry_run=true` → `DELETE …/modules/{M}` (§9 D9) |
| Test saved route | `POST …/routes/{R}/test` (returns `trace` and `error`, §9 G-UI-4, D16) |
| Test draft | `POST …/modules/run-draft` (§10.2) |
| Use as schema | `POST …/routes/{R}/test {apply_schemas:true}` |
| Route settings | `PUT …/routes/{R}[?dry_run=true]` (code routes: schema, description, mode, validate, timeout; preserved across module saves, §9 C8) |
| Used-by | `GET …/routes/{R}/used-by` |
| client.ts / OpenAPI / revisions / diff | `GET …/client.ts?environment=`, `…/openapi.json?environment=`, `…/spec/revisions`, `…/spec/diff?from=&to=` |
| SDK reference / functions / sandbox | `GET /api/sdk/reference` (§9 C14), `GET /api/sdk/functions`, `GET /api/sandbox/status` |
| SDK zip / dev token / broker log | `GET /api/sdk/python.zip`, `POST …/broker-tokens {environment}`, `GET …/broker-calls?execution_id=` |
| Secrets matrix / add / edit / delete | `GET …/secrets`, `POST …/secrets`, `PUT …/secrets/{id}`, `DELETE …/secrets/{id}` |
| Team variables (Workflows → Variables modal) | `/api/variables…`: team rows only (§9 D2) |
| Tables / rows | `GET/POST …/tables`, `GET/PUT/DELETE …/tables/{T}`, `GET …/rows?limit&cursor&filter&sort&count=true`, `POST …/rows`, `PATCH/PUT/DELETE …/rows/{id}` |
| Runs / run sheet / release number | `GET …/runs?limit&before&trigger&status&route_id&environment` (`X-Next-Cursor`), `GET …/runs/{X}`, `GET …/releases/{id}` |
| Create UI | `POST …/apps {name, definition, description≤1000}` → `PUT /api/apps/{A} {definition.studio.route_ids}` |
| Builder load/save/versions | `GET /api/apps/{A}`, `PUT /api/apps/{A}` (files without `api/client.ts`), versions endpoints (existing) |
| Builder chat | `POST /api/ai/app-builder/chat {…, project_id, route_ids}` |
| Builder client / preview token / usage / stubs | `GET …/client.ts?environment=<default>`, `POST …/apps/{A}/preview-token`, `GET /api/apps/{A}/api-usage`, `POST …/routes/stubs` |
| Preview fetches | the app's `fetch('/api/p/<slug>/…')`, resolved against the engine origin + the preview token (§5.2) |
| Publish / unpublish / delete app | `POST /api/apps/{A}/publish {slug, access, access_password?}`, `POST /api/apps/{A}/unpublish`, `DELETE /api/apps/{A}` |
| Published runtime | `GET /a/{slug}`; `/api/p/{slug}/…` with the app token; `POST /a/{slug}/_token` (§9 D3) |
| Release / promote / rollback / diff | `POST …/releases[?dry_run=true]`, `POST …/releases/{REL}/promote[?dry_run=true]`, `POST …/environments/{env}/rollback`, `GET …/releases[/{REL}[/diff?against=active]]` |
| Settings / slug rename / API keys / delete | `PUT /api/projects/{P}[?dry_run=true]`, `POST /api/apps/{A}/publish` (bulk republish), `GET/POST/DELETE …/api-keys`, `DELETE /api/projects/{P}` |
| Workflows | `GET/POST …/workflows`, `GET/PUT/DELETE /api/workflows/{W}`, `POST /execution-stream/{W}`, publish/unpublish |
| Redirect lookups | `GET /api/workflows/{W}`, `GET /api/apps/{A}`, `GET /api/projects/{id}` |
| Test-only | `/api/_test/*` (§10.3) |

### 11.4 Studio configuration

`studio:shared/lib/config.ts` gets `const override = import.meta.env.VITE_WORKFLOW_API_URL`, which wins when set and is
exported as `config.workflowApiUrl`. `backends.workflow` derives from it. There is no Vite proxy; the engine allows
CORS `*` and exposes the headers listed in §9 C1.

---

## 12. Playwright E2E plan

### 12.1 Isolated stack (`apps/workflow-studio/e2e/global-setup.ts`)

1. **Database (review F1)**: `TEST_DB_NAME=workflows_test_e2e_<8hex>`, created with
   `apps/workflow-engine/tests/e2e_stack.py up`. That script reuses `tests/integration/conftest.py:TestDatabase`; its
   `workflows_test_` prefix guard **stays**. It creates the schema and runs `python -m src.db.migrate apply`.
2. **Upstream**: a Node `http.createServer` on `127.0.0.1:<free>`. It echoes `{method, path, headers, body}`, and
   serves `GET /v1/balance → {"available":12840.25}` and `GET /v1/leak → {"echo": <Authorization header>}`, with the same
   value in a response header and in a 500 error text, for the redaction scenario T38. It is exported as `E2E_UPSTREAM_URL`.
3. **Engine with a clean environment (review F2)**: `spawn(venvPython, ['-m','uvicorn','src.main:app','--app-dir',
   ENGINE_DIR,'--host','127.0.0.1','--port',P], {cwd: <fresh tmp dir>, env: ALLOWLIST})`. **No inherited
   environment.** `ALLOWLIST` = `PATH` (venv bin, dirname of the absolute `esbuild` path, `/usr/bin`, `/bin`), `HOME=<tmp>`,
   `TMPDIR=<tmp>`, `LANG=C.UTF-8`, plus: `WORKFLOW_DB_HOST/PORT/USER/PASSWORD/NAME` (the test DB),
   `WORKFLOW_DISABLE_DOTENV=1`, `WORKFLOW_TEST_MODE=1`, `WORKFLOW_BIND_HOST=127.0.0.1`, `WORKFLOW_LLM_PROVIDER=fake`,
   `WORKFLOW_FAKE_LLM_SCRIPT=<abs path>`, `WORKFLOW_FAKE_LLM_TOKEN=<random 48>`, `WORKFLOW_ENCRYPTION_KEY=<generated
   Fernet>`, `WORKFLOW_PUBLIC_TOKEN_SECRET=<random>`, `SEED=0`, `E2E_UPSTREAM_URL`, and sandbox, SDK-cache and socket dirs
   under `<tmp>`. No `ANTHROPIC_*`, `OPENAI_*`, `GOOGLE_*` or `GEMINI_*` variable is set. The cwd has no `.env`, and dotenv is off anyway.
   `esbuild` is resolved once (`which esbuild` → realpath); `bwrap` is `/usr/bin/bwrap`.
4. **Studio**: `npx vite --port <free ≥5180> --strictPort` with only `PATH`, `HOME`, `VITE_WORKFLOW_API_URL=http://127.0.0.1:<engine>`.
5. **Guards** (abort setup on failure): engine port ≠ 8000; Vite port ≠ 5173; DB name matches `^workflows_test_e2e_`;
   `GET <engine>/api/_test/health` (with the token) returns `{fake_llm:true, db:<name>}`, which proves the fake mode and the DB;
   `GET <engine>/api/projects` lists only `proj_default`.
6. **Browser (review F7)**: `npx playwright install chromium` (the build pinned for 1.62.1). If that is unavailable,
   `/usr/bin/chromium` is used with a printed warning. `@playwright/test@1.62.1` is added to devDependencies, with the script
   `"test:e2e": "playwright test -c e2e"`.
7. Teardown kills both process groups, asserts the fake-LLM call log has **no `rule_id:null`** entries, and drops the DB
   unless `KEEP_E2E=1`.

Short app-token TTL for T27: in test mode the app settings accept `token_ttl_s` (test-only field, ignored unless
`WORKFLOW_TEST_MODE=1`), set to 5 by the T27 seed. This is part of the §9 D3 contract.

### 12.2 Independence (review F8)

`workers: 1`, but **every scenario seeds its own state through the API** (`e2e/fixtures/seed.ts`): `seedProject()`,
`seedSecret(p, key, value, envs, hosts)`, `seedTable(p, name, columns)`, `seedModule(p, name, file)`,
`seedApp(p, name, files, routeIds)`, `seedPublished(p, a, slug)`, `seedRelease(p)`, `seedWorkflow(p, def)`. Each test
uses a unique slug `e2e-<test>-<rand>`. `npx playwright test -g T14` runs alone. Fonts are blocked with `page.route`, and
the fallback system font is accepted.

### 12.3 Network, console and layout policy (review F3, F6)

- **Network**: `page.on('response')` covers every request to the engine origin and the preview and published pages.
  **Any 5xx fails** the test, except router responses whose 5xx status the step declared. **Any 4xx fails** unless the step
  declared it with `expectStatus({method, url: /…/, status})`, scoped to that step. A declared status that never happened
  also fails. `/test` and run-draft return HTTP 200 with the inner status, so the inner 4xx/5xx need no declaration.
- **Console**: fail on `console.error`, `console.warn`, `pageerror` and `Warning:` messages, in the studio **and** the
  preview iframe **and** the published app. The allow-list is a file (`e2e/console-allow.ts`) and starts **empty**.
  `useAppBuilderChat.ts:110`'s `console.warn('[App Builder]…')` must become a UI error state instead.
- **Layout**: `assertNoClipping(page)` checks, for every `[data-testid$="-panel"]`, that
  `scrollWidth <= clientWidth + 1` (unless it is marked `data-scroll-x="allowed"`: the code editor and body boxes). It also
  checks that every visible `button` and `[role=tab]` is inside its panel's bounding box, with a non-zero intersection
  ≥ 90% of its own area. It runs at the end of every UI scenario and at 1280/1024 in T45. **It would have caught the
  clipped Send button in v1.**

### 12.4 Scenarios (46: T1–T45 plus T27b). Each one asserts UI state **and** backend state through the API.

| # | Scenario | Key assertions |
|---|---|---|
| T1 | Projects home → create project | First-run shown; card on `/projects`; `GET /api/projects` has it |
| T2 | Setup step 1: secret | `PAYMENTS_TOKEN` (dev+prod, host 127.0.0.1); matrix `set/set`; API `value:null`; **the value string is absent from `document.body.innerText` and from every API response body** |
| T3 | Generate (prompt "Store refunds … __refunds__") | Phases; draft in the editor; result "3 routes · self-test **2/2**" (`GET /refunds`, `GET /refunds/stats`); tables row "Create table refunds"; `GET …/modules` empty and **`GET …/tables` lacks `refunds`**; call log: `rule_id` `gen-refunds`, the system prompt contains `http_request`, `PAYMENTS_TOKEN` and the existing tables, the tools contain the `emit_module` schema, no message contains the secret value; self-test runs have `trigger.type=generate_self_test` |
| T4 | Secret scrub | Paste the secret value into a comment in the editor, Refine with the prompt "Keep everything as is __scrub__" → `rule_id` `gen-scrub`; the call log (all messages, including the `Current module` message) lacks the value and contains `***`; the `secret_scrubbed` warning is shown |
| T5 | Draft test GET /refunds/stats | 200, `count:0` (virtual table); upstream saw `Authorization: Bearer <value>`; the trace shows the placeholder `{{ $vars.PAYMENTS_TOKEN }}`, never the value; run `trigger.type=draft_test` |
| T6 | Draft write before save | POST /refunds → inner 502, `error.code=table_not_found` card → *Create table now* → `GET …/tables` has `refunds` → resend → inner 200 |
| T7 | Save | Create-table checkbox → POST tables then modules; 3 routes `source:"code"`; outline settles; client.ts tab has `createRefund`, `listRefunds`, `refundStats`; Overview lists them; `handler.line` used by "Open in editor" |
| T8 | Saved-route tests | POST /refunds `{reason:"damaged",amount:84.5}` → inner **200** (dict return) with `id`; without amount → inner 422 shown amber; GET stats `count:1`; Runs shows `route_test`; run sheet trace, `metadata.request` shown |
| T9 | Use as schema | Disabled after a 422; enabled after a 200; applied → spec revision +1; `response_schema.properties.count` |
| T10 | Missing secret widget | Seeds per §10.3. Refine with "Notify the webhook on each refund __webhook__" → result `attempts:1`, call-log rule ids exactly `[gen-secret-missing]` → widget `REFUNDS_WEBHOOK_KEY` missing → popover → saved → widget gone; API has the key |
| T11 | Syntax error then fix | Broken line → live dry-run diagnostic at line 7; ⌘S → `expectStatus 422`; state "1 error, not saved"; `GET …/modules/{M}` source unchanged; `POST …/modules?dry_run=true` created nothing (module count unchanged); fix → saved |
| T12 | Autocomplete | `studio.nodes.ht` → item **label** `http_request`, **detail** begins `(*, method, url`, **type tag** `HttpRequest`; Enter inserts the snippet; `studio.db.table("` lists `refunds`; `studio.secret("` lists `PAYMENTS_TOKEN` |
| T13 | Route conflict & invalid decorator | Duplicate `POST /orders` (owned by a seeded workflow) → `expectStatus 409`, diagnostic on the decorator line naming the workflow, `conflicts[0].handler.name` asserted; `auth=AUTH` → `expectStatus 422 invalid_decorator` diagnostic |
| T14 | Repair loop | Prompt "Make a module __broken__" → status `repairing` → result `attempts:2`; call log rule ids `[gen-broken, repair]`; the repair message starts with "Fix these problems" and names line 7 |
| T15 | LLM failure & retry | Prompt "x __llm_fail__" → composer error, editor unchanged, the stream ended with `error` + `done`; `__llm_flaky__` → success; the call log shows 3 calls (`flaky`, `flaky`, `flaky-ok`) |
| T16 | Concurrent generation | Two pages generate in one project → second gets `expectStatus 409 generation_in_progress` → "Another generation is running" |
| T17 | Concurrent edit | Pages A and B open the module; A saves; B saves → `expectStatus 409 module_changed` → dialog → Reload theirs shows A's code; draft restore with a stale `base_sha` shows the banner |
| T18 | Traceback link | Module raising on line 12 → run-draft `error.line=12` → "Go to line 12" moves the cursor to line 12 (the CM state's line) |
| T19 | Data view | Rows from T8 (seeded) visible; Add row with `amount:"abc"` → `expectStatus 422 row_invalid` → field error; valid row saved (API count +1); a cell edit persists after reload |
| T20 | Create UI dialog | 3 refund routes checked → Create → app exists with `definition.studio.route_ids` (3); chat body `project_id`=P and `route_ids` (3); call-log system prompt marks those 3 "← focus"; app description ≤ 1000 |
| T21 | Builder typed client | Chat "Wrote App.tsx"; `GET /api/apps/{A}` files lack `api/client.ts`; `api-usage` has `refundStats` `status:"ok"` with file and line; panel row ✓ appears **only after** the preview's 2xx |
| T22 | Route picker | Change to 2 routes → `PUT /api/apps/{A}` stored; the next chat body has the 2 ids; **then an AI turn (auto-save) and a manual save → reload** → the picker still shows the 2 routes and `GET /api/apps/{A}` has `definition.studio.route_ids` (2) (N4) |
| T23 | Preview calls the backend | The bridge request URL's origin is `http://127.0.0.1:<engine>`; `Authorization: Bearer` present; `refund-count` = API count; the form submit inserts a row (API count +1); `preview-token.expires_at` ends with `Z` |
| T24 | Builder stream error | `__llm_fail__` in the builder → error message + Retry; the stream had `error` then `done`; no console warning |
| T25 | Publish | Pre-flight lists `PAYMENTS_TOKEN` missing in prod (seeded dev-only) → Add to prod → row clears; access Public; publish → URL; the published page shows the count; its `/api/p/…` calls return 200 with an app token; 0 console messages |
| T26 | Slug taken | Publish a second app with the same slug → `expectStatus 409 slug_taken` inline |
| T27 | Token refresh | App with `token_ttl_s:5`. The step declares `expectStatus({url:/\/api\/p\//, status:401})` (N8). After expiry, click Refresh in the app → exactly **one** 401 `token_expired`, then `POST /a/{slug}/_token` 200, then the retried call 200; data still loads; no error surfaces in the UI |
| T27b | Token refresh is authorised | Password app: `POST /a/{slug}/_token` without the unlock cookie → `expectStatus 401 unlock_required`; after `_unlock` → 200. Private app → `expectStatus 404` (N3) |
| T28 | Unpublish | Unpublish → `GET /a/{slug}` → `expectStatus 404` |
| T29 | Release + promote | Release sheet: dry run blocked (`secrets_present`) → inline add → Re-run → pass → snapshot + promote → prod #N; edit the module (add a field), save → the published response lacks it (open page, **same token**); promote again → it has it, **without reloading the page** (token bound to the app) |
| T30 | Rollback | Roll back to the previous release → the field is gone; toast lists warnings |
| T31 | Breaking-change dialog | Remove `GET /refunds/stats` (used by the app) → dialog lists `App.tsx:<line>` → Save anyway → `api-usage` `missing` |
| T32 | Auth loosening | Change `auth="api_key"` → `"none"` → dialog shows `auth_loosened` → must confirm |
| T33 | Delete module | Confirm lists 3 routes + "used by" → deleted; routes gone; app usage `missing`; the `refunds` table still exists |
| T34 | Route fields preserved | Set a description via the route settings → re-save the module → the description is unchanged |
| T35 | Environment switch | prod → Runs shows only prod runs (seeded via the router), client.ts tab fetches `environment=prod`, Secrets highlights the prod column; route test still sends `environment: dev` |
| T36 | Settings | (a) Slug rename + republish → the published app works at the new base; the old `/api/p/<old>` 404 is declared. (b) **`GET /api/variables/{project-secret-id}` → 404** (D2). (c) API key: shown once, the list shows only the prefix; the key gets 200 on an `api_key` route; revoke → `expectStatus 401`. (d) Delete a non-empty project → `expectStatus 409 project_not_empty`, listed inline |
| T37 | Runs pagination | Seed 60 router calls → page 1 has 50; "Load more" uses `X-Next-Cursor` (readable cross-origin) → 10 more, no duplicates; an unknown cursor → empty page |
| T38 | Redaction | Route calling `/v1/leak` with the secret → body, headers, error message, run sheet and trace contain `***`; `innerText` and all API bodies lack the value |
| T39 | Old URLs | `/editor?workflowId=W` → `/p/<slug>/workflows/W`; `/builder?appId=A` → `/p/<slug>/apps/A`; `/editor` → `/p/default/workflows/new`; `/builder` → create-UI dialog; unknown id → `/projects` + toast |
| T40 | Workflows page | Seeded `Start → Set {answer: {{ 20 + 22 }}}` → list row → open → Run → success shows `42`; Runs shows `trigger.type=manual` |
| T41 | llm_unavailable | `PUT /api/_test/fake-llm/state {unavailable:true}` → Generate → `expectStatus 503` → "AI isn't configured" state with templates; Write it myself works; restore the state |
| T42 | Sandbox unavailable | `PUT /api/_test/sandbox {available:false}` → red badge; Send disabled with the explanation; saving still works; restore |
| T43 | First-run gating & completion | Steps 3–4 show "needs a route" until a module exists; after secret → module → test → app the guide is replaced by Overview; `?setup=1` shows 4/4 |
| T44 | Theme | Toggle dark → `getComputedStyle(body).backgroundColor` = dark `--background` (`#111111`, `index.css`); a panel's background = dark `--card`; no console messages |
| T45 | Responsive | At 1280×800 and 1024×768 on Overview, Backend (with a response shown), Builder, Runs sheet: `assertNoClipping` passes; **Send is fully visible and clickable**; the outline is the rail at 1024 |

### 12.5 Running

```bash
cd apps/workflow-studio
npm run test:e2e                  # full suite, isolated stack
KEEP_E2E=1 npx playwright test -c e2e -g T14   # one scenario, stack kept
```
The live `:8000` engine, `:5173` studio and `workflows` database are never touched (§12.1 guards).

---

## 13. Build order (security first; the review's "re-number")

| Slice | Content | Gate |
|---|---|---|
| **B0 (backend, before any UI)** | §9 D1, D2, D4, D7, C1 (security + core correctness) | engine tests green |
| **0 (studio foundation, review G1)**: about 1 day | Hoist `queryClient` into router context; `shared/api/http.ts` (`ApiError` with `status`, `code`, `details` from `detail`, the 5 error shapes); query keys + invalidation helpers (UI-INTEGRATION §3.3/§3.5 designs); `config.workflowApiUrl` (§11.4); tokens + contrast test (§7.2/7.3); primitives (§7.1); nested-button fixes (§1.1); per-project env store (G3); E2E harness with T1, T39, T44 | harness green, 0 console messages |
| 1 | Shell, Overview, Projects home, redirects | T1, T39, T43 (partial) |
| B1 (backend) | §9 B1/B4/C7/C8/C9/D9/D16/C14/C16/G-UI-4/G-UI-10, D5, D11 | engine tests |
| 2 | Backend view, manual path: editor, save, diagnostics, test panel, trace, Reference, autocomplete, dialogs | T7–T9, T11–T13, T17, T18, T31–T34 |
| B2 (backend) | §10.3 fake LLM → §10.1 generate → §10.2 run-draft | engine tests |
| 3 | Composer, drafts, tables-on-save, secret widgets, states | T3–T6, T10, T14–T16, T41, T42 |
| 4 | Data, Secrets, Runs (+ C2, C3, D6) | T2, T19, T35, T37, T38 |
| B3 + 5 | C5, C6, C11, C13 + Create-UI dialog, builder panel, route picker, preview bridge | T20–T24 |
| B4 + 6 | C4, D3, D10, B7 + Publish, Release, Settings | T25–T30, T36 |
| 7 | Workflows list, responsive and dark pass | T40, T44, T45 |

---

## 14. Review resolution (`UI-DESIGN-REVIEW.md`, 81 findings)

None are rejected. "Accepted (no change)" is used only where the review itself asks for no change.

| # | Sev | Resolution (what changed, where) |
|---|---|---|
| A1 | minor | Accepted (no change): 4 pages kept (§2.1). |
| A2 | major | A builder **route picker** popover is persisted in `definition.studio.route_ids` and sent on every chat turn; apps with no routes show "Attach routes" (§5.2, `builder.png`). The Create-UI dialog writes the same field (§5.1). Covered by E2E T22. |
| A3 | major | App tokens are bound to the app + refresh endpoint (§9 D3, §5.3); T27/T29. |
| A4 | minor | SDK reference from one source with the correct `Response` signature and secret rule (§4.6, §4.5; C14/C15). |
| B1 | **blocker** | The generator **declares** tables (`emit_module.tables`); `analysis.tables` has columns; the result row has "Create table on save" (default on); Save does `POST …/tables` before the module; run-draft and self-test use `virtual_tables` (reads empty, writes `table_not_found` → "Create table now") (§4.2, §4.4, §10.1, §10.2, §9 D5; `backend.png`, `states.png`). T3 asserts the table is absent after generating; T6/T7 assert it exists after the fix or the save. |
| B2 | major | §6: specs for delete/rename module, delete app, unpublish, app settings, API keys, the Workflows list (columns, actions, empty state) and delete workflow; `dialogs.png`, `workflows.png`, `settings.png`; T28, T33, T36. |
| B3 | major | D11 + copy in the Test panel, Create-UI footer, Data header, Publish pre-flight and environment-switch tooltip (§2.4, §3.4, §4.7, §5.1, §5.3). |
| B4 | major | `base_sha` on PUT → `409 module_changed`; a dialog with Diff/Reload/Overwrite; restored drafts carry `base_sha` and show the stale banner (§4.2 step 6–7, §9 B4; `dialogs.png`); T17. |
| B5 | major | A draft and Discard are each one CM transaction; the editor is never remounted; new CodeEditor props and Compartments; `buildTheme` reads the new tokens (§4.2 step 3, §7.1). |
| B6 | major | A per-view state table with copy and test ids (§8.1); mockups of the streaming composer, `llm_unavailable`, syntax error and draft-test failure (`states.png`), the concurrent-edit, breaking-change and delete dialogs (`dialogs.png`), and empty/pruned Runs copy (§3.6). |
| B7 | major | Slug rename confirm: "Published apps break until republished", the affected list, "Rename and republish N apps" bulk republish (§3.7, `settings.png`); T36a. |
| B8 | minor | Decided: steps 3–4 are **gated** ("needs a route", with the reason); step 3 is done via `trigger.type ∈ {route_test, draft_test}` (§3.3, `project-first-run.png`); T43. |
| B9 | minor | Unsaved module URL `~new`; names `new`/`generate`/`default` are reserved and rejected (§2.2, `dialogs.png`). |
| B10 | minor | D12 defines "missing" vs "not set", with distinct copy and colours; outline dot + "1 missing"; the Overview legend (§3.2, §3.5; `project.png`, `secrets.png`). |
| C1 | major | §9 C1 `expose_headers`; T37 pages via `X-Next-Cursor`. |
| C2 | major | `trigger.type` taxonomy; the Runs filter uses it; workflow runs are stored as `manual`; first-run step 3 and T8/T40 assertions updated (§3.6, §9 C2). |
| C3 | major | Redacted request/response stored in run metadata (16 KB cap); the release number via `GET …/releases/{id}` (§3.6, §9 C3; `runs.png`). |
| C4 | major | `409 slug_taken`; the UI always sends `access`, default Public, with a note for Private (§5.3, §9 C4; `publish.png`); T26. |
| C5 | major | The builder SSE always ends `error`→`done`; the studio drops `plan` and treats a stream without `done` as an error (§5.2, §9 C5); T24. |
| C6 | major | Backend contract (§9 C6) **and** the first-turn stub removed from the mockup and copy (`builder.png`). |
| C7 | major | The full error contract and UI per code, including a null column and a whole-line diagnostic (§4.4, §9 C7); T13. |
| C8 | major | Module save preserves route-level fields (§9 C8); T34. |
| C9 | major | One dry-run shape (§9 C9), used by BreakingChangesDialog and live analysis (§4.2, §4.3). |
| C10 | major | `/test` semantics: always the draft environment, Use-as-schema only on 2xx, null `execution_id` hides the trace link (§4.7, §2.4, §9 C10); T9. |
| C11 | major | api-usage detects `api.<op>(` and adds `stub` (§9 C11); ✓ only on an observed 2xx (§5.2); T21. |
| C12 | major | Preview bridge: resolve against `config.workflowApiUrl`, then inject only for engine-origin `/api/p/` (§5.2, §11.4); T23 asserts the origin. |
| C13 | minor | `expires_at` in UTC with `Z` (§9 C13); T23. |
| C14 | major | `GET /api/sdk/reference` generated from the SDK; the correct `Response(status=200, body=None, headers=None)`; one source for UI and LLM (§4.6, §10.1). |
| C15 | minor | Rule reworded: f-strings OK, never transform (§4.5); in the generation context (§10.1). |
| C16 | minor | Uses `python_names` now; G-UI-5 rescoped to `required`/`default` per parameter (§4.6, §9). |
| C17 | minor | Noted: the outline and first-run read `GET …/modules` (§3.1). |
| C18 | minor | Description truncated to 1000; the full prompt goes via URL/chat (§5.1); T20. |
| C19 | minor | `422 unknown_environment` (§9 C19). |
| D1 | **blocker** | §9 D1 (literal-only decorators, `422 invalid_decorator`), in B0 before any UI; the rule is in the generation context; T13. |
| D2 | **blocker** | §9 D2 (team-scoped `/api/variables/{id}`), in B0; the Secrets view never edits project rows through it (§3.5); T36b. |
| D3 | **blocker** | §9 D3 (app-bound token, refresh endpoint, `token_expired`); the generated client refreshes once (§5.3); T27, T29. |
| D4 | **blocker** | §9 D4 (redact every outward string, including headers, errors, SSE and run-draft), in B0; T38 plus T2/T5 value-absence checks. |
| D5 | **blocker** | §9 D5 (`read_only` + `virtual_tables` claims, an exact deny list, the `read_only` error), used by self-test (§10.1 step 4); T3, T5. |
| D6 | major | §9 D6 (retention on all paths, cascade, empty page for an unknown cursor); Runs copy "recent runs (keeps 100)"; T37. |
| D7 | major | §9 D7 (`POST` dry run honoured; unsupported `dry_run` → 422); the client-side regex path removed (§4.3); T11 asserts the module count is unchanged. |
| D8 | major | §9 D8 (snapshot modules for pinned PythonStep); engine test. |
| D9 | major | §9 D9 (`DELETE ?dry_run=true`) drives the delete confirm (§6.1, `dialogs.png`); T33. |
| D10 | major | Every release code is documented with its UI; blocked releases are badged in history; diff filtered; no "active" on draft environments; only apps with no source at all fail checks, draft-only apps are versioned into the release (§3.8, `release.png`); T29/T30. |
| D11 | major | `auth_loosened` is breaking and requires confirmation (§9 D11, `dialogs.png`); T32. |
| D12 | minor | §9 D12. |
| D13 | minor | §9 D12–D15 row (root type change breaking). |
| D14 | minor | §9 D12–D15 row (usage cached at version creation). |
| D15 | minor | §9 D12–D15 row (Code-node line offset). |
| D16 | major | Structured `error{type,message,traceback,line,module,code}` from run-draft and `/test`; the UI links `error.line` (no stderr regex) (§4.4, §3.6, §9 D16; `runs.png`, `states.png`); T18. |
| D17 | minor | §9 D12–D15/D17 row (public-app `error_code`/`details`). |
| E1 | major | SSE framing fixed: all `event: message`; exactly one terminal `result`/`error` then `done`; JSON-mode status table; `llm_unavailable` is pre-stream 503 only (§10.1). |
| E2 | major | Limits: prompt 8 000, source 200 KB, conversation last 6, 3 attempts, 120 s, self-test caps, one in-flight per project+actor (409), cancel on disconnect; each with an engine test (§10.1). |
| E3 | major | `conflicts[{kind:"module_name"}]` + the repair message renames it; an editable name chip in the result row (§10.1, §4.1). |
| E4 | major | An outbound secret scrub before every `call_llm` (all envs, all variants); `secret_scrubbed` warning; self-test output never sent to the LLM (§10.1); T3/T4. |
| E5 | major | run-draft spec: route resolution, other modules as saved, environment, real writes, virtual tables, timeout, size limits, `inferred` never persisted, error shape, `trigger.type`, HTTP errors (§10.2). |
| E6 | **blocker** | The fake is activated from `os.environ` only, plus test flag + `workflows_test_` DB + loopback bind + ≥32-char token + dotenv disabled; startup refuses otherwise; `/api/_test/*` only then, token + loopback; engine tests for each refusal (§10.3). |
| E7 | major | The hook stays at `_call_llm_once`; the fake uses the adapters' `_normalize_tools` and logs the schemas; adapter contract tests with recorded fixtures (§10.3). |
| E8 | major | `raise: retryable` with `status`, `nth`, `times`; backoff 0 in fake mode; T15 exercises retry (3 calls) (§10.3). |
| E9 | major | An explicit `repair` rule matching the fixed first line of the repair message; interpolation applies to `@file:` contents; the full script is pinned (§10.3); T14. |
| E10 | minor | Default `raise: unmatched`; teardown fails on any `rule_id:null` (§10.3, §12.1). |
| F1 | major | DB `workflows_test_e2e_<hex>` through the existing guard (§12.1). |
| F2 | major | Engine spawned with an allowlisted env, no provider keys, tmp cwd, dotenv disabled, tmp dirs; the health guard proves fake + DB (§12.1). |
| F3 | major | Per-panel clipping + in-panel button checks, a 4xx/5xx network policy with `expectStatus`, and failure on `console.warn` (§12.3). The v1 clipped Send is fixed (§4.7, `backend-1024.png`). |
| F4 | major | Fixed: self-test 2/2 (T3); POST returns 200 (T8); autocomplete label/detail/type (T12); workflow runs `manual` (T40); dark bg = `--background #111111` (T44); T20 asserts the server marked routes "← focus". |
| F5 | major | Added: env switch T35, settings T36, delete module T33, unpublish T28, `llm_unavailable` T41, sandbox unavailable T42, concurrent edit T17, tables T3/T6/T7, token refresh T27, traceback T18, redaction T38. None omitted. |
| F6 | major | Every nested-button site listed and fixed in slice 0, with a grep audit (§1.1). |
| F7 | minor | Pinned Playwright Chromium, falling back to the system browser with a warning; `esbuild` resolved to an absolute path (§12.1). |
| F8 | minor | API seed helpers; every scenario is runnable alone (§12.2). |
| G1 | major | Slice 0 (≈1 day) explicitly builds the `queryClient` hoist, `ApiError`, query keys and invalidations; the intro states they don't exist (§13, header). |
| G2 | minor | Promote `PillTabs`, wrap Base UI tabs/toggle-group/checkbox/switch, generalise `ConversationEmptyState`; no parallel primitives (§7.1). |
| G3 | minor | The env store becomes per project, default `project.default_environment`; `api/client.ts` is stripped from saves (§6.6, §5.2). |
| G4 | minor | `tokens.css` header corrected: "copy of the `index.css` theme variables, **omitting** the 92 `--node-*`/`--sticky-*` tokens". |
| H1 | major | New mockups: `secrets.png`, `data.png`, `runs.png` (list + sheet), `publish.png`, `release.png` (blocked), `settings.png` (slug + API keys), `states.png`, `dialogs.png`, `workflows.png` (list + ⌘K), `builder-1024.png`, `create-ui-dark.png`, `builder-dark.png`, `secrets-dark.png`, `runs-dark.png`. |
| H2 | major | Below 1360, the request bar and Send go on two rows (Send full width); JSON wraps and scrolls inside its box; trace detail truncates with a tooltip; Diff is in ⋯ (§4.1, §4.7, §8.3; `backend-1280.png`, `backend-1024.png`). |
| H3 | major | Light method colours darkened (all ≥ 4.59:1 on their tint); comment 4.2:1; gutter 3.5:1; `--warning-text`/`--ai-text` added; a contrast unit test (§7.2, §7.3). |
| H4 | minor | Keywords magenta; PATCH teal; stub = dashed amber pill (§1.3, §7.2). |
| H5 | minor | The 1024 rail shows view icons only, with module/table flyouts (§3.1, `backend-1024.png`). |
| H6 | minor | One dataset in `shell.js`: Payments Ops = 2 modules, 5 routes, 2 tables, 1 app, 12 runs · 24h; ✓ only on an observed 2xx; `331 ms` format; "· 24h" everywhere; API keys in every outline. |
| H7 | minor | One primary on Overview; the Apps empty state is a text link and `New app` is hidden while empty (§3.2). |
| H8 | minor | The Default-project card lists its workflows like the other cards list routes; ⋯ menus on every card (`home.png`). |
| H9 | minor | Dark screenshots added for Create UI, Builder, Secrets and Runs. |

### 14.1 Re-review (v2): the 8 partial findings, now resolved

| # | What changed (v2.1) |
|---|---|
| B10 | The health rule is defined in §3.2 (a referenced secret missing in `live_environment` → Degraded). The outline count includes draft-referenced keys (§3.1). Mockups aligned: Payments Ops is "Degraded · 1 missing secret" on `home.png` and `project.png`; the outline shows "Secrets · 1 missing" in every saved or draft screen, including `backend.png`/`backend-dark.png` (one change in `shell.js`). |
| C2 | `runs.png`/`runs-dark.png` relabel the router rows `app` (taxonomy §9 C2); no `api` trigger type is shown anywhere. |
| D3 | `_token` authorisation rules (public / password + unlock cookie / private → 404 / rate limit) were added to §9 D3; T27b. |
| D10 | `release.png`: the dev history rows show "snapshot" only; there is no rollback on a draft environment. |
| E5 | run-draft: the timeout is HTTP 200 with inner 504 + `error.code:"timeout"`; route auth is not enforced for drafts and the Test panel says so (§10.2). |
| E9 | The rule set is deterministic: a pinned message layout (§10.1), explicit markers, `repair` scoped with `any_user_matches`, explicit `tables` in every response, a fixture/seed table (§10.3); T10 and T14 assert the exact `rule_id` sequence. |
| H1 | `workflows.png` now shows the list itself (populated, ⋯ menu with Delete blocked, empty state); ⌘K moved to `command-palette.png`. |
| H6 | `data.png` uses integer row ids, no user `created_at` column ("created · row metadata"); the `backend.png` tables row declares only reason/amount; "Use as schema" is disabled for a draft route (tooltip "Save the module first"); the B10 items above. |

### 14.2 Re-review (v2): new findings N1–N12

| # | Sev | Resolution |
|---|---|---|
| N1 | major | §10.1 "Message layout (pinned)": system → `Current module <name>.py:` source message + `OK.` → conversation → the prompt verbatim as the last user message; an engine test asserts the layout. T4 has the literal prompt "Keep everything as is __scrub__" and the rule `gen-scrub`. |
| N2 | major | Scenario markers (`__refunds__` …) replace natural-language matches; `repair` requires `__broken__`; every `emit_module` response declares `tables`; the fixture/table/secret table and per-scenario seeds are in §10.3; T10 asserts `attempts:1` and `[gen-secret-missing]`; T14 asserts `[gen-broken, repair]`. |
| N3 | major | §9 D3: `_token` → public issue, password needs the unlock cookie (`401 unlock_required`), private/unpublished → 404, 30/min per-IP → 429; same TTL and rotation; T27b. |
| N4 | major | Two guards: the engine keeps `draft_definition.studio` when an incoming `definition` omits it (§9 N4 row), and one studio `saveApp()` always merges `studio` (§5.1 step 2); T22 now covers an AI auto-save + manual save + reload. |
| N5 | minor | "Cancel it" was removed; `generation_in_progress` offers Retry only (§4.4). |
| N6 | minor | §10.2 Timeout (HTTP 200, inner 504) and Auth (not enforced for drafts, noted in the Test panel). |
| N7 | minor | Test mode is refused unless the DB name starts with `workflows_test_` (also gates `token_ttl_s`); `WORKFLOW_BIND_HOST` is documented as advisory, with the loopback middleware as the enforcement; the generation lock is per project + `X-User-Id` ("local" when absent) and is best-effort per client (§10.3, §9 D3). |
| N8 | minor | T27 declares the expected `/api/p/` 401 and asserts exactly one, followed by the refresh. |
| N9 | minor | §3.4 inlines the filter grammar (`$eq $ne $gt $gte $lt $lte $in $contains $exists`, `$and/$or`). |
| N10 | minor | §6.1 delete copy is conditional on whether prod has a release; `dialogs.png` updated. |
| N11 | minor | All leftover mockup data errors were fixed in one pass (`shell.js`, `runs`, `release`, `data`, `backend`, `home`, `project`, `create-ui`); all 25 PNGs re-rendered. |
| N12 | minor | A separate `workflows.png` (list, blocked Delete, empty) plus `command-palette.png`. |

---

## Appendix A — mockups (sources in `design-mockups/`, PNGs in `screenshots/ui-design/`)

| Screen | Source | PNGs |
|---|---|---|
| Projects home | `home.html` | `home.png` |
| Overview (populated) | `project.html` | `project.png`, `project-dark.png` |
| First run | `project-first-run.html` | `project-first-run.png` |
| Backend: draft, autocomplete, secret widget, tables row, test + trace | `backend.html` | `backend.png`, `backend-dark.png`, `backend-1280.png`, `backend-1024.png` |
| Backend states: streaming, llm_unavailable, syntax error, draft-test failure | `states.html` | `states.png` |
| Dialogs: breaking change, delete module, concurrent edit + new module | `dialogs.html` | `dialogs.png` |
| Data view | `data.html` | `data.png` |
| Secrets matrix | `secrets.html` | `secrets.png`, `secrets-dark.png` |
| Runs + run sheet | `runs.html` | `runs.png`, `runs-dark.png` |
| Create UI dialog | `create-ui.html` | `create-ui.png`, `create-ui-dark.png` |
| Builder (route picker open) | `builder.html` | `builder.png`, `builder-1024.png`, `builder-dark.png` |
| Publish dialog (pre-flight + success) | `publish.html` | `publish.png` |
| Release sheet (blocked promote) | `release.html` | `release.png` |
| Settings sheet (slug rename + API keys) | `settings.html` | `settings.png` |
| Workflows list (populated, Delete blocked, empty) | `workflows.html` | `workflows.png` |
| Command palette | `command-palette.html` | `command-palette.png` |

Shared: `assets/tokens.css` (theme variables copied from `index.css`, omitting the `--node-*`/`--sticky-*` tokens),
`assets/studio.css` (component layer, the proposed tokens, responsive rules), `assets/shell.js` (one dataset for the top bar and outline).
