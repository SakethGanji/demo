# Test cleanup: what to fix, merge and remove

This list comes from a review of every test file on 2026-10-24. For what each file does, see `TESTS.md`.

Port the tests first, then work through this list. **Before you delete a test, check that the behaviour it covers is still tested somewhere else.** If it isn't, move the missing cases into the surviving file. Never weaken or skip a test just to make it pass.

Paths are relative to `apps/workflow-engine/tests/` and `apps/workflow-studio/` unless a full path is given.

## 1. Fix first (silent gaps)

| Problem | Fix |
|---|---|
| `integration/test_zz_endpoint_coverage.py` never runs. It only runs when every integration file collects tests, and `integration/test_stage5_t1_support.py` has none, so the check always skips itself. Routes without tests go unreported. | Rename `test_stage5_t1_support.py` to a non-test name (e.g. `stage5_t1_support.py`) and update its imports. Then run the full integration suite once, and add tests for any routes the coverage check reports |
| When Postgres is down, 52 unit files skip and the run looks green | Make the PG fixtures fail when the DB is unreachable. Add an opt-out env var for machines without Postgres |
| The fake-LLM integration engine sets `WF_CHAT_BUILDER=legacy` (`integration/conftest.py`), so the one-shot builders you actually use have unit tests only | Add one fake-LLM integration test for each one-shot path: workflow chat build, workflow patch edit, and the one-shot app build. Make each go through the HTTP API |
| Sandbox tests without a bwrap gate fail instead of skipping when bwrap is missing: `integration/test_ui_run_draft.py`, `test_ui_generate.py`, `test_review_g3_broker_release.py`, `test_broker_run_workflow_http.py` | Add the same `bwrap` skip gate the other sandbox tests use |
| `routes/test_project_plan_routes.py` has no conftest of its own | Give `tests/routes/` a conftest, or move the file into `integration/` |

## 2. Remove (dead)

- `tests/engine/` and `tests/services/` hold only stale `__pycache__`. Delete them.
- The strict xfail in `unit/test_stage8_t1_runtime_edges.py`. Its own reason says it can never pass. Delete that one test.
- If you don't use screenshot captures: `apps/workflow-studio/e2e/capture.spec.ts`, `capture.config.ts` and `99-showcase.spec.ts`. They may still expect the old canvas editor.

## 3. Merge into the newer file, then delete (duplicates)

| Old file | Covered by |
|---|---|
| `integration/test_stage2_checker.py` | `integration/test_stage2_contracts_checker.py` |
| `integration/test_stage5_tasks_api.py`, `integration/test_stage5_interfaces_smoke.py` | the Stage 5 T1 files (`integration/test_stage5_t1_*`) |
| `integration/test_stage8_b4_extract.py`, `test_stage8_b10_inline.py`, `test_stage8_b4_releases.py`, `test_stage8_b5_templates_http.py` | their Stage 8 T1 versions |
| `integration/test_phase2_secrets.py` | journey j23 (`integration/journeys/`) |
| `integration/test_phase3_public_run.py` | journey j09 |
| `unit/test_stage3a_node_catalog.py` | the other node-catalog unit tests |
| `unit/test_mutations_approval.py` | `unit/test_stage3a_mutations.py` (it repeats a case from there) |

There are also small repeats inside files. Keep one copy of each:
- "every node declares an effect" is checked in 3 files;
- the Approval test-mode check appears 4 times.

**UI:**
- `e2e/tests/11a`–`11d` overlap specs 01, 06, 08 and 12. Keep the design checks, and drop the flows they repeat.
- `e2e/tests/45-notebook-edge.spec.ts` repeats some flows from 41–44. Keep its edge cases and drop the repeats.
- Legacy editor specs 14–21 overlap the glass specs 31–35 in about 7 flows. See §5.

## 4. Strengthen (weak)

- **Tests with no assertion:**
  - 7 `*_passes` tests in the Mongo guard file (`grep -l "mongo" unit/*.py`);
  - 2 in `unit/test_stage3b_fixes.py`;
  - 2 in `unit/test_wf_ai.py`.

  Make each one assert what it is meant to prove.
- **Too much mocking of private methods:** `unit/test_app_builder_f3*`, `unit/test_builder_fix_f3_*`, the Gemini round-trip test and the LLM truncation test. Test through the public function instead.
- **Thin integration tests:** `integration/test_char_ai_misc.py`, the app description test and the code map test check little more than the status code. Assert the body, and re-read the result.
- **Setup calls that don't check their result** (about 30 in integration tests): check the status code at once, so a failure shows up where it happens.
- **Skips that hide data:** 175 parameterised skips in `unit/test_stage8_t1_extract_props.py` hide seeds that already have a checker error. Fix those seeds, or list them explicitly.
- **Hard-coded scratchpad paths:** 113 e2e `shot()` calls write to them. Point them at the Playwright output directory.

## 5. Remove if the feature is cut in your copy

| Feature | Tests that go with it |
|---|---|
| Knowledge | about 15 unit files, 5 integration files (`grep -l "knowledge\|kg_" unit/*.py integration/*.py`), 9 UI unit files (`src/features/knowledge/**`, including `layout3d.test.ts`), e2e 23–24 |
| Interfaces | about 12 unit files, plus 4 Stage 5 integration files |
| Legacy editor (`?legacy=1`) | e2e 14–21, and `e2e/fixtures/legacy.ts` |
| App builder visual editing | 10 UI unit files under `src/features/app-builder/visual/` (behind `VITE_VISUAL_EDIT`) |

That is about 45–55 files: roughly 30 backend and 20 UI.

## 6. Already failing at baseline

For each one, either fix the code or the test, or delete the test if its feature is gone:
- `unit/test_stage8_b5_install_pg.py` (30 tests): template contract errors about `created_at`/`updated_at`. The templates are probably wrong, not the test.
- `unit/test_kg_extract.py`: knowledge. Goes with §5.
- `integration/test_stage3a_runner.py::test_tests_and_defaults_survive_publish_and_version_restore`: the publish gate returns 422 because "Log escalated" is missing `created_at`/`updated_at`. Same cause as the first item.
- `src/features/knowledge/lib/layout3d.test.ts` (2 tests): knowledge.
- Legacy e2e 16–20 (23 failures): legacy editor. Goes with §5.
- The rest of the 130 engine baseline failures are listed by file in `TESTS.md`.

## 7. Speed (optional, after the cleanup)

| Change | Expected gain |
|---|---|
| Split into a fast lane (pure unit, no Postgres) and a slow lane (PG, integration, e2e) with pytest markers and npm scripts | fast lane in about 1–2 min |
| Engine tests in parallel (pytest-xdist), with one database per worker | engine suite about 21 → 5–7 min |
| Share engine boots across a module: there are about 45 boots and 25 create-and-migrate cycles today | about 3–6 min off integration |
| Replace fixed sleeps with polling: up to 45 s in the j22 scheduler test, plus sleeps in Stage 5, notebook and review_g5 | about 1–2 min |
| e2e with several Playwright workers, one stack per worker; light and dark only where the spec is about theme | e2e about 20 → 6–8 min |

## Also missing (no test today)

- **Integration:** datasets, schedules, project files and changes, the builder gate endpoints, `GET /plan`.
- **Unit:** `notebook_keep`, `public_apps`, `webhook_service`, `tsx_parser`, and the nodes Set, If, item lists, AI memory, MongoDB and Neo4j.
- **e2e:** knowledge, datasets, `/plan`, `/build`, `/code`, templates, connections. e2e runs on Chromium only.
