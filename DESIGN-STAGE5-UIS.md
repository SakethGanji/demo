# DESIGN — Stage 5: UIs on workflows (forms, chat, custom screens, approvals)

Status: design, frozen for build once the owner signs off. Author: lead architect (Opus). Date: 2026-10-18.
Scope source: PLAN-WORKFLOW-EDITOR.md Stage 5, DESIGN-WORKFLOWS-BACKEND.md §6–§7 (B4), the concept
`screenshots/concepts/workflow-editor-with-ui.png`. Out of scope: login, roles, audit, Stage 4 durable runs.

---

## 0. What the mockup asks for (the target picture)

Refund triage in the editor, with the **Interface** tab on:

- **Canvas.** `Refund request` (Form trigger, "Form · 4 fields") → `Classify` (AI Agent) → `Over limit?` (If).
  - `> $500` → `Manager review` (Approval, "3 waiting", "Custom UI · Review").
    - approved → `Issue refund` (HTTP POST /refunds)
    - rejected → `Notify requester`
  - `≤ $500` → `Auto refund` → `Notify requester`.
  - All paths end at `Show result` (Respond to UI).
- **Right panel tab "Interface".** URL `/i/payments/refunds`, with three cards: Form / Chat / Custom UI.
  - A live preview of the `Manager review` task screen on real run data.
  - "Wired to the workflow automatically" chips: Input `amount, order_id, reason, email` · Task `Manager review` · Context `Classify.summary` · Result `Show result`.
  - An ask bar ("Change the screen…") and **Promote to app**.
- **Review card.** Amount, order, reason, "Flagged because", the AI summary, history rows, and **Reject / Approve refund** buttons.

Done when: a person fills the form, the AI classifies, a manager approves on the review screen, the refund is
issued, and the submitter sees the result. All of this runs on real Postgres, survives an engine restart while
waiting, and works in Stage 3 test mode.

---

## 1. What exists today (findings that shape the design)

| Area | Fact | Consequence |
|---|---|---|
| Runner | `WorkflowRunner.run` is a BFS queue; `pre_populated_states`, `pending_inputs`, `stop_at`, `start_node_name` exist; no suspend. | We add a **suspend signal**. We resume with the existing partial-run machinery (`services/partial_run.py` rebuilds states from `node_outputs`). |
| Wait node | Caps at 300 s with `asyncio.sleep`. | Not durable. The Approval node must not sleep in-process. |
| executions | `status` already documents `waiting`; columns `resume_at`, metadata JSONB. | We reuse `waiting`. `runs/lib/runLabels.ts` already shows it as in flight. |
| Retention | `ExecutionRepository._cleanup` keeps the newest 100 non-test executions. | **Bug-in-waiting:** a waiting run would be pruned. We must exclude `status='waiting'` and any execution with an open task. |
| StaleReaper | Fails `running` executions older than 4 h. | Fine, because waiting is not running. A resumed run is `running` again, with a fresh `start_time`-independent heartbeat (§5.6). |
| CronScheduler | 30 s tick (`_tick` → crons + `_tick_smoke`). | We add `_tick_tasks` (timeouts + resume sweep). |
| Effects | `NODE_EFFECTS`; in test mode an unmocked effect ≠ none fails `TEST_UNMOCKED_EFFECT`; a mock is `{items, port}`. | Approval gets effect **`human`**. A mocked decision is a mock with `port: approved|rejected|timed_out`. No new test-mode concept is needed. |
| Triggers | `_TEST_TRIGGER_TYPES` (runner) includes ChatInput; `graph.TRIGGER_NODE_TYPES` does not. | Add `FormTrigger` to both. Add `ChatInput` to `TRIGGER_NODE_TYPES` (fixes a latent inconsistency). |
| Contracts | Webhook/ChatInput/Start read `params.input_schema` (Shape). `shapes.validate` supports min/max/len/pattern. | FormTrigger's contract **is** `input_schema`. Server-side validation is `shapes.validate`. |
| Public apps | `/a/{slug}`: 404 hides existence, `issue_token/verify_token`, `redact_deep`, `substitute_inputs=False`, allow-list `apps.workflow_ids`. No rate limits. | Public interface routes copy these exact rules. We add a rate-limit hook. |
| App builder | AI agent loop writing TSX; `client_codegen.generate_client` produces a typed TS client; esbuild bundling. | "Promote to app" hands a generated starter (TSX + typed client) to this existing machinery. |
| LLM | `llm_provider.call_llm`; fake LLM + `/fake-llm/script`; Stage 3B `ask_json(task, system, user)` with a fixed first system line. | Screen editing reuses `ask_json` with system line `INTERFACE_AI:EDIT_SCREEN`. |
| Definition storage | `draft_definition` holds nodes, connections, settings, tests, test_defaults; versions snapshot it. | `interface` becomes a new top-level key and **must survive every dump path** (`workflow_to_definition`, update, version snapshot, n8n export). |
| Refund fixture | Webhook → Normalize → If → Risk API / log. No AI, no approval. | We need a new fixture, `refund_triage_ui.json` (§9). |

---

## 2. Concepts and naming (frozen)

- **Interface.** How people use a workflow. One per workflow, kind `form | chat | custom`, stored in the definition.
- **Surface.** The rendered page: the form page, the chat page, or the custom screen.
- **Task.** A durable human step created by an Approval node in a real run. A `human_tasks` row.
- **Inbox.** The studio list of tasks across a project.
- **Screen spec.** A declarative JSON description of a custom screen (§6.3). No arbitrary code until "Promote to app".
- **Live vs preview.** Live = the published version at `/i/{project}/{slug}`. Preview = the draft, run from the editor with `mode=interface_preview` (n8n's Production URL vs Test URL).

---

## 3. Data model

### 3.1 Definition key `interface` (versioned with the workflow)

```jsonc
"interface": {
  "kind": "custom",                 // "form" | "chat" | "custom"
  "slug": "refunds",                // ^[a-z0-9][a-z0-9-]{1,62}$, unique per project (enforced at publish)
  "title": "Refund request",
  "description": "Ask for a refund on an order.",
  "trigger_node": "Refund request", // FormTrigger (form/custom) or ChatInput (chat)
  "result_node": "Show result",     // optional; Respond node whose body is the result; else last output
  "screens": {                      // custom only; keyed by screen id
    "start":  { /* ScreenSpec, §6.3 */ },
    "task:Manager review": { /* ScreenSpec */ },
    "result": { /* ScreenSpec */ }
  },
  "app_id": null,                   // set by Promote to app
  "access": { "mode": "public" }    // placeholder: "public" | "studio"; future: {"roles":[...]} — §8.6
}
```

Rules:
- Absent `interface` means no interface (every existing workflow).
- It is versioned like `tests`. Publishing snapshots it, and live surfaces read it from the **published** version.
- The slug-uniqueness check runs at publish: no other workflow in the project has a published version with the same `interface.slug`. A clash returns 409 `INTERFACE_SLUG_TAKEN`. An index table (§3.4) makes the check cheap.

### 3.2 Migration `20261019120000_interfaces_tasks.sql`

```sql
-- Human tasks (the execution_waits of DESIGN-WORKFLOWS-BACKEND §6, approval kind only for now)
CREATE TABLE human_tasks (
  id               TEXT PRIMARY KEY,                 -- "tsk_" + ulid
  project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id      TEXT NOT NULL,
  workflow_version_id INTEGER,                       -- version the run is pinned to (NULL = draft run)
  execution_id     TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
  node_name        TEXT NOT NULL,
  run_index        INTEGER NOT NULL DEFAULT 0,       -- nth execution of that node in the run (loops)
  kind             TEXT NOT NULL DEFAULT 'approval',
  title            TEXT NOT NULL,                    -- rendered at suspend time
  context          JSONB NOT NULL DEFAULT '{}',      -- rendered, redacted, <= 64 KB
  form             JSONB NOT NULL DEFAULT '[]',      -- field specs (§4.2)
  assignees        JSONB NOT NULL DEFAULT '[]',      -- ["dana@bank.example"] typed strings for now
  status           TEXT NOT NULL DEFAULT 'open',     -- open | approved | rejected | timed_out | cancelled
  due_at           TIMESTAMPTZ,
  on_timeout       TEXT NOT NULL DEFAULT 'timed_out',-- reject | approve | timed_out
  decision         JSONB,                            -- {decision, comment, form_data, decided_by, decided_at}
  decided_by       TEXT,                             -- typed name/email now; principal id later
  decided_at       TIMESTAMPTZ,
  decide_key       TEXT,                             -- Idempotency-Key of the winning decide
  resume_state     TEXT NOT NULL DEFAULT 'none',     -- none | pending | running | done | failed
  resume_owner     TEXT,                             -- boot id of the claiming process
  resume_heartbeat TIMESTAMPTZ,
  snapshot         JSONB NOT NULL,                   -- resume snapshot (§5.3), <= 1 MB
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT human_tasks_status_chk CHECK (status IN ('open','approved','rejected','timed_out','cancelled')),
  CONSTRAINT human_tasks_resume_chk CHECK (resume_state IN ('none','pending','running','done','failed'))
);
CREATE UNIQUE INDEX human_tasks_exec_node_idx ON human_tasks (execution_id, node_name, run_index); -- idempotent suspend
CREATE INDEX human_tasks_inbox_idx   ON human_tasks (project_id, status, created_at DESC, id DESC);
CREATE INDEX human_tasks_due_idx     ON human_tasks (due_at) WHERE status = 'open' AND due_at IS NOT NULL;
CREATE INDEX human_tasks_resume_idx  ON human_tasks (resume_state) WHERE resume_state IN ('pending','running');
CREATE INDEX human_tasks_workflow_idx ON human_tasks (workflow_id, status);

-- Interface submissions: dedupe + map submission -> execution for anonymous polling
CREATE TABLE interface_submissions (
  id              TEXT PRIMARY KEY,                  -- "sub_" + ulid
  project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id     TEXT NOT NULL,
  slug            TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,                     -- client-supplied, <= 128 chars
  execution_id    TEXT REFERENCES executions(id) ON DELETE SET NULL,
  run_token_hash  TEXT NOT NULL,                     -- sha256 of the poll token handed to the submitter
  mode            TEXT NOT NULL,                     -- live | preview
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX interface_submissions_key_idx ON interface_submissions (project_id, slug, idempotency_key);
CREATE INDEX interface_submissions_exec_idx ON interface_submissions (execution_id);

-- Chat
CREATE TABLE chat_sessions (
  id            TEXT PRIMARY KEY,                    -- "chs_" + ulid
  project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_id   TEXT NOT NULL,
  slug          TEXT NOT NULL,
  mode          TEXT NOT NULL,                       -- live | preview
  user_label    TEXT,                                -- typed name now; principal id later (user_id col reserved)
  user_id       TEXT,                                -- NULL until identity lands
  token_hash    TEXT NOT NULL,                       -- sha256 of the session bearer token
  busy_execution_id TEXT,                            -- non-null while a turn is in flight
  last_seq      INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX chat_sessions_wf_idx ON chat_sessions (workflow_id, updated_at DESC);

CREATE TABLE chat_messages (
  id           TEXT PRIMARY KEY,                     -- "chm_" + ulid
  session_id   TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('user','assistant','error')),
  content      TEXT NOT NULL,                        -- <= 16 KB
  execution_id TEXT,
  client_message_id TEXT,                            -- dedupe of user sends
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX chat_messages_seq_idx ON chat_messages (session_id, seq);
CREATE UNIQUE INDEX chat_messages_client_idx ON chat_messages (session_id, client_message_id) WHERE client_message_id IS NOT NULL;

-- Live slug index (written at publish, deleted when the workflow is deleted)
CREATE TABLE interface_slugs (
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  slug        TEXT NOT NULL,
  workflow_id TEXT NOT NULL,
  kind        TEXT NOT NULL,
  version_id  INTEGER NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, slug)
);
CREATE UNIQUE INDEX interface_slugs_wf_idx ON interface_slugs (workflow_id);
```

SQLModel classes go in `db/models.py`: `HumanTask`, `InterfaceSubmission`, `ChatSession`, `ChatMessage`, `InterfaceSlug`.

### 3.3 `executions` usage (no new columns)

- `status='waiting'` while a run has at least one open task and nothing else is running.
- `metadata.suspension = {"tasks": ["tsk_..."], "since": iso}`.
- `metadata.result = {...}`: the redacted result, capped at 256 KB. Written by `complete()` (§5.5). Over the cap, it becomes `{"truncated": true}`.
- `metadata.interface = {"slug": "...", "submission_id": "...", "mode": "live|preview"}`.

### 3.4 Size limits (frozen)

| Thing | Limit | On breach |
|---|---|---|
| Form submit body | 64 KB | 413 `PAYLOAD_TOO_LARGE` |
| String field with no maxLength | 4 000 chars default | 422 field error |
| Task `context` | 64 KB after render | Truncate values, set `context._truncated=true` |
| Task decide body | 32 KB | 413 |
| Chat message | 16 KB | 413 |
| Chat history passed to the run | last 20 messages and at most 32 KB | Oldest dropped |
| `metadata.result` | 256 KB | `{"truncated": true}` |
| Task snapshot | 1 MB | Node fails `APPROVAL_SNAPSHOT_TOO_LARGE` |
| Screen spec | 128 KB, 200 components | 422 `SCREEN_TOO_LARGE` |

---

## 4. Node types

### 4.1 `FormTrigger` (new; `nodes/triggers/form_trigger.py`)

| Param | Type | Default | Notes |
|---|---|---|---|
| `input_schema` | Shape | `{type: object, properties: {}, required: []}` | **The contract.** |
| `title` | string | workflow name | Form heading |
| `description` | string | "" | |
| `submit_label` | string | "Submit" | |
| `field_order` | string[] | schema order | |
| `fields_ui` | `{[field]: {label?, help?, placeholder?, widget?}}` | {} | `widget` ∈ text, textarea, number, currency, select, radio, checkbox, email, date |
| `success_message` | string | "Submitted" | Shown when the run has no result node |

- Output: one item `{...validated fields, "_submission": {id, submitted_at, mode}}`. Field values sit at the top level, like n8n's Form Trigger, so `{{ $json.amount }}` works.
- Effect: `none`. It is a trigger and is added to `_TEST_TRIGGER_TYPES` and `TRIGGER_NODE_TYPES`.
- Declared contract: `output = input_schema` plus `_submission` (open).
- Widget derivation from the Shape (frozen in `engine/form_fields.py`, shared by the backend and the studio via the `/schema` response):
  - string → text. string with `maxLength > 200` → textarea. `format:"email"` → email. `format:"date"` → date.
  - `enum` → select (≤ 7 options → radio).
  - number/integer → number. `format:"currency"` → currency.
  - boolean → checkbox.
  - Arrays and nested objects are **unsupported** in Stage 5. The checker warns WF013 and the form omits them.

### 4.2 `Approval` (new; `nodes/flow/approval.py`)

| Param | Type | Default | Notes |
|---|---|---|---|
| `title` | expression string | `"{{ $node.name }}"` | Rendered at suspend |
| `assignees` | string[] (expressions ok) | [] | Typed emails/names. Empty = anyone in the project. **No enforcement in Stage 5** (§8.6) |
| `context` | `{[label]: expression}` | {} | e.g. `{"Summary": "{{ $('Classify').item.json.summary }}"}` |
| `form` | `FieldSpec[]` | [] | `{name, label, type: text\|textarea\|number\|select\|checkbox, required?, options?}`. Extra inputs for the approver |
| `allow_comment` | bool | true | |
| `require_comment_on_reject` | bool | false | |
| `due_in` | duration string `^\d+[mhd]$` | "" (none) | Minimum 1 m, maximum 90 d |
| `on_timeout` | enum `timed_out \| reject \| approve` | `timed_out` | Default is the dedicated port (safest: the workflow decides) |
| `four_eyes` | bool | false | **Placeholder.** Stored, shown, not enforced until identity (checker info WF016) |

- Ports: `approved` (0), `rejected` (1), `timed_out` (2). Plus `error` when `on_error=error_output`.
- Output item on resume: `{...input item json, "approval": {decision: "approved"|"rejected"|"timed_out", comment, form_data, decided_by, decided_at, task_id}}`.
  - The input is passed through so downstream `{{ $json.amount }}` still works. This follows n8n's Wait/Form: the input passes through and the response is merged in.
- One task **per input item batch**, not per item. The node runs once per arrival (n8n Wait semantics). The decision applies to all its items. The checker warns WF014 if the node can receive more than one item (best effort: upstream is a SplitOut/loop).
- Effect: **`human`** (new value in `NODE_EFFECTS`). An unmocked Approval in a test run fails `TEST_UNMOCKED_EFFECT`.
- Real-run behaviour: §5.

### 4.3 `RespondToUI`: reuse the Respond node

No new node. `Show result` is a Respond node. The interface's `result_node` points at it. `ExecutionContext.webhook_response` already captures its body. `complete()` copies it into `metadata.result` (§5.5). The studio palette label stays "Respond". The mockup's "Respond to UI" is a display alias when the workflow has an interface.

### 4.4 `ChatInput` (extended, backward compatible)

- Output now: `{message, timestamp, session_id, history: [{role, content, at}], user_label}`. `history` excludes the current message.
- `input_schema` default gains `session_id: string`, `history: array`, both optional.
- The memory nodes (`sessionId` param) can use `{{ $json.session_id }}`. The fixture shows it.

### 4.5 Checker additions (`services/checker` / mistake checker; lenient, warnings unless noted)

| Code | Level | Rule |
|---|---|---|
| WF013 | warn | FormTrigger field type not renderable (array/object) |
| WF014 | warn | Approval may receive more than one item per arrival |
| WF015 | warn | Approval `rejected` or `timed_out` port unconnected while `on_timeout=timed_out` (output dropped silently) |
| WF016 | info | `four_eyes` set; not enforced until identity |
| WF017 | **error** | Interface `trigger_node` missing or of the wrong type for `kind` |
| WF018 | warn | Screen spec binding refers to a field/node not in contracts (§6.3) |
| WF019 | warn | Interface has no `result_node` and the last node can produce many items |

WF010–WF012 stay reserved as in Stage 2.

---

## 5. Runner changes: minimal durable pause/resume

Goal: an Approval pauses for hours or days, survives restart, and resumes **at most once** per decision. This is not Stage 4. There are no general durable waits, no checkpoint per node, and no replay.

### 5.1 Suspend signal

- `engine/types.py`: a node signals a pause by returning `NodeResult(suspend=Suspension(...))`. It does not raise, so retry and `on_error` logic never see a pause as a failure.
- New dataclass `Suspension{kind: "approval", payload: dict}` (title, context, form, assignees, due_at, on_timeout).
- `NodeResult` gains `suspend: Suspension | None = None`.
- `WorkflowRunner._process_job`:
  1. Mocks are checked **first**, as today. A mocked Approval (`{items, port}`) never suspends. This is the test-mode path.
  2. Pinned data on an Approval: emit the pinned items on `approved` (port 0). This matches n8n pinned behaviour.
  3. If `result.suspend` is set and `context.mode` is not a test mode:
     - record `context.suspensions.append({node, run_index, suspension, input_items})`
     - store the node's input as its "pending" output in `node_outputs` with `status="waiting"`
     - **do not queue** downstream.
  4. If `result.suspend` is set in test mode with no mock: fail `TEST_UNMOCKED_EFFECT` (effect `human`). This is already the generic path once `NODE_EFFECTS["Approval"]="human"`.
- The other branches keep running. When the queue drains:
  - `context.suspensions` non-empty and no errors → the run outcome is **waiting**.
  - With errors → **failed** (tasks are not created; it fails like any run).

### 5.2 Creating tasks (in `ExecutionRepository.complete`)

`complete(context, ...)`:
- If `context.suspensions` is set and there are no errors, then in one transaction:
  1. `INSERT INTO human_tasks ... ON CONFLICT (execution_id, node_name, run_index) DO NOTHING` (idempotent).
  2. Set `executions.status='waiting'`, `metadata.suspension`, `end_time=NULL`.
  3. Skip the drift hook and retention cleanup for this call.
- `title`/`context` are rendered by the node at suspend time with the normal expression engine, then `redact_deep(secret_values)`. Secrets never land in a task row.

### 5.3 Resume snapshot (`human_tasks.snapshot`)

```jsonc
{
  "v": 1,
  "definition_source": {"version_id": 7} | {"draft_definition": {...}},  // draft runs embed the definition
  "start_node": "Manager review",
  "input_items": [{"json": {...}}],
  "pending_inputs": {"Merge": {"0": [...]}},   // runner pending_inputs at suspend, for Merges downstream
  "node_run_counts": {"Classify": 1},
  "mode": "webhook", "environment": "live", "project_id": "prj_...",
  "extras_keys": {"interface": {...}}           // non-secret extras only; secrets/vars reloaded at resume
}
```

- Variables and secrets are **reloaded** at resume, never stored.
- Published runs reference `version_id`, so a later publish does not change an in-flight run. This is the n8n "execution runs the version it started on" rule.

### 5.4 Deciding (idempotent, concurrency-safe)

`TaskService.decide(task_id, decision, comment, form_data, decided_by, idempotency_key)`:

```sql
UPDATE human_tasks
   SET status = :status, decision = :decision, decided_by = :by, decided_at = now(),
       decide_key = :key, resume_state = 'pending', updated_at = now()
 WHERE id = :id AND status = 'open'
RETURNING *;
```

- 1 row → this caller won. Return 200 `{task, outcome: "decided"}` and kick the resumer (an in-process `asyncio.create_task`, plus the scheduler sweep as a backstop).
- 0 rows → re-read the task:
  - Not found → 404.
  - Already decided with the same `decide_key` → 200 `{task, outcome: "decided"}` (a true retry of the same click).
  - Already decided otherwise → 200 `{task, outcome: "already_decided"}`. The body carries the winning decision, so the UI shows "Already approved by Dana at 14:02".
  - cancelled/timed_out → 200 `{task, outcome: "already_closed"}`.
- **Two approvers at once:** the row-level `WHERE status='open'` means exactly one UPDATE wins (Postgres row lock). No advisory lock is needed.
- Validation before the UPDATE:
  - `decision ∈ {approved, rejected}`.
  - `form_data` is validated against the task `form` (required, types, options) → 422 with field errors.
  - `require_comment_on_reject`.
  - `decided_by` is required, 1–200 chars, trimmed.

### 5.5 Resumer (`engine/task_resumer.py`)

`claim_and_resume(task_id)`. Claim in one transaction:

```sql
UPDATE human_tasks SET resume_state='running', resume_owner=:boot, resume_heartbeat=now()
 WHERE id=:id AND resume_state='pending' RETURNING *;
UPDATE executions SET status='running' WHERE id=:exec AND status='waiting';
```

- The second statement updates 0 rows if the execution was cancelled. In that case set `resume_state='done'` and stop.
- If other tasks of the same execution are still open (parallel approvals), the execution stays `waiting`. The resume runs the decided branch only. Its `complete()` sees the remaining open tasks and writes `waiting` again, not success. The rule: **status = waiting while any task of the execution is open**.

Run:
1. Build `pre_populated_states` from `node_outputs` of the execution (successful outputs, as `partial_run.run_from`). Exclude the Approval node itself.
2. Call `ExecutionService.run_saved_workflow(..., version_id=snapshot.version_id, start_node_name=task.node_name, initial_data_override=snapshot.input_items, pre_populated_states=..., pending_inputs=snapshot.pending_inputs, execution_id=task.execution_id, resume_decision=task.decision)`.
   - New kwargs: `execution_id` (reuse instead of `gen_exec_id`), `resume_decision`, and `skip_start=True` (do not call `execution_repo.start`; flip `waiting→running` already done).
   - The runner passes `resume_decision` into the Approval node's context via `extras["resume"] = {node, run_index, decision}`. The Approval node then emits its input + `approval` on the decision port instead of suspending.
3. `complete()` **merges** node outputs (the reused set is skipped) and writes `success | failed | waiting`. Then set `resume_state='done'`.
   - If it raises, set `resume_state='failed'` and the execution fails with error code `RESUME_FAILED`.

Heartbeat: every 30 s while running.

**Crash safety (at-most-once, explicit):**
- Startup sweep: tasks with `resume_state='running'` and a stale heartbeat (> 2 min) or a different `resume_owner` are **not re-run**.
  - Set them `resume_state='failed'` and mark the execution failed with `RESUME_INTERRUPTED` ("engine stopped while resuming after approval; downstream may have partly run — check before retrying").
  - Rationale: re-running could issue a refund twice. Exactly-once needs Stage 4 idempotency keys. The operator can use the existing **retry from failure** deliberately.
- `resume_state='pending'` tasks (decided, never claimed) **are** resumed by the sweep. Nothing downstream has run, so this is safe.
- The idempotency key `{execution_id}:{node}:{run_index}` (DESIGN-BACKEND §6.4) is exposed to downstream nodes as `{{ $execution.resumeKey }}` so HTTP nodes can send it as an `Idempotency-Key` header. The fixture does.

### 5.6 Scheduler tick (`cron_scheduler._tick_tasks`, every 30 s)

1. **Timeouts:**
   ```sql
   SELECT id FROM human_tasks WHERE status='open' AND due_at <= now()
   ORDER BY due_at LIMIT 100 FOR UPDATE SKIP LOCKED
   ```
   - For each: set `status` from `on_timeout`: `approve→approved`, `reject→rejected`, `timed_out→timed_out`.
   - Set `decided_by='system:timeout'`, `resume_state='pending'`.
   - `SKIP LOCKED` makes it safe across two engine instances.
2. **Resume sweep:** `resume_state='pending'` older than 5 s (lets the in-process kick win). Claim and resume, at most 20 per tick.
3. **Stale `running` resumes:** as §5.5. Also run once at startup in `main.py` before the scheduler starts.

### 5.7 Other engine touch points

| Where | Change |
|---|---|
| `ExecutionRepository._cleanup` | Exclude `status='waiting'` and executions with `human_tasks.status='open'` from retention pruning. **Required.** |
| `ExecutionRepository.cancel` / `ExecutionService.cancel_execution` | Allow cancelling `waiting` runs. `UPDATE human_tasks SET status='cancelled' WHERE execution_id=:id AND status='open'`. |
| `StaleReaper` | It only reaps `running`, but it ages runs by `start_time`, so a run resumed 3 days after it started would be reaped at once. Fix: the resume claim stores `metadata.resumed_at`, and the reaper ages by `COALESCE(metadata->>'resumed_at', start_time)`. Builder A proves it with a test. |
| `engine/effects.py` | `"Approval": "human"`, `"FormTrigger": "none"`. The `human` effect counts as "needs a mock" in test mode. |
| `services/scorecard.py` `branches_of` | Approval contributes ports `approved`, `rejected`, `timed_out` (only `timed_out` when `due_in` is set or `on_timeout=timed_out`). |
| `engine/test_recipes.py` | Recipes for Approval: "approved path", "rejected path", "timed out path". Each generates a test with mock `{items: <input passthrough + approval>, port}`. |
| `services/mutations.py` | `swap_branches` extended to Approval (swap approved/rejected targets), so the mutation score catches tests that don't check the decision. |
| `test_suggest` / `partial_run` | Use `TRIGGER_NODE_TYPES`, which now includes FormTrigger and ChatInput. |
| `routes/streaming.py` | A waiting run ends the stream with `{"type": "execution_waiting", "tasks": [...]}` instead of `execution_complete`. |
| `$execution` expression root | Add `resumeKey` and `id` if not present. |

---

## 6. Interfaces

### 6.1 Form

- Schema: `GET /api/i/{project_slug}/{slug}/schema` returns fields derived from the FormTrigger `input_schema` (§4.1). It contains **no** node internals, no expressions and no other node names.
- Submit: `POST /api/i/{p}/{slug}/submit`, body `{fields: {...}}`, header `Idempotency-Key` (required; the studio generates a uuid per form open).
- Server validation:
  1. 64 KB cap.
  2. Unknown fields → 422 `UNKNOWN_FIELD`.
  3. `shapes.validate(input_schema, fields)` + default `maxLength`.
  4. Strings are NFC-normalized and trimmed. No expression evaluation of input. Values starting with `{{` are data and are never evaluated, because `substitute_inputs=False`.
- The run starts **detached** (`asyncio.create_task`, registered in `execution_registry`), so closing the browser doesn't cancel it.
- Response 202 `{submission_id, execution_id, run_token, poll_url}`.
- The same Idempotency-Key returns the **same** 202 body (dedupe by the unique index; `run_token` is reissued for the same submission).

**Progress: polling, not SSE (decision).**
- `GET /api/i/{p}/{slug}/runs/{execution_id}` with `Authorization: Bearer <run_token>`.
- The client polls every 0.5 s for the first 5 s, then 2 s, then every 10 s after 2 minutes.

Why polling:
1. Runs may wait for an approval for days. Nobody holds an SSE connection for days. The page must work on reload, on another device, and after an engine restart. A poll URL does; a stream doesn't.
2. Bank proxies and WAFs commonly buffer or kill `text/event-stream`.
3. The existing SSE in `routes/streaming.py` ties the run's life to the request (cancel on disconnect). That is exactly wrong for a public form.
4. Polling is stateless and horizontally scalable: any instance answers from Postgres. A cheap indexed read by PK is fine at bank volumes.
5. SSE can be added later as an optimisation on the same response shape.

Run view response (the **only** thing a public client sees about a run):

```jsonc
{
  "execution_id": "exe_...",
  "status": "running" | "waiting" | "success" | "failed" | "cancelled",
  "progress": { "done": 3, "total": 7, "current": "Classify" },   // current = node *label*, not internals; omitted if interface.access.hide_progress
  "waiting_for": [ { "task_id": "tsk_...", "title": "Manager review", "due_at": "..." } ],  // titles only
  "result": { ... } | null,      // metadata.result, redacted; only when success
  "message": "Submitted" | null, // success_message when no result
  "error": { "code": "RUN_FAILED", "message": "Something went wrong. Reference exe_..." } | null  // never node errors/stack
}
```

`total` is the count of enabled nodes reachable from the trigger. `done` is distinct nodes with outputs.

### 6.2 Chat

- `POST /api/i/{p}/{slug}/chat/sessions` with body `{user_label?}` returns 201 `{session_id, session_token}`. The token is a bearer, stored hashed.
- `GET /api/i/{p}/{slug}/chat/sessions/{sid}/messages?after=<seq>&limit=50` returns `{messages: [{seq, role, content, created_at}], busy: bool, next_after}`.
- `POST /api/i/{p}/{slug}/chat/sessions/{sid}/messages` with body `{content, client_message_id}` returns 202 `{seq, execution_id}`. Inside one transaction:
  1. `SELECT ... FOR UPDATE` on the session. If `busy_execution_id` is set and that execution is running → **409 `CHAT_BUSY`**. One in-flight turn per session keeps history ordered.
  2. Insert the user message (`seq = last_seq+1`). A duplicate `client_message_id` returns the original 202.
  3. Set `busy_execution_id`.
  4. Start a detached run with ChatInput item `{message, timestamp, session_id, history, user_label}`.
- On completion (a hook in `complete()` when `metadata.interface.chat_session_id` is set):
  - Insert the assistant message, taken from the first that exists: Respond body `.text|.message|.response` (string) → last node output `.response|.text|.message|.output` → JSON-stringified body (truncated to 16 KB).
  - On failure, insert role `error` with "Something went wrong (ref exe_...)".
  - Clear `busy`.
- A waiting chat run (an approval inside a chat flow) inserts an assistant message "Waiting for <task title>…" and keeps `busy`. The reply arrives after the resume.
- The client polls messages every 1 s while `busy`, else every 10 s while visible.
- Session tokens are scoped to `(project, slug, session)`. 404 hides other sessions.

### 6.3 Custom UI: a declarative screen spec (decision)

Stage 5 custom screens are **JSON specs rendered by a studio renderer**, not free TSX. Free code arrives only via "Promote to app" into the existing app builder.

This is a deliberate narrowing of DESIGN-BACKEND §7 ("an app of kind workflow_interface"). Why:
- **Safe for a bank.** There is no arbitrary code on a public URL and no XSS surface. The renderer escapes everything. Links are restricted to `https:` with a project egress-style allowlist.
- **Checkable.** Every binding is validated against node contracts (WF018).
- **Deterministic tests.** The fake LLM returns a spec. Playwright asserts testids.
- **Live preview for free.** The same renderer runs on run data in the editor.
- It still grows into a real app when someone needs more.

ScreenSpec (frozen v1):

```jsonc
{
  "v": 1,
  "title": "Refund request",
  "layout": "card",                       // card | page
  "components": [
    { "type": "heading",  "text": "Refund request" },
    { "type": "status",   "bind": "task.status", "due": "task.due_at" },
    { "type": "amount",   "label": "Amount", "bind": "input.amount", "currency": "USD", "size": "xl" },
    { "type": "text",     "bind": "input.order_id", "template": "Order #{{value}} · {{input.email}}" },
    { "type": "field",    "label": "Reason", "bind": "input.reason" },
    { "type": "field",    "label": "Flagged because", "value": "Over $500 limit" },
    { "type": "callout",  "tone": "ai", "label": "AI summary", "bind": "node.Classify.summary" },
    { "type": "list",     "label": "History", "bind": "node.Lookup history.orders", "item": "{{date}} · {{text}}" },
    { "type": "link",     "label": "Carrier tracking", "bind": "node.Classify.tracking_url" },
    { "type": "form",     "bind": "task.form" },                // renders the Approval form fields
    { "type": "actions",  "bind": "task" }                      // Reject / Approve (labels overridable)
  ]
}
```

- Component types (closed set): `heading, text, field, amount, status, callout, list, table, link, divider, form, actions, result, input_form` (the start form).
- Binding roots (closed set):
  - `input.*` — the trigger's output fields.
  - `node.<Node name>.<path>` — the first item of that node's output; must be **upstream** of the screen's task node.
  - `task.*` — `title, status, due_at, assignees, context, form`.
  - `result.*` — the result screen only.
  - `run.*` — `id, status, started_at`.
  - Templates use `{{path}}` relative to these roots only. There is no expression engine: plain path lookup, so nothing is executable.
- The **"Wired to" chips** are computed from the spec bindings and the interface:
  - Input = `input.*` fields used.
  - Task = task nodes with screens.
  - Context = `node.*` bindings.
  - Result = `result_node`.
- **Data exposure rule (important for a bank):** a task screen can show only what the Approval node put into the task `context`, plus `input.*`. `node.*` bindings on **public** screens resolve **at suspend time** into `task.context._bindings`.
  - The Approval node captures exactly the bound paths, redacted, when it suspends.
  - So a public task page never reads `node_outputs`. Screens can't leak data the workflow author didn't bind.
  - The live preview in the studio reads `node_outputs` (internal, the author already sees them).

**Change the screen with AI.** `POST /api/workflows/{id}/interface/screens/{screen_id}/ai-edit`, body `{instruction, spec}`, returns `{spec, summary, warnings[]}`.
- Adapter: `services/interface_ai.py` → `engine/test_ai.ask_json`-style with system first line `INTERFACE_AI:EDIT_SCREEN`.
- The prompt includes: the current spec, the available bindings (from contracts and the last run's shapes), the component catalogue and the rules.
- The response is validated against the ScreenSpec JSON schema and the binding rules. Invalid → one repair round-trip → else 422 `AI_SPEC_INVALID` with the reasons.
- Not configured (`llm_provider.is_configured()` false) → 503 `LLM_NOT_CONFIGURED`.
- Tests script the fake LLM by the system line.
- **Generate initial screens** (`POST /api/workflows/{id}/interface/generate`, body `{kind}`) is **deterministic, no LLM**:
  - start = `input_form`
  - each Approval → task screen with input fields + context + form + actions
  - result screen.
  - AI is only for edits. This keeps the default path working with no LLM credit (a known blocker).

**Typed client.** `GET /api/workflows/{id}/interface/client.ts` returns a TS module generated by `services/interface_client_codegen.py` (built on `client_codegen._ts_type`):

```ts
export interface RefundsInput { amount: number; order_id: string; reason: "not_received" | "damaged" | "other"; email: string }
export interface RefundsResult { status: string; refund_id?: string; message?: string }
export interface ManagerReviewDecision { comment?: string; form_data: { /* from Approval.form */ } }
export type RunStatus = "running" | "waiting" | "success" | "failed" | "cancelled";
export interface RunView<R> { execution_id: string; status: RunStatus; progress?: {...}; waiting_for: TaskRef[]; result: R | null; error: {...} | null }
export function createClient(baseUrl: string, opts?: { fetch?: typeof fetch }): {
  startRun(input: RefundsInput, idempotencyKey?: string): Promise<{ execution_id: string; run_token: string }>;
  getRun(executionId: string, runToken: string): Promise<RunView<RefundsResult>>;
  watchRun(executionId: string, runToken: string, onUpdate: (r: RunView<RefundsResult>) => void, signal?: AbortSignal): Promise<RunView<RefundsResult>>; // polling w/ backoff
  listTasks(params?: { status?: "open" | "closed"; cursor?: string; limit?: number }): Promise<Page<TaskSummary>>;
  getTask(taskId: string): Promise<TaskDetail<"Manager review">>;
  approveTask(taskId: string, d: ManagerReviewDecision & { decided_by: string }, idempotencyKey?: string): Promise<DecideResult>;
  rejectTask(taskId: string, d: ManagerReviewDecision & { decided_by: string }, idempotencyKey?: string): Promise<DecideResult>;
}
```

- The four verbs required by the brief map as: start run = `startRun`, watch run = `watchRun`, list tasks = `listTasks`, approve task = `approveTask`/`rejectTask`.
- Generation is deterministic (sorted keys) so it can be snapshot tested. It is type-checked with `tsc --noEmit` in an integration test (§10).

**Promote to app.** `POST /api/workflows/{id}/interface/promote` returns 201 `{app_id, app_url}`:
- Creates an `AppModel` with `workflow_ids=[id]`, `slug=<interface.slug>-app`, and `draft_source_code` = generated TSX (`App.tsx` rendering the specs with plain JSX + `api/client.ts` = the typed client).
- Sets `interface.app_id` and saves the draft.
- Idempotent: a second call returns the existing app (200).
- From then on, the existing app builder AI edits it. The interface panel shows "Promoted to app → Open app builder" and the spec editor becomes read-only (the app is the source of truth). The public surface stays the spec renderer until the app is published.
- Tasks API for apps: the app reads tasks through the same `/api/i/...` public task endpoints, using its app token. `apps.workflow_ids` is the allow-list.

### 6.4 Public task screens (the approver's page)

The approver opens `/i/{project_slug}/{slug}/tasks/{task_id}`. In Stage 5 there is no login, so the page asks for "Your name or email" (remembered in `localStorage`, best effort) and sends it as `decided_by`.

Bank reality check: an unauthenticated public approve URL is unacceptable. So in Stage 5:
- **Task endpoints are internal (studio) only by default.** `interface.access.mode="public"` exposes **form submit, run polling and chat** publicly.
- Task screens are served under the studio (`/api/tasks/...`).
- The public `/api/i/.../tasks/*` routes exist only when `interface.access.public_tasks=true`. Default false. The studio warns "anyone with the link can approve". In that case they require a per-task bearer `task_token`, handed only to assignees later.

The vertical slice uses the studio inbox and the studio-hosted task page.

---

## 7. Endpoints (frozen)

All JSON. Errors use the existing envelope `{"detail": {"code": "...", "message": "...", "fields"?: {...}}}`. Internal routes sit under `/api/...` with no project prefix (as Stages 2–3).

### 7.1 Internal (studio)

| Method & path | Request | Response |
|---|---|---|
| `GET /api/workflows/{id}/interface` | — | `{interface: Interface \| null, live: {slug, version_id, url} \| null, wired: Wired, warnings: Issue[]}` |
| `PUT /api/workflows/{id}/interface` | `Interface` (full replace; `null` removes) | `{interface, wired, warnings}`. 422 `INTERFACE_INVALID` with fields |
| `POST /api/workflows/{id}/interface/generate` | `{kind: "form"\|"chat"\|"custom", slug?}` | `{interface}` (not saved; the studio PUTs it) |
| `POST /api/workflows/{id}/interface/preview/submit` | `{fields}` + `Idempotency-Key` | 202 `{execution_id, submission_id}` (draft run, `mode="interface_preview"`) |
| `GET /api/workflows/{id}/interface/preview/data?execution_id=` | — | `{input, nodes: {[name]: json}, tasks: TaskSummary[], result}` (for the live preview; defaults to the latest non-test run of this workflow) |
| `POST /api/workflows/{id}/interface/screens/{screen_id}/ai-edit` | `{instruction (1–2000 chars), spec}` | `{spec, summary, warnings}`; 422 `AI_SPEC_INVALID`; 503 `LLM_NOT_CONFIGURED` |
| `GET /api/workflows/{id}/interface/client.ts` | — | `text/plain` TS |
| `POST /api/workflows/{id}/interface/promote` | `{}` | 201/200 `{app_id, app_url}` |
| `GET /api/tasks?project_id=&status=open\|closed\|all&workflow_id=&assignee=&cursor=&limit=` | — | `{items: TaskSummary[], next_cursor}`; keyset on `(created_at DESC, id DESC)`; limit 1–100, default 25 |
| `GET /api/tasks/{task_id}` | — | `TaskDetail` |
| `POST /api/tasks/{task_id}/decide` | `{decision: "approved"\|"rejected", comment?, form_data?, decided_by}` + `Idempotency-Key` (required) | 200 `{task: TaskDetail, outcome: "decided"\|"already_decided"\|"already_closed"}`; 422 fields; 404 |
| `GET /api/tasks/counts?project_id=` | — | `{open: n, by_workflow: {[id]: n}}` (the "3 waiting" badge) |
| `POST /api/executions/{id}/cancel` | existing | Now also cancels `waiting` + open tasks |

```ts
type TaskSummary = { id; workflow_id; workflow_name; execution_id; node_name; title; status;
                     assignees: string[]; due_at: string|null; created_at; decided_by: string|null; decided_at: string|null };
type TaskDetail  = TaskSummary & { context: Record<string, unknown>; form: FieldSpec[]; decision: Decision|null;
                     screen: ScreenSpec|null; input: Record<string, unknown>; allow_comment: boolean;
                     require_comment_on_reject: boolean; on_timeout: string; run_status: string };
type Wired = { input: string[]; tasks: string[]; context: string[]; result: string|null };
```

The cursor is an opaque base64 of `created_at|id`.

### 7.2 Public (anonymous): `/api/i/{project_slug}/{slug}/...`, mounted in a new `routes/public_interfaces.py`

Resolution: `interface_slugs` → published version → `interface`. Not found, unpublished, or `access.mode="studio"` → **404** (hide existence).

| Method & path | Request | Response |
|---|---|---|
| `GET /schema` | — | `{kind, title, description, submit_label, fields: FormField[], screens?: {start?, result?}}` (task screens never public unless `public_tasks`) |
| `POST /submit` | `{fields}` + `Idempotency-Key` | 202 `{submission_id, execution_id, run_token, poll_url}` |
| `GET /runs/{execution_id}` | `Authorization: Bearer run_token` | `RunView` (§6.1); 404 if the token doesn't match |
| `POST /chat/sessions` | `{user_label?}` | 201 `{session_id, session_token}` |
| `GET /chat/sessions/{sid}/messages?after=&limit=` | Bearer session_token | `{messages, busy, next_after}` |
| `POST /chat/sessions/{sid}/messages` | `{content, client_message_id}` | 202 `{seq, execution_id}`; 409 `CHAT_BUSY` |

`FormField = {name, label, widget, required, help?, placeholder?, options?: {value,label}[], min?, max?, minLength?, maxLength?, pattern?}`.

**Rules for every public route:**
- `substitute_inputs=False`. Outputs are passed through `redact_deep(secret_values)`.
- No node names, except the current node *label* in progress. No errors and no stack traces.
- `Cache-Control: no-store`.
- Body caps (§3.4). Strict JSON (`extra="forbid"` pydantic models).
- **Rate-limit hook.** `core/rate_limit.py` defines `RateLimiter` with `check(bucket: str, key: str, cost: int = 1) -> None` (raises 429 `RATE_LIMITED` with `Retry-After`).
  - The default implementation is `NoopRateLimiter`. A Postgres token-bucket or gateway implementation plugs in later.
  - Buckets: `iface.submit:{project}:{slug}` keyed by client IP (`X-Forwarded-For` honoured only when `TRUSTED_PROXY` is set), `iface.poll`, `chat.send`, `task.decide`.
  - Calls are wired at every public route and at decide **now**, so adding limits later is configuration, not code.
- Run tokens: `core/public_tokens.issue_token`-style HMAC tokens, scoped `{kind: "run", execution_id, slug}`, TTL 30 days, stored hashed in `interface_submissions`.
- CORS: same-origin only. No credentials.
- Live runs use the `live` environment exactly like `public_apps._run_app_workflow`. Preview uses the draft in `default`.

### 7.3 Studio public pages (served by the SPA, not the engine)

- `/i/$project/$slug` → form or chat or custom start screen.
- `/i/$project/$slug/runs/$executionId?t=<run_token>` → progress + result. The token is in the fragment-free query so it is bookmarkable; it is cleared from history after the first read into sessionStorage, best effort.

---

## 8. Bank concerns (summary of the decisions above)

1. **Idempotency.**
   - Submit uses the required `Idempotency-Key` with a unique index.
   - Decide uses a conditional UPDATE plus `decide_key`.
   - Chat send uses `client_message_id`.
   - Task creation uses the unique `(execution_id, node_name, run_index)`.
   - Downstream side effects get `$execution.resumeKey`.
2. **Concurrency.** Two approvers: row-level conditional UPDATE, exactly one wins, and the loser sees who won. Timeout vs approve races are the same UPDATE. Multi-instance scheduler: `FOR UPDATE SKIP LOCKED`. Resume claim: conditional UPDATE on `resume_state`.
3. **At-most-once resume.** A crash mid-resume fails loudly (`RESUME_INTERRUPTED`) instead of re-running payments.
4. **Pagination.** Keyset for tasks and chat messages (`after=seq`). No OFFSET.
5. **Indexes** as in §3.2. The inbox query is index-only on `(project_id, status, created_at, id)`.
6. **Identity later without rework.**
   - Every place that takes `decided_by`/`user_label` goes through one function, `core/actor.py: current_actor(request) -> Actor{id: str|None, label: str, kind: "typed"|"user"|"system"}`. Stage 5 returns `kind="typed"` from the body.
   - Columns `decided_by`, `user_id` and `assignees` are text/JSON and hold principal ids later.
   - Assignee enforcement and `four_eyes` are one check in `TaskService.decide` (`authorize_decide(actor, task)` → today always allow).
   - `interface.access` gains `roles` later.
   - No endpoint shapes change.
7. **Validation.** Server-side against the contract (`shapes.validate`) for forms, and against the task form for decisions. The client-side validation is a convenience only.
8. **No secrets.**
   - Public responses never include parameters, expressions, credentials, node errors or `$vars`.
   - Task context is redacted at render.
   - The `/schema` response is built from the Shape only.
9. **Public vs internal.**
   - `/api/i/...` is anonymous and narrow.
   - `/api/tasks`, `/api/workflows/{id}/interface*` are internal (the studio is behind network controls today, and auth middleware later).
   - Public task approval is off by default.
10. **Payload limits** as in §3.4.
11. **Retention.** Waiting runs and open tasks are never pruned.

---

## 9. The minimal vertical slice: "Refund triage end to end"

### 9.1 Fixture `apps/workflow-engine/tests/fixtures/workflows/refund_triage_ui.json`

Workflow "Refund triage (UI)", seeded by `tests/e2e_stack.py seed` next to the existing one (same `__REFUND_API_URL__` replacement).

| Node | Type | Key params |
|---|---|---|
| Refund request | FormTrigger | `input_schema`: `order_id` string pattern `^A-\d{5}$`, `amount` number min 0.01 max 100000 format currency, `reason` enum [not_received, damaged, wrong_item, other], `email` string format email; all required |
| Classify | AIAgent | system "Classify refund requests…", `output_schema` `{category: string, summary: string, suggest: enum[approve, reject]}`. Fake LLM in tests: system first line `REFUND_CLASSIFY` |
| Over limit? | If | `{{ $json.amount > 500 }}`, reading `$('Refund request')` |
| Manager review | Approval | title `Refund {{ $('Refund request').item.json.order_id }} · ${{ $('Refund request').item.json.amount }}`, context `{Summary: Classify.summary, Suggest: Classify.suggest}`, form `[{name: "note", label: "Note to requester", type: "textarea"}]`, `due_in: "3h"`, `on_timeout: "timed_out"` |
| Issue refund | HttpRequest | POST `__REFUND_API_URL__/ok`, header `Idempotency-Key: {{ $execution.resumeKey }}` |
| Auto refund | HttpRequest | POST `__REFUND_API_URL__/ok` |
| Notify requester | Set | `{status: "rejected", message: "Your refund was not approved."}` (Teams is out of scope, so a Set stands in) |
| Show result | Respond | body `{status, refund_id, message}` |

- Connections: as the mockup. `timed_out` → Notify requester (with `message: "Review timed out"`, via a Set before it).
- Interface: `kind: custom`, `slug: refunds`, screens start/task:Manager review/result generated by `generate` + one fixture edit ("Flagged because").
- Tests (Stage 3): approved path, rejected path, auto path, timed-out path, each mocking Classify (`llm`), the HTTP nodes (`network`) and Manager review (`human`, port).
- The fake refund API gains `/refunds` recording `Idempotency-Key` (to assert at-most-once in the integration test).

### 9.2 The journey

1. Studio → Refund triage (UI) → Interface tab shows the Custom UI card, chips and the preview.
2. Publish.
3. Open `/i/{project}/refunds` → fill the form ($742) → submit → the run page shows "Waiting for Manager review".
4. Studio → Approvals inbox → the task with context (AI summary) → type name → **Approve refund**.
5. The run page (polling) flips to success and shows the `Show result` body. The fake API saw exactly one `/refunds` call with the resume key.
6. Restart variant: submit → stop the engine → start the engine → approve → success.

---

## 10. Test plan

### 10.1 Engine unit (`tests/unit/`, builders write with their code)

- `test_approval_node.py`:
  - suspend payload rendering
  - redaction of context
  - port emission on resume per decision
  - `on_timeout` mapping
  - mocked port in test mode
  - unmocked → `TEST_UNMOCKED_EFFECT`
  - pinned → approved
- `test_runner_suspend.py`:
  - suspend doesn't queue downstream
  - other branches finish
  - waiting outcome
  - errors + suspend → failed
  - two Approvals in parallel → two suspensions
- `test_form_fields.py`: Shape → FormField widget mapping table; unsupported types.
- `test_interface_validation.py`: slug regex, trigger_node kind (WF017), binding validation (WF018), size caps.
- `test_screen_spec.py`: schema validation, closed component and binding sets, chip computation.
- `test_interface_client_codegen.py`: deterministic output snapshot.
- `test_scorecard_approval.py`, `test_recipes_approval.py`, `test_mutations_approval.py`.

### 10.2 Integration over HTTP on real Postgres (`tests/integration/test_stage5_*.py`, tester agent)

Uses `scripts/test_worker.sh` isolation and the fake LLM.

- **Form:**
  - schema hides internals
  - submit validation: missing, wrong type, unknown field, pattern, over 64 KB, enum
  - Idempotency-Key replay returns the same execution
  - poll token mismatch → 404
  - unpublished → 404; `access=studio` → 404
  - `{{ $vars.X }}` in input stays literal
  - a secret in a node output is redacted in the result
- **Approval:**
  - approve → success with result
  - reject → rejected branch
  - timeout via a forced tick (test hook `POST /__test__/tasks/tick` behind the loopback guard, sets `due_at` in the past) → timed_out port
  - `on_timeout=approve`
- **Double click:** 2 concurrent decides (`asyncio.gather`) → one `decided`, one `already_decided`; exactly one downstream run; the fake API sees one `/refunds`.
- **Two approvers** with different decisions concurrently → one wins, the loser sees the winner.
- **Same Idempotency-Key** decide replay → `decided`.
- **Survives restart:** start the engine subprocess → submit → kill -9 → restart → task still open → approve → success.
- **Crash mid-resume:** a fault-injection hook stops after the claim → restart → `RESUME_INTERRUPTED`; no second `/refunds`.
- **Pending decided, never claimed** → resumed by the sweep after restart.
- **Retention:** create 101+ runs with one waiting → the waiting one survives `_cleanup`.
- **Cancel waiting** → tasks cancelled → decide returns `already_closed`.
- **Inbox:** keyset paging over 60 tasks with no dupes or gaps; filters; counts.
- **Chat:** session create, two turns, history in the run's ChatInput item (asserted via node_outputs), `CHAT_BUSY` 409 on a concurrent send, `client_message_id` dedupe, token scoping 404.
- **AI edit:** scripted fake reply → valid spec; invalid reply → repair → 422; LLM not configured → 503.
- **Client:** generate `client.ts` → write it next to a tiny consumer `.ts` → `npx tsc --noEmit` passes. A deliberately wrong consumer fails tsc (proves the types are real).
- **Promote:** app created, `workflow_ids` set, idempotent second call.
- **Stage 3 regression:** the refund_triage_ui test suite (4 tests) passes; scorecard shows the Approval branches covered; the checker is clean except the expected infos.

### 10.3 API journey test (`tests/integration/test_stage5_journey.py`)

The §9.2 journey purely over HTTP: seed → publish → submit → poll waiting → inbox → decide → poll success → assert the result body and a single refund.

### 10.4 Playwright `apps/workflow-studio/e2e/tests/18-interfaces.spec.ts` (one e2e agent; `flock /tmp/e2e.lock`)

1. Interface tab: cards visible, chips show `amount, order_id, reason, email` / `Manager review` / `Classify.summary` / `Show result`.
2. The live preview renders the task screen from the seeded run.
3. Switch kind to Form → preview form fields; switch back.
4. AI edit with a scripted fake LLM ("add the carrier tracking link") → the preview shows the link component.
5. Publish → open `/i/<p>/refunds` → field validation messages (bad order id) → submit → "Waiting for Manager review".
6. Approvals inbox: rail badge count, row, open the task → context shows the AI summary → type name → Approve → the button disables and the toast says "Approved".
7. Back on the run page: success + result.
8. Double-click Approve on a second task → one decision; the second click shows "Already approved by …".
9. Reject path → the rejected message.
10. Chat interface on a small chat fixture: two turns, history persists across reload.
11. Promote to app → navigates to the app builder with the app.
12. A test run in the Tests panel with the mocked approval passes.

No console errors (existing rule). Fonts blocked. Prefix-sweep cleanup.

### 10.5 Gates (unchanged rules)

- Engine suite green; baseline plus new tests.
- Studio unit green; `tsc -b` clean; lint ≤ 96; `design:check` clean.
- e2e 01–18 green (one agent).

---

## 11. Studio screens and data-testids (frozen)

### 11.1 Editor Interface panel

- `editorLayoutStore`: `RightPanelTab = 'nodes' | 'ai' | 'interface'`.
- The navbar shows an `Interface` center tab (mockup) which opens the right panel tab. Testid `navbar-tab-interface`, label `Interface (Custom|Form|Chat|Off)`.
- Panel component: `features/workflow-editor/components/interface/InterfacePanel.tsx`.

| testid | Element |
|---|---|
| `interface-panel` | Panel root |
| `interface-url` | `/i/{p}/{slug}` text + copy; shows "Publish to go live" when not live |
| `interface-kind-form` / `-chat` / `-custom` | The three cards (`aria-pressed`) |
| `interface-off` | "No interface" control |
| `interface-slug-input` | Slug editor |
| `interface-preview` | Preview frame |
| `interface-preview-screen-select` | start / task:<node> / result |
| `interface-preview-run` | "Live data · run #…" picker |
| `interface-wired` | Chips container; children `wired-chip-input`, `wired-chip-task`, `wired-chip-context`, `wired-chip-result` |
| `interface-ask-input`, `interface-ask-submit` | AI change bar |
| `interface-ask-summary` | AI summary/warnings |
| `interface-promote` | Promote to app |
| `interface-open-app` | After promote |
| `interface-warnings` | Checker issues for the interface |
| `interface-client-download` | Download `client.ts` |

### 11.2 Shared renderer: `src/features/interfaces/` (new feature folder)

- `renderer/ScreenRenderer.tsx`: renders a ScreenSpec. Each component root has `data-testid="screen-<type>"`, plus `data-bind`.
- `form/InterfaceForm.tsx`: `iface-form`, `iface-field-<name>`, `iface-field-error-<name>`, `iface-submit`.
- `run/RunStatusView.tsx`: `iface-run-status` (data-status), `iface-run-progress`, `iface-run-waiting`, `iface-run-result`, `iface-run-error`.
- `chat/InterfaceChat.tsx`, reusing `shared/components/chat/*`: `iface-chat`, `iface-chat-input`, `iface-chat-send`, `iface-chat-message` (data-role, data-seq), `iface-chat-busy`.
- `api/interfaceApi.ts`: public client + internal endpoints. Query keys in `shared/api/queryKeys.ts` under `interfaces.*`, `tasks.*`.

### 11.3 Public routes (no studio shell)

`app/routes/index.ts` adds the root-level `i/$project/$slug` and `i/$project/$slug/runs/$executionId`, outside `p/$project`. Testid `iface-page`.

### 11.4 Approvals inbox

- Workspace child route `p/$project/approvals` and `p/$project/approvals/$taskId`.
- Rail item `rail-approvals` with badge `rail-approvals-count` in `ProjectOutline.tsx`.

| testid | Element |
|---|---|
| `approvals-page` | Root |
| `approvals-filter-status` | open / closed / all (segmented) |
| `approvals-filter-workflow` | Select |
| `approvals-row` | data-task-id, data-status; cells title, workflow, due, assignees, created |
| `approvals-load-more` | Keyset next |
| `approvals-empty` | Empty state |
| `task-detail` | Detail root (renders the custom screen if one exists, else the default layout) |
| `task-context` | Context key/values |
| `task-form` | Approver form |
| `task-decided-by` | Name/email input (persisted to localStorage) |
| `task-comment` | Comment |
| `task-approve`, `task-reject` | Buttons (disabled while pending; one Idempotency-Key per page view) |
| `task-outcome` | Outcome banner: "Approved by X at T" / "Already approved by …" |
| `task-run-link` | Link to the run in Runs |

### 11.5 Canvas touches

- The Approval node shows a "N waiting" chip (`node-approval-waiting`) from `/api/tasks/counts`, and a "Custom UI · Review" chip when a task screen exists.
- The FormTrigger shows "Form · N fields".
- Node palette: FormTrigger under Triggers, Approval under Flow.
- Approval parameter panel: a standard schema-driven form. A form-fields editor reuses the Set-fields editor pattern.

---

## 12. Build waves

Rules (HANDOFF-STAGE3):
- At most 3 Sonnet builders at once, with disjoint files. Testers are separate agents who write from §7/§10 without reading the implementation first.
- Only one agent runs e2e. No git. Lead reviews each wave before the next.

### Wave 1: three builders in parallel

**Builder A: durable approvals (engine core).** Owns:
- `db/migrations/20261019120000_interfaces_tasks.sql` (whole file, all tables, so B doesn't touch the migration)
- `db/models.py` (all five new models)
- `engine/types.py` (Suspension, `NodeResult.suspend`)
- `engine/workflow_runner.py`
- `nodes/flow/approval.py` + node registry entry
- `engine/effects.py`
- `engine/task_resumer.py`, `engine/cron_scheduler.py` (`_tick_tasks`), `main.py` (startup sweep)
- `repositories/execution_repository.py` (complete→waiting, `metadata.result`, `_cleanup`, cancel), `repositories/task_repository.py`
- `services/task_service.py`, `services/execution_service.py` (`execution_id`/`skip_start`/`resume_decision` kwargs)
- `routes/tasks.py` (`/api/tasks*`), `routes/test_hooks.py` (tasks tick + fault hook)
- `services/scorecard.py`, `engine/test_recipes.py`, `services/mutations.py`
- `core/actor.py`
- unit tests in §10.1 for these.

**Builder B: interfaces (engine surfaces).** Owns:
- `nodes/triggers/form_trigger.py` + registry entry
- `engine/form_fields.py`, `engine/graph.py` (`TRIGGER_NODE_TYPES`)
- `nodes/ai/chat_input.py`
- `schemas/interface.py` (Interface, ScreenSpec, FormField pydantic + JSON schema)
- `repositories/workflow_repository.py` (`interface` key survives all dumps), `services/workflow_service.py` (publish → `interface_slugs`, slug clash)
- `services/interface_service.py`, `services/interface_ai.py`, `services/interface_client_codegen.py`, `services/interface_promote.py`
- `services/chat_service.py`
- `routes/interfaces.py` (internal), `routes/public_interfaces.py` (`/api/i`), `routes/__init__.py`
- `core/rate_limit.py`
- checker codes WF013–WF019 in the checker module
- `tests/fixtures/workflows/refund_triage_ui.json` + chat fixture, `tests/e2e_stack.py`, `tests/fake_refund_api.py` (`/refunds`)
- unit tests for these.
- Depends on A's frozen interfaces only:
  - `HumanTask` model fields (§3.2)
  - `TaskService.list/get/decide` signatures (§7.1)
  - `ExecutionService.run_saved_workflow(..., detached via asyncio.create_task)`
  - `metadata.result` written by A's `complete()`
  - A completion hook point `execution_repository.on_complete_hooks: list[Callable[[ExecutionContext, Execution], Awaitable[None]]]`. A creates the list; B registers the chat hook.

**Builder C: studio.** Owns:
- `src/features/interfaces/**` (new)
- `src/features/workflow-editor/components/interface/**` (new)
- `src/features/approvals/**` (new)
- `editorLayoutStore.ts`, `WorkflowEditorPage.tsx` (RightPanel tab only), `WorkflowNavbar.tsx`
- `app/routes/index.ts`, `app/routes/project.tsx`, `ProjectOutline.tsx`
- `shared/api/endpoints.ts`, `shared/api/queryKeys.ts`
- canvas node chips for Approval/FormTrigger
- studio unit tests (renderer, form-field mapping parity, keyset hook).
- Builds against §7 shapes with MSW-style fixtures in unit tests. It does not run e2e.

Frozen contracts handed to all three: §3.1, §3.2, §4.1–4.2 params/ports/output, §5.4 decide semantics, §6.1 RunView, §6.3 ScreenSpec v1 + binding roots, §7 all tables, §11 testids.

### Wave 2: integration and wiring (two builders, then the lead review)

- **A2 (engine):** wire B's public submit to A's detached run + `metadata.interface`. Chat waiting message. `$execution.resumeKey`. Streaming `execution_waiting`. Fix-ups from the Wave 1 review.
- **C2 (studio):** live preview on real data, AI edit bar, promote flow, inbox badge + node chips, Tests-panel mock editor for Approval ports (port select already exists for mocks: add the three port names).
- The lead runs the engine suite, studio unit, tsc, lint, design:check.

### Wave 3: testers (separate agents, write from the contract)

- **T1:** integration tests §10.2 (real Postgres, `test_worker.sh`, restart and fault tests with an engine subprocess).
- **T2:** API journey §10.3 + client `tsc` check.
- **T3 (the only e2e agent):** `18-interfaces.spec.ts` §10.4, then the full 01–18 run.

Builders fix what testers find (refute/confirm each claim with a failing test first). Then the lead does a final review and reports to the owner; no commit until "commit".

---

## 13. Decisions where we followed n8n

1. **Form Trigger: Test URL vs Production URL.** Preview runs the draft; live runs the published version only. Field values sit at the top level of the item.
2. **Wait / "On form submitted" resume semantics.** The paused node passes its input through and merges the response. The execution status is `waiting`. The execution resumes in place (same execution id), not as a new run.
3. **An execution runs the version it started on.** Republishing never changes in-flight waiting runs.
4. **Chat Trigger with session id.** Memory nodes key on `sessionId`. We expose `session_id` the same way.
5. **Respond node** produces the surface's response, rather than a new "Respond to form" node (n8n has "Respond to Webhook" + form completion; we keep one node).
6. **Pinned data** on a waiting node emits on the main/first port.
7. **Cancel stops a waiting execution.**

Where we did **not** follow n8n:
- n8n stores waiting runs and resumes by webhook with a "resume URL". Ours is a task row with idempotent decide, because a bank needs an inbox, double-click safety and timeouts, not a secret URL.
- Custom screens are declarative specs, not n8n-style free HTML in a Form node (n8n allows custom HTML/CSS; that is an XSS surface we refuse on public bank pages).

---

## 14. Risks and open questions

| # | Risk | Mitigation / owner decision needed |
|---|---|---|
| R1 | **At-most-once is not exactly-once.** A crash mid-resume needs a human to retry. | Explicit `RESUME_INTERRUPTED` + `$execution.resumeKey` for idempotent downstream APIs. Exactly-once is Stage 4. **Owner to accept.** |
| R2 | The runner change (suspend) touches the hottest file; the Stage 3 test-mode paths must not regress. | Mocks are checked before suspend; the full engine suite is a gate; dedicated unit tests. |
| R3 | Resuming via partial-run re-hydration depends on `node_outputs` being complete for the execution (it is pruned by retention for old runs). | Retention excludes waiting runs; `node_outputs` cascade is checked in T1. |
| R4 | A draft (preview) run waiting while the author edits the draft. | The snapshot embeds the draft definition at suspend; resume uses the snapshot, not the live draft. |
| R5 | **No identity:** anyone in the studio can approve any task; `decided_by` is self-asserted. | Public task approval off by default; `current_actor`/`authorize_decide` seams; clearly labelled in the UI ("typed name, not verified"). **Owner to accept for Stage 5.** |
| R6 | Declarative screens narrow §7's "app of kind workflow_interface". | Promote-to-app gives the full app path. **Owner to confirm the narrowing.** |
| R7 | LLM credits are a known blocker; the AI edit is unusable live without them. | Generation is deterministic; AI is edit-only; fake LLM in all tests; 503 is shown cleanly. |
| R8 | Polling cost at scale. | PK reads, backoff, `no-store`. The SSE upgrade path keeps the same RunView. Rate-limit hook. |
| R9 | One task per arrival, not per item. Loops over many items with approvals create many tasks. | WF014 warning; `run_index` in the unique key handles loops. |
| R10 | Parallel approvals in one run: the status rules (waiting while any task is open) and merge outputs on partial resume are subtle. | Covered in unit + integration tests. If wave review finds it fragile, Stage 5 may restrict to one open task per execution (checker error) as a fallback. |
| R11 | The `interface` key gets dropped by a dump path (has happened before with `contract`/`tests`). | B owns all dump paths; a round-trip test via PUT → GET → publish → version GET. |
| R12 | Bank proxies may strip the `Idempotency-Key` header. | Also accept `idempotency_key` in the body; header wins. |

## 15. Lead decisions (2026-10-18, owner asleep; owner may overturn)

- **R1 accepted:** at-most-once resume with loud `RESUME_INTERRUPTED`. Paying a refund twice is worse than a manual retry. Exactly-once waits for Stage 4.
- **R5 accepted:** the owner's standing rule is "features only: no login, roles or audit". The UI labels the typed name as "not verified".
- **R6 accepted:** declarative screen specs, not generated code, on public pages. Promote-to-app keeps the full path.
- **R10:** build parallel approvals as designed. If the Wave 1 review finds it fragile, fall back to one open task per execution (a checker error).
- This document is now **frozen** as the Stage 5 contract (§3, §4, §5, §6, §7, §11).
