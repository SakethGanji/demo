# Handoff — the service is UI-ready (2026-08-09, overnight)

This session took the analytics-service from "API believed solid" to "proven
UI-ready, with a clean reference front end". Read this first.

## TL;DR

- **The API is done and hardened.** Two more audit passes (14 fixes earlier + a
  cross-cutting hardening pass) closed every verified defect a UI would hit. Full
  backend suite is green.
- **There is a working, clean reference UI** at `apps/analytics-service/web/`,
  built on a typed client generated from the OpenAPI spec, covering every flow.
- **It's proven end-to-end in a real browser** (Playwright): every page and tab
  renders and the interactive flows work with **zero console/page errors**.
- **`UI-INTEGRATION-GUIDE.md`** documents the full contract so you (or another
  front end) can build against it cleanly.
- **Six PII leak paths were found and closed** in the final audit round (a viewer
  or editor could read raw sensitive values through transform preview/compile,
  join preview, profile runs, profile drift, and saved-definition runs). Two were
  reachable straight from the UI. All are gated and pinned by tests — see
  `AUDIT-FINDINGS-2.md`.
- **A committed UI regression suite** (`web/tests`, `npm run test:ui`) pins every
  defect this work uncovered, so they cannot silently return.

## How to run it

Postgres (`analytics-pg`) and MinIO (`analytics-minio`) are Docker containers;
start them if they're down (`docker start analytics-pg analytics-minio`).

```bash
cd apps/analytics-service
# API (base 'accelerator' DB is migrated; local storage):
ACCELERATOR_DB_NAME=accelerator ACCELERATOR_DB_PASSWORD=accelerator \
ACCELERATOR_STORAGE_BACKEND=local ACCELERATOR_STORAGE_DIR=/tmp/accelerator \
ACCELERATOR_AUTH_ENABLED=true ACCELERATOR_PORT=8001 \
venv/bin/python -m uvicorn app.main:app --port 8001

cd web && npm install && npm run dev     # http://localhost:5173
```

Two demo datasets are seeded in the `accelerator` DB: **Regional Orders.csv**
(120 rows; `email` marked confidential for the masking demo; a not-null rule) and
**CRM.xlsx** (Customers + Orders sheets with an FK rule → relationships/joins).
Re-seed anytime with `venv/bin/python scripts/seed_demo.py` (see "Resetting the
demo" below), or just upload your own.

## What the UI covers (each is a real, wired flow)

Catalog · Upload · Storage · Admin · and a Dataset workspace:
Overview (health scorecard) · Explore (filter / sort / cursor-paging / column
stats + **data-dictionary editing incl. marking a column sensitive**) · Quality
(rules + validation runs) · Versions (tags, promote, workbook / schema / row
diff) · Analytics (aggregate + pivot builders → charts, save/run/publish) ·
Relationships (seed / discover / steward inbox / join forecast → execute →
publish) · Transform (pipeline build → compile/preview → run → publish) · Library
(saved views, read-only SQL console, lineage graph).

**Dataset management**: rename / edit / deprecate / **delete** / favorite from the
dataset header; **upload a new version** to an existing dataset from Upload.

**Admin console** (`/admin`): teams & members (add/role/remove), webhooks
(create with one-time secret reveal + delivery history), audit log, storage &
retention (usage + admin-only GC).

Top-bar **"Act as"** switches identity (`X-User-Id`/`X-Team-Id`) so you can see
RBAC refusals and column masking from a viewer or another team.

## What changed in the API this session (all committed, with tests)

Second cross-cutting audit (3 agents) → fixed:
- numeric filter operators 500'd on non-numeric input → typed 400
  `invalid-filter-value`;
- explorer `/query` 500'd on an execution-time DuckDB error (e.g. bad regex) →
  400 `invalid-query`;
- `/aggregate` `limit=-1` 500'd → floored to an empty page;
- pivot `include_row_totals` mixed units for percentage displays → row totals are
  value-display only;
- profiling histogram dropped empty bins → every bin emitted;
- `GET /samples/{filename}` ignored the artifact's media_type → serves the real
  content type;
- data_accelerator collection lists reported `limit:0` on empty → floored at 1.

(The earlier session fixed 14 more: masking re-leak via derived artifacts, cursor
paging data loss, relationship provenance, multi-sheet size accounting, chart
masked-axis collapse, diff-across-rename, documentation-coverage, transform
save-time validation, and more — see `AUDIT-FINDINGS-2.md`.)

## Decisions & caveats worth knowing

- **`GET /datasets/{id}` returns the dataset's DATA preview** (dataset_id, sheets,
  preview), **not** its catalog metadata — there is no single-dataset metadata
  endpoint returning `DatasetInfo`. The UI resolves name/version from the catalog
  list (`GET /datasets`). If you want a `GET /datasets/{id}` that returns the
  resource metadata, that's a small backend addition worth making.
- **Known minor, harmless inconsistencies** (documented in the guide, not fixed to
  avoid churning pinned behavior): most `DELETE`s are 204 but dataset/tag deletes
  return a 200 body; six data_accelerator POSTs carry a vestigial always-true
  `success`; timestamps are Postgres-`::text` except `JobOut` (ISO-8601) — JS
  `Date` parses both.
- **Two audit leads were refuted, not "fixed"** (deliberate, pinned behavior):
  publishing one analytics run to multiple targets, and aggregate `group_count`
  meaning the returned page's group count.
- The UI is a **reference** — pragmatic forms, not every knob. It's a clean base
  to extend or a spec to reimplement; the design system + typed client are the
  reusable parts.

## Testing

Two suites, both green, both runnable by anyone:

```bash
# API (2692 tests, needs Postgres + MinIO + worker DBs)
scripts/provision_test_workers.sh 1
scripts/test_worker.sh w1 tests/ -q

# UI regressions (11 browser-driven tests; needs the API + `npm run dev` up)
cd web && npm run test:ui
```

`web/tests/` pins every defect that actually happened here — the 204-delete
lie, the phantom dataset page, tied-key paging completeness, the view-editor
rewrite, the duplicate-alias wrong total, and the six sensitive-data leaks.
The rules for adding to it are in `web/tests/README.md`; the short version is
**never hardcode a pass, verify writes by re-reading, and create your own
fixtures** (the suite caught itself depending on ambient state twice).

## Resetting the demo

The database accumulates fixtures fast when you are testing. To get back to a
clean two-dataset demo:

```bash
docker exec analytics-pg psql -U accelerator -d postgres -q \
  -c "DROP DATABASE IF EXISTS accelerator WITH (FORCE);" \
  -c "CREATE DATABASE accelerator OWNER accelerator;"
venv/bin/python -m app.infra.db.postgres.migrate apply
rm -rf /tmp/accelerator && mkdir -p /tmp/accelerator      # local blobs
# start the API, then:
venv/bin/python scripts/seed_demo.py
```

## Where things are

- API fixes: across `app/features/**` and `app/shared/**`; tests in `tests/`.
- Reference UI: `web/` (see `web/README.md`).
- Contract for UI devs: `UI-INTEGRATION-GUIDE.md`.
- Frozen spec: `openapi.json` (client generated from it).
- Audit record: `AUDIT-FINDINGS-2.md`.
