# Stage 3B contracts (frozen)

Base: Stage 3A (`decd9e4`), `CONTRACTS-STAGE3A.md`. Everything there still holds.
Owner instruction (2026-10-17): "make sure it's tested with real gemini calls".
So there are two tiers:
- **Default suites** (engine pytest, studio unit, e2e) use the fake LLM only. They are deterministic and never need a key.
- **Live Gemini tier** (`tests/live/test_test_ai_gemini.py`, opt-in) makes real Gemini calls through the same code path. The lead runs it, and also runs one browser demo against real Gemini.
- The key in `apps/workflow-engine/.env.local` is never printed, logged, copied into files, or put in test output.

## 1. Model adapter (`src/engine/test_ai.py`, owner A)

```
TEST_AI_MODEL = env WORKFLOW_TEST_AI_MODEL or WORKFLOW_MODULE_GEN_MODEL or "gemini-3.5-flash"
async def ask_json(task: Literal["draft_tests","variations"], system: str, user: str, *, model=None) -> dict
```
- Calls `llm_provider.call_llm` (so the fake LLM hooks in automatically), with `temperature=0.2` and `response_format={"type": "json_object"}`.
- It parses the first JSON object in the reply, tolerating ```json fences. If the reply isn't JSON, it raises `TestAIError(code="bad_json")`.
- If the model isn't configured (`llm_provider.is_configured`) and the fake isn't active, it raises `TestAIError(code="not_configured")`.
- Every system prompt starts with a fixed first line, so fake-LLM scripts can match with `system_matches`:
  - `TEST_AI:DRAFT_TESTS`
  - `TEST_AI:VARIATIONS`
- **Route errors:**
  - `not_configured` → 503 `{"detail": {"code": "ai_not_configured", "message": ...}}`;
  - `bad_json` or a provider failure → 502 `{"detail": {"code": "ai_failed", "message": ...}}`.

## 2. AI drafts tests for grid gaps (owner A)

```
POST /api/workflows/{id}/tests/ai-draft
  body {"definition"?: WorkflowDefinition, "cells"?: [{"node": str, "category": Category}], "max"?: int (1..20, default 8)}
  -> {"drafts": [AIDraft], "rejected": [{"reason": str, "test"?: any}], "cells": [{"node","category"}], "model": str}
AIDraft = TestCase & {"ai_run": {"status": "passed"|"failed"|"error", "failures": [Failure]}}
```
- **Cells.** If `cells` is missing, the cells are the scorecard `gaps` of the definition (grid cells with status `none`), computed with a run of the current tests (`persist=False`). At most `max` cells are used.
- **Prompt (data, not prose).** It includes:
  - the nodes, connections and parameters of the definition;
  - `shapes_by_node` from `check_workflow`;
  - the node catalog entries for the node types present;
  - `test_defaults.mocks`;
  - existing tests as `{id, name, category, target, covers}` only;
  - the target cells;
  - the TestCase / Mock / Assertion grammar from CONTRACTS-STAGE3A §1, §2 and §4.

  The model must answer `{"tests": [TestCase-like]}`.
- **Server-side sanitising.** Never trust the model. For each returned test:
  - Force `id` to a new `t_` + 8 hex that the workflow doesn't already have. This applies even if the model sent an id that exists: the AI can never overwrite a test.
  - Force `status: "draft"`, `locked: false` and `source: "ai"`, and drop `approved_by`/`approved_at`.
  - Validate with `normalize_tests`. The model's draft is rejected (it goes into `rejected` with a reason) when:
    - a node name in its target, mocks or expect doesn't exist;
    - it uses an unknown assertion op;
    - its category isn't valid.
  - **One repair round:** the invalid ones go back to the model with the reasons, once.
- **Dry run.** Every accepted draft is run once in test mode (`persist=False`), and the result goes into `ai_run`.
  - Drafts whose `ai_run.status` is `error` with an `unmocked` or `setup` failure are still returned, so the user sees why.
  - Order: `passed` first, then `failed`, then `error`.
- **Nothing is saved.** The studio inserts drafts into `tests` (they're drafts, so the lock rules allow it). Approve uses the existing 3A route. Reject means removing the draft.
- **Never weaken.** The route never edits or removes an existing test. A unit test proves this: a model reply that reuses a locked test's id, sets `locked`/`approved`, or tries to edit an existing test still leaves the stored tests byte-identical, and it yields only new drafts.

## 3. Variations / fuzzing (`src/services/fuzz.py`, owner B; AI half owner A)

```
POST /api/workflows/{id}/tests/fuzz
  body {"definition"?, "target"?: {"kind":"workflow"} | {"kind":"node","node"}, "count"?: int (1..200, default 60),
        "seed"?: int (default 1), "ai"?: bool (default false)}
  -> {"runs": int, "seed": int, "findings": [Finding], "inputs_tried": int, "ai_inputs": int}
Finding = {"id": str, "kind": "node_error"|"contract"|"crash", "node": str|null, "field": str|null,
           "input": {"items": [json]}, "error": {"message": str, "code"?: str, "path"?: str},
           "count": int, "test": TestCase}
```
- **Base input.** For a workflow target, it's the first test's `input.items[0]` (or the trigger's sample/declared shape). For a node target, it's that node's input shape.
- **Generators** (deterministic for a given seed):
  1. **Edge values** per field from `data_categories.values_for(shape)`, covering boundary, missing, wrong_type and unusual. One field changes at a time.
  2. **Missing fields:** each optional field is removed, one at a time.
  3. **Allowed but odd values:** `0`, negative, `""`, very long strings (300 chars), unicode, `null` where nullable, empty list or object.
  4. **Seeded random:** combinations of 2–3 field changes, up to `count`.
  5. **AI inputs** (`ai: true`): `test_ai.propose_variations(definition, shapes, base_items, n=10) -> [items]`, owner A, with system line `TEST_AI:VARIATIONS`. These are added to the pool, and `ai_inputs` counts them.
- **Running.** Each input runs once in test mode with `test_defaults.mocks`, `persist=False`, and frozen now/seed. Runs are capped at `count` and at 20 s total.
- **Findings:**
  - `node_error`: a node ended with status `error`;
  - `contract`: a contract failure;
  - `crash`: the run raised.

  Findings are deduplicated by `(kind, node, error message with numbers stripped)`. `count` is how many inputs hit it, and the smallest input is kept.
- `field` is the input field that was changed, when one was.
- **The finding's `test`** is a ready draft that the studio adds on "Save as test":
  - `category`: missing, wrong_type, boundary or unusual, by generator;
  - `source`: `"fuzz"`, or `"ai"` for AI inputs;
  - `expect`: `[{"node": <node>, "op": "status", "value": "success"}]`, which fails until the bug is fixed;
  - `status: "draft"`.
- **Schema change (owner B):** `TestCase.source` also allows `"fuzz"` (in `schemas/workflow.py`; owner D adds it to the studio types).
- The route never saves anything.

## 4. Drift alerts and stale tests (owner C)

- **Migration** `src/db/migrations/20261018120000_drift_smoke.sql` creates tables `api_drift`, `smoke_configs` and `smoke_runs`. Models go in `db/models.py`.
- **`api_drift`** columns:
  - `id` (`drift_` + hex), `project_id`, `workflow_id`, `node_name`;
  - `api_ref` jsonb null, `basis` (`spec` | `contract` | `mock`);
  - `status_code` int null;
  - `diffs` jsonb `[{path, kind: "missing"|"type"|"extra"|"status", expected, actual}]`;
  - `fingerprint`, `first_seen`, `last_seen`, `count`, `last_execution_id`;
  - `status` (`open` | `resolved`), `resolved_by`, `resolved_at`.

  It's unique on `(workflow_id, node_name, fingerprint)`.
- **Detection** (`services/drift.py`: `async def check_execution(session, execution_id) -> list[dict]`):
  - **When it runs:** after every non-test execution completes (modes other than `test`). It's called from `ExecutionRepository.complete`, guarded so a drift failure is logged and never changes the run's result.
  - **What it checks:** each node with effect `network` or `database` that produced items. The expected shape comes from:
    1. the spec response for the received `statusCode` (HttpRequest with `api_ref`), basis `spec`;
    2. else the node's declared or approved contract, basis `contract`;
    3. else that node's mock in `test_defaults.mocks`, using the shape inferred from the mock (`http.body` or `items`), basis `mock`.
  - **Comparing** uses the existing shape validation (`engine/contracts.py` / `shapes.py`):
    - a status code not documented in the spec gives a `status` diff;
    - a required field that's missing or has the wrong type gives a diff;
    - extra fields are recorded as `extra` only for basis `mock`, because that means the mock is out of date.
  - **Storing:** an upsert by fingerprint (sha1 of sorted `path:kind`). A repeat bumps `count` and `last_seen`, and reopens a resolved record.
- **Stale tests** are derived and never written into the definition, so locked tests stay untouched. A test is stale when its mocks (own or default) cover a node with an open drift. The run report and the scorecard get `"stale_test_ids": [id]`.
- **API:**
  ```
  GET  /api/workflows/{id}/drift?status=open|all      -> {"alerts": [Drift], "stale_test_ids": [str]}
  POST /api/workflows/{id}/drift/{drift_id}/resolve   {"by": str} -> Drift
  GET  /api/projects/{project_id}/drift               -> {"alerts": [Drift]}   (open only)
  Drift = row as JSON + "title": "<node> reply changed" + "summary": "body.decision missing; body.score: expected number, got string"
  ```

## 5. Live smoke tests (owner C)

- **`smoke_configs`** columns:
  - `workflow_id` PK, `enabled` bool, `test_ids` jsonb;
  - `live_nodes` jsonb `[node]`: only these nodes call the real or sandbox system; every other outside step keeps its mock;
  - `schedule` (cron string, null), `before_release` bool;
  - `next_run_at`, `updated_at`.
- **`smoke_runs`** columns:
  - `id` (`smoke_` + hex), `workflow_id`, `trigger` (`manual` | `schedule` | `release`);
  - `status` (`passed` | `failed` | `error`), `started_at`, `finished_at`;
  - `results` jsonb `[{test_id, name, status, failures, live_calls: [{node, status_code, ms}]}]`.
- **Live run:** `TestRunOptions` gains `live_nodes: frozenset[str]`. A live node skips its mock and uses the real transport. Strict offline mode still applies to every other unmocked outside step.
- **Smoke assertions:** only the structural ops are enforced: `status`, `shape`, `exists`, `not_exists`, `type`, `count`. Contract checks are enforced too. Value ops (`eq`, `gt`, …) are evaluated but reported as `{"kind": "info"}` and never fail a smoke run, because live data changes.
- **Drift:** live replies also go through the drift detection from §4.
- **API:**
  ```
  GET  /api/workflows/{id}/smoke        -> SmokeConfig (defaults: enabled false, [] , [], null, false)
  PUT  /api/workflows/{id}/smoke        SmokeConfig -> SmokeConfig   (unknown test ids / node names -> 422)
  POST /api/workflows/{id}/smoke/run    -> SmokeRun   (409 when no test_ids or no live_nodes)
  GET  /api/workflows/{id}/smoke/runs?limit=20 -> [SmokeRun]  (newest first)
  ```
- **Schedule:** `CronScheduler._tick` also picks the due `smoke_configs` (enabled, schedule set, `next_run_at <= now`). It runs them with `trigger: "schedule"` and sets `next_run_at` with croniter.
- **Release:** `release_service.run_checks` adds `smoke_tests_pass` per workflow in the snapshot that has `enabled && before_release`. It runs a smoke run (`trigger: "release"`):
  - a failure gives `fail` (blocks);
  - an error reaching the system gives `fail`, with details.
- **Fake API for tests and the demo** (`tests/fake_refund_api.py`): add `POST /_shape {"version": "v1"|"v2"}`.
  - `v2` changes `/ok` to `{"ok": true, "result": {"decision": "review"}, "score": "high"}`, so `decision` moves and the reply shape changes.
  - `v1` is the default and keeps today's reply.

## 6. Studio (owner D, Wave 2)

- **AI fill gaps.**
  - The `tests-ai-fill` button is in the Tests toolbar and on the grid.
  - It calls `ai-draft` and inserts the drafts.
  - Draft rows from AI show `test-ai-badge`, plus `test-keep` (opens approve) and `test-reject` (removes the draft).
  - While it waits, it shows `tests-ai-busy`. A 503 shows a toast "AI isn't set up".
- **Variations.**
  - The `tests-fuzz` button opens the `fuzz-panel`, with `fuzz-count`, `fuzz-ai` (toggle) and `fuzz-run`.
  - The results list `fuzz-finding` rows with `data-kind` and `data-node`, each showing the node, field, input and error, plus `fuzz-save` ("Save as test").
- **Drift.**
  - The `drift-banner` appears in the editor when there are open alerts: "Risk API reply changed", with `drift-details` and `drift-resolve`.
  - Stale tests show `test-stale-badge`.
  - The workflows list shows `wf-drift-dot` on workflows with open drift.
- **Smoke.**
  - A `smoke-settings` section sits in the Tests tab, with `smoke-enabled`, `smoke-test-<id>` checkboxes, `smoke-live-<node>` checkboxes, `smoke-schedule`, `smoke-before-release`, `smoke-save` and `smoke-run`.
  - Each result is a `smoke-run-row` with `data-status`.
- Use `src/shared/components/ui/*` and the tokens only. `npm run design:check` must stay clean.

## 7. Tests

- **Engine unit and integration (default tier, fake LLM):**
  - adapter parsing;
  - sanitising and the never-weaken proof;
  - the repair round;
  - fuzz generators are deterministic per seed, and a bug is found;
  - drift detection, upsert and resolve;
  - stale ids;
  - smoke run with `live_nodes` against `fake_refund_api`;
  - the scheduler tick and the release check.
- **API journey (default tier):** Refund triage, covering AI draft (fake script), approve, fuzz finds the planted bug, save as test, a real run against the fake API in v1 then v2 (drift opens and tests turn stale), smoke run, the release blocked by smoke, and resolve.
- **Live Gemini tier** (`tests/live/test_test_ai_gemini.py`):
  - **Gating:** it's skipped unless `WORKFLOW_LIVE_GEMINI=1` and a Gemini key is configured. It's marked so the default run never collects real calls.
  - **Draft check:** it calls the real draft service on Refund triage and asserts that:
    - at least 1 draft is accepted;
    - every draft is `status: "draft"`, `locked: false`, `source: "ai"` and passes `normalize_tests`;
    - no existing test changed;
    - at least one draft covers a requested cell;
    - each has an `ai_run`.
  - **Variations check:** it calls `propose_variations` and asserts that it returns lists of JSON objects.
  - **Output:** it prints only counts and names, never the key or full prompts.
- **Playwright** `e2e/tests/17-ai-and-live.spec.ts` (fake LLM): AI fill gaps then keep/reject, the fuzz panel finds a bug then save, the drift banner after the fake API switches to v2 then resolve, and smoke settings then run then a result row.
