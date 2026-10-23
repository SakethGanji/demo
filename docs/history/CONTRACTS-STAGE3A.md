# Stage 3A contracts (frozen)

Stage 3A covers the testing core inside the editor.

- **Source design:** `HANDOFF-STAGE3.md` §4–§5, then `DESIGN-WORKFLOWS-BACKEND.md` §5. Where they differ, this file wins.
- **Changes:** to change anything here, ask the lead first.
- **Paths:** routes stay under `/api/...`, with no `/projects/{p}` prefix, the same as Stages 1 and 2.
- **Engine paths** are relative to `apps/workflow-engine/src`. **Studio paths** are relative to `apps/workflow-studio/src/features/workflow-editor` unless shown otherwise.

---

## 1. Test data in the workflow definition

The workflow definition (`WorkflowDefinition` schema, the repository dumps at `workflow_repository.py` ~49/133/296, version snapshots, restore, export and import) gains two optional top-level keys. Both must survive every one of those paths.

```
tests: [TestCase]                      # default []
test_defaults: {                       # default {}
  mocks: {<node name>: Mock},          # one default mock per outside step, shared by all tests
  now?: iso str,                       # frozen clock; default "2026-01-01T09:00:00+00:00"
  seed?: int                           # default 1
}
```

### TestCase
```
{
  id: str,                              # "t_" + 8 lowercase hex, client or server generated, unique per workflow
  name: str (1..120),
  description?: str,
  target: {kind: "workflow"} | {kind: "node", node: str} | {kind: "range", from: str, to: str},
  input:  {items: [json]},              # workflow: trigger output items; node/range: items fed into target / from
  mocks:  {<node>: Mock},               # overrides test_defaults.mocks for this test only
  expect: [Assertion],
  category: Category,
  field?: str,                          # input field the category is about (grid detail), e.g. "amount"
  covers?: [str],                       # nodes this test counts for in the grid; default below
  status: "draft" | "approved",
  locked: bool,                         # true exactly when approved
  approved_by?: str, approved_at?: iso str,
  source: "human" | "suggested" | "replay" | "ai",
  now?: iso str, seed?: int             # per-test override of test_defaults
}
Category = "normal" | "boundary" | "missing" | "wrong_type" | "unusual"
         | "ext_success" | "ext_error" | "ext_timeout" | "ext_empty"
```
- **Default `covers`:** for a node target, `[node]`. For a workflow or range target, the distinct nodes named in `expect`.
- **Draft tests** are run and reported, but never block anything.

### Lock rules (enforced server-side on `PUT /api/workflows/{id}`)
- A save that changes or deletes a locked test, or sets `status`/`locked`/`approved_*` on any test, is refused with 409 `{"detail": {"message": "Locked tests can't be changed", "test_ids": [...]}}`.
- **Renames.** `PUT` accepts an optional top-level `node_renames: {old: new}`. The server first applies `rename_node_in_tests` to the **stored** tests, then compares. A rename alone never trips the lock. The studio sends `node_renames` whenever it renamed nodes since the last save.
- **Approve:** `POST /api/workflows/{id}/tests/{test_id}/approve` with body `{"approved_by": str}`. It sets `status=approved`, `locked=true` and `approved_at`, saves the draft, and returns `WorkflowDetailResponse`. It returns 404 for an unknown test id.
- **Unlock:** `POST /api/workflows/{id}/tests/{test_id}/unlock` with body `{"by": str}`. It sets `status=draft` and `locked=false`, clears `approved_*`, and returns `WorkflowDetailResponse`.

## 2. Mocks

```
Mock = {items: [json], port?: str}                 # node outputs these items on port (default "main"), like pinned data
     | {http: {status: int, body?: any, headers?: {str: str}}}   # HttpRequest only, see below
     | {error: {message: str, code?: str}}         # node raises this error
     | {timeout: true}                             # node raises TimeoutError("TEST_MOCK_TIMEOUT: <node>")
     | {rules: [{when: {path, op, value}, then: Mock}], default?: Mock}   # smart mock
```
- **Expressions.** String values inside `items`, `http.body` and `error.message` may hold `{{ … }}`. They're resolved per input item, the same as node parameters, which covers "echo order_id".
- **Rules.** `rules` are tested per input item, against that item's `json`, using the assertion ops of §4. The first match wins, then `default`. With no match and no default, the node counts as unmocked.
- **Priority.** For each node in a test run: test `mocks` > `test_defaults.mocks` > node `pinned_data` > real execution (only when the node's effect is `none`) > **unmocked failure**.
- **The `http` mock is served through the real HttpRequest code.** In test mode, the runner gives the HttpRequest node an `httpx.MockTransport` client that:
  - records the request it receives;
  - returns the mocked status, body and headers.

  So URL and body building, redaction and the node's own handling of a 404 (which is a reply carrying `statusCode`, **not** an exception) all run exactly as in production. `error` and `timeout` mocks raise from inside the transport.
- **Other node types.** For any other node, `items` skip `execute` like pinned data. `error` and `timeout` raise **inside the retry loop** of `_process_job`, so `retry_on_fail` and `on_error` (stop / continue / error_output) behave as in production.

## 3. Test mode in the runner (`engine/workflow_runner.py`, new `engine/test_mode.py`, `engine/effects.py`)

### Effects
- `engine/effects.py` holds `NODE_EFFECTS: dict[node_type, "none"|"network"|"database"|"llm"|"subworkflow"]` and `effect_of(node_type)`.
- A unit test asserts every registered node type is in the table.
- The values:

| Effect | Node types |
|---|---|
| network | HttpRequest, SendEmail, Poll, and Aggregate, Sample and Profile, which send their items to the analytics service (amended by the lead after Wave 2) |
| database | Postgres, MongoDB, Neo4j, DataTable (all operations, because tests must not read or write real tables) |
| llm | LlmChat, AiAgent |
| subworkflow | ExecuteWorkflow (n8n-style: the child has its own tests; here it's an outside step that needs a mock) |
| none | everything else, including triggers, Code and PythonStep (sandboxed), Wait and Respond |

### RunOptions
`WorkflowRunner.run(..., test_options: TestRunOptions | None = None)`:
```
TestRunOptions = {mocks: {node: Mock}, now: datetime, seed: int, persist: bool}
```
When `test_options` is set:
1. **Unmocked outside steps.** A node with effect ≠ none and no mock, pinned data or disabled flag fails with error code **`TEST_UNMOCKED_EFFECT`**.
   - Details: `{"node", "effect", "fixes": ["generate_from_spec", "use_last_real_reply"]}`.
   - This failure **bypasses** retries and `on_error`. It's a test-setup failure, not production behaviour, so the run stops.
2. **Frozen clock.** `ExecutionContext` gains `frozen_now: datetime | None` and `rng: random.Random`.
   - The expression helpers `now`, `date_now` and `timestamp` (`expression_engine.py:96-98`), the Sample node, and the current-time and random-number tools read from them.
   - Code and PythonStep aren't frozen; the repeatability check flags them if they vary.
3. **Wait** doesn't sleep.
4. **Secrets and credentials.**
   - Credentials are not resolved.
   - `$vars` values marked secret resolve to `"<secret:NAME>"`. Non-secret vars resolve normally.
5. **Request capture.** The context holds `test_requests: {node: [Request]}`.
   - HttpRequest: `{method, url, headers, body}`, one per request.
   - Other outside steps: `{params}`, the resolved parameters once per job, with `_credentials` removed.
6. **Saving runs.** `persist=True` saves the execution with `mode="test"`. Runs lists (`GET /api/executions` and the workflow's runs) **exclude** `mode="test"` unless `?include_test=true`. Each test-run request deletes test executions older than 7 days. `persist=False` writes nothing to the DB (used by planted bugs and publish).

## 4. Assertions (`engine/assertions.py`, pure)

```
Assertion = {
  id?: str,
  subject?: "output" | "request" | "node",   # default "output"
  node: str,
  port?: str,          # output only; default "main"
  item?: int | "all" | "any",   # default 0; "all" means every item must pass, "any" means at least one
  path?: str,          # dotted with [i]: "body.items[0].id"; "" or absent means the whole item json
  op: "eq"|"neq"|"gt"|"gte"|"lt"|"lte"|"contains"|"matches"|"exists"|"not_exists"
      |"count"|"type"|"one_of"|"range"|"status"|"shape",
  value?: any
}
```
- **Subjects.**
  - `output` reads the items the node emitted on `port`.
  - `request` reads `test_requests[node]`, where `item` indexes the requests.
  - `node` is used with `status` only.
- **Ops.**
  - `count`: the number of items on the port (or requests) equals `value`. `path` and `item` are ignored.
  - `status`: `value ∈ {"success","error","no_output","not_run"}`. `no_output` means the node ran but emitted nothing on any port.
  - `type`: `value ∈ {"string","integer","number","boolean","object","array","null"}`. An integer also counts as `number`, but `bool` isn't a number.
  - `range`: `value = {min?, max?}`, both inclusive.
  - `one_of`: `value` is a list.
  - `matches`: `re.search`.
  - `contains`: a substring of a string, a member of a list, or a key of a dict.
  - `exists` and `not_exists`: whether the path resolves. A value of `null` counts as existing.
  - `shape`: `value` is a Shape. When it's absent, the node's contract output for `port` is used. When that's `unknown`, the assertion passes with `note: "no contract"`.
  - Comparing different types (`gt` on a string against a number) **fails**, with the message "type mismatch". It doesn't raise.
- **Result:** `evaluate(assertion, run) -> AssertionResult`:
  ```
  AssertionResult = {"passed": bool, "node", "port", "path", "op", "expected", "actual", "message", "note"?}
  ```

### Contract checks on every run
- `engine/shapes.py` gains `validate(shape, value, path="") -> [Violation]`, where `Violation = {"path", "expected", "actual", "message"}`.
- Shape gains the optional keywords `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`.
- `validate` follows `unknown` leniently. Extra properties are allowed. A missing `required` property is a violation, and so is a type, enum or keyword miss. `nullable` allows null.
- Every test run validates every emitted item of every node against `shapes_by_node[node].outputs[port]` from `check_workflow`, but only where `x-source` is `declared`, `derived` or `spec`.
  - A miss there is a failure of kind `contract`.
  - An `observed` shape gives a warning in `warnings`, not a failure.

### User-tightened types
`NodeContractOverride` gains:
- `inputs?: {port: Shape}`;
- `source` also allowing `"declared"`.

A declared `inputs` shape is checked against the items arriving at that node. A miss there is a failure of kind `contract` with `"direction": "input"`.

## 5. Test runner service and API (`services/test_runner.py`)

- `run_tests(stored_workflow | definition, *, ids=None, persist=True) -> TestRunReport`. The tests run one after another.
- **Targets.**
  - A `node` target uses the Stage 1 partial-run options: `start_node_name=node`, `stop_at=node`, `initial_data_override=input.items`, and the Merge seeding from `partial_run`.
  - A `range` target starts at `from` and stops at `to`.
  - A `workflow` target starts at the trigger, with the trigger's output replaced by `input.items`.

```
POST /api/workflows/{id}/tests/run   {"ids"?: [str], "definition"?: {nodes, connections, tests, test_defaults}}
  -> TestRunReport   # "definition" runs the unsaved draft (live editing); never saves the workflow
TestRunReport = {
  "results": [TestResult],
  "summary": {"total","passed","failed","errored","approved_total","approved_failed"},
  "badges": {node: "pass"|"fail"},          # a node is "fail" if any failure points at it, else "pass" if any test covers it
  "grid": Grid
}
TestResult = {
  "test_id","name","status": "passed"|"failed"|"error","duration_ms","execution_id": str|None,
  "assertions": [AssertionResult], "failures": [Failure], "warnings": [Failure],
  "node_status": {node: "success"|"error"|"no_output"|"not_run"},
  "ports_taken": {node: [port]},            # ports that emitted ≥1 item (used by branch coverage)
  "requests": {node: [Request]},
  "outputs": {node: {port: [json]}}         # first 20 items per port
}
Failure = {"kind": "assertion"|"contract"|"unmocked"|"node_error"|"setup",
           "node": str|None, "port"?: str, "path"?: str, "expected"?: any, "actual"?: any,
           "message": str, "fixes"?: [str], "direction"?: "input"|"output"}
```
- **Status.** A test is `passed` when it has no failures. It's `error` when there's an `unmocked` or `setup` failure, or a node error the test didn't expect: a node with status `error` that has no `status` assertion expecting `error`. Otherwise it's `failed`.

### Lead amendments after testing (2026-10-17)
1. **Triggers.** In a workflow-target test, every trigger type emits exactly `input.items` without running its own logic. Any default payload uses the frozen clock.
2. **Rule leaves.** A leaf mock under `rules` behaves exactly like the same mock at the top level. An `{items}` leaf on HttpRequest emits the items directly.
3. **Error items.** Items that carry `_errorNode` (passed on by `on_error: continue`) are not contract-checked. The `error` port is checked against the error shape.
4. **Trigger shapes.** For workflow-target tests, the shapes downstream of the trigger are recomputed from the test's own `input.items` (`from_sample`, `x-source: "derived"`). Intentionally bad inputs then don't produce contract noise. Node-level declared, approved, spec and table contracts are still checked.

### Grid
```
Grid = {"categories": [Category], "rows": [{"node", "categories": {Category: {"status": "pass"|"fail"|"none"|"na", "tests": [id]}}}]}
```
- **Which cells apply.** Wave 1 uses a default. Trigger nodes have no row. Effect ≠ none nodes have the `ext_*` columns plus normal. Other nodes have normal, boundary, missing, wrong_type and unusual, except that `boundary` is `na` for nodes that aren't If, Switch or Filter. Wave 2 recipes replace this default via `recipe_for(type).categories`.
- **Cell status.** A cell is `pass` when every covering test passed, `fail` when any failed, and `none` when no test covers it.

## 6. API catalog (`services/api_catalog.py`, `services/openapi_import.py`, `services/mock_gen.py`, `routes/api_specs.py`)

- **Table `api_specs`.** New migration `src/db/migrations/20261017120000_api_specs.sql`. Columns: `id`, `project_id`, `name`, `base_url`, `spec` (jsonb, normalised), `source` (`paste`|`upload`|`url`|`example`), `source_url`, `created_at` and `updated_at`.
- **Formats.** It accepts OpenAPI **3.0/3.1 only**, as JSON or YAML. Swagger 2 gets 422 with "Swagger 2 isn't supported; convert to OpenAPI 3". Local `$ref`s are resolved; remote refs are a 422.

```
GET    /api/api-specs?project_id=            -> [ApiSpecSummary {id, name, base_url, operation_count, source}]
POST   /api/api-specs  {project_id, name?, spec_text?: str, url?: str}   -> ApiSpecDetail (422 on bad spec, message says why)
       url fetch: the host must pass the project's egress allowlist (same matcher as HttpRequest); 10 s timeout; 2 MB cap
POST   /api/api-specs/from-example {project_id, name, method, base_url, path, status?: 200, example: any} -> ApiSpecDetail
       builds a one-operation OpenAPI doc whose response schema is inferred from the example (source "example")
GET    /api/api-specs/{id}        -> ApiSpecDetail
DELETE /api/api-specs/{id}        -> {"success": true}
GET    /api/api-specs/{id}/operations/{operation_id}/mocks -> [MockOption]
ApiSpecDetail = {id, name, base_url, source, operations: [Operation]}
Operation = {operation_id, method, path, summary, path_params: [str], request_shape: Shape|None,
             responses: {"<status>": {description, shape: Shape|None, example: any|None}}}
MockOption = {label: "200 success"|"404 Not found"|..., category: Category, mock: Mock}
```
- **`operation_id`** is the spec's `operationId`. When that's missing, it's `"<METHOD> <path>"`.
- **MockOptions.** There's one per documented response:
  - 2xx gives `ext_success`;
  - 4xx and 5xx give `ext_error`;
  - plus `{timeout: true}` as `ext_timeout`;
  - plus a 2xx with an empty body (`{}` or `[]` by schema) as `ext_empty`.
- **Mock bodies** come from the spec's example when it has one. Otherwise they come from `mock_gen.example_from_shape(shape, seed)`, which is deterministic. It uses the first `enum` value and `minimum` (or 1); dates are `2026-01-01T09:00:00Z`; a uuid is `00000000-0000-4000-8000-000000000001`; other strings are `"<field>_example"`.
- **Shapes from spec schemas** get `x-source: "spec"`. `shapes.py` accepts `"spec"` as a known source, which the checker treats like `declared`.

### HttpRequest "API → endpoint"
- **New params:** `api_ref: {spec_id, operation_id} | null` and `path_params: {name: expr}`.
- **At run time,** when `api_ref` is set, the method comes from the operation, and the URL is `base_url + path` with `{name}` filled from the resolved `path_params`. The `url` param is ignored.
- **Spec lookup.** The service loads the referenced specs into `context.extras["api_specs"]` before every run (normal or test).
- **Contract.** `node_contract(node_def, inputs, *, table_columns=None, api_operation=None)` produces the output `main` for an `api_ref` node:
  ```
  {statusCode: integer, headers: object, body: <shape of the first 2xx response>}
  ```
  It's `x-source: "spec"`, and `body` is lenient on non-2xx.
- **Checker.** `CheckLookups` gains `api_operation(spec_id, operation_id) -> Operation | None`. A missing spec or operation gives **WF006**.

### Mocks for one node (`routes/test_mocks.py`)
```
POST /api/workflows/{id}/nodes/{name}/mock/generate  {"kind": "from_spec"|"from_contract"|"last_real", "status"?: int}
  -> {"mock": Mock, "source": str}            # never saves; the studio writes it into test_defaults or the test
```
- **`from_spec`** needs `api_ref`. It takes the 2xx response, or the given `status`.
- **`from_contract`** builds items from the node's output shape (approved contract, declared, or DataTable columns).
- **Postgres** reads only `information_schema.columns` through the node's credential, when its operation names a table. When it can't, it returns 409 `{"detail": {"message", "hint": "paste an example"}}`.
- **`last_real`** comes from the latest successful non-test execution output. For HttpRequest it becomes `{http: {status, body, headers}}`; for everything else it's `{items}`. When there isn't one, it returns 409.

## 7. Wave 2 (frozen now so Wave 1 builds toward it)

### Recipes (`engine/test_recipes.py`)
- `recipe_for(node_type) -> Recipe {checks: [str], categories: [Category], suggest(node_def, shapes) -> [TestCase]}`. The recipes follow the table in HANDOFF §4.
- **Suggesting:**
  - `POST /api/workflows/{id}/tests/suggest {"node"?: str}` returns `[TestCase]` drafts (`source: "suggested"`), which are not saved.
  - Boundaries are read from If, Filter and Switch conditions: `{{ $json.amount > 500 }}` gives 500 and 501 for integer fields (500 and 500.01 for number fields), and the `minimum`/`maximum`/`maxLength` of declared input shapes give their edges.
  - For outside steps, there's one draft per MockOption.
  - Category value generators live in `engine/data_categories.py`: `values_for(shape) -> {Category: [value]}`.

### Save as test
`POST /api/workflows/{id}/tests/from-execution {"execution_id", "name"?}` returns one TestCase draft (`source: "replay"`):
- the input is the trigger output;
- the mocks are the recorded outputs of effect ≠ none nodes;
- the expect list has `count` per taken port of If and Switch, `status: success` on the last nodes, and `shape` on every node with a contract.

### Scorecard (`services/scorecard.py`)
```
POST /api/workflows/{id}/score {"definition"?, "mutations"?: true, "repeat"?: 0..20}
score(definition, lookups, *, mutations=True, repeat=0) -> Scorecard
Scorecard = {
  "checker": {"errors": int, "warnings": int},
  "tests": TestRunReport.summary,
  "coverage": {"branches_total", "branches_taken", "untaken": [{"node","port"}], "grid_gaps": int},
  "mutation": {"total","caught","survived": [{"id","operator","node","description"}]} | None,
  "repeatability": {"runs","identical": bool,"differing_tests": [id]} | None,
  "gaps": [{"node","category","reason"}],
  "bar": "14/14 passing · 1 gap · 19/20 planted bugs caught"
}
```
- **Branches** are every output port of If and Switch, plus the `error` port of nodes with `on_error="error_output"`.
- **Planted-bug operators** (at most 30 mutants, picked deterministically by seed, run with `persist=False`):
  1. `flip_compare`: `>`↔`>=`, `<`↔`<=`, `==`↔`!=` in If, Filter and Switch conditions.
  2. `change_set_value`: a number +1, a string + `"_x"`, a boolean negated.
  3. `swap_branches`: swap an If node's true and false connections.
  4. `drop_node`: make one non-trigger node disabled.
  5. `break_expression`: replace one `$json.x` reference with `None`.
- **Caught** means at least one test that passes on the original fails on the mutant.

### Publish and release block
- **Publish.** `POST /api/workflows/{id}/publish` runs the approved tests with `persist=False` after the checker passes. If any fail, it returns 422:
  ```
  {"detail": {"message": "Approved tests fail", "failed_tests": [{"test_id","name","failures"}]}}
  ```
- **Release.** `release_service.run_checks` adds the check `workflow_tests_pass` per workflow in the snapshot.
- Drafts are never required.

### AI-ready foundations
- **`services/workflow_toolkit.py`.** Pure functions on definition dicts:
  - `add_node`, `remove_node`, `set_parameter`, `connect` and `disconnect`;
  - `rename_node`, which also rewrites `$node["X"]` and `$node.X` refs, the connections, `tests`, `test_defaults.mocks` and `pinned` names;
  - `add_test`, `update_test` (which refuses locked tests), `check`, `run_tests` and `score`.

  `POST /api/workflows/{id}/apply-ops {"ops": [{"op", "args"}], "dry_run"?: true}` returns `{"definition", "check": CheckResult}`, and saves only when `dry_run` is false.
- **`services/node_catalog.py`.** `build_node_catalog() -> [{type, display_name, effect, parameters, contract_kind: "derived"|"declared"|"observed", recipe: {checks, categories}, example}]`, served at `GET /api/nodes/catalog`. A test asserts it covers every registered type.

## 8. Studio

- **Data and API client.**
  - `tests` and `test_defaults` round-trip through `lib/workflowTransform.ts` and live in `workflowStore`.
  - Shared TS types and pure helpers go in `lib/tests.ts`: the summary and bar text, the grid from a report, and `newTestCase(node, category)`.
  - Runs and the scorecard go in `stores/testStore.ts`.
  - API calls go in the studio's existing API client module, following its pattern.
- **Live tests.** When the draft changes, debounced to about 1500 ms, and the workflow has tests, the editor calls `tests/run` with `definition`. Scoring with planted bugs runs only when the user clicks it.
- **Tests tab.**
  - `bottom-tab-tests` with a count badge `tests-count`.
  - Rows are `test-row` with `data-test-id` and `data-status` (passed|failed|error|draft-unrun).
  - Toolbar: `tests-run-all`, `tests-new`, `tests-suggest` and `tests-score`.
  - A failed test lists `test-failure` rows with `data-node` and `data-kind`. Clicking one selects that node.
  - An `unmocked` failure shows the fixes `fix-generate-mock` and `fix-last-real-reply`. Each calls `mock/generate`, writes the result into `test_defaults.mocks[node]`, and re-runs.
- **Test editor** (`test-editor`, a side drawer):
  - fields `test-name`, `test-category`, `test-target`, `test-input` (JSON);
  - per mock, `test-mock-<node>` with source choices (default / items / http / error / timeout);
  - `test-expect-row` (node, port, path, op, value) and `test-expect-add`;
  - `test-save`, plus `test-approved-by` and `test-approve`.
  - A locked test shows `test-locked`, has every input disabled, and offers `test-unlock`.
- **Coverage grid.** `coverage-grid` is a sub-view in the Tests tab, toggled by `tests-view-grid`. Its cells are `coverage-cell` with `data-node`, `data-category` and `data-status`. Clicking a `none` cell opens the editor with a new test prefilled for that node and category.
- **Test bar.** `test-bar` sits in the editor navbar and shows the `Scorecard.bar` text, or the summary when it hasn't been scored yet. It has `data-state` pass|fail|none.
- **Node badges.** `node-test-badge` has `data-status` pass|fail, from `badges`.
- **API catalog.**
  - The screen is reached from the project area, next to where the existing project settings and vars live. It's `api-catalog`, with `api-add`, `api-spec-input` (paste), `api-spec-url`, `api-spec-file`, `api-save`, `api-row` and `api-operation-row`.
  - The HttpRequest node panel gets a mode toggle `http-mode-api` and an endpoint select `http-api-endpoint`.
  - Picking an endpoint writes `api_ref`, shows `path_params` fields, and writes the operation's 2xx MockOption into `test_defaults.mocks[node]` unless one is already there.
- **Publish blocked.** On a 422 with `failed_tests`, a toast lists them and the Tests tab opens.
- **Look.** Use `src/shared/components/ui/*` and the tokens only. `npm run design:check` must stay clean.
