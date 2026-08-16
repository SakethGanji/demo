# UI regression tests

Browser-driven tests that pin the defects found by driving this UI by hand.
Every one of them corresponds to a real bug (see `../../AUDIT-FINDINGS-2.md`);
if one fails, that bug has come back.

## Running

Both servers must be up, and the API must be reachable with the seeded System
superuser:

```bash
# 1. API (from apps/analytics-service) — needs Postgres + MinIO
ACCELERATOR_DB_NAME=accelerator ACCELERATOR_DB_PASSWORD=accelerator \
ACCELERATOR_STORAGE_BACKEND=local ACCELERATOR_STORAGE_DIR=/tmp/accelerator \
ACCELERATOR_AUTH_ENABLED=true ACCELERATOR_PORT=8001 \
venv/bin/python -m uvicorn app.main:app --port 8001

# 2. UI
cd web && npm run dev

# 3. Tests (from web/)
npm run test:ui
```

Overridable via env: `UI_BASE` (default `http://localhost:5173`), `API_BASE`
(default `http://localhost:8001/api/v1`), `ADMIN_USER_ID`.

Exit code is non-zero if any test fails, so it drops straight into CI.

## Rules for adding tests here

1. **Never hardcode a pass.** Compute every assertion from the DOM or from a
   re-read of the API. A test that always passes is worse than no test — one of
   the bugs in this codebase survived a check that asserted `true`.
2. **Verify writes by re-reading**, not by seeing a success toast. Several bugs
   here showed a cheerful toast while the write had failed (or a failure toast
   while it had succeeded).
3. **Seed your own fixture** with known answers and push its id into `created`
   so the suite cleans up after itself. Don't depend on demo data.
4. **Scope selectors.** A bare `button:has-text("Delete")` matched the page
   header's delete-the-whole-dataset button before the row's — the first draft
   of test 1 deleted the fixture instead of the rule.

## What's covered

| # | Pins |
|---|---|
| 1 | 204 DELETE reported as a failure (hit all 10 delete call sites) |
| 2 | 422 validation errors carrying per-field reasons |
| 3 | Phantom dataset page for an unknown/cross-team id |
| 4 | Removing the last filter left the grid filtered with no way to clear |
| 5 | Cursor paging skipping/duplicating rows on a tied sort key |
| 6 | "Act as" showing a stale seat label |
| 7 | Catalog row click doing a full page reload |
| 8 | Duplicate aggregation alias producing a wrong grand total |
| 9 | Non-additive-only aggregate showing no total and no explanation |
| 10 | View edit silently dropping search / OR logic / extra sort keys |

## Not covered here

Accessibility, responsive layout, masking depth across every tab, TUS/resumable
upload, and behaviour at volume were audited separately — see
`../../AUDIT-FINDINGS-2.md`. They are worth adding here over time.
