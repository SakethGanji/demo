# Analytics Service — Handoff (2026-08-04)

## Current state

**Branch:** `feat/auth-rbac-audit` (not pushed). Auth/RBAC/audit POC committed 2026-08-03;
Phase 1 (sheets first-class + diffs + tag promotion) built 2026-08-04. 28/28 tests passing,
verified against BOTH storage backends (local FS and S3/MinIO).

**Phase 1 complete (2026-08-04):**
- `dataset_version_sheets`: one row per sheet per version (sheet_key, name, index,
  visibility, status, storage_key, row/col counts, size, checksum, schema_json,
  schema_fingerprint). Migration `20260804000000_sheets` backfills from source JSONB;
  every ready non-Excel version gets one synthetic sheet named `data` (constant name =
  stable diff key). `storage_key NULL` ⇒ resolve to the version's canonical parquet.
- Schemas captured at ingest (DuckDB DESCRIBE on each sheet parquet): physical name,
  original header cell (pre-pandas-mangling, read via openpyxl), normalized_name
  (lowercase snake, synthetic `column_{i}` for blank/Unnamed, `_2` suffix for dups),
  dtype, nullability, position. Fingerprint = sha256 of normalized schema. Legacy
  versions get schema filled lazily on first read (sheets/diff endpoints) and persisted.
- Hidden-sheet visibility captured (visible/hidden/very_hidden via openpyxl sheet_state).
- `GET .../versions/{a}/diff/{b}` — workbook diff (added/removed/modified/unchanged +
  rename candidates as *suggestions only*, matched by schema fingerprint).
- `GET .../versions/{a}/sheets/{sheet}/diff/{b}` — column adds/removes, type/nullability/
  order changes (rank-based, so pure adds don't flag downstream cols), row-count delta.
- `dataset_tag_history` + `POST .../tags/{tag}/promote` (refuses non-ready versions),
  `/rollback` (walks history to previous distinct version), `GET .../tags/{tag}/history`.
  Raw PUT/DELETE also record history (actions: set/promote/rollback/delete) with
  reason + actor, same transaction as the tag mutation.
- **sheet-selection-required is now ENFORCED** (was aspirational): multi-sheet versions
  hit via sample/profile/aggregate/download without `sheet` get problem+json 400 with
  `code: "sheet-selection-required"` and a `sheets` array. Single-sheet auto-resolves.
  `ProblemException` (api/errors.py) carries custom codes + extra fields.
- Sheet endpoints (`/datasets/{id}/sheets[...]`) now serve schema from Postgres (no file
  I/O for the list; single-sheet GET still reads a 5-row preview).

**Local-dev note:** the local DB had applied an earlier draft of the auth migration
(pre-commit, with password columns). Reconciled 2026-08-04: dropped `users.password_hash`
+ `users.last_login_at`, updated the recorded checksum to match the committed file.

**Auth/RBAC POC (2026-08-03, all tested):**
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

- **Sheets are first-class as of Phase 1.** Physical layout unchanged (one parquet per
  Excel sheet via `layout.sheet_parquet(name)`); metadata now lives in
  `dataset_version_sheets` (authoritative), with `dataset_versions.source` JSONB still
  dual-written for provenance/back-compat. Resolution: `app/shared/datasets.py`
  (`get_version_sheet_rows` → DB first, JSONB fallback for pre-migration versions).
- Tags (`dataset_version_tags`) point at whole versions, unique per (dataset, tag);
  every mutation appends to `dataset_tag_history`.
- `jobs` table + `app/shared/jobs.py` exist and are underused — ready substrate for
  validation runs and saved-analytics runs.
- Sampling pipeline (`data_accelerator/services/sampling.py`) is sheet-aware and seeded
  (deterministic per-step seeds), 7 methods + dedup/fill/goal stages. No persisted run manifest.
- Per-sheet schemas + fingerprints are captured at ingest and stored in Postgres
  (`shared/data_io.py`: `build_sheet_schema`/`normalize_column_names`/`describe_parquet`).
- Excel ingestion (`pd.read_excel(openpyxl)`) now captures hidden-sheet visibility and
  original-vs-normalized column names. Still NO header-row config or merged-cell policy.
- Upload scanning is a pluggable no-op (`shared/scanning.py`); file-path source is superuser-only.
- Storage backend pluggable local/S3 (`infra/db/storage.py`); TUS staging always local.

## Product roadmap (evaluated 2026-08-03)

Source: product recommendations doc reviewed against code. Verdict: directionally right.
Corrections applied: (a) validate/compare/approve/promote do NOT exist yet despite the doc's
lifecycle claim; (b) generalized `artifacts` table deferred to Phase 3 (publish is when it
pays); (c) doc's "Excel policies" are ingestion build-work, not documentation.

### Phase 1 — sheets first-class + diff + promotion basics  ✅ DONE 2026-08-04
All six items shipped (see "Current state" above for the as-built details).

### Phase 2 — trust  ← START HERE
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
