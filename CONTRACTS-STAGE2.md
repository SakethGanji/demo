# Stage 2 contracts (frozen)

This covers Stage 2: contracts, the mistake checker, autocomplete without running, and n8n import.

- **Source design:** `DESIGN-WORKFLOWS-BACKEND.md` §4. Where the two differ, this file wins.
- **Changes:** if you need to change something here, ask the lead first. Don't change it yourself.
- **Paths:** routes follow the existing layout under `/api/workflows`, the same as Stage 1. There are no `/projects/{p}` prefixes.

## 1. Shape (engine `src/engine/shapes.py`, studio `features/workflow-editor/lib/shapes.ts`)

A shape is a JSON Schema subset, and it describes one item's `json`:
```
Shape = {
  "type": "object"|"array"|"string"|"integer"|"number"|"boolean"|"null"|"unknown",
  "properties"?: {name: Shape}, "items"?: Shape, "required"?: [str], "enum"?: [..],
  "format"?: str, "nullable"?: bool,
  "x-source"?: "derived"|"declared"|"observed"|"unknown"
}
UNKNOWN = {"type": "unknown", "x-source": "unknown"}
NodeContract = {"inputs": {port: Shape}, "outputs": {port: Shape}, "error": Shape}
```

The engine module also provides these helpers, all pure:
- `unknown()`
- `object_of(props, required=[])`
- `merge_shapes(a, b)`, a union that is lenient. A property on either side is kept. A type conflict becomes `unknown`.
- `lookup(shape, path: list[str|int]) -> Shape | None`. It returns `None` when the path definitely doesn't exist, which needs a known object with no such property. It returns `UNKNOWN` when it can't tell.
- `from_sample(items: list[dict])`, which wraps `services/schema_inference.infer_json_schema` with `x-source: "observed"`.

## 2. Node contracts (engine)

- **`BaseNode.contract`.** The signature is `BaseNode.contract(self, params: dict, inputs: dict[str, Shape]) -> NodeContract`. By default, every output port returns `UNKNOWN`.
- **Output ports.** The port names in `outputs` must match the node's real output names, such as `main`, `true`/`false` and the Switch outputs. Stage 1's `error` output is added by the checker for nodes with `on_error="error_output"`. Its shape is the input shape plus `error: {message: string, node: string}`.
- **Calculated nodes ("derived").** Set (with keep-only and include-other modes), Filter (passthrough), If and Switch (passthrough per branch), Merge (by mode: append is a union, combine/merge by key is the object union), Aggregate, ItemLists, Sample, Respond, StopAndError, Wait (passthrough), Loop (passthrough), and a disabled node of any type (passthrough).
- **Declared nodes.**
  - Start, Webhook, ExecuteWorkflowTrigger and ChatInput read `params.input_schema` (a Shape).
    - When it's absent, Webhook defaults to `{body: unknown, headers: object, query: object, method: string}`.
    - Start defaults to `{triggeredAt: string, mode: string}`.
  - Code and PythonStep read `params.output_schema`. AI Agent and LLM chat read a response/output schema param if one exists. Cron gives `{timestamp: string}`, or whatever it really emits.
- **Learned nodes ("observed").** HttpRequest, Postgres, MongoDB, Neo4j, DataTable and LLM without a schema.
  - Their output is `UNKNOWN` unless the node instance has an approved `contract`.
  - DataTable read takes its shape from the table's columns, which is "declared" and needs no approval, when the table exists.

### Instance contract field
`NodeDefinitionSchema` gains `contract: NodeContractOverride | None`:
```
{"outputs": {port: Shape}, "source": "observed", "approved_by": str, "approved_at": iso str, "from_execution": str}
```
- **Persistence.** It must survive save, load, publish (version snapshot), restore, export and import.
- **Rebuild sites.** Copy it through both `NodeDefinition` rebuild sites in `workflow_runner.py`, as in Stage 1.
- **Studio key.** In the studio's `node.data` it's `contract`, with the same object.
- **Effect.** When it's present, its `outputs` replace the node type's computed outputs.

### Learn and approve endpoints
- `POST /api/workflows/{id}/nodes/{name}/contract/learn`
  - Body: `{execution_id?: str}`. Without one, it uses the latest execution that has a saved output for the node.
  - It returns `{"outputs": {"main": Shape}, "from_execution": id, "sample_count": n}`. **It does not save.**
  - It returns 409 `{"detail": {"message", "missing_nodes": [name]}}` when there's no saved output.
- `POST /api/workflows/{id}/nodes/{name}/contract/approve`
  - Body: `{"outputs": {...}, "from_execution": id, "approved_by": str}`. `approved_by` is a typed name; there's no auth yet.
  - It writes `contract` into the draft and returns `WorkflowDetailResponse`.
- `DELETE /api/workflows/{id}/nodes/{name}/contract` removes the contract and returns `WorkflowDetailResponse`.

## 3. Checker (engine `src/services/workflow_checker.py`)

### The function
It is pure, apart from injected lookups:
```
check_workflow(definition: dict, *, lookups: CheckLookups) -> CheckResult
CheckLookups: vars_exist(name)->bool, secret_exists(name)->bool, table_exists(id_or_name)->bool,
              table_columns(id_or_name)->Shape|None, workflow_exists(id)->bool, egress_allowlist: list[str]
CheckResult = {
  "findings": [Finding], "shapes_by_node": {node_name: {"inputs": {...}, "outputs": {...}}},
  "coverage": {"refs_total": int, "refs_checked": int, "refs_unknown": int,
               "nodes_total": int, "nodes_known_output": int}
}
Finding = {"code": "WF001".."WF009", "severity": "error"|"warning"|"info", "message": str,
           "node": str|None, "param": str|None, "path": str|None}
```

### How it checks
1. Topological order.
   - A cycle that doesn't pass through a Loop node is reported as WF008 on one node in the cycle, with the message "cycle".
2. Shapes are propagated from the triggers through `contract()`.
   - A node's input shape is its upstream port shape.
   - For multi-input nodes, each input is listed separately.
3. Expressions.
   - Every string parameter, nested in dicts and lists, is scanned for `{{ … }}`.
   - Each one is transformed with the existing `expression_engine._transform_expression` and parsed with `ast.parse`; a parse failure is **WF009**.
   - Then attribute and subscript chains are walked from these roots:
     - `$json` (the current input);
     - `$input` (the first input);
     - `$node["X"].json` or `$node.X.json`;
     - `$vars.X`.

     Anything else is ignored.
4. Findings.

| Code | Severity | When |
|---|---|---|
| WF001 | error | `$node["X"]` where X doesn't exist or isn't an ancestor |
| WF002 | error | The path is missing from a **known** shape (`lookup` returns `None`). When the shape is `observed` (learned from sample runs), it's a **warning** instead, because samples can miss optional fields. |
| WF003 | warning | Type mismatch: a known string or boolean in a numeric comparison or arithmetic in the same expression, or an If/Filter number condition on a known string field |
| WF004 | warning | A non-error output port of a node with ≥2 output ports (If/Switch) that has no outgoing connection, while some other port of that node is connected; also a connected node unreachable from any trigger |
| WF005 | info | Reference into an `unknown` shape (coverage gap) |
| WF006 | error | `$vars.X` that doesn't exist; a DataTable node's table that doesn't exist; an ExecuteWorkflow target that doesn't exist; a node `credential`/secret reference that doesn't exist |
| WF007 | error | An HttpRequest URL host that is literal (no expression in the host part) and not on a non-empty project egress allowlist; same matcher as `nodes/integrations/http_request.py` |
| WF008 | error | A required node property missing or empty (from `node_description.properties` `required`), an invalid option value, or a cycle |
| WF009 | error | Expression doesn't parse |

5. Disabled nodes are checked as passthrough. Their own parameters are **not** checked.

### Endpoint and publish
- `POST /api/workflows/{id}/check`
  - Body: `{"definition"?: {nodes, connections}, "environment"?: str}`. Without a body, it checks the stored draft.
  - It returns `CheckResult`.
  - It never saves.
- **Saving** never blocks on findings.
- **Publishing** (`POST /{id}/publish`) blocks when there's any `error` finding.
  - The reply is 422 `{"detail": {"message": "Workflow has errors", "findings": [error findings]}}`.
  - Existing tests that publish must keep passing. Fix their fixtures only if they really contain errors, and report each one.

## 4. n8n import (engine `src/services/n8n_import.py`, route in a new `src/routes/n8n_import.py`)

- `POST /api/workflows/import/n8n`
  - Body: `{"n8n": <n8n workflow JSON>, "name"?: str, "project_id"?: str}`.
  - It returns `{"workflow": WorkflowCreateRequest-shaped dict, "report": Report}`. **It doesn't save.** The studio then saves through the normal create endpoint.
  - The route must be registered **before** `/{workflow_id}` routes, so `import` isn't read as an id.
- **The report:**
  ```
  Report = {"mapped": [{"n8n_name", "n8n_type", "node_type"}], "unmapped_nodes": [{"n8n_name","n8n_type","reason"}],
            "untranslated_expressions": [{"node","param","expression"}], "disabled_code_nodes": [name], "findings": [Finding]}
  ```
  `findings` is `check_workflow` run on the imported result, with real lookups for the target project.
- **Mapping table:**
  - `n8n-nodes-base.manualTrigger` and `start` become Start.
  - `webhook` becomes Webhook.
  - `scheduleTrigger` and `cron` become Cron.
  - `httpRequest` becomes HttpRequest.
  - `set` becomes Set (v1 `values.string/number/boolean` and v3 `assignments`).
  - `if`, `switch`, `merge` and `filter` map to the nodes of the same name.
  - `splitInBatches` becomes Loop.
  - `wait`, `executeWorkflow` and `executeWorkflowTrigger` map to the nodes of the same name.
  - `respondToWebhook` becomes Respond.
  - `postgres`, `mongoDb` and `aggregate` map to Postgres, MongoDB and Aggregate.
  - `itemLists` becomes ItemLists.
  - `noOp` becomes a Set passthrough.
  - `stopAndError` becomes StopAndError.
  - `code` and `function` become Code with **`disabled: true`**. The original JS source is kept in a parameter, and they're listed in `disabled_code_nodes`.
  - `stickyNote` is dropped silently.
  - Anything else goes in `unmapped_nodes` and is replaced by a disabled Set placeholder. Its name is kept, so connections survive.
- **Connections.**
  - n8n `connections[src].main[i][]` become our edges.
  - Port i maps to the target node's i-th output name: If 0→`true`, 1→`false`; Switch by index; otherwise `main`.
  - For Merge, the target input index maps to the input names our Merge uses.
- **Expressions.**
  - n8n `={{ … }}`: the leading `=` is stripped.
  - `$json.x` stays as it is.
  - `$('X').item.json.y`, `$node["X"].json.y` and `$('X').first().json.y` become `$node["X"].json.y`.
  - `$input.item.json` becomes `$json`.
  - `.length` becomes `len(...)`, and `.toLowerCase()`/`.toUpperCase()` become `.lower()`/`.upper()`, where that's simple. Everything else is kept verbatim and listed in `untranslated_expressions`.
  - `===` and `!==` become `==` and `!=`; `&&` and `||` become `and` and `or`.
- **Positions.** `position: [x, y]` becomes `{x, y}`.

## 5. Studio
- **Live check.**
  - When the draft changes, debounced to about 600 ms, and after load, the editor calls `/check` with the current definition (`toBackendWorkflow`).
  - The result goes into a new `stores/checkStore.ts`: findings by node, `shapes_by_node` and coverage.
  - A failed `/check` is silent, except for a console warning.
- **Marks on nodes.**
  - A node with an error finding shows a red mark, `data-check="error"`. One with only warnings shows amber, `data-check="warning"`.
  - Info findings show no mark.
  - The mark element is `node-check-mark`, and its tooltip lists the messages.
- **Marks on parameters.** In the node panel, a field with a finding shows the message under it (`param-check-<param>`), red for errors and amber for warnings.
- **Problems tab.**
  - The bottom panel gets a "Problems" tab (`bottom-tab-problems`) with a count badge (`problems-count`).
  - Its rows are `problem-row` with `data-code`.
  - Clicking a row selects and opens that node.
- **Autocomplete without running.**
  - The expression editor's suggestions, and the input panel's field list when there's no run data, use `shapes_by_node[node].inputs` (for `$json.`) and `shapes_by_node[X].outputs.main` (for `$node["X"].json.`).
  - Fields from a shape are marked as coming from the contract (`data-source="contract"`).
  - Run data, when present, still wins.
- **Learned contracts.**
  - In the node panel's output area, for observed-type nodes that have run data, there's a "Use as contract" button (`ndv-contract-learn`).
  - It calls learn and shows the proposed shape in a dialog (`contract-dialog`).
  - The dialog has a typed "Approved by" name (`contract-approved-by`) and Approve (`contract-approve`), which calls approve and reloads the node.
  - Nodes with an approved contract show a small badge, `node-contract-badge`.
  - "Remove contract" is `ndv-contract-remove`.
- **n8n import.**
  - The workflows list gets "Import from n8n" (`import-n8n`), which opens a dialog (`import-n8n-dialog`).
  - The dialog has a JSON paste area (`import-n8n-input`) and a Preview button (`import-n8n-preview`), which calls import and shows the report sections (`import-n8n-report`).
  - Import (`import-n8n-confirm`) creates the workflow through the normal create endpoint and opens it in the editor.
- **Publish blocked.** The publish button, on a 422, shows the error findings in a toast and opens the Problems tab.
