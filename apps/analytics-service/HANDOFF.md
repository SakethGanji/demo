# Analytics Service — Handoff (2026-08-05, post-Wave-5)

## Control plane vs data plane (standing architectural rule)

**Postgres is the control plane. It holds identity, config, schema, counts,
ratios, status, and pointers — never a copy of dataset cell values.** Anything
row-shaped belongs in the object store, registered in `artifacts` so
`/samples` authorization (team visibility, cross-team 404) applies to it.

`tests/test_control_plane.py` enforces this. It is schema-driven: it lists
every JSONB column in the schema and fails on any that isn't in a reviewed
allow-list, so a future migration that reintroduces a row-shaped column has to
confront the rule rather than slip past it.

One deliberate exception, documented rather than silent: `profile_runs.profile`
(and the transformation runs' copies of it) contains capped top-N values
alongside its counts and ratios. Those are aggregate statistics that the
catalog facets, health dimensions, drift, and insights all read **via SQL** —
`signals_lateral()` is built directly on them — so moving them to object
storage would put an S3 round-trip on every catalog page. Reversible if the
strict reading is wanted.

Fixed 2026-08-05: `validation_rule_results.sample_failures` stored literal
dataset rows as JSONB. Failing rows now go to the object store as a
`validation_failures` artifact with only `failure_count` +
`failure_artifact_id` in Postgres (migration `20260812000000`). Two things fell
out for free — failing rows are downloadable through `/samples/{f}/data`, and
publishable as a dataset in their own right.

## Artifact storage layout + retention (2026-08-05)

Derived outputs (samples, aggregations, pivots, query results, diffs,
transformation and join outputs, validation failures, exports) used to share one
flat `samples/` prefix. Ten kinds in one namespace could not express anything a
bucket needs to express, and three problems followed from that single fact:

- **No lifecycle rule could tell them apart.** Query scratch and a published
  source looked identical to S3, so nothing was ever deleted.
- **No prefix-scoped IAM.** One tenant's derived data could not be isolated.
- **Deleting a dataset had to enumerate keys** instead of dropping a prefix,
  and so could only reach blobs whose ownership row existed.

The layout is now:

    artifacts/{team_id}/{dataset_id}/{kind}/{filename}

built by `ArtifactLayout` (`app/infra/db/storage.py`), which replaced
`sample_key()`. It is deliberately **not** date-partitioned: expiry keys off
object age, which S3 evaluates natively, and listings come from Postgres — so a
date segment would buy nothing and would cost the property the design leans on.
The key is a *pure function of its fields*, which is what lets the service that
writes the parquet and the code that registers ownership derive the same string
without passing it between them (`tests/unit/test_artifact_layout.py` pins this).

Consequences worth knowing:

- **`artifacts.filename` is the public identifier** (migration `20260814000000`).
  A caller holding `/samples/{filename}` knows neither team, dataset, nor kind,
  so resolution goes through the artifact row — the same row that already
  governed authorization. Authorization and resolution became one lookup.
- **An unregistered blob is unreachable by everyone**, superusers included. It
  is not merely unauthorized: nothing can produce its key. That is why the
  orphan sweep exists.
- **`GET /samples` reads Postgres**, not the bucket. The old listing enumerated
  every key globally and filtered in Python, so one caller's page cost grew with
  every artifact in the deployment, and it showed blobs nobody could open. It
  paginates and reports the real artifact kind.
- **Dataset deletion is a prefix delete**, which also reclaims blobs whose row
  never landed.
- **The dead top-level `exports/` prefix is gone** — an export is just another
  artifact kind, inheriting its source's team and dataset so both are swept
  together.
- **Chart rendering no longer persists.** It recomputed a definition and wrote
  a parquet that nothing registered — a guaranteed orphan on every render.

**Retention** (migration `20260814010000`, `files/services/retention.py`).
Retention is a property of the kind, which is why the kind is a path segment.
`expires_at` is stamped on the row at write time from the policy then in force,
so editing the policy never retroactively shortens something already stored;
`published_source` is null (keep forever). Two sweeps run as the `artifact_gc`
job — expired rows, then orphan blobs past a 24h grace window (a job writes the
parquet before registering it, so a brand-new unreferenced key is normal, not
garbage). Admin surface: `GET /storage/retention`, `POST /storage/gc`.

Publishing copies the blob into the dataset's own version storage rather than
pointing at the artifact key, which is what makes retention safe to apply to
every derived kind — collecting a `sample_output` can never strand a version.
`tests/test_artifact_retention.py` pins that end to end.

Dev migration: old `samples/` rows were deleted rather than relocated (the old
key records neither team nor dataset nor kind, so nothing could be reconstructed
from it) and the blobs purged with `scripts/purge_legacy_artifacts.py`.

## Test-suite performance (2026-08-05)

The suite took ~12 minutes. Three measured causes, all fixed; it is now ~110s
for the same 1028 tests, and stays there run over run.

1. **The per-test table reset was `TRUNCATE`** — 190 ms every test, ~208 s of
   the run, because TRUNCATE recreates each table's file and indexes regardless
   of how empty it is (~7 ms × 27 tables). `DELETE` over the same tables is
   ~11 ms. TRUNCATE was chosen for `CASCADE`, which was needed because
   `datasets` and `dataset_versions` reference each other — a real FK cycle
   with no valid delete order. DELETE works anyway: every inbound FK in the set
   is `ON DELETE CASCADE` or `SET NULL`, so any order resolves. Nothing depends
   on `RESTART IDENTITY` (the only sequence in the set is a surrogate key).
2. **`GET /storage/usage` did one HEAD request per object.** With 24k objects
   in the test bucket that was 31 s per call, and the test calls it twice — 63 s
   in a single test, 25× the next slowest. This was a real production bug, not
   just a test artifact: the endpoint was unusable on any real bucket.
   `StorageBackend.list_sizes()` now takes the size from the listing itself
   (`list_objects_v2` already returns it), 31 s → 1.8 s on the same data.
   `test_artifact_storage.py` pins it against `size()` per key.
3. **Nothing cleaned what the tests created outside the reset set.** The
   per-test cleanup deliberately preserves users/teams/team_members and the
   append-only `audit_log` — correct per test, but every run left its
   `create_team_user` rows and audit trail behind, plus every blob written to
   either backend. 7k users, 5.7k teams, 79k audit rows and 24k objects had
   accumulated, and the suite got slower every time it ran. The session-scoped
   `_fresh_environment` fixture now drops them once at startup
   (`ACCELERATOR_KEEP_TEST_STATE=1` skips it). This is not isolation — no test
   can reach a previous run's rows — it is what keeps the cost flat.

The lesson worth carrying: two of the three only showed up as *drift*. A suite
that creates state it never removes gets slower in a way no single commit
causes, so nobody bisects it.

## Post-roadmap hardening (2026-08-05, after Waves 0–6)

Six capability gaps closed after the roadmap was complete, driven by "what
would make this a good system" rather than by the spec:

- **Keyed row-level version diff** (`POST .../sheets/{s}/row-diff/{b}`) — the
  question a reviewer actually asks before promoting. Schema diff and profile
  drift never answered "which rows changed". Matches on the sheet's declared
  primary key, classifies every row via one FULL OUTER JOIN, and writes the
  full cell-level result to a `diff_output` artifact. Refuses a non-unique key
  (409) rather than answering wrongly.
- **Chart rendering** (`POST .../charts/{id}/render`) — `chart_definitions`
  stored config that nothing could execute. Charts still own no query logic;
  rendering re-runs the referenced definition/view. Fields are inferred when
  the config omits them, so a chart with an empty config still renders.
- **PII masking** — the dictionary's `sensitivity` field was inert. Now
  enforced wherever raw rows are returned, with `dataset:read_sensitive`
  (admin/owner, deliberately not editor) granting the unmasked view. The raw
  download is gated by the same permission for datasets that declare anything —
  masking alone would be theatre.
- **Outlier insights** — Tukey fences over the quartiles the profiler already
  computes. Reports fences and which side, never the outlying values.
- **Transitive lineage graph** (`GET .../lineage/graph`) — recursive CTE with a
  depth cap and cycle guard; turns lineage from a list into a chain.
- **Webhooks** (`/webhooks`) — lifecycle notifications delivered through the job
  worker, HMAC-signed over `timestamp.body`, thin payloads, delivery attempts
  recorded. A dead receiver can never fail the operation that triggered it.

Route coverage is measured, not assumed: **142/142 API routes were exercised by
the integration suite** (verified by wrapping each route's ASGI callable during
a full run). The artifact-storage work since added `/storage/retention` and
`/storage/gc`; both are covered by `test_artifact_retention.py` and e2e, but
the full wrap-and-count has not been re-run, so treat 142/142 as the last
measured figure rather than the current one. 1028 tests, e2e_curl at 104 checks.

## MCP surface folded in (2026-08-06)

`app/features/mcp/` — 27 MCP tools mounted at `/api/v1/mcp`, moved in from the
standalone `apps/analytics-mcp` server. Details in `ARCHITECTURE.md` §6; the
three things not to undo:

1. **Identity is resolved per request by `get_principal`, and rebound per
   JSON-RPC message.** The standalone server baked one `X-User-Id` into one
   `httpx.AsyncClient` at construction, which is correct for one-process-per-user
   and an identity-laundering hole anywhere else. `tests/test_mcp_endpoint.py`
   drives two users through one process, sequentially and concurrently, and
   asserts each sees only its own data. That test is the regression guard for
   the whole fold-in.
2. **The transport is stateless streamable HTTP** — not a preference. A stateful
   session runs its handlers in a task spawned by whichever request opened it.
3. **The endpoint is a Starlette `Route`, not an `APIRoute`,** so it stays out of
   the OpenAPI document. It is JSON-RPC, not REST.

`apps/analytics-mcp` was a ~130-line stdio→HTTP relay for clients that can only
launch subprocesses (Claude Desktop/Code). It held no tool definitions and was
**deleted on 2026-08-10** — the tools were already here and only here, so nothing
was lost from the surface, but with it went the only stdio path. MCP is now
reachable over HTTP at `/api/v1/mcp` only; a stdio client needs that relay back
(recover it with `git revert` of the deletion commit, or `git show <rev>:apps/analytics-mcp/src/analytics_mcp/__main__.py`).

Five client-side workarounds came out at the same time, because the upstream
defects they covered were fixed: filter-grammar validation, the additive-function
totals filter, `sort_order` lowercasing on `aggregate`, `write_documentation`'s
read-merge-write (now `PATCH`, which newly makes clearing a field possible), and
the `count - null_count` derivation (now `ColumnProfile.non_null_count`).

## Current state

**ALL FIVE ROADMAP PHASES + ROADMAP.md WAVES 0–5 COMPLETE (§1–§24), plus the
job worker**, plus the post-roadmap hardening above. 1028 tests passing
serially (unit layer Postgres-free; the ENTIRE integration suite parametrized
over BOTH storage backends — local FS and S3/MinIO — in one `pytest`
invocation). Twenty-three migrations; runner unchanged. `scripts/e2e_curl.sh`
at 104 live-HTTP checks, 0 failures. **`WAVE4-6-PLAN.md` is now spent — it was the
design input for the three waves below and is superseded by this document.**
What remains is the ops track (see the end of this file), not features.

**Waves 4–5 commits on main:** `6c9e11e` (Wave 4 §19–§21 transformations),
`44a5f2b` (Wave 5 §22–§24 relationships/joins/sub-sampling).

**Wave 4 (§19–§21) — transformation pipelines.** New `app/features/transform/`
(migration `20260809010000_transformations`). A pipeline is an ordered list of
discriminated-union steps compiled to ONE DuckDB statement, one CTE per step,
with a **running schema folded alongside the SQL** — so a step referencing a
column an earlier step dropped fails at compile time with `unknown-column`
(listing what IS available at that point) rather than as a DuckDB error
mid-run. Filter steps embed the query DSL's `FilterGroup` verbatim;
set-relative operators (`top_n`, `is_duplicate`) that `compile_filter` writes
against `_filter_src` are retargeted at the preceding CTE, which is exactly
"the whole input" at that point in the chain. §20 computed columns are a typed
expression tree, never a parsed string; the standing invariant across
`expr.py` + `compile.py` is that **no node emits user text into SQL** —
identifiers via `quote_ident`, values as `?` binds, and every operator/part/
type token a pydantic `Literal` mapped to a whitelisted SQL token. §21
auto-profiles each output and diffs it against the source's PERSISTED profile
run (ad-hoc fallback). Runs go through the job worker — one registered
`transform` handler serves both the sync request path and `sync=false`.
Publishing is non-destructive, lineage `transformed_from`.
- `compute_profile_drift` gained `added_columns`/`removed_columns`: per-column
  deltas only covered columns present on BOTH sides, so a projection change was
  invisible — which is precisely what a transformation does.
- `library.service.publish_run`'s version machinery was extracted as
  `publish_artifact_as_version(...)` (relation + `extra_lineage` parameterized)
  so transformations and joins publish identically instead of duplicating it.

**Wave 5 (§22–§24) — relationships, guided joins, sub-sampling.** New
`app/features/relationships/` (migrations `20260810000000_relationships`,
`20260810010000_join_builder`). Edges arrive three ways — seeded from
`foreign_key` quality rules, discovered statistically, or declared manually —
and both endpoints carry their own dataset id (equal within a workbook) because
§23 joins across datasets. Discovery scores a column pair from four signals and
evaluates BOTH directions; the side whose key is near-unique is the parent,
which is what orients the edge.
- **Two signals are FLOORS, not weights** (`probes.qualifies`): a repeating
  "parent" key is a many-to-many, not a reference, and near-zero overlap is
  noise. A pure weighted sum let a strong name outvote both — caught by a unit
  test, and the reason `qualifies()` exists separately from `confidence()`.
- §23 joins are driven ONLY by a confirmed relationship — that gate is what
  makes exposing cross-dataset joins safe. Pre-flight probes measure duplicate
  keys per side, many-to-many, the EXACT output row count (per-key
  multiplicities multiply), unmatched % both ways, and column collisions;
  `tests/unit/test_join_warnings.py` checks each estimate against a real DuckDB
  join across 1:1 / 1:N / N:N with NULLs. No new table — `join` is a new
  analytics kind (three CHECK widenings) riding definitions/runs/artifacts/
  lineage, with ONE reused definition per (relationship, how) so repeated joins
  don't litter the library. Publish writes two `joined_from` lineage rows.
- §24 extended `RelatedSheetLink` with `relationship_id` (precedence: explicit
  keys > relationship > §5 FK-rule default) and `sampling_steps`/
  `target_total_volume`. Sub-sampling a sheet another link reads from is
  refused (`cannot-subsample-parent`) — it would silently orphan the
  dependants. `_run_sampling_pipeline_inner` gained `source`/`output`/`persist`
  so a sub-sample runs on the same connection without colliding with the
  driver's tables or leaving an unregistered orphan blob. v1 payloads behave
  identically (asserted).

Wave-3 commits on main: `f1410cc` (§15 column-level data dictionary,
migration `20260808000000_column_metadata`), `59d2509` (§16 duplicate +
missing-data explorers), `1db0d90` (§17 dataset health read-model), plus §18
catalog facets (this session — see "Wave 3" below).
Wave-1 commits on main: `b27b513` (§6 explorer query endpoints), `5fd086d`
(§6b sandboxed SQL, migration `20260807000000_explorer_sql`), `4a8ac40` (§7
column explorer), `729035a` (§8 profile runs + insights, migration
`20260807010000_profiling`), `8729ff6` (§9 profile drift), `6e816f0` (§10
saved views + Wave-1 journey). Wave-2 commits: `87c1cc5` (§11 pivots,
migration `20260807020000_pivot`), `07f0e1e` (§12 exports), `33332d6` (§13
timeline), `f2ea8f0` (§14 charts, migration `20260807030000_charts`).

**Job worker (2026-08-05, commit `1a9d235`) — ops track, prerequisite for Wave 4:**
`app/shared/worker.py` is the POC replacement for FastAPI `BackgroundTasks`:
features enqueue a `jobs` row (`shared/jobs.create_job`) and register an async
handler per `job_type`; the worker loop (started/stopped in `app/main.py`
lifespan, gated on `settings.job_worker_enabled`) atomically claims pending
jobs with `FOR UPDATE SKIP LOCKED`, runs the handler, marks completed/failed.
Key pieces: handler registry (`register_handler`), `run_pending_jobs_once`
(drains synchronously — this is how tests + requests complete work, since the
ASGI test client does NOT run the lifespan loop), `run_worker_loop`, and
`dispatch(job_type, …, inline)` so the request path and the loop share ONE
handler (`inline=True` = current synchronous contract: run now, return result,
re-raise on failure; `inline=False` = enqueue, worker drains). **The loop only
claims job_types with a registered handler**, so existing inline
validation/profiling/sampling and the upload BackgroundTasks path are untouched
(no double-execution). Deliberately deferred (documented recipe in
WAVE4-6-PLAN, low-risk): migrating uploads/validation/profiling onto
`dispatch` and adding retry (`attempts`/`run_after`). Tests:
`tests/test_job_worker.py`. **Wave 4 §19 uses it:**
`worker.dispatch("transform", …, inline=<sync flag>)`, registering the handler
in a feature module imported at `app/main.py` top.

**Wave 3 (2026-08-05) — ROADMAP §15–§18:**
- **§15 Data dictionary.** `dataset_column_metadata` (migration
  `20260808000000_column_metadata`; UNIQUE (logical_sheet_id, column_name),
  FK CASCADE from dataset + dataset_sheets). CRUD under
  `/datasets/{id}/sheet-metadata/{sheet}/columns[/{column}]` in `discovery/`.
  Keyed on logical_sheet_id (no `reassign_logical_sheet` extension needed);
  `column_name` validated against the current version's normalized schema
  (`unknown-column` 400 with `available`). Added to `_TRUNCATE`.
  Both the sheet-metadata and the column-dictionary path expose **PUT and
  PATCH**: `PUT` replaces the whole record (an omitted field is cleared — the
  only way to blank one), `PATCH` merges via `exclude_unset=True` + a partial
  `UPDATE`, so an omitted field survives while an explicit `null` still clears.
  `PATCH` 404s rather than creating, and repeats `PUT`'s `dataset:write` check.
- **§16 Duplicate/missing explorers.** Read-only in `features/explorer/`:
  `GET .../sheets/{s}/duplicates` (exact + `columns=`-subset groups via GROUP
  BY/HAVING COUNT(*)>1, capped, example rows) and `GET .../sheets/{s}/missing`
  (per-column null stats — `source: profile_run` when a completed run exists,
  else computed). No new tables. Tests: `test_dup_missing_explorers.py`.
- **§17 Health.** `GET /datasets/{id}/health` — multi-dimension read-model,
  NO single opaque score (`discovery/health.py`). Seven dimensions
  (schema_stability, validation, missing_data, duplicates, drift, freshness,
  documentation), each a pure `evaluate_*` over persisted signals with a
  status + summary + evidence pointers. Signal fetchers are repo functions
  (`recent_version_sheets`, `latest_profiled_pairs`, `documentation_stats`,
  quality `latest_completed_run`). Tests: `test_health.py`.
- **§18 Catalog facets.** `discovery/repo.py::signals_lateral()` — one reusable
  `CROSS JOIN LATERAL ... sig` computing three per-dataset signals
  (`validation_status` passed/failed/none, `has_schema_drift` bool,
  `documentation` full/partial/none) from the SAME sources as the §17 health
  dimensions, so catalog and `/health` never disagree. `GET /datasets/facets`
  gained those three bucket groups; `GET /datasets` gained matching filters
  (`validation_status`, `has_schema_drift`, `documentation`) and returns the
  signals on each row (`DatasetInfo`). No new tables. Tests:
  `test_catalog_facets.py` (10; also cross-checks catalog == health).

**Wave 2 (2026-08-04) — ROADMAP §11–§14:**
- **§11 Pivots.** `POST /pivot` + analytics kind `pivot`
  (`data_accelerator/services/pivot.py`), compiled onto the extracted
  aggregation helpers, which are reused at EVERY grain: long-format GROUP BY
  (rows + pivot dim) → percentage displays (`pct_of_row/column/grand_total`)
  as window functions over the materialized result → widening via bound CASE
  (`MAX_PIVOT_COLUMNS` guard, `too-many-pivot-columns` 400). Totals are
  RE-AGGREGATED at the coarser grain, so mean/nunique totals are correct —
  never sums of cells. Migration widened three CHECKs: kind (`pivot`),
  artifact_type (`pivot_output`), lineage relation (`pivoted_from`).
  `execute_definition` dispatches the kind; publish records `pivoted_from`.
  Cell naming: single value spec → bare pivot value; multiple → `{value}_{alias}`.
- **§12 Exports.** `POST /samples/{filename}/export?format=csv|xlsx|parquet`
  converts any stored output via `export_dataframe`; the export is an `export`
  artifact whose ownership is INHERITED from the source artifact, so /samples
  authorization (team visibility, cross-team 404) applies unchanged.
- **§13 Timeline.** `GET /datasets/{id}/timeline` — one UNION ALL in
  `discovery/repo.py` merging versions, tag history, validation runs, profile
  runs, lineage both directions (self-publish rows deduped), and audited
  WRITE requests (reads stay in `/usage`). Offset paging; no new tables.
- **§14 Charts.** `chart_definitions` (migration): exactly one source —
  `definition_id` OR `view_id` (DB CHECK + pydantic validator), FK CASCADE
  from either source, opaque `config` JSONB. CRUD under
  `/datasets/{id}/charts` (library feature); PATCH retargeting clears the
  other source. Tests: `test_pivot.py`, `test_exports.py`, `test_timeline.py`,
  `test_charts.py`.

**Wave 1 (2026-08-04) — ROADMAP §6–§10, all in `app/features/explorer/`:**
- **§6 Explorer.** `GET .../versions/{v}[/sheets/{s}]/preview` + `POST .../query`
  (body `QuerySpec` → `QueryPage`) over `app/shared/query.execute_query`. Sheet
  resolution factored as `shared/datasets.resolve_version_sheet_row` (shared by
  `resolve_version_sheet_path`) so schema_json-needing callers get identical
  sheet-selection-required semantics. Tests: `test_explorer.py`.
- **§6b Sandboxed SQL.** `app/shared/duck.py::open_sandboxed` — MATERIALIZE
  sheets, then `enable_external_access=false` + `lock_configuration=true`;
  gate = `duckdb.extract_statements` (exactly one SELECT; read-only PRAGMAs
  parse as SELECT and are harmless, config PRAGMAs parse as SET and are
  rejected); watchdog `conn.interrupt()` timeout; row cap; sanitized errors.
  `run_sandboxed` returns a DataFrame because the locked conn can never write
  files — the endpoint persists the result parquet from a fresh conn and
  registers a `query_output` artifact (CHECK widened by migration). Size guard
  `version-too-large-for-sql` 413 (`MAX_SQL_MATERIALIZE_BYTES` in
  explorer/service.py). Tests: `tests/unit/test_duck_sandbox.py` (19) +
  escape-attempt integration tests.
- **§7 Column explorer.** `GET .../columns/{column}` — `profile_column_duckdb`
  was ALREADY per-column (no factoring needed); extras added: uniqueness,
  candidate-key, rare values, examples. Column refs resolve via the DSL's
  `_resolve` (unknown-column 400 with `available`).
- **§8 Profile runs.** `profile_runs` (UNIQUE (version, logical_sheet,
  algorithm_version); upsert resets + replaces insights → idempotent) +
  `profile_insights`. POST/GET `.../versions/{v}/profile-runs`, GET
  `.../profile-runs/{id}` (with profile JSON). Jobs wiring copied from
  `validate_version` — synchronous inline, job_type `profiling` was already in
  the CHECK. Insight rules are pure functions (`explorer/insights.py`):
  likely-primary-key, constant-column, high-null-rate, duplicate-rows,
  future-timestamps, high-correlation; vs previous ready version's persisted
  run: null-rate-spike, new-categories, new-sheet.
- **§9 Profile drift.** `include=profile` on both diff endpoints (they had zero
  query params; unknown sections 400). `compute_profile_drift` (diffs.py, pure):
  row/duplicate deltas, per-common-column null/distinct/mean/std deltas,
  category adds/removes. Sheet diff strict (`profile-required` 400 naming
  missing sides); workbook diff soft (`profile_missing` list).
- **§10 Saved views.** `dataset_views` CRUD + `/run` under
  `/datasets/{id}/views`. Keyed on logical_sheet_id; `/run` pins the version
  via the library's `_selector_pin` (returns resolve_version kwargs, NOT an
  id) and re-resolves the sheet BY LOGICAL ID — confirmed renames need no view
  mutation (tested); sheet absent from pinned version → 404
  `sheet-not-in-version`. Stored queries never carry cursors; `/run` takes
  cursor/limit overrides. `test_wave1_journeys.py` = the workspace end-to-end.

**Wave 0 (2026-08-04, migration `20260806000000_logical_sheets`) — ROADMAP §1–§5:**
- **§1 Logical sheet identity.** `dataset_sheets` (one row per logical sheet;
  partial-unique live `(dataset_id, current_sheet_key)`); `logical_sheet_id` NOT NULL on
  `dataset_version_sheets`, nullable on `dataset_sheet_metadata` + `quality_rules`
  (SQL-backfilled: same key = same identity). Ingest links rows in
  `files/repo.insert_version_sheets` — the single choke point all four sheet-row writers
  (processing, sync upload, COW replace, publish) flow through.
  `POST .../versions/{v}/confirm-rename` ({from_sheet, to_sheet, force}) folds the
  auto-created identity into the original: version rows re-pointed, selector text +
  FK-rule `parameters.ref_sheet` rewritten to the new key, spurious `dataset_sheets` row
  deleted. Gated on the diff's fingerprint candidates (`rename-not-candidate` 400 unless
  force), 409 `conflicting-sheet-state` if the new identity accumulated its own
  metadata/rules. Quality engine resolves rule→sheet logical-id-first
  (`quality/engine.py:_find_sheet`); rule create/update pins selectors to live logical
  sheets (`quality/api.py:_resolve_sheet_selector`); discovery upsert stores the id via
  SQL subselect. Sheet responses expose `logical_sheet_id`. Tests: `test_logical_sheets.py`.
- **§2 Typed query DSL.** `app/shared/query/` — `Filter`/`FilterGroup`/`Sort`/`QuerySpec`/
  `QueryPage` (typed formalization of the `shared/filters.py` dict shapes; existing
  payloads validate unchanged), `validate.py` (schema checks before any file I/O →
  `unknown-column` / `operator-type-mismatch` 400s), `compile.py` (reuses
  `compile_filter`; multi-sort; search-over-VARCHAR; opaque cursor
  `{version_id, spec_hash, offset}` exploiting version immutability — `invalid-cursor`
  on tamper/mismatch). Substrate for Wave-1 explorer/saved views. Tests:
  `tests/unit/test_query_dsl.py` (24).
- **§3 Aggregation extras.** `AggregateRequest.filters` (FilterGroup; `filter_expr`
  deprecated but still accepted, ANDed), `having` over aggregation aliases,
  `group_by: str | GroupByBucket` (`date_trunc` with explicit TIMESTAMP cast /
  `bin_width` / `bin_count` — CASE/FLOOR emulation, DuckDB 1.5.3 has no width_bucket),
  per-spec conditional aggregates via `FILTER (WHERE ...)`, sort on bucket/agg aliases,
  server-side cap `MAX_AGGREGATION_ROWS = 100_000` with `truncated` response flag.
  Bind order (WHERE → FILTER → HAVING) kept correct by pre-filtering in a CTE.
  Tests: `test_aggregation_extras.py` (11).
- **§4 Corrupt uploads are 400s.** `InvalidFileError` raised from `convert_to_parquet`
  parse failures; sync upload maps it to problem+json 400 `invalid-file` (async path
  records `error_kind`). The old 500 contract test was updated.
- **§5 Coordinated-sampling auto-keys.** `RelatedSheetLink.left_on/right_on` optional
  (both-or-neither): omitted keys default from the single enabled `foreign_key` rule
  linking the pair (either direction, normalized→physical column mapping); zero or
  multiple matches → 400 naming the rules. Resolved keys echoed in the response.

**Samples authorization (2026-08-04, closing the capability-URL gap the journey tests
found):** every persisted /sample, /sample/coordinated (driver + related), and
/aggregate output is now registered in `artifacts` at the API layer
(`data_accelerator/api.py:_register_output_artifacts` — dataset's team, or the
caller's active team for dataset-less sources; library runs already registered
theirs). `GET /samples`, `GET /samples/{f}`, and `/samples/{f}/data` authorize
against those rows: superusers see all; dataset-owned artifacts go through
`ensure_dataset_permission` (cross-team → 404); team-owned need dataset:read in
that team (else 404, existence hidden). Superseded 2026-08-05 in one respect —
a file with NO ownership row is now unreachable by *everyone*, superusers
included, because the row is also the only way to produce the storage key (see
"Artifact storage layout"). Tests: `test_sample_authorization.py`.

**Aggregation testability refactor (2026-08-04):** the SQL assembly inlined in
`run_aggregation` is now pure helpers (`_build_join_select`, `_compile_where`,
`_compile_group_entries`, `_compile_select_aggs`, `_compile_having`,
`_assemble_sql`, `_effective_limit`/`_is_truncated`) with 24 unit tests
(`tests/unit/test_aggregation_assembly.py`) pinning select-list collision
prefixing, bind ordering (WHERE → FILTER → HAVING), error texts, and the
truncation matrix. One deliberate behavior fix rode along: `group_by: []` now
omits the GROUP BY clause and returns a single grand-total row (previously a
DuckDB parser error surfaced as 400).

**Schema-review refinements (2026-08-05, migration `20260805000000_ops_refinements`):**
- `artifacts` gained `dataset_id`/`team_id` ownership (FK CASCADE, backfilled from
  analytics runs). Dataset deletion now also removes owned artifact ROWS (cascade) and
  their stored BLOBS (samples-area keys, snapshotted before the DB delete in
  `files/services/management.py`) — previously both were orphaned forever.
- `audit_log.duration_ms`: request latency captured by `AuditMiddleware`, exposed in
  `GET /audit`.
- `dataset_lineage.relation` is now kind-specific on publish: `sampled_from` /
  `aggregated_from` (fallback `published_from` for future kinds); CHECK updated.
Declined from the same review (see session notes): artifact_id on sheet rows (wait for
a dedup/GC feature), renaming `current_version_id`, a dedicated `profile_runs` table,
diff caches, processing/lifecycle status split. Tag normalization was already done.

**Coordinated cross-sheet sampling (2026-08-04/05, was the last deferred Phase 5 item):**
`POST /api/v1/sample/coordinated` → `CoordinatedSampleResponse`. Samples a driver sheet
with the normal pipeline, then semi-joins each related sheet down to rows whose key appears
in an already-sampled parent (driver by default, or another related sheet via
`parent_sheet` — resolved in dependency order with a cycle/unknown-parent 400 guard).
v1 scope (locked with the user): **filter-only + explicit keys** — no auto-discovered FK
graph, no sub-sampling of related sheets. Read-only over sources; writes only derived
parquet to the samples area. Guarded by `_authorize_source` (RBAC + cross-team 404 +
audit). Schemas: `RelatedSheetLink`/`CoordinatedSampleRequest`/`RelatedSheetSample`/
`CoordinatedSampleResponse`; service: `run_coordinated_sampling` +
`_persist_table` in `services/sampling.py` (reuses `_run_sampling_pipeline_inner`,
which leaves the driver's `sampled` table on the conn).

**Phase 2 — trust (`features/quality/`):** quality_rules (8 rule types across
dataset/sheet/column/cross-sheet scopes, selectors = sheet_key + normalized column names,
each compiled to one parameterized DuckDB query in `quality/engine.py`); validation_runs +
validation_rule_results as durable results (rule snapshot + sample failures) with a jobs row
per run; promotion gate: datasets with enabled rules refuse promote without a completed
validation run with zero error-level failures (problem+json `validation-required` /
`validation-failed`); raw PUT /tags stays ungated as the escape hatch, rollback never gated.

**Phase 3 — reuse (`features/library/`):** analytics_definitions (sample/aggregate/profile,
version_selector current|tag|version, params = underlying request body) + analytics_runs;
run outputs registered in the generalized `artifacts` table;
POST .../analytics/runs/{id}/publish turns an output into a new dataset or new version
(full version machinery incl. sheet row + checksums); `dataset_lineage`
(published_from / sheet_replaced_from, denormalized parent labels) + GET .../lineage.

**Phase 4 — discovery (`features/discovery/`):** datasets gained domain/source_system/
refresh_frequency/deprecated(+reason)/metadata JSONB; listing filters (domain, favorites,
include_deprecated) + per-row is_favorite; dataset_sheet_metadata (grain + PK columns per
logical sheet_key); GET /search/columns (team-scoped, zero file I/O — Phase 1 schemas);
GET /datasets/facets (registered BEFORE /datasets/{id}); favorites; GET .../usage from the
audit trail.

**Phase 5 — advanced:** format=xlsx with no sheet reconstructs the whole workbook (tab
order + hidden visibility restored); `include_sheets` upload field = partial-workbook
ingestion (recorded in provenance); checksum-based artifact reuse (unchanged sheets point at
the previous ready version's parquet — identical workbooks ⇒ identical manifest_checksum);
POST /datasets/{id}/sheets/{sheet}/replace = copy-on-write sheet replacement (new immutable
version, other sheets reuse artifacts, lineage recorded); relationship-based joins in
/aggregate (JoinSpec, inner|left, collision-safe select); coordinated cross-sheet sampling
(see "Current state" above — shipped last, 2026-08-04/05).

**Ops hardening:** GET /upload/status/{id} answers from the DB when the in-memory cache is
gone (restart-safe); GET /jobs + /jobs/{id} (team-scoped, cross-team 404) for observability.
Still deliberately POC-grade: BackgroundTasks (not a worker pulling the jobs table),
TUS staging on local disk, header identity until the SSO/JWT swap.

**Schema hardening (2026-08-04, post design review; migration `20260804020000`):**
- `dataset_versions.row_count` now = TOTAL rows across sheets (was first-sheet-only for
  workbooks — misleading; historical rows backfilled). New: `sheet_count`,
  `source_checksum` (sha256 of the exact uploaded bytes), `manifest_checksum` (sha256 over
  ordered per-sheet checksums = the version's content identity). `checksum` stays as the
  canonical-parquet hash.
- `datasets.current_version_id`: composite FK guarantees same-dataset; `complete_version`
  only advances it to a HIGHER ready version (out-of-order async completions can't move
  "current" backwards). Semantics: highest ready version = default for unqualified reads.
- Sheets: every NEW ready sheet row has an explicit `storage_key` (keyed by deduplicated
  `sheet_key`, so colliding sanitized names like "Q 1"/"Q-1" can't overwrite each other);
  the NULL⇒version-path fallback remains only for pre-Phase-1 backfilled rows.
  `UNIQUE(version, sheet_index)`, at most one default sheet per version,
  `schema_extractor_version` + `schema_backfilled_at` distinguish ingest-time schemas from
  lazy backfills. Columns gained `header_was_duplicated`/`generated_name` flags.
- Tags are case-insensitive slugs: lowercased at every entry point, DB CHECK enforces the
  normalized form. Tag history rows carry `request_id` (correlates with the audit log).
- Version `source` JSONB now records parsing provenance (parser + versions,
  conversion_version, options) so identical uploads producing different artifacts are
  explainable.

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

## Test architecture (built 2026-08-05)

**Layers.** `tests/unit/` (26 files) = fast tests, no Postgres/app server (pure helpers,
quality engine against tmp parquet, in-memory DuckDB, pure SQL builders and scorers).
`tests/test_*.py` (40 files) = integration tests, in-process ASGI client against the real
app + Postgres + storage backend. 1028 total.

**Both backends in one run.** `tests/conftest.py` has a `storage_backend` fixture with
`params=["local", "s3"]`; the `client` fixture depends on it, so EVERY integration test
runs twice (test ids get `[local]`/`[s3]` suffixes). The s3 param mutates the (plain
pydantic, read-at-call-time) `settings` object with the MinIO config
(`localhost:9000`, `minioadmin`/`minioadmin`, bucket `analytics`, prefix `tests`) and
swaps the singleton via `init_storage(S3StorageBackend(...))`; teardown restores local.
In-process switching has been stable — the env-switch-matrix fallback from the plan was
not needed. Test artifacts accumulate under the MinIO `tests/` prefix; wipe at will.

**Isolation.** An autouse `_db_cleanup` fixture TRUNCATEs the 15 mutable domain tables
(children→parents, `RESTART IDENTITY CASCADE`) before every integration test, reusing the
app's own engine. Preserved: `users`/`teams`/`team_members`/`refresh_tokens` (seeded
System superuser `00000000-...-0001` + Default team), `audit_log` (append-only trigger),
`schema_migrations`. Every FK into the truncation set comes from within it, so CASCADE
can't reach preserved tables. Consequence: tests assert EXACT counts. The fixture skips
files under `tests/unit/`.

**Shared factories** (conftest): `make_workbook` (Revenue dup/blank headers + hidden
Secrets; v2 via kwargs), `make_orders_workbook(clean=)` (dirty variant: NULL id, dup id,
orphan FK), `make_holdings_workbook`, `make_crm_workbook` (Customers/Orders/hidden
Scratch); helpers `create_team_user(client, admin_id, role, team_id=None) -> (uid, tid)`,
`upload_file`, `upload_inline`, `poll_status`, `auth`, `rid`; fixtures `admin_id`,
`admin_dataset`; constants `DEFAULT_TEAM_ID`, `DEFAULT_USER_ID`, `SAMPLE_CSV`, `XLSX_MIME`.

**Endpoint → test coverage matrix** (~58 routes, all exercised):

| Area | Endpoints | Tests |
|---|---|---|
| auth/users/me | /auth/me, /auth/users | test_api, test_e2e_journeys |
| teams + membership | /teams, /teams/{id}/members (GET/POST/PATCH/DELETE) | test_teams_membership, test_api |
| simple upload + status | /upload, /upload/status/{id} | test_e2e_journeys, test_ops, test_files_misc |
| TUS resumable | OPTIONS/POST /tus/, HEAD/PATCH/DELETE /tus/{id}, /tus/{id}/status | test_uploads_tus |
| datasets CRUD/browse | /datasets[...], /versions, /sheets[...] | test_e2e_journeys, test_sheets_and_tags, test_api |
| diffs | versions/{a}/diff/{b}, sheets/{s}/diff/{b} | test_sheets_and_tags, test_e2e_journeys |
| tags + history | PUT/GET/DELETE tags, promote/rollback/history, tags list | test_sheets_and_tags, test_analytics_errors, test_quality |
| analytics ops | /sample, /sample/coordinated, /profile, /aggregate | test_coordinated_sampling, test_analytics_errors, test_api, test_advanced |
| samples/storage | /samples, /samples/{f}[, /data], /storage/usage | test_files_misc, test_api, test_coordinated_sampling, test_artifact_storage |
| retention/GC | /storage/retention, /storage/gc | test_artifact_retention (+ unit test_artifact_layout) |
| downloads | dataset + version downloads, format=xlsx workbook | test_advanced, test_e2e_journeys, test_analytics_errors |
| quality | /rules CRUD, validate, validations, promotion gates | test_quality (+ unit test_quality_engine) |
| library | /analytics defs CRUD + single GET, runs, publish, lineage | test_library |
| discovery | search/columns, datasets/search, facets, favorites, sheet-metadata, usage | test_discovery |
| jobs/audit | /jobs, /jobs/{id}, /audit | test_ops, test_api |
| COW replace / partial ingest / reuse | sheets/{s}/replace, include_sheets | test_advanced |
| explorer (Wave 1) | preview, query, columns/{c}, /sql | test_explorer (+ unit test_duck_sandbox) |
| profile runs + drift | profile-runs (POST/GET/detail), diff include=profile | test_profiling_runs (+ unit test_profile_insights, test_profile_drift) |
| saved views | /views CRUD, /views/{id}/run | test_saved_views, test_wave1_journeys |
| pivots (Wave 2) | /pivot, kind=pivot defs/runs/publish | test_pivot |
| exports | /samples/{f}/export | test_exports |
| timeline | /datasets/{id}/timeline | test_timeline |
| charts | /datasets/{id}/charts CRUD | test_charts |
| transformations (Wave 4) | /transformations CRUD, preview, run, runs, publish | test_transformations (+ unit test_transform_steps, test_expression_compiler) |
| relationships (Wave 5) | /relationships seed/suggest/CRUD/confirm/reject | test_relationships (+ unit test_relationship_suggester, test_fk_link_candidates) |
| join builder | /joins/preview, /joins/execute, /joins/{run}/publish | test_join_builder (+ unit test_join_warnings) |
| sub-sampling | /sample/coordinated (relationship_id, sampling_steps) | test_coordinated_sampling |

Cross-cutting contracts covered everywhere: sheet-selection-required 400, cross-team 404
existence hiding, 403 in-team permission failures, validation gates, problem+json shape,
audit trail, pagination envelope.

**Run locally:**
```bash
docker start analytics-pg analytics-minio   # postgres:16-alpine on :5432 (accelerator/accelerator),
                                            # MinIO on :9000 (console :9001), bucket 'analytics' exists
cd apps/analytics-service
venv/bin/python -m pytest -q                # FULL suite: unit + integration on BOTH backends (~3 min)
venv/bin/python -m pytest tests/unit -q     # fast layer only (~4s, no containers needed)
venv/bin/python -m pytest -q -k "[local]"   # integration on local backend only
venv/bin/python -m app.infra.db.postgres.migrate apply   # migration runner (checksummed)

# Live-HTTP e2e (104 curl checks, UI-shaped journeys incl. RBAC surface).
ACCELERATOR_AUTH_ENABLED=true \
  venv/bin/python -m uvicorn app.main:app --port 9009 &
BASE=http://localhost:9009 scripts/e2e_curl.sh
```
`.env` holds live API keys — gitignored, keep it that way. Tests seed nothing; they rely
on System superuser `00000000-...-0001` seeded by migrations.

## Forward roadmap → see ROADMAP.md

A new product-strategy doc was evaluated 2026-08-04; the resulting forward plan
(30 features in 7 dependency-ordered waves + ops track, incl. a sandboxed raw-SQL
escape hatch in Wave 1) lives in **ROADMAP.md**, which
supersedes the phased roadmap below (all five phases of which are complete). The deferred
items in the next section are folded into ROADMAP.md Wave 0 / Wave 5 — start there.

## Next session — the waves are done; what's left is the ops track

**ROADMAP §1–§29 are all built and shipped.** `WAVE4-6-PLAN.md` was the design
input for Waves 4–6 and is now spent — it is kept for provenance, but this
handoff is the as-built record and supersedes it. `ROADMAP.md` remains the
design/prose reference for each feature.

One thing to know before picking up anything new:

**Two design defects were found by tests, not by review** — the relationship
scorer's non-monotonic weighted sum (a strong name could outvote a fatal
signal) and a postal-code regex that matched any short string. Both are now
pinned by unit tests. The pure-function + unit-test split is what caught
them; keep new scoring/probe logic pure and separately tested.

**Remaining work is the ops track** (listed at the end of this file): a real
retry/backoff policy on the job worker, TUS staging on S3, and the SSO/JWT swap
at `auth/deps.py::get_principal`. None of these are feature work.

One ops item is now half-done: `artifact_gc` exists as a registered job with an
admin trigger (`POST /storage/gc`), but nothing *schedules* it — there is no
periodic scheduler in the service. Either add one, or point the deployment's
cron at that endpoint. Its sweep is deliberately bounded (`GC_BATCH`), so a
backlog wants repeated runs rather than one long one.

**Implementation facts that hold everywhere (learned building Wave 0):**
- All four sheet-row write paths flow through `files/repo.py::insert_version_sheets`;
  logical-sheet linkage is automatic for any new version-producing feature.
- `_register_output_artifacts` (data_accelerator/api.py) is mandatory for any new
  endpoint that persists a derived file. Since the storage-layout change it is
  not merely an authorization concern: the storage key embeds team/dataset/kind,
  so an unregistered blob is *unreachable* — no /samples request can produce its
  key — and the retention sweep will eventually reclaim it. Build the key with
  `ArtifactLayout(kind, team_id=…, dataset_id=…)` on the write side and the same
  layout on the register side; `_output_layout()` gives you both.
- New artifact kinds need three edits, or they are silently wrong: the
  `artifacts_artifact_type_check` constraint, `retention.RETENTION_DAYS`
  (`tests/unit/test_artifact_layout.py` fails if the two drift apart), and the
  `ArtifactLayout` kind string used at the write site.
- Confirm-rename rewrites `quality_rules.sheet_selector`, FK `parameters.ref_sheet`,
  and `dataset_sheet_metadata.sheet_key` — new sheet-keyed state you add must be
  either keyed by logical_sheet_id (preferred; follows automatically) or added to
  `shared/repo.py::reassign_logical_sheet` (then extend `count_logical_sheet_state`
  so the 409 conflict guard sees it).
- Cross-feature repo imports are accepted style (files/api → library repo,
  sampling → quality repo).
- Tests: add every new table to `tests/conftest.py::_TRUNCATE` (children before
  parents; verify no FK from outside the set). pytest.ini already passes `-q`
  (adding another -q hides the summary line). Journey-style tests
  (`test_wave0_journeys.py`, `test_wave1_journeys.py`) are the template for
  UI-path coverage; unit layer must stay Postgres-free.
- Wave-1-earned facts: `resolve_version_sheet_row` (shared/datasets.py) is the
  row-returning twin of `resolve_version_sheet_path` — use it when you need
  schema_json with identical selection semantics. `_selector_pin` returns
  resolve_version KWARGS ({}, {"tag":…}, {"version_number":…}), not an id.
  A locked sandboxed DuckDB conn can never write files — export results as a
  DataFrame and persist from a fresh conn. `duckdb.extract_statements` typing:
  read-only PRAGMAs are SELECT (harmless table functions), config PRAGMAs are
  SET. Pandas dedupes duplicate Excel headers as `Name.1` — physical names in
  schema_json reflect that.
- Wave-2-earned facts: totals over aggregated results must be RE-AGGREGATED
  from the source at the coarser grain (pivot.py::_aggregate_into is reused at
  every grain) — never sum cells; mean/nunique lie otherwise. Derived files
  (exports) inherit the SOURCE artifact's dataset/team ownership so /samples
  authorization holds transitively. The timeline is one UNION ALL in
  `discovery/repo.py::_TIMELINE_EVENTS` — any new run/history table (e.g.
  Wave-4 transformation_runs) should add a branch there. New analytics kinds
  are three CHECK widenings (kind, artifact_type, lineage relation) + one
  `execute_definition` branch + one publish relation-map entry — §11's
  migration is the template. `scripts/e2e_curl.sh` is the live-HTTP contract
  suite (52 checks) — extend it with each new endpoint family and keep it at
  0 failures.
- Wave-3-earned facts: catalog signals and the §17 health dimensions MUST come
  from one source — `discovery/repo.py::signals_lateral()` computes the three
  catalog buckets (validation/schema-drift/documentation) from the same tables
  the `evaluate_*` health functions read, and reuses `DOC_COLUMN_COVERAGE` from
  `health.py` (imported lazily to dodge the health↔repo cycle). Any list/facet
  filter referencing those signals must inject `signals_lateral()` into BOTH
  the COUNT and the row query (they share `where`). `test_catalog_facets.py`
  double-checks catalog == health. A column that flips dtype when a null
  appears (e.g. BIGINT→DOUBLE) legitimately registers as schema drift — pin
  fixtures with float columns to AVOID churn (the health tests do exactly this).

- Wave-4-earned facts: a step compiler that folds a **running schema** beside the
  SQL turns "column doesn't exist at this point in the chain" from a DuckDB
  error into a 400 that names what IS available — worth the extra bookkeeping.
  `compile_filter` writes set-relative operators against a `_filter_src` view;
  inside a CTE chain that "whole input" is the preceding CTE, so a single
  `FROM _filter_src` → `FROM step_n` substitution buys the full operator
  vocabulary. Every value a user supplies must be a `?` bind and every token a
  `Literal`-mapped whitelist entry — that pair is what makes a
  user-constructed pipeline safe, and both `expr.py` and `compile.py` state it
  as an invariant at the top of the file.
- Wave-5-earned facts: when combining evidence, distinguish **weights** from
  **floors**. A weighted sum alone let a strong name match outvote a fatal
  signal (a repeating "parent" key is a many-to-many, not a reference); the fix
  is a separate `qualifies()` predicate, not reweighting. Estimating join
  output by summing per-key multiplicities is exact, not approximate — worth
  testing against a real join per cardinality shape. `_run_sampling_pipeline_inner`
  is now parametrized (`source`/`output`/`persist`) — pass `persist=False` for
  any nested use, or it leaves an unregistered orphan blob in the samples area
  that no /samples request can ever authorize.
- Shared-storage gotcha (bit twice): `_db_cleanup` truncates DB tables but the
  **samples area persists across tests**. Assertions like "no join_* file
  exists" pass alone and fail in a full run — compare a before/after count
  instead.

**Ops track (still open, schedule alongside waves):**
- Real job worker pulling the `jobs` table (replaces BackgroundTasks) — required by
  Wave 4, valuable from §8 onward.
- **TUS staging on S3:** TUS chunks always stage on local disk today.
- SSO/JWT swap at the single `app/features/auth/deps.py::get_principal` seam.
- Small hardenings landed with the tests (2026-08-05): sampling schema now rejects
  non-positive `sample_size`/`sample_fraction`/`target_total_volume` (422 — a negative
  size used to reach DuckDB as `LIMIT -5` and crash unhandled), and
  `publish_run(new_dataset)` 409s on a duplicate dataset name in the team (the table has
  no unique name constraint, so publish could silently shadow an existing dataset).

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
- Version contents + source-derived metadata are frozen once processing completes;
  legacy versions may receive explicitly tracked metadata backfills
  (`schema_backfilled_at`). Tags → whole versions only (no mixed-sheet tags).
- Partial workbook processing failure ⇒ version status = failed (atomic publication default).
- sheet_count = 1 ⇒ `sheet` may be omitted; sheet_count > 1 ⇒ `sheet` is required —
  problem+json "sheet-selection-required" listing sheets, never silently pick one.
- Every NEW ready sheet row has an explicit storage_key, checksum, row_count, and schema.
- current_version_id: same dataset (DB-enforced), status ready, highest ready version wins.
- Tag names are lowercase slugs (DB CHECK); every tag mutation appends exactly one
  history row (same transaction) with actor + request_id.
- Cross-team existence hiding (404 not 403) must extend to all new endpoints.
- New write/egress endpoints must land in the audit trail (middleware does this if routed
  under the protected router).

## Note on AI assistance (removed)

Wave 6 (§25–§29 of ROADMAP.md) built an AI-assistance slice
(`app/features/ai/`: semantic inference, rule/transform suggestions, dataset
summaries, NL→query) and it shipped and was tested as described above. It was
later deliberately removed as a product decision — not a bug fix. ROADMAP.md
still describes it as designed/built; this handoff has been edited to drop
Wave 6/AI specifics accordingly, since they no longer describe the running
system.
