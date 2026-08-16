# Waves 4–6 — turn-key implementation plan (2026-08-05)

Three code-grounded design passes (one per wave, each verified against the
actual code) distilled into an execution plan. Read the matching **ROADMAP.md**
"Technical design" section alongside each item. Build order is top-to-bottom;
after **each** feature: suite green on both backends, extend
`scripts/e2e_curl.sh` (keep 0 failures), commit per feature, update HANDOFF.

**Already landed this session (main):** the job worker (commit `1a9d235`) —
`app/shared/worker.py` (handler registry, atomic `FOR UPDATE SKIP LOCKED`
claim, `run_pending_jobs_once`, `run_worker_loop`, `dispatch(inline=…)`),
started/stopped in `app/main.py` lifespan, settings
`job_worker_enabled`/`job_worker_poll_seconds`, tests `tests/test_job_worker.py`.
The loop only claims job_types with a **registered** handler, so existing
inline paths are untouched. **Wave 4 §19 uses it directly:**
`await worker.dispatch("transform", params=…, inline=(sync flag))` — register
the transform handler in a feature module imported at `app/main.py` top so the
loop sees it.

Migration cursor: latest on disk is `20260808000000_column_metadata`. Use the
next free day-stamp per feature; **`ls migrations | tail -3` before finalizing
filenames** (the runner applies lexically by filename and is checksummed).

---

## WAVE 4 — Transformation & publishing

### §19 Transformation pipeline builder  → migration `20260809010000_transformations.sql`

New vertical slice `app/features/transform/` (`steps.py`, `expr.py`,
`compile.py`, `schemas.py`, `repo.py`, `service.py`, `api.py`); register router
in `app/main.py` after `library_router`.

**Tables** (mirror the library pair `20260804040000_reuse.sql`):
- `transformation_definitions(id, dataset_id FK CASCADE, logical_sheet_id FK
  dataset_sheets NOT NULL, name, description, version_selector JSONB
  DEFAULT '{"mode":"current"}', steps JSONB DEFAULT '[]', created_by, created_at,
  updated_at, UNIQUE(dataset_id,name))` — **keyed on logical_sheet_id** → survives
  confirm-rename with no extra work.
- `transformation_runs(id, definition_id FK CASCADE, dataset_version_id FK
  SET NULL, job_id FK SET NULL, status CHECK(running|completed|failed),
  mode CHECK(full|preview), result_summary JSONB, artifact_id FK SET NULL,
  output_profile JSONB, source_drift JSONB /* §21 */, triggered_by, started_at,
  completed_at, error)`.
- **Two CHECK widenings** (template `20260807020000_pivot.sql`): `artifacts.artifact_type`
  += `transform_output`; `dataset_lineage.relation` += `transformed_from`.
  (No `jobs.job_type` widening — `transform` is already in the baseline CHECK.)

**Step model** — discriminated union `Annotated[Union[...], Field(discriminator="type")]`
in `steps.py`. Types: `select` `drop` `rename` `reorder` `cast`(type whitelist)
`trim` `case_normalize` `replace`(exact/substring/regex + nulls_to) `parse_dates`
`split` `merge` `compute`(§20 expr) `filter`(**embeds `app.shared.query.schemas.FilterGroup`**)
`deduplicate`(subset, keep first/last/none, order_by) `sort` `limit`.

**Compile** (`compile.py`, pure `compile_step(step, in_cols, binds) -> (sql, out_cols)`):
chain of CTEs, one per step — `WITH src AS (SELECT * FROM read_parquet('<path>')),
step_0 AS (…FROM src), step_1 AS (…FROM step_0), … SELECT * FROM step_n`. Validate
each step against the **running schema** folded step-by-step (projection steps mutate
the column set; downstream refs → `unknown-column` 400 with `available`). Per-step SQL:
cast→`CAST(col AS <TYPE>)` (whitelist map), trim→`TRIM/LTRIM/RTRIM`,
case_normalize→`LOWER/UPPER/INITCAP`, replace→`CASE`/`REPLACE`/`REGEXP_REPLACE`
(+`COALESCE` for nulls_to), parse_dates→`STRPTIME(col, ?)` (format is a **bind**),
split→`STR_SPLIT(col, ?)[i]`, merge→`CONCAT_WS(?, …)`, filter→`compile_filter(where)`,
deduplicate→`QUALIFY row_number() OVER (PARTITION BY subset ORDER BY order_by)=1`
(keep=none → `QUALIFY count(*) OVER(…)=1`), sort→`ORDER BY`, limit→`LIMIT`.
**Every user value is a `?` bind; identifiers via `quote_ident`; never interpolate text.**

**Endpoints** (protected router; RBAC as noted):
`POST /datasets/{id}/transformations` (create, WRITE — resolve+require sheet,
validate pipeline before persist), `GET`/`GET {def_id}`/`PATCH`/`DELETE` CRUD,
`POST .../{def_id}/preview` (compile over `USING SAMPLE n ROWS`, run in-request,
`{columns, rows, approximate:true, output_schema}`, no job/artifact),
`POST .../{def_id}/run?sync=false` (`worker.dispatch("transform", inline=sync)`;
handler pins version via `library.service._selector_pin` → `resolve_version_sheet_path`
→ compile → `COPY (<sql>) TO <tmp> (FORMAT PARQUET)` → `sampling._persist_table`
→ `library_repo.create_artifact(key,'transform_output',dataset_id,team_id)` →
`transformation_runs` completed), `GET .../runs` + `GET .../runs/{run_id}`,
`POST .../transformations/runs/{run_id}/publish` (reuse `library.service.publish_run`
shape; extract a shared `_publish_artifact_as_version(...)` or add a `relation` param;
records `transformed_from`; **non-destructive** — always new dataset/version).

**Reuse:** `_selector_pin`, `shared/datasets.resolve_version*`, `sampling._persist_table`,
`library/repo.create_artifact/record_lineage`, `_register_output_artifacts` semantics,
`shared/query.compile.compile_filter`, `shared/utils/sql.quote_ident/safe_value`.
Add a `transformation_runs` branch to `discovery/repo.py::_TIMELINE_EVENTS`.

**Tests.** Unit `tests/unit/test_transform_steps.py` (in-memory DuckDB + tmp parquet):
discriminated-union round-trip + unknown type; per-step SQL + out_cols + binds;
keep-first/last QUALIFY; cast whitelist; schema-fold (filter on dropped col →
unknown-column); execute small pipeline (dedupe/trim/replace) asserting rows.
Integration `tests/test_transformations.py` (both backends): create→preview→run
(sync)→row count vs source→`/samples/{output}` authorized + outsider 404→publish
`new_dataset`→lineage `transformed_from`→source unchanged; cross-team 404; viewer
WRITE 403; multi-sheet needs `sheet` 400; one async `sync=false`→`poll_run`;
confirm-rename then def still runs. `_TRUNCATE` += `transformation_runs`,
`transformation_definitions` (children first).

### §20 Computed columns / formula builder

A `compute` step type inside §19 (and optionally `computed: list[ComputedColumn]`
on the explorer `QuerySpec` for virtual columns — same compiler).

**Expression tree** (`expr.py`, discriminated union on `op`): `col` `lit`
`arith`(add/sub/mul/div/mod) `concat` `if`(cases[{when:FilterGroup,then}], else)
`coalesce` `round` `date_extract`(year/month/day/hour/minute/dow/week/quarter)
`cast` `str`(lower/upper/trim/length/substr). Pure `compile_expr(node, cols, binds)`:
`arith`→`(a <op> b)`, `concat`→`CONCAT_WS(?, …)`, `if`→nested `CASE WHEN
compile_filter(when) THEN … ELSE … END`, `coalesce`→`COALESCE(…)`, `round`→`ROUND(a,?)`,
`date_extract`→`EXTRACT(<part> FROM a)` (part from a **whitelisted Literal**),
`cast`→`CAST(a AS <TYPE>)`, `str`→`LOWER/UPPER/TRIM/LENGTH/SUBSTR`.
**Invariant: no node emits user text into SQL** — identifiers via `quote_ident`,
values via binds, `op`/`part`/`to`/`fn` are `Literal` enums mapped to fixed tokens.
Type checks: `date_extract` needs temporal, `arith` needs numeric → `operator-type-mismatch`
(reuse `validate._is_temporal`/`is_numeric_duckdb_type`). Gotcha: integer `/` →
cast a side to DOUBLE when `fn=div`; cap recursion depth.

**Tests.** Unit `tests/unit/test_expression_compiler.py`: each node → SQL + binds;
nested if/arith; unknown col 400; date_extract on VARCHAR → mismatch; execute a few
against in-memory DuckDB. Integration: a `compute` step inside `test_transformations.py`.

### §21 Auto-profile transform outputs

In the `transform` handler after the artifact is written: (1)
`out_profile = run_profiling(ProfileRequest(file_path=<artifact path>, include_correlations=True))`;
(2) source profile — prefer the persisted source `profile_runs` row (via
`explorer/repo`), else profile the pinned source sheet ad-hoc; (3)
`drift = compute_profile_drift(source_profile, out_profile, sheet_key=def.sheet)`
(pure, already unit-tested); optionally `compute_insights(out_profile, prev=source)`.
Persist `transformation_runs.output_profile`/`source_drift` (columns already in the
§19 migration). Surface on `GET .../runs/{run_id}`. Gotcha: byte-cap the auto-profile
(like `MAX_SQL_MATERIALIZE_BYTES`); don't couple to `profile_runs` uniqueness (output
has no logical sheet). Tests: extend `test_profile_drift.py` (drop col + add computed col
→ add/remove deltas); integration in `test_transformations.py` (row_count < source,
drift reflects new/removed cols; `source:profile_run` when a persisted run exists).

---

## WAVE 5 — Relationships & joins

New slice `app/features/relationships/` (`api.py`, `schemas.py`, `service.py`,
`repo.py`, `probes.py`). Verify Wave 4's highest migration timestamp first.

### §22 Relationship model + discovery → migration `20260810000000_relationships.sql`

**Table** `dataset_relationships(id, dataset_id FK CASCADE /* left/owning, scopes
RBAC */, from_logical_sheet_id FK dataset_sheets, from_column /* NORMALIZED */,
to_dataset_id FK CASCADE /* = dataset_id within-workbook */, to_logical_sheet_id FK,
to_column /* NORMALIZED */, status CHECK(suggested|confirmed|rejected)
DEFAULT suggested, method CHECK(fk_rule|statistical|manual), evidence JSONB,
confidence DOUBLE, algorithm_version INT DEFAULT 1, created_by, reviewed_by,
created_at, updated_at, UNIQUE(from_logical_sheet_id,from_column,to_logical_sheet_id,to_column))`.
Indexes on `dataset_id`, `to_dataset_id`, `(dataset_id,status)`. **Widen `jobs.job_type`
CHECK** += `relationship_discovery` (template = quality migration). **Design decision
flagged:** HANDOFF sketched a single-dataset table; §23 needs cross-dataset joins over
confirmed relationships, so both endpoints carry their own dataset id (`to_dataset_id`
defaults to `dataset_id`). Keying both endpoints on `logical_sheet_id` ⇒ confirm-rename
needs no rewrite. Columns are **normalized** names — map to physical at probe/join time
via `sampling._physical_key`.

**Seeder** `service.seed_from_fk_rules(dataset_id)`: extract the FK-matching core of
`sampling._default_link_keys` into a reusable `sampling._fk_link_candidates(fk_rules,
sheet_rows, related, parent)` (so seeder and `_default_link_keys` share one impl); for
each enabled `foreign_key` rule emit a directed edge (`method='fk_rule'`,
`evidence={rule_id,rule_name}`, `status='suggested'`); `repo.upsert_relationship` on the
UNIQUE tuple — idempotent, never clobbers confirmed/rejected.

**Statistical suggester** `service.suggest_relationships` (runs as a
`relationship_discovery` job — wiring template = `explorer/service.profile_version`).
Pure probe builders in `probes.py` (unit-testable). Per ordered column pair across ready
sheets, four signals: (1) name-match (normalized-name eq / `_id` / `{sheet}_id`, Python
pre-filter); (2) type-compat gate (dtype families, reject numeric↔varchar); (3)
value-overlap coverage = distinct child keys present in parent (anti-join rate); (4)
target uniqueness = parent distinct/(total−nulls) (PK side ⇒ orients edge). `confidence =
w1*name + w3*coverage + w4*uniqueness`; threshold (~0.6) → persist `statistical` suggestion.
Load sheets like coordinated sampling (`load_data(driver)` + `read_parquet` views —
backend-agnostic). Cap candidate pairs; exclude NULL keys in every probe; never resurrect
`rejected` edges.

**Endpoints** (all `ensure_dataset_permission` on `{id}`; cross-dataset also on
`to_dataset_id`): `POST /datasets/{id}/relationships/seed`, `POST .../suggest` (→
`{job_id, suggestions}`), `GET .../relationships?status=` (Page), `GET .../{rel_id}`,
`POST .../relationships` (manual, WRITE), `POST .../{rel_id}/confirm|reject` (WRITE,
set reviewed_by; 409 `invalid-relationship-transition` on bad transition), `DELETE
.../{rel_id}`. Resolve sheet→logical id via `discovery/repo.get_live_logical_sheet`,
validate columns against `schema_json` (`unknown-column` 400).

### §23 Standalone join builder → migration `20260810010000_join_builder.sql`

No new table (rides `artifacts` + `dataset_lineage` + `library.publish_run`).
**Three CHECK widenings** (template = pivot): `artifacts.artifact_type` += `join_output`;
`dataset_lineage.relation` += `joined_from` (cross-dataset records **two** rows —
`record_lineage` twice); `analytics_definitions.kind` += `join`.

Generalizes `aggregation._build_join_select` + the join block (`aggregation.py:232-257`)
into a cross-dataset guided join with **mandatory pre-flight warnings**. Schemas:
`JoinBuildSpec{relationship_id /* MUST be confirmed */, how:inner|left, left_version?,
right_version?, select_columns?}`; `JoinWarnings{left_duplicate_keys, right_duplicate_keys,
estimated_output_rows, row_expansion_factor, unmatched_left_pct, unmatched_right_pct,
column_collisions[], many_to_many}`; `JoinPreview{warnings, left_rows, right_rows,
preview:LIMIT 5, output_columns}`.

**Probes** (`probes.py`, pure SQL builders; load both sides into one conn via
`resolve_version_sheet_path` + `read_parquet` view; map normalized→physical via
`_physical_key`): (1) dup keys each side `GROUP BY key HAVING COUNT(*)>1`; (2)
many_to_many = dup both sides; (3) estimated expansion = `SUM(lc*rc)` over per-key
multiplicity join (+ unmatched-left for `left` join); (4) unmatched-key % via anti-join
both directions; (5) collisions = non-key names on both sides. All identifiers
`quote_ident`; NULL keys excluded (`IS NOT NULL`).

**Endpoints** (team-scoped, **not** `/datasets/{id}/…` — two datasets, no single owner;
the ONLY place cross-dataset joins are allowed): `POST /joins/preview` (probes + LIMIT-5,
no persist), `POST /joins/execute` (persist `join_output` artifact via `_persist_table` +
`_register_output_artifacts`, return sample filename + warnings + run), `POST
/joins/{run_id}/publish` (new dataset/version, lineage `joined_from` ×2). Guard:
execute/publish require relationship `status='confirmed'` → else 409
`relationship-not-confirmed`. RBAC: READ both sides for preview/execute; WRITE target for
publish. Add a `join` branch to `library.service.publish_run`'s relation map.

### §24 Sub-sampling (coordinated-sampling v2) — code-only, no migration

Extend `RelatedSheetLink` (sampling schemas): (1) `relationship_id?` — resolve
`left_on`/`right_on`/`parent_sheet` from a confirmed `dataset_relationships` row
(normalized→physical), precedence explicit keys > relationship_id > FK-rule default (§5
preserved); validate confirmed + endpoints belong to the version (400
`relationship-not-confirmed`/`relationship-endpoint-mismatch`). (2) `sampling_steps?` +
`target_total_volume?` — in the related-sheet loop (`sampling.py:681-687`), after the
key-filtered set, run it through `_run_sampling_pipeline_inner` when params present, else
v1 behavior (keep all — backward compatible). **Referential guard:** sub-sampling a sheet
that is another link's `parent_sheet` breaks downstream refs → reject 400
`cannot-subsample-parent` (the dependency-order loop already tracks `sampled_tables`).
Echo resolved keys + relationship_id + sub-sample counts.

**Wave 5 tests.** Unit: `test_relationship_suggester.py` (scoring/type-gate/coverage/
uniqueness/orientation), `test_join_warnings.py` (dup keys, many-to-many, exact expansion
1:1/1:N/N:N w/ NULLs, unmatched %, collisions), `test_fk_link_candidates.py` (both
directions, normalized→physical, 0/1/multiple). Integration: `test_relationships.py`
(seed/suggest/list/confirm/reject/409, cross-team 404 on both datasets, audit),
`test_join_builder.py` (preview warnings, 409 unconfirmed, execute artifact +/samples auth,
cross-dataset same-team, cross-team 404, publish + joined_from ×2), extend
`test_coordinated_sampling.py` (relationship-driven keys == explicit; sampling_steps
reduce child count while refs hold; cannot-subsample-parent; v1 payload unchanged).
`_TRUNCATE` += `dataset_relationships` (before dataset_sheets/datasets).

---

## WAVE 6 — AI (last) — new slice `app/features/ai/`

`api.py`, `schemas.py`, `service.py`, `prompts.py`, `patterns.py` (deterministic
detectors), `repo.py`, `llm.py` (gateway). Register `ai_router` on the protected group.

**Existing LLM plumbing** (`app/infra/llm/`): `call_llm(model, messages, temperature=0.2,
tools=None, **kwargs) -> LLMResponse` (async); `model.startswith("claude-")` → Anthropic;
tools as `{name, description, parameters}` hardened by `prepare_tools_for_provider(...,
"anthropic")`; `LLMResponse.text/.tool_calls[ToolCall(id,name,args)]/.usage`;
`safe_repair_json`, `validate_tool_args` available; key = `settings.anthropic_api_key`.
**Two gaps:** `_call_anthropic` ignores `kwargs["tool_choice"]` (recommend a 3-line
passthrough so the model is forced to call the emit tool) and has no structured-output
config → reach structured output via **tool-use**, not `output_config`.

**Gateway `ai/llm.py::complete_structured(tool, messages, *, purpose, timeout_s, max_tokens)`**
— the single seam tests patch and where all guardrails live: `model=settings.ai_model`
(default **`claude-opus-4-8`**, exact id, no date suffix; `claude-haiku-4-5`/`claude-sonnet-5`
OK for cheap §25/§27), `temperature=0`, `asyncio.wait_for(timeout)` → 503 `ai-timeout`,
feature-gate → 503 `ai-disabled` when off/no key, `ANALYTICS_LLM_STUB` env seam returning
canned fixtures keyed by `purpose` (for offline e2e), parse+`safe_repair_json`+pydantic →
422 `ai-unparseable` on failure. New settings: `ai_enabled=True`, `ai_model="claude-opus-4-8"`,
`ai_timeout_s=30`, `ai_max_tokens=4096`, `ai_allow_sample_rows=False`.

**Universal guardrails:** no raw dataset rows to the model (prompts built from
`schema_json`, dictionary, aggregated profile/`top_values`/insights only; gated by
`ai_allow_sample_rows`); validate every structured output against the pydantic/DSL schema;
**never auto-apply** (proposals go through the existing mutation endpoints); team-scope +
audit every route; cost/timeout caps.

**One table** `ai_suggestions(id, dataset_id FK CASCADE, logical_sheet_id FK NULL,
kind CHECK(semantic_type|quality_rule|transform_step), column_name?, payload JSONB,
evidence JSONB, source CHECK(pattern|llm), status CHECK(suggested|accepted|rejected)
DEFAULT suggested, model?, created_by, created_at, updated_at)` — migration numbered
after Waves 4–5. `_TRUNCATE` += `ai_suggestions` (first, before datasets/dataset_sheets).

**Features:**
- **§25 semantic inference** — `patterns.infer_semantic_type(name, dtype, top_values,
  null_pct)` (regex over aggregated top_values: email/phone/currency/country/ISO-8601;
  name heuristics) first; LLM (tool `emit_semantic_type`) only for ambiguous. `POST
  .../sheets/{sheet}/semantic-inference` → suggestions; apply via **existing** dictionary
  PUT (or accept endpoint → `upsert_column_metadata`).
- **§26 rule suggestions** — deterministic map from `compute_insights` + profile to
  `quality_rules` payloads (likely-PK→uniqueness, 0-null→not_null, closed-set→allowed-values,
  confirmed FK relationship→foreign_key — degrade gracefully if §22 absent). `GET
  .../rule-suggestions`; apply via existing `POST /datasets/{id}/rules`.
- **§27 summaries** — `GET /datasets/{id}/summary`: assemble aggregated stats + insights +
  dictionary + timeline → tool `emit_summary{headline, narrative, key_findings[]}`. Read-only,
  no rows.
- **§28 NL→DSL (flagship)** — `POST .../sheets/{sheet}/nl-query {question}` → tool
  `emit_query_spec` whose `parameters = QuerySpec.model_json_schema()` (recursive FilterGroup
  `$defs`/`$ref` are fine for Anthropic). Parse→`QuerySpec(**args)` (pydantic re-validates
  operator arity)→`validate_spec(spec, schema_json)`. **Return 200 `{spec, valid, problems,
  explanation}` even when invalid** (inspection is the point; fold `validate_spec`'s 400 into
  `problems`). **Never import `execute_query`** — the user runs the returned spec via the
  existing `POST .../query`. Assert in a test that no run row is created.
- **§29 transform suggestions** — deterministic map from insights → §19 `TransformStep`
  payloads (trim/case_normalize/parse_dates/deduplicate). `GET
  .../sheets/{sheet}/transform-suggestions`; apply via §19 add-step. Needs §19.

**Tests.** Unit (patch `complete_structured` in the module under test): `test_semantic_patterns`,
`test_rule_suggestions`, `test_transform_suggestions`, `test_nl_prompt` (context has no raw
rows; tool params == QuerySpec schema), `test_nl_parse` (valid spec; repair; 422; unknown-col →
`{valid:false}` not raised, not executed), `test_summary_context`, `test_ai_gateway` (timeout
503, disabled 503, stub path). Integration (autouse conftest fixture patching
`ai.llm.call_llm` to a deterministic fake keyed by tool name — establishes the LLM-mock pattern,
none exists today): `test_ai_nl_query` (spec-only, assert no execution, then run the spec via
`/query`), `test_ai_semantic_inference`, `test_ai_rule_suggestions`, `test_ai_transform_suggestions`,
`test_ai_summary`; cross-team 404 / sheet-required / 403 / `ai-disabled` 503 on every route.
e2e: new "I. AI assistance" section behind an `ANALYTICS_LLM_STUB` guard.

**Gotchas:** force tool use (tool_choice passthrough) or handle text-fallback; never execute
NL output; `validate_spec` 400 must be caught for §28; keep recursive schema as-emitted; always
`safe_repair_json` (never 500); cap prompt size (`top_values[:20]`, `columns[:200]`);
no-raw-rows unit test; §26 needs §22, §29 needs §19 (degrade gracefully); temperature=0.

---

## Cross-cutting invariants (hold for every feature above)

- Output-persisting endpoints MUST call `_register_output_artifacts` (or
  `library_repo.create_artifact` with dataset_id+team_id) or `/samples/{f}` 404s.
- New definition/run tables key on `logical_sheet_id` (survives confirm-rename) — else
  extend `shared/repo.reassign_logical_sheet` + `count_logical_sheet_state`.
- New run/history tables add a branch to `discovery/repo._TIMELINE_EVENTS`.
- New analytics kinds = 3 CHECK widenings (kind, artifact_type, lineage relation) + one
  `execute_definition` branch + one publish relation-map entry (pivot migration is the template).
- Cross-team → 404 (existence hiding), in-team perm fail → 403, problem+json, audit via the
  protected router, immutable versions/source, sheet-selection-required 400 for multi-sheet.
- Every new table → `tests/conftest.py::_TRUNCATE` (children before parents; verify no FK
  escapes the set). Unit layer stays Postgres-free. e2e_curl stays at 0 failures.
- Migration numbering: `ls migrations | tail -3` first; each file has `-- migrate:up/down`;
  down deletes new-enum rows before narrowing a CHECK.
