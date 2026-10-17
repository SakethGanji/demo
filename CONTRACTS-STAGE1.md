# Stage 1 contracts (frozen)

Every agent builds against this file. If you need to change something here, ask the lead first. Don't change it yourself.

## 1. Node definition fields (engine `NodeDefinitionSchema`, the `NodeDefinition` dataclass, and studio `node.data`)

| Engine (snake) | Studio `node.data` (camel) | Type | Default |
|---|---|---|---|
| `disabled` | `disabled` | bool | `false` |
| `on_error` | `onError` | `"stop" \| "continue" \| "error_output"` | `"stop"` |

- **Legacy field.** The engine still accepts `continue_on_fail`.
  - On load: if `on_error` is absent and `continue_on_fail` is true, then `on_error = "continue"`.
  - When writing new definitions, write `on_error` only.
  - The studio stops writing `continue_on_fail`. When it reads, it falls back to `continue_on_fail ? "continue" : "stop"`.
- **Rebuild sites.** The runner rebuilds `NodeDefinition` by hand at `workflow_runner.py` about line 630 and about line 961. Both sites must copy `disabled` and `on_error`.
- **Disabled node.** It outputs its input items unchanged on `main`. It emits normal node events, and its node_output row records the passthrough items.
- **Disabled trigger.** Saving, running or publishing a workflow whose trigger is disabled is a validation error. The error is HTTP 400 with the message `"Trigger node '<name>' is disabled"`.

## 2. Error output
- **Output name.** The output is `error`. Connections use `source_output: "error"`.
- **Item shape.** Each failed input item becomes `{"json": {...input item json, "error": {"message": str, "node": str}}}`.
- **When it applies.** Only after the retries (`retry_on_fail`) are used up.
- **Routing.** The failed items don't go to `main`. The run continues, and the node's status is `success`. The error does not go in the execution's `errors` list.
- **No error connection.** If `on_error="error_output"` and nothing is connected to `error`, the items are dropped and the run continues.
- **Other modes are unchanged.** `continue` keeps the current `continue_on_fail` behaviour, and `stop` keeps the current default.

## 3. Runner
`WorkflowRunner.run(..., stop_at: str | None = None)` and `_run_with_context(..., stop_at=None)`: when a node named `stop_at` finishes, no downstream jobs are queued.

## 4. API (all paths are under `/api/workflows`, and all success replies are `ExecutionResponse` unless noted)

### `POST /{id}/nodes/{name}/test`
```json
{ "source": "input" | "last_run" | "pinned", "input_items": [ {..json..} ], "environment": "default" }
```
- **Where the input comes from:**
  - `input`: `input_items` (required; each dict becomes `NodeData(json=dict)`).
  - `last_run`: the upstream node's output from this workflow's latest execution that has a saved output for that upstream node.
  - `pinned`: the upstream node's `pinned_data`.
- **Upstream node.** It is the source of the first connection into `name`. If `name` is a trigger, its input is `[]` and that's fine.
- **Errors:**
  - 409 `{"detail": {"message": "...", "missing_nodes": [names]}}` when `last_run` or `pinned` finds no data.
  - 404 for an unknown node.
- **What runs.** Only `name` runs (`start_node_name=name`, `stop_at=name`).
- **Tagging.** The execution is saved with `exec_metadata.trigger = {"type": "node_test", "node": name}` and `mode="manual"`.

### `POST /{id}/run-to/{name}`
- The body is optional: `{ "input_data"?: dict, "environment"?: str }`, the same as `/run`.
- It's a normal run from the trigger with `stop_at=name`.
- It's tagged with `trigger = {"type": "run_to", "node": name}`.

### `POST /{id}/run-from/{name}`
- The body is `{ "execution_id"?: str }`. When it's omitted, the most recent of the last 20 executions that has saved outputs for every ancestor is used, like n8n, so a node test doesn't hide the last full run. If none qualifies, the 409 lists what the latest execution is missing.
- **What gets reused.** All ancestors of `name` (by graph) get `pre_populated_states` from that execution's successful node_outputs, and execution starts at `name`.
- **Input to `name`.** It comes from its upstream outputs. A Merge node receives every input.
- **Errors:**
  - 409 `{"detail": {"message": "...", "missing_nodes": [names]}}` if any ancestor lacks a saved output.
  - 404 if there is no execution.
- **What gets saved.** The new execution saves node_outputs only for the nodes it actually ran: `name` and the nodes downstream of it.
- **Tagging.** `trigger = {"type": "run_from", "node": name, "source_execution": id}`.

### `POST /{id}/versions/{version_id}/restore`
- It copies the version's definition into `draft_definition`.
- It returns `WorkflowDetailResponse`, the same as `GET /{id}`.
- It returns 404 for an unknown version.

## 5. Studio
- **Editor URL.** `/…/editor?execution=<id>` (the existing editor route plus a query param) opens the editor in "viewing run" mode.
- **Run data in the editor.** It comes from `GET /api/executions/{id}` (`node_data`). It fills the same store a live run fills.
- **The error handle.** Its React Flow handle id is `error` (source handle), and it maps to `source_output: "error"`.
- **Pure functions.** `features/workflow-editor/lib/workflowDiff.ts` and `lib/autoLayout.ts`:
  ```ts
  diffWorkflows(base: WorkflowDefinition, target: WorkflowDefinition): {
    added: string[]; removed: string[]; changed: string[];  // node names; position-only changes are ignored
  }
  autoLayout(nodes: {id: string; width?: number; height?: number}[], edges: {source: string; target: string}[]):
    Record<string, {x: number; y: number}>  // left→right by depth, branches spaced vertically; deterministic
  ```

## 6. New `data-testid`s
- **Node context menu:** `ctx-test-step`, `ctx-run-to`, `ctx-run-from` and `ctx-toggle-disable`.
- **Node panel:**
  - `ndv-test-step`;
  - the source switch: `ndv-test-source-input`, `ndv-test-source-last-run` and `ndv-test-source-pinned`;
  - `ndv-test-input-editor` and `ndv-pin-from-last-run`.
- **Node settings and the canvas:**
  - the select: `node-setting-on-error`, with options `on-error-stop`, `on-error-continue` and `on-error-error-output`;
  - the error handle: `node-error-handle`;
  - the disabled node class: `node-disabled`.
- **Runs:** `run-open-in-editor`.
- **Viewing-run banner:** `viewing-run-banner`, `viewing-run-back-to-live` and `viewing-run-pin`.
- **History:**
  - the sheet: `navbar-history`, `history-sheet` and `history-version-<version_number>`;
  - restore: `history-restore`;
  - the diff markers on nodes: `diff-added`, `diff-removed` and `diff-changed` (as `data-diff` attributes).
- **Palette:** `command-palette`, `command-palette-input` and `cmd-<action>`, where action is one of `add-node`, `run`, `test-step`, `run-to`, `run-from`, `toggle-disable`, `history`, `tidy-up` and `zoom-fit`.
