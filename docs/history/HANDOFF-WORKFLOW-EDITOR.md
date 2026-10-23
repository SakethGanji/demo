# Handoff: build the workflow editor, Stage 1 (start here)

Written 2026-10-17. This supersedes `HANDOFF-INTEGRATION.md` as the entry point. All paths are relative to `demo/`.

## 1. Read these, in this order
1. This file (all of it).
2. `PLAN-WORKFLOW-EDITOR.md`: the product plan, Stages 1-9. **You are building Stage 1.**
3. `screenshots/concepts/workflow-editor-with-ui.png`: the target picture (Stage 5 makes it real; Stage 1 doesn't touch the Interface panel).
4. `DESIGN-WORKFLOWS-BACKEND.md` §2 (what exists) and §5.2 (runner options). The rest is for later stages.
5. `apps/workflow-studio/DESIGN-STANDARD.md` before writing any UI.

## 2. Rules from the owner (follow exactly)
- **Features only.** No login, roles or audit work.
- **Backend first**, with tests, then the UI, then Playwright.
- **Don't commit or push** until the owner says so. Agents never touch git.
- **Keep the approved look:** use existing `apps/workflow-studio/src/shared/components/ui/*` and the tokens in `src/index.css`. Don't invent colours, borders or gradients. Run `npm run design:check`.
- **The owner reads replies in chat, not documents.** Report progress in plain language as short ordered lists. Keep plans in repo files.
- When a decision isn't covered here, pick the option that matches n8n's behaviour and say so in the reply.

## 3. Environment
- **Postgres:** Docker `workflow-engine-postgres-1` on `:5433` (`docker start workflow-engine-postgres-1`).
- **Dev engine:** `cd apps/workflow-engine && set -a && . ./.env.local && set +a && venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8000`.
- **Dev studio:** `cd apps/workflow-studio && npx vite --port 5173 --host 127.0.0.1`.
- **Dev DB:** `workflows_studio_dev`, seeded with Payments Ops, Billing sync and Support desk. Leave the old `workflows` DB alone (stale schema).
- **`apps/workflow-engine/.env.local`** holds a live Gemini key. **Never print it, log it, or copy it** into files, prompts or screenshots. Stage 1 needs no LLM calls.
- **Engine tests:**
  - Unit (~2 s): `cd apps/workflow-engine && venv/bin/python -m pytest tests/unit -q`.
  - Full suite (needs Postgres): `venv/bin/python -m pytest -q`. Baseline: 509 passed + 2 expected xfails.
- **Studio:**
  - Unit: `npm run test:unit` (105).
  - Types: `npx tsc -b`.
  - Lint: `npm run lint` (baseline 96 problems; don't go above).
  - Design check: `npm run design:check`.
- **E2E:** `flock /tmp/e2e.lock npx playwright test -c e2e [file]` from `apps/workflow-studio`. Baseline: 114 passing.
  - Run **one at a time**; the stack state lives in `e2e/.stack/state.json`.
  - After an aborted run, kill leftover `uvicorn --app-dir` and `vite --strictPort` processes **by PID** (never `pkill -f`; it can kill your own shell).
  - Drop leftover DBs with `venv/bin/python tests/e2e_stack.py down --db <name>`.
- **Git:** branch `revert-to-5684ba5`, HEAD `bbd49dc` at the time of writing.

## 4. What already exists (don't rebuild)
- **Runner:** `apps/workflow-engine/src/engine/workflow_runner.py`. `run()` (line 51) already takes `start_node_name`, `initial_data` and `pre_populated_states`. Read lines 51-270 and 552-935 before changing anything.
- **Pinned data:** `node_def.pinned_data` skip path, around line 606.
- **Node definition:** `apps/workflow-engine/src/schemas/workflow.py` `NodeDefinitionSchema` (has `retry_on_fail`, `continue_on_fail`) and the dataclass in `src/engine/types.py`.
- **Routes:** `src/routes/workflows.py` (`/run` at line 158; `/versions` and `/versions/{id}` exist) and `src/routes/executions.py` (`/retry` exists).
- **Saved per-node outputs:** table `node_outputs`; `NodeOutputRepository.get_successful_outputs(execution_id)`.
- **Editor:** `apps/workflow-studio/src/features/workflow-editor/`.
  - Node panel: `components/ndv/NodeDetailsModal.tsx`. Its execute button (around line 105) runs the **whole** workflow; that's a bug to fix.
  - Canvas: `components/canvas/*` (`NodeContextMenu.tsx` is the right-click menu).
  - Shortcuts: `hooks/useKeyboardShortcuts.ts`.
  - Run stream: `hooks/useExecutionStream.ts`.
  - Stores: `stores/workflowStore.ts`, `stores/ndvStore.ts`.

## 5. Stage 1 tasks (do them in this order; finish tests for each before the next)

### 1.1 Stop at a node (engine)
- Add `stop_at: str | None = None` to `WorkflowRunner.run()` and `_run_with_context`.
- In `_queue_next_nodes`, don't queue downstream jobs when the finished node's name equals `stop_at`.
- **Tests:** new `tests/unit/test_runner_partial.py`. A 3-node chain with stop at the middle node runs exactly 2 nodes. Stopping at a branch node doesn't run either branch.

### 1.2 Test one step, run to here, run from here (engine + API)
- `POST /api/workflows/{id}/nodes/{name}/test`, body `{input_items?: list[dict], source: "input"|"last_run"|"pinned", environment?: str}`. Runs only that node (`start_node_name=name`, `stop_at=name`).
  - Input comes from: the body; or the upstream node's output in the workflow's latest execution; or the upstream pin.
  - Returns the normal `ExecutionResponse`.
  - Tag the execution: `exec_metadata.trigger = {"type": "node_test", "node": name}`.
- `POST /api/workflows/{id}/run-to/{name}`: a normal run from the trigger with `stop_at=name`.
- `POST /api/workflows/{id}/run-from/{name}`, body `{execution_id?: str}` (default: the latest execution).
  - Load that execution's successful outputs and pass every upstream node's output as `pre_populated_states`.
  - Start at `name`.
  - If an upstream node has no saved output, return 409 with the names of the missing nodes.
- Put the logic in `services/workflow_service.py` (or a new `services/partial_run.py`), not in the route.
- **Tests:** new `tests/integration/test_stage1_run_debug.py`:
  - A node test with typed input.
  - A node test with last-run input.
  - Run-to stops correctly.
  - Run-from re-executes only the downstream nodes (count rows in `node_outputs` for the new execution).
  - Run-from returns 409 when an upstream output is missing.
  - Merge node: run-from below a Merge gets both inputs.

### 1.3 Disable a node (engine + UI)
- Add `disabled: bool = False` to `NodeDefinitionSchema`, the dataclass and `to_dataclass`.
- In `_process_job`: a disabled node passes its input items straight to its `main` output (like n8n).
- A disabled **trigger** is a validation error.
- Studio:
  - The node context menu gets "Disable / Enable", with plain `D` as the shortcut (⌘D is already Duplicate in `useKeyboardShortcuts.ts:188`; plain `d` is free).
  - A disabled node renders faded with its label struck through.
  - The flag must survive save, load, export and import.
- **Tests:**
  - Unit: passthrough, with multiple items.
  - Integration: save and reload keeps the flag.
  - Playwright: disable a node, run, see items pass through.

### 1.4 Error output per node (engine + UI)
- Add `on_error: "stop" | "continue" | "error_output" = "stop"` to the node definition. Map the old `continue_on_fail=true` to `"continue"` when loading (keep reading the old field; write only the new one).
- `error_output`: when the node fails (after retries), send items to a second output named `error`. Each item carries the input item's json plus `{"error": {"message", "node"}}`. The run doesn't stop. Downstream of `main` gets no items from the failed item.
- Studio:
  - Node settings get an "On error" select.
  - When it's `error_output`, the node shows a second, red output handle labelled "error", which can be connected like any output.
- **Tests:**
  - Unit: each mode, including with retries.
  - Integration: a workflow where an HTTP node fails and its error branch writes to a table.
  - Playwright: connect the error handle, run, see the error branch execute.

### 1.5 Debug a past run in the editor (UI, small API)
- In Runs (`features/runs`), add an "Open in editor" button on a run. It links to the editor with `?execution=<id>`.
- The editor loads that execution (`GET /api/executions/{id}` already returns node outputs; check `ExecutionDetailResponse`). It then fills the same run-data store that a live run fills, so every node shows that run's input and output and the node panel works as after a run.
- Show a banner: "Viewing run #<id> from <time>" with "Back to live" and "Pin this data" (pins the selected node's output).
- **Tests:** Playwright: run a workflow, open the run from Runs, see output on a node, pin it.

### 1.6 Fix the node-panel button, add the canvas actions (UI)
- In `NodeDetailsModal.tsx`, replace the whole-workflow execute call with **Test step**, using the 1.2 node test API.
  - Add a source switch: "Typed input | Last run | Pinned".
  - Typed input opens a JSON editor; reuse the existing CodeMirror setup from `ndv/ExpressionEditor.tsx`.
- Node right-click menu: add "Test step", "Run to here", "Run from here".
- Add **Pin from last run** next to the existing pin button.
- **Tests:** Playwright: test one step without running the others (the other nodes show no new output); run to here; run from here.

### 1.7 Version history with diff and restore (API + UI)
- Add `POST /api/workflows/{id}/versions/{version_id}/restore`: copies that version's definition into `draft_definition` and returns it.
- Studio:
  - A "History" button in `WorkflowNavbar` opens a sheet listing versions (number, time, node count).
  - Selecting a version shows a diff **on the canvas**: compare nodes by `name`; added nodes outlined green, removed shown as ghosts, changed (parameters differ) outlined amber; ignore position-only changes.
  - Put the diff logic in a pure function, `lib/workflowDiff.ts`, with vitest tests.
  - "Restore this version" calls the API and reloads.
- **Tests:**
  - vitest for `workflowDiff` (added, removed, changed, position-only ignored).
  - Integration for the restore endpoint.
  - Playwright: publish v1, edit, see the diff, restore.

### 1.8 Command palette and tidy up (UI)
- **⌘K palette** for editor actions: add node, run, test step, run to/from here, disable, history, tidy up, zoom to fit. Build it on `src/shared/components/ui/command.tsx`, which already exists; `search-menu.tsx` is a reference.
- **Tidy up:** a pure function `lib/autoLayout.ts` that lays nodes out left to right by graph depth, spacing branches vertically. Use a layout library only if one is already in `package.json`. Add vitest tests for it.
- **Tests:** vitest for `autoLayout`; Playwright: open the palette with ⌘K, run "Tidy up", positions change.

## 6. Stage 1 done when
1. All new tests pass.
2. The full engine suite has 509+ passed and the same 2 xfails.
3. The studio unit tests, `tsc`, `design:check` and lint (≤ 96) all pass.
4. All existing e2e specs plus the new `apps/workflow-studio/e2e/tests/14-editor-run-debug.spec.ts` pass.
5. **The demo works** on a "Refund triage" workflow. It is **not** in the dev seed; it only exists in the live Marketplace test (`apps/workflow-engine/tests/live/marketplace_steps.cjs`). So:
   - First add a fixture, `apps/workflow-engine/tests/fixtures/workflows/refund_triage.json`: Webhook (amount, order_id, reason) → Set → If (amount > 500) → HttpRequest to a local fake server → DataTable insert on both branches.
   - Have the e2e stack create it (and a seeded failed run, made by pointing the HTTP node at a failing URL) for spec 14. Optionally add it to `src/db/seed.py` for the dev DB.

   Then the demo:
   - Open the failed run in the editor.
   - See the data on every node.
   - Fix one node and test that step alone.
   - Run from that node.
   - Restore the previous version from History.

Then report to the owner in chat: an ordered list of what was built, the test counts before and after, and anything skipped and why. Wait for "commit" before committing. Then start Stage 2 from `PLAN-WORKFLOW-EDITOR.md`.

## 7. Working tips
- **Work one task at a time;** after each change run the unit tests first (seconds), then the related integration file.
- **Don't refactor code you aren't changing.** Don't rename existing test IDs; e2e relies on about 174 `data-testid`s. Add a `data-testid` to every new control.
- **If you use helper agents,** run at most 3 at once, give each a disjoint set of files, and don't let them run e2e in parallel.
- **Screenshots:** headless Chromium works for checking UI (`chromium --headless --screenshot=...`). Look at a screenshot before saying a UI change is done.
- **If a step is unclear or blocked,** do the rest of the stage, then list the blocked item and your question for the owner in the report.
