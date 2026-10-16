# Meridian Operations: generated case-management dashboard

A real Gemini-generated backend and React UI, refined through Studio and tested
against a separate Postgres database containing synthetic banking data. The UI is
retained exactly as emitted by the final Refine call. The backend's ephemeral
connection port is normalized to 5432; its query/validation logic is unchanged.

- 600 transactions, 40 accounts, eight branches, 100 cases, 1,200 transaction events.
- Eight API routes; 13 Python functions and 33 local call references.
- Overview with real KPIs and 15-day chart; server-filtered/paginated Transactions;
  account/branch joins; transaction detail with audit events; case management.
- Case owner/state/note validation; an atomic case update and append-only action
  record; visible read-after-write history. Transaction amounts remain read-only.
- Light/dark themes, narrow-screen navigation, errors and Retry recovery.

## Repeat the full app test without additional AI calls

Use the prerequisites in `apps/workflow-engine/tests/live/README.md`: Python/Studio
packages, Chromium, bubblewrap, PostgreSQL server binaries and the test Postgres on
localhost:5433. Run one pytest/Playwright process at a time.

From the repository root:

```bash
BANKING_FULL_E2E=1 BANKING_CASEWORK_EXAMPLE=1 PG_BINDIR=/path/to/postgresql/bin \
  apps/workflow-engine/venv/bin/python apps/workflow-engine/tests/live/run_banking_app.py
```

The harness creates fresh engine and external databases, saves generated database
credentials through Secrets in dev/prod, retargets only the connection port, loads
these retained sources, checks reads and writes in preview and published form,
and removes its isolated services/databases. Source replay is explicitly labeled;
it does not fabricate model responses or application data.

The fresh-generation alternative is `BANKING_FULL_E2E=1` without the casework-example
flag, with the real Gemini key loaded into the process environment. That run makes
billable Generate, Refine, and Create UI calls. Generated UI may need further Refine
iterations; the benchmark fails rather than accepting broken controls or fake data.

## Load it manually in Studio

1. In a dedicated empty Postgres database, run `seed.sql`. It never drops existing data.
2. Give the app's dedicated role schema usage and SELECT on the six `bank_*` tables,
   UPDATE on `bank_cases`, INSERT on `bank_case_actions`, and USAGE/SELECT on
   `bank_case_actions_id_seq`. Give no write access to transactions/accounts.
3. Save PG_USER, PG_PASSWORD and PG_DATABASE through Secrets with the actual host
   binding for each environment. Edit the non-secret host/port in `_connection`.
4. Save `banking_ops.py` as a backend module. Apply representative successful response
   schemas to its operations; configure opsUpdateCase's body as required string fields
   `state` (open/in_review/resolved), `owner`, and `note`.
5. Create an app bound to all eight operations and put `App.tsx` in its editor. It uses
   the project's generated `./api/client`; do not replace that client with a fixture.

The test independently queries SQL to verify counts, rejected writes, case changes,
audit inserts, and unchanged transaction values. It also disables/re-enables the
actual database role to test error recovery. Screenshots/reports remain local in
`screenshots/full-banking-app/`. These are synthetic public demo routes, not a live
bank or Splunk integration, production authorization audit, or load-test result.
