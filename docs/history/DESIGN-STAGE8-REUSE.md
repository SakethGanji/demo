# Stage 8 design: reuse through sub-workflows and templates

Status: design, ready for builders. Date: 2026-10-18. Author: architect pass, from a code study of the current branch.

This design builds on Stage 2 (contracts and the checker), Stage 3A/3B (tests, mocks, smoke), releases and environments, Stage 5 (`DESIGN-STAGE5-UIS.md`) and Stage 6 (`DESIGN-STAGE6-AI-BUILDER.md`). Stages 5 and 6 are still being built, so this doc assumes only what their design docs say. Section 15 lists every such assumption.

Scope: features only. There is no login, no roles and no audit log in this stage. Section 13 lists the seams for them so they can be added later without reshaping anything.

---

## 0. Summary

1. **Sub-workflow block.** `ExecuteWorkflow` becomes a typed block.
   - Its input comes from the child's `ExecuteWorkflowTrigger` input contract.
   - Its output comes from the child's return node: the first `Respond` reached, or, failing that, the last node run.
   - It pins the child version with `version: "draft" | "latest" | <version_number>`.
   - It runs the child in-process. The child gets its own `executions` row, linked to the parent (the columns already exist and are never written today).
   - It has a depth limit, a timeout and a call-stack recursion guard.
   - A child failure goes through the normal retry and `on_error` path.
2. **Releases pin the closure.** `build_snapshot` follows sub-workflow references transitively and pins every child version. In a release environment, a run resolves each sub-workflow call through the snapshot, never through the live publish pointer.
3. **Cycle detection, in two places.**
   - Static: a new reference index, `workflow_refs`, plus a Tarjan pass in the checker (WF020).
   - Runtime: a call-stack guard (`SUBWORKFLOW_CYCLE`).
4. **Convert selection to sub-workflow.** This is a pure function that returns either a plan or a refusal with a reason.
   - It follows n8n: exactly one entry node and exactly one exit node/port.
   - The selection must be convex.
   - References from inside the selection to nodes outside it become typed child inputs.
5. **Approval inside a sub-workflow stays forbidden in v1.**
   - The runtime already enforces this with `APPROVAL_IN_SUBWORKFLOW`.
   - Stage 8 adds a static error (WF025) and a pattern in the templates: approval in the parent, work in the child.
6. **Templates.**
   - A template is a versioned JSON bundle (`wf-template/1`) holding workflows (with their tests and interface), table schemas with seed rows, API specs, and required secrets and variables as names only.
   - Install is atomic, idempotent, clash-safe and remaps every id.
   - Each bundled template must pass its own tests in CI, in a fresh project on real Postgres.
7. **Six starter bank templates**, aligned with the Stage 6 eval cases and fake bank APIs: refund triage, KYC review, card dispute intake, AML alert triage, loan pre-check, and a shared customer-notify sub-workflow.
8. **AI builder.**
   - It sees callable sub-workflows with their typed I/O.
   - It can propose "start from template".
   - Templates feed the few-shot pool, excluding eval overlap.

---

## 1. What exists today (findings that shape this design)

### 1.1 ExecuteWorkflow node (`src/nodes/flow/execute_workflow.py`)

| # | Gap | Effect | Fixed in |
|---|---|---|---|
| G1 | Always loads the **draft** (`workflow_repository.get(id)`). | A production parent runs whatever is being edited in the child. | §4 |
| G2 | No project check. | A workflow can call any team workflow. | §3.4 (WF027) |
| G3 | `WorkflowRunner()` is created with no db session. | Child node outputs are not persisted and no child execution row exists. The child can't be debugged. | §3.3 |
| G4 | Output collapses into **one** item: `{_subworkflow, ...last json of each terminal}`. | Item multiplicity is lost and terminal outputs are merged together. | §2.3 |
| G5 | No timeout. | A stuck child blocks the parent until the global stale reaper runs. | §3.5 |
| G6 | The child shares the parent's `extras` dict. | `api_specs` was loaded only for the parent's nodes, so a child `api_ref` HTTP call can't resolve its spec. | §3.3 |
| G7 | No static cycle check. WF006 only checks that the target exists. | A → B → A is caught only at the depth limit (10 levels deep). | §5 |
| G8 | Contract is `observed`/UNKNOWN. | The checker can't type anything downstream of a sub-workflow call. | §2.4 |
| G9 | A second call site in `nodes/ai/tools/workflow_tool.py` (agent tool). | It must use the same invoker. | §3.2 |
| G10 | The credential-site `NodeDefinition` rebuild in `workflow_runner._process_job` drops `label`. | Unrelated bug found during the study. B1 fixes it because B1 owns the file. | §11 |

### 1.2 Other findings

- `ExecutionModel` already has `parent_execution_id`, `parent_node_name`, `depth` and the index `ix_executions_parent`.
- The `executions_mode_check` constraint already allows `'sub_workflow'`.
- `workflow_runner._suspend` already fails with `APPROVAL_IN_SUBWORKFLOW` when `context.parent_execution_id is not None`. Stage 5 made this call; Stage 8 keeps it (§3.7).
- `release_service.build_snapshot` pins only workflows that routes and apps reference (`_pin_workflow`). Sub-workflow children are not pinned, so a release is not reproducible today.
- `workflow_to_definition` / `definition_to_workflow` drop unknown definition keys. **Stage 8 adds no new definition keys.** Everything new lives in node `parameters`, which round-trip untouched.

---

## 2. Node types, parameters and contract derivation

### 2.1 `ExecuteWorkflowTrigger` (child side) — new parameters

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `inputSource` | options `fields \| jsonSchema \| passthrough` | `fields` for new nodes, `jsonSchema` when `inputSchema` is already set (legacy) | How the input is declared. n8n names: "Define using fields below", "Define using JSON example/schema", "Accept all data". |
| `fields` | collection of `{name, type: string\|number\|boolean\|object\|array, required: bool, description}` | `[]` | The declared inputs. |
| `inputSchema` | json (existing) | — | Used when `inputSource = jsonSchema`. |
| `defaultInput` | json (existing) | — | Used by manual runs of the child on its own. |

**`contract()`** — the existing `sh.param_shape` path is extended:
- `fields` is turned into a Shape: an object with each field's type, a `required` list, and `x-source: declared`.
- `passthrough` gives `UNKNOWN`.
- The output still adds `_triggeredAt`, `_triggerType` and `_executionDepth`.

The **input contract of the workflow** is this Shape without the three `_` fields.

`execute()` validates incoming items against the declared Shape. It uses the Stage 2 shape validator that already backs test contracts.
- A missing required field fails with `SUBWORKFLOW_INPUT_INVALID`, which names the path.
- An extra field is kept, as in n8n.
- A type mismatch on a declared field fails.

This is the one place where the bank gets strictness at runtime. The checker stays lenient (§5).

### 2.2 `ExecuteWorkflow` (parent side) — v2 parameters

| Parameter | Type | Default (new node) | Notes |
|---|---|---|---|
| `workflowId` | workflowSelector | — | Existing. Must be a literal id; expressions are refused by WF006 in v1 so pinning and cycles stay static. |
| `version` | options + number: `"draft" \| "latest" \| <int>` | `"latest"` | `<int>` is the child's `version_number`, **not** the version row id. See §4. |
| `inputMode` | `map \| passThrough \| custom` | `map` | `map` is new: one row per child input field (n8n "Workflow Inputs" resource mapper). |
| `inputs` | object `{field: expression}` | `{}` | Used when `inputMode = map`. The studio builds the rows from the child input contract. |
| `includeItem` | boolean | `false` | With `map`, start from the incoming item's json and overlay the mapped fields. Convert-selection uses this. |
| `customInput` | json | — | Existing, used when `inputMode = custom`. |
| `mode` | `once \| each` | `once` | n8n "Run once with all items" / "Run once for each item". `each` starts one child execution per item, in sequence, in item order. |
| `timeoutSeconds` | number | `300` | Capped at 3600, and also capped by the parent's remaining `settings.max_execution_time`. |
| `outputMode` | `items \| merged` | `items` | `merged` is the legacy single-item output (G4). Kept only for compatibility. |

**Legacy compatibility.** A node with no `version` parameter is a legacy node. It is read as `version: "draft"`, `inputMode` as stored, and `outputMode: "merged"`. This is decided in one helper, `execute_workflow.normalize_params(params)`. Old published versions keep their exact behaviour, which matters because versions are immutable and can't be migrated. The checker reports WF023 (draft pin) on legacy nodes as an `info` in draft and as an error at publish (§4.3), so they get upgraded when the owner next publishes.

### 2.3 Output semantics

- **`items` mode** (new default): the node emits on `main` the items of the child's **return node**.
  - With `mode: each`, it emits the concatenation of each child run's return items, in input order.
  - Every emitted item gets `pairedItem` lineage back to the parent input item that produced it, so `$('X').item` keeps working downstream.
  - Each item's metadata (not its json) carries `{child_execution_id, child_workflow_id, child_version_number}`. The run view uses this to link out.
- **Return node:**
  1. If the child has any `Respond` nodes, the return node is the first one that executes. Its output is passthrough, which is its input items. Its `statusCode`/`headers`/`body` are ignored when the child is called as a sub-workflow, and the checker says so (WF026 info).
  2. Otherwise the return node is the last node that finished successfully (`context.last_completed_node`). This is the n8n rule.
  3. If the child produced no output at all (for example an If that sent nothing anywhere), the result is `[]`, and the parent continues with an empty branch (NO_OUTPUT_SIGNAL semantics).
- **`merged` mode** (legacy): today's single item, unchanged.

### 2.4 Contract derivation (`ExecuteWorkflow.contract(params, inputs, lookups)`)

```
child = lookups.workflow_io(workflowId, version)        # cached per (workflow_id, resolved version_number|"draft")
if child is None:            -> NodeContract(inputs={"main": UNKNOWN}, outputs={"main": UNKNOWN})
inputs  = {"main": child.input}                          # x-source: derived
outputs = {"main": child.output if outputMode == "items" else merged_shape(child)}   # x-source: derived
error   = sh.error_shape(extra={"child_execution_id": str, "child_node": str, "child_error_code": str})
```

`lookups.workflow_io` comes from the pure function `services/workflow_io.py:derive_io(definition, lookups) -> WorkflowIO`:
- **`input`** is the `ExecuteWorkflowTrigger` output contract minus the `_` fields, or `UNKNOWN` if the child has no such trigger. The child also gets WF021 in that case.
- **`output`** comes from running the checker's shape propagation on the child (`_Ctx.order` plus `_port_shape`, with no findings collected):
  - With `Respond` nodes: the `anyOf` union of the `main` input shapes of every `Respond`, or the single shape if they're all equal.
  - Without `Respond`: the union of the output shapes of every terminal node.
  - If there are more than 3 distinct shapes, or any one of them is UNKNOWN, the output is `UNKNOWN` plus WF026 (warning).
- An approved observed contract on the child's return node (`NodeDefinition.contract`) wins over the derived one. This is the same precedence Stage 2 already uses.
- **`has_human`** is true if the child, or any child reachable from it, contains an `Approval` node (for WF025).
- `callable`/`reasons` sum up the above for the picker.

The checker then works as it does for any other node:
- `$json.x` downstream of the call is typed.
- A `map` row whose expression type doesn't match the child's field type is WF024 (warning).
- A required child field with no mapping row and no `includeItem` is WF024 (error when `inputMode = map`; the runtime would fail on it for certain).

### 2.5 Effects, catalog, n8n import

- `NODE_EFFECTS["ExecuteWorkflow"]` stays `"subworkflow"`, so in test mode it is mocked by default (§6.3).
- `node_catalog` describes the new parameters. The AI builder's catalog subset shows `version`, `inputMode: map` and `inputs`.
- `n8n_import.h_exec` maps n8n parameters:
  - `workflowId` (both the `__rl` and plain forms) to `workflowId`.
  - `mode: "each"` to `mode: each`.
  - `workflowInputs.value` (the resource mapper) to `inputMode: map` plus `inputs`.
  - `options.waitForSubWorkflow: false` gives a warning: fire-and-forget is not supported in v1, and the import waits for the child.
  - The imported node gets `version: "latest"`.
- `h_exec_trigger` maps `workflowInputs.values` (`{name,type}`) to `fields`, and `inputSource: "passthrough"` to `passthrough`.

---

## 3. Runtime

### 3.1 Shape of the call

```
parent ExecuteWorkflow.execute()
  └─ context.subworkflow_invoker.invoke(SubworkflowCall)      # engine/subworkflow.py (protocol)
        ├─ resolve version          (services/subworkflow_service.py)
        ├─ guards: project, depth, call-stack cycle, has_human
        ├─ ExecutionRepository.start(child row, parent links)   # own session, committed
        ├─ WorkflowRunner.run_subworkflow(child ctx)            # in-process, same event loop
        │     timeout: asyncio.wait_for(timeoutSeconds)
        ├─ ExecutionRepository.complete(child ctx)              # node_outputs persisted
        └─ SubworkflowResult(items, child_execution_id, status, error)
```

### 3.2 `engine/subworkflow.py` (new, frozen interface between B1 and B2)

```python
@dataclass(frozen=True)
class SubworkflowCall:
    parent_context: ExecutionContext
    parent_node: str
    workflow_id: str
    version: str | int                 # "draft" | "latest" | version_number
    items: list[NodeData]              # already mapped by the node
    timeout_seconds: float
    test_inline: bool = False          # §6.3

@dataclass
class SubworkflowResult:
    items: list[NodeData]
    child_execution_id: str | None
    child_workflow_id: str
    child_version_number: int | None   # None = draft
    status: Literal["success", "failed", "timeout"]
    error: ExecutionError | None = None

class SubworkflowInvoker(Protocol):
    async def invoke(self, call: SubworkflowCall) -> SubworkflowResult: ...

class InMemoryInvoker:  # used when context has no invoker (pure engine unit tests, legacy callers)
    """Loads via context.workflow_repository (draft only), runs via WorkflowRunner.run_subworkflow, writes nothing."""
```

New `ExecutionContext` fields (types.py):
- `subworkflow_invoker: Any | None = None`.
- `call_stack: tuple[str, ...] = ()`, the workflow ids from the root to this run.
- `root_execution_id: str | None = None`.
- `deadline: float | None = None`, a monotonic deadline inherited from the parent.

`run_subworkflow` changes:
- It copies `call_stack + (child_id,)`, `root_execution_id`, the invoker and the deadline into the child context.
- It gives the child **its own `extras` dict**: a shallow copy of the parent's, with `api_specs` replaced by the child's (G6). `release_id`/`release_modules`/`release_pins` and the broker token are carried over.
- `test_requests` is still shared, so test assertions can see child requests when the child runs inline.

`workflow_tool.py` (AI agent tool) calls the invoker the same way (G9), with `version` taken from its own config (default `"latest"`).

### 3.3 Child execution row (`services/subworkflow_service.py:DbSubworkflowInvoker`)

- **Separate session.** The child runs in its own session from the session factory, so its row is committed even if the parent later rolls back or fails. That is what you need for debugging.
- **`ExecutionRepository.start(...)`** gains these kwargs:
  - `parent_execution_id`, `parent_node_name`, `depth`;
  - `root_execution_id` (new column);
  - `workflow_version_id` (the existing column, now written for children as well).
- **`mode`:**
  - `'sub_workflow'` for real runs;
  - `'test'` when the parent is a test run with `persist=True`, so the existing 7-day purge covers it;
  - nothing persisted when the parent run is unsaved (`_unsaved_runs` or `persist=False`). The child then uses `InMemoryInvoker` semantics, but with version resolution still applied.
- **`project_id`/`environment`** are the parent's. Same-project calls only, in v1.
- **`complete()`** persists the child's node_outputs, drift and hooks exactly like a top-level run. `_cleanup` (keep the newest 100) **counts only root executions** (`parent_execution_id IS NULL`). Children go when their root goes, via the FK `ON DELETE CASCADE` added by the migration, so a busy child can't prune its own parents' history.
- **Variables and secrets:** loaded once for the environment, then shared with the child. It's the same project and environment, so the values are identical, and loading them once avoids a second decrypt.
- **`api_specs`:** the invoker calls `load_api_specs(session, child.workflow.nodes)` and merges the result into the child's extras.
- **SSE events:** child node events are re-emitted on the parent stream with `subworkflow_parent_node` and `subworkflow_id` set. Those `ExecutionEvent` fields already exist.

### 3.4 Guards (checked in this order, before the child row is created)

| Guard | Error code | Message (user-facing) |
|---|---|---|
| Child not found, or in another project | `SUBWORKFLOW_NOT_FOUND` | "Sub-workflow '{id}' isn't in this project." |
| Version can't be resolved (§4.2) | `SUBWORKFLOW_VERSION_MISSING` | "Sub-workflow '{name}' has no version {v}." / "…has never been published." |
| Child has no ExecuteWorkflowTrigger | `SUBWORKFLOW_NOT_CALLABLE` | "'{name}' can't be called: it has no Execute Workflow Trigger." |
| `depth + 1 > max_execution_depth` (10, unchanged) | `RECURSION_LIMIT` (existing `RecursionLimitError`) | "Sub-workflows are nested more than 10 deep." |
| `workflow_id in call_stack` | `SUBWORKFLOW_CYCLE` | "'{a}' → '{b}' → '{a}': a workflow can't call itself, directly or indirectly." |
| Child version `has_human` | `APPROVAL_IN_SUBWORKFLOW` (the existing code, raised early) | "'{name}' has an Approval step; approvals must be in the top-level workflow." |

The last guard is raised at call time, not when the Approval is reached. That way the child never does half its work and then stops.

### 3.5 Timeout

- The timeout is `asyncio.wait_for(run, min(timeoutSeconds, deadline - now))`.
- On timeout, the child task is cancelled. Cancellation reaches node awaits such as HTTP, which the shared httpx client honours.
- The child row is completed with status `failed`, error code `SUBWORKFLOW_TIMEOUT`, and `cancelled_at` set.
- The parent sees a node error, `SUBWORKFLOW_TIMEOUT`.

### 3.6 Error propagation and `on_error`

- A child failure becomes a single node error on the parent's `ExecuteWorkflow` node:
  - `error_code`: `SUBWORKFLOW_FAILED`, or the guard code from §3.4.
  - `details`: `{child_execution_id, child_workflow_id, child_version_number, child_node, child_error_code, child_error}`.
  - The message reads "Sub-workflow '{name}' failed at '{child_label}': {child_error}".
- The node raises, so the existing retry loop and `on_error` handling apply unchanged:
  - `retry_on_fail: n` re-invokes the child. Each attempt is a new child execution; the attempt index goes in the child's metadata.
  - `on_error: stop`: the parent fails, and its error lists the child execution id.
  - `on_error: continue`: the node emits the input items, with `error` added the way `continue` already does it.
  - `on_error: error_output`: the node emits on the `error` port one item per failed child run, `{error, code, child_execution_id, failed_node}`. With `mode: each`, items that succeeded still go to `main` and failed ones go to `error`. This is per-item routing, the same as other nodes in `each`-style execution.
- A child's own `on_error` settings apply inside the child first. A child that "continues past" an error counts as a success for the parent.
- **The error workflow** (`settings.error_workflow_id`) fires for the **root** only, never for children. The root failure already carries the child id, and firing for both would double-alert.

### 3.7 Approval inside a sub-workflow — decision: forbidden in v1

Decision: an `Approval` (effect `human`) anywhere in a child's closure is a static error (WF025) and an early runtime error (§3.4). This keeps Stage 5's runtime choice as it is.

Justification:
1. **At-most-once resume is defined per execution.**
   - Stage 5's resume snapshot, `human_tasks.snapshot` and the at-most-once resumer restore **one** `ExecutionContext` and continue it.
   - A nested pause would need the parent to suspend while it holds a waiting child, and then rebuild a two-level stack on decision: child first, then the parent's `ExecuteWorkflow` node with the child's result, with item pairing intact across both.
   - That is a wait graph. It's the Stage 4 durable-waits work, and getting it subtly wrong could pay a refund twice.
2. **Pinning gets ambiguous across a pause.**
   - A pause can last days.
   - If the child is republished in between, it's unclear whether the resumed child keeps its version and the parent's next call gets the new one.
   - We would have to specify and test this before a bank relies on it.
3. **The workaround is clean and is the better design anyway.**
   - Put the decision in the top-level workflow and the work in the sub-workflows.
   - For example: `Eligibility (sub)` → `Approval` → `Issue refund (sub)`.
   - This keeps every human decision visible on the canvas that owns the case, which is what auditors read. The refund-triage and KYC templates show this pattern.
4. **The seam is designed now.**
   - `SubworkflowResult.status` is a Literal that can gain `"waiting"`.
   - `execution_waits` from Stage 5 can hold `{waiting_on_execution_id}`.
   - Lifting the ban later is a runtime change, not a model change.

n8n note: n8n lets a child Wait. Here we deliberately depart from n8n, for the reasons above.

### 3.8 Hooks for frozen time and RNG

The child shares `frozen_now` and `rng`. `ExecuteWorkflowTrigger.execute` must read `context.frozen_now or datetime.now()`; today it calls `datetime.now()` directly. With that fix, an inline test child is deterministic.

---

## 4. Version pinning, releases and environments

### 4.1 Pin values

| `version` | Draft environment (`env_kind == "draft"`), including manual and editor runs | Release environment (`env_kind == "release"`) |
|---|---|---|
| `"draft"` | The child's current draft | The version pinned in the release snapshot for this (parent version, node) |
| `"latest"` | The child's `published_version_id`, resolved at **call time** | The snapshot pin |
| `N` | `workflow_versions` where `version_number = N` | The snapshot pin (equal to N by construction) |

A release run never reads the live publish pointer of a child. That makes a release fully reproducible, which is the point of releases for a bank.

### 4.2 Resolution function (`services/subworkflow_service.py:resolve_child`)

```
resolve_child(session, parent_ctx, parent_node, workflow_id, version) -> (StoredWorkflow, version_row_id | None)
  pins = parent_ctx.extras.get("release_pins")             # set by route_dispatcher/public_apps/etc. for release envs
  if pins is not None:
      key = f"{parent_ctx.version_id}:{parent_node}"
      vid = pins.get(key) or raise SUBWORKFLOW_VERSION_MISSING("not in release snapshot")
      return get_version_workflow(workflow_id, vid), vid
  if version == "draft":  return repo.get(workflow_id), None
  if version == "latest": vid = child.published_version_id or raise SUBWORKFLOW_VERSION_MISSING("never published")
  else:                   vid = version_id_for(workflow_id, int(version)) or raise SUBWORKFLOW_VERSION_MISSING
  return get_version_workflow(workflow_id, vid), vid
```

`get_version_workflow` takes a **row id**. The pin stores a version_number, and `version_id_for` maps it to the row id. A child's own calls resolve with the child's `version_id` as the key.

### 4.3 Publish rules

- Publishing a parent whose definition has a `version: "draft"` reference is refused with 422 (WF023 promoted to error at publish).
  - The reason: a published production trigger must not run someone's unsaved edits.
  - The editor shows WF023 as `info` while drafting, in keeping with the lenient checker.
- Publishing a parent whose `latest`/`N` child can't be resolved is refused (WF022).
- **Publishing a child** never fails because of its parents. Instead, the publish response gains `impact: [{parent_workflow_id, parent_name, node, version_spec, in_release: bool, breaking: [WF024 findings]}]`. The impact is computed from `workflow_refs` (parents that use `latest`) by re-checking each parent's published definition against the new child I/O. The studio shows a confirm dialog when `breaking` is non-empty (owner decision O7: warn, don't block).

### 4.4 Release snapshot closure (`release_service.build_snapshot`)

- **Snapshot keys.**
  - `snapshot.workflows` keeps its shape `[{workflow_id, version_id}]`.
  - Each entry gains optional `via: "route" | "app" | "subworkflow"`.
  - A new key holds the edges: `snapshot.subworkflows = [{parent_workflow_id, parent_version_id, node, child_workflow_id, child_version_id, spec}]`.
- **Closure.** It's a BFS from every pinned root version.
  - For each ExecuteWorkflow node in that version's definition:
    - `N` gives that row.
    - `latest` gives `published_version_id` at snapshot time.
    - `draft` gives `_pin_workflow(child)`, which reuses or creates a version from the draft. This is the same rule roots already use.
  - Then it recurses into the child version.
  - It keeps a visited set keyed by version id.
  - If it finds a cycle in the closure it stops and records it for the check below. It does not loop.
- **Two parents, two pins.** Two parents may pin different versions of the same child. That's allowed: the edges are keyed per (parent version, node).
- **Release extras.** `release_extras(rel)` adds `release_pins: {f"{parent_version_id}:{node}": child_version_id}`. All five release run sites already pass `release_extras` (`route_dispatcher`, `public_apps`, `interface_service`, `chat_service`, `app_service`), so no change is needed at those sites.
- **New `run_checks` entries:**
  - `subworkflows_resolved`: every edge resolved, no cycle, no `has_human` child.
  - `subworkflow_contracts_compatible`: no WF024 errors on any edge, using pinned versions on both sides.
  - The existing `workflow_tests_pass` runs for child versions too, because children are now in `snapshot.workflows`.
- **`_diff`** between releases lists child version changes as `{workflow, from_version, to_version, via: "subworkflow", parents:[...]}`.

### 4.5 Environments

Draft environments resolve live, using §4.1. A release environment run with no `release_pins` would be a legacy release from before Stage 8. Such a run falls back to draft-environment resolution and adds a run warning, "release predates sub-workflow pinning; re-cut the release". This keeps old releases working, and the release page flags them.

---

## 5. Cycle detection

### 5.1 Reference index: `workflow_refs`

- **Rows.** There is one row per ExecuteWorkflow node per (parent workflow, parent version or draft).
  - The draft rows (`parent_version_id IS NULL`) are rewritten on every draft save.
  - The version rows are written on publish and never change, because versions are immutable.
- **Maintenance.** `services/workflow_refs.py:sync_refs(session, workflow_id, version_id|None, definition)` is called from:
  - `WorkflowService.create`/`update`;
  - `publish` (inside `_create_version`);
  - `restore_version`;
  - the template installer.
- **Backfill.** The migration doesn't backfill. That would need Python to parse definitions. Instead, a one-shot idempotent startup task, `workflow_refs.backfill_if_empty`, runs from `main.py` lifespan when the table is empty. It's cheap: one pass over drafts plus versions.

### 5.2 Static check (checker, WF020)

- **Lookup.** `CheckLookups` gains `call_graph() -> dict[str, list[tuple[str, str]]]`, which maps a workflow id to its `(child_id, spec)` edges. It's built from `workflow_refs` draft rows plus the live `published_version_id` rows (one query in `build_lookups`). `StaticLookups` gets a default of `{}`, so existing unit tests still construct.
- **Graph.** The checker overlays the **current, unsaved** definition's edges for its own id, then runs the existing `_sccs` (Tarjan) over the graph.
- **Result.** Any SCC that contains the current workflow, or a self-edge, gives WF020 (error) on each ExecuteWorkflow node that's on the cycle. The message names the full cycle path.
- **Cost.** The graph is per project (WF027 forbids cross-project calls), so it's small. Draft edges and published edges both count. That's conservative: a cycle that only appears through a draft pin still gets flagged, because a manual run would hit it.

### 5.3 Runtime guard

The `call_stack` check in §3.4 catches anything static analysis missed, such as an expression `workflowId` from a legacy node, or a race between two saves.

### 5.4 Checker codes added (WF020–WF029)

| Code | Severity | Condition |
|---|---|---|
| WF020 | error | Sub-workflow cycle (including a workflow that calls itself). |
| WF021 | error | Target workflow has no `ExecuteWorkflowTrigger`. |
| WF022 | error | Pinned `N` doesn't exist, or `latest` but the child has never been published. In a draft check this is a warning when `version = latest`, so you can still build a parent and child together; at publish it's an error. |
| WF023 | info / error at publish | `version: "draft"`, including legacy nodes. |
| WF024 | warning; error for a missing required field in `map` mode | Input mapping doesn't fit the child input contract. |
| WF025 | error | Child closure contains `Approval`. |
| WF026 | warning / info | Child output can't be typed (several shapes, or UNKNOWN). Respond body ignored when called as a sub-workflow (info). |
| WF027 | error | Child is in another project. |
| WF028 | warning | Static call chain is deeper than `max_execution_depth`. |
| WF029 | info | Child trigger declares no inputs (`passthrough`), so the parent can't be type-checked. |

The existing WF006 ("sub-workflow doesn't exist") stays as it is. It also gains: "`workflowId` is an expression; sub-workflows must be chosen, not computed" (error, new nodes only; legacy nodes get a warning).

---

## 6. Test mode

### 6.1 Default: the call is an outside step

`ExecuteWorkflow` keeps effect `subworkflow`. In a test it must be mocked (test mocks > `test_defaults.mocks` > pinned data), or the run fails with `TEST_UNMOCKED_EFFECT`. This stays as Stage 3A shipped it, and it keeps parent tests fast and focused.

### 6.2 Better generated mocks

- `mock_gen` and `ai_default_mocks` build the default mock from the child output contract (`workflow_io.output`) through the existing shape-to-example generator, instead of `{items:[{}]}`.
- If the output is UNKNOWN, they keep `{items:[{}]}` plus the Stage 6 warning.

### 6.3 Opt-in inline child: `"subworkflows": {"<node>": "inline"}` in a test

- **What it is.** A new optional test field, schema-validated in `schemas/workflow.py`. It's a test-case field, not a definition key, so it round-trips inside `tests`.
- **How it runs.** The marked node is not mocked. Instead, the invoker runs the child with `test_inline=True`:
  - It uses the same `TestRunOptions` (`now`, `seed`).
  - Mocks for the child come from the child version's own `test_defaults.mocks`, plus any parent-test mock keyed `"<node>/<child node>"` (the most specific wins).
  - Every child effect must be covered by those mocks, otherwise the run fails `TEST_UNMOCKED_EFFECT` with the path `"<node>/<child node>"`.
- **Assertions.** Assertions may reference `"<node>/<child node>"` outputs.
- **Persistence.** With `persist=True` the child is saved as a `mode='test'` execution linked to the parent's test execution.
- **Why it matters.** This lets templates test the real composition (§8.5).

---

## 7. Convert selection to sub-workflow

### 7.1 API

Request:

```
POST /workflows/{id}/extract-subworkflow
{ "nodes": ["Classify", "Over limit?"], "name": "Refund eligibility", "dry_run": true }
```

| Status | Body |
|---|---|
| 200 | `{plan: ExtractPlan, child_definition, parent_definition, warnings: [...], affected_tests: [...]}` |
| 200 (`dry_run=false`) | The above plus `{child_workflow_id, child_version_number \| null}`. The parent draft is saved. |
| 422 | `{code: "CS0x", message, nodes: [...]}` |

The work is a pure function, `services/subworkflow_extract.py:extract(definition, selection, child_name, lookups) -> ExtractPlan | Refusal`. The route only persists the result.

### 7.2 Algorithm

Let `S` be the selected node names. Edges are `(src, src_port, dst, dst_port)`; only `main`-type connections are data edges. Boundary sets:
- `IN = {(x,p,y,q) : x∉S, y∈S}`;
- `OUT = {(y,p,z,q) : y∈S, z∉S}`.

1. **Validate the selection** (refusals in §7.3).
2. **Entry.** `entry` is the unique `y` over `IN`.
   - If `IN` is empty, the selection starts at a node with no inputs. That's only possible if it holds a trigger, which is CS02.
   - All `x` in `IN` keep their edges, re-targeted to the new ExecuteWorkflow node `E`. Several sources feeding one entry node is fine, since they feed one input in the parent too.
3. **Exit.** `exit` is the unique `(y,p)` over `OUT`.
   - If `OUT` is empty, the selection is at the end of the graph.
     - The exit is the unique sink of `S`.
     - If there are several sinks, the child output follows the "last node run" rule, and the plan gets warning CSW1, "several end nodes; the sub-workflow returns whichever runs last".
   - The child's return is `exit`'s output on port `p`.
     - If `p != main` (for example, an If's `true` port leaving the selection), the plan adds a child-side `NoOp` named "Return" wired from `(exit, p)`. That way the child's return is the items that went out that port.
     - Any other port of `exit` that stays inside `S` is fine.
4. **Outside references → inputs.** Scan every expression of every node in `S`, using the checker's `_RefVisitor`/`_walk_strings`. For each reference to a node `X ∉ S`:
   - **Case 1: `X` is the entry source and the reference is `$json.*`.** No change is needed, because the item passes through (`includeItem: true`).
   - **Case 2: `$node["X"].json.<path>` / `$('X').item.json.<path>` with a static path.**
     - Add the child input field `f = sanitize(f"{X}_{path}")` (deduplicated), with its type taken from X's shape at `path` (UNKNOWN gives `object`), `required: true`.
     - Parent mapping: `inputs[f] = "{{ $node[\"X\"].json.<path> }}"`.
     - Child rewrite: replace the reference with `$node["Inputs"].json.f` (the child trigger is named `Inputs`).
   - **Case 3: anything else** (the whole `.json`, computed keys, `$items("X")`, `.all()`): refuse with CS07.
5. **Inside references from outside → output.** Scan every node `∉ S` for references to nodes in `S`:
   - **A reference to `exit`** is rewritten to the name of `E`. Its shape is unchanged, since `E` outputs the exit items.
   - **A reference to any other inner node** is refused with CS08. It would need a second output, which a sub-workflow doesn't have.
6. **Build the child.**
   - **Trigger.** It holds an `ExecuteWorkflowTrigger` named `Inputs`, with `inputSource: fields`.
     - The fields are the entry source's output fields (from the checker's shape, when it's known) plus the fields added in step 4.
     - If the entry shape is UNKNOWN, `inputSource` is `passthrough` and the step-4 fields are added on top. In that case the plan gets WF029.
   - **Nodes.** It copies the nodes of `S` with their names, parameters, pinned data, labels, `on_error` and retry settings unchanged. Each one is moved by `-(min x, min y)`, plus an offset for the trigger.
   - **Edges.** `Inputs → entry` and every edge inside `S`, plus the step-3 "Return" NoOp if one was added.
   - **Tests.** The child gets one generated **draft** (unapproved) test. Its input is the parent's latest successful run item at `entry`, if one exists, and it asserts on `exit`'s shape (Stage 3A suggest machinery, `test_suggest`).
   - **Project.** Same project, same folder.
7. **Build the parent.**
   - Remove `S` and add `E`: `type: ExecuteWorkflow`, named after the child (deduplicated), at the centroid of `S`, with:
     - `workflowId` = the new child id;
     - `version` = `"latest"`;
     - `inputMode: "map"`, `includeItem: true`, `inputs` from step 4;
     - `mode: "once"`.
   - The `on_error` of `E` is `stop`.
     - If `exit` had `on_error: error_output` with its `error` port leaving `S`, the error edges move to `E`'s `error` port.
     - That's the one non-`main` port allowed to cross the boundary.
8. **Tests of the parent.**
   - Mocks, pins and assertions keyed on nodes in `S` are moved to `E`. A mock on `exit` becomes the mock of `E`.
   - Tests that assert on other inner nodes are listed in `affected_tests` and marked **stale** using the Stage 3A stale mechanism. They are not deleted.
   - Approved tests that turn stale block publish until they're reviewed. That's intended: the behaviour moved.
9. **Persist** (`dry_run=false`) in one transaction:
   - create the child (and `sync_refs`);
   - try to publish the child v1 through the normal `publish` (checks plus tests);
   - if that succeeds, `E.version = "latest"`, otherwise `E.version = "draft"`, and the response carries `child_publish_errors`;
   - save the parent draft (and `sync_refs`).

   Undo in the editor restores the parent draft. The child stays, and is listed in the toast with a "delete" action.

### 7.3 Refusal cases (`CS01`–`CS10`)

| Code | Refused when | Message hint |
|---|---|---|
| CS01 | `S` is empty or has 1 node that is `NoOp`/`StickyNote` | "Select the steps to move." |
| CS02 | `S` contains a trigger (`TRIGGER_NODE_TYPES`) | "Triggers stay in the parent." |
| CS03 | More than one entry node: `IN` targets two or more distinct `y` | "The selection has 2 starting points ({a}, {b}). Include the step where they meet, or select less." (n8n's rule) |
| CS04 | More than one exit: `OUT` leaves from two or more distinct `(y,port)`, apart from the `error` port case in step 7 | "The selection hands data out from 2 places; a sub-workflow returns one." |
| CS05 | Not convex: some path `u∈S → v∉S → … → w∈S` exists | "{v} sits between selected steps; include it or leave both out." |
| CS06 | `S` contains `Approval`, `Respond` in a webhook/route parent, `FormTrigger`-dependent nodes, or `ChatInput` | "Approvals stay in the top-level workflow" / "This Respond answers the caller; it can't move." |
| CS07 | An inner expression references an outside node non-statically (step 4) | Names the node, parameter and expression. |
| CS08 | An outside node references an inner node other than `exit` (step 5) | Names both nodes. |
| CS09 | A non-`main` connection crosses the boundary: AI-agent tool/model/memory sub-nodes, or Loop `loop`/`done` back-edges | "Move the agent together with its tools" / "Select the whole loop." |
| CS10 | The resulting child would fail WF020 (it already calls the parent, so the extraction would make a cycle) | "This would make '{parent}' and '{child}' call each other." |

All refusals are computed before anything is written. `dry_run` is what the studio calls on every selection change, debounced at 300 ms, so the dialog shows the reason before the user confirms.

---

## 8. Templates

### 8.1 Location and catalog

- **Location.** Templates ship in the repo under `apps/workflow-engine/templates/<slug>/`:
  - `template.json`, the bundle;
  - an optional `apis/*.yaml`, holding OpenAPI files referenced by the bundle;
  - `README.md`, a 10-line human description shown in the preview.
- **Catalog.** It's read-only in v1. It's loaded at startup by `services/template_bundle.py:load_catalog()`, validated and hashed. An invalid bundle fails startup in dev and test (and in CI through the bundle test); in production the bad bundle is skipped with an error log.
- **The seam for user-authored templates.** `GET /workflows/{id}/export-template` produces a valid bundle. A later `templates` DB table (with `team_id` and `created_by`) can hold bundles that use the same schema and installer.

### 8.2 Bundle format `wf-template/1`

```json
{
  "format": "wf-template/1",
  "slug": "refund-triage",
  "version": "1.0.0",
  "name": "Refund triage with manager approval",
  "summary": "Classify refund requests, auto-approve small ones, send large ones to a manager.",
  "category": "payments",
  "tags": ["refunds", "approval", "form"],
  "min_engine_stage": 8,
  "requires": {
    "templates": [{"slug": "customer-notify", "version": "^1"}],
    "secrets":   [{"name": "REFUNDS_API_KEY", "used_by": ["main/Issue refund"]}],
    "variables": [{"name": "REFUND_AUTO_LIMIT", "default": "500", "type": "string"}],
    "egress_hosts": ["refunds.bank.example", "notify.bank.example"]
  },
  "apis": [
    {"key": "refunds", "name": "Refunds API", "file": "apis/refunds.yaml"}
  ],
  "tables": [
    {"key": "refund_limits", "name": "refund_limits",
     "columns": [{"name": "segment", "type": "string", "required": true},
                 {"name": "auto_limit", "type": "number", "required": true}],
     "seed_rows": [{"segment": "retail", "auto_limit": 500}, {"segment": "premier", "auto_limit": 2000}]}
  ],
  "workflows": [
    {"key": "eligibility", "role": "subworkflow", "name": "Refund eligibility", "publish": true,
     "definition": { "...": "a normal workflow definition: nodes, connections, settings, tests, test_defaults" }},
    {"key": "main", "role": "entry", "name": "Refund triage", "publish": false,
     "definition": { "...": "includes interface (Stage 5) and tests (Stage 3A)" }}
  ],
  "few_shot": {"request": "When a customer asks for a refund ...", "spec": { "...": "Stage 6 spec" }, "eval_overlap": ["refund_over_500"]},
  "ci": {"min_tests_per_workflow": 3}
}
```

**Tokens.** Bundle-internal references use exact-string tokens. They are never inside `{{ }}`, so they can't collide with expressions:

| Token | Allowed in | Replaced by |
|---|---|---|
| `@tpl:workflow/<key>` | `ExecuteWorkflow.workflowId`, `workflow_tool` config, `settings.error_workflow_id`, interface `workflow` refs | The new workflow id. For a key from `requires.templates` the form is `@tpl:workflow/<slug>:<key>`. |
| `@tpl:table/<key>` | DataTable node `table` | The new table **id**. Names can be renamed on clash; ids are stable. |
| `@tpl:api/<key>` | `HttpRequest.api_ref[0]` | The registered `api_specs` id. |
| `@tpl:slug/<key>` | interface slug fields | The installed slug, possibly suffixed. |

The installer replaces tokens **only** at those parameter paths. Any token left in the definition after the replacement is an install error. The bundle schema test rejects bundles whose tokens don't resolve to a declared key.

**Rules checked by the bundle validator (`TemplateBundle` pydantic model plus lints):**
1. The `format` is known, `slug` matches the directory, and `version` is semver.
2. Every workflow definition passes `WorkflowDefinition` validation.
3. Exactly one workflow has `role: entry`.
4. `role: subworkflow` workflows have an `ExecuteWorkflowTrigger` with declared `fields`.
5. No secret values anywhere:
   - credential ids must be empty or a token;
   - strings that match the secret-pattern lint (keys, bearer tokens, PANs that pass a Luhn check, IBANs) fail;
   - seed rows must use the reserved fake ranges (cards `4000 00xx`, emails `@example.com`).
6. Every host used by HttpRequest nodes is in `requires.egress_hosts`, and is a `*.example` host.
7. Every workflow has at least `ci.min_tests_per_workflow` tests (3 by default), at least one of which exercises a failure or rejection path.
8. All tests are marked approved in the bundle (see §8.3, step 6).
9. `requires.templates` has no cycles.

### 8.3 Install (`services/template_installer.py`)

Request:

```
POST /projects/{p}/templates/{slug}/install
{ "version": "1.0.0" | null,          # null = catalog version
  "clash": "rename" | "fail",          # default rename
  "reuse_tables": ["refund_limits"],   # opt-in: reuse an existing table of the same name if columns are compatible
  "as_copy": false,                    # true = install another copy even if installed
  "run_tests": true,                   # run every installed workflow's tests and include the report
  "environment": null }                # for the needs report (secrets/variables present?)
```

Algorithm. It is pure planning followed by one transaction:

1. **Load** the bundle and compute `bundle_hash` (sha256 of the canonical JSON plus the API files).
2. **Idempotency.** Look up `template_installs` by `(project_id, slug, install_name)`, where `install_name` is the slug, or `slug-2`… when `as_copy`.
   - The same `bundle_hash` and status `installed` returns 200 `{status: "already_installed", ...mapping}`, and nothing is written. Repeating the POST is safe.
   - The same slug with a **different** hash or version returns 409 `{code: "TEMPLATE_VERSION_INSTALLED", installed_version}`. Upgrade is a later seam (§13). The user can pass `as_copy`.
3. **Dependencies.** Each `requires.templates` entry is installed first, recursively, with the same options. If it's already installed with a compatible version it's reused. Its mapping is merged into the token namespace.
4. **Plan names**, by the pure function `plan_install(bundle, project_snapshot, options) -> InstallPlan`:
   - **Workflows.** If the name is taken in the project, `clash=rename` gives `"<name> (2)"`, `(3)`… (the n8n convention), and `fail` gives 409 `TEMPLATE_NAME_CLASH` listing every clash.
   - **Tables** (unique per project and name):
     - `reuse_tables` with compatible columns (a superset, matching types) reuses the table and does **not** insert seed rows.
     - Otherwise `rename` gives `<name>_2`.
   - **API specs.** Reuse an existing spec with the same name and content hash; otherwise register a new one (suffixing the name on clash).
   - **Interface slugs** (Stage 5 `interface_slugs`): suffix `-2` on clash.
   - **Webhook paths and route paths:** suffix `-2` on clash. Triggers are never activated by install.
5. **Execute** in a single DB transaction. Any exception rolls back everything, so there's no partial install.
   1. Register the API specs and add `requires.egress_hosts` to the project egress allowlist, but only if the allowlist is non-empty. An empty allowlist means unrestricted, and we must not switch restriction on by accident. Hosts added this way are recorded in the install's `mapping.egress_added`.
   2. Create the tables and insert seed rows through `DataTableService.create_table`/`insert_rows`, so row validation applies.
   3. Create workflows in dependency order (sub-workflows before the workflows that reference them, using the bundle's own reference DAG). For each:
      - deep-copy the definition and replace tokens;
      - give it a fresh workflow id (repository-generated);
      - call `WorkflowService.create` (which does `sync_refs`);
      - set the interface if one is present.
   4. **Publish** every workflow with `publish: true`, in dependency order, through `WorkflowService.publish`. Publish runs the checker and the approved tests. If any check or test fails, the install fails and rolls back. That's a bundle bug, and CI catches it first.
   5. Write the `template_installs` row: `{mapping: {workflows: {key: id}, tables: {key: id}, apis: {key: spec_id}, slugs: {...}, egress_added: [...]}, needs, installed_by: actor}`.
6. **Test approval.** Bundle tests arrive approved, stamped `approved_by: "template:<slug>@<version>"` and `approved_at: install time`. They are the template author's reviewed expectations, so they gate the user's later edits exactly like their own approved tests.
7. **Needs report.**
   - For each required secret or variable, `needs` lists `{kind, name, environments_missing: [...]}`, computed per project environment from `VariableRepository`.
   - Required variables with a `default` are created in the project's draft environment only if missing.
   - Secrets are **never** created. They are reported, and the studio links to the secrets page.
8. **Tests.** After the commit, if `run_tests`, run each workflow's tests (`test_runner.run_tests`, `persist=True`) and include `{workflow_key: {passed, failed, report_id}}`.

Response (201 for a new install, 200 for `already_installed`):

```json
{"install_id": "tpi_…", "status": "installed", "template": {"slug": "refund-triage", "version": "1.0.0"},
 "workflows": [{"key": "main", "id": "wf_…", "name": "Refund triage", "published_version": null, "role": "entry"},
               {"key": "eligibility", "id": "wf_…", "name": "Refund eligibility", "published_version": 1, "role": "subworkflow"}],
 "tables": [{"key": "refund_limits", "id": "dt_…", "name": "refund_limits", "reused": false, "seeded_rows": 2}],
 "apis": [{"key": "refunds", "id": "spec_…", "reused": false}],
 "renamed": [{"kind": "workflow", "from": "Refund triage", "to": "Refund triage (2)"}],
 "needs": [{"kind": "secret", "name": "REFUNDS_API_KEY", "environments_missing": ["dev", "prod"]}],
 "dependencies": [{"slug": "customer-notify", "install_id": "tpi_…", "status": "already_installed"}],
 "tests": {"main": {"passed": 4, "failed": 0}, "eligibility": {"passed": 3, "failed": 0}}}
```

Errors are 404 `TEMPLATE_NOT_FOUND`, 409 `TEMPLATE_NAME_CLASH` / `TEMPLATE_VERSION_INSTALLED`, and 422 `TEMPLATE_INSTALL_FAILED` with `{stage, workflow_key, findings|test_failures}`.

### 8.4 Export (seam plus authoring tool)

```
GET /workflows/{id}/export-template?slug=&version=
```

This returns a bundle. It contains:
- the workflow and its transitive sub-workflows;
- the referenced tables, as schema only, with `seed_rows: []`;
- the referenced API specs;
- secrets and variables as names, found by scanning `$vars`/credential refs.

Ids are replaced by tokens. The response is validated against `TemplateBundle`, and lints run in `warn` mode. This is how the starter library gets authored: build in the studio, export, hand-edit the seeds, commit.

### 8.5 Template CI gate

`apps/workflow-engine/tests/integration/test_templates_ci.py` is parametrized over `templates/*/template.json`. It runs on real Postgres over HTTP (the existing integration client), and each case uses a fresh project.

1. Create the project `tplci_<slug>_<rand>` with egress allowlist = `requires.egress_hosts`.
2. `POST /projects/{p}/templates/{slug}/install` with `{run_tests: true}`. The response must be 201, and every workflow must have `failed == 0` and `passed >= ci.min_tests_per_workflow`.
3. `POST /workflows/{id}/check` for every workflow: no `error` findings.
4. Repeat the install: 200 `already_installed`, and the row counts of workflows, tables and data_table_rows in the project are unchanged.
5. `as_copy: true` install: 201, every name is suffixed, the tests still pass, and the two installs share no ids.
6. Every `role: entry` workflow publishes (`POST /workflows/{id}/publish` → 200).
7. Cleanup uses a prefix sweep on `tplci_`, the existing convention. There are no per-test deletes.

There is also a unit-level `tests/unit/test_template_bundles.py`. It validates the schema and lints every bundle with no database, so a bad bundle fails in under a second.

### 8.6 Starter library (6 templates)

All six use the Stage 6 fake bank APIs (`tests/fixtures/ai_builder/apis/*.yaml`, copied into each template's `apis/`) and `*.example` hosts. Their tests mock every outside step, so CI needs no network and no secrets.

| # | Slug | Workflows (sub = sub-workflow) | Tables | APIs | Interface | Shows | Stage 6 eval overlap |
|---|---|---|---|---|---|---|---|
| 1 | `customer-notify` | `Notify customer` (sub; inputs `customer_id, channel, template, vars`) | `notify_templates` (seeded with 4 message templates) | customers, notify | — | A reusable child that others depend on | — |
| 2 | `refund-triage` | `Refund triage` (entry, from `refund_triage_ui.json`); `Refund eligibility` (sub: AIAgent classify + `refund_limits` lookup → `{eligible, category, limit}`) | `refund_limits` | refunds | Stage 5 form | Approval in the parent, work in the child; `requires: customer-notify` | `refund_over_500` |
| 3 | `kyc-review` | `KYC review` (entry: form with document fields → sub → If risk → Approval → accounts update); `Screen customer` (sub: sanctions.screen + kyc.verify + risk score → `{risk, hits[]}`) | `kyc_risk_thresholds` | kyc, sanctions, risk, accounts | form | Two-API child with typed output | `kyc_document_review` |
| 4 | `card-dispute-intake` | `Card dispute intake` (entry: webhook → disputes.create → If fraud → cards.block → chargebacks.open → notify sub → Respond) | `dispute_reason_codes` | disputes, cards, chargebacks | — | Webhook + Respond; error_output path to a queue | `card_dispute_intake` |
| 5 | `aml-alert-triage` | `AML alert triage` (entry: Cron → aml.listAlerts → Loop → sub with `mode: each` → queues.assign); `Score alert` (sub: risk + AIAgent → `{score, reason}`) | `aml_rules` | aml, risk, queues | — | `each` mode, Cron, per-item errors | `aml_alert_triage` |
| 6 | `loan-precheck` | `Loan pre-check` (entry: form → customers.get → Code affordability → fees.quote → Respond) | `loan_policy` | customers, fees | form | Single workflow, Code node, data-table policy | `loan_precheck` |

Every template meets these test rules:
1. At least 3 tests per workflow, covering the happy path, a rejection or decline path, and an outside-step failure going through `on_error`.
2. The entry workflow of templates 2, 3 and 5 has one test with `"subworkflows": {"<node>": "inline"}`, so the composition itself is tested in CI.
3. Approval nodes are mocked through the Stage 5 approval mock format (`approval_items.json` conventions).

These mappings are defaults. PLAN open decision 3 (the pilot use case) may reorder them or replace one of them (owner decision O5).

---

## 9. AI builder integration (Stage 6)

1. **Grounding.** `ai_grounding` extends `existing_workflows: [{id, name, trigger}]` to `[{id, name, trigger, callable, inputs: [{name,type,required}], output_fields: [...], published_version}]`.
   - It keeps only callable workflows in the project, at most 20, ranked by name and description similarity to the request.
   - The catalog subset includes `ExecuteWorkflow` whenever any callable workflow exists.
2. **Ops.** These are unchanged. Using a sub-workflow is ordinary `add_node`/`set_parameters` with `type: ExecuteWorkflow`, `workflowId`, `version: "latest"`, `inputMode: map` and `inputs`.
   - `workflow_toolkit.apply_ops` validates that `workflowId` is in the grounding list. If it isn't, the op is rejected with the reason "unknown sub-workflow"; the model can't invent ids.
   - The Stage 6 checker gate (WF codes) covers WF020–WF029 automatically.
3. **Spec.** The `steps` of a Stage 6 spec may include `{"kind": "subworkflow", "workflow": "<id>", "inputs": {...}}`. The spec-to-ops compiler maps it to the ops above.
4. **Templates as a starting point.** Templates are not ops; they are a new proposal kind. `ai_builder` gains a step before spec generation: `match_template(request)` scores the catalog by `few_shot.request` and tags, using the cheap model tier. Above a threshold (0.8), the response includes a suggestion `{kind: "template", slug, reason}`.
   - The studio shows a "Start from template" card (`ai-template-suggestion`). Accepting it calls the install endpoint, then opens the entry workflow. Further AI edits proceed as normal ops on that workflow.
   - The ops-only rule of Stage 6 is kept, because install is a separate, explicit user action.
5. **Few-shot.** `ai_builder_prompts` loads examples from `tests/fixtures/ai_builder/examples/*.json` **and** from every template's `few_shot` (`{request, spec}`; the ops are derived by compiling the spec, so they never drift from the template).
   - **Eval hygiene.** When the eval harness runs case `c`, it excludes every template whose `few_shot.eval_overlap` contains `c`. A unit test asserts that the exclusion works for all 15 cases. Without it, the Stage 6 holdout would be contaminated.
6. **Default mocks.** `ai_default_mocks` uses §6.2, so an AI-built parent's generated tests get a typed child mock.

---

## 10. Data model and migration

### 10.1 Migration `src/db/migrations/20261021120000_reuse.sql`

This runs after Stage 6's `20261020120000`. If Stage 6 lands later, the timestamp still orders correctly.

```sql
-- 20261021120000_reuse
-- Stage 8: sub-workflow lineage, reference index, template installs.

-- 1. Executions: lineage for sub-workflow runs (parent_execution_id / parent_node_name / depth already exist).
ALTER TABLE executions ADD COLUMN IF NOT EXISTS root_execution_id text NULL;
CREATE INDEX IF NOT EXISTS ix_executions_root ON executions (root_execution_id);
-- orphan cleanup before adding the FK (no writer exists today, so this should delete nothing)
UPDATE executions SET parent_execution_id = NULL
  WHERE parent_execution_id IS NOT NULL AND parent_execution_id NOT IN (SELECT id FROM executions);
ALTER TABLE executions DROP CONSTRAINT IF EXISTS fk_executions_parent;
ALTER TABLE executions ADD CONSTRAINT fk_executions_parent
  FOREIGN KEY (parent_execution_id) REFERENCES executions (id) ON DELETE CASCADE;
-- (node_outputs already cascade from executions via the baseline FK; verify in B2, add if missing)

-- 2. Reference index for cycle detection, "used by", impact and delete protection.
CREATE TABLE IF NOT EXISTS workflow_refs (
  id                 bigserial PRIMARY KEY,
  project_id         text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  parent_workflow_id text NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
  parent_version_id  integer NULL REFERENCES workflow_versions (id) ON DELETE CASCADE,  -- NULL = draft
  node_name          text NOT NULL,
  child_workflow_id  text NOT NULL,          -- no FK: a dangling ref is a WF006 finding, not a DB error
  version_spec       text NOT NULL,          -- 'draft' | 'latest' | '<n>'
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_refs_node
  ON workflow_refs (parent_workflow_id, COALESCE(parent_version_id, 0), node_name);
CREATE INDEX IF NOT EXISTS ix_workflow_refs_child ON workflow_refs (child_workflow_id);
CREATE INDEX IF NOT EXISTS ix_workflow_refs_project ON workflow_refs (project_id);

-- 3. Template installs (idempotency + mapping).
CREATE TABLE IF NOT EXISTS template_installs (
  id                text PRIMARY KEY,                     -- 'tpi_' + uuid
  project_id        text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  template_slug     text NOT NULL,
  template_version  text NOT NULL,
  bundle_hash       text NOT NULL,
  install_name      text NOT NULL,                         -- slug, or slug-2.. for copies
  status            text NOT NULL CHECK (status IN ('installed')),   -- failed installs roll back; seam for 'upgrading'
  mapping           jsonb NOT NULL DEFAULT '{}'::jsonb,
  needs             jsonb NOT NULL DEFAULT '[]'::jsonb,
  depends_on        jsonb NOT NULL DEFAULT '[]'::jsonb,    -- [install_id]
  installed_by      text NULL,                             -- seam: user id once login exists
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, template_slug, install_name)
);
```

### 10.2 Models (`src/db/models.py`)

- `ExecutionModel.root_execution_id: str | None`.
- `WorkflowRefModel` and `TemplateInstallModel`, mirroring the SQL above.
- No change to `WorkflowModel`/`WorkflowVersionModel`. No new definition keys.

### 10.3 Delete protection

`DELETE /workflows/{id}` returns 409 `WORKFLOW_IN_USE` with `{used_by: [...]}` when:
- any **published** parent version (that is, the current `published_version_id` of a parent), or
- any release snapshot of a non-archived release

references the workflow. Draft-only references don't block. The parent then gets WF006 on its next check. `?force=true` is a seam for an admin role and is refused in v1.

---

## 11. Endpoints (request and response shapes)

All new routes follow existing conventions: `/workflows/...` is team-scoped with a project check, and `/projects/{project}/...` is project-scoped. Errors use the existing `{detail: {code, message, ...}}` envelope.

| Method and path | Request | Response |
|---|---|---|
| `GET /projects/{p}/subworkflows` | `?q=` | `[{id, name, description, published_version: int\|null, callable: bool, reasons: [str], inputs: [{name,type,required}], output_fields: [str], has_approval: bool, used_by_count: int}]` |
| `GET /workflows/{id}/io` | `?version=draft\|latest\|<n>` | `{workflow_id, version_number: int\|null, input: Shape, output: Shape, input_fields: [...], callable, reasons, findings: [WF021/WF026/WF029...]}` |
| `GET /workflows/{id}/used-by` | — | `[{parent_workflow_id, parent_name, node, version_spec, where: "draft"\|"published"\|"release", release_ids: [...]}]` |
| `POST /workflows/{id}/extract-subworkflow` | `{nodes: [str], name: str, dry_run: bool}` | §7.1 |
| `GET /workflows/{id}/export-template` | `?slug&version` | a `wf-template/1` bundle plus `{warnings}` |
| `POST /workflows/{id}/publish` (changed) | unchanged | gains `impact: [...]` (§4.3) |
| `GET /executions/{id}` (changed) | unchanged | gains `parent: {execution_id, workflow_id, workflow_name, node} \| null`, `root_execution_id`, `children: [{execution_id, node, workflow_id, workflow_name, version_number, status, duration_ms}]` |
| `GET /executions` (changed) | gains `?include_children=false` (default false: list roots only) | unchanged shape |
| `GET /templates` | `?category&q` | `[{slug, version, name, summary, category, tags, workflows: [{key, name, role}], tables: [name], apis: [name], requires: {...}, installed_in: [project_id]?}]`. `installed_in` appears only when `?project=` is given. |
| `GET /templates/{slug}` | — | the full bundle plus `readme` and a `preview` (per workflow: nodes `[{name,type,label}]`, connections, interface kind) |
| `POST /projects/{p}/templates/{slug}/install` | §8.3 | §8.3 |
| `GET /projects/{p}/template-installs` | — | `[{install_id, slug, version, install_name, created_at, workflows: [{key,id,name}]}]` |
| `GET /projects/{p}/template-installs/{id}` | — | the install row with mapping and a recomputed `needs` |

`GET /releases/{id}` already returns the snapshot, which now includes `subworkflows` edges. The release page renders them (§12).

---

## 12. Studio screens and data-testids

These follow the studio conventions: Base UI primitives, TanStack Query hooks in the feature folder, and `data-testid` on everything a spec touches.

### 12.1 ExecuteWorkflow node details (NDV) — `components/ndv/subworkflow/`

| Element | testid |
|---|---|
| Workflow picker (searchable; shows callable state and reasons; non-callable entries disabled) | `subwf-picker`, options `subwf-picker-option-<id>` |
| Version select (Draft / Latest published / v1…vN) | `subwf-version` |
| Draft-pin warning banner | `subwf-draft-warning` |
| Run mode (Once with all items / Once per item) | `subwf-mode` |
| Input mapping table (one row per child field; type and required badge; expression editor) | `subwf-inputs`, rows `subwf-input-<field>`, missing-required marker `subwf-input-missing-<field>` |
| "Include incoming item" toggle | `subwf-include-item` |
| Output preview (child output fields) | `subwf-output-fields` |
| Timeout | `subwf-timeout` |
| Open child (new tab, n8n behaviour) | `subwf-open-child` |
| Contract status (typed / untyped) | `subwf-contract-status` |

### 12.2 ExecuteWorkflowTrigger node details — inputs editor

| Element | testid |
|---|---|
| Input source (fields / JSON schema / accept anything) | `subwf-trigger-source` |
| Field list | `subwf-trigger-fields`; row `subwf-trigger-field-<i>`; name, type and required inputs `subwf-trigger-field-name-<i>`, `-type-<i>`, `-required-<i>` |
| Add field | `subwf-trigger-add-field` |
| "Used by" (count and list of parents) | `subwf-trigger-used-by` |

### 12.3 Canvas

| Element | testid |
|---|---|
| Badge on ExecuteWorkflow nodes, showing "Refund eligibility · v3" or "· latest" or "· draft" | `node-subwf-badge` |
| Double-click opens the child | (reuses node testid) |
| Context menu "Convert to sub-workflow" (shown when one or more nodes are selected) | `ctx-convert-subworkflow` |
| Keyboard shortcut Alt+X, added to KeyboardShortcutsHelp | — |

### 12.4 Convert dialog — `components/canvas/ConvertToSubworkflowDialog.tsx`

| Element | testid |
|---|---|
| Dialog | `convert-subwf-dialog` |
| Name | `convert-subwf-name` |
| Live refusal (code + message + highlighted nodes on canvas) | `convert-subwf-refusal` |
| Preview: inputs that will be created | `convert-subwf-inputs`, rows `convert-subwf-input-<field>` |
| Preview: returned from | `convert-subwf-exit` |
| Affected tests | `convert-subwf-affected-tests` |
| Confirm / cancel | `convert-subwf-confirm` / `convert-subwf-cancel` |
| Result toast with "Open sub-workflow" and "Delete it" | `convert-subwf-toast`, `convert-subwf-open`, `convert-subwf-delete` |

### 12.5 Runs

| Element | testid |
|---|---|
| ExecuteWorkflow output panel link "View sub-execution" (one per child run in `each` mode) | `run-open-child-execution` (list `run-child-executions`) |
| Child execution page breadcrumb "Called by <parent> · <node>" | `exec-parent-link` |
| Execution logs tree: child runs nested under the node | `exec-log-child-<execution_id>` |
| Error panel child link on `SUBWORKFLOW_FAILED` | `run-error-child-link` |

### 12.6 Workflow "Used by" and publish impact

| Element | testid |
|---|---|
| Used-by panel (workflow navbar menu) | `used-by-panel`, rows `used-by-row-<parent_id>` |
| Publish impact confirm when `breaking` is non-empty | `publish-impact-dialog`, `publish-impact-row`, `publish-impact-confirm` |

### 12.7 Templates — new feature `src/features/templates/`, route `/projects/$project/templates`

| Element | testid |
|---|---|
| Nav rail entry "Templates" | `nav-templates` |
| Grid, card per template | `templates-grid`, `template-card-<slug>` |
| Category filter and search | `templates-category`, `templates-search` |
| Preview drawer (README, workflows mini-canvas `WorkflowSVG`, tables, APIs, required secrets) | `template-preview`, `template-preview-workflow-<key>`, `template-preview-secret-<name>` |
| Clash mode, reuse-tables checkboxes | `template-clash-mode`, `template-reuse-table-<key>` |
| Install | `template-install` |
| Install result (renamed list, needs list linking to secrets, tests passed, open entry workflow) | `template-install-result`, `template-renamed-row`, `template-need-<kind>-<name>`, `template-tests-summary`, `template-open-entry` |
| Already-installed state on a card | `template-installed-badge` |
| Workflows list "New → From template" | `new-from-template` |
| AI chat suggestion card | `ai-template-suggestion`, `ai-template-suggestion-install` |

### 12.8 Release page

- The snapshot lists sub-workflow edges: `release-subwf-edges`, rows `release-subwf-edge-<i>`.
- The checks list shows `subworkflows_resolved` and `subworkflow_contracts_compatible`.
- These come from the existing checks component; there are no new testids beyond the edge list.

---

## 13. Seams for login, roles and audit (designed, not built)

1. **Actor threading.** Every new mutating service function takes `actor: str | None` (the existing pattern in `DataTableService.create_table`). It's stored in:
   - `template_installs.installed_by`;
   - `workflow_versions.created_by` for auto-publishes;
   - test `approved_by` stays `template:<slug>@<v>`.
2. **Authorization hook.** Each new route calls `authorize(request, action, resource)` from a new `services/authz.py`, which is a no-op returning True in v1. Actions:
   - `subworkflow.call`, checked when saving an ExecuteWorkflow reference: who may depend on a child;
   - `workflow.extract`;
   - `template.install`;
   - `template.export`;
   - `workflow.delete.force`.
3. **Cross-project calls.** WF027 and the runtime guard are the single choke point. A later "shared library project" feature relaxes them through `authorize("subworkflow.call", child)`, plus a cross-project pin in `release_pins`.
4. **Audit.** Each mutation returns a structured change record (`{action, actor, project_id, target, before_hash, after_hash}`), which the route currently drops. A later audit table subscribes in one place. The template install mapping and the `workflow_refs` history (version rows) are already an audit trail of composition.
5. **User-authored templates and upgrade.** The `template_installs.status` CHECK leaves room for `upgrading`. The installer's `plan_install` is pure, so a `plan_upgrade(old_mapping, new_bundle)` diff can reuse it.

---

## 14. Test plan

Baselines to keep:
- engine suite at or above 906 passed;
- e2e at or above 129;
- `tsc -b` clean;
- lint at or below 96;
- `design:check` clean.

### 14.1 Unit (no DB) — `tests/unit/`

| File | Covers |
|---|---|
| `test_subworkflow_extract.py` | At least 30 cases: linear, fan-in to one entry, If true-port exit adds Return NoOp, error_output port moves, outside-reference → input field (static path, `$('X').item`), rename of downstream references to the exit, the step-8 test remap, and each refusal CS01–CS10 with the exact code and node list. |
| `test_workflow_io.py` | Trigger `fields`/`jsonSchema`/`passthrough` → input Shape; output by Respond union, last-node rule, UNKNOWN and WF026; approved-contract precedence; `has_human` through a nested child. |
| `test_execute_workflow_node.py` | `normalize_params` legacy handling; `items` vs `merged` output; `each` mode concatenation and `pairedItem`; `on_error` stop/continue/error_output with per-item routing in `each`; retry makes N invocations; uses `InMemoryInvoker` and a fake invoker. |
| `test_subworkflow_guards.py` | Depth limit, call-stack cycle (self, A → B → A), approval guard is early (the child's first node never runs), timeout → `SUBWORKFLOW_TIMEOUT` and cancellation reaches an awaiting node. |
| `test_checker_subworkflows.py` | WF020–WF029 (Tarjan over overlay plus lookups graph), WF024 type and required cases, WF022 draft-vs-publish severity, WF006 expression `workflowId`. |
| `test_version_resolution.py` | `resolve_child` matrix §4.1 with fake repos; `release_pins` keying; legacy release fallback warning. |
| `test_template_bundles.py` | Every shipped bundle validates and lints; token coverage; secret-value lint catches planted keys, PANs and IBANs; `requires.templates` acyclic. |
| `test_template_plan.py` | `plan_install`: rename numbering, `fail` lists all clashes, table reuse compatibility, slug and webhook suffixing, dependency order. |
| `test_ai_template_fewshot.py` | Template few-shot loaded; eval-overlap exclusion for all 15 cases; grounding includes typed `inputs`. |
| `test_n8n_import_subworkflow.py` | n8n `executeWorkflow` v1.1+ fixtures (resource mapper, `mode: each`, `waitForSubWorkflow: false` warning) and the trigger's `workflowInputs`. |

### 14.2 Integration over HTTP on real Postgres — `tests/integration/`

| File | Covers |
|---|---|
| `test_subworkflow_runs.py` | Parent → child run writes the child row (`mode sub_workflow`, `parent_execution_id`, `parent_node_name`, `depth=1`, `root_execution_id`, `workflow_version_id`); child `node_outputs` persisted; `GET /executions/{id}` parent and children links; list hides children by default; cleanup counts roots only; deleting the root cascades. Child `api_ref` HttpRequest resolves its spec (G6). Unsaved runs (run-to/partial) write no child row. |
| `test_subworkflow_pinning.py` | `latest` follows republish in a draft env; `N` is stable; a `draft` pin refused at publish (422 WF023); child publish returns `impact` with `breaking`. |
| `test_subworkflow_releases.py` | `build_snapshot` closure (2 levels, diamond with two pins of one child); release env run uses the snapshot pin after the child is republished; new run_checks fail on a cycle or approval child; `_diff` lists child changes; legacy release fallback. |
| `test_workflow_refs.py` | `sync_refs` on create, update, publish, restore and install; backfill on an empty table; WF020 on save of the second edge of a cycle; `DELETE` 409 `WORKFLOW_IN_USE`. |
| `test_extract_endpoint.py` | `dry_run` and commit; child published v1 → parent pin `latest`; child publish failure → `draft` plus `child_publish_errors`; transaction rollback on a forced failure. |
| `test_subworkflow_tests_inline.py` | `"subworkflows": {"node": "inline"}` test passes with child `test_defaults`; unmocked child effect → `TEST_UNMOCKED_EFFECT` with path `node/child`; persisted test child row has `mode test`. |
| `test_templates_install.py` | Idempotency, `as_copy`, version 409, `clash: fail` 409, table reuse without seeding, egress allowlist rule (empty stays empty), needs report per environment, dependency reuse, full rollback when a publish fails (a broken bundle fixture under `tests/fixtures/templates_broken/`). |
| `test_templates_ci.py` | §8.5 (the gate; runs in the default suite). |

### 14.3 API journey — `tests/journeys/test_reuse_journey.py`

This follows the existing journey style: one long scenario over HTTP.

1. Create a project.
2. Install `refund-triage`. This also installs `customer-notify`.
3. Run the entry workflow's tests; all pass.
4. Open the entry workflow and extract `["Issue refund", "Notify requester"]` to "Pay and notify", with a dry run and then a commit.
5. Run the tests: the moved mocks pass, and the affected tests are reported stale.
6. Approve or refresh the stale tests, then publish.
7. Cut a release and promote it to the release environment.
8. Republish the child with a changed output field. The impact shows as breaking.
9. A release run still uses the old child pin, and the run succeeds.
10. Re-cutting the release fails the `subworkflow_contracts_compatible` check.
11. Fix the mapping, re-cut the release, and it passes.
12. Read the execution tree: root → child → grandchild (`customer-notify`).

### 14.4 Playwright — `apps/workflow-studio/e2e/tests/20-reuse.spec.ts`

This runs against the live API, under `flock /tmp/e2e.lock`, with fixtures created through the API using a `reuse_` prefix and a prefix sweep. Fonts are blocked, and console errors fail the test (existing rules).

1. **Template install.**
   - Open Templates (`nav-templates`) and filter to payments.
   - Preview `refund-triage`: the secret `REFUNDS_API_KEY` is listed.
   - Install: the result shows tests passed and the needs link.
   - Open the entry workflow, which has an `node-subwf-badge` reading "Refund eligibility · latest".
   - Install again: the card shows `template-installed-badge`, and the result reads "already installed".
2. **Picker and typed mapping.**
   - Add an ExecuteWorkflow node and pick `Refund eligibility`.
   - `subwf-inputs` shows the child fields, and a required one is marked missing.
   - Map it, and the checker finding clears.
   - Downstream `$json.eligible` autocompletes from the typed output.
3. **Trigger inputs.**
   - In the child, add a field through `subwf-trigger-add-field` and save.
   - The parent's mapping table shows the new row after reload.
4. **Convert selection.**
   - Select a 2-node non-convex selection: `convert-subwf-refusal` shows CS05.
   - Select a valid 2 nodes: the preview lists the inputs, then confirm.
   - The canvas now has one ExecuteWorkflow node, and the toast offers "Open sub-workflow". Opening it shows the moved nodes with an `Inputs` trigger.
   - Undo restores the parent.
5. **Run and drill down.**
   - Run the parent manually.
   - The output panel has `run-open-child-execution`; clicking it opens the child run with `exec-parent-link`.
   - The child's node outputs are visible.
6. **Cycle.** In the child, add an ExecuteWorkflow back to the parent: a WF020 finding appears on the node, and publish is blocked.
7. **Approval guard.** Add an Approval node to a child that's in use: a WF025 finding appears on the parent's ExecuteWorkflow node.
8. **Pinning.** Set the version to `v1`, then to Draft: `subwf-draft-warning` appears, and publish shows a WF023 error.
9. **Used by.** The child's `used-by-panel` lists the parent and node.

---

## 15. Dependencies on Stages 5 and 6 (only what their docs say)

| Needed from | What | Used by | If it's late |
|---|---|---|---|
| Stage 5 | The `interface` definition key, Approval node (effect `human`, ports approved/rejected/timed_out), `APPROVAL_IN_SUBWORKFLOW`, `interface_slugs` table, approval mock conventions, the `18-interfaces` spec number, migration `20261019120000` | WF025, guard, template interfaces, slug clash, template tests | Templates 2, 3 and 6 ship without `interface`, as API-triggered variants. Wave 3 B5 waits on `interface_slugs`. Everything else is independent. |
| Stage 6 | `ai_grounding` `existing_workflows`, `ai_default_mocks`, `ai_builder_prompts` few-shot loader and examples format `{request, spec, ops}`, spec `trigger.kind: execute`, `apply_ops` allowlist, fake bank APIs `tests/fixtures/ai_builder/apis/*.yaml`, eval case ids, `19-ai-builder` spec number, migration `20261020120000` | §9; the template APIs | §9 slips to a follow-up wave. Templates copy the API yaml files, so they don't depend on Stage 6 code. |
| Releases (built) | `build_snapshot`, `_pin_workflow`, `run_checks`, `release_extras`, `env_kind` | §4 | — |
| Stage 3A/3B (built) | Test mode mocks, `test_runner.run_tests`, stale tests, `mock_gen`, `test_suggest` | §6, §7 step 8, §8.5 | — |

Stage 8 files that Stage 5/6 builders are also editing are `workflow_runner.py`, `execution_repository.py`, `execution_service.py`, `models.py`, `routes/__init__.py`, `ai_grounding.py` and `ai_builder_prompts.py`. **Stage 8 Wave 1 starts only after Stage 5 and Stage 6 Wave 1 have merged into the working tree**, and Stage 8's AI wave starts after Stage 6 is done.

---

## 16. Build waves (at most 3 parallel Sonnet builders, disjoint files; testers separate)

Rules from HANDOFF-STAGE3 apply:
- builders don't write the tests named in §14; testers do, and each tester runs after their wave;
- one e2e agent, under `flock /tmp/e2e.lock`;
- copy any new NodeDefinition field through the rebuild sites (Stage 8 adds none on purpose);
- no commit until the owner says "commit".

### Wave 1 — engine and data foundation

| Builder | Owns (exclusive) | Delivers |
|---|---|---|
| **B1 engine** | `engine/subworkflow.py` (new), `engine/types.py`, `engine/workflow_runner.py`, `nodes/flow/execute_workflow.py`, `nodes/triggers/execute_workflow_trigger.py`, `nodes/ai/tools/workflow_tool.py` | §2.1–2.3, §3.2, §3.4–3.6, §3.8 runtime parts; `InMemoryInvoker`; `normalize_params`; the G10 label fix |
| **B2 persistence and service** | `db/migrations/20261021120000_reuse.sql`, `db/models.py`, `repositories/execution_repository.py`, `repositories/workflow_ref_repository.py` (new), `services/workflow_refs.py` (new), `services/subworkflow_service.py` (new), `services/execution_service.py`, `services/workflow_service.py`, `main.py` (backfill hook) | §3.3, §4.2, §4.3, §5.1, §10, delete protection; wires `DbSubworkflowInvoker` into every run site that goes through `execution_service` |
| **B3 static analysis** | `services/workflow_io.py` (new), `services/subworkflow_extract.py` (new), `services/workflow_checker.py`, `services/check_lookups.py`, `services/mock_gen.py` | §2.4, §5.2, §5.4, §6.2 (mock_gen part), §7.2–7.3 pure functions |

The B1/B2 contract is the frozen §3.2 interface. B2 codes against the dataclasses as specified, and B1 creates the file first, in its first commit-free checkpoint.

B2 doesn't import B3's `workflow_io`. The runtime `has_human` guard is a direct scan of the child version's nodes for `type == "Approval"`. That's enough at runtime, because each nested child is guarded again when it is called.

**T1** (after Wave 1) writes the §14.1 unit files for W1 plus `test_subworkflow_runs.py` and `test_workflow_refs.py`.

### Wave 2 — releases, routes, templates backend, editor UI

| Builder | Owns | Delivers |
|---|---|---|
| **B4 releases and API** | `services/release_service.py`, `routes/workflows.py`, `routes/executions.py`, `routes/subworkflows.py` (new), `schemas/subworkflow.py` (new), `schemas/workflow.py` (test `subworkflows` field), `engine/test_mode.py`, `services/test_runner.py`, `routes/__init__.py` | §4.4–4.5, §6.3, the §11 endpoints except templates, the extract route |
| **B5 templates backend** | `services/template_bundle.py`, `services/template_installer.py`, `services/authz.py` (new no-op), `routes/templates.py`, `schemas/template.py`, `templates/*/` (6 bundles plus API yaml copies), `tests/fixtures/templates_broken/` | §8 except CI test; §13 hooks. B5 registers its router by adding one line to `routes/__init__.py` in a hand-off note to B4, which owns that file. |
| **B6 studio editor** | `features/workflow-editor/components/ndv/subworkflow/*` (new), `components/canvas/ConvertToSubworkflowDialog.tsx` (new), `components/canvas/NodeContextMenu.tsx`, `components/canvas/nodes/*` (badge), `components/execution-logs/*`, `components/bottom-panel/*` (child links), `hooks/useSubworkflows.ts` (new), `hooks/useKeyboardShortcuts.ts`, `KeyboardShortcutsHelp.tsx` | §12.1–12.6 |

**T2** (after Wave 2) writes the rest of §14.1–14.2 (including `test_templates_ci.py` and `test_templates_install.py`) and the §14.3 journey.

### Wave 3 — templates UI, AI builder, import

| Builder | Owns | Delivers |
|---|---|---|
| **B7 studio templates and release** | `features/templates/*` (new), router registration file, nav rail component, `features/releases/*` (edge list), `features/workflows-list/WorkflowsListPage.tsx` (New → From template), publish-impact dialog in `components/workflow-navbar/*` | §12.6 (impact), §12.7, §12.8 |
| **B8 AI builder** | `services/ai_grounding.py`, `services/ai_builder_prompts.py`, `services/ai_default_mocks.py`, `services/workflow_toolkit.py`, `services/node_catalog.py`, `services/ai_builder.py` (template match step), `features/workflow-editor/components/ai-chat/*` (suggestion card) | §9 |
| **B9 import and polish** | `services/n8n_import.py`, `tests/fixtures/n8n/*subworkflow*` | §2.5 import |

**T3** (after Wave 3) writes `20-reuse.spec.ts` (the single e2e agent, under flock), runs the full engine and e2e suites, `tsc -b`, lint and `design:check`, and reports the baselines.

Estimate: W1 about 4 days, W2 about 5 days, W3 about 3 days, testers overlapping. That's about 3 weeks, matching PLAN.

---

## 17. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Output semantics change.** Today's merged single item becomes per-item, which would silently change downstream `$json` in existing workflows. | `outputMode: merged` for legacy nodes (no `version` param); immutable versions keep old behaviour; WF023 nudges an upgrade at the next publish; a unit test pins legacy output byte for byte. |
| R2 | **Release closure correctness.** A diamond (two pins of one child), a deep chain, or a cycle introduced between check and cut. | Edges keyed by (parent version, node); visited-set BFS; the `subworkflows_resolved` check; a runtime guard as the backstop; the journey covers the republish-after-release case. |
| R3 | **Child row volume and retention.** `each` over 1,000 items makes 1,000 rows, and 100-root pruning plus cascade could delete a lot at once. | Prune counts roots only; cascade; `each` documented as for small batches; a seam setting `settings.subworkflow_rows: "always"\|"on_error"` (v1: always). |
| R4 | **Long child inside a request** (webhook or route parents) holds the HTTP request open. | `timeoutSeconds` default 300, capped by the parent's `max_execution_time`; the separate child session avoids holding the parent transaction. |
| R5 | **Eval contamination.** Templates mirror five Stage 6 eval cases. | `eval_overlap` exclusion plus a unit test across all 15 cases. |
| R6 | **Fake data that looks real** in seed rows or tests (PANs, IBANs). | Bundle lints (Luhn and IBAN detectors, reserved ranges, `*.example` hosts) fail CI. |
| R7 | **Install blast radius.** Adding egress hosts to a project allowlist. | Only when the allowlist is already non-empty; recorded in `mapping.egress_added`; shown in the install result. |
| R8 | **Shared-file contention with Stage 5/6 builders** (`workflow_runner.py`, `models.py`, `execution_service.py`). | Hard ordering: Stage 8 W1 starts after Stage 5 and Stage 6 W1 have landed (§15). |
| R9 | **Convert-selection surprises** (expression rewrites). | Pure function with 30+ cases; `dry_run` preview shows every new input and rewrite; refusal rather than a guess (CS07/CS08); undo. |
| R10 | **Contract drift on `latest`.** A child republish breaks a published parent in a draft environment immediately. | The publish `impact` dialog; the runtime input validation at the child trigger fails loudly (not silently wrong); release environments are immune until the next cut. |

---

## 18. Decisions where we followed n8n, and where we departed

**Followed n8n:**
1. A child must start with an **Execute Workflow Trigger**, which declares its inputs ("Define using fields below" / JSON / "Accept all data").
2. The parent maps inputs per field: the resource-mapper style "Workflow Inputs".
3. "Run once with all items" / "Run once for each item".
4. The child returns **the last node's items**. We add one refinement: the first `Respond` reached, if present.
5. A sub-execution is its own execution, linked both ways ("View sub-execution" / "called by").
6. Convert to sub-workflow requires **a single input and a single output**, and refuses otherwise.
7. Template install creates new ids and suffixes clashing names with "(2)".
8. Double-clicking a sub-workflow node opens the child in a new tab.

**Departed from n8n (bank reasons):**
1. **Version pinning** (`draft`/`latest`/`N`), with release closure. n8n calls the saved workflow.
2. **No recursion**, not even guarded.
3. **No Approval/Wait in a child** (v1).
4. **Same-project only.**
5. **No fire-and-forget** (`waitForSubWorkflow: false` is imported as wait, with a warning).
6. The **`workflowId` must be a literal**, not an expression.
7. Templates **must ship passing tests**, which CI enforces.

---

## 19. Owner decisions needed (each with the default we build if there's no answer)

| # | Question | Default |
|---|---|---|
| O1 | Default `version` for new ExecuteWorkflow nodes | `latest` (published). Draft is allowed while editing but refused at publish. |
| O2 | Approval inside sub-workflows | Forbidden in v1 (WF025 plus a runtime guard). Lifting it is a durable-waits follow-up of about 1 week. |
| O3 | Cross-project sub-workflow calls (a shared "library" project) | Forbidden in v1. The seam is designed (§13.3). |
| O4 | Recursion (a workflow calling itself) | Forbidden. |
| O5 | Starter library contents | The 6 in §8.6. Re-rank once the pilot use case (PLAN open decision 3) is chosen. |
| O6 | Where templates live | In the repo, read-only, CI-gated. User-authored templates come later through export plus a DB table. |
| O7 | A child publish that breaks a parent pinned to `latest` | Warn with an impact dialog. Releases block via `subworkflow_contracts_compatible`. |
| O8 | Legacy ExecuteWorkflow nodes (merged single-item output) | Keep legacy behaviour until the node is edited or republished. No automatic rewrite. |
| O9 | Install auto-publishes `role: subworkflow` children | Yes, so that `latest` pins resolve. Entry workflows stay drafts, with triggers inactive. |

## Lead decisions (2026-10-18, overnight; the owner may overturn any)

1. O1–O9 are accepted at the designer's defaults:
   - O1 a new sub-workflow node pins `latest`, and publishing a parent with a `draft` pin is refused;
   - O2 no Approval inside a sub-workflow in v1 (WF025);
   - O3 no cross-project calls;
   - O4 no recursion;
   - O5 the 6 starter templates;
   - O6 templates are read-only in the repo and gated by CI;
   - O7 a child publish that breaks a parent warns, and releases block;
   - O8 existing sub-workflow nodes keep their merged output;
   - O9 install publishes the template's sub-workflows, while entry workflows stay drafts with triggers off.
2. This doc is the contract; there is no separate CONTRACTS file.
3. AIAgent's output item is `{response, structured: <outputSchema>, toolCalls, iterations}` (corrected 2026-10-18). Typing a child's output must read its contract, never assume flat schema fields.
4. Wave 1 starts after Stage 6 Wave 1 lands. The build follows the same rules: at most 3 builders with separate files, testers separate from builders, and no git.
