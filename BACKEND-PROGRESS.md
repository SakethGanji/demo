# BACKEND-PROGRESS — implementation log for BACKEND-DESIGN.md phases 1–9

Branch `revert-to-5684ba5` (local commits only, never pushed). **Status: phases 1–9 done; all
journeys J1–J17 implemented and passing.**

## How to run tests
```bash
cd apps/workflow-engine
venv/bin/python -m pytest tests -q                       # everything (~45 s)
venv/bin/python -m pytest tests/integration/journeys -q  # UI journeys J1–J17
venv/bin/python -m pytest tests/integration -q -k j14    # one journey
KEEP_TEST_DB=1 venv/bin/python -m pytest tests/integration -x
```
Harness (`tests/integration/conftest.py`): creates `workflows_test_<hex>` on localhost:5433
(override WORKFLOW_DB_HOST/PORT/USER/PASSWORD), applies all migrations with the real migrator,
starts `uvicorn src.main:app` on a free port (SEED=0), drops the DB afterwards. Refuses the
`workflows` DB and port 8000. Engine logs: `tests/integration/.logs/`. Run one pytest at a time.
`test_zz_endpoint_coverage.py` fails if any studio-used or new route is never requested (only on a
full integration run; filtered runs skip it). Preconditions: `esbuild` on PATH (publish steps),
`bwrap` (sandbox journeys) — both present here; no LLM key (AI-chat steps assert the SSE contract).

## Status
| Step | Tests (total) | Commit |
|---|---|---|
| 0 harness + characterization | 51 (28 unit + 23 char) | 9326732 |
| 1 projects | 55 | 79e7c52 |
| 2 secrets | 58 | 087934a |
| 3 public app run | 61 | 61a3d86 |
| 4 broker + SDK | 69 | 85ab7aa |
| UI gaps triage (design §10/§11) + G-items for phases 1–4 | 69 | bc428b3 |
| journeys J2–J10 | 77 + 3 xfail | 6c73bc4 |
| 5 API section | 83 + 2 xfail | 3ebe1bb |
| 6 app ↔ API | 90 + 1 xfail | 0c118df |
| 7 data tables | 92 + 1 xfail | 5eeecaf |
| 8 sandbox / python | 96 | 48ada79 |
| 9 releases | 98 | 156a49f |
| endpoint coverage check | 108 | 1645ff6 |

Final: **108 passed** = 28 original unit + 6 new unit + 32 characterization + 24 journeys + 18 phase tests.

## Journeys (UI-INTEGRATION.md §6)
| Journey | Result | Gate notes |
|---|---|---|
| J1 flagship | pass | esbuild steps (28, 38–39) ran |
| J2 legacy editor | pass | |
| J3 legacy builder | pass (2 tests) | publish steps ran (esbuild) |
| J4 variables modal | pass | |
| J5 API tester + AI chats | pass (2 tests) | [LLM] steps assert 200 SSE, not 422 |
| J6 projects home | pass | |
| J7 settings/moves/delete | pass | |
| J8 secrets | pass | |
| J9 public app run | pass | esbuild; adjusted per G22 + body wrapper (design §11) |
| J10 broker policy | pass | `statusCode` per design §11 |
| J11 router semantics | pass | |
| J12 API keys | pass | |
| J13 validate_request | pass | |
| J14 app ↔ API | pass (3 tests) | [LLM] step 14 asserts contract; step 15 esbuild ran |
| J15 data | pass (+ studio.db test) | |
| J16 python modules | pass (+ 2 sandbox tests) | bwrap ran (escape probes, memory/time limits) |
| J17 releases | pass | |
No journey is skipped on this machine.

## Contract gaps (design §10)
34 triaged: 30 accept, 3 accept-modified (G4, G22, G30), 1 accept-later (G25), 0 rejected.
All accepted items are implemented except G17's optional unsaved-files scan (deferred).

## Deviations / known limits
See BACKEND-DESIGN.md §11 (journey reconciliations) and §12 (as-built notes). Highlights:
studio `"default"` env maps to the project default env; extra execution-mode migration; Runs
history capped at 100 executions (pre-existing); published-app workflow runs are not release-pinned;
agent PTC/calculator still in-process; studio code was not changed (the preview iframe still needs
to send the preview token for `/api/p/` — UI work per UI-INTEGRATION §4.7).

## Not done / follow-ups
- The :8000 dev engine still runs the old code against an un-migrated `workflows` DB. Migrations are
  backward compatible; apply with `venv/bin/python -m src.db.migrate apply` (env pointing at :5433)
  and restart the engine — only with the owner's OK.
- Studio typecheck (`npx tsc -b --noEmit`) reports 53 errors that pre-date this work (studio files are
  unchanged from 5684ba5).

## Review fixes (UI-DESIGN-REVIEW.md, 2026-10-15)
Baseline before: **108 passed**. Tests live in `tests/integration/test_review_g*_*.py` (+ unit tests in
`tests/unit/test_review_*.py`); each was written first and seen failing. Design deltas: BACKEND-DESIGN §13.

| Group | Findings | Tests (total) | Commit |
|---|---|---|---|
| 1 security + dry runs | D1, D2, D4, D7, D9, C9, C1, D17 (partial) | 123 | (see git log) |
| 2 modules + routes | B4, C7, C8, G-UI-10, D16, G-UI-4, B1, D11, D13, D15, G-UI-5, C14 | 136 | (see git log) |
| 3 broker + release pinning | D5, D8 | 140 | (see git log) |
| 3b align with UI-DESIGN §9 | D11 kind, C7 conflicts[], B4 base_sha/rename, D5 virtual_tables | 141 | (see git log) |
| 4 runs | C2, C3, D6, C13 | 148 | (see git log) |
| 5a builder + usage + publish | C5, C6, C11, D14, C4 | 154 | (see git log) |
