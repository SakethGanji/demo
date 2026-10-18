# Plan — Environments: explore in Python, keep what works as a workflow

Written 2026-10-14 against commit `5684ba5` (branch `revert-to-5684ba5`).
Starts fresh: nothing is ported from older branches.

## The idea in one paragraph
Every project gets an isolated **environment** (a sandbox container) with a
generated `studio` Python package in which every workflow node is a function
(`studio.http.request(...)`, `studio.pg.query(...)`). A human or an agent writes
ordinary Python there. **Credentials never enter the sandbox.** Every `studio.*`
call goes to an engine-side **broker**, which runs the real node with the real
credentials and logs the call. That log is drawn on the canvas as a graph. When
a run does what you wanted, you **save it as a workflow** (trigger → Python
step), which the existing engine schedules and reruns with no model involved.

## Assumptions (change these and the plan changes)
- **Personal / single-user first.** The engine still trusts `X-User-Id`. Multi-user
  auth and a hosted sandbox provider are deferred, but kept possible through
  `SandboxBackend`.
- **Local Docker is the sandbox.** Docker is currently dead on this machine; fixing it
  is step 0.
- **Runs are stateless.** Each run executes the whole script in a fresh process, and
  only `/workspace` files persist. No Jupyter kernel. This matches "the script is the
  workflow".
- **Parameter names stay as stored** (camelCase, e.g. `toEmail`), so the SDK needs no
  mapping table that can drift.

## Architecture
```
 studio (React)                engine (FastAPI, host)                      sandbox (container)
 ┌───────────────┐  exec     ┌──────────────────────────┐   docker exec   ┌──────────────────┐
 │ /env/$id page │──────────▶│ routes/environments.py   │────────────────▶│ runner.py        │
 │ editor+output │◀── SSE ───│ sandbox/ (backend)       │                 │ user/agent code  │
 │ trace canvas  │           │                          │  POST /broker   │ import studio ───┼─┐
 │ agent chat    │           │ broker/ ◀────────────────┼─────────────────┼──────────────────┘ │
 └───────────────┘           │  token check → run ONE   │  (only route    internal network,   │
                             │  node via runner path →  │   out of the    no internet, no     │
                             │  creds + $vars + redact  │   sandbox)      host mounts, no env │
                             │  → env_calls row         │                 secrets             │
                             └──────────────────────────┘◀──────── gateway container ─────────┘
```

---

## Step 0 — Prerequisites (½–1 day)
- Fix Docker (`sudo systemctl reset-failed docker && sudo systemctl start docker`), then
  `docker compose up postgres mongo` in `apps/workflow-engine`.
- Make one existing workflow succeed end to end, as a baseline (the Vars Redaction Demo
  with `scripts/dummy_token_api.py` + a `DEMO_API_TOKEN` secret var).
- Test harness: add a `tests/conftest.py` with a disposable Postgres schema per run.
  Today there are 28 tests, all in one file.

## Phase 0 — Environment + broker + SDK + call log (the core; no LLM)

### Engine — new files
| File | What |
|---|---|
| `src/db/migrations/2026101x_environments.sql` | `environments` (id, team_id, name, image, status, container_id, settings jsonb, timestamps) · `env_runs` (id, environment_id, kind `manual\|agent\|workflow_step`, execution_id null, code, status, stdout, stderr, result jsonb, started/ended) · `env_calls` (id, run_id, seq, node_type, params jsonb **redacted**, output jsonb **truncated**, source_call_ids, caller_line, status, error, duration_ms) |
| `src/sandbox/backend.py` | `SandboxBackend` protocol: `create(env) / exec(env, code, token, timeout) / stop(env) / reap_idle()`. This is the seam for a hosted provider (E2B/Modal) later. |
| `src/sandbox/docker_backend.py` | Long-lived container per environment: non-root user, `--read-only` rootfs + tmpfs `/tmp`, volume `/workspace`, `--cap-drop ALL`, `--security-opt no-new-privileges`, pids/mem/cpu limits, `--runtime runsc` (gVisor) if present. Runs on an `internal: true` network whose only peer is a gateway that forwards to the broker port. **No env vars from the engine, no host mounts.** |
| `sandbox/Dockerfile`, `sandbox/runner.py` | Image: python-slim + pandas/numpy. `runner.py` reads code from stdin, runs it unrestricted (the container is the boundary), and returns stdout/stderr plus a JSON-serialisable `result` (the final expression, or a `result = ...` variable). |
| `src/broker/token.py` | HMAC token scoped to `(env_id, run_id, exp)`, valid only for the life of the run. New setting `WORKFLOW_BROKER_SECRET`. |
| `src/broker/service.py` | `call(token, node_type, params, items) → items`: checks the token, checks the **node allowlist**, builds a `NodeDefinition`, runs it through the runner's single-node path, redacts secrets with the existing `redact_secrets`, writes an `env_calls` row, and emits an event. |
| `src/routes/broker.py` | `POST /api/broker/call`, reachable from the sandbox network. |
| `src/routes/environments.py` | CRUD · `POST /{id}/start\|stop` · `POST /{id}/runs` {code} → mints token, execs, returns run + calls · `GET /runs/{id}` · `GET /runs/{id}/events` (SSE) |
| `src/sdk/generate.py` | From `node_registry` (allowlisted types only), writes the `studio` package source: one module per node group, one function per type, keyword args from `NodeProperty` (options → `Literal`, required-without-default → required), docstrings from descriptions, and `studio.help()` / `studio.list_nodes()`. Copied into the container when it starts. |
| `src/sdk/runtime.py` (ships in the package) | `_call()` posts to the broker. Wraps returned items so that passing them into a later call sends `source_call_ids` (real data-flow edges for Phase 1). Records `caller_line` from the stack frame. |

### Engine — changed files
- `src/engine/workflow_runner.py`: pull a public `run_single_node(node_def, items, context)`
  out of `_process_job`. It covers parameter resolution, `credentialId` resolution and
  `$vars` substitution. The broker and the normal runner both use it, so there is one
  code path, not two.
- `src/engine/node_registry.py`: add a per-type flag `sdk_callable`. **Allowed:**
  integrations (HttpRequest, Postgres, MongoDB, Neo4j, SendEmail, ObjectStore), LlmChat,
  and data helpers. **Refused:** triggers, flow control (If/Switch/Loop/Merge/Wait;
  Python does that itself), Code, AiAgent, ExecuteWorkflow.
- `src/main.py`: register the routers; on startup and shutdown reap idle containers
  (same pattern as `stale_reaper.py`).
- `requirements.txt`: `docker` (Python SDK).

### Studio
- New route `/env/$envId` (`src/features/environments/`): a CodeMirror Python editor
  (add `@codemirror/lang-python`), Run, stdout/stderr/result panes, and a call list
  (node type · status · duration · expand to see params/output).
- `/projects`: a "New environment" entry.

### Tests (written before or with the code)
- **SDK sync:** the generated functions match the allowlisted registry types exactly,
  and each signature matches the `NodeProperty` list.
- **Broker:** wrong-run token → 403 · expired → 403 · refused node type → 400 with the
  allowed list · a secret `$var` value never appears in the returned output or the
  stored `env_calls`.
- **Sandbox escape checks** (marked `docker`, run against a real container): cannot read
  the engine `.env` · environment has no `WORKFLOW_*` vars · cannot reach the internet ·
  cannot reach Postgres :5433 or Mongo :27017 · can reach the broker · a fork bomb or
  memory blow-up is contained · timeout kills the run.
- **Runner refactor:** existing workflows produce identical outputs before and after
  `run_single_node` is extracted.

**Done when:** you write a script in `/env/$id` that calls `studio.http.request` against the
mock API, using a secret `$var`, plus `studio.pg.query`, and see the result and the call
list; and every escape check is green.
**Stop if:** writing Python against `studio` isn't nicer than the canvas for you.

## Phase 1 — The call log on the canvas (no LLM)
- Engine: `GET /api/env-runs/{id}/trace` → `{nodes, edges}`. Each call becomes a node of its
  own node type, so the existing icons and node visuals are reused. Edges come from
  `source_call_ids` (data flow), falling back to sequence order.
- Studio: put the React Flow canvas in a `readOnly` mode and embed it in `/env/$id`. Nodes
  appear live over SSE as calls land. Clicking a node opens its input/output (reuse the
  NDV output viewer) and highlights `caller_line` in the editor; the reverse works on
  line hover.

**Done when:** a 5-call script draws a correct graph with data edges, live.
**Stop if:** the trace doesn't actually help you debug.

## Phase 2 — Agent in the environment (needs an LLM key)
- Engine: `src/services/env_agent_service.py` runs a loop on the existing `llm_provider.call_llm`.
  Tools: `run_python(code)` (= a manual run, `kind=agent`), `read_file` / `write_file`
  in `/workspace`, `sdk_help(topic)`, `submit_script(code, summary)` to finish. Limits on
  steps, tokens and wall-clock. The system prompt carries only a compact SDK index; details
  come from `sdk_help`.
- Migration: `env_agent_sessions` (id, env_id, prompt, model, status, usage, messages jsonb).
  A session that ends with zero successful runs is `failed`, never `success`.
- Studio: a chat panel on `/env/$id` (reuse `createChatStore` and shared chat components).
  Each `run_python` shows its code and output and links to its trace. The submitted script
  lands in the editor for review.
- Eval: 10 of *your* real tasks, scored by hand: success, number of attempts, tokens.
  Keep it in `scripts/env_agent_eval.py`.
- The existing JSON-generating AI chat panel stays as it is.

**Done when:** the agent finishes most of the 10 tasks and its final script reruns unchanged.
**Stop if:** it can't reliably finish your real tasks.

## Phase 3 — Save a run as a workflow (no LLM at run time)
- New node `src/nodes/data/python_step.py` (`PythonStep`). Parameters: `environmentId`,
  `code`. Input items are available as `items`; the return value is the output. It
  executes through `SandboxBackend` in a **fresh container** from the environment's image
  with `/workspace` mounted read-only, using a broker token scoped to the `execution_id`.
  Registering it makes it appear in the node picker automatically.
- `POST /api/env-runs/{id}/save-as-workflow` {name, trigger: manual|cron|webhook} → creates
  a workflow `Trigger → PythonStep(code snapshot)`. The scheduler, executions, retry,
  versions/publish and `$vars` environments are all reused; there is no new runtime.
- Each execution's `env_calls` link to its `execution_id`. The execution view shows the
  nested trace from Phase 1.
- Fix the existing `Code` node, which runs `exec` inside the engine process and is unsafe:
  route it through `SandboxBackend`, or deprecate it in favour of `PythonStep`.

**Done when:** an agent-written script, saved with a cron trigger, runs on schedule with no
model and shows success plus its trace in the executions list.
**Stop if:** you never rerun what you saved.

---

## Out of scope (until a phase proves it's needed)
Splitting scripts into several steps · persistent kernels · multi-user auth/RBAC · hosted
sandbox providers · approvals for write actions · sharing environments · the analytics
service · porting anything from older branches.

## Open decisions
1. Personal tool or product? If product: auth and a hosted `SandboxBackend` move ahead of Phase 2.
2. camelCase parameter names in Python: keep them (no drift) or add snake_case aliases?
3. Should Phase 3's fresh-container-per-execution be relaxed for fast, frequent cron jobs?
