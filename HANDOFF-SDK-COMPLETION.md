# SDK Completion — Brief for the next session

> **STATUS 2026-08-19 (later session): DONE.** All four verification-bar items pass (suite 145
> green). §1: subprocess isolation via `src/engine/workflow_sdk_sandbox.py` (JSON-over-stdio,
> timeout-killed, env-scrubbed; in-process core kept for tests). §2: `build_workflow` agent tool via
> the resolver's new `"sdk"` source (`src/services/workflow_sdk_tool_service.py`) with
> `signature_reference()` spliced into the agent's system prompt; e2e proven against real Postgres
> in `tests/services/test_workflow_sdk_agent_tool.py`. §3: the four run-service bugs fixed
> test-first (`tests/services/test_agent_run_service.py`). §4: `SDK_TYPES` derived from the registry
> (add-a-node drill in `tests/engine/test_workflow_sdk_completion.py`). Eval: 7/12 → **10/12** after
> constructor-misuse/import teaching errors; results in `scripts/sdk_eval_results.json`.

**Written 2026-08-19.** Goal in one sentence: make the workflow SDK *complete* — a running agent can
actually reach it, the sandbox is a real boundary, and **adding a node to the registry exposes it in the
SDK, the prompt reference, and validation with zero SDK edits** — so nodes and SDK surface grow together.

Read first: `HANDOFF-AGENT-SDK.md` (current SDK state, corrected 2026-08-19), `SDK-DESIGN.md` (the
generator spec this finishes), `HANDOFF-AGENT-PLATFORM.md` (the agent runtime this wires into).

## Where things stand (verified 2026-08-19)

- `apps/workflow-engine/src/engine/workflow_sdk.py`: 27/28 node types, `>>`/port wiring, `validate()`
  with cycle detection, `test_run()` with **degrade-to-upper-bound semantics** (undecidable branches →
  `maybe_reached` + `limitations`, `writes_staged` is an upper bound, `writes_staged_definite` the floor —
  never understate, never refuse), `Node()` escape hatch, `ExecutionResult.partial`. 96/96 tests green
  (`venv/bin/python -m pytest tests/engine/`, needs `workflow-engine-postgres-1` up — see port-conflict
  note at the bottom).
- The registry equality test exists (`test_workflow_sdk_all_types.py:63`) — `SDK_TYPES + EXCLUDED_TYPES`
  is pinned to `node_registry.list()`, so drift already fails a test. Completion means going one better:
  **derive** instead of pin (below).

## The work, in order

### 1. Isolation decision FIRST (it shapes everything after)
The exec sandbox is in-process; the builtins whitelist is a lint, not a boundary (attribute-chain escapes
work). Decide subprocess-per-execution vs staying in-process-for-synthetic-only **before** wiring the
resolver, because isolation changes the API: `call_tool()` becomes RPC, latency appears, and the wire
format between agent runtime and SDK gets frozen by step 2. Recommendation: subprocess with JSON
script-in / ExecutionResult-out over stdio; keep `execute_workflow_script` as the in-process core the
subprocess wraps, so tests stay fast.

### 2. Wire `agent_tool_resolver`
Expose the SDK as a tool a running agent can call (same resolver pattern the tool ladder already uses —
see HANDOFF-AGENT-PLATFORM.md). The tool surface is deliberately tiny: one tool that takes a script,
returns the ExecutionResult (error/partial/test_run report), plus `signature_reference()` injected into
the agent's prompt. End-to-end proof: an agent run (harness-driven; real LLM creds still absent per the
platform handoff) that submits a script and gets a persisted workflow back.

### 3. Fix the agent-run backend bugs standing between steps 2 and "actually works"
Four bugs in `agent_run_service.py` / `agent_repository.py` were flagged by the 2026-08-18 review but
never fixed, and the full list was NOT transcribed — only the worst is named: a **closed-session-used-in-
closure** bug that breaks every non-builtin tool call in a real agent run. Re-audit those two files
(fresh eyes, fail-first tests) rather than hunting for the old transcript.

### 4. Derive, don't pin — the "add a node once" guarantee
`SDK_TYPES` is a hand-maintained list; the equality test makes forgetting it a failure, but deriving it
makes forgetting it impossible: `SDK_TYPES = [t for t in node_registry.list() if t not in EXCLUDED_TYPES]`
after `register_all_nodes()`. Same for `signature_reference()` (derive grouping/order from each type's
registry `group` instead of the curated list order). Keep the exclusion test (`AIAgent` stays out; the
exclusion being explicit and tested is the point). After this, a new node = write the class + register it,
and constructor, validation, prompt reference, and tests (`@parametrize` over the derived list) all follow.
New *SDK namespace functions* have one home too: `build_sdk_namespace()` — document that as the single
extension point, and add a test pinning the namespace's non-constructor surface so additions are deliberate.

### 5. Optional, explicitly deferrable
- `.pyi` stub generation (SDK-DESIGN §3): only serves humans; the equality/derivation work above is what
  actually prevents drift. Do last, or not at all.
- Loop ports in `test_run()`: both walked as definite today (documented in HANDOFF-AGENT-SDK §4.7); the
  `maybe_reached` mechanism is the home for a stricter treatment if wanted.
- Re-run the SDK-vs-ladder eval via Claude subagents and PERSIST results this time —
  `scripts/sdk_eval_harness.py` exists; the current `sdk_eval_results.json` is quota-death noise
  (1 pass / 11 429s) and should be replaced, not believed.

## Verification bar for "complete"
1. Engine suite green including new resolver/subprocess tests.
2. A scripted end-to-end: agent-run harness → SDK tool → workflow persisted in Postgres → fetched back.
3. The add-a-node drill, performed for real: register a throwaway node type in a test, assert it appears
   in constructors + `signature_reference()` + `describe()` with zero `workflow_sdk.py` edits.
4. No hand-maintained type list left in `workflow_sdk.py`.

## Environment gotcha (will bite you in the first ten minutes)
`analytics-pg` and `workflow-engine-postgres-1` both bind host 5432 — only one runs at a time. Engine
work: `docker stop analytics-pg && docker start workflow-engine-postgres-1`. If a container comes up
with no ports after a daemon restart, `docker network connect bridge <name>` heals it. Long-term fix
(worth doing as step 0 if it keeps biting): move the engine DB to 5433 via `WORKFLOW_DB_PORT`.
