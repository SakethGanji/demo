# Workflow Engine Plan

> Horizontally-scaled in-process execution with Postgres + Redis.
> Everything is I/O-bound — asyncio handles concurrency, no distributed workers needed.

---

## Table of Contents

1. [Strategy](#1-strategy)
2. [Architecture](#2-architecture)
3. [Database Schema](#3-database-schema)
4. [Migration](#4-migration)
5. [Runner Changes](#5-runner-changes)
6. [Versioning Flow](#6-versioning-flow)
7. [SSE Streaming](#7-sse-streaming)
8. [Active Triggers](#8-active-triggers)
9. [Webhook Handling](#9-webhook-handling)
10. [Credential Encryption](#10-credential-encryption)
11. [Error Handling & Retries](#11-error-handling)
12. [Retry From Failure](#12-retry-from-failure)
13. [Production Hardening](#13-production-hardening)
14. [Multi-Instance Scaling](#14-multi-instance-scaling)
15. [Long-Running Workflows (Wait Pattern)](#15-wait-pattern)
16. [Phase 2: Redis](#16-redis)
17. [File-by-File Changes](#17-file-changes)
18. [Implementation Order](#18-implementation-order)
19. [Production Recommendations](#19-production-recommendations)

---

## 1. Strategy

### Why in-process + horizontal scaling

All workflow nodes are I/O-bound (HTTP calls, DB queries, LLM API calls). A single Python process with asyncio handles thousands of concurrent I/O operations. No GIL problem. No need for distributed task queues.

**Horizontal scaling** = multiple identical app instances behind a load balancer, each running workflows in-process with asyncio. Simple ops, no queue infrastructure, no task serialization.

**Phase 1** — Postgres persistence, versioning, triggers, credentials, production hardening.
**Phase 2** — Redis for cross-instance coordination (SSE streaming, rate limiting, caching).

### Why not distributed workers

Distributed task queues (Celery-style) solve CPU-bound parallelism. For I/O-bound work:
- asyncio within a single process already gives massive concurrency
- Per-node hop overhead in distributed mode (~12-15ms) dwarfs fast node execution (<1ms)
- 200-node sequential chain: ~2.5s pure overhead vs ~20ms in-process
- Adds ~900 lines of infrastructure code + failure modes (race conditions, zombie tasks, convergence races)

**When you'd actually need distributed workers:**
- CPU-heavy nodes (video processing, ML inference) — we don't have these
- Strict process isolation (one workflow OOMing can't take down others) — unlikely with I/O work
- 10,000+ concurrent workflow executions — not a near-term concern

If that day comes, add a targeted task queue — don't pre-build it.

---

## 2. Architecture

```
                    Load Balancer
                (consistent hash on workflow_id)
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
         Instance 1   Instance 2   Instance 3
         ┌──────────────────────┐
         │  FastAPI + Uvicorn   │
         │                      │
         │  REST API            │
         │  SSE (in-process)    │  ← asyncio.Queue callback
         │  WorkflowRunner      │  ← BFS runner, unchanged
         │  Webhook intake      │
         │  Cron scheduler      │
         └──────────┬───────────┘
                    │
            ┌───────┴───────┐
            ▼               ▼
      ┌───────────┐   ┌───────────┐
      │ Postgres   │   │ Redis     │  ← Phase 2
      └───────────┘   └───────────┘
```

**How it works:** Each instance is identical. The instance that receives a trigger (webhook, cron, manual) runs the workflow in-process via asyncio. Postgres is the shared persistence layer. Redis (Phase 2) enables cross-instance SSE streaming and rate limiting.

**Multi-instance coordination:**
- Sticky routing (consistent hash on workflow_id) ensures SSE client and runner are on the same instance
- `FOR UPDATE SKIP LOCKED` on cron triggers prevents duplicate firing across instances
- Redis pub/sub (Phase 2) removes the need for sticky routing

---

## 3. Database Schema

Full schema lives in `src/db/migrations/20260307120000_baseline.sql`. The schema has 16 tables covering:

- **Identity & access:** users, teams, team_members
- **Organization:** folders, tags, workflow_tags
- **Workflows:** workflows, workflow_versions
- **Execution:** executions, node_outputs
- **Triggers:** active_triggers (generic — handles webhook, cron, interval, polling, kafka, mqtt, etc.)
- **Credentials:** credentials, shared_credentials
- **Variables:** variables
- **Data tables:** data_tables, data_table_rows

### Key tables

#### workflows

```sql
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL DEFAULT 'default',
    folder_id TEXT REFERENCES folders(id),
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    draft_definition JSON,          -- working copy in editor
    published_version_id INTEGER,   -- points to immutable version for execution
    settings JSON,
    ...
);
```

**`draft_definition`**: Where the user's in-progress work lives. The editor reads/writes this column. Executions never use it — they always use `workflow_versions.definition`.

#### active_triggers (generic)

```sql
CREATE TABLE active_triggers (
    id SERIAL PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    workflow_version_id INTEGER,
    team_id TEXT NOT NULL DEFAULT 'default',
    node_name TEXT NOT NULL,
    type TEXT NOT NULL,              -- webhook, cron, interval, polling, kafka, mqtt, ...
    config JSON NOT NULL DEFAULT '{}',  -- type-specific settings
    state JSON NOT NULL DEFAULT '{}',   -- runtime state (poll cursors, offsets)
    next_run_at TIMESTAMP,
    last_run_at TIMESTAMP,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

The trigger table is fully generic — `config` JSON holds type-specific settings, `state` JSON holds runtime state. Supports all trigger types without schema changes:

| Type | config | state |
|---|---|---|
| webhook | `{path, method, response_mode}` | — |
| cron | `{expression}` | — |
| interval | `{seconds}` | — |
| polling | `{url, interval_seconds, auth}` | `{last_cursor, etag}` |
| kafka | `{brokers, topic, group_id}` | `{offsets}` |
| mqtt | `{broker, topic, qos}` | `{last_message_id}` |

#### node_outputs

Append-only. Written during execution, one row per node completion.

```sql
CREATE TABLE node_outputs (
    id SERIAL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    output JSON NOT NULL,
    metrics JSON,
    status TEXT NOT NULL,            -- success, error, no_output
    error TEXT,
    run_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE(execution_id, node_name, run_index)
);
```

**Two roles:**
1. Durable output storage — survives process crash, viewable in execution history
2. Expression resolution for retry-from-failure — `$node["X"]` reads from here

---

## 4. Migration

### Custom SQL migration runner

Using a custom migration runner (`src/db/migrate.py`) instead of Alembic. Features:
- Advisory locks for safe concurrent migration
- Checksum verification (detects tampered migrations)
- Transactional execution (each migration is atomic)
- Commands: apply, status, rollback, new, dry-run

### Dependencies

```
# requirements.txt
asyncpg
psycopg2-binary    # for migration runner (sync)
croniter
cryptography
```

### NGC Secret Integration

Database passwords fetched via NGC SecretAgent (`src/core/secrets.py`):
- `resolve_database_url(settings)` — priority: explicit URL > individual components with NGC > None
- Cached `ngc getSecret` with 3 retries
- Falls back to env var password if NGC unavailable

---

## 5. Runner Changes

The `WorkflowRunner` is the only execution engine. Two additions:

### 5.1 Write node_outputs during execution

```python
# In WorkflowRunner._process_job(), after storing result in context:
await self._persist_node_output(
    execution_id=context.execution_id,
    node_name=job.node_name,
    output=main_output,
    metrics=node_metrics,
    status="success",
    run_index=job.run_index,
)
```

### 5.2 Pass db_session factory + create/complete execution records

```python
class WorkflowRunner:
    def __init__(self, db_session_factory=None):
        self._db_session = db_session_factory  # None = skip persistence

    async def run(self, ..., pre_populated_states: dict | None = None,
                  version_id: int | None = None):
        context = self._create_context(workflow, mode)
        if pre_populated_states:
            context.node_states = pre_populated_states
        # ... existing BFS loop (unchanged) ...
        return context
```

**Total runner changes: ~120 lines added. Zero existing lines modified.**

---

## 6. Versioning Flow

### Save (draft)

```python
async def save_workflow(db, workflow_id: str, definition: dict):
    """User clicks save in editor. Updates draft only."""
    await db.execute(
        "UPDATE workflows SET draft_definition = :def, updated_at = now() WHERE id = :id",
        {"def": json.dumps(definition), "id": workflow_id})
```

### Activate / Publish

```python
async def activate_workflow(db, workflow_id: str):
    """User clicks activate. Freezes current draft as a new version."""
    # 1. Get current draft
    # 2. INSERT INTO workflow_versions (next version number)
    # 3. UPDATE workflows SET published_version_id, active = true
    # 4. sync_triggers()
```

### Execute

Always executes the published version, never the draft.

**UI/API impact: None.** The workflow JSON format is identical.

---

## 7. SSE Streaming

In-process asyncio.Queue. No external pub/sub needed for single-instance.

```python
@router.post("/execution-stream/{workflow_id}")
async def stream_execution(workflow_id: str, ...):
    queue = asyncio.Queue()
    def on_event(event): queue.put_nowait(event)

    async def event_generator():
        task = asyncio.create_task(
            run_workflow(db, runner, workflow_id, ..., on_event=on_event))
        running_executions[exec_id] = task
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield json.dumps(serialize_event(event))
                if event.type in (ExecutionEventType.EXECUTION_COMPLETE,
                                  ExecutionEventType.EXECUTION_ERROR):
                    return
        finally:
            running_executions.pop(exec_id, None)
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_generator())
```

**Multi-instance limitation:** SSE client must connect to the same instance running the workflow. Solved by sticky routing (Phase 1) or Redis pub/sub (Phase 2).

---

## 8. Active Triggers

### Sync on Activate/Deactivate

```python
async def sync_triggers(db, workflow_id, version_id):
    # Delete old triggers for this workflow
    # Parse definition, create new triggers per type:
    #   Webhook → config: {path, method, response_mode}
    #   Cron → config: {expression}, next_run_at computed
    #   Interval → config: {seconds}, next_run_at = now()
```

### Cron Scheduler

Runs in each instance. Uses `FOR UPDATE SKIP LOCKED` so multiple instances don't fire the same cron.

```python
class CronScheduler:
    async def _tick(self):
        due = await trigger_repo.get_due_triggers()
        for trigger in due:
            asyncio.create_task(self._run_cron(trigger.id, trigger.workflow_id))
            await trigger_repo.update_after_run(trigger.id)
```

### Circuit Breaking

Triggers track `error_count`. After N consecutive failures, trigger is auto-disabled:
```python
async def record_error(self, trigger_id, error, max_errors=5):
    trigger.error_count += 1
    if trigger.error_count >= max_errors:
        trigger.enabled = False
```

---

## 9. Webhook Handling

```python
@router.post("/webhook/{path:path}")
async def handle_webhook(path: str, request: Request):
    trigger = await trigger_repo.find_webhook_trigger(f"/{path}", request.method)
    if not trigger:
        raise HTTPException(404, "No active webhook")

    webhook_data = [NodeData(json={
        "headers": dict(request.headers),
        "body": await request.json(),
        "method": request.method,
        "path": path,
    })]

    response_mode = trigger.config.get("response_mode", "onReceived")
    if response_mode == "onReceived":
        exec_id = await start_and_run_background(trigger, webhook_data)
        return {"status": "success", "executionId": exec_id}
    elif response_mode == "onComplete":
        context = await run_workflow_sync(trigger, webhook_data)
        return context.webhook_response or last_node_output(context)
```

---

## 10. Credential Encryption

```python
from cryptography.fernet import Fernet

ENCRYPTION_KEY = settings.encryption_key  # env: WORKFLOW_ENCRYPTION_KEY

def encrypt_credential(data: dict) -> str:
    return Fernet(ENCRYPTION_KEY).encrypt(json.dumps(data).encode()).decode()

def decrypt_credential(encrypted: str) -> dict:
    return json.loads(Fernet(ENCRYPTION_KEY).decrypt(encrypted.encode()))
```

Nodes reference credentials by ID. Decrypted before node execution.

---

## 11. Error Handling

**No changes to the runner's retry logic.** It already handles:
- Configurable retries per node (`retry_on_fail`, `retry_delay`)
- `continue_on_fail` (propagate error data downstream)
- `WorkflowStopSignal` (graceful stop)

Errors persisted to `node_outputs` with `status='error'`.

---

## 12. Retry From Failure

```python
async def retry_from_failure(db, runner, failed_exec_id: str) -> str:
    # 1. Load old execution + version
    # 2. Load successful node_outputs → pre_populated_states
    # 3. Find failed node
    # 4. Create new execution linked to old (retry_of_execution_id)
    # 5. Copy successful outputs to new execution
    # 6. Rebuild input for failed node from parent outputs
    # 7. Run from failed node with pre_populated_states
```

---

## 13. Production Hardening

### 13.1 Cancellation

```python
running_executions: dict[str, asyncio.Task] = {}

@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    task = running_executions.get(execution_id)
    if task and not task.done():
        task.cancel()
    # Update DB: status = 'cancelled'
```

### 13.2 Graceful Shutdown

```python
@asynccontextmanager
async def lifespan(app):
    yield
    tasks = list(running_executions.values())
    if tasks:
        logger.info(f"Draining {len(tasks)} running workflows...")
        done, pending = await asyncio.wait(tasks, timeout=30)
        for t in pending:
            t.cancel()
```

### 13.3 Stale Execution Reaper

```python
class StaleExecutionReaper:
    async def reap(self, db, threshold_hours: int = 4):
        # Mark stale 'running' executions as 'failed'
```

Threshold is 4 hours (not 1) because AI agent workflows can legitimately run for hours.

### 13.4 Retention Cleanup

```python
class RetentionCleaner:
    async def clean(self, db, retention_days: int = 30):
        # Delete old completed executions (CASCADE handles node_outputs)
```

### 13.5 CPU-Bound Node Protection

Nodes that do heavy computation (Code, Filter with complex expressions) run in a thread pool:

```python
result = await asyncio.get_event_loop().run_in_executor(
    thread_pool, run_user_code, code, input_data)
```

---

## 14. Multi-Instance Scaling

### Load Balancer Config

```nginx
upstream workflow_engine {
    hash $workflow_id consistent;
    server instance1:8000;
    server instance2:8000;
    server instance3:8000;
}
```

Sticky routing ensures SSE client and runner are co-located. Phase 2 (Redis pub/sub) removes this requirement.

### Instance failure

1. Load balancer detects health check failure (~10s)
2. Rehashes: affected workflows route to a new instance
3. Running executions on dead instance are lost
4. Stale reaper marks them `failed`
5. User can retry from failure using persisted `node_outputs`

### Instance addition

1. Consistent hashing redistributes ~1/N of workflows
2. Some SSE connections break (client reconnects)
3. No data migration — Postgres is shared

---

## 15. Long-Running Workflows (Wait Pattern)

For workflows that wait for external events (human approval, webhook callback mid-flow). Checkpoints to DB, resumes later.

```python
class WaitNode(BaseNode):
    async def execute(self, context, node_def, input_data):
        resume_token = generate_token()
        await save_waiting_execution(
            execution_id=context.execution_id,
            node_name=node_def.name,
            node_states=context.node_states,
            resume_token=resume_token,
        )
        raise WorkflowStopSignal(message="Waiting for external event")
```

Resume via `POST /executions/{execution_id}/resume`.

Schema (add when needed):
```sql
CREATE TABLE waiting_executions (
    execution_id TEXT PRIMARY KEY REFERENCES executions(id),
    resume_node TEXT NOT NULL,
    resume_token TEXT NOT NULL,
    node_states JSON NOT NULL,
    waiting_since TIMESTAMP DEFAULT now()
);
```

---

## 16. Phase 2: Redis

Add Redis when you need cross-instance coordination. No rush — single instance works fine to start.

### What Redis solves

| Use Case | Without Redis | With Redis |
|---|---|---|
| SSE streaming | Sticky routing required | Any instance can stream any execution |
| Rate limiting | Per-instance only | Global rate limits across instances |
| Caching | Per-instance LRU | Shared cache (credentials, definitions) |
| Live UI updates | Polling or sticky routing | Pub/sub push to any connected client |

### Setup

```
pip install redis[hiredis]
```

```python
# config.py
redis_url: str = "redis://localhost:6379/0"
```

### Execution event bus

```python
# On workflow event (node complete, execution done, etc.):
await redis.publish(f"exec:{execution_id}", json.dumps(event))

# SSE handler (any instance):
async with redis.subscribe(f"exec:{execution_id}") as channel:
    async for message in channel:
        yield message
```

This removes the need for sticky routing — SSE client on Instance A can stream events from a workflow running on Instance B.

### Rate limiting

Token bucket per credential/API key:

```python
async def check_rate_limit(key: str, limit: int, window: int = 60) -> bool:
    count = await redis.incr(f"rl:{key}")
    if count == 1:
        await redis.expire(f"rl:{key}", window)
    return count <= limit
```

### What NOT to use Redis for

- **Task queues** — not needed, asyncio handles I/O concurrency
- **Distributed locking** — Postgres `FOR UPDATE SKIP LOCKED` handles cron dedup
- **Session storage** — only if you add SSO and need cross-instance sessions

---

## 17. File-by-File Changes

### Phase 1: New Files

| File | Purpose |
|---|---|
| `src/db/migrations/20260307120000_baseline.sql` | Full schema (16 tables) |
| `src/db/migrate.py` | Custom SQL migration runner |
| `src/core/secrets.py` | NGC secret fetching + DB URL resolution |
| `src/repositories/version_repository.py` | Version CRUD |
| `src/repositories/node_output_repository.py` | Node output write/read |
| `src/repositories/trigger_repository.py` | Trigger sync + lookup + circuit breaking |
| `src/repositories/credential_repository.py` | Credential CRUD + encrypt/decrypt |
| `src/engine/cron_scheduler.py` | Cron tick with SKIP LOCKED |

### Phase 1: Modified Files

| File | Change |
|---|---|
| `src/db/session.py` | SQLite → Postgres via `resolve_database_url()` |
| `src/db/models.py` | SQLModel classes for all tables |
| `src/core/config.py` | Postgres settings, NGC settings, encryption_key |
| `src/main.py` | Cron scheduler + reaper in lifespan |
| `src/engine/workflow_runner.py` | `_persist_node_output()`, `pre_populated_states` |
| `src/services/workflow_service.py` | Versioning flow, activation |
| `src/services/webhook_service.py` | Lookup via active_triggers |
| `src/routes/streaming.py` | Track running_executions |
| `src/routes/workflows.py` | Version endpoints, draft/publish |
| `src/routes/executions.py` | Retry, cancellation endpoints |

### Phase 2: New/Modified Files (Redis)

| File | Purpose |
|---|---|
| `src/core/redis.py` | Redis connection pool + helpers |
| `src/engine/event_bus.py` | Redis pub/sub for execution events |
| `src/middleware/rate_limit.py` | Redis-based rate limiting |
| `src/routes/streaming.py` | SSE via Redis subscribe (removes sticky routing need) |

### Never Modified

| File | Why |
|---|---|
| `src/engine/expression_engine.py` | Takes `node_states` dict — source is irrelevant |
| `src/engine/node_registry.py` | Stateless singleton |
| `src/engine/types.py` | Unchanged |
| `src/nodes/**/*.py` | All 60+ nodes unchanged |
| `src/schemas/**` | Unchanged |

---

## 18. Implementation Order

### Phase 1: Core (~5-7 days)

**Database (2-3 days)**
1. Docker compose with Postgres 16
2. Baseline schema migration
3. Custom migration runner
4. NGC secret integration
5. Update session.py, models.py, config.py

**Versioning + Persistence (2-3 days)**
6. Version repository
7. Workflow service — draft/publish/activate
8. Workflow runner — `_persist_node_output()`, `pre_populated_states`
9. Execution repository — read from node_outputs
10. Routes — draft save, activate, version list

**Triggers (1-2 days)**
11. Trigger repository — sync on activate/deactivate
12. Webhook service — lookup via active_triggers
13. Cron scheduler — SKIP LOCKED ticker
14. Wire into main.py lifespan

### Phase 1: Credentials & Hardening (~3-4 days)

**Credentials (1 day)**
15. Credential encrypt/decrypt
16. Credential repository + API endpoints
17. Wire into runner

**Production Hardening (1-2 days)**
18. Cancellation (running_executions dict)
19. Graceful shutdown
20. Stale execution reaper
21. Retention cleanup
22. Health check endpoint

**Retry From Failure (1 day)**
23. `retry_from_failure()` function
24. `POST /executions/{id}/retry` endpoint

### Phase 2: Redis (when needed, ~2-3 days)

25. Redis connection pool
26. Execution event bus (pub/sub)
27. SSE via Redis subscribe
28. Rate limiting middleware
29. Remove sticky routing requirement

### Priority

| Priority | Item | Value |
|---|---|---|
| **P0** | Database + schema | Foundation for everything |
| **P0** | Versioning + persistence | Crash recovery, execution history |
| **P0** | Structured logging (see 19.1) | Debuggability |
| **P0** | Health check (see 19.3) | Required for container deployment |
| **P1** | Triggers | Webhooks + crons |
| **P1** | Credentials | Needed for integrations |
| **P1** | Production hardening | Cancellation + graceful shutdown |
| **P2** | Retry from failure | Nice-to-have |
| **P2** | Redis | When you go multi-instance |

---

## 19. Production Recommendations

### 19.1 Structured Logging & Request Tracing

Add from day 1. When a 30-node execution fails, you need `execution_id` on every log line.

```python
import contextvars

execution_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("execution_id", default="-")

class ExecutionContextFilter(logging.Filter):
    def filter(self, record):
        record.execution_id = execution_id_var.get("-")
        return True

FORMAT = "%(asctime)s %(levelname)s [exec:%(execution_id)s] %(name)s — %(message)s"
```

### 19.2 Workflow Definition Validation on Save

Bad definitions should fail fast on save, not mid-execution.

```python
def validate_workflow_definition(definition: dict) -> list[str]:
    errors = []
    # 1. All connection endpoints reference existing nodes
    # 2. Exactly one trigger node
    # 3. No orphan nodes (excluding StickyNote)
    return errors
```

Call on draft save (warn) and on activate (block).

### 19.3 Health Check Endpoint

```python
@router.get("/health")
async def health_check(db=Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "db": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
```

### 19.4 Webhook Rate Limiting

Public-facing webhook endpoints need rate limiting. In-memory per-instance rate limiting works for Phase 1. Redis-based for Phase 2 multi-instance.

### 19.5 Loop Concurrency Limits

SplitInBatches with 10,000 items should respect `max_concurrency` to prevent resource exhaustion.

---

## Appendix: Docker Compose

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: command-studio
      POSTGRES_USER: workflow
      POSTGRES_PASSWORD: workflow
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  app:
    build: .
    environment:
      WORKFLOW_DATABASE_URL: postgresql+asyncpg://workflow:workflow@postgres:5432/command-studio
      WORKFLOW_ENCRYPTION_KEY: "${WORKFLOW_ENCRYPTION_KEY}"
    ports:
      - "8000:8000"
    depends_on:
      - postgres
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 3

volumes:
  pgdata:
```
