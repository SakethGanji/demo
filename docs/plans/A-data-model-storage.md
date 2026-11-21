# Plan A: data model and storage (Parquet + DuckDB, teams, multi-table versions)

Planning session PLAN-A, 2026-11-21, motion-components @ c3334f2. Lens: the entity model, the metadata schema, the
Parquet layout, the versioning algorithms and the storage abstraction. No product code was changed. The benchmarks were
run in a scratch venv (duckdb 2.0.1, pyarrow 26.0.1, pandas, 4 vCPU, 15 GB RAM, local SSD); the script is reproduced in
appendix B.

The brief's fixed decisions are taken as given: Parquet is the stored format, DuckDB is the engine, metadata lives in
SQLite now and Postgres later, every change is a new version, the hierarchy is team → project → dataset → version →
tables, and no lakeFS/Iceberg/Delta unless scale forces it (section 9 names the trigger).

---

## 0. Decisions in one screen

| # | Decision | Why |
|---|---|---|
| D1 | A **version** is an immutable **manifest**: a list of tables, each a list of content-addressed Parquet files. The version's DB row is the commit point. | One mechanism for 1 table and 20 tables; atomic on local disk and S3 (no rename on S3). |
| D2 | Files are **content-addressed**: `blobs/<sha256[0:2]>/<sha256>.parquet`, written once, never modified. | Unchanged tables are shared across versions for free (dedupe by hash); any outside change is detectable by re-hashing; write-once keys are safe on S3. |
| D3 | Copy-on-write at **table** granularity, plus **append parts**: an unchanged table is carried by reference (zero bytes); an append-only delta adds a part file; anything else rewrites the table's files. | Measured: a full 10M-row table rewrite takes 12–36 s (section 8), so finer granularity (deletion vectors, column groups) is not worth its complexity before the escalation trigger. |
| D4 | Every table file carries **`__row_id BIGINT`**, the stable row identity inside a dataset (today's `root_rows`), and files are **sorted by `__row_id`**. | Diffs and paired compares become a join on one integer column; splits are keyed by it; no more 10M-element JSON arrays in lineage.json. |
| D5 | Splits are Parquet files `(__row_id, fold)` referenced from the version (not from the folder), keyed `(table, target, strategy, params)`. | Carrying a holdout to a derived version is a join, and split identity becomes a hash of two typed columns. |
| D6 | Three hashes: **file sha256** (bytes, the integrity check), **table sha256** (hash of its ordered part hashes), **data sha256** (hash of the canonical data section of the manifest = the version's identity for dedupe) and **manifest sha256** (the whole manifest incl. provenance = the commit hash). | Dedupe must ignore who/why; integrity must cover who/why. |
| D7 | Today's `datasets` table stays the **version** table (its ids are in runs, edges, URLs, audit). New tables: `teams`, `team_members`, `dataset_roots` (the logical dataset), `version_tables`, `table_files`, `blobs`, `blob_refs`, `table_keys`, `table_relations`, `splits`, `views`, `view_versions`, `run_inputs`. | Zero-rename migration: 182 call sites of `csv_path`/`load_frame` keep working behind a shim during the move. |
| D8 | A **saved join (view)** has a **grain table**: every other table joins n:1 onto it (or is aggregated to it). The view's rows *are* the grain table's rows, so `__row_id`, splits and paired compares carry over unchanged. Materialisation is a cache keyed by the view spec + input table hashes. | Without a grain, a join changes the row set and every pairing guarantee is lost. |
| D9 | Migration converts each CSV version to Parquet **with the pandas dtypes old runs saw** (no retyping), keeps the old CSV sha256 as `legacy_sha256`, and keeps data.csv as a `legacy_csv` blob while any run that hashed it is live. Retyping is a new, explicit version. | Old runs' `data_sha256`, split hashes and paired compares must keep verifying. |
| D10 | DuckDB runs **in-process, in-memory, per job** (`duckdb.connect()`; `memory_limit`, `threads`, `temp_directory` set); there is no `.duckdb` database file. Heavy jobs (matching, diff, join materialise) go through a small job queue with a concurrency cap. | No DuckDB file lock contention; memory bounded per job; spill to disk measured to work. |

---

## 1. Entity model

```
team ─┬─< team_members (principal, role)
      └─< project ─┬─< dataset_root (the logical dataset; card fields, default version, key/relations defaults)
                   │        └─< dataset version (today's `datasets` row; immutable; one manifest)
                   │                 ├─< version_tables (name, table sha, schema, n_rows, change)
                   │                 │        └─< table_files (ordered parts → blobs)
                   │                 ├─< table_keys / table_relations (declared + validated per version)
                   │                 └─< splits (per table, target, strategy) → blob
                   ├─< view (saved join; grain table)  └─< view_versions (spec pinned to dataset versions) → optional blob
                   └─< runs ──< run_inputs (exact version/table/view + hashes the fit read)
blobs (content-addressed files) ──< blob_refs (who keeps each file alive: version table, split, view cache, legacy csv, tombstone sample)
```

Rules:

- A **dataset root** belongs to exactly one project, a project to exactly one team. Moving a dataset between projects
  moves the root (and denormalised `project_id`/`team_id` on its versions) in one transaction, audited. Moving across
  teams is an admin action because files are shared by hash (a blob can be referenced from two teams; that is fine: a
  blob has no owner, its references do).
- A **version** has a `parent_id` (NULL for v1) and may have extra `derived_from` parents (a join of two datasets into
  a new dataset, Feature Lab merging a version of another dataset). The primary parent defines `__row_id` continuity.
- Versions form a **tree**, not a line: two people can branch from v3. `version_no` is allocated per root (1, 2, 3 …)
  in creation order, unique per root. The *default* version is a pointer on the root (today's `dataset_cards.default_version_id`).
- A **table name** is the identity of a table across versions of a dataset (`applications`, `bureau`, `Sheet 2`).
  Renaming a table is an op (`rename_table`) recorded in the manifest; `__row_id` continuity follows the op, not the name.
- **Row identity** (`__row_id`) is per (dataset root, table name). `dataset_root_tables.next_row_id` is the
  high-water mark; new rows take ids from it, never re-using one (today new rows get negative ids; legacy negative ids
  are kept as they are, new ones are positive and above the high-water mark).

### 1.1 Metadata DDL (portable SQLite ⇄ Postgres)

Portability rules: ids are app-generated TEXT (`tm_`, `prj_`, `ds_`, `tbl_`, `spl_`, `vw_`, `vv_` + 8–16 hex);
timestamps are ISO-8601 UTC TEXT (sortable, identical in both; Postgres may switch them to `timestamptz` in its own
migration file); JSON is TEXT (Postgres migration uses `jsonb`), and **anything queried is a real column, never a
JSON path** (today's `json_extract(profile, '$.n_cols')` becomes `datasets.n_cols`); booleans are INTEGER 0/1 with a
CHECK; counts are BIGINT (`INTEGER` in SQLite is 64-bit anyway); no AUTOINCREMENT in new tables (the existing
`audit_log` and `lineage_edges` keep theirs; Postgres uses `bigint generated by default as identity` there). Partial
unique indexes (`WHERE …`) and CHECKs work in both.

```sql
-- ── tenancy ──────────────────────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE teams (
    id          TEXT PRIMARY KEY,                       -- tm_<hex>
    slug        TEXT NOT NULL UNIQUE,                   -- url-safe, immutable
    name        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'archived')),
    storage_quota_bytes BIGINT,                         -- NULL = unlimited; checked on upload
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL
);
CREATE TABLE team_members (
    team_id     TEXT NOT NULL REFERENCES teams(id),
    principal   TEXT NOT NULL,                          -- the "Acting as" name today; the SSO subject later
    role        TEXT NOT NULL CHECK (role IN ('owner', 'maintainer', 'editor', 'viewer')),
    added_at    TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    PRIMARY KEY (team_id, principal)
);
CREATE INDEX ix_team_members_principal ON team_members (principal);

-- projects: existing table, gains team_id (backfill: every existing project → team 'tm_default')
ALTER TABLE projects ADD COLUMN team_id TEXT REFERENCES teams(id);
ALTER TABLE projects ADD COLUMN state TEXT NOT NULL DEFAULT 'active';
CREATE INDEX ix_projects_team ON projects (team_id);

-- ── the logical dataset ───────────────────────────────────────────────────────────────────────────────────────────────
-- id = the root version's id (today's datasets.root_id), so every existing URL and root_id reference stays valid.
-- Absorbs dataset_cards (kept as a VIEW over this table for one release, then dropped).
CREATE TABLE dataset_roots (
    id                 TEXT PRIMARY KEY,                -- ds_<hex> (= id of v1)
    team_id            TEXT NOT NULL REFERENCES teams(id),
    project_id         TEXT NOT NULL REFERENCES projects(id),
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    owner              TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    label_definition   TEXT NOT NULL DEFAULT '',
    known_issues       TEXT NOT NULL DEFAULT '',
    default_version_id TEXT,                            -- pointer; a rollback moves it, never the data
    next_version_no    INTEGER NOT NULL DEFAULT 1,      -- allocation counter (bumped in the commit transaction)
    state              TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'archived', 'deleted')),
    created_at         TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    updated_at         TEXT,
    updated_by         TEXT
);
CREATE INDEX ix_roots_project ON dataset_roots (project_id, state);
CREATE INDEX ix_roots_team ON dataset_roots (team_id, state);
CREATE UNIQUE INDEX ux_roots_name ON dataset_roots (project_id, name) WHERE state <> 'deleted';

-- per root and table name: the row-id high-water mark and the declared defaults copied into new versions
CREATE TABLE dataset_root_tables (
    root_id      TEXT NOT NULL REFERENCES dataset_roots(id),
    table_name   TEXT NOT NULL,
    next_row_id  BIGINT NOT NULL,                       -- max(__row_id) ever issued + 1 (never decreases)
    PRIMARY KEY (root_id, table_name)
);

-- ── versions: the existing `datasets` table, one row per version (columns added, none removed) ─────────────────────
-- Existing: id, name, filename, source, note, created_at, size_bytes, rows, target, kind, profile, project_id,
--           parent_id, root_id, sha256, n_rows, op, created_by, state, state_at, state_by, state_scope, verified_at, verified_ok
ALTER TABLE datasets ADD COLUMN team_id          TEXT;          -- denormalised for filters/ACL; set with project_id
ALTER TABLE datasets ADD COLUMN version_no       INTEGER;       -- 1, 2, 3 … per root (backfilled from created_at order)
ALTER TABLE datasets ADD COLUMN storage          TEXT NOT NULL DEFAULT 'csv' CHECK (storage IN ('csv', 'parquet'));
ALTER TABLE datasets ADD COLUMN manifest_sha256  TEXT;          -- the commit hash (whole manifest); NULL while storage='csv'
ALTER TABLE datasets ADD COLUMN data_sha256      TEXT;          -- hash of the manifest's data section (dedupe identity)
ALTER TABLE datasets ADD COLUMN legacy_sha256    TEXT;          -- the data.csv sha256 recorded before migration (= old `sha256`)
ALTER TABLE datasets ADD COLUMN n_tables         INTEGER NOT NULL DEFAULT 1;
ALTER TABLE datasets ADD COLUMN n_cols           INTEGER;       -- of the primary table (today json_extract(profile,'$.n_cols'))
ALTER TABLE datasets ADD COLUMN primary_table    TEXT;          -- the table Build trains on by default ('data' for migrated versions)
ALTER TABLE datasets ADD COLUMN reason           TEXT NOT NULL DEFAULT '';
ALTER TABLE datasets ADD COLUMN commit_state     TEXT NOT NULL DEFAULT 'committed' CHECK (commit_state IN ('committed', 'failed'));
CREATE UNIQUE INDEX ux_versions_no ON datasets (root_id, version_no);
CREATE INDEX ix_versions_data_sha ON datasets (root_id, data_sha256);   -- dedupe lookup
CREATE INDEX ix_versions_project ON datasets (project_id, created_at);
CREATE INDEX ix_versions_parent ON datasets (parent_id);
-- `sha256` keeps its meaning for readers that know only it: = data_sha256 for parquet versions, = legacy_sha256 for csv ones.

-- ── tables of a version ───────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE version_tables (
    version_id     TEXT NOT NULL REFERENCES datasets(id),
    table_name     TEXT NOT NULL,                       -- 1..120 chars; no '/', no leading '__'
    ordinal        INTEGER NOT NULL,                    -- display order
    table_sha256   TEXT NOT NULL,                       -- sha256 over the ordered part sha256s (section 3.6)
    n_rows         BIGINT NOT NULL,
    n_cols         INTEGER NOT NULL,                    -- user columns (without __row_id)
    bytes          BIGINT NOT NULL,                     -- sum of part sizes
    schema_json    TEXT NOT NULL,                       -- [{name, type, nullable, source_type?}], user columns in order
    schema_sha256  TEXT NOT NULL,                       -- hash of the canonical schema_json (schema-only change detection)
    row_id_min     BIGINT, row_id_max BIGINT,
    change         TEXT NOT NULL CHECK (change IN ('new', 'carried', 'appended', 'rewritten')),
    from_version   TEXT,                                -- for carried/appended: the version whose table this continues
    from_table     TEXT,                                -- source name when renamed
    source_file    TEXT,                                -- upload filename / sheet ('loans.xlsx#Payments')
    profile_json   TEXT,                                -- the profiler's output for this table (large; read by id only)
    PRIMARY KEY (version_id, table_name),
    UNIQUE (version_id, ordinal)
);
CREATE INDEX ix_vt_sha ON version_tables (table_sha256);  -- "where else is this table used", dedupe across datasets

CREATE TABLE table_files (
    version_id   TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    part         INTEGER NOT NULL,                      -- 0..n-1, read in this order
    blob_sha256  TEXT NOT NULL REFERENCES blobs(sha256),
    n_rows       BIGINT NOT NULL,
    row_id_min   BIGINT, row_id_max BIGINT,
    PRIMARY KEY (version_id, table_name, part),
    FOREIGN KEY (version_id, table_name) REFERENCES version_tables(version_id, table_name)
);
CREATE INDEX ix_tf_blob ON table_files (blob_sha256);

-- ── keys and relations (declared by a person or guessed and confirmed; validated at commit) ─────────────────────────
CREATE TABLE table_keys (
    version_id   TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    columns_json TEXT NOT NULL,                         -- ["customer_id"] or composite ["loan_id","month"]
    kind         TEXT NOT NULL CHECK (kind IN ('primary', 'unique')),
    declared_by  TEXT NOT NULL,                         -- a principal, or 'guessed' (shown as unconfirmed)
    valid        INTEGER NOT NULL CHECK (valid IN (0, 1)),
    n_duplicates BIGINT NOT NULL DEFAULT 0,
    n_nulls      BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (version_id, table_name, kind, columns_json)
);
CREATE TABLE table_relations (
    version_id    TEXT NOT NULL,
    name          TEXT NOT NULL,                        -- 'payments→applications'
    from_table    TEXT NOT NULL, from_cols_json TEXT NOT NULL,   -- the many side (FK)
    to_table      TEXT NOT NULL, to_cols_json   TEXT NOT NULL,   -- the one side (must be a valid key)
    cardinality   TEXT NOT NULL CHECK (cardinality IN ('n:1', '1:1')),
    declared_by   TEXT NOT NULL,
    n_orphans     BIGINT NOT NULL DEFAULT 0,            -- from rows with no match: a warning, never a block
    PRIMARY KEY (version_id, name)
);

-- ── splits ─────────────────────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE splits (
    id             TEXT PRIMARY KEY,                    -- spl_<hex>
    version_id     TEXT NOT NULL REFERENCES datasets(id),
    table_name     TEXT NOT NULL,                       -- a view version's splits use its grain table's
    split_key      TEXT NOT NULL,                       -- today's splits.split_key(target, strategy, params)
    target         TEXT, strategy TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
    seed           INTEGER NOT NULL, holdout_share REAL NOT NULL, n_folds INTEGER NOT NULL, stratified INTEGER NOT NULL,
    blob_sha256    TEXT NOT NULL REFERENCES blobs(sha256),  -- parquet (__row_id BIGINT, fold TINYINT) sorted by __row_id
    split_sha256   TEXT NOT NULL,                       -- logical identity, section 3.6
    legacy_sha256  TEXT,                                -- splits.split_sha256() of the JSON it was migrated from
    n_holdout      BIGINT NOT NULL,
    carried_from   TEXT REFERENCES splits(id),          -- the parent's split this one continues (holdout kept)
    adopted_from   TEXT,                                -- 'splits.json' (legacy) or NULL
    created_at     TEXT NOT NULL, created_by TEXT NOT NULL,
    UNIQUE (version_id, table_name, split_key)          -- a split is never redrawn
);

-- ── content-addressed files ─────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE blobs (
    sha256       TEXT PRIMARY KEY,                      -- of the file bytes
    key          TEXT NOT NULL UNIQUE,                  -- 'blobs/ab/abcd….parquet' relative to the store root
    kind         TEXT NOT NULL CHECK (kind IN ('table', 'split', 'view_cache', 'manifest', 'source', 'legacy_csv', 'frozen_holdout', 'export')),
    bytes        BIGINT NOT NULL,
    n_rows       BIGINT,
    state        TEXT NOT NULL CHECK (state IN ('pending', 'live', 'purging', 'purged')),
    created_at   TEXT NOT NULL,
    verified_at  TEXT, verified_ok INTEGER,
    purged_at    TEXT, purged_by TEXT, purge_reason TEXT
);
CREATE INDEX ix_blobs_state ON blobs (state, created_at);   -- GC scans pending/purging only

-- who keeps a blob alive; GC deletes a blob with no row here (after a grace period)
CREATE TABLE blob_refs (
    blob_sha256  TEXT NOT NULL REFERENCES blobs(sha256),
    owner_kind   TEXT NOT NULL CHECK (owner_kind IN ('version', 'split', 'view_version', 'run', 'export', 'upload_session')),
    owner_id     TEXT NOT NULL,
    role         TEXT NOT NULL,                         -- 'table:applications#0', 'manifest', 'source', 'legacy_csv', 'frozen_holdout'
    created_at   TEXT NOT NULL,
    PRIMARY KEY (blob_sha256, owner_kind, owner_id, role)
);
CREATE INDEX ix_blob_refs_owner ON blob_refs (owner_kind, owner_id);

-- ── saved joins / views ─────────────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE views (
    id           TEXT PRIMARY KEY,                      -- vw_<hex>
    team_id      TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'archived', 'deleted')),
    created_at   TEXT NOT NULL, created_by TEXT NOT NULL,
    UNIQUE (project_id, name)
);
CREATE TABLE view_versions (
    id            TEXT PRIMARY KEY,                     -- vv_<hex>
    view_id       TEXT NOT NULL REFERENCES views(id),
    version_no    INTEGER NOT NULL,
    spec_json     TEXT NOT NULL,                        -- section 4.4; every input pinned to a dataset version + table
    spec_sha256   TEXT NOT NULL,                        -- canonical spec incl. the pinned input table sha256s
    grain_version TEXT NOT NULL REFERENCES datasets(id),
    grain_table   TEXT NOT NULL,
    n_rows        BIGINT,                               -- = the grain table's rows (enforced)
    schema_json   TEXT,
    cache_blob    TEXT REFERENCES blobs(sha256),        -- materialised result (optional; a cache, re-creatable)
    cache_sha256  TEXT,                                 -- sha256 of the materialised bytes the last run read
    created_at    TEXT NOT NULL, created_by TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    UNIQUE (view_id, version_no),
    UNIQUE (view_id, spec_sha256)                       -- the same spec on the same inputs is the same view version
);

-- ── what a run read (replaces csv_path/splits_path/data_sha256 scattered in runs.config) ────────────────────────────
CREATE TABLE run_inputs (
    id             TEXT PRIMARY KEY,                    -- ri_<hex>
    run_id         TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('train', 'holdout_source', 'score', 'reference', 'join_input')),
    input_kind     TEXT NOT NULL CHECK (input_kind IN ('table', 'view')),
    version_id     TEXT,                                -- dataset version (table input, or a view's input)
    table_name     TEXT,
    view_version_id TEXT,
    table_sha256   TEXT,                                -- expected (from the manifest)
    read_sha256    TEXT,                                -- computed by the worker over the bytes it read
    split_id       TEXT REFERENCES splits(id),
    split_sha256   TEXT,
    match          INTEGER,                             -- read_sha256 = table_sha256 (NULL = not checked)
    created_at     TEXT NOT NULL
);
CREATE INDEX ix_run_inputs_run ON run_inputs (run_id);
CREATE INDEX ix_run_inputs_version ON run_inputs (version_id, table_name);   -- "which runs read applications@v7"
CREATE INDEX ix_run_inputs_view ON run_inputs (view_version_id);
```

A surrogate id because the natural key has nullable parts (a table input has no view, a view input no table), which
neither engine allows in a PRIMARY KEY. The `trained_on` edge stays (the lineage tree and `usage()` read it);
`run_inputs` is its typed detail, one row per table or view the fit read.

`lineage_edges` changes: `src_kind`/`dst_kind` CHECK gains `'view'` (a view version), relation gains `'joined_from'`
(dataset version → view version, meta: table name, table sha256) and `'merged_from'` (a secondary parent). SQLite
cannot alter a CHECK: rebuild once, as `migrate()` already does for `champions`.

**Audit hooks.** Every write path ends in the commit transaction (section 5.2) which writes, on the same connection,
an `audit_log` row: `dataset_version_created` (details: dataset_id, root_id, team_id, project_id, parent, op,
manifest_sha256, data_sha256, tables [{name, sha, change}]), `view_version_created`, `blob_purged`,
`blob_gc_deleted` (system), `table_keys_declared`, `team_member_added/removed`, `dataset_moved`. Reads that today write
read-audit keep doing so, with the table name added. `audit_log.details` gains a promoted `team_id` column
(`ALTER TABLE audit_log ADD COLUMN team_id TEXT; CREATE INDEX ix_audit_team ON audit_log (team_id, ts)`), so a
team's audit is one indexed query.

**Permissions** (designed now, enforced when `ENSEMBLE_ENFORCE_TEAMS=1`): `can(principal, action, resource)` resolves
resource → project → team, then role ≥ the action's minimum (viewer: read/preview/export; editor: upload, edit,
derive, train; maintainer: archive, default pointer, keys/relations, views; owner: members, delete approval, purge).
Every list query takes `team_id IN (:teams_of_principal)`; that is why `team_id` is denormalised onto `datasets`,
`views`, `audit_log` and `runs` (add `runs.team_id` too). With enforcement off, `can()` logs would-be denials to
`audit_log` as `acl_would_deny`, so the rules can be checked on real use before switching on.

### 1.2 Mapping onto today's tables

| Today | Target | Migration |
|---|---|---|
| `datasets` (one row per version, root_id, parent_id, sha256, state…) | same table = version; + columns above | backfill `version_no` (row order per root), `n_cols`, `team_id`, `storage='csv'`, `legacy_sha256 = sha256` |
| `dataset_cards` (root_id, card fields, default_version_id) | `dataset_roots` | copy; keep `dataset_cards` as a VIEW for one release |
| `projects` | + `team_id`, `state` | every project → `tm_default` |
| `lineage_edges` | unchanged; + `view` kind, `joined_from`, `merged_from` | rebuild CHECK once |
| `data/datasets/<id>/data.csv` | `version_tables('data')` + `table_files` → blob | section 6 |
| `lineage.json` `root_rows` | the `__row_id` column | section 6 |
| `lineage.json` other fields (op, changes, checks, reason, by) | the manifest `ops`/`provenance` + `datasets.op/reason/created_by` | copied into the migrated manifest |
| `splits.json`, `splits/<key>.json` | `splits` + blob | section 6 |
| `TOMBSTONE.json` | `datasets.state='deleted'` + the manifest blob (kept) + `blob_refs(role='frozen_holdout')` | the JSON is regenerated from the DB on demand |
| `runs.config.csv_path/splits_path/data_sha256(_expected)` | `run_inputs` | old runs keep their config; new runs write both during the transition |
| `model_versions` (`hashes.data_sha256` in result) | unchanged; registry build reads `run_inputs` | — |
| `dataset_requests` (four-eyes delete) | unchanged; action gains `'purge'` (section 5.4) | rebuild CHECK |
| `audit_log` | + `team_id` | backfill from details.dataset_id → datasets.team_id |

---

## 2. Parquet layout

### 2.1 Store root and keys

One **store root** per deployment: `file:///…/data/lake/` locally, `s3://<bucket>/<prefix>/` in the cloud. Keys below
are relative to it. Nothing is ever overwritten; nothing is renamed after it is visible.

```
blobs/<aa>/<sha256>.parquet        table parts, split files, view caches (aa = first two hex chars: 256 fan-out dirs)
blobs/<aa>/<sha256>.json           manifests (canonical JSON, section 2.2)
blobs/<aa>/<sha256>.<ext>          kept upload sources (.csv, .xlsx; optional, retention per team) and legacy data.csv
tmp/<writer-uuid>/<n>.parquet      in-flight writes; never referenced; swept after 24 h
exports/<version_id>/<table>.csv   export cache (re-creatable; LRU-deleted; not a blob)
```

Why content-addressed and not `teams/<t>/projects/<p>/datasets/<d>/v<n>/<table>.parquet`:

- dedupe: a version that changes one of five tables writes one file; a carried table costs one DB row;
- integrity: the key *is* the hash, so verify = re-hash and compare with the key; an outside change cannot hide;
- moving a dataset to another project or team moves no bytes;
- S3 has no atomic rename and no directory; a write-once key plus a DB commit is the only safe pattern anyway.

What humans browse is the DB (and an `export` endpoint), not the bucket. For people who want a readable layout on disk
(admins debugging), `GET /api/datasets/{id}/manifest` returns the manifest with resolved file paths.

Per-team isolation, if it is ever required (separate buckets/KMS keys per team), is a store root per team
(`teams.store_root`), not a different key scheme; dedupe then stops at the team boundary, which is the right
behaviour for isolation.

### 2.2 The manifest (one per version; its sha256 is the commit hash)

Canonical form: UTF-8 JSON, keys sorted, no insignificant whitespace (`json.dumps(m, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)`), integers as integers, no floats except where stated, timestamps ISO-8601
UTC with seconds. `data` holds everything that defines the data; `provenance` everything about how it came to be.

```json
{
  "format": "ensemble.manifest/1",
  "dataset": {"root_id": "ds_1a2b3c4d", "version_id": "ds_9f8e7d6c", "version_no": 7, "team_id": "tm_credit", "project_id": "prj_1a2b3c4d"},
  "data": {
    "primary_table": "applications",
    "tables": [
      {
        "name": "applications",
        "table_sha256": "5e1c…",
        "n_rows": 10000000,
        "n_cols": 30,
        "row_id": {"column": "__row_id", "min": 0, "max": 10099999, "sorted": true},
        "schema": [
          {"name": "customer_id", "type": "BIGINT", "nullable": false},
          {"name": "zip", "type": "VARCHAR", "nullable": false, "note": "leading zeros: kept as text"},
          {"name": "app_date", "type": "DATE", "nullable": false},
          {"name": "amt_0", "type": "DOUBLE", "nullable": true},
          {"name": "target", "type": "INTEGER", "nullable": false}
        ],
        "schema_sha256": "0b77…",
        "files": [
          {"sha256": "a1f0…", "key": "blobs/a1/a1f0….parquet", "bytes": 712345678, "n_rows": 10000000, "row_id_min": 0, "row_id_max": 10099999}
        ],
        "keys": [{"kind": "primary", "columns": ["customer_id"], "valid": true}]
      },
      {"name": "bureau", "table_sha256": "77aa…", "n_rows": 10000000, "n_cols": 5, "…": "…"}
    ],
    "relations": [
      {"name": "bureau→applications", "from": {"table": "bureau", "columns": ["customer_id"]},
       "to": {"table": "applications", "columns": ["customer_id"]}, "cardinality": "1:1", "n_orphans": 0}
    ]
  },
  "data_sha256": "c0de…",
  "provenance": {
    "parent": {"version_id": "ds_5a5a5a5a", "manifest_sha256": "beef…"},
    "other_parents": [],
    "op": "upload_version",
    "ops": [
      {"op": "upload_version", "table": "applications", "matched_by": "key:customer_id", "matched": 9900000, "changed": 100000, "added": 100000, "removed": 100000},
      {"op": "carry", "table": "bureau", "from_version": "ds_5a5a5a5a"}
    ],
    "changes": {"applications": "rewritten", "bureau": "carried"},
    "sources": [{"filename": "applications_2026-11.csv", "sha256": "9999…", "bytes": 2161000000, "kept": true, "encoding": "utf-8", "separator": ","}],
    "checks": {"warnings": [{"code": "row_drop", "table": "applications", "message": "…"}]},
    "created_at": "2026-11-21T10:00:00Z",
    "created_by": "a.analyst",
    "reason": "November extract",
    "engine": {"writer": "duckdb 2.0.1", "parquet": {"compression": "zstd", "level": 3, "row_group_rows": 122880}}
  },
  "legacy": null
}
```

- `data_sha256 = sha256(canonical(data))`. Two versions with the same tables, schemas, keys and relations have the
  same `data_sha256` whoever made them: that is today's DL3 dedupe ("the same bytes as v3"), now per dataset and
  multi-table.
- `manifest_sha256 = sha256(canonical(manifest))` with `data_sha256` filled in. It is stored as `datasets.manifest_sha256`
  and the manifest is itself a blob at `blobs/<aa>/<manifest_sha256>.json`. A version row whose manifest blob is
  missing or re-hashes differently fails `verify`.
- The manifest has no mutable fields. State (archived/deleted), the default pointer, tags and notes live in the DB.
- `legacy` (migrated versions only): `{"csv_sha256": "…", "csv_blob": "…", "root_rows_source": "lineage.json",
  "dtypes": "pandas-inferred", "migrated_at": "…", "verified": {"frame_equal": true}}`.
- `ops` is capped at 200 entries per version as today (`MAX_OPS`); the full op list of an edit, when larger, is a
  `source` blob referenced from `sources`.

### 2.3 Row identity

- Every table file has `__row_id BIGINT NOT NULL` as its **first** column, unique within the table, and the file is
  written **sorted by `__row_id`** (exception: migrated CSV-era versions keep their original row order, section 6.1;
  the manifest says `row_id.sorted`) (Parquet min/max statistics then let DuckDB prune row groups on `__row_id`
  predicates, and two versions of a table diff with a merge join).
- Meaning: today's *root row*. v1: `__row_id = 0..n-1` (= the position in the uploaded file). A derived version keeps
  each surviving row's id; new rows take `next_row_id, next_row_id+1, …` from `dataset_root_tables`. Migrated legacy
  negative ids (synthetic rows) are kept as they are.
- Position (the 0-based index today's splits and `set_cells._row` use) is not stored: it is
  `read_parquet(…, file_row_number=true)` / the order of `__row_id`. Edit ops move to `__row_id` (`set_cells.row`
  accepts either during the transition; the UI sends `__row_id`).
- The `__` prefix is reserved: an upload whose header has a column starting with `__` is refused with a message.
- Consumers that hand a frame to AutoGluon drop `__row_id` (and use it as the pandas index, which is what
  `load_frames` does with positions today).

### 2.4 Splits

A split is a Parquet blob with two columns, sorted by `__row_id`:

| column | type | values |
|---|---|---|
| `__row_id` | BIGINT | every row of the table |
| `fold` | TINYINT | 0..k-1 = CV fold; -1 = holdout; -2 = excluded; -3 = tuning (today's conventions) |

`holdout` is `fold = -1` (today it is a separate list; the JSON had both). Metadata (target, strategy, params, seed,
share, stratified) is in the `splits` row, not the file. A split for a derived version is **carried**: a SQL join of
the parent split on `__row_id` keeps every surviving row's fold and holdout membership, and new rows get the seeded
`new_holdout_share` assignment (today's `derive()` loop, now one query). The split blob is referenced by
`blob_refs(owner_kind='split')` and survives as long as its version.

### 2.5 Files, row groups, compression, partitioning

- **Writer**: DuckDB `COPY … TO … (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 122880)`.
  Measured: 0.35× of the CSV size (710 MB for a 2.06 GB CSV), against 0.55× for pyarrow's streaming writer with zstd
  (1.13 GB: smaller batches, weaker dictionaries). Read speed into pandas was not compared between the two.
- **Row groups**: DuckDB's 122 880 rows (≈ 5–15 MB compressed at 30 columns): parallel reads and good pruning. Wider
  tables (>200 columns) use 30 720.
- **Parts**: one file per table up to `PART_MAX_ROWS = 8 000 000` or ~1 GB compressed; larger tables are written as
  several parts split by `__row_id` range (DuckDB `COPY … (FILE_SIZE_BYTES '1GB')` with a per-file sha computed after).
  Parts are also how an append-only delta is stored without rewriting (section 3.2).
- **No Hive partitioning** inside a version. The unit of change is a version, reads are whole-table or `__row_id`
  pruned, and partition columns would need a policy per dataset. A dataset that is naturally monthly (an extract per
  month) is modelled as appended parts, which gives the same pruning through `row_id_min/max`.
- **Sort**: by `__row_id`. No secondary sort (Z-order etc.): not needed at 10M rows (section 8).

### 2.6 Typed schema rules (the import contract)

The import runs in DuckDB in two passes over the uploaded file:

1. `sniff_csv` (sample 20 480 rows) proposes types (the sniff and check together: 0.25 s at 100k, 0.7 s at 1M and 3.9 s at
   10M rows); then one scan with `all_varchar=true` counts, per proposed-numeric
   column, values with a leading zero (`^-?0[0-9]`), and for every column the values that fail `TRY_CAST` to the
   proposed type (the sniffer only saw a sample).
2. Decide, then `COPY (SELECT row_number()-1 AS __row_id, * FROM read_csv(…, columns={decided})) TO parquet`.

| Rule | Decision |
|---|---|
| A numeric-looking column with any leading-zero value (zip, account, branch code) | `VARCHAR` (never int). Measured: DuckDB 2.0's sniffer already keeps the test `zip` as VARCHAR; pandas reads it as int64 and loses the zeros (today's bug). |
| Integers | `BIGINT` (`INTEGER` if the sniffer says so); nulls allowed. Stays integer in Parquet; pandas sees float64 only for an int column with nulls (or `Int64` with `types_mapper`). |
| Any value that fails the cast, outside a NA token | the column stays `VARCHAR` and a warning lists 5 examples; nothing is silently nulled. |
| Decimal comma (`;`-separated European files) | `decimal_separator=','` as today. |
| Dates | `DATE` / `TIMESTAMP` when every non-null value parses in one unambiguous format; a column where both `dd/mm` and `mm/dd` fit stays `VARCHAR` with a warning ("declare the format"). Time zones: kept as `TIMESTAMP WITH TIME ZONE` only when the text has an offset. |
| Booleans | `BOOLEAN` for true/false, yes/no, Y/N columns (exactly two values plus NA). |
| Money / exact decimals | `DOUBLE` by default; `DECIMAL(p,s)` when the user declares it (Contract page). |
| Categoricals | `VARCHAR`; Parquet dictionary encoding does the compression. |
| NA tokens | `''`, `NA`, `N/A`, `NULL`, `null`, `NaN`, `-` (as pandas today, minus `None`), configurable per team. |
| Column names | kept exactly (trimmed); duplicates and `__` prefix refused, as today. |
| Declared overrides | `PUT /api/datasets/{id}/contract` (per table: column → type, date format) applies to the next version and is stored in the manifest schema as `source_type`. |

A version's schema is fixed by its manifest; changing a type is an op (`cast_column`) and a new version.

---

## 3. Versioning algorithms (Parquet + DuckDB)

All algorithms share one skeleton, the **version writer**:

```
w = VersionWriter(root_id, parent_version_id, op, by, reason)     # opens an in-memory DuckDB (threads, memory_limit, temp_directory)
for each table in the new version:
    either  w.carry(table, from_version)                          # 0 bytes written: reuse the parent's table_files rows
    or      w.append(table, from_version, new_rows_relation)      # writes one new part; old parts reused
    or      w.write(table, relation_sql)                          # COPY relation (incl. __row_id) → tmp → hash → blob
w.split(...)  # carry or draw the splits that the version needs now (others are drawn lazily, as today)
w.commit()    # manifest → blob; ONE DB transaction (section 5.2); returns the version, or the existing one on dedupe
```

`relation_sql` is always a DuckDB query over `read_parquet([...parent parts...])` and the uploaded file, so no table
is ever fully in Python memory. Pandas remains only where the algorithm is pandas today and tables are small
(Data Doctor / Feature Lab transforms that are Python functions): those hand back an Arrow table with `__row_id`,
written through the same writer (`w.write_arrow`).

### 3.1 Full upload → new version (row matching in DuckDB)

Input: the parent version, an uploaded file (or several, section 4), an optional row key per table.

```sql
-- 1. import the upload with the typed rules (section 2.6) into a temp Parquet file (no __row_id yet)
COPY (SELECT * FROM read_csv('upload.csv', columns = {decided})) TO 'tmp/new.parquet' (FORMAT parquet);

-- 2a. match by key (key declared or guessed and confirmed; must be unique and non-null on both sides)
CREATE TEMP TABLE m AS
SELECT n.file_row_number AS new_pos, p.__row_id AS parent_row
FROM read_parquet('tmp/new.parquet', file_row_number = true) n
LEFT JOIN read_parquet([parent parts]) p USING (customer_id);

-- 2b. or match by row hash over the shared same-type columns; duplicates pair in order (today's _hash_pairs)
CREATE TEMP TABLE m AS
WITH a AS (SELECT __row_id, hash(c1, c2, …) AS h FROM read_parquet([parent parts])),
     b AS (SELECT file_row_number AS pos, hash(c1, c2, …) AS h FROM read_parquet('tmp/new.parquet', file_row_number = true)),
     a2 AS (SELECT *, row_number() OVER (PARTITION BY h ORDER BY __row_id) AS occ FROM a),
     b2 AS (SELECT *, row_number() OVER (PARTITION BY h ORDER BY pos) AS occ FROM b)
SELECT b2.pos AS new_pos, a2.__row_id AS parent_row FROM b2 LEFT JOIN a2 USING (h, occ);

-- 3. assign ids: matched rows keep the parent's, new rows take next_row_id + k (in file order)
-- 4. write the table sorted by __row_id (sketch: new rows numbered in file order from next_row_id)
COPY (SELECT COALESCE(m.parent_row, :next_row_id - 1 + row_number() OVER (PARTITION BY m.parent_row IS NULL ORDER BY n.pos)) AS __row_id, n.* EXCLUDE (pos)
      FROM … ORDER BY __row_id) TO 'tmp/part0.parquet' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880);
```

- Counts for the preview (matched / changed / added / removed) are aggregates over `m` plus the diff of 3.5; the
  preview writes the temp files only (in `tmp/`, swept), never a blob or a DB row, as today's `preview=true`.
- `hash()` is DuckDB's 64-bit hash. At 10M rows the chance of any collision is ~3·10⁻⁶; a collision can only pair two
  different rows, which the changed-row check (3.5) then reports as "changed", never silently equal.
- Measured (section 8): key match **0.84 s**, hash match **3.4 s** at 10M rows, against minutes for today's pandas
  path (`legacy_match`).

### 3.2 Delta upload (upserts and deletes by key)

A delta is two optional files per table: `upserts` (full rows, the key required) and `deletes` (key column(s) only),
or one file with an `__op` column (`U`/`D`), the shape most export tools produce. Needs a valid primary key on the
table (`table_keys`).

```sql
WITH p AS (SELECT * FROM read_parquet([parent parts])),
     u AS (SELECT * FROM read_parquet('tmp/upserts.parquet')),
     d AS (SELECT key FROM read_parquet('tmp/deletes.parquet')),
     kept AS (SELECT p.* FROM p ANTI JOIN d USING (key) ANTI JOIN u USING (key)),
     upd  AS (SELECT p.__row_id, u.* FROM u JOIN p USING (key)),                      -- updated rows keep their id
     ins  AS (SELECT :next_row_id - 1 + row_number() OVER (ORDER BY u.key) AS __row_id, u.* FROM u ANTI JOIN p USING (key))
SELECT * FROM (kept UNION ALL BY NAME upd UNION ALL BY NAME ins) ORDER BY __row_id
```

- **Insert-only delta** (no key collides, no deletes): written as a new **part** after the parent's parts
  (`change='appended'`); the parent's files are reused, so the cost is the size of the delta, not the table.
- **Mixed delta**: the table is rewritten (measured **10M rows + 1.5 % delta: see section 8**).
- Checks before commit: keys in `deletes` that do not exist (warning with count), duplicate keys inside the delta
  (refused), schema drift between delta and table (same rules as an upload: warning; a new column is added as NULL to
  old rows only with an explicit `allow_new_columns`).
- The manifest `ops` records `{op: "delta", table, upserted, inserted, deleted, missing_deletes, source sha256}`.

### 3.3 Edit ops (today's `dataset_edit.py`)

Each op compiles to a SQL projection/filter over the previous relation, so a chain of ops is one query:

| op | SQL |
|---|---|
| `set_cells` | `LEFT JOIN (VALUES …) e(__row_id, col, val)` per column, `COALESCE` via `CASE WHEN e.__row_id IS NOT NULL` (≤ 5 000 cells) |
| `delete_rows` / `filter_rows` | `WHERE NOT (…)` / `WHERE …` (filters compile to SQL predicates; the formula whitelist stays) |
| `add_rows` | `UNION ALL BY NAME SELECT next_row_id + k AS __row_id, …` |
| `rename_column` / `drop_column` | `SELECT * RENAME (a AS b)` / `SELECT * EXCLUDE (a)` |
| `add_column` | `SELECT *, <compiled formula> AS name` (the AST whitelist compiles to SQL instead of numpy) |
| `cast_column` | `TRY_CAST(col AS T)`; the count of values that became NULL is reported in the change log and the preview |
| `replace_values` / `fill_missing` | `CASE …` / `COALESCE(col, (SELECT median(col) FROM prev))` |

The preview evaluates the query with `LIMIT` for examples plus aggregate counts, and `__row_id` replaces `_row`
(`_row` is accepted for one release and translated with `file_row_number`). Changing a single table writes only that
table; the others are carried. The parent-integrity check of DL3 becomes: the parent's part files re-hash to their
keys (cheap: 0.7 s per 700 MB, and skippable when `blobs.verified_at` is recent).

### 3.4 Derived versions (Data Doctor, Feature Lab, relabel, Autopilot, active learning)

Today `derive(parent, df, keep_idx | row_map, …)` takes a pandas frame plus positions. New contract:

```python
store.derive(parent_id, {table_name: TableChange}, op=…, by=…, reason=…, splits="carry")
TableChange = Carry() | Sql(query) | Arrow(table_with___row_id) | Append(arrow_or_sql)
```

- A frame returned by a Python fix **must keep `__row_id`** for surviving rows and NULL for new rows; the writer
  assigns new ids. This replaces `keep_idx`/`row_map` and the `root_rows` arrays.
- Feature Lab adding columns is `Sql("SELECT *, <expr> AS f1 FROM t")` when the feature is expressible in SQL, else
  Arrow; either way only that table is rewritten.
- Relabel (`set_cells` on the target) and drop rows are edit ops (3.3).
- Splits are carried by joining the parent split on `__row_id` (2.4): one query instead of the Python loop in
  `derive()`.
- Dedupe (DL3 m3): compute `data_sha256` before commit; for `DEDUPE_OPS` return the existing version with the same
  `data_sha256` in this root. Note the change: dedupe is by **data identity** (same table hashes, schema, keys), so it
  works across ops and is independent of CSV byte formatting.

### 3.5 Diffs, per table

`diff(a, b)` for two versions of the same root:

1. **Tables**: by name; `table_sha256` equal → `unchanged` (no scan). A table present in one side only is
   added/removed; a removed + added pair with equal `table_sha256` is reported as a rename.
2. **Schema** (per changed table): from the manifests: columns added/removed/retyped (no scan).
3. **Rows** (per changed table), a join on `__row_id`:

```sql
SELECT count(*) FILTER (WHERE a.__row_id IS NULL) AS added,
       count(*) FILTER (WHERE b.__row_id IS NULL) AS removed,
       count(*) FILTER (WHERE a.__row_id IS NOT NULL AND b.__row_id IS NOT NULL AND (a.c1 IS DISTINCT FROM b.c1 OR …)) AS changed,
       sum(CAST(a.c1 IS DISTINCT FROM b.c1 AS INT)) AS c1_changed, …                 -- per shared column
FROM read_parquet([a parts]) a FULL JOIN read_parquet([b parts]) b USING (__row_id)
```

   plus the first 50 changed rows with before/after values for the UI, and per-column distribution shifts from
   DuckDB `SUMMARIZE`-style aggregates (replaces `column_stats`' 200 000-row pandas sample with exact numbers).
4. Versions in **different roots** (or a parquet version against an unmigrated csv one) have no shared `__row_id`:
   fall back to key match (3.1) or row hash, and say so, as today.

Measured at 10M rows: per-column counts **4.1 s**, full diff incl. changed-row count **8.2 s** with no memory limit
and in section 8 under a 1 GB limit.

### 3.6 Hashing

| Hash | Definition | Computed | Used for |
|---|---|---|---|
| file sha256 | sha256 of the file bytes | streaming over the tmp file right after COPY (8 MB chunks; measured 0.73 s for 710 MB) and, for S3, sent as `x-amz-checksum-sha256` so S3 verifies the upload | blob key; integrity; outside-change detection |
| table sha256 | `sha256("ensemble.table/1\n" + "\n".join(part sha256s in part order))` | from the part hashes, no data read | carried-table detection, dedupe, `run_inputs.table_sha256` |
| schema sha256 | sha256 of the canonical schema JSON | from the manifest | schema-only change detection |
| data sha256 | sha256 of canonical `manifest.data` | at commit | version identity, dedupe |
| manifest sha256 | sha256 of the canonical manifest | at commit | the commit hash; `verify` |
| split sha256 | `sha256("ensemble.split/1\n" + __row_id int64-LE bytes + fold int8 bytes)` over rows sorted by `__row_id`, streamed in batches with pyarrow | at split creation | split identity, independent of Parquet writer |
| logical table hash (optional) | DuckDB `sum(hash(__row_id, cols…)::HUGEINT) mod 2⁶⁴` | on demand (1.1 s at 10M) | "same content, different bytes" (a re-encode by a new writer version): a hint shown as "equal content", never an integrity proof (non-cryptographic) |

Why byte hashes and not a logical hash as the identity: integrity must be cryptographic and must not depend on the
engine's hash function staying stable across DuckDB releases. The cost is that the same rows written by a different
writer version get a different table sha256; that only costs dedupe (a second copy), never correctness, and the
logical hash explains it in the UI.

The training worker keeps DL3's rule, "hash exactly the bytes this fit reads": it streams each part file through
sha256 while reading it (one pass; `pyarrow.parquet.read_table` on a `HashingFile` wrapper, or hash-then-read from the
local cache), recomputes the table sha256, and compares with `run_inputs.table_sha256`; mismatch → the fit is refused
as today (`worker.data_gate`).

### 3.7 Copy-on-write: what is shared and what is rewritten

| Change | Bytes written | Notes |
|---|---|---|
| Upload replaces 1 of N tables | that table | N-1 tables carried (one `table_files` row each) |
| Edit / Doctor fix on 1 table | that table | as above |
| Insert-only delta | the new rows only (a new part) | parts accumulate; compaction (below) |
| Upsert/delete delta, any edit | the whole table (all its parts) | 10M rows: measured in section 8 |
| Metadata only (keys, relations, card) | nothing in the lake | keys/relations are part of `data` → new version; card/default are not versioned |
| Same data again | nothing | dedupe returns the existing version |

**Compaction**: a table with more than 16 parts, or with parts under 64 MB, is rewritten into ≤ 1 GB parts the next
time it is rewritten anyway; never as a separate background job that would create a version nobody asked for.

**Not chosen (and why)**: deletion vectors / row-level COW (Delta, Iceberg) would make a 1 % update write 1 % of
the bytes instead of 100 %. At 10M rows the full rewrite is tens of seconds and ~700 MB, and the version count per
dataset is human-driven (tens to hundreds), so the storage cost is bounded by `versions × changed tables`. Escalation
trigger (section 9): tables routinely > 100M rows *and* frequent small updates.

---

## 4. Multi-sheet and multi-table datasets

### 4.1 Excel: one table per sheet

- Reader: `python-calamine` (Rust, reads xlsx/xls/xlsb/ods, typed cells, ~10× openpyxl) into Arrow per sheet, then
  the same writer. DuckDB's `excel` extension (`read_xlsx`) is an alternative but needs an extension download, which
  is a deployment risk behind a proxy; calamine is a wheel.
- Each non-empty sheet → a table named after the sheet (trimmed; `/` and leading `__` replaced; collisions get
  ` (2)`). Hidden sheets are listed in the preview and skipped unless ticked. Sheets with fewer than two data rows
  are listed as "skipped: empty / notes".
- Header row: the first row with ≥ 2 non-empty cells and no merged cells; the preview shows it and lets the person
  pick another row (`header_row` per sheet in the upload request).
- Cell types: Excel types are used (a text cell `"00123"` stays VARCHAR; dates stay DATE/TIMESTAMP). Formulas → their
  cached values. A column mixing numbers and text → VARCHAR with a warning (same rule as CSV).
- The source workbook is kept as a `source` blob (subject to retention), referenced from the manifest `sources`,
  each table's `source_file` = `book.xlsx#Sheet name`.

### 4.2 Several files in one upload

`POST /api/datasets` (new dataset) and `POST /api/datasets/{id}/versions` (new version) accept multipart with N files
and a JSON `plan`:

```json
{"tables": [
   {"file": "applications.csv", "table": "applications", "mode": "replace", "row_key": "application_id"},
   {"file": "bureau.csv",       "table": "bureau",       "mode": "replace", "row_key": "customer_id"},
   {"file": "payments_nov.csv", "table": "payments",     "mode": "append"},
   {"file": "closed.csv",       "table": "applications", "mode": "delete"}
 ],
 "primary_table": "applications",
 "keep": "others"            // tables not named: "others" = carried from the parent (default), "none" = dropped
}
```

Without a plan, the preview proposes one: each file → a table named after the file stem, matched to an existing
table of the same name (`replace`), new names → new tables. The preview returns per-table counts, contract warnings
and the guessed keys/relations, and writes nothing (temp files only). The commit is one version whatever the number
of files: a reader never sees half of an upload.

### 4.3 Keys and relations

- **Keys**: per table, guessed (`guess_key` today: id-like name, unique, non-null) and shown as "unconfirmed"; a
  person confirms or declares (`PUT /api/datasets/{id}/tables/{table}/key`), which makes a new version only if the
  key differs (keys are part of `data`). Validation at commit: `SELECT count(*) - count(DISTINCT key), count(*) FILTER
  (WHERE key IS NULL)` → `valid`, `n_duplicates`, `n_nulls`. An invalid declared key is a warning on uploads and a
  hard error for deltas (a delta cannot be applied without a valid key).
- **Relations**: `from.table(cols) → to.table(cols)`, with `to` a valid key. Guessed when a column of one table has
  the name and type of another table's key and ≥ 90 % of its values are found there. Validation: orphans
  (`from` rows with no match) counted, never blocking. Relations are what the view editor (4.4) offers as join paths,
  and what Data Doctor uses for cross-table checks (orphans, many-to-one fan-out).
- Carried forward to the next version automatically when both tables still exist with the columns; re-validated.

### 4.4 Saved joins (views) as model inputs

A view is a named, versioned **spec**, not data:

```json
{
  "format": "ensemble.view/1",
  "grain": {"version_id": "ds_9f8e7d6c", "table": "applications"},
  "joins": [
    {"type": "left", "relation": "bureau→applications",
     "input": {"version_id": "ds_9f8e7d6c", "table": "bureau"}, "columns": ["score", "n_lines", "total_debt", "status"], "prefix": "bureau_"},
    {"type": "aggregate", "input": {"version_id": "ds_9f8e7d6c", "table": "payments"},
     "on": {"from": ["customer_id"], "to": ["customer_id"]},
     "aggs": [{"name": "n_pay", "fn": "count"}, {"name": "sum_pay", "fn": "sum", "column": "amount"},
              {"name": "last_paid", "fn": "max", "column": "paid_on"},
              {"name": "pay_90d", "fn": "sum", "column": "amount", "where": {"column": "paid_on", "cmp": "ge", "value": {"relative_to": "app_date", "days": -90}}}]}
  ],
  "filter": null,
  "drop_columns": []
}
```

Rules that keep it safe:

1. **Grain**: the view has exactly the grain table's rows, in its `__row_id`; `n:1`/`1:1` joins add columns, `1:n`
   children must be aggregated (`aggregate` join). A join that would multiply rows is refused at spec validation
   (checked from `table_keys`: the joined side's `on` columns must be a valid key). A `filter` is allowed and recorded;
   the view's `__row_id`s are then a subset, still the grain's ids.
2. **Pinned inputs**: every input names a dataset version and a table; `spec_sha256` is computed over the canonical
   spec **plus each input's `table_sha256`**, so a view version is immutable data-wise. Inputs may come from other
   datasets of the same team (a bureau dataset shared across projects): a `merged_from` lineage edge records it.
3. **"Follow latest"**: a view can be marked `track: "default"` (UI: "use the dataset's default version"); this does
   not mutate a view version. When the default of an input moves, the next use creates a new view version (or reuses
   the one with the same `spec_sha256`), audited as `view_version_created` with `reason: "input default moved"`.
4. **Point-in-time** joins (the `relative_to` window above) are expressed in the spec, so leakage control is reviewable.
5. **Splits** come from the grain table's split (2.4), filtered to the view's rows: paired compares across runs on
   different views over the same grain stay paired.

**Materialised or not?** A view version is materialised lazily, the first time a run, a profile or an export needs
it, into a `view_cache` blob (`ORDER BY __row_id`), recorded as `view_versions.cache_blob`. It is a cache: GC may
delete it when no live run references it (`blob_refs(owner_kind='run')` pins it for runs that are live in the
registry, so a champion's exact training bytes stay reproducible and verifiable). Measured: materialising a
10M-row grain + 1:1 table + aggregated 3-per-row child takes **9.9 s** (section 8). Small views are not worth
caching (< 1 s); the threshold is `VIEW_CACHE_MIN_ROWS = 1 000 000`.

**Lineage of a run on a view**: `run_inputs` gets one row `role='train', input_kind='view', view_version_id,
read_sha256 = sha256 of the materialised bytes` plus one `role='join_input'` row per input table
(`version_id, table_name, table_sha256`); `lineage_edges` gets `dataset → view (joined_from, meta: table, sha)` and
`view → run (trained_on)`. `usage()` and the delete blockers follow `joined_from` edges, so a dataset version that fed
a live champion through a view cannot be deleted, exactly like a direct input today.

---

## 5. Storage abstraction, commit, GC, tombstones and purge

### 5.1 The `BlobStore` interface (new module `backend/app/lake/store.py`)

```python
class BlobStore(Protocol):
    root: str                                              # 'file:///…/data/lake' | 's3://bucket/prefix'
    def put_file(self, local: Path, sha256: str, ext: str) -> str      # → key; no-op if the key exists (write-once)
    def exists(self, key: str) -> bool
    def open(self, key: str) -> BinaryIO                   # streaming read (verify, export, worker hash-while-read)
    def uri(self, key: str) -> str                         # what DuckDB/pyarrow read: local path or s3:// URL
    def local_copy(self, key: str) -> Path                 # cache for engines that need a local file (AutoGluon via pandas)
    def delete(self, key: str) -> None                     # GC / purge only
    def list(self, prefix: str) -> Iterator[tuple[str, int, datetime]]   # GC orphan sweep, verify-all
```

- **LocalBlobStore**: `put_file` = `os.link`/`rename` from `tmp/` into place (same filesystem, atomic), then
  `chmod 0444`. `uri` = the path.
- **S3BlobStore** (pyarrow `S3FileSystem` or boto3 for writes; DuckDB `httpfs` with a `CREATE SECRET` for reads):
  `put_file` = `PutObject` with `If-None-Match: *` (S3 conditional writes) and `x-amz-checksum-sha256` (S3 rejects the
  upload if the bytes differ from the hash we computed); multipart for > 100 MB with per-part checksums. Object Lock /
  versioning on the bucket are optional extra protection, not required.
- **Local read cache** for S3 (`data/cache/blobs/<sha>`, LRU by size, default 50 GB): training, Data Doctor and
  anything pandas-based read through it; DuckDB queries read S3 directly (range requests, row-group pruning).
  Since keys are hashes, the cache never needs invalidation.
- **DuckDB sessions**: `lake.duck(job)` returns an in-memory connection with `threads`, `memory_limit`
  (default min(4 GB, 25 % RAM)), `temp_directory=data/tmp/duck/<job>`, and the S3 secret when needed. The httpfs
  extension is **bundled in the deployment image** (`INSTALL httpfs` at build time, `SET autoinstall_known_extensions=false`)
  so production never downloads extensions.

### 5.2 The DB row is the commit point

```
1. writer: COPY → tmp/<uuid>/n.parquet ; sha256 streaming ; INSERT blobs(sha, key, kind, bytes, state='pending')  ON CONFLICT DO NOTHING
2. writer: store.put_file(tmp, sha)  (no-op if present: dedupe across datasets for free)
3. writer: manifest → canonical bytes → sha → blob (same as 1–2)
4. ONE transaction:
     UPDATE dataset_roots SET next_version_no = next_version_no + 1 … RETURNING (or SELECT … then UPDATE; SQLite: BEGIN IMMEDIATE)
     INSERT datasets (version row, manifest_sha256, data_sha256, …)
     INSERT version_tables, table_files, table_keys, table_relations, splits
     INSERT blob_refs (version → every part, the manifest, sources; split → its blob)
     UPDATE blobs SET state='live' WHERE sha IN (…)
     UPDATE dataset_root_tables SET next_row_id = …   (monotonic: MAX(existing, new))
     INSERT lineage_edges (derived_from / merged_from / joined_from), audit_log
     dedupe check inside the transaction: if a committed version of this root has the same data_sha256 and op ∈ DEDUPE_OPS → ROLLBACK, return it
5. after commit: rm -r tmp/<uuid>; set the default pointer per today's settle_default()
```

A crash before 4 leaves `pending` blobs (and maybe files) with no refs: GC removes them after the grace period. A
crash after 4 leaves only tmp files. Nothing reads a blob except through a committed version row, so readers never
see partial versions. Concurrent writers on the same root serialise only on step 4 (milliseconds), not on the writes.

### 5.3 Garbage collection

`POST /api/admin/lake/gc` (and a daily job), two phases, both idempotent:

1. **DB-driven**: `SELECT b.sha256 FROM blobs b LEFT JOIN blob_refs r ON r.blob_sha256 = b.sha256 WHERE r.blob_sha256
   IS NULL AND b.state IN ('pending','live') AND b.created_at < now - 24h` → mark `purging` (one transaction),
   delete the object, mark `purged` (row kept: it records that the bytes existed, when and why they went).
2. **Store-driven orphan sweep** (weekly): list `blobs/` and `tmp/`; any object whose sha has no `blobs` row and is
   older than 24 h is deleted (a writer that died between 2 and 1's insert; or files copied in from outside, which is
   reported as an integrity finding, not silently removed, when its name is not a valid sha of its bytes).

What holds refs: committed versions (any state except `deleted`), splits of those versions, view caches pinned by a
live run, legacy CSVs pinned by a run that hashed them (section 6), frozen holdout samples (owner = the deleted
version, role `frozen_holdout`), `upload_session` refs for previews awaiting commit (expire after 24 h).

### 5.4 Archive, delete (tombstone) and purge with shared files

- **Archive**: DB state only; nothing in the lake changes.
- **Delete** (four eyes, today's `decide_delete`): the version row stays with `state='deleted'`; its `blob_refs`
  rows for table parts, splits and sources are **removed**, the manifest ref and the `frozen_holdout` ref (if asked)
  are kept. The tombstone is the version row + the manifest blob (which holds names, schemas, row counts and hashes:
  no row data). GC then deletes each part **only if no other version references it**: a table carried unchanged into
  v8 keeps its bytes when v7 is deleted, which is correct (v8 still has that data).
- **Purge** (new action `dataset_requests.action='purge'`, four eyes, for "these rows must be erased", e.g. PII or a
  legal request): the request names versions *or* a table sha. Because files are shared, the decision shows **every
  version, view cache, split and frozen sample that references the same blobs**, across datasets and teams; the purge
  either covers all of them (each becomes `deleted`, audited) or is refused. On approval the blobs move to `purging`
  immediately (not after the GC grace period), the objects are deleted (and, on S3 with versioning, every object
  version), and `blobs.purged_by/purge_reason` are set. Run rows, lineage edges, manifests and the audit log stay.
  A purge that must also remove the manifest (column names are themselves sensitive) replaces the manifest blob's
  content with a redacted manifest whose `data_sha256` is kept; `verify` then reports "redacted" instead of a mismatch.
- **Retention** (`retired_data_retention_days`, DL2) works unchanged on version rows; the lake follows the refs.

---

## 6. Migrating existing CSV versions to Parquet

Goal: after migration every existing version, run, split, compare and integrity check gives the same answer as
before; only new versions get the new typing.

### 6.1 Per-version algorithm (`backend/app/lake/migrate.py`, idempotent, resumable, root by root, v1 first)

1. **Verify first**: `file_sha256(data.csv) == datasets.sha256`. On a mismatch the version is **not** migrated; it is
   reported (`verified_ok=0`, audit `dataset_migration_skipped`) and stays `storage='csv'` until someone resolves it
   (today's DL3 integrity path). The migration never launders an outside change.
2. **Read exactly as consumers do**: `df = pd.read_csv(BytesIO(raw))` (the worker's `load_frames` call). The
   Parquet table is `pa.Table.from_pandas(df, preserve_index=False)`: the **pandas-inferred dtypes are kept**
   (zip stays int64 if it was int64 before; leading zeros were already lost when data.csv was written). Retyping
   is a later, explicit version (`op='retype'`), never part of the migration.
3. **`__row_id`** = `lineage.json.root_rows` when it has one entry per row (paired versions); `0..n-1` for roots;
   for versions with no mapping (refresh, monitoring window: today's `inferred` edges) fresh ids from
   `next_row_id`, and the manifest says `legacy.root_rows_source = "none"` so compares keep refusing to pair them.
   `dataset_root_tables.next_row_id = max(__row_id) + 1` over the root's versions (legacy negative ids stay negative).
4. **Row order is kept** (migrated tables are written in data.csv order, `row_id.sorted = false` in the manifest).
   Old runs' predictions, holdout lists and `set_cells._row` refer to positions; position p is `file_row_number` p,
   so they keep meaning the same rows. New versions written later are sorted.
5. **Round-trip proof**: read the Parquet back (`to_pandas()`), require `frame.equals(df)` and equal dtypes, and
   test `to_csv(index=False)` of the read-back frame against the original bytes. Measured on 1M rows of mixed types
   (ints, floats, nullable ints, strings with NA, bools, date strings): frame equal, dtypes equal, **CSV bytes
   reproduced exactly** (the original data.csv was itself written by `to_csv`). Record `legacy.csv_reproducible`.
6. **Hashes**: `legacy_sha256 = datasets.sha256` (the CSV hash); the new `data_sha256`/`manifest_sha256` are set;
   `datasets.sha256` switches to `data_sha256` (with `storage='parquet'` telling readers which one it is).
7. **Legacy CSV**: data.csv moves into the lake as a blob whose key is its own sha256 (= `legacy_sha256`), kind
   `legacy_csv`. It is referenced by every run whose `data_sha256` equals it (`blob_refs(owner_kind='run',
   role='legacy_csv')`), so an old run can still be re-verified byte-for-byte. When `csv_reproducible` is true and the
   runs are retired past the retention period, the ref is dropped and GC frees it (the bytes can be regenerated and
   re-hashed from Parquet on demand); when false, it is kept as long as any such run exists.
8. **Splits**: each `splits/<key>.json` and the legacy `splits.json` → `(__row_id, fold)` Parquet via the position
   → `__row_id` map; `splits.legacy_sha256 = splits.split_sha256(json)` (DL4's identity) and the new typed
   `split_sha256`. Runs that recorded a split hash keep matching the legacy one. The JSON files go into the lake as
   `source` blobs (small) referenced by the split.
9. **lineage.json** → the manifest `provenance` (`op`, `changes` capped, `checks`, `reason`, `by`, `parent`) and,
   complete, a `source` blob (role `legacy_lineage`). `TOMBSTONE.json` of deleted versions → the version row is
   already `deleted`; a manifest is written from the tombstone (no files), `holdout_sample.csv` → a `frozen_holdout`
   blob.
10. **Commit** in one transaction (section 5.2), audit `dataset_migrated` with both hashes. The folder
    `data/datasets/<id>/` is renamed `<id>.migrated` and deleted one release later.

### 6.2 What keeps working, and how

| Concern | Before | After |
|---|---|---|
| A run's `data_sha256` | sha256 of data.csv bytes | unchanged on the run; re-verify hashes the `legacy_csv` blob (or the regenerated CSV when `csv_reproducible`) |
| New run on a migrated version | `data_sha256_expected = datasets.sha256` (CSV) | `run_inputs.table_sha256` (Parquet parts); worker hashes the parts it reads |
| Paired compare (`compare.root_info`) | `lineage.json.root_rows` by position | `__row_id` by `file_row_number`: identical arrays (checked during migration: `root_rows == __row_id column`) |
| Split identity (DL4) | `split_sha256(json)` | `legacy_sha256` for old runs; typed `split_sha256` for new |
| Dedupe (`sha256` equality within a root) | CSV bytes | `data_sha256` (migrated versions compare among themselves; a CSV-era and a Parquet-era version of the same rows match through `legacy.csv_reproducible` + the logical hash, reported as "same rows", not deduped) |
| `csv_path` consumers (40 files) | `data/datasets/<id>/data.csv` | `store.csv_path(id)` becomes a shim: returns the export-cache CSV materialised from Parquet (byte-identical for migrated versions); consumers move to `load_frame`/`load_table` phase by phase |
| Old run configs pointing at `…/data.csv` | file read | `datasets.resolve_legacy_path()` maps `data/datasets/<id>/data.csv` → the shim |

### 6.3 Cost

Per version: one pandas read (2.5 s at 10M × 30), one Arrow write (0.6 s at 1M), two sha256 passes (CSV 2 s + Parquet
0.7 s at 10M) and the round-trip read (3 s at 10M). The POC's data directory migrates in minutes; a deployment with
thousands of versions runs it as a background job with progress in `catalog_meta` and an API to watch it.

---

## 7. Phased build plan (sessions of ~2 h; each ends with manual API calls chained as the UI calls them, plus `npm run build`)

Every phase keeps the app working: CSV versions and Parquet versions coexist (`datasets.storage`) until phase 4
finishes the migration.

| Phase | Scope | Files | Manual verification (curl, in UI order) |
|---|---|---|---|
| **L0 Schema + lake core** | DDL of section 1.1 in `catalog/schema.py migrate()` (idempotent ALTERs, CHECK rebuild of `lineage_edges`); `teams` with `tm_default`, backfill `projects.team_id`, `datasets.version_no/team_id/n_cols/legacy_sha256`, `dataset_roots` from `dataset_cards`; new package `backend/app/lake/` (`store.py` LocalBlobStore, `manifest.py` canonical JSON + hashes, `duck.py` session factory, `writer.py` VersionWriter skeleton). No behaviour change. | `catalog/schema.py`, `db.py`, `lake/*` (new), `catalog/dsets.py` (read `dataset_roots` with fallback to `dataset_cards`) | `GET /api/datasets` (unchanged list); `GET /api/datasets/{id}/history` (v numbers = `version_no`); `GET /api/projects` (team shown); restart twice (migrate idempotent) |
| **L1 Parquet for new uploads (single table)** | `create_from_upload` → typed import (2.6) → `table 'data'` + manifest + commit (5.2); `load_frame()` reads Parquet for `storage='parquet'`; `csv_path()` shim materialises the export cache; worker: `load_frames` reads Parquet via `run_inputs`, hashes parts, `data_gate` compares `table_sha256`; splits stay JSON keyed by position for now (position = `file_row_number`). | `datasets.py`, `engine/api.py` (`queue_run`), `engine/worker.py`, `catalog/store.py` (`record_root_dataset` takes a manifest), `catalog/datasets_api.py` (`GET …/manifest`) | upload a CSV with a leading-zero zip → `GET /api/datasets/{id}` shows zip as text; `GET …/manifest` (hashes, schema); `POST /api/runs` on it → events show `data_hashed` with `match: true`; corrupt a byte of the blob → next run refused (409 path of DL3); `GET /api/datasets/{id}/export?format=csv` |
| **L2 Versions in DuckDB** | `upload_version` (3.1 key + hash match in DuckDB, preview writes tmp only), `edit_version` (3.3 SQL compiler for the 10 ops), `derive()` new contract (3.4) with a pandas adapter so Doctor/Feature Lab/Autopilot callers are unchanged; dedupe by `data_sha256`; splits carried by `__row_id` join (Parquet split files, 2.4). | `datasets.py`, `dataset_versions.py`, `dataset_edit.py`, `splits.py`, `catalog/dsets.py` (`_pairs`, `diff` → 3.5) | upload v2 with `preview=true` → counts; commit → `GET …/history`; `POST …/edit` set_cells + drop_column preview then commit; `GET /api/datasets/diff?a=v1&b=v3`; run on v1 and v3 → `GET /api/compare?a=…&b=…` is paired |
| **L3 Migration** | `lake/migrate.py` (6.1) + `POST /api/admin/lake/migrate` (dry-run + run, progress), `resolve_legacy_path` shim, `compare.root_info` reads `__row_id`; verify endpoints check Parquet + legacy CSV. | `lake/migrate.py` (new), `catalog/compare.py`, `catalog/dsets.py` (`verify`, `verify_all`, `export_manifest`), `datasets.py` | before: record `GET /api/compare` for two old runs, `GET …/verify` for 3 versions; `POST /api/admin/lake/migrate?dry_run=1` → plan; run → `GET …/verify` (both hashes ok), the same compare JSON (byte-equal deltas and CIs), re-verify an old run's data hash; train on a migrated version |
| **L4 Consumers off `csv_path`** | move the 40 files that read `csv_path`/`load_frame` (appendix A) to `load_table(version, table)` / DuckDB queries; registry build/scoring hash via `run_inputs`; delete the shim's write path for new versions. | appendix A list (registry/*, engine/*, anomaly, forecast, survival, el, segmented, reject, multilabel, vela, autopilot, gym, sqlsource) | each kind: start a run from the UI's API call (`POST /api/runs` with its kind), `GET /api/runs/{id}` succeeded; registry `POST /api/registry/build` → card shows data hash; batch score |
| **L5 Multi-table + Excel** | multi-file + plan upload (4.2), calamine sheets (4.1), `version_tables` UI listing, per-table preview/diff/profile, `primary_table`, carry/append/rewrite per table; keys & relations (4.3). | `datasets.py` (`read_upload` → `read_uploads`), `catalog/datasets_api.py`, `profiling.py` (per table), `dataset_versions.py` | upload `book.xlsx` with 3 sheets → 3 tables; upload one CSV as `payments` append → `GET …/manifest` shows 2 carried + 1 appended, 0 bytes for carried; `PUT …/tables/bureau/key`; `GET …/relations` with orphan counts |
| **L6 Deltas** | delta upload (3.2) with `__op` or upsert/delete files, insert-only → new part, compaction rule. | `datasets.py`, `lake/writer.py` | `POST …/versions` with a delta `preview=true` → upserted/inserted/deleted/missing; commit; diff v(n-1)→v(n) equals the delta |
| **L7 Saved joins** | `views`, `view_versions`, spec validation (grain, n:1, aggregate), lazy materialise + cache, splits from the grain, runs on views (`run_inputs` join rows, `joined_from` edges), usage/blockers follow views. | new `catalog/views.py`, `views_api.py`; `engine/api.py`; `catalog/dsets.py` (`usage`, `blockers`); `catalog/lineage.py` | `POST /api/views` (spec) → validation error for a 1:n join without aggregation; fix → `POST /api/views/{id}/preview`; `POST /api/runs` on the view; `GET /api/lineage/tree?kind=run&id=…` shows both tables; request delete of the bureau version → blocked by the run |
| **L8 Teams and permissions** | `team_members` API, `can()` checks on every dataset/view/run endpoint, `acl_would_deny` audit, `ENSEMBLE_ENFORCE_TEAMS` flag, team filters on lists; dataset move across projects/teams. | `catalog/governance.py` (`can`), every `*_api.py` router touching datasets/runs/views, `catalog/store.py` | add member as viewer; with the flag off: viewer uploads → succeeds + `acl_would_deny` in `GET /api/audit`; flag on → 403 with the role needed; list endpoints show only the team's datasets |
| **L9 S3 + GC + purge** | `S3BlobStore`, local read cache, httpfs bundled, `lake/gc.py`, `dataset_requests.action='purge'` with the shared-blob impact list. | `lake/store.py`, `lake/gc.py` (new), `catalog/dsets.py` (`request_delete`, `decide_delete`), `config.py` | with MinIO locally: upload, train, `GET …/manifest` (s3 keys); delete a version whose table is carried in the next → GC keeps the shared part; `POST /api/admin/lake/gc?dry_run=1`; purge request lists every referencing version |
| **L10 Postgres** | the DDL as an Alembic-free migration list run against Postgres (types: jsonb, timestamptz optional); `Database` gets a PG connection pool; SQL that uses `json_extract`, `INSERT OR IGNORE`, `rowid` rewritten (they are listed by `grep`); version-number allocation with `SELECT … FOR UPDATE`. | `db.py`, `catalog/*.py` (portable SQL) | same chain as L2 and L7 against Postgres; two concurrent uploads to the same root → versions 8 and 9, no duplicate |

L1–L3 are the critical path (≈ 3 sessions); L4 is the long tail (≈ 2–3 sessions, mechanical); L5–L9 can be ordered by
user need; L10 when multi-user arrives.

---

## 8. Measured numbers

Synthetic table: 30 columns (bigint id, leading-zero zip, account string, 2 dates, 1 timestamp, 8 ints, 9 two-decimal
floats, 5 five-value categoricals, a 16-char memo, a 0/1 target). v2 = v1 with 1 % of rows deleted, 1 % changed
(2 columns), 1 % new, shuffled. Second table `bureau` (1:1, 5 cols), third `payments` (3 rows per customer).
4 vCPU, 15 GB RAM, local SSD, duckdb 2.0.1, pyarrow 26.0.1, pandas 3.1.1. Times are wall-clock; memory is the
process's peak RSS. Each operation ran in its own process.

| Operation | 100k rows | 1M rows | 10M rows | 10M, DuckDB `memory_limit=1GB` |
|---|---|---|---|---|
| CSV size → Parquet (zstd) size | 20 MB → 7.3 MB | 205 MB → 71 MB | 2.06 GB → 710 MB | — |
| CSV → Parquet, typed 2-pass + `__row_id` (DuckDB) | 0.56 s / 212 MB | 3.8 s / 605 MB | 35.8 s / 721 MB ¹ | — |
| CSV → Parquet, pyarrow streaming (no typing pass) | 0.35 s / 275 MB | 4.4 s / 639 MB | 51.9 s / 2.5 GB (1.13 GB file) | — |
| Row match by key (DuckDB join, writes row map) | 0.04 s / 95 MB | 0.14 s / 177 MB | **0.84 s / 733 MB** | — |
| Row match by row hash, duplicates in order (DuckDB) | 0.08 s / 147 MB | 0.39 s / 454 MB | **3.4 s / 1.3 GB** | 11.0 s / 1.05 GB |
| Today's path: pandas read of 2 CSVs + `_key_pairs` | 0.11 + 0.35 s | 0.5 + 2.6 s | **OOM-killed (> 15 GB)** | — |
| Today's path: `hash_pandas_object` + `_hash_pairs` | 0.46 s / 382 MB | 6.6 s / 2.1 GB | (killed above) | — |
| Diff: per-column change counts | 0.23 s | 1.3 s | **4.1 s** / 4.0 GB | 15.0 s / 1.09 GB |
| Diff: + changed-row count | 0.38 s | 1.7 s | **8.2 s** | 29.7 s |
| Delta apply (0.5 % upserts, 0.5 % inserts, 0.5 % deletes) → new table | 0.31 s | 1.1 s | **12.5 s** / 7.9 GB | 26.4 s / 2.1 GB (2 GB limit) |
| sha256 of the CSV file (8 MB chunks) | 0.03 s | 0.24 s | 2.0 s (≈ 1 GB/s) | — |
| sha256 of the Parquet file | 0.01 s | 0.08 s | **0.73 s** | — |
| Logical table hash in DuckDB (sum of row hashes) | 0.06 s | 0.17 s | 1.1 s / 509 MB | — |
| Join: grain + 1:1 table + aggregated 1:n child (3N rows) → Parquet | 0.40 s | 1.3 s | **9.9 s** / 5.2 GB | 36.2 s / 1.17 GB |
| Parquet → pandas (training load) | 0.08 s | 0.21 s | 3.0 s (3.3 GB frame) | — |
| CSV → pandas (today's load; zip becomes int64) | 0.04 s | 0.21 s | 2.5 s / 6.8 GB | — |

¹ The `row_number() OVER ()` that numbers rows serialises the write: CSV → Parquet without it takes 12.9 s, and
adding `__row_id` afterwards from `file_row_number` takes 8.4 s (21 s total). L1 uses the two-step form (or numbers
rows per input chunk), which also gives the round-trip check for free.

What the numbers say:

- Every key operation at 10M × 30 is seconds, and DuckDB stays within a 1 GB limit by spilling (2–4× slower). The
  per-job memory limit is therefore a safe default and many jobs can run side by side.
- Today's pandas/Python matching does not finish at 10M rows on 15 GB. That alone justifies L2.
- Storage per version falls to ~35 % of the CSV, before any table sharing. Carried tables cost nothing.
- Training reads are as fast as today, with correct types.
- Not measured: S3 throughput (no bucket reachable here; DuckDB httpfs range reads are typically 100–500 MB/s per
  job; the read cache keeps repeated training reads local) and Excel (calamine).

---

## 9. Scale, and when to escalate

| Dimension | Design capacity | Bottleneck and answer |
|---|---|---|
| Teams × projects × datasets × versions | 10⁶ version rows, 10⁷ `table_files` rows | indexed lookups only (root, project, team, sha); Postgres for > 1 writer |
| Runs | 10⁵ runs, 10⁶ `run_inputs` | indexed by run and by (version, table) |
| Table size | 10⁸ rows per table | parts of ≤ 8M rows; DuckDB streams; rewrite of 10⁸ rows ≈ 5–10 min, acceptable for a human-paced version |
| Storage growth | versions × changed tables | carried tables are free; GC follows refs; quotas per team (`teams.storage_quota_bytes`) |
| Concurrent heavy jobs | `cores / 4` DuckDB jobs × `memory_limit` | the job queue; the DB commit is milliseconds |
| SQLite | one writer | fine for the POC (commits are short); Postgres at L10 |

**Escalate to Iceberg (or Delta) when** both hold: tables routinely above ~100M rows **and** frequent small
row-level changes (daily deltas touching < 1 %), so that full-table rewrites cost more than an engineer-month of
integration per year in compute and storage. Then the manifest's `files` list becomes an Iceberg snapshot (DuckDB
reads Iceberg), and the rest of this design (version rows, `__row_id`, splits, views, refs) stays. lakeFS adds
nothing here: content addressing plus the DB commit already give atomic multi-table versions.

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Parquet bytes differ across writer versions, so the same rows get a new table sha after a DuckDB upgrade | dedupe misses (a second copy); never wrong | logical hash shown as "same content"; pin DuckDB version per release; the writer version is in the manifest |
| Shared blobs make purge wider than people expect | a PII purge must touch versions in other datasets/teams | the purge decision lists every referencing version and is all-or-nothing; audit |
| `__row_id` continuity broken by a path that rebuilds a frame without it | unpaired compares, carried splits lost | `derive()` refuses a rewritten table with no `__row_id` unless `op` is declared "new rows" (then all rows new and the edge `inferred`), as `paired=False` today |
| Migration finds data.csv files changed outside the app | those versions cannot be migrated | reported, not migrated; resolved through the DL3 integrity flow |
| Typing rules surprise users (a column they thought numeric is text) | features change between CSV-era and Parquet-era versions | preview shows each decided type with the reason; per-table contract overrides; retyping is explicit |
| DuckDB extension downloads blocked in production | S3 reads fail | bundle httpfs in the image; `autoinstall_known_extensions=false`; startup check |
| SQLite write contention with many teams | commit latency | commits are tiny; Postgres at L10 |
| Views hide data leakage (future rows aggregated) | optimistic models | point-in-time windows in the spec; Data Doctor checks aggregates against the grain's date |
| 40 consumer files still on `csv_path` during L1–L4 | CSV shim cost and two read paths | shim caches by table sha (materialised once); L4 removes it |
| `hash()` collisions in row-hash matching | two different rows paired | the changed-row check reports them as changed; probability ~3·10⁻⁶ at 10M rows |
| Local disk full during big rewrites (tmp + spill) | failed version | the writer checks free space ≥ 3× the table's size before starting and fails with a message |

---

## Appendix A: files that read `csv_path` / `load_frame` (L4 worklist, counts from `grep -c`)

`datasets.py` 23 · `registry/scoring.py` 11 · `registry/build.py` 10 · `anomaly/api.py` 8 · `engine/shift_api.py` 7 ·
`engine/api.py` 7 · `autopilot/engine.py` 7 · `registry/api.py` 6 · `forecast/core.py` 5 · `registry/monitoring_api.py` 4 ·
`gym/export.py` 4 · `engine/worker.py` 4 · `engine/explain.py` 4 · `data_doctor_api.py` 4 · `catalog/store.py` 4 ·
`catalog/dsets.py` 4 · `catalog/datasets_api.py` 4 · `autopilot/audit.py` 4 · `vela/experiments.py` 3 · `survival/api.py` 3 ·
`segmented/api.py` 3 · `reject/api.py` 3 · `multilabel/api.py` 3 · `main.py` 3 · `forecast/api.py` 3 · `engine/slices_api.py` 3 ·
`engine/heavy.py` 3 · `engine/active_learning_api.py` 3 · `el/api.py` 3 · `anomaly/core.py` 3 · `segmented/core.py` 2 ·
`registry/changes_api.py` 2 · `engine/serving.py` 2 · `engine/annotations_api.py` 2 · `engine/annotations.py` 2 ·
`vela/missing.py` 1 · `vela/findings.py` 1 · `vela/doctor.py` 1 · `vela/api.py` 1 · `sqlsource/core.py` 1.

Other modules changed by the plan: `db.py`, `catalog/schema.py` (DDL, backfill), `catalog/store.py`
(`record_root_dataset`/`record_version` take a manifest), `catalog/dsets.py` (history, diff, verify, delete/purge,
usage through views), `catalog/compare.py` (`root_info` from `__row_id`), `catalog/lineage.py` (view nodes),
`catalog/governance.py` (`can`), `dataset_versions.py` (DuckDB matching), `dataset_edit.py` (SQL compiler),
`splits.py` (Parquet splits keyed by `__row_id`, typed split hash), `profiling.py` (per table, DuckDB aggregates for
big tables), `engine/worker.py` (`load_frames`, `data_gate`), new package `lake/` (`store.py`, `manifest.py`, `duck.py`,
`writer.py`, `migrate.py`, `gc.py`), new `catalog/views.py`.

## Appendix B: benchmark reproduction

Scratch venv: `python -m venv v && v/bin/pip install duckdb pyarrow pandas`. The script generates the tables with
DuckDB (`range(N)` + `hash(i, k)` per column), writes `v1.csv` / `v2.csv`, then runs each operation in its own
process (`bench.py <op> <N>`, ops: `gen csv2pq csv2pq_arrow match_key match_hash diff hash join to_pandas csv_pandas
delta legacy_match`; `MEMLIMIT=1GB` sets DuckDB's `memory_limit` and a spill directory), printing one JSON line
with seconds and `ru_maxrss`. The SQL of each operation is the SQL shown in sections 3.1, 3.2, 3.5, 3.6 and 4.4.
The migration round-trip test (6.1 step 5) wrote a 1M-row mixed-type frame with `to_csv`, read it back with
`read_csv`, converted it to Parquet and back, and compared frames, dtypes and re-written CSV bytes (all equal).
