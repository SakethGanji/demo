# Analytics Service — Handoff (2026-08-03)

## Current state

**Branch:** `main`, everything below is UNCOMMITTED (staged deletions + unstaged mods + untracked new files).
First action for a new session: commit this work on a feature branch (suggested split:
migrations / auth+RBAC / audit / api envelopes / data-plane RBAC wiring / tests).

**What was just built (all tested, 20/20 passing):**
- POC header auth: `X-User-Id` names an active user; no passwords/tokens. Swap point
  for real auth is `app/features/auth/deps.py:get_principal` only.
- Team-scoped RBAC: `viewer < editor < admin < owner` (`auth/permissions.py` matrix),
  `team_members` table. Superusers bypass. Cross-team datasets 404 (existence hidden);
  in-team permission failures 403. Admins can't grant owner; last owner can't be removed.
- Append-only `audit_log` (DB trigger forbids UPDATE/DELETE) + `/audit` (superuser-only).
- Uniform API layer: `/api/v1`, `Page` envelope, problem+json errors, X-Request-Id.
- `prompt_lab` feature + bundled datasets DELETED (now a standalone service).
- Migrations applied to local DB: `baseline`, `20260803000000_auth`, `20260803010000_hardening`.

**Run locally:**
```bash
docker start analytics-pg analytics-minio   # postgres:16-alpine on :5432, creds accelerator/accelerator
cd apps/analytics-service
venv/bin/python -m pytest -q                # integration tests, need Postgres up
venv/bin/python -m app.infra.db.postgres.migrate apply   # migration runner (checksummed)
```
`.env` holds live API keys — gitignored, keep it that way. Tests seed nothing; they rely
on System superuser `00000000-...-0001` seeded by migrations.

## Key architecture facts (verified, non-obvious)

- **Per-sheet Parquet artifacts already exist physically.** `files/services/processing.py`
  writes one canonical parquet per Excel sheet via `layout.sheet_parquet(name)`.
  Only the *metadata* is second-class: sheet names live in `dataset_versions.source` JSONB;
  schemas live only inside parquet files. Baseline migration comment explicitly anticipates
  adding a `dataset_sheets` table later.
- Tags (`dataset_version_tags`) point at whole versions, unique per (dataset, tag). No history.
- `jobs` table + `app/shared/jobs.py` exist and are underused — ready substrate for
  validation runs and saved-analytics runs.
- Sampling pipeline (`data_accelerator/services/sampling.py`) is sheet-aware and seeded
  (deterministic per-step seeds), 7 methods + dedup/fill/goal stages. No persisted run manifest.
- Schema extraction already trivially available: DuckDB DESCRIBE in `shared/data_io.py` (~line 303).
- Excel ingestion is plain `pd.read_excel(openpyxl)`: NO hidden-sheet detection, header-row
  config, merged-cell policy, or duplicate-column normalization.
- Upload scanning is a pluggable no-op (`shared/scanning.py`); file-path source is superuser-only.
- Storage backend pluggable local/S3 (`infra/db/storage.py`); TUS staging always local.

## Product roadmap (evaluated 2026-08-03)

Source: product recommendations doc reviewed against code. Verdict: directionally right.
Corrections applied: (a) validate/compare/approve/promote do NOT exist yet despite the doc's
lifecycle claim; (b) generalized `artifacts` table deferred to Phase 3 (publish is when it
pays); (c) doc's "Excel policies" are ingestion build-work, not documentation.

### Phase 1 — sheets first-class + diff + promotion basics  ← START HERE
1. Migration: `dataset_version_sheets` (id, dataset_version_id, sheet_key, sheet_name,
   sheet_index, visibility, status, row_count, column_count, size_bytes, checksum,
   schema_json, schema_fingerprint, created_at). Backfill from source JSONB + parquet inspection.
   Keep version-level row_count/checksum as convenience summaries.
2. Extract + store per-sheet schema at ingest (DuckDB DESCRIBE; add normalized names,
   nullability, column order, fingerprint = hash of normalized schema).
3. Workbook diff endpoint `GET /datasets/{id}/versions/{a}/diff/{b}`:
   added/removed/modified/unchanged sheets; rename candidates only as suggestions
   (never auto-declare renames).
4. Sheet schema diff `.../sheets/{sheet}/diff/{b}`: column add/remove, type/nullability/order
   changes, row-count delta.
5. `dataset_tag_history` table + `POST .../tags/{tag}/promote`, `/rollback`,
   `GET .../tags/{tag}/history` (with reason + actor). Keep raw PUT /tags.
6. Ingest: hidden-sheet visibility capture + duplicate/synthetic column-name normalization
   (store original AND normalized names).

### Phase 2 — trust
Quality rules CRUD (scopes: version, sheet, column, cross-sheet FK), validation runs on the
`jobs` table, validation results endpoint, promotion gates (e.g. "validated" tag requires
zero error-level failures), approval notes. All rules compile to single DuckDB queries.

### Phase 3 — reuse
Saved-analytics definitions (version_selector: tag|version, sheet, params) + run history on
`jobs`; publish sample/aggregation result → new dataset or new version (introduce generalized
`artifacts` table HERE); `dataset_lineage` (version- and sheet-level parents).

### Phase 4 — discovery
Rich dataset metadata (owners, domain, source_system, refresh_frequency, grain, PK columns,
deprecation, custom kv) — sheet-level for grain/PK; column-level search (needs Phase 1
schemas in Postgres); facets; favorites; usage metrics.

### Phase 5 — advanced
Coordinated cross-sheet sampling, relationship-based joins (not general SQL), copy-on-write
sheet replacement, checksum artifact reuse, reconstructed `format=xlsx` downloads,
partial-workbook ingestion opt-in.

### Opportunistic (no phase)
Aggregation: HAVING, sort, limit, date/numeric bucketing, conditional aggregates — cheap
DuckDB wins, slot in anytime.

### Explicitly NOT now
More sampling algorithms, distributed compute, dashboards, general cross-dataset joins,
streaming, ML training, vector DBs, billing, orchestration, per-sheet independent promotion,
Excel formatting/macro preservation, real auth (header identity stays until SSO/JWT swap).

### Invariants to preserve
- Versions immutable, never overwritten. Tags → whole versions only (no mixed-sheet tags).
- Partial workbook processing failure ⇒ version status = failed (atomic publication default).
- Multi-sheet requests must name a sheet; return problem+json "sheet-selection-required"
  listing sheets — never silently pick the first sheet. Single-sheet datasets auto-resolve.
- Cross-team existence hiding (404 not 403) must extend to all new endpoints.
- New write/egress endpoints must land in the audit trail (middleware does this if routed
  under the protected router).
