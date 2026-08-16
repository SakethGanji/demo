# Browser tests — the datasets surface

41 tests driving the real app against a real analytics-service. No mocks: every
bug this suite exists to catch lives in the seam between the UI and the service,
and a mocked test cannot see that seam.

## Running

Both servers must be up, and the API must be reachable with the seeded System
superuser.

```bash
# 1. infra
docker start analytics-pg analytics-minio

# 2. the API (from apps/analytics-service)
ACCELERATOR_DB_NAME=accelerator ACCELERATOR_DB_PASSWORD=accelerator \
ACCELERATOR_STORAGE_BACKEND=local ACCELERATOR_STORAGE_DIR=/tmp/accelerator \
ACCELERATOR_AUTH_ENABLED=true ACCELERATOR_PORT=8001 \
venv/bin/python -m uvicorn app.main:app --port 8001

# 3. the tests (from apps/workflow-studio) — starts Vite itself if needed
npm run test:ui
```

`global-setup.ts` checks the API first and fails with the command to fix it,
rather than letting the suite produce forty identical timeouts.

Useful variants: `npm run test:ui:headed`, `npx playwright test catalog.spec.ts`,
`npx playwright test -g "cursor paging"`, `npm run test:ui:report`.

Env overrides: `UI_BASE`, `API_BASE`, `ADMIN_USER_ID`, `ANALYTICS_PYTHON`.

**Drive the studio at `localhost` exactly.** It talks cross-origin to `:8001` and
picks its backend by *hostname*; any other host falls through to the empty
default and every API call 404s against Vite instead.

## The rules

Four are inherited, each learned from a bug that shipped:

1. **Never hardcode a pass.** Compute every assertion from the DOM or from a
   fresh API read. A test that always passes is worse than no test.
2. **Verify writes by re-reading.** A toast is not evidence — ten delete call
   sites once reported failure while succeeding.
3. **Seed your own fixture** with known answers. Don't depend on demo data.
4. **Scope selectors.** A bare `Delete` once matched the page-level control
   before the row's, and deleted the fixture instead of the rule.

This suite adds a fifth:

5. **Console errors fail the test.** The previous harness collected page errors
   and never asserted on them, so "zero console errors" was verified by hand
   exactly once. Here an unexpected `console.error`, `pageerror` or HTTP 5xx
   fails the test. When an error *is* the expected behaviour — the 409 from a
   refused promote, the 403 from a refused profile — allow it explicitly with
   `h.allowError(/409 \(Conflict\)/)` and say why.

## Isolation

Tests run in parallel, so they must not be able to break each other.

- **Fixtures are per-test and uniquely named.** `h.seed()` creates a dataset
  named `uitest-<prefix>-<time>-<n>.csv`; assertions scope themselves by
  searching for that name or a shared token.
- **Cleanup is a prefix sweep at both ends of the run**, not per test. Deleting
  a fixture the moment its test ended meant one worker destroyed datasets while
  another worker's browser had them listed, and the second failed on a 404 that
  had nothing to do with what it was testing. The sweep also tidies after a
  crashed run, which per-test cleanup never did.
- **The browser has no internet.** `index.html` pulls fonts from Google, so runs
  used to fail whenever `fonts.gstatic.com` hiccuped. The harness fulfils those
  requests with empty CSS.
- **Deep-link to your own dataset** (`/data?dataset=<id>`) rather than letting
  the page fall back to whichever dataset happens to be first.

## What is covered

| File | Covers |
|---|---|
| `catalog.spec.ts` | listing, search, server-side filters, sorting the whole catalog, paging every row exactly once, client-side routing, unknown-id handling, rail search not moving the selection |
| `workspace.spec.ts` | grid contents vs the API, cursor paging with tied sort keys, paging backwards, version switching, multi-sheet selection, dataset switching |
| `writes.spec.ts` | upload (new dataset and new version), metadata PATCH, rule create/toggle/delete, validation, tags, promote's quality gate, the column dictionary |
| `lenses.spec.ts` | the grid staying loaded across lenses, profiling + health, relationship seeding, schema-only compile, artifacts + retention, version history + tags, masked-column reporting, the restricted state |
| `security.spec.ts` | masking for a viewer vs an admin, seat switching through the UI, datasets with nothing sensitive, viewer-safe schema compile, RBAC write refusals, 404-not-403 existence hiding |

## Not covered

Honest gaps, so nobody reads a green run as more than it is:

- **Accessibility** — no axe pass, no keyboard-only traversal.
- **Responsive layout** — one 1440×1000 viewport; the page is a fixed
  three-column layout and has not been tested narrow.
- **TUS / resumable upload** — only the single-shot multipart path is exercised.
- **Behaviour at volume** — largest fixture is 120 rows and 26 datasets.
- **Dark mode** — the viz tokens have validated dark values, but no test asserts
  on them.
- **Cross-team isolation** — the outsider seat is a viewer of the *same* team;
  a genuinely foreign team is not constructed.

## Fixture helpers

`h.seed(rows, prefix)` — a CSV-backed dataset, sheet `data`.
`h.seedWorkbook({Sheet: rows}, prefix)` — a real multi-sheet `.xlsx`, built with
the analytics-service venv's openpyxl (multi-sheet behaviour cannot be
exercised with a CSV, and this beats adding a spreadsheet dependency).
`h.addVersion(id, rows)` — another version of an existing dataset.
`h.markSensitive(id, column)` — the switch that turns masking on.
`h.viewer()` — a viewer seat in the Default team, created if absent. The
migrations seed only the System owner, so there is no viewer out of the box.
`h.asSeat(page, userId)` — load the app as that seat; call before navigating.
