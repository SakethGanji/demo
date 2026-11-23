# Meridian Operations: banking showcase

A read-only operations app with an AI-generated backend and a deliberately designed UI,
connected to real synthetic Postgres data:
Overview with KPIs and daily volume chart; searchable, server-filtered and paginated Transactions;
transaction details with account/branch joins, exception cases and audit events; Accounts and Exceptions.

The saved backend has six GET APIs. `banking_ops.py` is the generated module, with its ephemeral
connection host/port replaced by editable `DB_HOST` and `DB_PORT` constants and trailing whitespace
removed. Credential references are unchanged. `App.tsx` is the redesigned showcase UI: light/dark themes, transaction-volume and settlement
charts, regional portfolio exposure, priority queue, searchable/sortable accounts, exception
severity/search, CSV page export, and an accessible transaction drawer. Metrics come from the APIs;
no invented performance deltas or placeholder chart values are displayed.

To use the example in the studio:

1. Create a dedicated demo Postgres database and run `seed.sql` as its owner. For example,
   `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f examples/banking-operations/seed.sql`.
   The script fails if its tables already exist; it does not delete existing data.
2. Give a separate database login SELECT access to the five `bank_*` tables and schema usage.
3. Create a studio project. In Secrets, save PG_USER, PG_PASSWORD and PG_DATABASE for each environment
   you will use, binding them to the demo database host. Never put those values in prompts or source.
4. Set the backend module's non-secret DB_HOST/DB_PORT, then save it as `banking_ops` through the
   backend code editor. Test its routes against the seeded database and select **Use as schema**
   on representative successful responses, so the generated client has the actual response fields.
   For transaction detail, use TX-000600 as the id.
5. Create an app, place `App.tsx` in its code editor, and use the project's generated `./api/client`.
   The full opt-in benchmark instead generates both backend and UI through the browser.

For the repeatable real-provider benchmark, see
`apps/workflow-engine/tests/live/README.md`. Screenshots/report remain local under
`screenshots/banking-app/`.

This example deliberately uses synthetic data and public demo routes. It is not ready to expose
bank customer data. Enterprise identity, per-user and per-source authorization, approved connector
onboarding/schema discovery, query limits, audit retention, and a real Splunk integration are still
platform work. No live Splunk instance is simulated or claimed here.

Verified with real Gemini, real Postgres and Playwright in both preview and published form. The final
exact-source replay also checks fresh-response database recovery and actual rendered chart variation.
The last engine regression suite passed 307 tests. Replaying the source does not make additional AI calls.

## Rehearse the retained design

With the isolated-stack prerequisites from the live-test README:

```bash
BANKING_EXAMPLE=1 BANKING_DESIGN_PASS=1 PG_BINDIR=/path/to/postgresql/bin \
  apps/workflow-engine/venv/bin/python apps/workflow-engine/tests/live/run_banking_app.py
```

This needs no Gemini key: it loads the retained backend and UI, creates fresh synthetic Postgres
data, saves credentials through the UI, verifies preview and publication, then removes the test
stack. It also checks theme contrast, account search/sort, case filters, CSV download, drawer focus
and Escape, and overflow at 1024px and 390px. Screenshots are under `screenshots/banking-app/design-*`.
