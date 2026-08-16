# Analytics Service — Forward Roadmap (2026-08-04)

Supersedes the phased roadmap in HANDOFF.md ("Product roadmap (evaluated 2026-08-03)"),
whose five phases are ALL COMPLETE. This plan was produced by evaluating the
"Tabular Dataset Platform — Feature Strategy" product doc against the as-built service.

**Evaluation verdict on the product doc:** directionally sound, but written without full
knowledge of current state — roughly a third of its pillars already exist and are tested
(version comparison at schema level, data quality + promotion gates, catalog/discovery,
publishing + lineage, within-workbook joins, and all core principles, which are our
standing invariants). Its phasing is therefore not adopted as written; the genuine gaps
are adopted below in dependency order.

**Ordering logic:** fix the one structural defect first (logical sheet identity), then
build the shared query substrate everything else reuses (the filter DSL), then the
pillars in dependency order, AI last. Items within a wave can run in parallel; waves are
ordered because later ones depend on earlier ones.

---

## Wave 0 — Foundations and small hardenings

### 1. Logical sheet identity + confirm-rename  ← hard prerequisite
`dataset_sheets` table with `logical_sheet_id`; a rename-confirmation flow built on the
diff endpoint's existing rename candidates. Today `dataset_sheet_metadata` and
`quality_rules.sheet_selector` key on name-derived `sheet_key`, so a sheet rename
silently detaches semantic metadata and quality rules (disarming the promotion gate).
Every feature below adds sheet-keyed state (views, pivots, dictionary, relationships) —
this must land first or they all inherit the defect.

### 2. Structured filter/expression DSL (design once, own deliverable)
The `{logic, conditions}` model plus sort and cursor pagination (see Technical design §2 —
a compiler for this shape already exists in `app/shared/filters.py`; this item formalizes
it into a typed public contract rather than building from scratch).
Shared contract for the explorer query (6), saved views (10), pivots (11), quality
distribution rules, transformation filter steps (19), and the NL query builder (28).

**Why a DSL instead of raw SQL (DuckDB stays the engine):** the DSL *compiles to*
parameterized DuckDB SQL — exactly the pattern `quality/engine.py` already uses. The DSL
is the API contract layer:
- **Security/scoping.** Access resolves dataset → version → sheet → storage key
  server-side with RBAC + cross-team 404 hiding. Raw user SQL could reference arbitrary
  files/tables (`read_parquet`, `ATTACH`, `COPY TO`) and bypass all of it; structured
  filters can only reference columns of the one authorized sheet.
- **Validation before I/O.** Filters validate against the sheet schema in Postgres →
  clean problem+json 400s (unknown column, type mismatch) with no file reads, instead of
  leaked DuckDB parser errors.
- **Configs must be data, not text.** Saved views/pivots/transform steps/NL output need
  to be rendered back into UI builders, diffed, and *rewritten* (re-point column refs on
  rename under logical sheet identity). Arbitrary SQL strings can't be safely rewritten.
- **Stable public contract.** Engine, row caps, cursoring, and rewrites evolve behind it
  without breaking saved definitions.
Real cost is small: a JSON→SQL compiler with a column/operator whitelist — a
generalization of what the quality engine already does.

Raw SQL from users is NOT banned outright — it's confined to the sandboxed ad-hoc
endpoint (6b), which is engine-sandboxed rather than string-filtered. The DSL remains
the only format for anything *saved or UI-driven* (views, pivots, transforms), because
those must be renderable and rewritable.

### 3. Opportunistic aggregation extras
HAVING, sort/limit completion, date/numeric bucketing, conditional aggregates. Cheap
DuckDB wins (already on the deferred list); also the substrate the pivot builder (11)
compiles onto.

### 4. Corrupt-upload 400
Sync upload of a corrupt file currently surfaces as a 500 problem; make it a problem+json
400. Update the pinned test
`tests/test_files_misc.py::test_corrupt_xlsx_fails_without_ready_version`.

### 5. Auto-key defaults for coordinated sampling
Use existing `foreign_key` quality rules (`ref_sheet` + `ref_column`) as persisted
child→parent edges to default `related` links when the caller omits keys. Small; becomes
the seed of the relationship model (22).

---

## Wave 1 — Exploration (the Dataset Exploration Workspace)

### 6. Dataset explorer query endpoints
`preview` + `POST .../query` under the existing nested route shape
(`/datasets/{id}/versions/{v}/sheets/{sheet}/...`), honoring sheet-selection-required and
404-hiding. Column projection, structured filters, multi-sort, search-in-string-columns,
cursor pagination — compiled to DuckDB over existing per-sheet parquet. Biggest gap
today: the only row-level read is a 5-row preview on the single-sheet GET.

### 6b. Raw SQL escape hatch (ad-hoc, sandboxed)
`POST /datasets/{id}/versions/{v}/sql`: power users submit a SQL string; every sheet of
the authorized version is registered as a read-only DuckDB view named by its `sheet_key`
(synthetic `data` for CSV/Parquet), so cross-sheet joins within the version work
naturally. Safety is **engine-enforced, never regex/blocklist** (string-matching SQL is
evadable and DuckDB's replacement scans read files from bare string literals in FROM):
- read-only connection; only the version's sheet views registered
- `SET enable_external_access = false` (kills ATTACH/file reads/httpfs) then
  `SET lock_configuration = true` (query can't flip settings back)
- single-statement SELECT-only check, query timeout, hard row cap
- normal RBAC + cross-team 404 + audit trail; DuckDB errors sanitized into problem+json
Deliberate limits: ad-hoc only — raw-SQL queries CANNOT be saved as views/pivots (we
can't rewrite or render SQL strings on rename; saved definitions stay DSL-only).
Results are exportable and, via the standard artifact path, publishable with lineage
(`queried_from`).

### 7. Column explorer
`GET .../columns/{column}`: null count/%, distinct/uniqueness, min/max/quantiles,
top/rare values, histogram, string-length distribution, candidate-key status, examples.
Extends the existing profiler into a per-column read API.

### 8. Profile runs + rule-based insight engine
Persist profiles as `profile_runs` + `profile_insights`. NOTE: the 2026-08-05 schema
review explicitly declined `profile_runs` "until a feature pays for it" — this is that
feature; revisiting is legitimate, not churn. Deterministic rules only (no LLM):
likely-PK, constant column, null-rate spike, new/unexpected categories, high correlation,
future timestamps, new sheet added. Insights carry evidence, severity, sheet/column.

### 9. Profile-drift comparison
Extend the schema-only diff endpoints with profile deltas: row-count, null-rate,
distinct-count, distribution shift, category adds/removes, duplicate-rate. Computed on
demand from the two versions' profile runs; cache later if needed. Completes the
upload → compare → validate → promote review loop; feeds health (17) and a future
distribution-shift quality rule.

### 10. Saved views
`dataset_views` keyed by `logical_sheet_id`, reusing the existing
`version_selector: current|tag|version` pattern from `analytics_definitions` and the
Wave-0 DSL. CRUD + `/run` (= feature 6's query with stored config).

---

## Wave 2 — Analysis

### 11. Pivot builder
`pivot_definitions` (rows/columns/values/filters/subtotals/grand totals, incl.
percentage-of-row/column/grand-total aggregations) + `/run`, compiled onto the extended
aggregation engine (3). Treat "pivot" as a new analytics kind: reuse
`analytics_definitions`/`analytics_runs`/`artifacts`, not a parallel system.

### 12. Exports + publish for query/pivot results
CSV/XLSX/Parquet export; publishing a pivot result as dataset/version rides the existing
publish + lineage path (new relation, e.g. `pivoted_from`).

### 13. Dataset timeline
One read-only composite endpoint merging version uploads, profile completions,
validation runs, tag history (actor + request_id), publishes, and audit events. All raw
materials exist — cheap, high perceived value.

### 14. Chart definitions (backend only)
Thin `chart_definitions` referencing a pivot/analytics definition — no query logic of its
own. Mostly a frontend concern; blocks nothing.

---

## Wave 3 — Documentation and health

### 15. Data dictionary (column level)
`dataset_column_metadata` keyed by `logical_sheet_id` + normalized column name: business
name, description, semantic type, allowed values, unit, sensitivity. Dataset- and
sheet-level docs largely exist (domain/owner fields, grain/PK metadata) — this fills the
column level.

### 16. Duplicate + missing-data explorers
Views over the profiler/insights (7–8): exact and column-subset duplicate groups,
missingness by column, rows-most-missing, null-rate deltas between versions.
Keep-first/last remediation belongs to Wave 4 (19), not here.

### 17. Dataset health
Multi-dimension summary (schema stability, validation, missing data, duplicates,
freshness, drift, documentation completeness) — no single opaque score; each dimension
links to evidence. Sequenced here: only honest once validation history (exists), drift
(9), and documentation (15) have real data.

### 18. Catalog upgrades
New search filters/facets now computable: has-schema-drift, validation status, health
dimensions, documentation completeness. Trivial extensions to existing facets/search.

---

## Wave 4 — Transformation and publishing

### 19. Transformation pipeline builder
`transformation_definitions` + `transformation_runs` on the existing `jobs` table:
select/rename/reorder/drop columns, filter (reuses DSL), deduplicate (incl.
keep-first/last), trim, case-normalize, replace values/nulls, cast, parse dates,
split/merge columns, sort, limit. Preview-on-sample before full run; output = artifact;
publish rides existing publish/lineage (`transformed_from`). Non-destructive throughout
(immutability invariant).

### 20. Computed-column / formula builder
Safe structured expression trees (arithmetic, concat, conditionals, date extraction,
coalesce, rounding) compiled to DuckDB expressions — never raw SQL from users. Ships as a
step type inside 19; optionally as virtual columns in explorer queries.

### 21. Output-profile inspection on transform runs
Auto-profile transformation outputs and surface insight deltas vs. the source (reuses
8–9), closing define → preview → run → inspect → publish.

---

## Wave 5 — Relationships and joins

### 22. Relationship discovery + confirmed relationship model
`dataset_relationships` (suggested/confirmed/rejected, with evidence) seeded from FK
quality rules (5), then a statistical suggester: name match, type compatibility,
value-overlap coverage, target uniqueness. Confirmed relationships are the input to 23–24.

### 23. Standalone join builder
Guided inner/left joins on confirmed relationships with required pre-execution warnings:
duplicate keys either side, estimated row expansion, unmatched-key %, column collisions,
many-to-many detection. Generalizes the existing within-workbook `JoinSpec` in
`/aggregate`; output publishable with lineage. Cross-dataset joins only here, once
relationships are confirmed objects.

### 24. Related-sheet sub-sampling
Coordinated-sampling v2 (explicitly deferred from v1): sample within filtered related
rows instead of keeping all of them, driven by confirmed relationships.

---

## Wave 6 — AI assistance (deliberately last)

### 25. Column semantic inference
Email/phone/currency/country/timestamp — pattern-based first, LLM optional later. Feeds
dictionary (15) and rule suggestions (26).

### 26. Quality-rule suggestions
From profiles and relationships ("customer_id appears unique → uniqueness rule?").
Always proposed, never auto-enabled.

### 27. Dataset summaries
Readable narrative over profile, insights, and timeline.

### 28. Natural-language query builder
NL → the Wave-0 structured DSL, shown to the user for inspection before running.
Never NL → SQL.

### 29. Transformation suggestions
Propose trim/case/parse-dates/dedupe steps from insight evidence.

**AI guardrails:** no raw datasets to external models by default, no LLM-executed SQL,
no auto-publishing, AI output always inspectable with deterministic machinery underneath.

---

## Parallel ops track (not product features; schedule alongside any wave)

- Real job worker pulling the `jobs` table (replacing BackgroundTasks) — target around
  Wave 1–2, when profiling/transform runs become routine.
- TUS staging on S3 (currently always local disk).
- SSO/JWT swap at the single `get_principal` seam (`app/features/auth/deps.py`) —
  whenever the org side is ready.

## Still excluded (unchanged; the product doc agrees)

More sampling algorithms, distributed compute, streaming, full spreadsheet editing,
cell-level/delta versioning, BI dashboards, workflow orchestration, ML training, vector
DBs, general SQL notebooks, macro/Excel-format preservation, fine-grained billing.

## Cross-cutting rules for every item above

- All new endpoints keep the invariants: sheet-selection-required 400,
  cross-team 404 existence hiding, audit-trail coverage, immutable versions,
  whole-version tags, problem+json errors, pagination envelope.
- Every new definition table keys on `logical_sheet_id` (feature 1) — never on
  name-derived `sheet_key`.
- Every run-producing feature records reproducibility metadata (source
  dataset/version/sheet, params, seed where applicable, algorithm version, timing,
  artifact, warnings) — the pattern `analytics_runs` already follows.

---

# Technical design (2026-08-04, verified against the code)

Produced by reviewing every Wave 0–1 claim against the as-built service. All structural
claims above hold (no logical sheet identity anywhere; `sheet_key`-keyed state in exactly
`dataset_sheet_metadata` + `quality_rules.sheet_selector`; the only row-level read is the
`LIMIT 5` preview in `app/shared/data_io.py:520`; diff rename candidates are advisory).
Four verified facts change the work relative to the plan text above:

1. **The filter DSL partially exists.** `app/shared/filters.py` implements
   `compile_filter` over `Filter{column, op, value, case_sensitive}` /
   `FilterGroup{logic: and|or, conditions}` with ~30 operators, `quote_ident` identifiers
   and `?` binds; sampling steps already pass these as untyped dicts. Feature 2 is a
   formalization job (typed Pydantic contract, schema validation, sort, cursor), not
   greenfield. The public shape is `{logic, conditions}` — adopted from the code, not the
   `{"and": [...]}` literal originally sketched.
2. **No cursor pagination exists anywhere.** `Page[T]` (`app/api/pagination.py`) is
   offset/limit only. Query endpoints introduce a separate `QueryPage{items, next_cursor}`
   envelope.
3. **No DuckDB hardening exists anywhere.** No `read_only`, `enable_external_access`,
   timeouts, or row caps in the codebase; connections are ad-hoc per request. The 6b
   sandbox is built from zero — and note `sanitize_filter_expr` (`app/shared/utils/
   sql.py:42`) is exactly the regex-blocklist this roadmap calls evadable; the DSL rollout
   deprecates `filter_expr` on all saved/UI paths.
4. **The identity refactor touches ~15 files** and two independent copies of the
   `sheet_key` normalization rule (`app/shared/data_io.py:165` and the SQL backfill regex
   in `20260804000000_sheets.sql:60`) that must stay in sync.

## §1 Logical sheet identity

One migration (`dataset_sheets` + backfill):

- `dataset_sheets(id UUID PK /* = logical_sheet_id */, dataset_id FK CASCADE,
  current_sheet_key TEXT, display_name TEXT, first_seen_version_id FK SET NULL,
  retired_at TIMESTAMPTZ, created_at)`; partial UNIQUE `(dataset_id, current_sheet_key)
  WHERE retired_at IS NULL`.
- `ADD COLUMN logical_sheet_id UUID FK dataset_sheets` on `dataset_version_sheets`
  (NOT NULL after backfill), `dataset_sheet_metadata`, `quality_rules`.
- Backfill in pure SQL: group `dataset_version_sheets` by `(dataset_id, sheet_key)` → one
  `dataset_sheets` row each; join-update the three FKs. Encodes today's implicit identity
  (same key = same sheet) as the starting state.

Ingest linkage (`files/services/processing.py` + `files/repo.py`): on version completion,
match new sheet rows to live `dataset_sheets` by `sheet_key`; unmatched → create a new
`dataset_sheets` row. Never auto-retire; "absent from current version" is computed state.

Confirm-rename: `POST /datasets/{id}/versions/{v}/confirm-rename` with
`{from_sheet, to_sheet}`, validated against the diff's fingerprint-matched candidates
(`diffs.py:77-99`) but accepting non-candidate pairs with `force: true` (rename +
schema change won't fingerprint-match). Effect: re-point the new row's
`logical_sheet_id` to the old sheet's id, delete the spurious auto-created
`dataset_sheets` row, update `current_sheet_key`/`display_name`. Metadata and quality
rules keyed by logical id follow automatically — that is the point of the feature.

Deliberately unchanged: URLs stay name-addressed (resolution gains one hop:
name → version sheet row → logical id); physical parquet layout stays keyed by
per-version `sheet_key` (versions are immutable, so per-version keys are safe);
`sheet_selector` / `dataset_sheet_metadata.sheet_key` columns remain for display and
back-compat, but resolution goes logical-id-first. Write paths that must carry logical
ids through: `processing.py`, `replace.py` (COW), `library/service.py` publish
(synthetic `data` sheet gets a logical id in the new dataset). Highest-risk Wave-0 item.

## §2 Filter/expression DSL

New package `app/shared/query/`:

- `schemas.py` — typed `Filter` / `FilterGroup` (formalizing the existing dict shape so
  current sampling-step payloads validate unchanged), `Sort{column, direction}`,
  `QuerySpec{columns?, filters?, sort?, search?, cursor?, limit?}`.
- `validate.py` — resolve the sheet's `schema_json` from Postgres; check every column
  ref + operator/type compatibility BEFORE file I/O → problem+json 400
  (`unknown-column`, `operator-type-mismatch`).
- `compile.py` — reuse `compile_filter` as-is; add sort compilation and
  search-in-string-columns (`OR icontains` over text columns from the schema).
- Cursor: exploit version immutability — opaque base64 of
  `{version_id, spec_hash, offset}`. Stateless and stable because the underlying parquet
  never changes; 400 if `spec_hash` mismatches the resubmitted spec. Default limit 100,
  max 1000. Lives beside `Page[T]`, not inside it.

## §3 Aggregation extras

Extend `AggregateRequest` (`data_accelerator/schemas.py:632`): `having` (structured
conditions over agg aliases only), finish sort/limit, `group_by` entries become
`str | Bucket{column, date_trunc | bin_width | bin_count}`, `AggregationSpec.filter`
(a `FilterGroup`) compiling to `FILTER (WHERE ...)`. Replace `filter_expr` with
`filters: FilterGroup` (accept `filter_expr` deprecated for one release). Add a
server-side output row cap — there is none today.

## §4 Corrupt-upload 400

Catch the parse failure in the sync path of `files/services/processing.py`, raise
`ProblemException(400, code="invalid-file")`; update the pinned test.

## §5 Auto-key defaults for coordinated sampling

In `run_coordinated_sampling` (`sampling.py:536`): when a `RelatedSheetLink` omits keys,
query enabled `foreign_key` rules whose sheet/ref_sheet match the (related, parent) pair;
exactly one match → use it; zero or multiple → 400 listing candidates.

## §6 Explorer query endpoints

New vertical slice `app/features/explorer/` (api/schemas/service) on the protected
router, full paths per the data-plane convention:

- `GET .../versions/{v}/sheets/{sheet}/preview?limit=` — resolve via
  `ensure_dataset_permission` + `resolve_version_sheet_path`, `SELECT * LIMIT n`.
- `POST .../versions/{v}/sheets/{sheet}/query` — body `QuerySpec`;
  validate → compile → execute over the sheet parquet → `QueryPage`.

Sheet-selection-required and single-sheet auto-resolve come free from
`resolve_version_sheet_path`.

## §6b Sandboxed raw SQL — materialization wrinkle

`SET enable_external_access = false` kills ALL file access, including the lazy
`read_parquet` scan backing a view and including local files. "Register views, then lock
down" therefore does NOT work. Working design:

1. Fresh connection; while access is still enabled, MATERIALIZE each sheet:
   `CREATE TABLE <sheet_key> AS SELECT * FROM read_parquet(...)` (uniform for local + S3
   via the existing `_connect_s3` path).
2. Then `SET enable_external_access = false; SET lock_configuration = true;`.
3. Statement gate via DuckDB's own parser (`duckdb.extract_statements`): exactly one
   statement, type SELECT. Never regex.
4. Timeout via watchdog thread calling `conn.interrupt()` (DuckDB has no
   statement-timeout setting); row cap by wrapping:
   `SELECT * FROM (<user sql>) LIMIT cap+1`, flag truncation.
5. Sanitize DuckDB errors (strip storage paths/keys) into problem+json.

Materialization costs memory proportional to version size → guard on total sheet
`size_bytes` (`version-too-large-for-sql` 413-style problem). Honest v1 limit. All of it
lives in a new shared helper (`app/shared/duck.py`,
`open_sandboxed(sheets, timeout_s, row_cap)`) — also the seed of centralized DuckDB
hygiene, which currently doesn't exist.

## §7 Column explorer

Factor `profile_column_duckdb` (`profiling.py:62`) so single-column stats run standalone;
add candidate-key check and top/rare values; one route.

## §8 Profile runs + insights

- `profile_runs(id, dataset_version_id FK CASCADE, logical_sheet_id FK, job_id FK,
  status, profile JSONB, algorithm_version, started_at, completed_at)`;
  UNIQUE `(dataset_version_id, logical_sheet_id, algorithm_version)` — idempotent
  re-profiling per engine version.
- `profile_insights(id, profile_run_id FK CASCADE, rule TEXT, severity, column_name,
  evidence JSONB)`.
- Rides `jobs` (`job_type='profiling'` already in the CHECK). Insight rules are pure
  functions over persisted profile JSON — unit-testable without Postgres.

## §9 Profile-drift diff

`include=profile` query param on the two diff endpoints; load both versions'
`profile_runs`, compute deltas in Python; problem `profile-required` naming the missing
side. No caching until it hurts.

## §10 Saved views

`dataset_views(id, dataset_id FK CASCADE, logical_sheet_id FK dataset_sheets NOT NULL,
name, description, version_selector JSONB, query JSONB /* QuerySpec */, created_by,
timestamps, UNIQUE(dataset_id, name))` — first table born under the logical-id rule.
CRUD + `/run`: pin version via the library's `_selector_pin` pattern, resolve the logical
sheet in that version (rename-proof — feature 1's payoff), execute §6's query. Column
renames within a sheet remain a gap: acceptable v1, clean `unknown-column` 400 on run.

## Waves 2–6 condensed

- **11 Pivot:** widen `analytics_definitions.kind` CHECK with `pivot`; params =
  `PivotSpec` compiled onto §3 (percentage-of-total = window functions over the
  materialized agg result). Runs/artifacts/publish untouched. Real design task =
  `PivotSpec` itself.
- **12 Exports:** `COPY TO` CSV/XLSX/Parquet from an artifact;
  `artifact_type='export'` (already in CHECK); publish rides `publish_run` with new
  `pivoted_from` lineage relation.
- **13 Timeline:** read-only merge of existing tables (versions, tag history, validation
  runs, audit, lineage); one composite endpoint, offset paging. Cheap.
- **15 Dictionary:** `dataset_column_metadata(id, dataset_id, logical_sheet_id FK,
  column_name /* normalized */, business_name, description, semantic_type, unit,
  sensitivity, allowed_values JSONB, updated_by/at,
  UNIQUE(logical_sheet_id, column_name))`.
- **17 Health:** pure read-model over existing signals; no new tables.
- **19–21 Transforms:** `transformation_definitions/_runs` mirroring library tables;
  steps as a discriminated union (filter steps embed `FilterGroup`; computed columns are
  typed expression trees). Output → artifact → existing publish (`transformed_from`).
  **Forces the ops-track worker** — schedule the worker before or with Wave 4, not
  "around Wave 1–2".
- **22–24 Relationships:** `dataset_relationships(dataset_id, from/to logical_sheet_id +
  column, status suggested|confirmed|rejected, evidence JSONB, method)`; seeded from FK
  rules (§5's lookup becomes the seeder); suggester = overlap/uniqueness DuckDB probes as
  a job. Join builder generalizes `JoinSpec` with pre-flight warning probes (distinct
  counts, anti-join match rate).
- **25–29 AI:** all consume persisted structures from earlier waves; NL→DSL emits a
  `QuerySpec` the user inspects. No new substrate.

## Cross-cutting build rules

- One migration per feature; checksummed runner; `-- migrate:up/down` sections; keep
  `normalize_sheet_key` (Python) and any SQL backfill regex in lockstep.
- Tests per the established architecture: DSL validator/compiler + sandbox gate as fast
  unit tests (in-memory DuckDB + tmp parquet); every new endpoint integration-tested on
  both storage backends via the parametrized `client` fixture; every new route asserts
  the standing contracts.
- Sequencing: §1 ∥ §2 (independent); §6/§6b/§10 depend on §2; §10 depends on §1; §8
  before §9 and §17. Biggest rocks in Waves 0–1: §1 (refactor breadth) and §6b (sandbox
  machinery). §4, §5, §7, §13 are each ≤ a day.
