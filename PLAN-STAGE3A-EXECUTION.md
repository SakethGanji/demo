# Stage 3A execution plan (owner said "proceed" 2026-10-17; contracts frozen in CONTRACTS-STAGE3A.md)

Baselines re-recorded 2026-10-17 and matching: engine 906 passed/1 skip/2 xfail, studio unit 155, tsc clean, lint 96, design:check clean.

## Wave 1 file ownership
- A, the engine core:
  - `engine/{effects,test_mode,assertions,shapes,workflow_runner,types,expression_engine}.py`;
  - Wait, Sample and the time/random tools;
  - `schemas/workflow.py`, `repositories/workflow_repository.py` and the executions listing filter;
  - `routes/workflows.py`, `services/{workflow_service,execution_service,test_runner}.py`.
- B, the API catalog:
  - the migration and `ApiSpecModel` in `db/models.py`;
  - `repositories/api_spec_repository.py`, `services/{api_catalog,openapi_import,mock_gen,workflow_checker,check_lookups}.py` and `engine/contracts.py`;
  - `nodes/integrations/http_request.py`;
  - `routes/{api_specs,test_mocks}.py` and their router registration.
- C, the studio: everything under `apps/workflow-studio/src` (no e2e).
- The lead integrates after Wave 1: `execution_service` calls B's `load_api_specs` to fill `context.extras["api_specs"]`.

Source: `HANDOFF-STAGE3.md` §4–§7. Contracts get frozen in `CONTRACTS-STAGE3A.md` after the owner OKs this plan.

## What the code review found (2026-10-17)
1. **No side-effect field on nodes.** Strict offline mode needs one. Add `BaseNode.effect: "none"|"network"|"write"|"llm"` plus a table covering every node type (HttpRequest, Postgres, MongoDB, Neo4j, DataTable write, email = network/write; LLM/Agent = llm; Set/If/... = none).
2. **Time is not frozen.** `expression_engine.py:96-98` calls `datetime.now()` directly, and Code/Sample use `random`. Add `context.clock` and `context.rng`, and route the expression helpers and those nodes through them.
3. **The mock hook point is clear.** In `_process_job`, right after the disabled check (`workflow_runner.py:617`):
   - mocked items go down the same path as pinned data;
   - a mocked error raises inside the retry loop (`:674`), so retries and `on_error` behave as in production.
4. **There are two publish gates:** `POST /workflows/{id}/publish` and `release_service.run_checks`. Both get a `workflow_tests_pass` check.
5. **`tests: []` is workflow-level,** so the node rebuild sites don't change. It has to be copied through the repository dumps (`workflow_repository.py:49/133/296`), the version snapshot, restore, export and import.
6. **Refund triage's Risk API URL is a placeholder** (`__REFUND_API_URL__`). The catalog endpoint picker replaces it in the demo.

## Waves (at most 3 agents at once, separate files, agents never touch git)
**Wave 1 (parallel)**
- A. Engine testing core:
  - the test model in the definition;
  - `RunOptions` mode=test with mocks, offline mode (`TEST_UNMOCKED_EFFECT`), frozen clock and seed, and request capture;
  - automatic contract checks;
  - the assertion evaluator (16 ops);
  - `POST /workflows/{id}/tests/run` and the test-case CRUD routes inside the draft.
- B. API catalog:
  - a `api_specs` table plus migration;
  - OpenAPI 3.x import (paste, upload or URL; the URL is checked against the egress allowlist);
  - endpoint → contract and default mocks, including documented errors;
  - the HttpRequest `api_ref` param;
  - DB mocks from the table schema;
  - paste-one-example → contract and mock.
- C. Studio shell, built against the contract with fixture data:
  - the Tests tab, test editor, coverage grid, node badges and test bar;
  - the API catalog screen and the endpoint picker.

**Wave 2 (parallel)**
- D. Recipes per node type, "suggest tests" (boundaries from If/Switch conditions), and "Save as test" from a run.
- E. Scorecard:
  - branch coverage;
  - planted bugs, using 5 operators run in memory with no DB writes;
  - the 20-run repeatability check;
  - `score(workflow)`;
  - the toolkit functions and the node catalog generator;
  - locked tests and the publish/release block.
- F. Studio wiring to the real API, one-click mock fixes, and publish-blocked UI.

**Testers (separate agents)**
- G. Backend integration tests on real Postgres, plus `test_stage3a_api_journey.py` walking the demo.
- H. Playwright `16-tests-in-editor.spec.ts` on the real stack. Only this agent runs e2e.

**Lead:** review every diff, re-run checks, look at screenshots, send bugs to fix agents, then run the full gates and the demo, and report.

## Decisions I'm making (n8n has no test feature, so these are my calls)
1. The API catalog is per project. It accepts OpenAPI 3.0/3.1 in JSON or YAML; Swagger 2 is out.
2. Test runs are saved as executions with `mode="test"`. They're hidden from Runs and kept for 7 days.
3. Planted-bugs runs are in memory only and are never saved.
4. A test can target the whole workflow, one node, or a range.
5. Locking a test needs a typed "Approved by" name, like contract approval. Only approved tests block a publish.

## Done when
`HANDOFF-STAGE3.md` §6. Baselines: engine 906/1 skip/2 xfail, studio unit 155, tsc clean, lint ≤96, design:check clean, e2e 129.
