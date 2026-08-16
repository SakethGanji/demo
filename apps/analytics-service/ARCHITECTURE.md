# Analytics Service — Technical Architecture

Audience: technical lead / reviewing engineer.
Scope: the API surface (135 routes), how the features compose, and where data
physically lives.

---

## 1. The one architectural rule everything else follows

> **Postgres is the control plane. It stores identity, configuration, schema,
> counts, ratios, status and *pointers*. It never stores a copy of dataset cell
> values. Anything row-shaped lives in object storage, registered in
> `artifacts`.**

This is enforced, not aspirational. `tests/test_control_plane.py` enumerates
**every JSONB column in the schema** and fails on any that isn't in a reviewed
allow-list, so a future migration reintroducing a row-shaped column has to
confront the rule rather than slip past it. It has caught three columns in
practice.

One documented exception: `profile_runs.profile` holds capped top-N values
alongside its counts and ratios. Those are aggregate statistics that catalog
facets, health dimensions, drift and insights all read **via SQL** — moving them
to object storage would put an S3 round trip on every catalog page. Reversible
if the strict reading is wanted.

**Consequence for reviewers:** if you add an endpoint that produces rows, they
go to the object store and you register an `artifacts` row. There is no other
option that will pass CI.

---

## 2. Runtime shape

```
             ┌──────────────────────────────────────────────┐
   HTTP ───► │ FastAPI                                       │
             │  ├─ CORS                                      │
             │  ├─ Audit middleware  → audit_log (append-only)│
             │  └─ /api/v1                                   │
             │      ├─ public:   auth, teams                 │
             │      ├─ protected: everything else            │
             │      │      Depends(get_principal)            │
             │      └─ /mcp   JSON-RPC, 27 MCP tools         │
             │            same get_principal, per request;   │
             │            calls its own routes over ASGI     │
             └──────────────┬───────────────────────────────┘
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
   ┌─────────┐        ┌──────────┐         ┌──────────┐
   │ Postgres│        │  DuckDB  │         │ Storage  │
   │ control │        │ compute  │◄────────│ local FS │
   │  plane  │        │(ephemeral)│  reads │ or S3    │
   └─────────┘        └──────────┘         └──────────┘
        ▲                                        ▲
        │            ┌──────────────┐            │
        └────────────│  Job worker  │────────────┘
                     │ FOR UPDATE   │
                     │ SKIP LOCKED  │
                     └──────────────┘
```

**DuckDB is stateless compute.** Every analytical operation opens a connection,
registers the relevant parquet file(s) as tables, runs generated SQL, writes any
output back to the object store, and closes. Nothing persists in DuckDB between
requests. That is what makes the service horizontally scalable despite doing
real analytics in-process.

### Module layout

```
app/
  api/          errors (problem+json), pagination
  features/     audit  auth  data_accelerator  discovery  explorer
                files  jobs  library  quality  relationships  transform  webhooks
  infra/        config, db (postgres session + migrations + storage), llm
  shared/       worker, jobs, datasets, masking, data_io, query, repo, utils
```

Each feature is a vertical slice with the same shape:

| File | Responsibility |
|---|---|
| `api.py` | Routes, RBAC, HTTP contract. No SQL, no DuckDB. |
| `service.py` | Orchestration: resolve → compute → persist → register |
| `repo.py` | SQLAlchemy Core `text()` against Postgres. No business logic. |
| `schemas.py` | Pydantic v2 request/response models |
| `*.py` (pure) | `compile.py`, `expr.py`, `steps.py`, `probes.py`, `engine.py` — **pure functions, unit-tested without Postgres** |

That last row is deliberate and load-bearing. SQL generation, expression
compilation, relationship scoring and join warnings are pure functions taking
data and returning data. They carry ~29 unit-test files that need no database.
Two real design defects (a non-monotonic relationship scorer, a permissive
regex) were caught by those tests, not by review.

### Cross-cutting request pipeline

1. **Audit middleware** writes one append-only `audit_log` row per mutating
   request or download: actor, method, path, status, duration, plus the
   attribution taken from the matched route — a route-shaped `action`
   (`PUT /api/v1/datasets/{dataset_id}/favorite`), the aggregate root as
   `resource_type`/`resource_id` (sub-resource ids stay in `metadata`), and the
   `team_id` that OWNS that resource, which is what `audit.query(team_ids=…)`
   scopes on. A DB trigger rejects `UPDATE`/`DELETE` on it.
2. **`get_principal`** resolves `X-User-Id` → `Principal(user_id, is_superuser,
   memberships: {team_id: Role})`. **This is the single SSO seam** — swapping
   header auth for JWT/OIDC is a change to this one function.
3. **`ensure_dataset_permission(principal, dataset_id, Permission.X)`** is the
   only authorization entry point for dataset-scoped routes. It returns the
   dataset row, so handlers get authorization and lookup in one call.

---

## 3. Authorization model

Roles are ordered: `viewer < editor < admin < owner`, plus a platform
`is_superuser` flag.

The response code distinction is a security property, not a style choice:

| Situation | Response | Why |
|---|---|---|
| Caller is outside the owning team | **404** | "403" would confirm the resource exists, leaking another tenant's inventory |
| Caller is in the team but lacks the role | **403** | They already know it exists; a truthful error is better UX |
| Malformed / unknown id | **404** | Same shape as cross-tenant, so probing is indistinguishable |

`DATASET_READ_SENSITIVE` is granted to **admin/owner only — deliberately not
editor** — and gates both the unmasked read path and the raw download.

All errors are `application/problem+json` with a machine-readable `code`
(`sheet-selection-required`, `unknown-column`, `relationship-not-confirmed`,
`ambiguous-diff-key`, `select-only`, …) so a UI can branch on the code rather
than parse prose.

---

## 4. Storage layout

Two distinct namespaces with different lifecycles.

### 4a. Dataset versions — the durable plane

```
datasets/{team_id}/{dataset_id}/v{NNNNNN}/
    source/orders.xlsx              original upload, byte-preserved
    parquet/dataset.parquet         canonical (single-sheet)
    parquet/sheets/Revenue.parquet  one per sheet (workbooks)
    manifest.json                   version metadata
    derived/…                       per-version derived cache
```

Zero-padded version numbers sort lexically, so a prefix listing is already in
version order. **Versions are immutable** — a new upload is always a new
version directory; nothing is ever overwritten in place.

### 4b. Derived artifacts — the disposable plane

```
artifacts/{team_id}/{dataset_id}/{kind}/{filename}
```

`kind` ∈ `sample_output`, `aggregation_output`, `pivot_output`, `query_output`,
`transform_output`, `join_output`, `diff_output`, `validation_failures`,
`export`, `published_source`.

Ownerless outputs (inline-data sources, superuser `file_path`) fall back to
explicit segments: `artifacts/_shared/_adhoc/{kind}/{filename}`.

**Every segment earns its place:**
- **team** — a bucket policy or IAM prefix can scope one tenant
- **dataset** — deleting a dataset is a *prefix delete*, not an enumeration
- **kind** — retention differs sharply by kind; with the kind only in the
  filename no lifecycle rule could tell scratch from a published source

**Deliberately not date-partitioned.** Expiry keys off object age (S3 evaluates
that natively) and listings come from Postgres, so a date segment buys nothing —
and it would cost the property the design depends on:

> `ArtifactLayout.key()` is a **pure function of its fields**. The service that
> writes the parquet and the code that registers ownership build the key
> *independently and never exchange the string*. If it were time-dependent they
> would drift, and the artifact would be silently unreachable.
> `tests/unit/test_artifact_layout.py` pins this.

### 4c. Resolution and its consequence

A caller holding `/samples/{filename}` knows neither team, dataset, nor kind —
so it **cannot construct the key**. Resolution goes through
`artifacts.filename`, which is the same row that already governs authorization.

**Authorization and resolution became one lookup.** The consequence is worth
being explicit about: *a blob with no artifact row is unreachable by everyone,
superusers included.* Not merely unauthorized — unaddressable. That is precisely
why the orphan sweep exists.

### 4d. Retention

`artifacts.expires_at` is stamped **at write time from the policy then in
force**, so a later policy edit never retroactively shortens something already
stored.

| Kind | Days | Rationale |
|---|---|---|
| `published_source` | **never** | Backs a dataset version |
| `validation_failures` | 90 | Evidence attached to a run someone will work through |
| `transform/join/sample/aggregation/pivot_output` | 30 | The *definition* is saved; re-running is cheaper than storing |
| `diff_output` | 14 | Review scratch |
| `export`, `query_output` | 7 | Pure scratch |
| *(unknown kind)* | 30 | New code doesn't get a licence to keep bytes forever |

The `artifact_gc` job runs two passes: expired rows, then orphan blobs past a
24h grace window (a job writes the parquet *before* registering it, so a
brand-new unreferenced key is normal, not garbage). Delete order is
**blob first, row second** — a crash between them leaves a reclaimable orphan;
the reverse leaves a row pointing at nothing, which reads as corruption.

Both passes are bounded at `GC_BATCH` (500) per call, because every deletion is
a blocking storage round trip on the event loop. A truncated sweep sets
`more_remaining: true` in the response — without it a sweep that hit its limit
is indistinguishable from a sweep that found nothing left to do.

`GET /storage/retention`, `POST /storage/gc` — superuser only. Nothing schedules
it yet.

---

## 5. Core data model

### Identity & tenancy
`teams` ← `team_members` (role) → `users`. Every dataset belongs to exactly one
team; that is the tenancy boundary.

### Datasets and versions
```
datasets (current_version_id ─┐)
    └─< dataset_versions ─────┘   immutable, version_number, status, path
            └─< dataset_version_sheets   per-sheet: storage_key, schema_json,
                                          schema_fingerprint, row/col counts
```
`datasets` and `dataset_versions` reference each other — a genuine FK cycle
(`current_version_id` ↔ `dataset_id`). Worth knowing before writing any
teardown or migration logic.

### Logical sheets — the rename-proofing mechanism

This is the least obvious and most important piece of the model.

`dataset_version_sheets.sheet_key` is the *physical* name in a given version.
`dataset_sheets` is a **stable logical identity** that survives renames:

```
dataset_sheets (logical id, current_sheet_key, display_name)
    ▲
    │ logical_sheet_id
    ├── dataset_version_sheets     the physical sheet in each version
    ├── dataset_views              saved queries
    ├── transformation_definitions pipelines
    ├── quality_rules              validation rules
    ├── dataset_column_metadata    the dictionary
    └── dataset_relationships      FK endpoints
```

Everything durable keys off `logical_sheet_id`, **not** the sheet name. When a
user renames "Revenue" to "Rev2026" and confirms, saved views, pipelines, rules
and dictionary entries all keep pointing at the right sheet.

`confirm-rename` also rewrites the places that legitimately hold a *name*:
`quality_rules.sheet_selector`, FK `parameters.ref_sheet`, and
`dataset_sheet_metadata.sheet_key`. **If you add sheet-keyed state, either key
it by `logical_sheet_id` (preferred — it follows automatically) or add it to
`shared/repo.py::reassign_logical_sheet` and extend `count_logical_sheet_state`
so the 409 conflict guard sees it.**

### Version selectors

Saved artefacts (views, analytics definitions, transformations) don't pin a
version id. They store a `version_selector` JSONB — `{}` (current),
`{"version_number": N}`, or `{"tag": "production"}` — resolved **at run time**.
A view saved against "current" follows promotions; one pinned to a tag follows
the tag. Same mechanism in three features.

### Runs, jobs, artifacts

Four features share one shape: a *definition* table + a *run* table + a `job_id`
+ an `artifact_id`.

```
{analytics,transformation}_definitions ─< …_runs ─► jobs
                                             └────► artifacts
validation_runs ─< validation_rule_results ──────► artifacts (failure_artifact_id)
profile_runs    ─< profile_insights
```

Because they share the shape, run history, artifact ownership, `/samples`
authorization and publishing all work identically for each — a new
result-producing feature gets those for free.

### Lineage
`dataset_lineage` records (child version → parent version, `relation`).
Relations: `pivoted_from`, `transformed_from`, `joined_from`, `sampled_from`,
`aggregated_from`. A join writes **two** rows. `GET /lineage/graph` walks it
with a recursive CTE, depth-capped and cycle-guarded.

---

## 6. The API surface — 135 routes

### Identity & tenancy (8)
```
GET    /auth/me                              POST   /auth/users
GET    /teams                                POST   /teams
GET    /teams/{id}/members                   POST   /teams/{id}/members
PATCH  /teams/{id}/members/{user_id}         DELETE /teams/{id}/members/{user_id}
```

### Ingestion (6)
```
POST   /upload                        multipart | inline JSON | dataset_id (new version)
GET    /upload/status/{version_id}
POST   /tus/                          TUS resumable: create
PATCH  /tus/{id}                      …append
GET    /tus/{id}/status               …resume point
DELETE /tus/{id}
```
Upload → parquet conversion → schema extraction → `dataset_version_sheets` rows.
TUS staging is always local disk, then published to the active backend.

### Datasets, versions, sheets, tags (21)
```
GET    /datasets                      list (+ facet filters, signal columns)
GET    /datasets/search               GET /datasets/facets
GET    /datasets/{id}                 PATCH /datasets/{id}    DELETE /datasets/{id}
GET    /datasets/{id}/versions
GET    /datasets/{id}/versions/{a}/diff/{b}                 schema + optional profile drift
GET    /datasets/{id}/sheets          GET  …/sheets/{name}     current version
GET    …/versions/{n}/sheets                                that version's sheets
POST   /datasets/{id}/sheets/{name}/replace                 copy-on-write single sheet
GET    …/versions/{a}/sheets/{s}/diff/{b}
POST   …/versions/{a}/sheets/{s}/row-diff/{b}               keyed row-level diff
POST   …/versions/{n}/confirm-rename
PUT    /datasets/{id}/tags            GET/DELETE …/tags/{name}
GET    …/tags/{name}/history          POST …/tags/{name}/promote | /rollback
```
Tags are **whole-version**, never per-sheet — a "production" tag that pointed at
a mix of versions would make "what is in production?" unanswerable.

### Explorer (20)
```
GET    …/versions/{n}/preview                     …/sheets/{s}/preview
POST   …/versions/{n}/query                       …/sheets/{s}/query
GET    …/versions/{n}/columns/{c}                 …/sheets/{s}/columns/{c}
GET    …/versions/{n}/duplicates | /missing       (+ sheet-scoped)
POST   …/versions/{n}/sql                         sandboxed single SELECT
POST   …/versions/{n}/profile-runs                GET …/profile-runs, …/{run_id}
POST   /datasets/{id}/views                       GET/PATCH/DELETE …/views/{id}
POST   /datasets/{id}/views/{id}/run
```
The **sheet-selection contract**: addressing a multi-sheet version without a
sheet returns 400 `sheet-selection-required` **with the sheet list**. Never
guess — a silently-wrong tab is a wrong business answer.

The **SQL sandbox**: single `SELECT` only (`select-only` on anything else),
row-capped, size-capped (413 `version-too-large-for-sql`), file reads blocked,
and error text sanitized so a rejected path is not echoed back.

### Analytics (5)
```
POST   /sample              goal-oriented sampling (methods, distribution goals, seed)
POST   /sample/coordinated  driver sheet + related sheets filtered by key
POST   /profile             column statistics
POST   /aggregate           group-by, having, sort, cross-sheet join
POST   /pivot               row dims × pivot dim × aggregations, totals
```
Coordinated sampling produces a **referentially consistent slice**: related
sheets are semi-joined down to rows referenced by an already-sampled parent.
Keys come from a confirmed relationship or a single enabled FK rule; ambiguity
is a 400 naming the candidates. Deterministic under a fixed `seed`.

### Quality (8)
```
POST   /datasets/{id}/rules              GET  /datasets/{id}/rules
GET/PATCH/DELETE  /datasets/{id}/rules/{rule_id}
POST   …/versions/{n}/validate
GET    …/versions/{n}/validations       GET /datasets/{id}/validations/{run_id}
```
9 rule types. Failing rows are written to object storage as a
`validation_failures` artifact; Postgres keeps `failure_count` +
`failure_artifact_id`. Two things fall out: failures are downloadable through
`/samples/{f}/data`, and publishable as a dataset to work through.

### Library — definitions, runs, publish, lineage, charts (16)
```
POST/GET/PATCH/DELETE  /datasets/{id}/analytics[/{def_id}]
POST   …/analytics/{def_id}/run          GET …/analytics/{def_id}/runs
POST   …/analytics/runs/{run_id}/publish        → new dataset | new version
GET    /datasets/{id}/lineage            GET …/lineage/graph
POST/GET/PATCH/DELETE  /datasets/{id}/charts[/{chart_id}]
POST   …/charts/{chart_id}/render
```
**Publishing copies the blob** into the target dataset's own version storage
rather than pointing at the artifact key. That single decision is what makes
retention safe to apply to every derived kind — collecting a `sample_output`
can never strand a version.

Charts own no query logic; render re-runs the referenced view/definition.
Rendering is `persist=False` — it used to write a parquet nothing registered,
a guaranteed orphan on every render.

### Transformations (10)
```
POST/GET/PATCH/DELETE  /datasets/{id}/transformations[/{def_id}]
POST   …/transformations/{def_id}/preview     bounded sample, nothing persisted
POST   …/transformations/{def_id}/run         via job worker
GET    …/transformations/{def_id}/runs        GET …/transformations/runs/{run_id}
POST   …/transformations/runs/{run_id}/publish
```
16 step types (`select drop rename reorder cast trim case_normalize replace
parse_dates split merge compute filter deduplicate sort limit`), max 50 steps.
`compute` takes a typed expression tree with whitelisted ops and depth ≤ 12.

**Compilation:** each step becomes a CTE, folding a "running schema" forward, so
step *n* validates against the columns step *n−1* actually produced. Compiled at
**save time**, so an invalid pipeline is a 400 on `POST`, not a 3am job failure.

**Invariant:** no node ever emits user text into SQL — identifiers are quoted
from the validated schema, literals are bound parameters.

The same registered handler runs the job whether the caller asked for inline
(synchronous) or background execution — there is exactly one implementation of
what a transformation *does*.

### Relationships & joins (11)
```
POST   /datasets/{id}/relationships/suggest     statistical discovery
POST   …/relationships/seed                     from declared FK rules
POST/GET/DELETE  …/relationships[/{rel_id}]
POST   …/relationships/{rel_id}/confirm | /reject
POST   /joins/preview                           pre-flight, no rows materialized
POST   /joins/execute
POST   /joins/{run_id}/publish
```
Discovery scores name similarity (0.25), coverage (0.45) and target uniqueness
(0.30), then applies **hard floors** — uniqueness ≥ 0.9, coverage ≥ 0.5 — so a
strong name can never outvote a fatal signal. (The floors exist because the
weighted sum alone was non-monotonic; a unit test caught it.)

**Only a `confirmed` relationship may drive a join** (409
`relationship-not-confirmed`). That constraint is what makes cross-dataset
joining safe to expose: the keys have been reviewed by a human, and both
endpoints carry a dataset id whose permissions are checked independently.

`/joins/preview` measures before running: duplicate keys per side, many-to-many,
**exact** output row count (per-key multiplicities multiply), unmatched % per
side, colliding column names.

### Discovery / catalog (14)
```
GET    /search/columns                     GET /datasets/facets
GET    /datasets/{id}/health               composite read-model
GET    /datasets/{id}/timeline             merged event stream
GET    /datasets/{id}/usage
GET/PUT/PATCH/DELETE  …/sheet-metadata[/{sheet_key}[/columns[/{col}]]]
PUT/DELETE      /datasets/{id}/favorite
```
Sheet and column metadata carry **both** write verbs on purpose. `PUT` is a
true whole-record replace — a field omitted from the body is cleared, which is
the only way to blank one — and `PATCH` merges, writing only the fields present
in the body while an explicit `null` still clears. `PATCH` therefore needs
`exclude_unset=True` plus a partial `UPDATE` built column by column: once an
explicit null is flattened into a bind parameter, `COALESCE` can no longer tell
it from "not supplied". `PATCH` never creates a record (404), and carries the
same `dataset:write` check as `PUT`.

Every write on these paths resolves its target first: an unknown `sheet_key`
(or, for `PUT`/`PATCH`, an unknown column) is a **404**, never a stored record
under the name as typed. A logical sheet is never retired, so this still allows
documenting a sheet that has dropped out of the current version. `GET` and
`DELETE` on a column entry tolerate a column the current schema no longer has
and fall back to the ingest normalization rule, so the spelling that wrote an
entry always reaches it.

Health dimensions (validation, drift, documentation, missing data) are computed
from `profile_runs` + `validation_runs` **in SQL** — this is the reason for the
documented `profile_runs.profile` exception. A test asserts the catalog facets
and the health read-model agree; two independent computations of "documented"
would drift.

### Downloads & storage (9)
```
GET    /datasets/{id}/download                    csv|parquet|xlsx
GET    …/versions/{n}/download                    xlsx reconstructs the workbook
GET    /samples                                   from Postgres, paginated
GET    /samples/{filename}                        GET …/{filename}/data
POST   /samples/{filename}/export
GET    /storage/usage    GET /storage/retention   POST /storage/gc
```
`/storage/usage` uses `StorageBackend.list_sizes()`, which takes sizes from the
listing. It previously issued **one HEAD per object** — 31s on a 24k-object
bucket. A parity test pins `list_sizes()` against `size()` per key.

### Webhooks, jobs, audit (10)
```
POST/GET/PATCH/DELETE  /webhooks[/{id}]
POST   /webhooks/{id}/test          GET /webhooks/{id}/deliveries
GET    /jobs   GET /jobs/{id}       GET /audit          (superuser)
```
HMAC-SHA256 over `timestamp.body`. The secret is returned **once, on create**,
and never readable again. Delivery goes through the job worker, so a dead
receiver can never fail the operation that triggered it.

### MCP (1 endpoint, 27 tools) — `app/features/mcp/`
```
POST/GET/DELETE  /api/v1/mcp        streamable HTTP, stateless, JSON responses
```
Not a REST route and deliberately absent from the OpenAPI document — it is
JSON-RPC. Mounted as a Starlette `Route` holding a raw ASGI app, so FastAPI's
schema generator ignores it. Three things about it are load-bearing:

- **Identity is per request, not per process.** An ASGI middleware runs the same
  `get_principal` every route above uses, and an MCP server middleware rebinds
  the resolved principal for each inbound JSON-RPC *message*. Nothing is
  captured at startup; one process serves many users. Statelessness is part of
  that: a stateful streamable-HTTP session runs its handlers in a task spawned
  by whichever request opened the session.
- **Tools call the service's own routes**, over `httpx.ASGITransport` against
  this app — in-process HTTP, no socket. Every authorization check, the
  404-hides-existence rule and sensitive-column masking therefore run on the
  real code path, so the MCP surface cannot drift from REST RBAC.
- **The 27 tools live here and only here.** `apps/analytics-mcp` was a stdio
  relay for clients that can only launch subprocesses; it held no tool
  definitions and was deleted 2026-08-10. The endpoint is HTTP-only now, so a
  stdio client (Claude Desktop/Code) needs that relay restored from git history.
- **Every tool response is capped at 60,000 characters**, by the `guard`
  decorator all 27 wear (`tools/_common.py`). A tool may clamp itself earlier
  with a hint specific to what it read; a body that already carries the
  truncation marker is left alone, because `run_sql` and `read_artifact`
  deliberately keep their artifact handle and `offset=` hint outside their own
  clamp and a second pass at the same budget would delete exactly those.

---

## 7. How the pieces compose

The critical property: features are wired through **shared seams**, not
point-to-point. Adding a result-producing feature means implementing the seam,
not re-implementing publishing/authorization/history.

**The canonical write path** — every derived output follows it:

```
1. resolve      version_selector → version; sheet by logical_sheet_id
2. authorize    ensure_dataset_permission → dataset row (team known)
3. layout       ArtifactLayout(kind, team_id, dataset_id)   ← built once, at the API layer
4. compute      DuckDB: register parquet → generated SQL → COPY TO parquet
5. publish      storage.put_file(layout.key(filename), local)
6. register     library_repo.create_artifact(layout.key(f), kind, filename=f, …)
7. record       …_runs row + jobs row + optional dataset_lineage
```

Steps 3 and 6 build the key **independently**. They agree because
`ArtifactLayout.key()` is pure. That is the whole reason the class is shaped
the way it is.

**End-to-end example — the pivot in the e2e script:**

```
POST /pivot
  └─ _authorize_source            → dataset row, team
  └─ _output_layout(…, "pivot_output")
  └─ run_pivot(request, layout)   → DuckDB → artifacts/{team}/{ds}/pivot_output/pivot_x.parquet
  └─ _register_output_artifacts   → artifacts row (filename, size, expires_at)

POST /samples/{pivot_x}/export?format=csv
  └─ _authorize_sample_access     → artifacts row (authorization AND resolution)
  └─ layout = ArtifactLayout("export", team, dataset)     ← inherits the source's prefix
  └─ export_sample_file(source_key, …, layout)
  └─ create_artifact(...)                                  → downloadable, and swept together

POST /datasets/{id}/analytics/runs/{run}/publish
  └─ resolve_publishable_artifact
  └─ publish_artifact_as_version  → COPIES the blob into datasets/{team}/{new_ds}/v000001/…
  └─ dataset_lineage(relation="pivoted_from")
  └─ webhooks.emit("dataset.published")

GET /datasets/{new}/lineage/graph → recursive CTE over dataset_lineage
GET /datasets/{id}/timeline       → UNION over versions, tags, validations,
                                     profiles, transformations, lineage, audit
```

**Shared seams worth knowing:**

| Seam | Location | Used by |
|---|---|---|
| `publish_artifact_as_version` | `library/service.py` | analytics runs, transformations, joins |
| `_output_layout` / `_register_output_artifacts` | `data_accelerator/api.py` | sample, pivot, aggregate, sql, coordinated |
| `_persist_table(conn, table, prefix, layout)` | `data_accelerator/services/sampling.py` | quality, transform, joins, rowdiff, coordinated |
| `resolve_version` + `_selector_pin` | `shared/datasets.py` | views, definitions, transformations |
| `insert_version_sheets` | `files/repo.py` | **all four** version-producing paths — logical-sheet linkage is automatic |
| `worker.register_handler(type, fn)` | `shared/worker.py` | transform, relationship_discovery, webhook_delivery, artifact_gc |
| `resolve_masking` / `ensure_raw_access` | `shared/masking.py` | explorer, downloads, row diff, dataset/sheet previews, `_authorize_source` (sample/profile/pivot/aggregate) |

---

## 8. Job worker

Features enqueue a `jobs` row and register a handler by `job_type`. The loop
claims pending jobs with `FOR UPDATE SKIP LOCKED`, so multiple workers — or a
worker racing the request path — can never run the same job twice.

`dispatch(job_type, params, inline=True|False)` selects synchronous vs
background execution **with the same handler body**. Tests call
`run_pending_jobs_once()` for determinism; the in-process ASGI client doesn't
run the app lifespan, so the loop is dormant there.

Registered: `transform`, `relationship_discovery`, `webhook_delivery`,
`artifact_gc`.

**Known gap:** no retry/backoff policy. A failed job is recorded as failed and
stays failed.

---

## 9. Testing architecture

| Layer | Count | Needs |
|---|---|---|
| `tests/unit/` (26 files) | Postgres-free | pure functions: SQL builders, expression compiler, scorers, masking, layout, retention |
| `tests/test_*.py` (40 files) | Postgres + storage | in-process ASGI against the real app |
| `scripts/e2e_curl.sh` | live server | 104 checks over real HTTP |

**The whole integration suite is parametrized over both storage backends** —
local FS and S3/MinIO — in a single `pytest` invocation. Every integration test
runs twice.

**1,028 tests in ~110 seconds.** Three things got it there from ~12 minutes:
- the per-test reset is `DELETE`, not `TRUNCATE` (TRUNCATE recreates each
  table's file and indexes: ~7ms/table × 27 = 190ms *per test*)
- `list_sizes()` instead of HEAD-per-object
- a session-scoped fixture purges state that accumulates *across* runs (7k
  users, 79k audit rows and 24k objects had piled up; nothing ever removed them)

Two of those only showed up as **drift** — no single commit caused them.

> **Never run two pytest invocations concurrently.** They share one Postgres and
> one storage dir; the per-test reset makes the other run fail in ways that look
> exactly like real bugs.

---

## 10. Operational state

**Done:** 135 routes, 24 migrations, 1028 tests, e2e 104/104, both storage
backends, retention + GC, control-plane enforcement.

**Outstanding — the ops track:**

| Item | Where | Size |
|---|---|---|
| SSO/JWT swap | `auth/deps.py::get_principal` | One function; the RBAC model behind it is complete and tested |
| Job retry/backoff | `shared/worker.py` | Needs an attempts column + policy |
| Schedule `artifact_gc` | — | Job + admin trigger exist; nothing fires it on a timer |
| TUS staging on S3 | `files/services/tus.py` | Staging is local-disk-only by design; fine single-node |

**Also honest:** route coverage was measured at 142/142 by instrumenting every
route's ASGI callable during a full run. Routes have been added since; all are
covered by tests, but that instrumentation has not been re-run — treat 142/142
as the last *measured* figure, not the current one.
