# Real Postgres + Gemini dashboard

This opt-in browser check makes billable real Gemini calls. It never uses the fake LLM.
It creates and removes an isolated engine database and an independent PostgreSQL cluster.
The external cluster requires SCRAM password authentication; the dashboard role has SELECT
access only. It does not connect to an existing external database.

Prerequisites: the engine Python environment, studio npm dependencies, Chromium, bubblewrap,
and Postgres server binaries. The isolated engine-test Postgres must be running on localhost:5433
with the existing integration-test credentials. Run only one pytest/Playwright process at a time.

From the repository root, with the ignored engine environment file configured:

```bash
set -a
. apps/workflow-engine/.env.local
set +a
# PG_BINDIR must contain initdb and pg_ctl; omit it if those tools are on PATH.
PG_BINDIR=/path/to/postgresql/bin apps/workflow-engine/venv/bin/python \
  apps/workflow-engine/tests/live/run_postgres_dashboard.py
```

`WORKFLOW_MODULE_GEN_MODEL` chooses the real Gemini model (default `gemini-3.5-flash`).
`CHROMIUM_PATH` optionally overrides `/usr/bin/chromium`.

The browser saves PG_USER, PG_PASSWORD and PG_DATABASE through the Secrets UI, in dev and
prod. It reloads the page to verify persistence, rotates the database password, confirms the
old password fails authentication, and replaces both environment values through the UI.
It then generates and refines a backend that calls `studio.nodes.postgres` with
`studio.secret` references. The generated UI calls the resulting typed client methods.
After a direct database update and insert, Refresh must show all four current rows and EUR;
the published app must show those same values. The test checks that secret values appear in
neither generated source nor application response bodies. Artifacts are local, under
`screenshots/postgres-dashboard/`, and must not be committed.

Generation's automatic read-only self-tests intentionally refuse external SQL. Their blocked
results are recorded separately; the saved route tests and preview/published calls must
actually execute against the external database and return 200. Do not loosen that policy to
make generation's self-tests green.

## Banking operations benchmark

For the larger multi-view app, use the same prerequisites and credentials, then run:

```bash
PG_BINDIR=/path/to/postgresql/bin apps/workflow-engine/venv/bin/python \
  apps/workflow-engine/tests/live/run_banking_app.py
```

The benchmark seeds 600 synthetic transactions, 40 accounts, eight branches, 100 exception
cases and 1,200 audit events across five related tables. Real Gemini generates six backend
routes and a React app with Overview, Transactions, Accounts and Exceptions views. Before UI
checks, saved routes are compared against independent database expectations, and successful
representative responses are applied as schemas to the typed client (no response values are
added to the model context). Browser checks
cover server pagination, status/region filters, search, an empty result, joined transaction
and audit details, account/case tables, publishing and refresh after a database change.
Artifacts are local under `screenshots/banking-app/`.

This is a functional app-generation benchmark with synthetic data, not a banking production
or load-test certification. It does not connect to a real Splunk instance. Splunk connector
onboarding, enterprise identity and per-user data access remain separate platform milestones.

For a diagnostic rerun, `BANKING_BACKEND_PROOF` may point at a previous local report to reuse its
real generated backend (only the ephemeral Postgres port is retargeted). `BANKING_UI_PROOF` may
point at a previous report to refine its real generated UI. `BANKING_REFINEMENT_FILE` optionally
supplies a focused refinement prompt. `BANKING_REPLAY_UI=1` with a UI proof skips additional AI
calls and verifies that exact generated source against a fresh real database, including publishing
and fresh-response recovery. The report marks source reuse/replay explicitly. No AI event stream
or application response is fabricated during replay.

Replay setup explicitly binds the retained app to the current project’s generated route IDs.
The preview bridge rejects apps without route bindings; replay does not bypass that policy.

## Three generated app ideas

With the same environment-variable and Postgres prerequisites, run:

```bash
PG_BINDIR=/path/to/postgresql/bin apps/workflow-engine/venv/bin/python \
  apps/workflow-engine/tests/live/run_app_ideas.py
```

This creates a fresh engine database and an independent synthetic-data Postgres cluster.
Real Gemini generates a backend and UI for each project through the browser:

- Storefront: product reads, atomic order creation and stock decrement, invalid quantity and
  unavailable-stock rejection, and competing requests for the last units. Preview and published
  checkout are checked against independent SQL queries. This does not take payments.
- Support desk: ticket reads/filtering, persisted resolution, invalid-status rejection and a
  missing-ticket response. The published app must retain the preview's update.
- Expense reporting: real grouped totals, department filtering, and Refresh after a direct
  database insert. The published app must display the updated dataset.

The database role has only the table-specific read/write privileges needed by these cases.
Credentials are saved through Secrets in each project. Actual successful responses provide
response schemas to the client generator; write-request schemas are explicitly configured.
The tests do not fabricate AI responses, application data, or successful writes. Each idea
reports independently, so a failure does not prevent exercising the remaining ideas.
Local reports and preview/published screenshots are under `screenshots/app-ideas/`.

For the redesigned retained banking showcase, run `BANKING_EXAMPLE=1 BANKING_DESIGN_PASS=1`
with `run_banking_app.py`. This mode needs no provider key and makes no AI calls; it uses real
Postgres and the real preview/published runtimes. See `examples/banking-operations/README.md`.


## Full banking app: generation, case writes, graph and publication

Run the banking benchmark with `BANKING_FULL_E2E=1` and the real Gemini key loaded
only into the process environment from the ignored engine `.env.local`:

```bash
BANKING_FULL_E2E=1 PG_BINDIR=/path/to/postgresql/bin \
  apps/workflow-engine/venv/bin/python apps/workflow-engine/tests/live/run_banking_app.py
```

This mode generates a fresh six-route backend through Studio, refines it through
Studio to add case updates and audit history, and generates the entire dashboard
through Create UI. It defaults to fresh generation and refuses the hand-designed
example mode. Diagnostic reuse of recorded generated source requires the explicit
`BANKING_FULL_RESUME=1` flag and is marked in the report. `BANKING_REPLAY_UI=1`
replays exact generated UI; omitting that flag with `BANKING_UI_PROOF` makes a real
Gemini Refine call. Preserve the initial generation report when iterating so the
original provider calls and subsequent source replay remain distinguishable. The separate synthetic database
role may read the banking data, update cases, and append case-action records; it
cannot write transaction amounts. Case updates and audit insertion use one atomic
parameterized SQL statement. Independently executed SQL checks verify browser writes,
invalid forms, read-after-write behavior, and persistence after published-page reload.
The native code map is checked against the exact generated backend. Publishing uses
the actual Studio publish dialog. The original banking checks also exercise all four
views, joins, pagination, server filters, chart variation, fresh database changes,
and an actual database-login outage with Retry recovery. Additional captures cover
light/dark and narrow screens. Artifacts are local in `screenshots/full-banking-app/`.
This is functional coverage of a synthetic banking app; it is not a live bank/Splunk
integration, load test, or production authorization certification.

The retained real-Gemini result can also be exercised without additional model calls:

```bash
BANKING_FULL_E2E=1 BANKING_CASEWORK_EXAMPLE=1 PG_BINDIR=/path/to/postgresql/bin \
  apps/workflow-engine/venv/bin/python apps/workflow-engine/tests/live/run_banking_app.py
```

See [the retained casework example](../../../../examples/banking-casework/README.md).
This replay creates a fresh real database and runs the browser checks against the
retained generated sources. Its report explicitly records that no provider calls
were made. During validation, the real Generate, Refine and Create UI flow and this
retained-source replay passed; the separate deterministic Studio suite passed all
51 tests. Initial generated chart, search and responsive problems required a real
Gemini UI refinement before the complete flow passed.

## Marketplace Trust & Payments (INTEGRATION-TEST-PLAN section 2 / 3B)

Opt-in and billable: real Gemini through the real browser UI, with backend state asserted through the
API after every step. It needs only the engine-test Postgres on `localhost:5433` (no external cluster).

```bash
cd apps/workflow-engine && set -a && . ./.env.local && set +a
flock /tmp/e2e.lock venv/bin/python tests/live/run_marketplace.py
```

`run_marketplace.py` builds an isolated stack (own `workflows_test_e2e_*` database, free ports, allow-listed
environment, `WORKFLOW_RUN_RETENTION=1000` because the default keeps only 100 runs), a local upstream HTTP
server (`/v1/balance`, `/v1/risk`) and a sitecustomize hook that counts every Gemini call the engine makes
(model, status, token counts only; never prompts or responses). `marketplace.cjs` plus `marketplace_steps.cjs`
drive Chromium with a strict policy: any console error/warning/pageerror, any unexpected 4xx/5xx, or any
response body containing a secret value fails the step.

Steps: project, secrets (write-only, per environment, host-bound), four tables, 200-row CSV import with column
mapping, hand-written `refunds.py` (CRUD template, approval threshold 422, stats via `studio.nodes.http_request`),
60 refunds through routes, `disputes.py` generated by Gemini then refined (Keep, Undo, redo), `customers.py` with
an allowed and a blocked egress host, "Refunds console" and "Disputes board" built in the app builder (create plus
chat refinements, API tester, version history), the "Refund triage" workflow (imported through the Workflows page,
real Gemini classify node, editor test run, webhook, deliberately failing input, scheduled workflow), a staging
environment, the release sheet (secret fixed inline, review, promote), publishing both apps, v2 release, API key
create/use/revoke, roll back to v1, about 300 backdated runs across 30 days (Runs view ranges, filters, Load
older, Overview grid), and every page in light and dark. Secret values are checked in generated source, app
bundles and HTML, every API surface, 400 run records, engine/Vite logs and all browser responses.

Per-step duration, Gemini calls, tokens, retries and pass/fail are in `screenshots/marketplace/report.json`
(local, uncommitted, with screenshots). A provider-side failure (quota, overload, malformed output) is retried at
most twice per step; retried steps are listed in the report and a step that only passed on a retry is flagged
`flagged_retry_only`. `MARKETPLACE_MAX_LLM` (default 95) stops the run before it exceeds the call budget.

Diagnostics: `MARKETPLACE_SERVE=1` keeps the stack up and prints the state file;
`MARKETPLACE_ONLY=s07,s08` / `MARKETPLACE_SKIP=...` select steps against it (steps are not idempotent: restart
for a clean slate); `MARKETPLACE_REUSE=1` re-verifies apps generated earlier on that stack without new calls.
