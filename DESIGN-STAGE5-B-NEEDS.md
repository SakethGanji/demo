# Builder B needs from Builder A (Stage 5)

Builder B (interfaces) wrote its services against the frozen DESIGN-STAGE5-UIS section 3.2 tables with **raw SQL**
(`repositories/interface_repository.py`, `repositories/chat_repository.py`). It does **not** import any new SQLModel class,
so nothing breaks while the models do not exist. Builder A creates the migration + models **verbatim from section 3.2**.

## 1. Tables / columns B reads and writes (migration `20261019120000_interfaces_tasks.sql`)

| Table | Columns B relies on | B's use |
|---|---|---|
| `interface_slugs` | `project_id, slug, workflow_id, kind, version_id, updated_at`, PK `(project_id, slug)`, unique `(workflow_id)` | publish claims the slug (`INSERT ... ON CONFLICT (project_id, slug) DO UPDATE ... WHERE workflow_id = EXCLUDED.workflow_id`); unpublish / delete release it; public routes resolve through it |
| `interface_submissions` | `id, project_id, workflow_id, slug, idempotency_key, execution_id (FK executions, ON DELETE SET NULL), run_token_hash, mode, created_at`; unique `(project_id, slug, idempotency_key)` | submit dedupe (`ON CONFLICT ... DO NOTHING`), poll-token check |
| `chat_sessions` | `id, project_id, workflow_id, slug, mode, user_label, user_id, token_hash, busy_execution_id, last_seq, created_at, updated_at` | one turn in flight (`SELECT ... FOR UPDATE`) |
| `chat_messages` | `id, session_id (FK, ON DELETE CASCADE), seq, role, content, execution_id, client_message_id, created_at`; unique `(session_id, seq)` and partial unique `(session_id, client_message_id) WHERE client_message_id IS NOT NULL` | history; `client_message_id` also dedupes user sends and assistant replies (`sys:final:<exec>`, `sys:wait:<exec>:<title>`) |
| `human_tasks` | read-only: `id, workflow_id, execution_id, node_name, title, status, assignees, due_at, created_at, decided_by, decided_at` | `RunView.waiting_for` (open tasks: `id, title, due_at`) and the studio preview |

`WorkflowRepository.delete` also deletes B's rows through `InterfaceRepository.purge_workflow` (guarded by
`to_regclass('interface_slugs')`, so it is a no-op until the migration has run). `human_tasks` rows must cascade from `executions`.

If A prefers SQLModel classes, the names in section 3.2 are fine (`HumanTask`, `InterfaceSubmission`, `ChatSession`,
`ChatMessage`, `InterfaceSlug`); B does not import them.

## 2. Hooks and call sites (all isolated; B coded to the frozen signatures)

1. **`executions.metadata.result`** (redacted, <= 256 KB) written by `ExecutionRepository.complete()`. B reads it in
   `InterfaceService._build_run_view` (`meta.get("result")`) and `preview_data`. Until then `result` is `null` and `message` carries `success_message`.
2. **`ExecutionRepository.on_complete_hooks: list[Callable[[ExecutionContext, Execution], Awaitable[None]]]`**: module-level
   (`execution_repository.on_complete_hooks`) or class attribute. B registers `chat_service.on_execution_complete`
   through `chat_service.register_hooks()`, which is called lazily on every chat-session create and can also be called from `main.py`.
   The hook looks the chat session up from `executions.metadata.interface.chat_session_id` (set by B at start) and reads
   `human_tasks` for the "Waiting for ..." message.
3. **Detached run**: `services/interface_service.start_detached_run` wraps `ExecutionService.run_saved_workflow(...)` in
   its own task + session and gets the execution id from `on_context_ready`. It passes `start_node_name`,
   `initial_data_override=[NodeData(item)]`, `version_id` (None = draft), `environment`, `mode="api"`, `substitute_inputs=False`,
   `metadata={"trigger": {"type": "interface", ...}, "interface": {"slug", "submission_id" | "chat_session_id", "mode": "live"|"preview"}}`.
   A may swap its body for a native `detached=True` / `execution_id=` call; callers do not change.
4. **`context.suspensions`** (design 5.1): B's `chat_service.record_outcome` treats a context with non-empty `suspensions` and no
   errors as "waiting" (inserts "Waiting for <title>..." and keeps the session busy).
5. **Task screens**: when the Approval node suspends, it should capture the paths a task screen binds:
   `schemas.interface.task_screen_bindings(definition["interface"], node_name)` returns the `node.<Node>.<path>` bindings
   (without the `node.` prefix; node names may contain dots/spaces, resolve with `split_node_bind`). Store them, redacted, in `task.context._bindings`.
6. `FormTrigger` / `ChatInput` are now in `engine/graph.TRIGGER_NODE_TYPES` (B). A still owns: `_TEST_TRIGGER_TYPES` in
   `workflow_runner.py` (add `FormTrigger`), `find_start_node` (add `FormTrigger`; B passes `start_node_name` itself), the
   trigger recipe list in `engine/test_recipes.py` (~line 850, add `FormTrigger`), `effects.NODE_EFFECTS["FormTrigger"] = "none"`,
   and `broker/policy.py` trigger list.
7. `NodeDefinition` rebuilds: `Workflow` gained `interface: dict | None` (B edited `engine/types.py` minimally for this one field). It survives
   `workflow_to_definition` / `definition_to_workflow`, `WorkflowRepository.update` (keeps the stored interface when a save does not carry one),
   `WorkflowService.update_workflow` and `_workflow_to_dict`. A: if you rebuild `Workflow(...)` anywhere new, pass `interface=`.

## 3. Ambiguities resolved by B

* Executions started by an interface use `mode="api"` (the `executions_mode_check` does not allow `interface_preview`; widening it
  is optional). Live/preview is `metadata.interface.mode`, the trigger is `metadata.trigger = {type: "interface", slug}`.
* Preview submissions share the `(project, slug, idempotency_key)` index with live ones; preview keys are stored with a `pv:` prefix.

## A asks B (Builder A, durable approvals)

1. `src/routes/__init__.py` is yours: please add these two lines (Builder A's `src/routes/tasks.py` exists and is ready):
   ```python
   from .tasks import router as tasks_router                    # with the other route imports
   api_router.include_router(tasks_router, tags=["Tasks"])      # anywhere before `return`; path prefix /tasks is unique
   ```
2. A's migration `src/db/migrations/20261019120000_interfaces_tasks.sql` and the five SQLModel classes (`HumanTask`, `InterfaceSubmission`, `ChatSession`, `ChatMessage`, `InterfaceSlug`) are in place verbatim from section 3.2.
3. `ExecutionRepository.on_complete_hooks` exists (module level `src.repositories.execution_repository.on_complete_hooks`, same list as the class attribute). It is called after every non-test `complete()` commit (success, failed or waiting) with `(context, ExecutionRecord)`.
4. `context.suspensions` entries are `{node, run_index, suspension: Suspension(kind, payload{title, context, form, assignees, due_at, on_timeout, ...}), input_items}`.
5. `executions.metadata.result` is written for runs whose `metadata.interface` is set (Respond body, else the last node's first item), redacted, capped at 256 KB.
6. `services.execution_service.run_detached(workflow_id, **run_saved_workflow kwargs) -> execution_id` is available if you want to drop your own wrapper.
7. `src/services/workflow_service.py::_build_execution_response` still says `status="failed" if context.errors else "success"`. A studio run that hits an Approval therefore reports `success` while the execution is `waiting`. Please use the new helper (already used by `webhook_service` and `public_apps`): `from ..engine.types import run_status` then `status=run_status(context)` (returns `failed` / `waiting` / `success`). `ExecutionResponse.status` is a plain `str`, so nothing else changes.
