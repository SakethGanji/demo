# Handoff: native code map and full banking E2E

Updated 2026-10-16. This is a short supplement for another session to merge into the
main handoff later. Older UI handoffs describe earlier states; do not treat their
T22/T24 failures or unrun specs as the latest validation status.

## Repository and commits

- Repo: `SakethGanji/demo`, working directory `/workspace/demo`.
- Branch: `revert-to-5684ba5`. Do not touch `clean-main`.
- `ee99ac5`: native source-derived backend map integrated into Studio.
- `9837608`: expanded banking E2E and retained real-Gemini-generated casework app.
- Both commits pushed to origin. This handoff is documentation only.

## Where the implementation lives

| Area | Location |
| --- | --- |
| Python engine | `apps/workflow-engine/` |
| React/Vite Studio | `apps/workflow-studio/` |
| Separate analytics service | `apps/analytics-service/` |
| Backend editor integration | `apps/workflow-studio/src/features/backend/BackendView.tsx` |
| Graph UI, layout, filtering and requests | `apps/workflow-studio/src/features/backend/code-map/` |
| Graph API types | `apps/workflow-studio/src/shared/api/codeMapTypes.ts` |
| Source analyzer | `apps/workflow-engine/src/services/code_map.py` |
| Module/map API routes | `apps/workflow-engine/src/routes/modules.py` |
| Graph scope, limits and commands | `apps/workflow-studio/src/features/backend/code-map/README.md` |
| Retained generated dashboard + backend + seed | `examples/banking-casework/` |
| Earlier designed banking showcase | `examples/banking-operations/` (different example) |
| Live E2E instructions | `apps/workflow-engine/tests/live/README.md` |
| Isolated banking runner | `apps/workflow-engine/tests/live/run_banking_app.py` |
| Browser generation/read/publish checks | `apps/workflow-engine/tests/live/banking_app.cjs` |
| Additional case-write/mobile/theme checks | `apps/workflow-engine/tests/live/full_banking_checks.cjs` |
| Database seed and role setup | `apps/workflow-engine/tests/live/banking_db.py` |
| Studio browser suite | `apps/workflow-studio/e2e/` |

## Latest verified results

- Complete deterministic Studio Playwright suite: **51 passed**, approximately five
  minutes. Includes builder T22/T24, publish/release/settings, workflow canvas,
  responsive behavior and native code map. Its model responses are fixtures.
- Separately exercised real Gemini Generate, backend Refine, Create UI and UI Refine
  against real Postgres. Initial chart/search/mobile issues required UI refinement;
  the final generated app passed the expanded checks.
- Final retained-source replay passed against another fresh real database with no
  additional model calls. Replay and provider-call reports are labeled separately.
- Synthetic dataset: 600 transactions, 40 accounts, eight branches, 100 cases and
  1,200 transaction events. Final backend: eight routes, 13 functions, 33 local call
  relationships. Final generated React UI: 1,087 lines.
- Checked saved secrets in dev/prod, SQL-backed KPIs/charts, server filtering/search,
  pagination, joins/drilldowns, validated case updates and atomic audit inserts.
  Independent SQL verified writes, audit counts and unchanged transaction values.
- Repeated reads/writes on the published app, checked reload persistence, light/dark
  and 390/1024-width layouts, escaped note text, direct database changes with Refresh,
  and a real database-login outage followed by Retry recovery. No browser runtime errors.
- Graph checked against the actual generated backend. It derives from current source,
  including drafts; supports Visual/Split/Code, layout, hover/pins, filters and source
  navigation. It is a conservative single-module syntax map, not an execution trace
  or editable business-workflow graph. See its README for unsupported constructs.
- Earlier graph-specific validation: 17 engine tests and 37 Studio unit tests passed.
  This is not a claim that every repository test or production scenario was tested.

## Re-run

One pytest or Playwright process at a time. From `apps/workflow-studio`:

```bash
npx playwright test -c e2e
```

From the repo root, replay the retained generated app with a fresh isolated database:

```bash
BANKING_FULL_E2E=1 BANKING_CASEWORK_EXAMPLE=1 \
PG_BINDIR=/workspace/.setup/pg/usr/lib/postgresql/17/bin \
/workspace/.venvs/demo/bin/python apps/workflow-engine/tests/live/run_banking_app.py
```

Paths above are specific to this cloud workspace. For prerequisites and portable
commands see the live-test and example READMEs. For fresh billable Gemini generation,
remove `BANKING_CASEWORK_EXAMPLE` and load the provider key only into the process
environment from ignored `apps/workflow-engine/.env.local`. Never print or commit it.
Fresh model output may require refinement; the test should fail rather than hide bugs.
The harness creates and cleans up its own engine/database/browser services.

## Screenshots and evidence (local, not committed)

`screenshots/full-banking-app/` contains:

- `published-overview.png`, `published-dark.png`
- `published-responsive-390.png`, `published-responsive-1024.png`
- `preview-detail.png`, `published-detail.png`
- `preview-case-updated.png`, `published-case-updated.png`
- `02-native-code-map.png`, `00-saved-secrets.png`
- `initial-generation-report.json`: original real-provider generation attempt.
- `refined-generation-report.json`: real UI refinement and passing app run.
- `report.json`: latest passing exact-source replay, explicitly no provider calls.

Additional graph captures: `screenshots/ui-built/backend-code-map-*.png`.
These are currently untracked local artifacts; another machine needs a rerun or an
explicit artifact transfer. Avoid blanket `git add .`.

## Local runtime / next-session cautions

At validation time primary dev services were engine `:8000`, Studio `:5173` and
analytics `:8001`; check that they are still running before relying on these ports.
Workspace startup helper: `/workspace/.setup/start-product-engine.py`.
The user authorized the earlier dev database reset; migrations were applied then.
Do not reset unrelated databases just to inspect the example.

The richer future dashboard designs are not implemented examples yet. Current
validation uses synthetic banking data, not a live bank or Splunk connection, and
is not a load test or production authorization certification. Existing auth/history
concerns in older handoffs are not resolved by these E2E passes. No dataset/file
management expansion was added in this work; the user deferred that scope.

Ask the user before spawning audit agents. Preserve ignored keys. Do not weaken
assertions to get green. No application code changed in the final validation commit;
it added test coverage and retained generated example sources.
