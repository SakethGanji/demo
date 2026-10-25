# TESTS

This guide lists every test in this repo. Its purpose is to help you port the suite to another copy of the codebase. In that copy the backend and the UI are separate repos, so this guide has two parts:

- **Part A, Backend:** `apps/workflow-engine`, `apps/jobs-service`, `apps/analytics-service`.
- **Part B, UI:** `apps/workflow-studio`.

Test counts are collected counts (pytest `--collect-only`, `vitest list`), so each parametrised case counts as one test. Runtimes come from the last full baseline run on this branch.

## Contents

1. Overview
2. Part A: Backend
   - A1 How to run
   - A2 Harnesses
   - A3 Environment variables
   - A4 Unit catalogue
   - A5 Integration catalogue
   - A6 Journeys, live, nodes, routes
   - A7 jobs-service
   - A8 analytics-service
3. Part B: UI
   - B1 How to run
   - B2 Unit harness and catalogue
   - B3 E2E harness
   - B4 E2E catalogue
4. Known failing at baseline
5. Porting advice

---

## 1. Overview

| Suite | Path | Files | Tests | Baseline time | Command |
|---|---|---|---|---|---|
| Engine unit | `apps/workflow-engine/tests/unit` | 185 | 4785 | 7.1 min | `venv/bin/python -m pytest tests/unit` |
| Engine integration (incl. journeys) | `apps/workflow-engine/tests/integration` | 108 | 1436 | 12.2 min | `venv/bin/python -m pytest tests/integration` |
| of which `integration/journeys` | `apps/workflow-engine/tests/integration/journeys` | 13 | 54 | 1.1 min | `venv/bin/python -m pytest tests/integration/journeys` |
| Engine journeys | `apps/workflow-engine/tests/journeys` | 2 | 2 | not measured | `venv/bin/python -m pytest tests/journeys` |
| Engine live (opt-in, billable) | `apps/workflow-engine/tests/live` | 2 (+ runner scripts) | 18 | skipped by default | `WORKFLOW_LIVE_GEMINI=1 venv/bin/python -m pytest tests/live` |
| Engine nodes | `apps/workflow-engine/tests/nodes` | 1 | 28 | seconds | `venv/bin/python -m pytest tests/nodes` |
| Engine routes | `apps/workflow-engine/tests/routes` | 1 | 6 | seconds | `venv/bin/python -m pytest tests/routes` |
| **Engine total** | `apps/workflow-engine/tests` | 299 collected | 6275 | about 21 min | `venv/bin/python -m pytest tests` |
| jobs-service | `apps/jobs-service/tests` | 2 | 8 | seconds | `npm test` |
| analytics-service | `apps/analytics-service/tests` | 0 here (sources absent) | about 2680 (from notes) | about 110 s (from notes) | see A8 |
| Studio unit | `apps/workflow-studio/src/**/*.test.ts` | 126 | 1222 listed | about 4.4 s | `npm run test:unit` |
| Studio e2e | `apps/workflow-studio/e2e/tests` + `e2e/capture.spec.ts` | 42 | about 246 `test(` sites; theme loops add more | 14-21 + 30-35 alone took 19.9 min | `npm run test:e2e` |

Commands run from the app folder (`apps/workflow-engine`, `apps/workflow-studio`, `apps/jobs-service`).

The baseline engine run took 1258 s: 5874 passed, 130 failed, 196 skipped, 4 xfailed.

---

# Part A: Backend

## A1. How to run

- Run everything from `apps/workflow-engine` with `venv/bin/python -m pytest ...`. There is no `pytest.ini`, `setup.cfg` or `pyproject` pytest section. `tests/` is a package (`tests/__init__.py`), so imports look like `from tests.integration.conftest import ...`.
- You need Postgres on `localhost:5433`, with user and password `workflow`, or override it with the `WORKFLOW_DB_*` variables. Docker is not used directly.
- Some tests need extra tools. They skip, or in a few cases fail, when the tool is missing:
  - `bwrap` (bubblewrap): sandboxed Python routes.
  - `esbuild`, `node`, `tsc`, and studio `node_modules`: app bundles and the kit.
  - Playwright Chromium: `test_app_builder_f3`, `test_notebook_browser`.
  - Node, to run the jobs-service: `test_notebook_browser`.
- Don't run two full integration runs at the same time on one machine. Each session creates its own DBs, but the engine processes and ports add up and the timing tests get flaky.
- Integration logs from engine subprocesses go to `tests/integration/.logs/`. This folder grows forever, so clear it now and then.

## A2. Harnesses

### `tests/unit/conftest.py`

| Fixture | What it does |
|---|---|
| `anyio_backend` | Returns `asyncio`. |
| `pg_dbname` (session) | Builds a `TestDatabase` from the integration conftest. That creates `workflows_test_<8hex>` and runs `python -m src.db.migrate apply`. If Postgres is unreachable, every test that uses it skips. 52 files depend on it. |
| `pg` | An async SQLAlchemy sessionmaker on asyncpg, with `search_path` set to `"workflow-app"`. It monkeypatches `src.db.session.async_session_factory` so service code uses the test DB. At teardown it cancels leftover `task_resumer._inflight` tasks. |

Tests that use `pg` or `pg_dbname` are marked **PG** in the catalogue. All others are pure: no DB, no network beyond loopback, no model.

Helper modules in `tests/unit` (not tests themselves):

- `_runner_fakes.py`, `_pg_world.py`, `_interface_fakes.py`, `_kg_support.py`
- `_stage6_b4_support.py`, `_stage8_b2_world.py`, `_stage8_b3_bank.py`, `wp_b_support.py`
- plan fixtures: `plan_fixture.py`, `builder_plan_fixture.py`, `expense_plan_fixture.py`, `office_plan_fixture.py`
- `oneshot_fixtures/office_bundle.json` and `oneshot_fixtures/office_plan.json`

### `tests/integration/conftest.py`

| Piece | What it does |
|---|---|
| `TestDatabase` | Creates `workflows_test_<8hex>`, or uses `TEST_DB_NAME`. It refuses `workflows`, `postgres`, `template0` and `template1`. It creates the `"workflow-app"` schema, migrates with a `src.db.migrate apply` subprocess, and drops the DB at the end unless `KEEP_TEST_DB=1`. |
| `EngineServer` | Runs `uvicorn src.main:app` as a subprocess on a free port and refuses `:8000`. Its env sets `SEED=0`, a fresh Fernet `WORKFLOW_ENCRYPTION_KEY`, `WORKFLOW_PUBLIC_TOKEN_SECRET`, `WORKFLOW_RELOAD=false`, `WORKFLOW_LOG_LEVEL=warning` and `WORKFLOW_TEST_MODE=1`. Logs go to `tests/integration/.logs`. |
| `_base_env` | Starts from the parent env and strips every LLM provider variable (`ANTHROPIC_*`, `OPENAI_*`, `GOOGLE_*`, `GEMINI_*`, `WORKFLOW_ANTHROPIC_*`, `WORKFLOW_OPENAI_*`, `WORKFLOW_GEMINI_*`, `COIN_TOKEN`, `LLM_PROVIDER`, `WORKFLOW_LLM_*`, `WORKFLOW_FAKE_LLM_*`). No integration test can reach a real model. |
| `fake_env` | Sets `WORKFLOW_LLM_PROVIDER=fake`, `WORKFLOW_TEST_MODE=1`, `WORKFLOW_BIND_HOST=127.0.0.1`, `WORKFLOW_FAKE_LLM_TOKEN`, `WORKFLOW_DISABLE_DOTENV=1`, `WORKFLOW_FAKE_LLM_SCRIPT=tests/fixtures/fake_llm/llm-script.json` and `WF_CHAT_BUILDER=legacy`. |
| `StudioClient` | An httpx client with the `/api` prefix and JSON content type. It records `hits` for the endpoint coverage test and offers `raw()` and `stream_events()` (SSE). |
| `_EchoHandler` / `upstream` | A local HTTP server that answers `/v1/balance`, `/b64`, `/redirect` and `/status/N`, and echoes every other path. |
| Session fixtures | `test_db`, `engine`, `api` (the normal engine); `fake_stack` (a second DB `workflows_test_fake_*` plus a fake-LLM engine started from an empty cwd, with `E2E_UPSTREAM_URL`); `fake_api`; `upstream`. |
| Helpers | `uniq`, `start_set_workflow`, `webhook_workflow`, `http_workflow`. |

Every integration file needs Postgres plus at least one engine subprocess. The catalogue marks only the extra needs.

Other integration support modules (none of these contain tests):

| File | Gives |
|---|---|
| `integration/test_stage5_t1_support.py` | A `Stack` with its own DB plus one or more engine subprocesses, `fresh_stack`, `restart()`, and `RefundApi`. It is collected but has 0 tests. It also stops `test_zz_endpoint_coverage` from ever running (see section 4). |
| `integration/notebook_support.py` | `nb_engine` and `nb_api`: an engine on the session DB with its own notebook storage dir. |
| `integration/nb_jobs_support.py` | `jobs_service`: the Node jobs-service on a free port with `JOBS_DEV=1` and `JOBS_LLM=off`. It skips when Node or Chromium is missing. |
| `integration/drift_smoke_support.py` | The fake risk API with v1/v2 shapes, for drift and smoke. |
| `integration/_stage8_b4_support.py`, `integration/_stage8_t1_support.py` | Stage 8 bank workflows and helpers. |
| `integration/journeys/_helpers.py` | `needs_bwrap`, `needs_esbuild`, `pending(phase)` (unused), journey builders. |

### `tests/journeys/conftest.py`

Re-imports the integration fixtures. `tests/journeys/ai_builder_journey.py` is a helper and is not collected.

### Fake LLM and cassettes

- The engine has a test-only fake LLM provider. It is active only with `WORKFLOW_LLM_PROVIDER=fake` and `WORKFLOW_TEST_MODE=1`, and it is protected by `WORKFLOW_FAKE_LLM_TOKEN`. It reads a JSON script of rules: a match on the system line or the prompt text, a reply, and a `rule_id`. Unit coverage is in `test_fake_llm.py`; the startup refusals are in `integration/test_ui_fake_llm.py`.
- Tests replace the script at runtime with `PUT /api/_test/fake-llm/script`, sending the `X-Test-Token` header. `GET /api/_test/health` reports `fake_llm` and the DB name.
- `tests/support/ai_cassette.py` records real calls when `WF_AI_RECORD_DIR` is set. `to_fake_script` turns a cassette into fake-LLM rules keyed by the system line: `WF_AI:SPEC`, `WF_AI:BUILD`, `WF_AI:REPAIR`, `WF_AI:NODE_*`, `WF_AI:CHAT_ROUTE` and `WF_AI:JSON_FIX`. The cassette folder `tests/fixtures/ai_builder/cassettes` does not exist in this copy, so nothing is replayed from real recordings.
- `tests/support/ai_eval.py` holds the eval binder (`run_case`) and the pure scorer (`score_case`, rubric H1-H7). The replay tier and the live eval both use it.

### Fixtures folder (`tests/fixtures`)

| Folder | Holds |
|---|---|
| `ai_builder/` | `apis/` (20 fake bank APIs), `eval/` (15 cases, 10 dev and 5 holdout), `examples/`, `approval_items.json`, `layout_golden.json`, `prompt_render_snapshot.json` |
| `fake_llm/` | `llm-script.json` (the default script) plus `ai_builder/`, `apps/`, `capability_graph/` and `modules/` scripts, 41 files in all |
| `checker_snapshots/` | 17 snapshots for `test_stage2_golden` |
| `n8n/` | 10 n8n imports, sub-workflow fixtures, `SOURCES.md` |
| `openapi/` | OpenAPI and Swagger specs for Stage 3A |
| `templates_broken/` | about 40 broken template bundles plus `_expected.json` (error codes) |
| `workflows/` | 9 workflow JSON files, including `refund_triage.json` |
| `knowledge/understand_cases.json` | Understand cases |
| `llm/anthropic_tools.json`, `llm/openai_tools.json` | recorded provider payloads for adapter contract tests |
| `interface_client/refunds.client.ts` | codegen snapshot |
| `shopdemo.py`, `shopdemo.sql` | external DB fixture for `test_external_connections` and `test_project_connections` |

### Snapshots

| Variable | Regenerates |
|---|---|
| `WF_PROMPT_SNAPSHOT_WRITE=1` | `fixtures/ai_builder/prompt_render_snapshot.json` (`test_prompts_optional_sections.py`) |
| `UPDATE_SNAPSHOTS=1` | `fixtures/checker_snapshots/*` (`test_stage2_golden.py`) and `fixtures/interface_client/refunds.client.ts` (`test_interface_client_codegen.py`) |
| `WF_AI_REPLAY_STRICT=1` | not a writer: makes `test_ai_builder_replay` fail on any unmatched fake-LLM call |

Regenerate a snapshot only after you have read the diff and agree with it.

### `tests/e2e_stack.py` and `tests/fake_refund_api.py`

These are used by the studio e2e global setup (Part B), not by pytest.

| Command | Does |
|---|---|
| `e2e_stack.py up` | Creates and migrates `workflows_test_e2e_<8hex>` and prints JSON with the DB name. |
| `e2e_stack.py seed --engine-url URL` | Starts `fake_refund_api.py`, creates the `refunds` table and the "Refund triage" workflow, and seeds one failed run (amount 900 hits `/drop`). It then points the draft at `/ok` and seeds interfaces from `refund_triage_ui.json` and `chat_support_ui.json`. |
| `e2e_stack.py nbseed --engine-url URL` | Creates `workflows_test_nbdata_<8hex>` holding `bank.wires`, and adds the connections `bank` (read-only) and `bank_rw` in `proj_default`. |
| `e2e_stack.py down --db --pid --nbdb` | Stops the fake API and drops both DBs. |

`fake_refund_api.py` routes:

- `/ok`, `/error` (500), `/drop` (drops the connection), `/health`
- `POST /refunds` (records the idempotency key), `GET /_refunds`, `/_refunds/reset`
- `POST /_shape`, which switches the response between v1 and v2 for drift tests

## A3. Environment variables (names only)

| Group | Names |
|---|---|
| DB | `WORKFLOW_DB_HOST`, `WORKFLOW_DB_PORT`, `WORKFLOW_DB_USER`, `WORKFLOW_DB_PASSWORD`, `WORKFLOW_DB_NAME`, `TEST_DB_NAME`, `KEEP_TEST_DB` |
| Test mode and fake LLM | `WORKFLOW_TEST_MODE`, `WORKFLOW_DISABLE_DOTENV`, `WORKFLOW_LLM_PROVIDER`, `WORKFLOW_FAKE_LLM_SCRIPT`, `WORKFLOW_FAKE_LLM_TOKEN`, `WORKFLOW_BIND_HOST`, `WORKFLOW_ENCRYPTION_KEY`, `WORKFLOW_PUBLIC_TOKEN_SECRET`, `E2E_UPSTREAM_URL` |
| Builder switches | `WF_CHAT_BUILDER`, `WORKFLOW_BUILDER_MODE`, `WORKFLOW_ONESHOT_MODEL`, `WORKFLOW_ONESHOT_PROFILE`, `WORKFLOW_ONESHOT_AUTOTEST`, `WORKFLOW_EDITABLE_RULES`, `WORKFLOW_BUILD_CONCURRENCY`, `WORKFLOW_APP_BUILDER_CLARIFY`, `WORKFLOW_MODULE_GEN_MODEL`, `WORKFLOW_RUN_RETENTION` |
| AI policy and eval | `WF_AI_ALLOWED_MODELS`, `WF_AI_ALLOWED_PROVIDERS`, `WF_AI_MAX_CALLS`, `WF_AI_MAX_PROMPT_CHARS`, `WF_AI_CALL_TIMEOUT_S`, `WF_AI_RECORD_DIR`, `WF_AI_EVAL_GRAPH`, `WF_AI_EVAL_RESULTS`, `WF_AI_EVAL_CASES`, `WF_AI_EVAL_REPEAT`, `WF_AI_EVAL_BAR`, `WF_AI_EVAL_NO_PII`, `WF_AI_STORE_PROMPTS`, `WF_AI_SEND_RUN_VALUES`, `WF_AI_REPLAY_STRICT`, `WF_AI_MODEL`, `WF_AI_ADAPTER`, `WF_AI_REGION`, `WF_AI_PRICE_IN_PER_MTOK`, `WF_AI_PRICE_OUT_PER_MTOK`, `WF_AI_EXAMPLES_DIR`, `WF_AI_BUILD_DEADLINE_S` |
| Snapshots | `WF_PROMPT_SNAPSHOT_WRITE`, `UPDATE_SNAPSHOTS` |
| Live tier | `WORKFLOW_LIVE_GEMINI`, `GEMINI_API_KEY`, `BANKING_EXAMPLE`, `BANKING_CASEWORK_EXAMPLE`, `MARKETPLACE_LLM_LOG`, `PG_BINDIR`, `CHROMIUM_PATH` |
| Other | `PLAYWRIGHT_BROWSERS_PATH`, `TRUSTED_PROXY` |
| jobs-service (from tests) | `JOBS_DEV`, `JOBS_LLM`, `JOBS_DATA_DIR`, `JOBS_PORT`, `JOBS_HOST`, `JOBS_SERVICE_URL` |
| Notebook | `WORKFLOW_NOTEBOOK_STORAGE_DIR` |

Never put real values in test code. The integration and e2e harnesses build their own env and refuse provider keys.

## A4. Engine unit catalogue (`apps/workflow-engine/tests/unit`)

Tags:

- **PG**: real Postgres through `pg` / `pg_dbname`
- **BW**: bubblewrap sandbox
- **ESB**: esbuild, node, tsc or studio `node_modules`
- **BR**: Playwright Chromium
- **SRV**: an in-process loopback server

Untagged files are pure. No unit test calls a live LLM.

### AI builder (staged spec/build/repair builder, `/ai/builder`, `/ai/proposals`)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_ai_builder_datasets.py` | 15 | PG | The builder loads only this project's datasets and the prompts carry a datasets section. |
| `test_ai_builder_prompts.py` | 167 | | Prompts as data: few-shot examples, prompt-injection resistance, prompt size limits. |
| `test_ai_default_mocks.py` | 52 | | Default mocks for every effectful node of an AI-built workflow. |
| `test_ai_grounding.py` | 97 | | The grounding context: catalog subset, API operations, compact shapes, expression help in sync with the evaluator. |
| `test_ai_policy.py` | 107 | | Model allow-list policy; no secrets or PII in prompts (canary checks). |
| `test_ai_repairs.py` | 99 | | Deterministic repairs FIX01-FIX12 as golden cases; the input is never mutated. |
| `test_ai_spec.py` | 120 | | Spec codes SPEC001-SPEC008, the virtual definition, rule tests, sanitising and approval. |
| `test_ai_spec_graph.py` | 8 | | SPEC010/SPEC011 graph issues from the capability graph understanding. |
| `test_wf_ai.py` | 107 | | `engine/wf_ai.ask`, the only path to a model: budgets, timeouts, the recorder and the cassette converter. |
| `test_prompts_optional_sections.py` | 5 | | Optional prompt sections change nothing when absent (sha256 snapshot). |
| `test_test_ai_unit.py` | 29 | | Stage 3B test-AI helpers: adapter parsing, sanitising, never weakening a test, repair, variations. |
| `test_eval_graph_arm.py` | 14 | | The eval's graph arm changes the spec call and the record, never the scorer. Tests `tests/support/ai_eval.py` only. |
| `test_stage6_approval_mocks.py` | 22 | | Decision-only Approval mock, AIAgent mock normalisation, SPEC009 minimum tests. |
| `test_stage6_b1_pure.py` | 29 | | Canonical hashing, test fingerprints, golden auto-layout, workflow diff. |
| `test_stage6_b1_toolkit.py` | 34 | | Toolkit ops `set_parameters`, `set_node_field`, `set_test_default_mock`, `set_interface`. |
| `test_stage6_b3_product_fixes.py` | 12 | | Code-node escape rewriting and the AIAgent output schema key. |
| `test_stage6_b4_loop.py` | 23 | PG | The builder loop in process with scripted fake LLM replies. |
| `test_stage6_b4_modify.py` | 11 | PG | Modify proposals, spec edits, revise, Keep / Undo / Discard. |
| `test_stage6_b4_node_assist.py` | 10 | PG | AI on a node: fix_error, write_code, write_expression, explain. |
| `test_stage6_b4_ops.py` | 29 | | Op parsing, aliases, the op allow-list and ordering. |
| `test_stage6_b5_chat.py` | 25 | PG | The editor AI chat on the legacy staged builder (sets `WF_CHAT_BUILDER=legacy`). |
| `test_stage6_b5_followups.py` | 13 | PG | Proposal `base_definition`; modify-mode spec validation without a trigger. |
| `test_stage6_eval_aliases.py` | 3 | | The eval binder maps hidden-test form inputs to the built form's field ids. |
| `test_reference_workflow_pick.py` | 2 | | The adapt reference picks the workflow whose key matches the part key. |

### Workflow one-shot builder (editor chat default)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_workflow_oneshot.py` | 22 | PG | One model call writes the workflow and its tests; an edit is one `changes.json`; questions get answers and save nothing. |

### App builder and one-shot app builder

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_oneshot_builder.py` | 12 | PG | Runtime brief, load and check of a real bundle in a test DB, the single fix pass, splitting a big plan. |
| `test_oneshot_compact.py` | 9 | | Compact profile for small models: per-feature calls under a token cap, minimal fix passes. |
| `test_oneshot_db_context.py` | 3 | | The prompt carries the project's external DB schema summary. |
| `test_oneshot_lean.py` | 13 | | Edit and fix calls send an app index and only the relevant files. |
| `test_oneshot_seed_links.py` | 4 | | Seed rows refer to parents by position; the loader maps positions to ids. |
| `test_app_autotest.py` | 11 | PG | Scenario tests of a one-shot app: runner, fix pass fed by failures, skip with no tests. |
| `test_app_builder_api_context.py` | 5 | | The app builder's API context and the `declare_endpoint` tool. |
| `test_app_builder_build_backend.py` | 10 | | `build_backend` runs the module generator on a planned module. |
| `test_app_builder_clarify.py` | 10 | ESB | A new app asks or records a brief before writing UI files (2 skips without esbuild). |
| `test_app_builder_f3.py` | 29 | ESB BR | Stall guard, vague first turn, invented literals, StepDiff, truncation retry, parallel screens (4 skips without tools). |
| `test_app_builder_plan_brief.py` | 7 | | With an approved plan the builder writes BRIEF.md from the plan and offers no clarify tool. |
| `test_app_builder_syntax.py` | 3 | ESB | Compiler errors stop a broken generated app from completing (module skip without esbuild). |
| `test_app_kit_catalog.py` | 7 | ESB | Kit catalog components render server-side; the preview UI-state hook restores after remount. |
| `test_app_kit_theme.py` | 9 | ESB | The kit uses only theme tokens and every token utility it uses is defined. |
| `test_app_scaffold.py` | 3 | ESB | The plan brief plus resource routes make a full app with no model call (uses tsc). |
| `test_app_bundle.py` | 15 | | App parts compile into a `wf-template/1` bundle with virtual siblings (capability graph app composer). |
| `test_app_plan.py` | 15 | | The application plan recipe (capability graph app composer). |
| `test_editability.py` | 10 | PG ESB | Visual-editing trial behind `WORKFLOW_EDITABLE_RULES`: the brief is unchanged with the flag off (2 skips). |
| `test_stale_base.py` | 3 | PG | A save or chat turn on a stale app version gets 409 `stale_base`. |
| `test_platform_index.py` | 7 | | Kit component props, SDK symbols, templates and tool schemas are indexed. |
| `test_platform_traps.py` | 5 | PG | Module save runs static checks; preview "view as" user; tolerant `api.op(` scans. |
| `test_platform_data_layer.py` | 6 | PG | `studio.db` rows are literal data; stacked route decorators reconcile idempotently. |

### Backend builder, build coordinator and gates

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_build_coordinator.py` | 17 | PG | The coordinator through the builds API in process, with builders stubbed. |
| `test_build_fast_path.py` | 6 | PG | The fast build path with the real scaffold, save path and gates; only the model is fake. |
| `test_build_live_stream.py` | 8 | PG | Live build stream `file`, `route` and preview events. |
| `test_builder_blind3_fixes.py` | 38 | | Builder defects from the third blind QA round, one failing case each. |
| `test_builder_fix_f1_plumbing.py` | 21 | PG | Stub adoption, declared tables, app availability, lookup tolerance, secret warnings. |
| `test_builder_fix_f2_gates.py` | 7 | | Jobs-contract gate, placeholder gate, plan-vs-delivery ledger. |
| `test_builder_fix_f2_partial.py` | 5 | | A build that delivers less than planned is continued, then reported partial. |
| `test_builder_fix_f2_selftest.py` | 3 | | Empty lists are valid on empty tables; failures name the step and values. |
| `test_builder_fix_f3_contract.py` | 10 | | Contract checker variable scope and nested arrays (no false positives). |
| `test_builder_fix_f3_delivery.py` | 12 | | Missing planned screens or routes, calls to absent routes, route attachment. |
| `test_builder_fix_f3_resolve.py` | 3 | | Follow-up chats see the app's files; a new empty app is not an error. |
| `test_builder_fix_f3_truncation.py` | 8 | | Tool results are never cut silently; the continue offset works. |
| `test_builder_gates.py` | 5 | PG BW | Gate service on saved artifacts (skips without a sandbox). |
| `test_builder_gates_project.py` | 23 | PG BW | Project-level gate across several modules on scratch tables. |
| `test_builder_wp2.py` | 50 | | `patch_module`, lean prompt, gap questions, scratch self-test, static checks, templates. |
| `test_module_generator.py` | 16 | | Pure parts of the module generator. |
| `test_module_generator_clarify.py` | 10 | | Spec-first step: ask about gaps, build once answered. |
| `test_module_generator_plan.py` | 11 | | With an approved plan the generator builds from it and asks nothing. |
| `test_module_schedules.py` | 19 | PG | `@schedule` in modules: parse, reconcile on save and delete, the due tick. |
| `test_scaffold.py` | 6 | | Plan scaffold, patch-scope helpers, inline SDK reference. |
| `test_sdk_resource.py` | 8 | | `studio.resource` CRUD and state transitions from the plan columns. |
| `test_sdk_jobs_generation.py` | 7 | | `studio.jobs` is generated, callable and taught in the prompt. |
| `test_helpers_dup.py` | 7 | | The duplicate-helper detector (renamed locals still match). |
| `test_code_map.py` | 15 | | Non-executing source projection and resolution boundaries. |
| `test_code_module_save_schema.py` | 10 | PG | Module save migrates added columns and refuses destructive ones. |
| `test_smoke_preflight.py` | 30 | PG | Static preflight (hosts, secrets, egress, Jobs ids) and the opt-in smoke run. |

### Plan

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_project_plan.py` | 35 | | Plan normalisation, PLAN001-PLAN014, plan diffs, consumer helpers. |
| `test_plan_checklist.py` | 5 | | The deployment checklist hides what the plan cannot need. |
| `test_plan_clarify.py` | 10 | PG | One question per topic, at most two rounds and 20 questions. |
| `test_plan_compact.py` | 9 | PG | Compact plan expands to the full body; one-round answers. |
| `test_plan_conformance.py` | 14 | PG | The saved project against the approved plan. |
| `test_project_files.py` | 24 | PG | Project file tree, change log, read-only and stale-base refusals. |
| `test_project_knowledge.py` | 8 | PG | Project knowledge store: indexing, ranked retrieval in a token budget. |
| `test_schema_migrations.py` | 17 | | Schema diff: additive ops planned, destructive refused, rename is drop plus add. |
| `test_schema_evolution.py` | 21 | PG | Additive schema evolution of data tables: backfill, unique, enum, migrations table. |

### Approvals and tasks (Stage 5)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_approval_node.py` | 26 | | Suspend payload, redaction, ports on resume, test mode, pinned data. |
| `test_approval_misc.py` | 8 | PG | Actor seam, replay of a decided approval, hooks, scheduler wiring, detached runs. |
| `test_approval_notify.py` | 16 | PG | `notify_email` recipients, the approver email with the task link, sent after the task row exists. |
| `test_approval_per_item_pg.py` | 12 | PG | One task per item; the run resumes once when the last one settles. |
| `test_task_decide_pg.py` | 21 | PG | Decide idempotency, one-winner races, validation, the inbox. |
| `test_task_flow_pg.py` | 10 | PG | A run pauses at an Approval, becomes waiting, a decision resumes it. |
| `test_task_resume_pg.py` | 24 | PG | A crash mid-resume is never re-run; the sweep resumes stuck tasks; timeouts. |
| `test_loop_approval_resume_pg.py` | 2 | PG | A Loop paused at an Approval keeps pending items across resumes. |
| `test_scorecard_approval.py` | 6 | PG | Approval ports count as branches in the scorecard; a mocked approval never waits. |
| `test_mutations_approval.py` | 5 | | Planted bug `swap_branches` on Approval. |
| `test_recipes_approval.py` | 6 | | Test recipes for Approval and FormTrigger. |
| `test_runner_suspend.py` | 9 | | Suspend never queues downstream; other branches finish. |

### Runner and nodes

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_node_contracts.py` | 55 | | Each node's declared contract, table-driven. |
| `test_node_disabled.py` | 6 | | Disabled nodes pass input through. |
| `test_on_error.py` | 9 | | `on_error` stop / continue / error_output with retries and legacy `continue_on_fail`. |
| `test_runner_partial.py` | 7 | | `stop_at` partial runs. |
| `test_runner_unknown_node_fails_run.py` | 1 | | An unregistered node type fails the run. |
| `test_loop_characterisation.py` | 10 | | Pins how Loop and Poll run today. |
| `test_loop_history.py` | 25 | | Per-item loop history and loop settings (concurrency, skip failed, stop after N). |
| `test_poll_condition.py` | 1 | | A pre-resolved Poll condition does not crash. |
| `test_json_template_structures.py` | 4 | | JSON templates stored as real JSON lists or objects. |
| `test_postgres_transaction_per_item_pg.py` | 2 | PG | Postgres `transaction` runs once per item. |
| `test_postgres_secret_policy.py` | 10 | | External Postgres destinations honour broker host bindings. |
| `test_send_email_and_html.py` | 19 | SRV | SendEmail over a real in-process SMTP server, the HTML node, the report renderer. |
| `test_shapes.py` | 21 | | Shape lookup (None vs UNKNOWN), merge, from_sample, normalize. |
| `test_row_timestamps_utc.py` | 3 | | Row timestamps carry an explicit UTC offset. |
| `test_retired_model_alias.py` | 7 | | Retired Gemini names still work through an alias. |
| `test_gemini_roundtrip.py` | 12 | | Gemini tool history keeps provider-signed content across turns. |
| `test_llm_adapter_contracts.py` | 4 | | Anthropic and OpenAI adapters against recorded request/response fixtures. |
| `test_llm_truncation.py` | 6 | | Truncated replies are flagged on every provider path. |
| `test_fake_llm.py` | 41 | | The test-only fake LLM: activation, script format, matching, retries. |
| `test_studio_function_tool.py` | 3 | | Nodes-as-functions agent tool config and token minting. |
| `test_sandbox_output_bounds.py` | 2 | BW | Both child pipes are drained within their caps. |

### Broker, router, tables, security (pure coverage)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_cov_broker_policy.py` | 32 | | Broker egress policy, read-only gate, secret host binding, crypto. |
| `test_cov_router_pure.py` | 9 | | Router path matching, body caps, result redaction. |
| `test_cov_slug_and_redaction.py` | 42 | | Slug rules, `$vars` substitution, secret redaction including encoded forms (1 runtime skip). |
| `test_cov_spec_and_client.py` | 24 | | Spec validation and diff, OpenAPI, typed-client codegen, schema inference. |
| `test_cov_tables_pure.py` | 46 | | Data-table type checks, column validation, filter compilation. |
| `test_broker_jobs.py` | 28 | SRV BW | Python routes drive the Jobs service through the broker (a stub Jobs server). |
| `test_broker_run_workflow.py` | 16 | PG | `run_workflow` from backend code through the broker. |
| `test_route_caller.py` | 5 | PG | `req.caller` is resolved server-side; identity headers are stripped. |
| `test_external_connections.py` | 26 | | SQL screening, masking, prompt context for external DBs. |
| `test_project_datasets_pg.py` | 24 | PG | Project datasets with the analytics service faked by `httpx.MockTransport`. |
| `test_review_spec_diff.py` | 3 | | Auth and root response type changes are breaking. |

### Checker, contracts, testing core (Stages 2, 3A, 3B)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_workflow_checker.py` | 102 | | WF001-WF009, expression paths, coverage, disabled nodes, error port, cycles (1 skip, a parked `$json` literal bug). |
| `test_import_placeholder_contract.py` | 6 | | n8n placeholders have UNKNOWN output, not passthrough. |
| `test_n8n_import.py` | 100 | | The n8n importer. |
| `test_stage3a_api_contract.py` | 14 | | HttpRequest with `api_ref` in contracts and WF006. |
| `test_stage3a_assertions.py` | 54 | | Every assertion op, array paths, item/all/any. |
| `test_stage3a_mock_gen.py` | 5 | | Deterministic example data and mock options. |
| `test_stage3a_mock_runner.py` | 37 | | Test mode: mock priority, error and timeout mocks, unmocked outside steps. |
| `test_stage3a_mutations.py` | 11 | | Planted-bug operators, determinism, cap of 30. |
| `test_stage3a_node_catalog.py` | 2 | | The catalog covers every registered type. |
| `test_stage3a_openapi.py` | 14 | | OpenAPI 3 import. |
| `test_stage3a_recipes.py` | 27 | | Test recipes, boundary extraction, data categories. |
| `test_stage3a_report.py` | 7 | | Test run report: grid, badges, summary, status rules. |
| `test_stage3a_shapes_effects.py` | 22 | | `shapes.validate`, the effects table, test renames, lock rules. |
| `test_stage3a_toolkit.py` | 10 | | Workflow toolkit and scorecard bar text. |
| `test_stage3b_fixes.py` | 16 | | Prompt redaction, fuzz time cap, live-node limits, smoke schedule floor, drift guard. |
| `test_fuzz_generators.py` | 6 | | Fuzz generators are deterministic per seed. |
| `test_fuzz_route.py` | 4 | | The fuzz route maps AI failures to codes. |

### Interfaces (forms, chat, public pages, Stage 5)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_interface_service.py` | 46 | | Public interface resolution (404 hides), schema, submit, idempotency, run view. |
| `test_public_interface_routes.py` | 24 | | `/api/i/{project}/{slug}`: body caps, strict models, no-store, rate-limit hook. |
| `test_interface_internal_routes.py` | 14 | | Studio `/api/workflows/{id}/interface*` mapping and errors. |
| `test_interface_publish.py` | 11 | | Publish owns the slug; 409 `INTERFACE_SLUG_TAKEN`. |
| `test_interface_roundtrip.py` | 9 | | The `interface` key survives every dump/load path. |
| `test_interface_checker.py` | 19 | | WF013-WF019 and FormTrigger / ChatInput nodes. |
| `test_interface_ai.py` | 15 | | "Change the screen" AI with one repair round. |
| `test_interface_client_codegen.py` | 8 | | The typed interface client is deterministic (snapshot). |
| `test_interface_detached_run.py` | 5 | | Detached runs return the id at once and outlive the caller. |
| `test_chat_service.py` | 32 | | Interface chat: one turn in flight, history, token scoping. |
| `test_form_fields.py` | 32 | | Shape to form fields and server-side validation. |
| `test_screen_spec.py` | 52 | | ScreenSpec v1 schema, components, bindings, generation. |

### Knowledge / capability graph

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_kg_checks.py` | 11 | | Concept checks CGX01-CGX05. |
| `test_kg_extract.py` | 8 | PG | Deterministic extraction into the graph. |
| `test_kg_learn.py` | 9 | PG | Operational memory: skeletons, slot tokens, scrubbing. |
| `test_kg_ontology.py` | 30 | | The seed ontology loads and maps to rows. |
| `test_kg_retrieve.py` | 9 | | Retrieval order over a loaded slice. |
| `test_kg_signatures.py` | 8 | | Concept signatures and slot read-back. |
| `test_kg_slots.py` | 104 | | Typed slot extractors and answer validation. |
| `test_kg_team.py` | 8 | PG | One graph across a team's projects. |
| `test_kg_understand.py` | 52 | | Understand: recognition gate, optional LLM call, slots, questions, answers. |

### Stage 8 reuse (sub-workflows, releases, templates)

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_stage8_b1_subworkflow.py` | 44 | | ExecuteWorkflow v2, in-memory invoker, guards, timeout, isolation. |
| `test_stage8_b1_trigger_and_tool.py` | 20 | | ExecuteWorkflowTrigger inputs and the WorkflowTool agent tool. |
| `test_stage8_b2_invoker.py` | 17 | PG | `DbSubworkflowInvoker` and its wiring into ExecutionService. |
| `test_stage8_b2_refs.py` | 13 | PG | `workflow_refs` index, publish rules, delete protection. |
| `test_stage8_b2_repository.py` | 10 | PG | Execution lineage; migration rollback on a second DB (1 own PG skip). |
| `test_stage8_b2_resolve.py` | 7 | PG | `resolve_child`. |
| `test_stage8_b3_checker.py` | 48 | | Checker codes WF020-WF029 and typed ExecuteWorkflow output. |
| `test_stage8_b3_extract.py` | 64 | | Convert selection to sub-workflow and refusals CS01-CS10. |
| `test_stage8_b3_io.py` | 25 | | Declared inputs, derived output, Approval closure. |
| `test_stage8_b3_lookups_db.py` | 6 | PG | The call closure is loaded from the DB for the checker. |
| `test_stage8_b4_helpers.py` | 27 | | Release pins and diff, version parsing, stale flag. |
| `test_stage8_b4_run_sites.py` | 6 | PG | Cron, broker and other run sites use the DB invoker. |
| `test_stage8_b5_bundle.py` | 61 | | Template bundle validator, every error code. |
| `test_stage8_b5_install_pg.py` | 37 | PG | Every shipped template installs and its own tests pass. |
| `test_stage8_b8_ai_reuse.py` | 110 | | Template matching and sub-workflow grounding in AI prompts. |
| `test_stage8_b9_n8n_subworkflows.py` | 42 | | n8n Execute Workflow import. |
| `test_stage8_b10_inline.py` | 24 | PG | Inline child in tests and typed generated mocks. |
| `test_stage8_b10_refs_hook.py` | 7 | PG | Every draft write refreshes `workflow_refs` in its transaction. |
| `test_stage8_t1_bundle_lints.py` | 26 | | Real data or secrets cannot pass template lints. |
| `test_stage8_t1_extract_props.py` | 801 | | Convert selection on 400 seeded random graphs (175 skip when the seed already has a checker error). |
| `test_stage8_t1_runtime_edges.py` | 50 | | Sub-workflow cycles, depth 10, timeouts (1 strict xfail that can never pass). |

### Notebook Ask prompt

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_notebook_ask_prompt.py` | 14 | | The prompt content. It carries notes, the last-run summary, data tables, all environments and the cell rules. Hidden `CONN_*_URL` secrets and null spec fields are left out. |
| `test_notebook_ask_followups.py` | 4 | | `$cred` expansion (username in any step, secrets only in fill/otp), the `remove` validation, project variables, and the prompt's row-match and number-cast rules. |

## A5. Engine integration catalogue (`apps/workflow-engine/tests/integration`)

Every file needs Postgres and an engine subprocess. Extra tags:

- **FAKE**: uses the fake-LLM engine (`fake_api`, which runs the legacy chat builder)
- **BW**: bubblewrap
- **ESB**: esbuild
- **JOBS**: Node jobs-service
- **BR**: Chromium
- **STACK**: starts its own extra DBs or engines
- **INPROC**: no HTTP; in-process routers or services on the test DB

### Stage 1-3: run/debug, checker, testing core

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_stage1_api_journey.py` | 1 | | Story: a failed refund run is debugged, fixed, resumed, rolled back. |
| `test_stage1_run_debug.py` | 36 | | Node test, run-to, run-from, disabled, on_error, restore; reads DB rows. |
| `test_stage2_api_journey.py` | 1 | | Story: check, typo blocks publish, fix, publish, run. |
| `test_stage2_checker.py` | 3 | | `POST /check` and the publish block. |
| `test_stage2_contracts_checker.py` | 46 | | Every WF code over `/check`, publish block, learn/approve/delete contracts. |
| `test_stage2_golden.py` | 36 | INPROC | Seeds, Marketplace workflows and 10 n8n imports through the checker (snapshots). |
| `test_stage3a_api_catalog.py` | 20 | | API catalog, spec-based contracts, mock generation. |
| `test_stage3a_api_journey.py` | 1 | | Story: Refund triage gets a Risk API from OpenAPI and tests. |
| `test_stage3a_contract_edges.py` | 161 | | Mock priority, smart mocks, error and timeout mocks under retry and on_error. |
| `test_stage3a_determinism.py` | 18 | | 20 repeated test runs per offline fixture give the same result. |
| `test_stage3a_runner.py` | 23 | | Test runs with mocks, unmocked failures, tests survive publish and restore. |
| `test_stage3a_scorecard.py` | 16 | | Scorecard, publish and release block, toolkit. |
| `test_stage3a_suggest.py` | 5 | | Suggested tests, save as test, node catalog. |
| `test_stage3b_smoke.py` | 19 | | Live smoke tests against the fake risk API. |
| `test_test_ai_draft.py` | 5 | FAKE | `POST /tests/ai-draft` with scripted replies. |
| `test_fuzz_api.py` | 4 | | Fuzz finds a planted bug; the finding's test fails then passes. |
| `test_drift_live.py` | 4 | | Drift alerts when the fake API switches from v1 to v2. |
| `test_loop_stop_early_api.py` | 1 | | `LOOP_STOPPED_EARLY` is kept on the saved run. |
| `test_update_explicit_empty.py` | 3 | | An explicit empty list replaces on PUT; settings merge by key. |

### Stage 5: approvals and interfaces

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_stage5_tasks_api.py` | 4 | | A run pauses, the task appears, a decision resumes the same run (builder smoke). |
| `test_stage5_interfaces_smoke.py` | 15 | ESB | Slug ownership, form submit and poll, idempotent replay (builder smoke). |
| `test_stage5_t1_chat.py` | 26 | STACK | Chat sessions, waiting message while held at an Approval. |
| `test_stage5_t1_decide.py` | 25 | STACK | Decide races and timeouts; one decision, at most one refund. |
| `test_stage5_t1_limits.py` | 6 | STACK | Size limits, secrets at rest, orphaned tasks, preview runs. |
| `test_stage5_t1_public.py` | 64 | STACK | Anonymous public interface: validation, 404 hiding, no internals leaked. |
| `test_stage5_t1_restart.py` | 11 | STACK | SIGKILL the engine; waiting runs survive and resume once. |
| `test_stage5_t2_journey.py` | 10 | STACK ESB | API journey end to end (uses tsc; 1 skip without it). |
| `test_stage5_wave2.py` | 15 | INPROC | Streamed run ends `waiting`; engine wiring. |

### Stage 6: AI builder over HTTP

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_stage6_b1_repository.py` | 18 | INPROC | Proposal repository: rows, conditional transitions, event log. |
| `test_stage6_b1_sandbox.py` | 48 | INPROC | Tests on an unsaved proposal; Keep/Undo/Discard races. |
| `test_stage6_b4_api.py` | 25 | FAKE | Builder routes, status codes, error envelopes. |
| `test_stage6_b5_api.py` | 7 | FAKE | Migrated chat, the project's api_ref list. |
| `test_ai_builder_api.py` | 56 | FAKE STACK | Independent Stage 6 pass: budgets, deadlines, reaper (3 extra engines; 1 xfail). |
| `test_ai_builder_replay.py` | 85 | FAKE | Eval replay tier: 15 cases on 20 fake bank APIs, no network (1 skip). |
| `test_ai_eval_reference_solutions.py` | 18 | | Hand-built reference solutions pass the hidden eval tests. |

### Stage 8: reuse

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_stage8_b2_reuse_http.py` | 5 | STACK | Child execution rows, delete protection, startup backfill (2 restarts). |
| `test_stage8_b4_extract.py` | 5 | | Convert selection over HTTP on a bank workflow. |
| `test_stage8_b4_releases.py` | 6 | | Releases pin the sub-workflow closure. |
| `test_stage8_b4_wiring.py` | 11 | | Every run site uses the DB invoker; `used_by`. |
| `test_stage8_b5_templates_http.py` | 24 | | Template catalog, preview, install of all starters with their tests. |
| `test_stage8_b8_ai_reuse_http.py` | 3 | FAKE | Spec draft offers a matching template and never installs it. |
| `test_stage8_b10_inline.py` | 10 | | Inline children in tests, typed mocks, `used_by` after writes. |
| `test_stage8_t1_extract.py` | 22 | | Convert selection judged by behaviour before and after. |
| `test_stage8_t1_inline.py` | 11 | | Inline-child tests with grandchildren, nested mocks, pins. |
| `test_stage8_t1_releases_delete.py` | 14 | | Release closure beyond one level; delete protection in every state. |
| `test_stage8_t1_retention.py` | 1 | STACK | Retention counts roots only (`WORKFLOW_RUN_RETENTION=3`). |
| `test_stage8_t1_runtime.py` | 28 | | Sub-workflow runtime under hostile use (1 strict xfail, defect S8-1). |
| `test_stage8_t1_templates.py` | 19 | STACK | Template CI gate, racing installs, rollback. |

### Notebook

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_notebook_api.py` | 24 | | Cells, runs, staleness, writes, apply/discard, run lock, for-each. |
| `test_notebook_api_testing.py` | 6 | | HTTP cell API testing: auth by secret key, sentence assertions. |
| `test_notebook_ask.py` | 6 | FAKE | Ask: one forced `plan_cells` call, at most one repair. |
| `test_notebook_browser.py` | 8 | JOBS BR | Browser cells on a real jobs-service and Chromium. |
| `test_notebook_edge.py` | 68 | STACK | Hostile input, hostile SQL, races, 404s, paging bounds, guards. |
| `test_notebook_keep.py` | 10 | | Keep turns a notebook into a saved workflow with no LLM. |
| `test_notebook_rows.py` | 5 | | A change scoped to exactly the selected rows. |
| `test_notebook_ask_memory.py` | 3 | FAKE | Notes are saved from plan_cells and shown in the next prompt; PATCH clears them; a notes-only reply creates no cells. |
| `test_notebook_run_options.py` | 5 | | http `rerun: "always"`, `continue_on_fail`, `has_item`, compare `fail_on_mismatch`, and for_each rows carrying their input `row`. |
| `test_notebook_ask_followups.py` | 3 | FAKE JOBS BR | The username expands in expect steps, failures report "found …", `remove` deletes cells, and project variables appear in the prompt. |
| `test_notebook_ask_walkthrough.py` | 3 | FAKE JOBS BR | The three owner journeys end to end: a business rule across site, DB and API; a test-case table run with write-back and re-running the failures; back-to-back commands. It boots a small local site with a login. |

### Modules, sandbox, broker, apps

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_ui_generate.py` | 23 | FAKE BW STACK | `POST /modules/generate` makes a validated draft and never writes. |
| `test_ui_run_draft.py` | 7 | BW | `POST /modules/run-draft` runs unsaved source in the sandbox. |
| `test_ui_fake_llm.py` | 10 | BW STACK | Fake LLM startup refusals, `/api/_test/*`, a `.env` switch has no effect. |
| `test_ui_audit_readonly_egress.py` | 3 | | Read-only broker tokens cannot reach the engine or private addresses (SSRF). |
| `test_broker_run_workflow_http.py` | 2 | BW | A Python route calls a workflow through the real broker socket. |
| `test_postgres_saved_secret.py` | 1 | BW | Sandbox to broker to Postgres with a write-only secret. |
| `test_project_connections.py` | 11 | BW | External DB connections: introspection, read-only queries, broker, Postgres node. |
| `test_oneshot_runtime_fixes.py` | 2 | BW | Scenario auto-tests restore the draft data they changed. |
| `test_build_end_to_end.py` | 1 | INPROC | A full build with the real coordinator (forces `WORKFLOW_BUILDER_MODE=pipeline`). |
| `test_plan_end_to_end.py` | 7 | INPROC | Plan flow through the real routers; only the model is fake. |
| `test_app_description.py` | 1 | | An app description is stored and returned. |
| `test_release_atomic_publish.py` | 1 | ESB | A failed app build does not activate a release. |
| `test_backend_gaps.py` | 9 | | Bulk row insert, run status code, daily run counts. |
| `test_code_map.py` | 2 | | Code-map analysis is read-only, scoped and bounded. |

### Capability graph and AI apps

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_ai_apps_http.py` | 11 | FAKE | App composer `/api/ai/apps`: team A's app, adapt, install. |
| `test_ai_builder_graph_http.py` | 8 | FAKE | Understand inside the spec and build loop (1 stale skip). |
| `test_kg_api.py` | 11 | STACK | Startup seed, rebuild, graph focus, capability matrix. |
| `test_kg_repository.py` | 34 | INPROC | Graph migration and repositories. |

### Phases, characterisation, review and audit fixes

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_phase1_projects.py` | 4 | STACK | Projects foundation and the baseline seed. |
| `test_phase2_secrets.py` | 3 | | Project secret wins over team secret; write-only; missing scan. |
| `test_phase3_public_run.py` | 3 | ESB | Published apps run real workflows. |
| `test_phase4_broker.py` | 5 | | SDK generation and broker policy matrix. |
| `test_phase5_api.py` | 2 | | Route, test, infer, spec revisions, OpenAPI. |
| `test_char_ai_misc.py` | 2 | | AI chat SSE without a key; file browser. |
| `test_char_apps.py` | 9 | ESB | App builder, publish, public pages, API tester. |
| `test_char_methods.py` | 9 | | Other HTTP methods on catch-all routes; root, health, credentials. |
| `test_char_webhooks_vars.py` | 6 | | Webhooks and `$vars`. |
| `test_char_workflows.py` | 7 | | Workflow endpoints with studio payload shapes. |
| `test_review_g1_security.py` | 15 | ESB | Redaction, CORS, variable scoping, dry-run correctness. |
| `test_review_g2_modules_routes.py` | 10 | BW | Optimistic concurrency, route conflicts, spec diff. |
| `test_review_g3_broker_release.py` | 5 | BW | Broker read-only claim, release-pinned PythonStep. |
| `test_review_g4_runs.py` | 7 | BW ESB | Trigger types, redacted route runs, retention. |
| `test_review_g5_app_tokens.py` | 7 | ESB | Published-app tokens bound to the app, refresh, survive promote. |
| `test_review_g5_builder_usage.py` | 6 | ESB | App builder SSE, declared endpoints, api-usage, slug. |
| `test_review_g6_releases_projects.py` | 9 | | Release outcomes; a real migration on a legacy row. |
| `test_review_g7_misc.py` | 2 | ESB | Public-app run errors carry codes; `/test` semantics. |
| `test_audit_module_concurrency_rename.py` | 2 | | Module saves are serialised; breaking renames are refused. |
| `test_audit_release_pinning.py` | 1 | BW ESB | Published-app runs are pinned to the active release. |
| `test_audit_team_secret_redaction.py` | 1 | BW | Route traces are redacted with team secrets too. |
| `test_zz_endpoint_coverage.py` | 1 | | Every studio route is hit by some test (runs only on a full collection; currently always skips). |

`test_stage5_t1_support.py` holds no tests (see A2).

### `integration/journeys`

| File | Tests | Tags | Protects |
|---|---|---|---|
| `test_j01_j17_flagship_releases.py` | 2 | ESB | Flagship journey; release gates and rollback. |
| `test_j02_j06_compat_and_projects.py` | 7 | ESB | Legacy editor, builder, variables, tester, chats, projects home. |
| `test_j07_j10_settings_secrets_public_broker.py` | 4 | ESB | Settings and environments, secrets, a published app runs a workflow, SDK broker policy. |
| `test_j11_j13_router_keys_schemas.py` | 3 | | Router semantics, API keys and auth modes, schemas and `validate_request`. |
| `test_j14_app_api.py` | 3 | ESB | App to API usage, stubs, breaking changes, preview. |
| `test_j15_data.py` | 2 | | Data tables and the DataTable node; `studio.db` via the broker. |
| `test_j16_python_modules.py` | 3 | BW | Python modules, sandbox trace, sandbox escape probes. |
| `test_j20_marketplace.py` | 9 | BW | A hand-built Marketplace project end to end (1 strict xfail: retention cap). |
| `test_j21_stage3b_ai_live.py` | 1 | FAKE | AI drafts, fuzz, drift, smoke and release on the fake LLM (the name says live; it is not). |
| `test_j21_tables_deep.py` | 7 | | Bulk insert, filters, sort, cursor paging, validation. |
| `test_j22_management_and_scheduler.py` | 8 | | Management endpoints, publish trigger sync, the scheduler (up to 45 s wait). |
| `test_j23_secrets_edges.py` | 3 | | Secret validation, scope precedence, write-only, host binding edits. |
| `test_j24_known_gaps.py` | 2 | | Strict xfails for engine gaps: egress allowlist on HTTP nodes, numeric secret. |

## A6. Other engine test folders

| File | Tests | Tags | Protects |
|---|---|---|---|
| `journeys/test_capability_graph_two_teams.py` | 1 | FAKE | Two teams reuse one pattern over HTTP. |
| `journeys/test_reuse_journey.py` | 1 | | A bank team reuses work over HTTP (templates, sub-workflows). |
| `live/test_ai_builder_eval.py` | 16 | LIVE | 15 bank requests through the AI builder on a real model; skips without `WORKFLOW_LIVE_GEMINI=1`. |
| `live/test_test_ai_gemini.py` | 2 | LIVE | Stage 3B test-AI helpers on a real model; same gate. |
| `nodes/ai/tools/test_mongo_query_guard.py` | 28 | | MongoQueryTool validator. |
| `routes/test_project_plan_routes.py` | 6 | PG | Plan API: draft, edit, approve, breaking revision needs `accept_breaking`. Imports fixtures from `tests.unit.conftest`. |

Live runner scripts in `tests/live` are not collected by pytest. They make billable calls and need `GEMINI_API_KEY`, `PG_BINDIR`, Chromium, bubblewrap and studio `node_modules`. They write artifacts under `screenshots/`. `tests/live/README.md` documents them.

| Script | Does |
|---|---|
| `run_postgres_dashboard.py` + `postgres_dashboard.cjs` + `dashboard_db.py` | A private Postgres cluster; secrets are saved and rotated through the UI; a generated dashboard; no secret leaks. |
| `run_banking_app.py` + `banking_app.cjs` / `full_banking_checks.cjs` + `banking_db.py` | 600 transactions, generated routes and app, checked against SQL oracles. |
| `run_app_ideas.py` + `app_ideas.cjs` + `ideas_db.py` | Three generated-app acceptance journeys. |
| `run_marketplace.py` + `marketplace.cjs` / `marketplace_steps.cjs` | The Marketplace scenario with a browser and a local upstream. |

Dead folders: `tests/engine/` and `tests/services/` contain only stale `__pycache__`. Do not port them.

## A7. jobs-service (`apps/jobs-service/tests`)

Run with `npm test` (`node --test tests/*.test.ts`).

| File | Tests | Tags | Protects |
|---|---|---|---|
| `evidence.test.ts` | 6 | | DOM diff, network diff, pause/step/resume, takeover relay. |
| `takeover-trace.test.ts` | 2 | BR | Typed text from a taken-over session never reaches the trace, video or screenshots. |

The engine also tests against the jobs-service in `integration/test_notebook_browser.py` (real service) and `unit/test_broker_jobs.py` (stub).

## A8. analytics-service (short)

The test sources are not in this copy. `apps/analytics-service/tests/` holds only stale `__pycache__` (163 `.pyc`, 144 of them `test_*`). `scripts/` holds only `__pycache__`, and `web/` has no `package.json` or tests. Git tracks no test files for this service.

From earlier project notes (not verified here):

- The pytest suite was about 2680 tests and took about 110 s over both storage backends (local and MinIO).
- Parallel runs need isolated workers:
  - `scripts/provision_test_workers.sh N`, then `scripts/test_worker.sh wN <pytest args>`.
  - Each worker gets its own DB, storage dir, tus dir and MinIO prefix.
  - Env names: `ACCELERATOR_DB_NAME`, `ACCELERATOR_STORAGE_DIR`, `ACCELERATOR_TUS_UPLOAD_DIR`, `TEST_S3_PREFIX`, `ACCELERATOR_KEEP_TEST_STATE`.
- Never run two default-env pytests at once. An autouse cleanup fixture deletes table rows.
- The web UI regressions ran with `cd web && npm run test:ui` (11 browser tests), against the API on :8001 and the dev server on :5173 (`UI_BASE`, `API_BASE`).

To port this suite, take it from the other copy.

---

# Part B: UI (`apps/workflow-studio`)

## B1. How to run

| What | Command |
|---|---|
| Unit | `npm run test:unit` (`vitest run`) |
| One unit file | `npx vitest run src/features/plan/planSteps.test.ts` |
| E2E, all | `npm run test:e2e` (`playwright test -c e2e`) |
| E2E, one spec | `npx playwright test -c e2e tests/34-glass-editor-parity.spec.ts` |
| Keep the stack after a run | `KEEP_E2E=1 npm run test:e2e ...` |
| Showcase spec | `SHOWCASE=1 npx playwright test -c e2e tests/99-showcase.spec.ts` |
| Captures | `npx playwright test -c e2e/capture.config.ts` |

E2E needs:

- Postgres on 5433
- the engine venv at `apps/workflow-engine/venv`
- Node, and Playwright Chromium (falls back to `/usr/bin/chromium`)
- esbuild, bubblewrap, and the jobs-service folder for specs 44-46

Run only one e2e run at a time. Each run starts its own stack, but `workers: 1` and the timing make parallel runs unreliable.

## B2. Unit tests

### Harness

- `vitest.config.ts` includes `src/**/*.test.ts` only, with environment `node` and the alias `@` for `./src`.
- There is no DOM and no React render tests. Components are tested through their pure logic modules.
- No unit test skips.

### Catalogue (file: listed tests, what it protects)

**features/ai-builder**

| File | Tests | Protects |
|---|---|---|
| `aiProposalStore.test.ts` | 12 | Folds build events into one row per round. |
| `api.test.ts` | 33 | Builder API errors map to plain messages (422, 409). |
| `apps/AdaptedFromBanner.test.ts` | 6 | The "adapted from" banner text. |
| `apps/appApi.test.ts` | 14 | App composer API calls are project-scoped and send the actor. |
| `apps/lib/appState.test.ts` | 35 | App build state: parts, tests, dependency order. |
| `lib/buildLog.test.ts` | 5 | A round becomes added steps, checks and test rows. |
| `lib/buildState.test.ts` | 10 | The ask, thinking, plan, building, review phases. |
| `lib/hoverTarget.test.ts` | 4 | Hover target finds the step root. |
| `lib/plainTest.test.ts` | 14 | Tests read as plain sentences. |
| `lib/proposalScope.test.ts` | 3 | New build vs change to an existing workflow. |
| `lib/understanding.test.ts` | 24 | Understanding chips and kg-node keys. |
| `nodeAi.test.ts` | 18 | Node AI tasks and their inputs. |
| `proposalMarks.test.ts` | 11 | Canvas marks for added, changed, removed nodes. |
| `stage/beats.test.ts` | 11 | Build animation beats in layer order. |
| `stage/blueprint.test.ts` | 7 | Blueprint layout matches the engine. |
| `stage/buildOps.test.ts` | 11 | Build ops ordering. |
| `stage/carryMeasured.test.ts` | 3 | Copies measured sizes by id. |
| `stage/opsFromEvents.test.ts` | 5 | Reads engine payloads into ops. |
| `stage/stageStore.test.ts` | 6 | Stage store begin, mode, end. |
| `stage/useBuildPlayer.test.ts` | 18 | Plays rounds live and lands on the proposal. |

**features/app-builder**

| File | Tests | Protects |
|---|---|---|
| `hooks/flushBeforeSend.test.ts` | 9 | Pending edits flush before a chat send; 409 stale_base handling. |
| `lib/autotest.test.ts` | 3 | Reads the autotest payload. |
| `lib/chatEvents.test.ts` | 3 | Reads streamed file, route and preview events. |
| `lib/clientGate.test.ts` | 3 | Holds files until the client refetch lands. |
| `lib/previewErrors.test.ts` | 5 | Preview errors are deduped and settle. |
| `lib/previewUsers.test.ts` | 2 | Preview "view as" user default. |
| `lib/restoreChat.test.ts` | 7 | Chat history rebuilt from AI versions. |
| `sandbox/esbuild-bundler.test.ts` | 5 | In-browser bundling of CSS imports. |
| `sandbox/requestPolicy.test.ts` | 20 | Preview request policy: only bound routes and workflow runs. |
| `visual/bind/__tests__/actions.test.ts` | 10 | Visual editor: button action binding. |
| `visual/bind/__tests__/data.test.ts` | 18 | Visual editor: data binding from API ops. |
| `visual/bind/__tests__/visibility.test.ts` | 8 | Visual editor: visibility conditions. |
| `visual/editor.test.ts` | 17 | Visual editor: maps JSX tags to elements and back. |
| `visual/history.test.ts` | 5 | Visual editor undo and redo. |
| `visual/manifest.test.ts` | 29 | Kit manifest props exist on components. |
| `visual/registry/registry.test.ts` | 33 | Kit editor registry. |
| `visual/selection.test.ts` | 5 | Selection walks React fibers. |
| `visual/splice.test.ts` | 16 | Source splicing for attribute edits. |
| `visual/structure.test.ts` | 25 | Structural edits: remove, prune imports, refusals. |
| `visual/style/classEdit.test.ts` | 21 | Tailwind class edits replace conflicts. |

The `visual/*` files test the visual editor, which is behind `VITE_VISUAL_EDIT` and has no e2e.

**Other feature folders**

| File | Tests | Protects |
|---|---|---|
| `approvals/lib/tasks.test.ts` | 13 | Task paging and decided labels. |
| `backend/cm/lineDiff.test.ts` | 3 | Line diff for the code editor. |
| `backend/code-map/layout.test.ts` | 3 | Code map layout and filters. |
| `backend/useSaveModule.test.ts` | 5 | Module save request bodies. |
| `build-studio/studioLogic.test.ts` | 7 | Build studio phase logic. |
| `code/codeLogic.test.ts` | 11 | File tree grouping. |
| `code/filesApi.test.ts` | 4 | Files API calls. |
| `connections/connectionsApi.test.ts` | 1 | Connections API routes. |
| `connections/connectionsLogic.test.ts` | 9 | Connection and table selection. |
| `data/lib/cells.test.ts` | 8 | Cell value conversion. |
| `data/lib/csv.test.ts` | 4 | CSV chunking. |
| `data/lib/filters.test.ts` | 12 | Data filters. |
| `data/lib/profile.test.ts` | 12 | Column profile. |
| `datasets/datasetsApi.test.ts` | 7 | Datasets API calls. |
| `datasets/lib/datasets.test.ts` | 15 | Dataset formatting. |
| `interfaces/lib/askErrors.test.ts` | 5 | Interface AI error messages. |
| `interfaces/lib/binding.test.ts` | 12 | Binding paths. |
| `interfaces/lib/chat.test.ts` | 2 | Chat message dedupe. |
| `interfaces/lib/chatTransport.test.ts` | 4 | Public vs preview chat endpoints. |
| `interfaces/lib/formFields.test.ts` | 28 | Form fields from schemas. |
| `interfaces/lib/interfaceDraft.test.ts` | 9 | Slug rules. |
| `interfaces/lib/polling.test.ts` | 6 | Poll backoff. |
| `knowledge/knowledgeApi.test.ts` | 7 | Knowledge API calls. |
| `knowledge/lib/coverage.test.ts` | 6 | Coverage heat. |
| `knowledge/lib/graph.test.ts` | 16 | Graph filtering. |
| `knowledge/lib/graph3d.test.ts` | 11 | 3D graph styling. |
| `knowledge/lib/kinds.test.ts` | 5 | Node kinds. |
| `knowledge/lib/layout.test.ts` | 13 | 2D layout determinism. |
| `knowledge/lib/layout3d.test.ts` | 10 | 3D layout determinism (2 fail at baseline). |
| `knowledge/lib/projects.test.ts` | 7 | Project colours. |
| `knowledge/lib/viewport.test.ts` | 6 | Viewport fit and zoom. |
| `notebook/cells/rowsApi.test.ts` | 8 | Row keys, target table guess, CSV. |
| `notebook/lib/cells.test.ts` | 5 | Cell references. |
| `notebook/lib/look.test.ts` | 5 | Status column detection. |
| `plan/PlanFlow.test.ts` | 6 | Plan flow phase and approve gate (heavily mocked). |
| `plan/PlanView.test.ts` | 9 | Approve button rules. |
| `plan/buildApi.test.ts` | 5 | Build API calls. |
| `plan/buildLogic.test.ts` | 12 | Build log reducer. |
| `plan/checklist.test.ts` | 9 | Checklist picks. |
| `plan/liveBuild.test.ts` | 13 | Live build file list. |
| `plan/planApi.test.ts` | 7 | Plan API calls. |
| `plan/planSteps.test.ts` | 11 | Plan step state. |
| `project/lib/activity.test.ts` | 2 | Activity grid. |
| `project/lib/entities.test.ts` | 3 | Entity urgency. |
| `project/lib/format.test.ts` | 9 | Duration and date formats. |
| `project/lib/nextSteps.test.ts` | 26 | Next-step suggestions. |
| `project/overview/lib/pulse.test.ts` | 6 | Overview attention list. |
| `runs/lib/runLabels.test.ts` | 8 | Run labels. |
| `runs/lib/runStats.test.ts` | 8 | Run stats buckets. |
| `templates/lib.test.ts` | 4 | Template filters and layout. |

**features/workflow-editor**

| File | Tests | Protects |
|---|---|---|
| `components/ai-chat/templateSuggestion.test.ts` | 9 | Template suggestion parsing. |
| `components/canvas/stageGeometry.test.ts` | 21 | Canvas area covered by panels. |
| `components/ndv/subworkflow/subworkflowParams.test.ts` | 14 | Sub-workflow version params. |
| `glass/lib/pageDoc.test.ts` | 11 | Page view step names and effects. |
| `glass/lib/pageModel.test.ts` | 15 | Page model sections and branches. |
| `glass/lib/pageRun.test.ts` | 8 | Per-case run counts. |
| `glass/lib/pageSentences.test.ts` | 18 | Workflow as sentences with value tokens. |
| `glass/lib/runPath.test.ts` | 7 | What ran, failed, was never reached. |
| `glass/lib/shapeMap.test.ts` | 3 | Shape map layout. |
| `lib/aiChatClient.test.ts` | 14 | AI chat request body and history. |
| `lib/approvalMocks.test.ts` | 3 | Approval mock defaults. |
| `lib/autoLayout.test.ts` | 6 | Auto layout. |
| `lib/contractSuggestions.test.ts` | 5 | Contract suggestions vs run data. |
| `lib/convertUndo.test.ts` | 3 | Undo of convert to sub-workflow. |
| `lib/executionRunData.test.ts` | 3 | Execution to run-data store. |
| `lib/expressionTrigger.test.ts` | 8 | Expression autocomplete trigger. |
| `lib/followRun.test.ts` | 5 | Follow a running run. |
| `lib/jsonParam.test.ts` | 6 | JSON parameter display. |
| `lib/pendingEdits.test.ts` | 2 | Pending edit flushers. |
| `lib/readView.test.ts` | 6 | Read view summaries. |
| `lib/shapes.test.ts` | 8 | Shape lookup. |
| `lib/subworkflowsApi.test.ts` | 9 | CS01-CS10 messages. |
| `lib/tests.test.ts` | 19 | Test ids, drafts, summary text. |
| `lib/testsAi.test.ts` | 12 | AI drafts inserted as unlocked tests. |
| `lib/workflowDiff.test.ts` | 5 | Workflow diff. |
| `lib/workflowTransform.test.ts` | 6 | `disabled` / `on_error` round trip. |
| `stores/checkStore.test.ts` | 3 | Checker findings by node. |
| `stores/convertSubworkflowStore.test.ts` | 2 | Convert dialog store. |
| `stores/nodeFlags.test.ts` | 4 | Disable and error-output toggles. |

**shared**

| File | Tests | Protects |
|---|---|---|
| `components/report/htmlReport.test.ts` | 21 | HTML report sanitising. |
| `components/ui/parsers.test.ts` | 5 | Diff parser agrees with lineDiff. |
| `components/ui/status-dot.test.ts` | 2 | Waiting status. |
| `glass/glass.test.ts` | 21 | Glass utilities and tokens defined in both themes. |
| `motion/testFlag.test.ts` | 4 | Motion test flag. |
| `motion/tokens.test.ts` | 3 | Motion durations match CSS. |
| `theme/__tests__/contrast.test.ts` | 20 | Text contrast at least 4.5:1 on tinted surfaces. |

## B3. E2E harness

### `e2e/playwright.config.ts`

- `workers: 1`, `fullyParallel: false`, retries 0.
- Timeouts: 120 s per test, 15 s for expect and actions.
- Viewport 1440x900, light colour scheme.
- The JSON reporter writes `e2e/.stack/results.json`.
- Chromium is pinned, with a fallback to `/usr/bin/chromium`.

### `e2e/global-setup.ts`

The global setup builds a private stack and never touches the developer's servers.

1. Runs `tests/e2e_stack.py up` in the engine folder to create `workflows_test_e2e_<8hex>`.
2. Starts a Node upstream with `/__log`, `/__reset`, `/v1/balance` and `/v1/leak` (`?fail=1`). Every other path echoes.
3. Starts the engine on a free port from a temporary cwd, with an allow-listed env:
   - the fake LLM script `e2e/fixtures/llm-script.json`
   - a random fake-LLM token and a Fernet key
   - a notebook storage dir and the esbuild path
   - It throws if any provider key is present.
4. Runs these guards:
   - `/api/_test/health` must report `fake_llm` true and the e2e DB name.
   - Only `proj_default` may exist.
   - None of the ports 8000, 8010, 8095, 5173 and 5174 may be used.
5. Runs `e2e_stack.py seed`, then `e2e_stack.py nbseed`.
6. Starts Vite on the first free port from 5180 (`strictPort`), pointed at the engine with `VITE_WORKFLOW_API_URL`.
7. Tries to start the jobs-service with `JOBS_DEV=1`, `JOBS_LLM=off` and a temporary `JOBS_DATA_DIR`. If it cannot start, it records `jobsSkip`, and specs 44-46 skip their browser parts.
8. Writes `e2e/.stack/state-<pid>.json`. Specs find it through `E2E_STATE`.

### `e2e/global-teardown.ts`

- Fails the run if the fake LLM answered any call with `rule_id: null`, that is, a prompt no rule matched.
- Kills the process groups and drops both DBs unless `KEEP_E2E=1`.

### `e2e/fixtures`

| File | Gives |
|---|---|
| `test.ts` | The base `test`. Any 4xx/5xx response fails the test unless declared with `net.expect(...)`. Any console error or warning fails it. `assertNoClipping` runs at the end. Google Fonts get empty CSS. `motion` defaults to `'off'`. Also provides `EngineApi` (`raw`, `ok`). |
| `console-allow.ts` | The console allow-list. It is empty; keep it that way. |
| `legacy.ts` | Init script that adds `?legacy=1` to editor URLs (`/workflows/<id>`), including on `pushState` and `replaceState`. Specs 14-21 use it to open the old canvas editor instead of the glass editor. |
| `seed.ts` | Seeded ids and helpers. |
| `editor.ts` | Legacy canvas editor helpers. |
| `notebook.ts` | Notebook helpers. |
| `sse.ts` | SSE reading. |
| `tests3a.ts` | Stage 3A helpers. Has a hard-coded screenshot path into an old scratchpad. |
| `shots17.ts` | Screenshot helper with a hard-coded scratchpad path. |
| `design.ts` | Design token checks. Line 17 expects the dark background `rgb(8, 9, 10)`. |
| `dock.ts`, `layout.ts` | Dock and layout checks. |
| `llm-script.json` | The fake LLM script for the UI. |
| `apps/RefundsApp.tsx`, `modules/*.py` | Fixture app and modules: `hello`, `refunds`, `refunds_webhook`, `broken`, `broken_fixed`. |

## B4. E2E catalogue (`apps/workflow-studio/e2e/tests`)

Each count is the number of `test(` call sites; theme loops and viewport loops multiply them at run time. Every test checks network and console cleanliness.

### Shell, backend, app builder (01-13)

| Spec | Tests | Protects |
|---|---|---|
| `01-shell.spec.ts` | 4 | Projects home and create, old URL redirects, first-run gating, theme toggle (expects dark `rgb(8, 9, 10)` at line 116). |
| `02-backend-manual.spec.ts` | 10 | Backend view by hand: saved route tests, use as schema, syntax error and fix, autocomplete. |
| `03-generate.spec.ts` | 12 | Generate a module with the fake LLM, secret scrubbing, draft tests, create table on save. |
| `04-data-secrets-runs.spec.ts` | 5 | Write-only secret, data view, environment switch, runs paging. |
| `05-builder.spec.ts` | 5 | Create UI from the backend, typed client, route picker, preview bridge. |
| `06-publish-release-settings.spec.ts` | 5 | Publish with preflight, slug taken, token refresh, release, settings. |
| `07-workflows-responsive.spec.ts` | 2 | Workflows page run; responsive layouts. |
| `08-release-settings.spec.ts` | 3 | Release with a missing secret, rollback warnings, settings and API keys. |
| `09-preview-boundary.spec.ts` | 1 | Preview blocks management, cross-project and external fetches. |
| `10-code-map.spec.ts` | 3 | Code map: hover, pin, filters, drafts, outage (writes into `screenshots/`). |
| `11a-design-data.spec.ts` | 6 | CSV import flow, delete with Undo, Columns menu. |
| `11b-design-runs.spec.ts` | 4 | Runs view: inline expand, j/k, run sheet, stack frame links. |
| `11c-design-backend-shell.spec.ts` | 8 | AI Keep/Undo, route picker, request-path art, the Cmd+K palette. |
| `11d-design-overview-home-release-glass.spec.ts` | 12 | Overview snippets, stat tiles, activity grid, release sheet, glass. |
| `12-backend-gaps.spec.ts` | 3 | Chunked CSV import, partial chunk failure, HTTP status on failed runs. |
| `13-live-fixes.spec.ts` | 2 | Regressions from the live Marketplace run. |

### Legacy canvas editor (14-21, use `legacy.ts`)

| Spec | Tests | Protects |
|---|---|---|
| `14-editor-run-debug.spec.ts` | 8 | Disable, error output, test step, run to and from, history, Tidy, Cmd+K. |
| `15-editor-contracts-checker.spec.ts` | 6 | Checker marks and Problems, contract-based autocomplete, learn/approve contracts. |
| `16-editor-tests.spec.ts` | 13 | API catalog, endpoint picker, test editor, mocks, suggestions, coverage grid. |
| `17-ai-and-live.spec.ts` | 5 | AI fill gaps, AI failure, variations panel, drift banner. |
| `18-interfaces.spec.ts` | 6 | Public form, approvals inbox, reject path, double-click pays once. |
| `19-ai-builder.spec.ts` | 17 | AI builder: outline, plan, modify marks, red tests, Keep/Discard. |
| `20-reuse.spec.ts` | 10 | Sub-workflows: call, pin, failing child, convert selection, publish impact (T20.9 placeholder skip). |
| `21-fixes.spec.ts` | 4 | Delete toast, publish version toast, parent-run link. |

### AI understand and overview (23-25)

| Spec | Tests | Protects |
|---|---|---|
| `23-ai-understand-clarify.spec.ts` | 5 | Live chips, clarify questions, refused answers (knowledge). |
| `24-ai-app-two-teams.spec.ts` | 3 | AI builds a whole app; Discard; team B adapts (knowledge, app composer). |
| `25-overview-dock.spec.ts` | 3 | Overview metrics, the dock, phone width. |

### Glass editor (30-35)

| Spec | Tests | Protects |
|---|---|---|
| `30-glass-design.spec.ts` | 3 | Glass tokens and controls on `/__design#glass`, computed styles, both themes. |
| `31-glass-page.spec.ts` | 6 | Glass is the default; Page reads as sentences; failed run path; run picker. |
| `32-glass-editor.spec.ts` | 5 | Inspector rename, undo/redo, save, publish; node modal; data passing; run drawer. |
| `33-glass-editor-tests-versions.spec.ts` | 2 | Save a run as a test, Run all; compare and restore v1. |
| `34-glass-editor-parity.spec.ts` | 25 | Parity walk: Stop, run to/from, context menu, failing step and more. |
| `35-glass-page-doc.spec.ts` | 7 | Page as a document: switch counts, for-each frame, lanes, test bar. |

### Notebook (40-46)

| Spec | Tests | Protects |
|---|---|---|
| `40-notebook-manager.spec.ts` | 5 | Manager list, new and delete, templates (skips if an endpoint returns 501). |
| `41-notebook-run.spec.ts` | 5 | Run all on the bank DB, stale cells, Rerun from here, for-each. |
| `42-notebook-change.spec.ts` | 1 | Change cell diff, stale proposal, apply with attribution. |
| `43-notebook-keep.spec.ts` | 2 | Keep as workflow with the check strip. |
| `44-notebook-browser.spec.ts` | 2 | Recorded browser recipe on the jobs-service (skips without it). |
| `45-notebook-edge.spec.ts` | 21 | Break-it journey: failures, empty results, large tables, re-reads every write. |
| `46-notebook-rows-api.spec.ts` | 5 | Row actions, API suite, browser take over, removed-cell card. |

### Other

| Spec | Tests | Protects |
|---|---|---|
| `99-showcase.spec.ts` | 2 | Showcase capture with real motion; runs only with `SHOWCASE=1`. |
| `e2e/capture.spec.ts` | 1 | UI captures in both themes; uses `e2e/capture.config.ts`. |

E2E skips (10 sites):

- T20.9 placeholder
- 99-showcase, without `SHOWCASE=1`
- 4 skips when an endpoint returns 501 (40 twice, 43, 45)
- 3 skips when the jobs-service is missing (44, 45, 46)
- 1 non-chromium skip in 11d that can never fire

---

## 4. Known failing at baseline

### Engine (130 failed)

| File | Failed | Cause in the failure message |
|---|---|---|
| `unit/test_stage8_b5_install_pg.py` | 30 | `TEMPLATE_INSTALL_FAILED`: shipped templates fail their own tests (contract "actual missing / expected required", for example `created_at`/`updated_at`). |
| `integration/test_stage8_b5_templates_http.py` | 17 | same template cause |
| `integration/test_stage8_t1_templates.py` | 15 | same template cause |
| `integration/test_stage3a_runner.py` | 7 | contract failures in test runs, including `test_tests_and_defaults_survive_publish_and_version_restore` |
| `integration/test_stage8_t1_inline.py` | 6 | contract failures |
| `integration/test_stage3a_scorecard.py` | 6 | contract failures |
| `integration/test_ui_generate.py` | 5 | stage sequence and route-key changes |
| `integration/test_stage3b_smoke.py` | 5 | contract failures |
| `integration/test_ai_apps_http.py` | 5 | `tests_failing` |
| `integration/test_stage8_b10_inline.py` | 4 | contract failures |
| `integration/test_notebook_keep.py` | 4 | Keep output |
| `integration/test_notebook_rows.py` | 3 | row change |
| `integration/test_drift_live.py` | 3 | `stale_test_ids` |
| `integration/test_stage3a_determinism.py` | 2 | WF008 cycle with Wait |
| `integration/test_stage3a_contract_edges.py` | 2 | contract failures |
| `integration/test_kg_api.py` | 2 | ontology count 24 vs 22 |
| `integration/test_kg_repository.py` | 1 | count 32 vs 30 |
| `unit/test_kg_extract.py` | 1 | extraction counts |
| `unit/test_kg_team.py` | 1 | template count |
| `unit/test_platform_data_layer.py` | 1 | `WORKFLOW_ENCRYPTION_KEY` not set (env-dependent) |
| `integration/test_build_end_to_end.py` | 1 | missing modules runs, tests_library, triage |
| `integration/test_review_g4_runs.py` | 1 | retention 101 <= 100 |
| `integration/test_notebook_edge.py` | 1 | `not_pending` 409 |
| `integration/test_stage3a_api_catalog.py` | 1 | contract failure |
| `integration/test_stage3a_api_journey.py` | 1 | contract failure |
| `integration/journeys/test_j16_python_modules.py` | 1 | 200 vs 409 |
| `integration/journeys/test_j20_marketplace.py` | 1 | journey step |
| `integration/journeys/test_j21_stage3b_ai_live.py` | 1 | journey step |
| `journeys/test_capability_graph_two_teams.py` | 1 | journey step |
| `journeys/test_reuse_journey.py` | 1 | template install 422 |

Most failures share one cause: shipped templates and Refund-triage-style workflows fail their own contract tests ("actual missing / expected required"). Fix that first; many failures should clear together.

### Studio unit (2 failed)

- `src/features/knowledge/lib/layout3d.test.ts`: 2 tests fail. 125 files and 1206 tests ran.

### Studio e2e (23 failed out of 118 run; specs 14-21 and 30-35)

| Spec | Failed tests | Logged causes |
|---|---|---|
| `16-editor-tests` | T16.1, T16.3-T16.9, T16.11 (10 with themes) | test row shows "failed" not "passed"; passed-count mismatch |
| `17-ai-and-live` | T17.3-T17.5 | variations and drift banner |
| `18-interfaces` | T18.2 | approvals inbox |
| `19-ai-builder` | T19.1, T19.6, T19.9, T19.11, T19.12, T19.14, T19.15, T19.17 | `ai-chat-spec-card` missing, dock not visible, clipping |
| `20-reuse` | T20.8 | passed-count mismatch |

The glass specs 30-35 passed. A later run of the legacy specs gave the same 23 failures. No dark-background `rgb(8, 9, 10)` failure appears in either log. Specs 01-13, 23-25 and 40-46 were not run in this baseline.

Known silent gaps:

- `integration/test_zz_endpoint_coverage.py` always skips, because `test_stage5_t1_support.py` collects no items.
- When Postgres is down, 52 unit files skip and the run can look green.

---

## 5. Porting advice

### Order

1. **Harness first.** Port these before any test file:
   - `tests/__init__.py`, `tests/unit/conftest.py`, `tests/integration/conftest.py`, `tests/journeys/conftest.py`
   - `tests/support/`, the support modules in A2, and `tests/fixtures/`
   - `tests/e2e_stack.py` and `tests/fake_refund_api.py`
   - the studio `e2e/` config, global setup and teardown, and `e2e/fixtures/`

   Check that the engine in the other copy still has:
   - the fake LLM provider and `/api/_test/*` routes
   - `WORKFLOW_TEST_MODE`, `WORKFLOW_DISABLE_DOTENV` and `src.db.migrate apply`
   - the `"workflow-app"` schema
2. **Unit next.** Start with the pure files, then the PG files. Run each file on its own and fix names and paths as you go.
3. **Integration next.** Start with Stage 1-3 and the checker, then reuse, approvals and notebook. Put STACK-heavy files (Stage 5 T1, notebook edge) last.
4. **E2E last.** Start with 01, then the glass specs 30-35 and the notebook specs 40-46. Port legacy specs 14-21 only if the other copy still serves `?legacy=1`.

Record a baseline in the other copy before you change any engine code, and compare against it after.

### Feature-dependent tests

Port these only if the feature exists in the other copy:

| Feature | Backend tests | UI tests |
|---|---|---|
| Notebook | `integration/test_notebook_*.py`, `notebook_support.py`, `nb_jobs_support.py`, `e2e_stack.py nbseed` | `src/features/notebook/**`, e2e 40-46 |
| Glass editor | `integration/test_update_explicit_empty.py` | `src/features/workflow-editor/glass/**`, `src/shared/glass/**`, e2e 30-35 |
| Legacy canvas editor | none | `e2e/fixtures/legacy.ts`, e2e 14-21 |
| App builder visual editing | `unit/test_editability.py` | `src/features/app-builder/visual/**` (10 files) |
| Knowledge / capability graph | `unit/test_kg_*.py`, `test_ai_spec_graph.py`, `test_prompts_optional_sections.py`, `test_eval_graph_arm.py`, `test_reference_workflow_pick.py`, `test_app_plan.py`, `test_app_bundle.py`, `integration/test_kg_*.py`, `test_ai_builder_graph_http.py`, `test_ai_apps_http.py`, `journeys/test_capability_graph_two_teams.py` | `src/features/knowledge/**`, `src/features/ai-builder/apps/**`, e2e 23-24 |
| Interfaces (forms, chat, public pages) | `unit/test_interface_*.py`, `test_public_interface_routes.py`, `test_chat_service.py`, `test_screen_spec.py`, `test_form_fields.py`, `integration/test_stage5_interfaces_smoke.py`, `test_stage5_t1_public.py`, `test_stage5_t1_chat.py`, `test_stage5_t2_journey.py` | `src/features/interfaces/**`, e2e 18 |
| Legacy staged chat builder (`WF_CHAT_BUILDER=legacy`) | `unit/test_stage6_b5_chat.py`, and every FAKE integration file | e2e 19 |
| Pipeline build mode | `integration/test_build_end_to_end.py`, pipeline cases in `test_build_*` | none |
| Jobs-service | `unit/test_broker_jobs.py`, `integration/test_notebook_browser.py` | e2e 44-46 |

### Rules

- Never weaken a test, add a skip or xfail, or loosen an assertion to make it pass. If it fails, the code or the port is wrong.
- Adapt names, paths, test ids, routes and fixture names to the other copy. Keep what each test asserts.
- Do not regenerate snapshots (`WF_PROMPT_SNAPSHOT_WRITE`, `UPDATE_SNAPSHOTS`) to make a test green until you have read the diff.
- Keep the e2e policies: network errors fail, console errors fail, the allow-list stays empty, and `rule_id: null` fails teardown.
- Fix the hard-coded screenshot paths in `e2e/fixtures/shots17.ts` and `tests3a.ts` while porting. Point them at a per-run folder.

### Merge and drop (brief)

- **Drop:**
  - `tests/engine/` and `tests/services/` (stale `__pycache__` only)
  - the strict xfail in `unit/test_stage8_t1_runtime_edges.py` that can never pass
  - `e2e/capture.spec.ts` and `99-showcase.spec.ts`, unless you need captures
- **Merge:**
  - `integration/test_stage2_checker.py` into `test_stage2_contracts_checker.py`
  - `test_stage5_tasks_api.py` and `test_stage5_interfaces_smoke.py` into the Stage 5 T1 files
  - the Stage 8 B files (`b4_extract`, `b10_inline`, `b4_releases`, `b5_templates_http`) into their T1 counterparts
  - `test_phase2_secrets.py` into `j23`, and `test_phase3_public_run.py` into `j07_j10`
  - `unit/test_stage3a_node_catalog.py` into `test_stage3a_shapes_effects.py`
  - `unit/test_mutations_approval.py` into `test_stage3a_mutations.py`
- **Fix:**
  - Give `test_stage5_t1_support.py` a non-`test_` name so `test_zz_endpoint_coverage.py` runs.
  - Give `tests/routes` its own conftest instead of importing from `tests.unit.conftest`.
- **Later, if the legacy editor goes:** drop e2e 14-21 after checking that 31-35 cover the same flows (history, Tidy, run to and from, publish, save run as test).
