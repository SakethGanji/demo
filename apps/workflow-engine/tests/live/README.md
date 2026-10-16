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
