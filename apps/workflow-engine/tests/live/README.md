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
