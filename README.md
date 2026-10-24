# New features: workflow Page editor, engine loop upgrades, Notebook

This repo is the working copy. Compare it with your `main` and bring the changes over. This README covers:
- what each feature is and how it looks;
- where every file lives;
- what to wire up;
- how to check that each feature works.

The diff baseline is commit `4f776cb` (2026-10-21). Everything listed below was added or changed after it.

| Feature | Code | Tests | Mostly |
|---|---|---|---|
| 1. Workflow editor: Page view + Canvas (studio) | ~10.8k lines | ~2.9k | new folders |
| 2. Engine: loops, Poll, checks, approvals per item, smaller fixes | ~5.5k | ~3.8k | edits to existing files |
| 3. Notebook (studio + engine + jobs-service) | ~13.7k | ~5.1k | new folders |

Only about 650 existing lines were deleted in total. For the new folders, copying them in is enough. For edits to shared files, apply what each change is for, because your copies of those files have drifted.

---

## How to bring it over

1. **Engine first.** Copy the new engine files, then make the edits to shared engine files listed in §2 and §3.4.
   - Run the 3 new migrations: `python -m src.db.migrate`.
   - Run the targeted engine tests in §5.
2. **Studio next.**
   - Copy `src/shared/glass/`, `src/features/workflow-editor/glass/`, `src/features/workflow-editor/components/read-view/` and `src/features/notebook/`.
   - Make the shared-file edits in §1.4 and §3.4.
   - Run `npm install`; this adds `@dagrejs/dagre`.
   - `npx tsc -b` must show 0 errors.
3. **jobs-service.** Apply the take-over changes (§3.3). They are only needed for browser take over in the notebook.
4. **Check by using it.** Run the API scenarios in §5, open the pages, then run the e2e specs for each feature once.

Keep engine changes additive. Every new setting is optional, and its default is the old behaviour. Old workflows and old stored runs must load and run unchanged.

---

## 1. Workflow editor: Page | Canvas

The editor at `/workflows/$id` has **two views**:

- **Page** is for business and ops readers. The workflow reads as a numbered document of plain sentences with editable value tokens. It works fully without AI.
- **Canvas** is for builders and IT: the n8n-style graph, now in glass, with an n8n-style node sheet.

JSON lives in the **⋯** menu: View definition, Definition side by side, Import from JSON. The old editor is still available at `?legacy=1`.

### 1.1 Page view: what it does

- **Numbered steps.** Each step has a mono kind label (`IF`, `SWITCH`, `LOOP`, `HTTP`, …) and a sentence written from the node's own parameters. There is one template per node type in `pageSentences.ts`.
  - Conditions read as plain words: "amount is over 500".
  - Some example sentences: splitOut reads "Split the list X into one item per entry", and NoOp reads "Carry on".
- **Value tokens.** A dotted underline marks a value you can edit. Click it to type a fixed value, or pick a field from an earlier step (`TokenPicker.tsx`). Edits go through the same `workflowStore` as the Canvas, so both views edit one model.
- **The test bar picks the path.** The floating bar under the page (`TestBar.tsx`) picks which record (run) the page follows.
  - The taken path is green. Untaken branches and switch cases fold to one line.
  - Each case shows how many items went its way.
- **Loops.**
  - An item stepper walks one item at a time and shows that item's real result.
  - A row of result squares shows each item's outcome: went through, failed or skipped. A legend gives the counts.
  - A step that didn't run for an item says why, for example "Not run for item 3. It failed at …".
  - Fail, stop-early and repeat-until rules are shown as sentences.
  - The loop's "Run at a time" setting shows the WF030/WF031 warnings and the clamp note (see §2).
- **Poll** lists every try ("try 1 · 14:44:16 not yet …", then "✓ met"), followed by why it stopped repeating.
- **Writes** are marked "simulated in tests".
- **Shape map** in the left rail (`Outline.tsx`, `shapeMap.ts`): a small read-only tree of the main chain, laid out automatically, with the picked run's path on it.
- **+ Add step** (`AddStepMenu.tsx`) adds a step from blanks, and it becomes a real engine node.
- **Old runs** without per-item history still show "Kept for the last item only".

### 1.2 Canvas view: what it does

- **Glass nodes** (`GlassNode.tsx`): an icon tile, a mono kind line with the run mark, and the step name.
- **Glass edges** (`GlassEdge.tsx`): after a run, each edge shows an item-count pill with a popover of the data that passed along it.
- **Node sheet** (`NodeSheet.tsx`): double-click a node to open three resizable panes: input · setup · output.
  - Drag a field from the input pane into an expression to get a live preview.
  - "Test step" runs the step on the upstream data.
- **Inspector** on the right has three tabs: Setup, Data and Settings. Settings covers what happens on failure: stop, continue, or the error output.
- **Draft bar** (`DraftBar.tsx`): the version picker, the changes popover, saved state and problems.
- **Versions popover:** shows a version's diff on the canvas, with Restore.
- **Run strip and test payload:** ▶ Run uses the trigger's test payload, and ■ Stop really cancels the run.
- **Bottom drawer:** Runs / Logs / Problems / Tests.
- **Node catalog:** the full catalog, searchable.
- **Other:** a right-click menu, sticky notes, pins, and an unsaved-changes guard (`UnsavedGuard.tsx`).
- **Zoom and minimap:** ⌘= / ⌘− zoom instantly, and clicking the minimap moves the view.
- **Large workflows:** a 36-step workflow no longer crashes with "Maximum update depth". The fix is in `WorkflowEdge.tsx`, which the legacy editor uses too.

### 1.3 Files (studio `apps/workflow-studio/src/`)

All of these are new:

| Path | What |
|---|---|
| `features/workflow-editor/glass/GlassEditorShell.tsx` | Mounted by `WorkflowEditorPage` unless `?legacy=1`; hosts the views |
| `features/workflow-editor/glass/GlassHeader.tsx` | The header bar: crumb, title, Save · Run ▾ · Schedule · env · Share, the Page \| Canvas switch, ⋯ |
| `features/workflow-editor/glass/page/` | `PageView.tsx` (the document), `Outline.tsx` (rail), `TestBar.tsx`, `RunCard.tsx`, `TokenPicker.tsx`, `AddStepMenu.tsx`, `glass-page.css` |
| `features/workflow-editor/glass/lib/` | Pure logic, each file with its tests: `pageModel.ts` (graph → sections), `pageSentences.ts` (sentence per node type), `pageDoc.ts` (kind labels and words), `pageRun.ts` (what the picked run says: cases taken, counts, loop items, poll tries), `runPath.ts` (green path), `shapeMap.ts` |
| `features/workflow-editor/glass/editor/` | The Canvas: `EditorView`, `GlassNode`, `GlassEdge`, `NodeSheet`, `Inspector*`, `NodeCatalog`, `DraftBar`, `RunStrip`, `TestPayload`, `BottomDrawer`, `Versions*`, `ChangesPopover`, `UnsavedGuard`, `runControl.ts`, `glass-editor.css` |
| `features/workflow-editor/glass/run/` | `RunPicker.tsx`, `useRunData.tsx` (runs paged newest first; one run's detail) |
| `features/workflow-editor/glass/definition/` | `DefinitionView.tsx` (read-only JSON linked to steps), `SideBySide.tsx` |
| `features/workflow-editor/glass/state/` | `glassStore.ts` (one store for the views), `editActions.ts` (edits from the Page go through `workflowStore`) |
| `features/workflow-editor/components/read-view/` | `WorkflowReadView.tsx`, `readView.css` (+ `lib/readView.ts`) |
| `shared/glass/` | The shared glass kit used by the editor and the Notebook: `GlassPanel`, `GlassSheet`, `Drawer`, `Chip`, `StatusMark`, `ValueToken`, `ViewSwitch`, `SplitButton`, `Palette`, `CheckStrip`, `DiffBlock`, `DataTablePreview`, `KindLabel`, `page/Page*` (Doc, Section, Step, Branch, Loop, ForEach, Parallel), `glass.css`, `GlassShowcase.tsx` (shown at `/design`) |

### 1.4 Edits to existing studio files

| File | Change |
|---|---|
| `features/workflow-editor/WorkflowEditorPage.tsx` | Mount `GlassEditorShell` by default; `?legacy=1` keeps the old editor |
| `features/workflow-editor/components/canvas/WorkflowCanvas.tsx`, `edges/WorkflowEdge.tsx` | Instant zoom, minimap click, the update-depth crash fix |
| `features/workflow-editor/components/ndv/ExpressionEditor.tsx` | `ExpressionPreviewContext` / `ExpressionSampleContext`: the node sheet turns on the resolved-value preview and passes sample data to nested editors |
| `features/workflow-editor/hooks/useExecutionStream.ts` | Stop also calls `POST /executions/{id}/cancel`, so the run really stops on the engine |
| `features/workflow-editor/stores/workflowStore.ts`, `editorLayoutStore.ts` | Edits from the Page; sticky notes; the view mode |
| `features/workflow-editor/lib/workflowTransform.ts`, `nodeIcons.ts` | Sticky notes saved in `settings.sticky_notes`; an approval icon |
| `features/workflow-editor/components/workflow-navbar/WorkflowNavbar.tsx` | The legacy editor gets a view switch (Read view / editor) |
| `shared/lib/api.ts`, `shared/lib/backendTypes.ts` | Types for `loop_runs`, `poll_tries`, the run list, and the notebook calls |
| `index.css` | Imports `shared/glass/glass.css`; dark pages off the canvas use the darker smoked glass |
| `app/routes/design.tsx` | `/design` shows the glass showcase |
| `scripts/design-check.mjs` | Exempts the app builder's generated theme folder |
| `package.json` | Adds `@dagrejs/dagre` (auto layout for the shape map) |

### 1.5 What the Page reads from the API

- `GET /api/workflows/{id}`
- `GET /api/executions?workflow_id=…` (the run picker)
- `GET /api/executions/{id}`, which needs these fields: `node_metrics[*].activeOutputs`, `node_data`, `errors`, and, new, `loop_runs` and `poll_tries`

Saving uses the existing `PUT /api/workflows/{id}`.

---

## 2. Engine changes (`apps/workflow-engine/src/`)

Everything here is additive. New fields are optional, and old workflow JSON and stored runs behave exactly as before.

**How this was checked:** the old and new engines ran side by side on the same workflows over the API, and every output was identical. That covered the loop and poll fixtures, 4 bank workflows with their test suites, and a rescore of 20 bank workflows.

### 2.1 Per-item loop history

- **New file:** `engine/loop_history.py`.
- **What's recorded:** every pass of a Loop: status, items, and per step its outputs, branch taken, time, retries and error.
- **Limits:** 200 passes, 3 items per step, 2 KB per item, and a `truncated` flag when a limit is hit.
- **Where it lives:** in the execution's existing metadata, so no migration is needed. The API returns it as the optional field `loop_runs` on `GET /executions/{id}`.
- **Pausing:** runs that pause (for an approval) and resume merge their history.
- **Edited:** `engine/workflow_runner.py`, `engine/types.py`, `repositories/execution_repository.py`, `schemas/execution.py`, `services/execution_service.py`.

### 2.2 New Loop settings (`nodes/flow/loop.py`)

All three are optional:

| Setting | Default | Meaning |
|---|---|---|
| `concurrency` | 1 | Items run at a time. Capped by `MAX_LOOP_CONCURRENCY = 20`, and loop history records a `concurrencyClamped` note when the cap applies. Bad values ("lots", -3) run as 1, as before |
| `onItemFailure` | `stop` | `stop`, or `skip` (skip the failed item and carry on) |
| `stopAfterFailures` | 0 (off) | Stop early after N failures in a row; the run fails with `LOOP_STOPPED_EARLY` |

- Running items at the same time gives exactly the same output as running them one by one.
- Some loop bodies can't run side by side: ones containing a Loop, Poll, Approval, Wait, Stop or Respond, or a step fed from outside the loop. Those run one at a time, and loop history records the reason as `concurrencyFallback`.
- With `stop` and concurrency above 1, up to N−1 items may already have started when one fails. Their calls have already happened, and their results are dropped, with the pass numbers recorded as `ranAfterStop`.
- When a Loop stops early, the saved run keeps the error's code and details (`execution_repository.py`; test `tests/integration/test_loop_stop_early_api.py`).

### 2.3 New workflow checks (`services/workflow_checker.py`, `POST /check`)

- **WF030** warns when `concurrency` is above 20, not a whole number, or below 1. The message says what will actually run.
- **WF031** warns when a Loop runs more than one item at a time, stops on failure, and its body has a step with a network, database, sub-workflow or LLM effect. Effects come from `engine/effects.py`. The warning names the steps and says to set "Run at a time" to 1 for payments and other one-time actions.

### 2.4 Poll tries (`nodes/flow/poll.py`)

- Each try is recorded (time, result, short value) and returned as the optional field `poll_tries`.
- **Fix:** a condition that had already resolved to a boolean used to crash on `.strip` (`tests/unit/test_poll_condition.py`).

### 2.5 Smaller engine changes (from the workflow one-shot and glass editor work)

| Change | Files |
|---|---|
| Approval `per_item`: one task per item, all open at once; the run resumes once, when the last is settled | `nodes/flow/approval.py`, `engine/task_resumer.py`, `repositories/task_repository.py`, `services/task_service.py`, `db/models.py` (`human_tasks.item_index`), migration `20261030120000_human_task_items.sql` |
| A Loop with an Approval keeps its pending items across pause and resume | `engine/task_resumer.py` |
| Postgres `transaction` runs once per item, like `query` does (it used to run only item 0); list params are resolved | `nodes/integrations/postgres.py` |
| Test mode records every item's request (it used to record only item 0's) | `engine/test_mode.py`, `engine/assertions.py` |
| Webhook runs get the API catalog and the egress allowlist | `services/webhook_service.py` |
| Rejecting an approval no longer requires the form's required fields | `services/task_service.py` |
| `PUT /workflows/{id}`: an explicit `[]` for nodes or connections means "remove all"; an absent field keeps them; settings merge by key | `services/workflow_service.py`, `routes/workflows.py` |
| A retried run's DB row exists before its id is returned (the first GET used to 404 sometimes) | `services/execution_service.py` |
| Streamed runs register for `POST /executions/{id}/cancel` | `routes/streaming.py` |
| Database connections are read-only unless `readonly: false` is sent explicitly | `services/connection_service.py` |
| Run attribution (`triggered_by`, `run_as`, `via_agent`) on `POST /workflows/{id}/run`, stored in `executions.metadata.attribution` and returned on runs | `routes/workflows.py`, `services/execution_service.py` |
| `base64(s)` expression function (Basic auth) | `engine/expression_engine.py`, signature in `services/ai_builder_prompts.py`, regenerated `tests/fixtures/ai_builder/prompt_render_snapshot.json` (`WF_PROMPT_SNAPSHOT_WRITE=1`) |
| Switch conditions can read `$vars`; tests can assert on a Respond step's reply | `nodes/flow/switch.py`, `nodes/flow/respond.py` |

**New fixtures:** `tests/fixtures/workflows/switch_loop.json`, `parallel_error.json`, `loop_failures.json` and `poll_attempts.json`.

---

## 3. Notebook

The Notebook is an **AI-driven** workbench for ops and testing people. Users say what they want, and Ask builds or edits the cells; nobody has to write code or SQL. The manual pickers stay as a way to tweak cells.

It lives in the project nav under "Notebooks": a Manager page, a template gallery, then a notebook page.

### 3.1 Cells

- **Cell kinds:** query, http, workflow, rule, change, data, store, browser, for_each, ask, note, wait.
- **Approval and person cells are removed.** The person running the notebook is already there; unattended sign-off belongs in workflows.
  - A stored one renders read-only as "This step type was removed", with a Delete step button. Runs skip it, and Keep leaves it out.
  - Creating or editing one returns 422 `kind_removed`.
- **Each cell reads as a sentence with value tokens** (`cells/Sentence.tsx`, `Tok.tsx`). Editing a token PATCHes the cell's spec; that cell and the ones after it go stale.
- **References between cells:** `{{cells.c3.rows}}`, `{{cells.c3.count}}`, `{{row.wire_id}}`. Values saved by an http cell are used as `{{name}}`.
- **Change cell:** shows the diff, then Apply or Discard. Applying twice writes once. Editing a cell above it makes the proposal stale (409), and a rerun fixes it.
- **Rerun:** a rerun stops before a write that was already applied, and you choose to keep the earlier result or reapply (`rerun/RerunBar.tsx`).
- **Run only if:** a guard can be put on any cell.
- **Ask** is one LLM call with a forced `plan_cells` tool and one repair retry. With no LLM it returns `llm_unavailable`, and nothing is half-created.

### 3.2 Interfaces

- **Workspace** (`workspace/Workspace.tsx`): a side panel that is closed until needed and opens automatically when a cell uses it.
  - Tabs: browser, results, data, change, api, workflow, trace, report.
  - It shows at most 4 tabs; the rest go under +N.
- **Tables you can act on** (`cells/RowsTable.tsx`):
  - search, a column filter, header-click sort, and a key-column picker (selection is refused when the key column repeats);
  - checkboxes, select all, select the filtered rows; up to 500 rows, loaded through the rows route.
  - The action bar has:
    - **Change selected…**: a change cell keyed by the picked keys;
    - **Run workflow on selected…** / **Call API for selected…**: a for_each over the picked rows;
    - **Export CSV**;
    - **Ask about the selection**, which sends `selection {cell, key, keys}` to `/ask`.
- **Browser take over:**
  - While a browser cell runs, the workspace shows its live frame.
  - Take over pauses the scripted steps. Clicks (as normalised x/y), typing, allow-listed keys and scrolling are relayed to the live page. Hand back resumes the steps.
  - **Typed text is never saved:**
    - the trace keeps only the steps before take over;
    - the video is deleted;
    - no screenshots or pass baseline are kept after take over;
    - typed values are removed from errors and failure evidence.

    The cell shows notes such as "trace not kept after take over".
  - When there is no live page, the engine answers 409 `not_pending`.
- **API testing (for the testing team).** This is an upgrade of the http cell, not a new kind, so it reuses the executor, write guards, Keep, for_each, the report and Ask.
  - **In the cell** (`cells/Http.tsx`):
    - params;
    - auth: bearer, basic or API key, always as a reference to a project secret;
    - JSON or form bodies;
    - sentence checks (status, time, JSON path, header, schema), each showing the actual value;
    - `extract` into variables.
  - **Environments:** per notebook, with value or secret variables (dev/uat).
  - **Import** from cURL or the API catalog. Credentials found in a cURL command are dropped, with a warning.
  - **Suite bar** (`cells/ApiSuite.tsx`): summary, environment picker, Run suite, Keep as test suite, and "Generate negative tests" (needs AI).
  - **API tab** (`workspace/ApiResponse.tsx`): JSON tree, headers, and a diff against the last run.
- **Keep as…** (`keep/KeepDrawer.tsx`) turns the notebook into a workflow, test suite, scheduled check, dataset or report.
  - Triggers: manual, cron, business day, after deploy.
  - Deterministic, with no LLM (`services/notebook/keep.py`).
- **Report:** deterministic markdown over each cell's latest result. **Trace** and **export.spec.ts** (a Playwright spec) are also available.

### 3.3 Files

**Engine** (`apps/workflow-engine/src/`), new:

| Path | What |
|---|---|
| `routes/notebooks.py`, `routes/notebook_keep.py` | All notebook routes (list below). Register `notebook_keep` **before** `notebooks` in `routes/__init__.py`, so `/notebooks/capabilities` doesn't match `/notebooks/{id}` |
| `schemas/notebook.py` | Pydantic contract (mirrored by hand in studio `features/notebook/types.ts`) |
| `db/notebook_models.py` | Tables `notebooks`, `notebook_cells`, `notebook_runs`, `notebook_cell_results`, `notebook_writes`, `notebook_environments` |
| `db/migrations/20261031120000_notebooks.sql`, `20261101130000_notebook_environments.sql` | Migrations |
| `services/notebook/service.py` | CRUD, append-only cells, and the read model the studio renders |
| `services/notebook/runner.py` | The run loop (async, returns 202; stale handling; stops before applied writes) |
| `services/notebook/executors/*.py` | One module per cell kind: `run(ctx, cell, inputs) -> CellOutcome` |
| `services/notebook/apitest.py` | HTTP request building and checks |
| `services/notebook/refs.py`, `effects.py`, `rule.py`, `storage.py`, `report.py`, `minirun.py`, `capabilities.py` | References, write classification, pure-Python rules, full row sets on disk, the report, unsaved-workflow runs, capabilities |
| `services/notebook/keep.py` | Keep as workflow, test suite and the rest |
| `services/notebook/templates.py` + `templates/*.json` | The template gallery |
| `scripts/notebook_spec.mts` | Builds the Playwright spec for `export.spec.ts` |

**Engine**, edited:

| File | Change |
|---|---|
| `routes/__init__.py` | Registers the two routers |
| `core/config.py` | `notebook_storage_dir` (env `WORKFLOW_NOTEBOOK_STORAGE_DIR`; default `data/notebooks`). The DB keeps a preview of at most 50 rows; full row sets go to disk |
| `broker/jobs.py` | `control` (`takeover` / `handback`) and `input` actions to jobs-service |
| `engine/expression_engine.py` | `base64` (see §2.5) |

**Notebook routes** (all under `/api`):

```
GET/POST   /projects/{project}/notebooks
GET/PATCH/DELETE /notebooks/{id}
POST       /notebooks/{id}/cells            PATCH/DELETE /notebook-cells/{cell}
POST       /notebooks/{id}/runs             GET /notebook-runs/{run}   POST /notebook-runs/{run}/cancel
POST       /notebook-cells/{cell}/apply | /discard | /retry-rows
GET        /notebook-cells/{cell}/preview | /rows
POST       /notebooks/{id}/ask              GET /notebooks/capabilities   GET /notebook-templates
GET        /notebook-cells/{cell}/frame | /artifacts | /artifacts/{name}
POST       /notebook-cells/{cell}/control | /input          (take over)
GET        /notebooks/{id}/report | /trace | /checks | /export.spec.ts
POST       /notebooks/{id}/keep/preview | /keep
GET/PUT    /notebooks/{id}/environments     POST /notebooks/{id}/import-http
```

**Studio** (`apps/workflow-studio/src/features/notebook/`), all new:

| Path | What |
|---|---|
| `NotebookManagerPage.tsx`, `NotebookPage.tsx`, `TemplateGallery.tsx` | The pages |
| `api.ts`, `hooks.ts`, `types.ts`, `context.tsx` | One function per endpoint; TanStack Query hooks that poll while a run is live; the contract; page state |
| `cells/` | `CellView`, `Sentence`, `Tok`, `Bodies` (per-kind result bodies), `RowsTable`, `Http`, `ApiSuite`, `Look`, `rowsApi.test.ts` |
| `composer/` | `Composer.tsx` (the bottom composer and "/" palette), `DraftCell.tsx` |
| `workspace/` | `Workspace.tsx`, `ApiResponse.tsx` |
| `keep/KeepDrawer.tsx`, `rerun/RerunBar.tsx`, `lib/` (cells, look, identity), `notebook.css` | |

**Studio wiring** (edited):
- `app/routes/project.tsx` adds `notebooksRoute` (`/p/$project/notebooks`) and `notebookRoute` (`notebooks/$notebookId`, search `cell`, `run`).
- `features/project/lib/places.ts` adds the "Notebooks" nav entry (`NotebookPen` icon).
- `shared/glass/Palette.tsx` (new) resets its query on open.

**jobs-service** (`apps/jobs-service/`), edited:
- `src/control.ts`, `server.ts`, `store.ts`, `testRun.ts`, `uiCase.ts`: take over, relayed input, the trace and video cut, and masking typed text.
- Tests: `tests/evidence.test.ts`, plus the new `tests/takeover-trace.test.ts`, which runs a real browser.

### 3.4 How to check it over the API

These scenarios all pass here. The driver scripts are not in the repo; recreate them from this list.

- **Overdue loans:**
  - query 4 rows, then select W1 and W2;
  - Change → needs_approval with a 2-row diff → Apply 200;
  - read back: released, written once;
  - for_each over the 2 rows with a POST inside → 2 ok.
- **API suite:**
  - PUT environments → 200; a bad active environment → 422;
  - GET balance → 4/4 checks pass, and `available` is saved;
  - the next POST sends `{"amount": …}` chained from it;
  - a deliberately wrong check fails with the actual value; PATCH it and rerun → passes;
  - cURL import drops Authorization, with a warning;
  - Keep as test_suite → 201, and running it via `POST /workflows/{id}/tests/run` sends the right auth headers.
- **Take over:**
  - `/control` before the run → 409;
  - during the run, takeover / click / type / key / scroll → 200; a click outside the frame or a disallowed key (Meta+R) → 422;
  - handback → the run finishes;
  - the trace zip does not contain the typed text;
  - input after the run → 409.
- **Removed kinds:** creating a person cell → 422; an old notebook with one still loads and runs.
- **Edge cases:**
  - a second run while one is going → 409;
  - stale proposal → 409;
  - bad input (a DELETE as a query, an http cell with no URL) → 422, and nothing is created;
  - delete during a run → 409.

---

## 4. Design: Studio glass

Both the editor and the Notebook use the Studio glass look. The values live in `apps/workflow-studio/src/index.css`. The page layer is `src/shared/glass/glass.css`; it adds the sky, mono kind labels, value tokens, the run rail, diff lines, chips, sheets and the drawer. Use the components in `shared/glass/`, not new CSS. `npm run design:check` enforces the rules, and `apps/workflow-studio/DESIGN-STANDARD.md` lists which component or utility to use.

**Rules:**
- `border: 0` on every surface. Edges are 1px inset rings at 4–6% alpha (`--hairline`, `--edge`). Dividers are `inset 0 1px 0 var(--hairline)`.
- Glass is `--glass-fill`, plus a `--glass-sheen` layer, plus `backdrop-filter: blur(24px) saturate(1.6)`, plus an inset top highlight, plus `--glass-depth` (two-layer shadow).
- Radii are 14–18 for glass cards, 20 for panels, 22 for sheets and 8–10 for controls. Wells inside glass use `--well` with an inset ring and no depth.
- Fonts: Inter for text; JetBrains Mono for kind labels, values, ids and timings. Kind labels are mono 10.5px, uppercase, with tracking .04em. Numbers are tabular.
- Status colours: success green, warning amber, destructive red. AI purple (`--ai`, ✦) is used **only** for things that need AI.
- Dashed or dotted lines are semantic only: a dotted underline is an editable value token, and dashed is an unfilled one.
- Light and dark follow the OS. `[data-theme]` overrides it.
- Every token is defined in `src/index.css` and `src/shared/glass/glass.css`. The code is the source of truth for the look.

**Notebook layout:**
- The column is up to 660px, with the workspace at 440–560px when open.
- Below 1280px it becomes a single column, with the workspace as a sheet.
- Below 640px the Explorer and the workspace become bottom sheets.

---

## 5. Tests

| What | Command | Result here |
|---|---|---|
| Engine: loops, poll, checker | `cd apps/workflow-engine && venv/bin/python -m pytest -q tests/unit/test_loop_characterisation.py tests/unit/test_loop_history.py tests/unit/test_poll_condition.py tests/unit/test_workflow_checker.py tests/integration/test_loop_stop_early_api.py` | all pass |
| Engine: prompts (base64) | `venv/bin/python -m pytest -q tests/unit/test_prompts_optional_sections.py tests/unit/test_ai_builder_prompts.py` | 172 pass |
| Engine: approvals, Postgres per item | `venv/bin/python -m pytest -q tests/unit/test_approval_per_item_pg.py tests/unit/test_loop_approval_resume_pg.py tests/unit/test_postgres_transaction_per_item_pg.py tests/unit/test_json_template_structures.py` | pass (needs Postgres) |
| Engine: notebook | `venv/bin/python -m pytest -q tests/integration/test_notebook_*.py tests/integration/test_update_explicit_empty.py` | 127 pass |
| jobs-service | `cd apps/jobs-service && npm test` | 8 pass |
| Studio types | `cd apps/workflow-studio && npx tsc -b` | 0 errors |
| Studio unit | `npm run test:unit` | 1220/1222 (2 old `layout3d` failures) |
| e2e: glass editor | `npx playwright test -c e2e tests/3` (specs 30–35) | all pass |
| e2e: notebook | `npx playwright test -c e2e tests/4` (specs 40–46) | 34/34 |
| e2e: legacy editor | `npx playwright test -c e2e tests/14 tests/15 tests/16 tests/17 tests/18 tests/19 tests/20 tests/21` (these run with `?legacy=1` via `e2e/fixtures/legacy.ts`) | 23 failures, all also failing before this work |

Each e2e run brings up its own throwaway DB, engine, jobs-service, fake LLM and Vite (port 5180 and up), via `tests/e2e_stack.py`, `e2e/global-setup.ts` and `global-teardown.ts`. Notebook seed data comes from `tests/e2e_stack.py nbseed`. Only one run at a time.

**These fail at `4f776cb` too:** `test_stage8_b5_install_pg.py` (30 tests), `test_kg_extract.py`, `test_stage3a_runner.py::test_tests_and_defaults_survive_publish_and_version_restore`, 2 in `layout3d.test.ts`, and 23 in legacy specs 14–21.

---

## 6. Not done and known limits

- **The live AI path is untested** because there were no credentials. That covers notebook Ask, "Generate negative tests" and the AI builder; they were only checked against a fake LLM and for a clean `llm_unavailable`.
- **Shown as "coming":** agent-driven browsing, Fork, for-each with child tasks, the Mailbox trigger and event-based waits.
- **Waits in kept notebook workflows are capped at 5 minutes.** The Wait there is in-process; port the durable Wait to lift the cap.
- **Not yet in the glass Canvas** (the legacy editor still has them):
  - contracts-checker marks;
  - the AI builder hand-off;
  - the interfaces panel;
  - convert to sub-workflow / "Used by";
  - `D` to disable a node;
  - error-output handles;
  - the spec-16 Tests tab flows.
- **Page view gaps:**
  - the right panel shows run details rather than notes;
  - Publish is in ⋯;
  - nested frames get narrow at 420px.

---

## 7. Other changes you'll see in the diff

These are not part of the three features, but they are in this repo since `4f776cb`:

- **One-shot workflow builder:** `services/workflow_oneshot.py` and `oneshot_prompt.md`, wired into the editor chat in `ai_chat_service.py`.
  - It builds in one model call, then applies `changes.json` patches for edits. `WF_CHAT_BUILDER=legacy` brings back the staged builder.
  - The prototype and its results are in `apps/workflow-engine/prototypes/workflow-oneshot/`; see its `README.md`.
- **App builder visual editing:**
  - studio `features/app-builder/visual/` and `sandbox/visual-edit-runtime.ts`;
  - engine `services/app_kit/`, `editability/`, `editability_lint.py`, `app_bundler_tailwind.mjs` (Tailwind compile at publish), plus `oneshot_builder.py`, `app_bundler.py`, `app_scaffold.py`, `app_service.py`, `routes/apps.py`, `schemas/app.py`, `routes/app_builder_ai.py`;
  - a save or AI turn based on an out-of-date version is refused with 409 `stale_base` (`tests/unit/test_stale_base.py`).

  Behind `WORKFLOW_EDITABLE_RULES=1` and the studio flag in `visual/flag.ts`.

---

## 8. Every file changed since `4f776cb`

`A` = new, `M` = edited. This excludes `prototypes/`, screenshots and test output.

<details><summary><b>Engine: notebook</b> (46 files)</summary>

```
A  apps/workflow-engine/scripts/notebook_spec.mts
A  apps/workflow-engine/src/db/migrations/20261031120000_notebooks.sql
A  apps/workflow-engine/src/db/migrations/20261101130000_notebook_environments.sql
A  apps/workflow-engine/src/db/notebook_models.py
A  apps/workflow-engine/src/routes/notebook_keep.py
A  apps/workflow-engine/src/routes/notebooks.py
A  apps/workflow-engine/src/schemas/notebook.py
A  apps/workflow-engine/src/services/notebook/apitest.py
A  apps/workflow-engine/src/services/notebook/capabilities.py
A  apps/workflow-engine/src/services/notebook/effects.py
A  apps/workflow-engine/src/services/notebook/executors/ask.py
A  apps/workflow-engine/src/services/notebook/executors/browser.py
A  apps/workflow-engine/src/services/notebook/executors/change.py
A  apps/workflow-engine/src/services/notebook/executors/_common.py
A  apps/workflow-engine/src/services/notebook/executors/data.py
A  apps/workflow-engine/src/services/notebook/executors/for_each.py
A  apps/workflow-engine/src/services/notebook/executors/http.py
A  apps/workflow-engine/src/services/notebook/executors/__init__.py
A  apps/workflow-engine/src/services/notebook/executors/note.py
A  apps/workflow-engine/src/services/notebook/executors/query.py
A  apps/workflow-engine/src/services/notebook/executors/rule.py
A  apps/workflow-engine/src/services/notebook/executors/store.py
A  apps/workflow-engine/src/services/notebook/executors/wait.py
A  apps/workflow-engine/src/services/notebook/executors/workflow.py
A  apps/workflow-engine/src/services/notebook/__init__.py
A  apps/workflow-engine/src/services/notebook/keep.py
A  apps/workflow-engine/src/services/notebook/minirun.py
A  apps/workflow-engine/src/services/notebook/refs.py
A  apps/workflow-engine/src/services/notebook/report.py
A  apps/workflow-engine/src/services/notebook/rule.py
A  apps/workflow-engine/src/services/notebook/runner.py
A  apps/workflow-engine/src/services/notebook/service.py
A  apps/workflow-engine/src/services/notebook/storage.py
A  apps/workflow-engine/src/services/notebook/templates/blank.json
A  apps/workflow-engine/src/services/notebook/templates/daily-data-quality.json
A  apps/workflow-engine/src/services/notebook/templates/portal-smoke.json
A  apps/workflow-engine/src/services/notebook/templates.py
A  apps/workflow-engine/src/services/notebook/templates/stuck-wires.json
A  apps/workflow-engine/tests/integration/notebook_support.py
A  apps/workflow-engine/tests/integration/test_notebook_api.py
A  apps/workflow-engine/tests/integration/test_notebook_api_testing.py
A  apps/workflow-engine/tests/integration/test_notebook_ask.py
A  apps/workflow-engine/tests/integration/test_notebook_browser.py
A  apps/workflow-engine/tests/integration/test_notebook_edge.py
A  apps/workflow-engine/tests/integration/test_notebook_keep.py
A  apps/workflow-engine/tests/integration/test_notebook_rows.py
```
</details>

<details><summary><b>Engine: everything else</b> (91 files)</summary>

```
M  apps/workflow-engine/src/broker/jobs.py
M  apps/workflow-engine/src/core/config.py
A  apps/workflow-engine/src/db/migrations/20261030120000_human_task_items.sql
M  apps/workflow-engine/src/db/models.py
M  apps/workflow-engine/src/engine/assertions.py
M  apps/workflow-engine/src/engine/expression_engine.py
A  apps/workflow-engine/src/engine/loop_history.py
M  apps/workflow-engine/src/engine/task_resumer.py
M  apps/workflow-engine/src/engine/test_mode.py
M  apps/workflow-engine/src/engine/types.py
M  apps/workflow-engine/src/engine/workflow_runner.py
M  apps/workflow-engine/src/nodes/flow/approval.py
M  apps/workflow-engine/src/nodes/flow/loop.py
M  apps/workflow-engine/src/nodes/flow/poll.py
M  apps/workflow-engine/src/nodes/flow/respond.py
M  apps/workflow-engine/src/nodes/flow/switch.py
M  apps/workflow-engine/src/nodes/integrations/postgres.py
M  apps/workflow-engine/src/repositories/execution_repository.py
M  apps/workflow-engine/src/repositories/task_repository.py
M  apps/workflow-engine/src/routes/app_builder_ai.py
M  apps/workflow-engine/src/routes/apps.py
M  apps/workflow-engine/src/routes/__init__.py
M  apps/workflow-engine/src/routes/streaming.py
M  apps/workflow-engine/src/routes/workflows.py
M  apps/workflow-engine/src/schemas/app_builder.py
M  apps/workflow-engine/src/schemas/app.py
M  apps/workflow-engine/src/schemas/execution.py
M  apps/workflow-engine/src/services/ai_builder_prompts.py
M  apps/workflow-engine/src/services/ai_builder.py
M  apps/workflow-engine/src/services/ai_chat_service.py
M  apps/workflow-engine/src/services/app_builder_ai_service.py
M  apps/workflow-engine/src/services/app_bundler.py
A  apps/workflow-engine/src/services/app_bundler_tailwind.mjs
A  apps/workflow-engine/src/services/app_kit/board.tsx
M  apps/workflow-engine/src/services/app_kit/charts.tsx
M  apps/workflow-engine/src/services/app_kit/DataTable.tsx
A  apps/workflow-engine/src/services/app_kit/display.tsx
M  apps/workflow-engine/src/services/app_kit/hooks.ts
M  apps/workflow-engine/src/services/app_kit/index.ts
A  apps/workflow-engine/src/services/app_kit/layout.tsx
M  apps/workflow-engine/src/services/app_kit/live.tsx
A  apps/workflow-engine/src/services/app_kit/overlay.tsx
A  apps/workflow-engine/src/services/app_kit/plots.tsx
M  apps/workflow-engine/src/services/app_kit/resource.tsx
M  apps/workflow-engine/src/services/app_kit/review.tsx
A  apps/workflow-engine/src/services/app_kit/theme.css
M  apps/workflow-engine/src/services/app_kit/ui.tsx
M  apps/workflow-engine/src/services/app_scaffold.py
M  apps/workflow-engine/src/services/app_service.py
M  apps/workflow-engine/src/services/connection_service.py
A  apps/workflow-engine/src/services/editability/lint.mjs
A  apps/workflow-engine/src/services/editability_lint.py
A  apps/workflow-engine/src/services/editability/registry.mjs
M  apps/workflow-engine/src/services/execution_service.py
M  apps/workflow-engine/src/services/oneshot_builder.py
A  apps/workflow-engine/src/services/oneshot_prompt.md
M  apps/workflow-engine/src/services/platform_index.py
M  apps/workflow-engine/src/services/task_service.py
M  apps/workflow-engine/src/services/webhook_service.py
M  apps/workflow-engine/src/services/workflow_checker.py
A  apps/workflow-engine/src/services/workflow_oneshot.py
M  apps/workflow-engine/src/services/workflow_service.py
M  apps/workflow-engine/tests/e2e_stack.py
M  apps/workflow-engine/tests/fixtures/ai_builder/prompt_render_snapshot.json
A  apps/workflow-engine/tests/fixtures/workflows/loop_failures.json
A  apps/workflow-engine/tests/fixtures/workflows/parallel_error.json
A  apps/workflow-engine/tests/fixtures/workflows/poll_attempts.json
A  apps/workflow-engine/tests/fixtures/workflows/switch_loop.json
M  apps/workflow-engine/tests/integration/conftest.py
A  apps/workflow-engine/tests/integration/nb_jobs_support.py
A  apps/workflow-engine/tests/integration/test_loop_stop_early_api.py
A  apps/workflow-engine/tests/integration/test_update_explicit_empty.py
A  apps/workflow-engine/tests/unit/test_app_kit_catalog.py
A  apps/workflow-engine/tests/unit/test_app_kit_theme.py
M  apps/workflow-engine/tests/unit/test_approval_node.py
A  apps/workflow-engine/tests/unit/test_approval_per_item_pg.py
A  apps/workflow-engine/tests/unit/test_editability.py
A  apps/workflow-engine/tests/unit/test_json_template_structures.py
A  apps/workflow-engine/tests/unit/test_loop_approval_resume_pg.py
A  apps/workflow-engine/tests/unit/test_loop_characterisation.py
A  apps/workflow-engine/tests/unit/test_loop_history.py
M  apps/workflow-engine/tests/unit/test_oneshot_builder.py
A  apps/workflow-engine/tests/unit/test_poll_condition.py
A  apps/workflow-engine/tests/unit/test_postgres_transaction_per_item_pg.py
M  apps/workflow-engine/tests/unit/test_stage3a_assertions.py
M  apps/workflow-engine/tests/unit/test_stage3a_mock_runner.py
M  apps/workflow-engine/tests/unit/test_stage6_b5_chat.py
A  apps/workflow-engine/tests/unit/test_stale_base.py
M  apps/workflow-engine/tests/unit/test_task_decide_pg.py
M  apps/workflow-engine/tests/unit/test_workflow_checker.py
A  apps/workflow-engine/tests/unit/test_workflow_oneshot.py
```
</details>

<details><summary><b>jobs-service</b> (7 files)</summary>

```
M  apps/jobs-service/src/control.ts
M  apps/jobs-service/src/server.ts
M  apps/jobs-service/src/store.ts
M  apps/jobs-service/src/testRun.ts
M  apps/jobs-service/src/uiCase.ts
M  apps/jobs-service/tests/evidence.test.ts
A  apps/jobs-service/tests/takeover-trace.test.ts
```
</details>

<details><summary><b>Studio: notebook</b> (36 files)</summary>

```
A  apps/workflow-studio/e2e/fixtures/notebook.ts
A  apps/workflow-studio/e2e/tests/40-notebook-manager.spec.ts
A  apps/workflow-studio/e2e/tests/41-notebook-run.spec.ts
A  apps/workflow-studio/e2e/tests/42-notebook-change.spec.ts
A  apps/workflow-studio/e2e/tests/43-notebook-keep.spec.ts
A  apps/workflow-studio/e2e/tests/44-notebook-browser.spec.ts
A  apps/workflow-studio/e2e/tests/45-notebook-edge.spec.ts
A  apps/workflow-studio/e2e/tests/46-notebook-rows-api.spec.ts
A  apps/workflow-studio/src/features/notebook/api.ts
A  apps/workflow-studio/src/features/notebook/cells/ApiSuite.tsx
A  apps/workflow-studio/src/features/notebook/cells/Bodies.tsx
A  apps/workflow-studio/src/features/notebook/cells/CellView.tsx
A  apps/workflow-studio/src/features/notebook/cells/Http.tsx
A  apps/workflow-studio/src/features/notebook/cells/Look.tsx
A  apps/workflow-studio/src/features/notebook/cells/rowsApi.test.ts
A  apps/workflow-studio/src/features/notebook/cells/RowsTable.tsx
A  apps/workflow-studio/src/features/notebook/cells/Sentence.tsx
A  apps/workflow-studio/src/features/notebook/cells/Tok.tsx
A  apps/workflow-studio/src/features/notebook/composer/Composer.tsx
A  apps/workflow-studio/src/features/notebook/composer/DraftCell.tsx
A  apps/workflow-studio/src/features/notebook/context.tsx
A  apps/workflow-studio/src/features/notebook/hooks.ts
A  apps/workflow-studio/src/features/notebook/keep/KeepDrawer.tsx
A  apps/workflow-studio/src/features/notebook/lib/cells.test.ts
A  apps/workflow-studio/src/features/notebook/lib/cells.ts
A  apps/workflow-studio/src/features/notebook/lib/identity.ts
A  apps/workflow-studio/src/features/notebook/lib/look.test.ts
A  apps/workflow-studio/src/features/notebook/lib/look.ts
A  apps/workflow-studio/src/features/notebook/notebook.css
A  apps/workflow-studio/src/features/notebook/NotebookManagerPage.tsx
A  apps/workflow-studio/src/features/notebook/NotebookPage.tsx
A  apps/workflow-studio/src/features/notebook/rerun/RerunBar.tsx
A  apps/workflow-studio/src/features/notebook/TemplateGallery.tsx
A  apps/workflow-studio/src/features/notebook/types.ts
A  apps/workflow-studio/src/features/notebook/workspace/ApiResponse.tsx
A  apps/workflow-studio/src/features/notebook/workspace/Workspace.tsx
```
</details>

<details><summary><b>Studio: workflow editor + shared glass</b> (94 files)</summary>

```
A  apps/workflow-studio/e2e/fixtures/legacy.ts
A  apps/workflow-studio/e2e/tests/30-glass-design.spec.ts
A  apps/workflow-studio/e2e/tests/31-glass-page.spec.ts
A  apps/workflow-studio/e2e/tests/32-glass-editor.spec.ts
A  apps/workflow-studio/e2e/tests/33-glass-editor-tests-versions.spec.ts
A  apps/workflow-studio/e2e/tests/34-glass-editor-parity.spec.ts
A  apps/workflow-studio/e2e/tests/35-glass-page-doc.spec.ts
M  apps/workflow-studio/src/features/workflow-editor/components/canvas/edges/WorkflowEdge.tsx
M  apps/workflow-studio/src/features/workflow-editor/components/canvas/WorkflowCanvas.tsx
M  apps/workflow-studio/src/features/workflow-editor/components/ndv/ExpressionEditor.tsx
A  apps/workflow-studio/src/features/workflow-editor/components/read-view/readView.css
A  apps/workflow-studio/src/features/workflow-editor/components/read-view/WorkflowReadView.tsx
M  apps/workflow-studio/src/features/workflow-editor/components/workflow-navbar/WorkflowNavbar.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/definition/DefinitionView.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/definition/SideBySide.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/BottomDrawer.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/ChangesPopover.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/DraftBar.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/EditorView.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/GlassEdge.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/glass-editor.css
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/GlassNode.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/InspectorData.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/InspectorSettings.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/InspectorSetup.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/Inspector.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/NodeCatalog.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/NodeSheet.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/runControl.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/RunStrip.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/status.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/TestPayload.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/ui.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/UnsavedGuard.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/VersionsPopover.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/editor/versions.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/GlassEditorShell.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/GlassHeader.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageDoc.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageDoc.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageModel.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageModel.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageRun.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageRun.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageSentences.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/pageSentences.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/runPath.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/runPath.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/shapeMap.test.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/lib/shapeMap.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/page/AddStepMenu.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/page/glass-page.css
A  apps/workflow-studio/src/features/workflow-editor/glass/page/Outline.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/page/PageView.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/page/RunCard.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/page/TestBar.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/page/TokenPicker.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/run/RunPicker.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/run/useRunData.tsx
A  apps/workflow-studio/src/features/workflow-editor/glass/state/editActions.ts
A  apps/workflow-studio/src/features/workflow-editor/glass/state/glassStore.ts
M  apps/workflow-studio/src/features/workflow-editor/hooks/useExecutionStream.ts
M  apps/workflow-studio/src/features/workflow-editor/lib/nodeIcons.ts
A  apps/workflow-studio/src/features/workflow-editor/lib/readView.test.ts
A  apps/workflow-studio/src/features/workflow-editor/lib/readView.ts
M  apps/workflow-studio/src/features/workflow-editor/lib/workflowTransform.ts
M  apps/workflow-studio/src/features/workflow-editor/stores/editorLayoutStore.ts
M  apps/workflow-studio/src/features/workflow-editor/stores/workflowStore.ts
M  apps/workflow-studio/src/features/workflow-editor/WorkflowEditorPage.tsx
A  apps/workflow-studio/src/shared/glass/CheckStrip.tsx
A  apps/workflow-studio/src/shared/glass/Chip.tsx
A  apps/workflow-studio/src/shared/glass/cx.ts
A  apps/workflow-studio/src/shared/glass/DataTablePreview.tsx
A  apps/workflow-studio/src/shared/glass/DiffBlock.tsx
A  apps/workflow-studio/src/shared/glass/Drawer.tsx
A  apps/workflow-studio/src/shared/glass/glass.css
A  apps/workflow-studio/src/shared/glass/GlassPanel.tsx
A  apps/workflow-studio/src/shared/glass/GlassSheet.tsx
A  apps/workflow-studio/src/shared/glass/GlassShowcase.tsx
A  apps/workflow-studio/src/shared/glass/glass.test.ts
A  apps/workflow-studio/src/shared/glass/index.ts
A  apps/workflow-studio/src/shared/glass/KindLabel.tsx
A  apps/workflow-studio/src/shared/glass/page/PageBranch.tsx
A  apps/workflow-studio/src/shared/glass/page/PageDoc.tsx
A  apps/workflow-studio/src/shared/glass/page/PageForEach.tsx
A  apps/workflow-studio/src/shared/glass/page/PageLoop.tsx
A  apps/workflow-studio/src/shared/glass/page/PageParallel.tsx
A  apps/workflow-studio/src/shared/glass/page/PageSection.tsx
A  apps/workflow-studio/src/shared/glass/page/PageStep.tsx
A  apps/workflow-studio/src/shared/glass/Palette.tsx
A  apps/workflow-studio/src/shared/glass/SplitButton.tsx
A  apps/workflow-studio/src/shared/glass/StatusMark.tsx
A  apps/workflow-studio/src/shared/glass/ValueToken.tsx
A  apps/workflow-studio/src/shared/glass/ViewSwitch.tsx
```
</details>

<details><summary><b>Studio: app builder</b> (66 files)</summary>

```
M  apps/workflow-studio/src/features/app-builder/components/AppBuilderChatPanel.tsx
M  apps/workflow-studio/src/features/app-builder/components/AppBuilderShell.tsx
M  apps/workflow-studio/src/features/app-builder/components/AppPreviewPanel.tsx
A  apps/workflow-studio/src/features/app-builder/hooks/flushBeforeSend.test.ts
M  apps/workflow-studio/src/features/app-builder/hooks/useAppBuilderChat.ts
A  apps/workflow-studio/src/features/app-builder/hooks/visualSave.ts
M  apps/workflow-studio/src/features/app-builder/sandbox/bridge.ts
M  apps/workflow-studio/src/features/app-builder/sandbox/esbuild-bundler.ts
M  apps/workflow-studio/src/features/app-builder/sandbox/IframeSandbox.tsx
A  apps/workflow-studio/src/features/app-builder/sandbox/visual-edit-runtime.ts
A  apps/workflow-studio/src/features/app-builder/visual/askAi.ts
A  apps/workflow-studio/src/features/app-builder/visual/AskFix.tsx
A  apps/workflow-studio/src/features/app-builder/visual/bind/actions.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/BindPanel.tsx
A  apps/workflow-studio/src/features/app-builder/visual/bind/ClickSection.tsx
A  apps/workflow-studio/src/features/app-builder/visual/bind/commit.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/controls.tsx
A  apps/workflow-studio/src/features/app-builder/visual/bind/DataSection.tsx
A  apps/workflow-studio/src/features/app-builder/visual/bind/data.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/scope.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/__tests__/actions.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/__tests__/data.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/__tests__/fixture.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/__tests__/visibility.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/visibility.ts
A  apps/workflow-studio/src/features/app-builder/visual/bind/VisibleSection.tsx
A  apps/workflow-studio/src/features/app-builder/visual/canvas/CanvasHandles.tsx
A  apps/workflow-studio/src/features/app-builder/visual/canvas/canvasRect.ts
A  apps/workflow-studio/src/features/app-builder/visual/canvas/CanvasToolbar.tsx
A  apps/workflow-studio/src/features/app-builder/visual/canvas/handles.ts
A  apps/workflow-studio/src/features/app-builder/visual/canvas/quickStyle.ts
A  apps/workflow-studio/src/features/app-builder/visual/canvas/QuickStyle.tsx
A  apps/workflow-studio/src/features/app-builder/visual/editor.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/editor.ts
A  apps/workflow-studio/src/features/app-builder/visual/flag.ts
A  apps/workflow-studio/src/features/app-builder/visual/history.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/history.ts
A  apps/workflow-studio/src/features/app-builder/visual/layers/act.ts
A  apps/workflow-studio/src/features/app-builder/visual/layers/files.ts
A  apps/workflow-studio/src/features/app-builder/visual/layers/LayersPanel.tsx
A  apps/workflow-studio/src/features/app-builder/visual/layers/tree.ts
A  apps/workflow-studio/src/features/app-builder/visual/manifest.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/manifest.ts
A  apps/workflow-studio/src/features/app-builder/visual/registry/extract.ts
A  apps/workflow-studio/src/features/app-builder/visual/registry/kitFixture.ts
A  apps/workflow-studio/src/features/app-builder/visual/registry/manifest.fixture.ts
A  apps/workflow-studio/src/features/app-builder/visual/registry/registry.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/registry/registry.ts
A  apps/workflow-studio/src/features/app-builder/visual/selection.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/selection.ts
A  apps/workflow-studio/src/features/app-builder/visual/source.ts
A  apps/workflow-studio/src/features/app-builder/visual/splice.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/splice.ts
A  apps/workflow-studio/src/features/app-builder/visual/StructurePanel.tsx
A  apps/workflow-studio/src/features/app-builder/visual/structure.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/structure.ts
A  apps/workflow-studio/src/features/app-builder/visual/style/breakpoints.ts
A  apps/workflow-studio/src/features/app-builder/visual/style/classEdit.test.ts
A  apps/workflow-studio/src/features/app-builder/visual/style/classEdit.ts
A  apps/workflow-studio/src/features/app-builder/visual/style/deviceStore.ts
A  apps/workflow-studio/src/features/app-builder/visual/style/StylePanel.tsx
A  apps/workflow-studio/src/features/app-builder/visual/style/tokens.ts
A  apps/workflow-studio/src/features/app-builder/visual/theme/ThemePanel.tsx
A  apps/workflow-studio/src/features/app-builder/visual/theme/theme.ts
A  apps/workflow-studio/src/features/app-builder/visual/useVisualEdit.ts
A  apps/workflow-studio/src/features/app-builder/visual/VisualEditPanel.tsx
```
</details>

<details><summary><b>Studio: other</b> (19 files)</summary>

```
M  apps/workflow-studio/e2e/global-setup.ts
M  apps/workflow-studio/e2e/global-teardown.ts
M  apps/workflow-studio/e2e/tests/14-editor-run-debug.spec.ts
M  apps/workflow-studio/e2e/tests/15-editor-contracts-checker.spec.ts
M  apps/workflow-studio/e2e/tests/16-editor-tests.spec.ts
M  apps/workflow-studio/e2e/tests/17-ai-and-live.spec.ts
M  apps/workflow-studio/e2e/tests/18-interfaces.spec.ts
M  apps/workflow-studio/e2e/tests/19-ai-builder.spec.ts
M  apps/workflow-studio/e2e/tests/20-reuse.spec.ts
M  apps/workflow-studio/e2e/tests/21-fixes.spec.ts
M  apps/workflow-studio/package.json
M  apps/workflow-studio/package-lock.json
M  apps/workflow-studio/scripts/design-check.mjs
M  apps/workflow-studio/src/app/routes/design.tsx
M  apps/workflow-studio/src/app/routes/project.tsx
M  apps/workflow-studio/src/features/project/lib/places.ts
M  apps/workflow-studio/src/index.css
M  apps/workflow-studio/src/shared/lib/api.ts
M  apps/workflow-studio/src/shared/lib/backendTypes.ts
```
</details>
