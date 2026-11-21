# ENSEMBLE data platform: the final plan (Parquet + DuckDB, team → project → dataset → version → tables)

Synthesis session PLAN-S, 2026-11-21, motion-components @ a2338a4. Sources: `data-platform-brief.md` (fixed
decisions), `A-data-model-storage.md`, `B-scale-performance.md`, `C-teams-governance-flows.md`, `progress/DLA4.md`,
`platform-eval.md`, and the code at that head. Where this plan differs from A, B or C, §1 gives the reason. **No product code was changed.**

One scratch benchmark was run to settle a disagreement (§1, R12): DuckDB 2.0.1, **pandas 2.3.3 and pyarrow 24.0.0, the
versions pinned in `backend/requirements*.txt`**, 4 vCPU, 5M × 27-column table. A and B benchmarked pandas 3.1 and
pyarrow 26, which the app cannot use: AutoGluon 1.6 needs pandas < 2.4.

---

## 0. Executive summary (one page)

**The architecture in plain words.**
- Every upload is converted once, streaming, into **typed Parquet files named by their own sha256** (content-addressed,
  write-once).
- A **dataset version** is a small immutable **manifest** listing its tables, and each table lists its files. The
  version's **row in the metadata DB is the commit point**. A version that changes one table of five writes one table;
  the other four are carried by reference at zero bytes.
- Every row carries a stable **`__row_id`**, so pairing, splits, diffs and holdouts are joins on one integer. **Splits
  are small Parquet files**, also content-addressed and cross-checked against every earlier run.
- **DuckDB** runs in-process with hard memory and spill limits for every preview, diff, match, join and profile.
  **pandas** appears only at the AutoGluon hand-off, through **one typed reader (`app/frames.py`)**.
- Metadata is SQLite today and Postgres from the first shared deployment. Teams and permissions are designed in now:
  they run in `audit` mode until sign-in exists, then in `enforce` mode.

```
  people / drop folder            API (FastAPI)                                   workers (same code, N hosts later)
  ─────────────────────   ┌──────────────────────────────────────────────┐     ┌───────────────────────────────┐
  CSV / XLSX / Parquet ──►│ /uploads → stream to tmp, sha256 on the fly  │     │ jobs table (leases)           │
  full extract or delta   │ /check   → DuckDB typed import, match, diff, │────►│ train: frames.read → pandas   │
                          │            contract, PII suggest (no writes) │     │   (budget → hash sample)      │
                          │ /commit  → VersionWriter ──┐                 │     │ profile / diff / join / GC    │
  UI / Vela agent ───────►│ authz.require(action, obj)  │                 │     └──────────────┬────────────────┘
                          └──────────────┬──────────────┼─────────────────┘                    │
                                         │ 1 transaction│ write-once blobs                      │ read by sha
                     ┌───────────────────▼───────┐   ┌──▼─────────────────────────────────────┐ │
                     │ metadata DB (SQLite → PG) │   │ lake: blobs/<aa>/<sha256>.parquet|json │◄┘
                     │ teams, projects, datasets │   │  tables · splits · join caches ·       │
                     │ (=versions), version_tables│  │  manifests · kept sources · legacy CSV │
                     │ table_files, splits, runs,│   │ local disk now → S3/MinIO at trigger   │
                     │ run_inputs, blob_refs,    │   └────────────────────────────────────────┘
                     │ audit (hash-chained/team) │        GC follows blob_refs; purge = 4-eyes
                     └───────────────────────────┘
```

**What changes for users.**
- Types survive: zip codes and account numbers keep their leading zeros, and ints stay ints. Every type in the
  preview shows the reason it was chosen.
- A dataset can have several tables, from several files or the sheets of an Excel workbook. A model trains on **one
  table** or a **saved join** whose grain is one table.
- Uploading the next version **asks the user to confirm how rows are matched** (a named key, or "row content" with a
  visible warning). It shows how many holdout rows would lose their identity before anything is written. DLA4 #2
  becomes impossible to hit silently.
- Delta files (upserts/deletes by key) are accepted.
- Big files upload (5 GB on the POC, streamed). Pages, search and diffs come back in under a second at 10M rows
  instead of 10–60 s. Above the worker's memory budget, a run trains on a recorded, deterministic sample and scores the
  full holdout.
- A team switcher; datasets live in team projects; PII columns are masked in previews and exports by default; there
  are certifications, shares, legal holds and per-team audit. Nothing is enforced until sign-in exists (`audit` mode
  logs would-be denials).
- Nothing existing breaks: every `ds_` id, run, compare, hash and champion keeps working, and old runs re-verify
  byte-for-byte.

**Scale envelope** (B §1, measured where marked in B §2):

| | POC: one host, SQLite, local disk | Tier 2: Postgres, S3/MinIO, API + N workers | Escalation |
|---|---|---|---|
| Teams / datasets / versions | 10 / 2 000 / 20 000 | 200 / 50 000 / 500 000 | — |
| Rows per table | ≤ 10M typical, 50M max | ≤ 100M | > 1B or outside readers → Iceberg |
| Interactive op at 10M rows (page, preview, diff, join, profile one column) | 0.02–2.3 s measured | same per worker | — |
| Full table rewrite | 50M rows in 19.5 s measured | 100M ≈ 40–60 s | — |
| Upload | ≤ 5 GB streamed | ≤ 50 GB (drop folder / multipart) | — |
| Training frame | ≤ 10 % of worker RAM, then a hash sample | same | — |
| Concurrent metadata writers | ≤ ~16 (SQLite p99 230 ms at 16) | Postgres p99 < 25 ms at 32 | — |
| Lake | ≤ 1 TB local | 10–100 TB object store | own delta parts at 20× live size |

**Total effort.**
- **29 build sessions** of about 2 h each, including **5 independent read-only checks**: about **58 h** serial.
- With **3 parallel lanes** the critical path is **15 sessions, about 32–36 h** of wall-clock.
- **4 more sessions** are built only when their trigger fires: S3, storage delta parts, and two for SSO plus the drop
  folder.

---

## 1. Conflicts between A, B and C, and how they are resolved

| # | Conflict | Resolution | Why |
|---|---|---|---|
| R1 | **What `datasets` / `ds_` ids mean.** A keeps the `datasets` table as the **version** table (`ds_` = version). C turns `datasets` into a **frame registry** (`ds_` = a training input) and mints `dv_` ids for versions. | **A.** `datasets` stays one row per version; ids stay `ds_`; the root id is v1's id. C's *frame* survives as a **concept and a module** (`app/frames.py`): a training input is an `InputRef` = `(version_id, table)` or a `join_frame_id`. Only join results get a row (`join_frames`, A's `view_versions`). No frame ids are minted for single tables. | Today `ds_` already means version in runs, edges, champions, URLs, audit, 202 `csv_path`/`load_frame` references and the UI. C's model would need a third id plus a 1:1 frame row per version, and every multi-table version would need N frame rows before anyone trains. A's model keeps every existing reference valid with zero remapping. A run records its exact table(s) in `run_inputs`, which covers what C wanted from frames (the hash of exactly what was read). |
| R2 | **Phase lists.** A: L0–L10; B: B1–B8; C: C1–C14. | **One merged list of 29 sessions (§9)** in 4 lanes plus 5 checks. Mapping in §9.0. | B and C both assumed A's L1 came first. Their phases overlap: A-L2 = B3, A-L8 ≈ C2, A-L9 ≈ C11 + B7, A-L5 ≈ C5. |
| R3 | **Full-rewrite cost.** A estimates 5–10 min for 100M rows; B measured 50M in 19.5 s (≈ 40–60 s at 100M). | **B.** Use the measured numbers. The cost of full copy-on-write is **storage, not compute**; the trigger for delta storage (R7) is therefore a storage ratio. | A's figure was extrapolated; B measured it. |
| R4 | **Where splits and row ids live.** A: `__row_id` column in every file, splits as Parquet `(__row_id, fold)` per `(version, table, split_key)`. C: splits per frame (`frames/<ds>/splits/<key>.json`), positions, the frame inherits the spine's. DL4 today: `split_files` table + `datasets.splits_sha256` (mutable). | **A, plus DLA4's fix.** `__row_id BIGINT` is the first column of every table file. Splits are content-addressed Parquet blobs in an **insert-only** `splits` table. A join frame's split is **the grain table's split filtered to its rows** (C's rule, A's mechanism). `split_files` and `datasets.splits_sha256` are retired after migration. The protecting hashes live **outside any mutable row**: the blob key itself, the hash-chained audit row `split_created`, and every run that used the split (§5.7). | Positions break as soon as a version is sorted, filtered or joined; `__row_id` is position-free. JSON splits are 89 MB at 10M rows (B 2.5). A mutable hash row is exactly the DLA4 #3 hole. |
| R5 | **Paging.** C pages with `LIMIT/OFFSET`; B measured Top-N `ORDER BY … LIMIT` pages going out of memory at 32 users and filling 20 GB of spill. | **B.** Pages are **keyset on `__row_id`** (`WHERE __row_id > :after [AND filter] LIMIT n`) for sorted tables. A jump to a position uses unsorted `LIMIT/OFFSET` (0.04 s at 10M, B 2.1); migrated unsorted tables use `file_row_number`. A UI sort by another column is **refused above 1M rows** with "filter first"; below that it is allowed. Search returns the first n hits, and the count is a separate cancellable call with a 3 s budget. | Measured. |
| R6 | **Diff.** A: one pass, full join, per-column counts (4 GB at 10M). B: two passes (hash pass, then a cell compare for changed keys only) plus `diff_cache`. C: per-table diff skipped when the sha matches. | **B + C.** Equal `table_sha256` → "unchanged", no scan. Otherwise B's two-pass diff on `__row_id` (same root) or on a **confirmed** business key (cross-root), cached by `(a_sha, b_sha, key)`. Without a key, a multiset row-hash diff that reports added/removed only. | Two-pass: 11.8 s / 2.5 GB vs 18.7 s / 10 GB at 50M (B 2.1). |
| R7 | **Delta.** A: insert-only → append part; any other delta → full rewrite. B: delete + upsert delta parts with checkpoints, behind a flag. C: user-facing delta flow, stored as a full table. | **The user flow is C's**; the default storage is **A's** (append part for insert-only, rewrite otherwise). **B's delta-part storage is escalation step 3**, built when a table's **versioned bytes exceed 20× its live size** or a team's quota passes 80 % mostly through re-uploads. It then also encodes **full extracts** as deltas against their parent (diff-by-key already computes the delta). | Storage is the only cost (R3). Most tables are < 1M rows (B §1: 80 %). Merge-on-read adds a read path and checkpoint logic that only the largest 2 % of tables need. |
| R8 | **Escalation order.** A: Iceberg when > 100M rows plus frequent small changes; lakeFS "adds nothing". B: Postgres + queue → S3 → own delta parts → Iceberg (outside readers or ~1B rows) → lakeFS rejected. | **B's order, unchanged** (§8). | B evaluated each step against this scenario: file exports only, no outside writers, a bank. A's Iceberg trigger is covered by B's step 3 at lower cost. |
| R9 | **Teams schema.** A: `team_members(principal TEXT, role ∈ owner/maintainer/editor/viewer)`, flag `ENSEMBLE_ENFORCE_TEAMS`. C: `principals` table, roles viewer/member/maintainer/owner + `risk_reviewer`, modes off/audit/enforce, route-level `require()`, coverage gate. | **C's model.** A's `editor` = C's `member`. A's `acl_would_deny` = C's `authz_would_deny` in mode `audit`. | C has the identity seam needed for SSO (`principals.idp/subject`), the model-risk role and the coverage gate, which replaces a test suite. |
| R10 | **Upload API.** A: multipart + JSON `plan` on `POST /datasets/{id}/versions`. C: staged `POST /uploads` → `/check` → `/commit` (idempotent). | **C's staging, with A's plan as the `PATCH /uploads/{u}` body.** The old endpoints become thin wrappers. | Staging gives an idempotent commit, lets the drop folder reuse the same path, and gives the explicit match confirmation (§5.3) a natural place. |
| R11 | **Saved joins.** A: `views`/`view_versions` pinned to input versions, with a `track:"default"` option. C: a saved join defined **on the dataset root** that applies to any version with the tables; frame cache keyed by `(dv, join_id, spec_sha)`. | **Both, named for users as C names them.** `joins` = a root-scoped, versioned spec (C). `join_frames` = that spec applied to one dataset version, with every input table sha pinned and an optional cache blob (A's `view_versions`). Inputs from other datasets (a shared bureau dataset) are allowed when pinned (A). | Users think "my join"; lineage and serving need "this join on these exact tables". |
| R12 | **Training-frame memory.** B: ≈ 9 B per cell, load peak 1.7×. C: ≈ 70 B per cell. | **Neither number holds; use a per-column estimator.** Measured with the pinned pandas 2.3.3 + pyarrow 24 on 5M × 27 columns (5 of them strings): `pd.read_parquet` 6.7 s, **frame 2.38 GB = 17.6 B/cell, peak RSS 3.67 GB (1.55×)**. With `dtype_backend="pyarrow"`: 8.0 B/cell, but AutoGluon 1.6 support for Arrow dtypes is not verified. Estimator: 8 B per numeric/bool cell, **≈ 58 B + mean length per object-string cell** (dates under compat count as strings), peak ×1.6, budget 10 % of worker RAM. P11 calibrates it against a real AutoGluon fit. | B's figure came from pandas 3 (Arrow strings by default); C's came from a string-heavy frame. |
| R13 | **Sampling hash.** B samples with DuckDB `hash(__row_id, seed)`. | **splitmix64 over `__row_id` in numpy** (well defined, library-independent), then a semi join. Measured 0.19 s for 5M ids. The engine's `hash()` is used only for ephemeral work (row-hash matching, logical "same content" hints), never for anything stored or recomputed. | DuckDB does not promise `hash()` stability across releases; a sample must be reproducible years later. |
| R14 | **Migration dtypes.** A: "`pd.read_csv` as consumers do". C: "the same `pd.read_csv` call the engine used". | **The worker's actual read**: `frame_io.read(raw, frame_io.load(dir))`, which applies `schema.json` when the version has one (DL4) and is plain `pd.read_csv` otherwise (`engine/worker.py:49-52`). | That is what every run's fit saw. The ~60 schema-less readers saw different dtypes; that is the DLA4 #4 bug, not a contract to preserve. |
| R15 | **Excel reader.** A: python-calamine. C: openpyxl. | **python-calamine** (a wheel, typed cells, ~10× faster), pinned in P12. Fallback: openpyxl if the bank's package review refuses it. Neither is in `requirements.lock` today. | — |
| R16 | **DuckDB connections.** A: one in-memory connection per job. B: one shared interactive connection with a semaphore and an interrupt, plus a connection per job. | **B** (`lake/duck.py`). | Measured concurrency, B 2.6. |
| R17 | **`blob_refs` shape.** A: `(blob, owner_kind, owner_id, role)`. C: `(sha, dv, team)`. | **A's, plus a denormalised `team_id`** so the purge impact report (C) is one query. | — |
| R18 | **Purge across teams.** A: all-or-nothing over every reference. C: blocked until each owning team acts. | **C's sign-off with A's atomicity.** The request lists every referencing version, split, join cache and frozen sample. It needs approval from the owner of each team involved (four eyes in the requesting team) and then executes all-or-nothing. A legal hold anywhere in the set blocks it. | — |
| R19 | **Dependencies.** Neither A nor B noticed that `duckdb` is not a dependency and that `pyarrow` is pinned at 24 by the lock. | P01 adds `duckdb==2.0.1` and keeps `pyarrow==24.0.0`. **Every benchmark gate in §9 is re-measured on the pinned stack.** | — |

---

## 2. Final metadata schema

Portability rules (A §1.1):
- ids are app-generated TEXT with prefixes: `tm_ pr_ prj_ ds_ spl_ jn_ jf_ up_ ri_ sh_ ct_ lh_ dc_ ar_ jb_`;
- timestamps are ISO-8601 UTC TEXT;
- JSON is TEXT (`jsonb` on Postgres);
- anything queried is a real column, never a JSON path;
- booleans are INTEGER 0/1 with a CHECK;
- partial indexes and CHECKs are allowed;
- no AUTOINCREMENT in new tables.

Migrations run in `catalog/schema.py migrate()` as idempotent steps recorded in `catalog_meta`.

### 2.1 Portable DDL (SQLite as written; the Postgres deltas are in 2.2)

```sql
-- ═════ identity and tenancy (C §1.1) ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE principals (
  id TEXT PRIMARY KEY,                       -- pr_
  display_name TEXT NOT NULL,
  identity_key TEXT NOT NULL UNIQUE,         -- governance.identity_key(display_name): the four-eyes key
  email TEXT, idp TEXT, subject TEXT,        -- idp NULL = typed name
  kind TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','service','system')),
  is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
  created_at TEXT NOT NULL, last_seen_at TEXT, disabled_at TEXT);
CREATE UNIQUE INDEX ux_principal_subject ON principals (idp, subject) WHERE subject IS NOT NULL;

CREATE TABLE teams (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived')),
  policy TEXT NOT NULL DEFAULT '{}',          -- retention, four-eyes options, NA tokens, PII defaults (§6.4)
  storage_quota_bytes BIGINT, store_root TEXT, -- NULL = deployment default (per-team bucket only if isolation is required)
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE team_members (
  team_id TEXT NOT NULL REFERENCES teams(id), principal_id TEXT NOT NULL REFERENCES principals(id),
  role TEXT NOT NULL CHECK (role IN ('viewer','member','maintainer','owner','risk_reviewer')),
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','idp_group')),
  added_by TEXT NOT NULL, added_at TEXT NOT NULL,
  PRIMARY KEY (team_id, principal_id));
CREATE INDEX ix_members_principal ON team_members (principal_id);

CREATE TABLE idp_group_roles (idp TEXT NOT NULL, group_name TEXT NOT NULL, team_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (idp, group_name, team_id));                                   -- used from T3 (SSO)

ALTER TABLE projects ADD COLUMN team_id TEXT NOT NULL DEFAULT 'tm_default';
ALTER TABLE projects ADD COLUMN state TEXT NOT NULL DEFAULT 'active';
CREATE INDEX ix_projects_team ON projects (team_id);

CREATE TABLE shares (
  id TEXT PRIMARY KEY, object_kind TEXT NOT NULL CHECK (object_kind IN ('dataset','project')), object_id TEXT NOT NULL,
  owner_team TEXT NOT NULL, grantee_team TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'certified' CHECK (scope IN ('certified','default','pinned','all')), pinned_version TEXT,
  pii TEXT NOT NULL DEFAULT 'masked' CHECK (pii IN ('masked','unmasked')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','revoked','expired')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, expires_at TEXT, revoked_by TEXT, revoked_at TEXT);
CREATE INDEX ix_shares_object ON shares (object_kind, object_id, status);
CREATE INDEX ix_shares_grantee ON shares (grantee_team, status);

CREATE TABLE approval_requests (            -- certify, share, legal-hold release, cross-team purge, unmasked export (policy)
  id TEXT PRIMARY KEY, action TEXT NOT NULL, object_kind TEXT NOT NULL, object_id TEXT NOT NULL, team_id TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','used','expired')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '');
CREATE INDEX ix_approvals_team ON approval_requests (team_id, status);

-- ═════ the logical dataset (A §1.1; absorbs dataset_cards, which stays a VIEW for one release) ═══════════════════
CREATE TABLE dataset_roots (
  id TEXT PRIMARY KEY,                        -- = id of v1 (today's root_id)
  team_id TEXT NOT NULL REFERENCES teams(id), project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
  label_definition TEXT NOT NULL DEFAULT '', known_issues TEXT NOT NULL DEFAULT '',
  default_version_id TEXT, next_version_no INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived','deleted')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE INDEX ix_roots_project ON dataset_roots (project_id, state);
CREATE INDEX ix_roots_team ON dataset_roots (team_id, state);
CREATE UNIQUE INDEX ux_roots_name ON dataset_roots (project_id, name) WHERE state <> 'deleted';

CREATE TABLE dataset_root_tables (           -- row-id high-water mark per (root, table); never decreases
  root_id TEXT NOT NULL REFERENCES dataset_roots(id), table_name TEXT NOT NULL, next_row_id BIGINT NOT NULL,
  PRIMARY KEY (root_id, table_name));

-- ═════ versions = the existing `datasets` table (columns added, none removed) ══════════════════════════════════════
ALTER TABLE datasets ADD COLUMN team_id TEXT;
ALTER TABLE datasets ADD COLUMN version_no INTEGER;
ALTER TABLE datasets ADD COLUMN storage TEXT NOT NULL DEFAULT 'csv' CHECK (storage IN ('csv','parquet'));
ALTER TABLE datasets ADD COLUMN manifest_sha256 TEXT;   -- commit hash; NULL while storage='csv'
ALTER TABLE datasets ADD COLUMN data_sha256 TEXT;       -- dedupe identity
ALTER TABLE datasets ADD COLUMN legacy_sha256 TEXT;     -- the data.csv sha256 (= old `sha256`), kept forever
ALTER TABLE datasets ADD COLUMN n_tables INTEGER NOT NULL DEFAULT 1;
ALTER TABLE datasets ADD COLUMN n_cols INTEGER;          -- replaces json_extract(profile,'$.n_cols')
ALTER TABLE datasets ADD COLUMN primary_table TEXT;      -- 'data' for migrated versions
ALTER TABLE datasets ADD COLUMN reason TEXT NOT NULL DEFAULT '';
ALTER TABLE datasets ADD COLUMN job_id TEXT;             -- the job that committed it (idempotent re-commit, B §9)
CREATE UNIQUE INDEX ux_versions_no ON datasets (root_id, version_no);
CREATE INDEX ix_versions_data_sha ON datasets (root_id, data_sha256);
CREATE INDEX ix_versions_project ON datasets (project_id, created_at);
CREATE INDEX ix_versions_team ON datasets (team_id, created_at);
CREATE INDEX ix_versions_parent ON datasets (parent_id);
-- `sha256` keeps a meaning for old readers: = data_sha256 when storage='parquet', = legacy_sha256 when 'csv'.
-- `splits_sha256` (DL4) is no longer written after P09; kept for old rows.

CREATE TABLE version_tables (
  version_id TEXT NOT NULL REFERENCES datasets(id), table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
  table_sha256 TEXT NOT NULL, n_rows BIGINT NOT NULL, n_cols INTEGER NOT NULL, bytes BIGINT NOT NULL,
  schema_json TEXT NOT NULL, schema_sha256 TEXT NOT NULL,
  row_id_min BIGINT, row_id_max BIGINT, row_id_sorted INTEGER NOT NULL DEFAULT 1 CHECK (row_id_sorted IN (0,1)),
  change TEXT NOT NULL CHECK (change IN ('new','carried','appended','rewritten')),
  from_version TEXT, from_table TEXT, source_file TEXT,
  PRIMARY KEY (version_id, table_name), UNIQUE (version_id, ordinal));
CREATE INDEX ix_vt_sha ON version_tables (table_sha256);

CREATE TABLE table_files (
  version_id TEXT NOT NULL, table_name TEXT NOT NULL, part INTEGER NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  role TEXT NOT NULL DEFAULT 'base' CHECK (role IN ('base','append','upsert','delete')),  -- upsert/delete: escalation T2 only
  n_rows BIGINT NOT NULL, row_id_min BIGINT, row_id_max BIGINT,
  PRIMARY KEY (version_id, table_name, part),
  FOREIGN KEY (version_id, table_name) REFERENCES version_tables(version_id, table_name));
CREATE INDEX ix_tf_blob ON table_files (blob_sha256);

CREATE TABLE table_keys (
  version_id TEXT NOT NULL, table_name TEXT NOT NULL, columns_json TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('primary','unique')),
  declared_by TEXT NOT NULL,                 -- a principal id; never 'guessed' (§5.3: guesses are not stored as keys)
  valid INTEGER NOT NULL CHECK (valid IN (0,1)), n_duplicates BIGINT NOT NULL DEFAULT 0, n_nulls BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (version_id, table_name, kind, columns_json));

CREATE TABLE table_relations (
  version_id TEXT NOT NULL, name TEXT NOT NULL,
  from_table TEXT NOT NULL, from_cols_json TEXT NOT NULL, to_table TEXT NOT NULL, to_cols_json TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('n:1','1:1')), declared_by TEXT NOT NULL,
  n_orphans BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (version_id, name));

-- ═════ content-addressed files ══════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE blobs (
  sha256 TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('table','split','join_cache','manifest','source','legacy_csv','legacy_json',
                                     'frozen_holdout','sample','audit_archive')),
  bytes BIGINT NOT NULL, n_rows BIGINT,
  state TEXT NOT NULL CHECK (state IN ('pending','live','purging','purged')),
  created_at TEXT NOT NULL, verified_at TEXT, verified_ok INTEGER,
  purged_at TEXT, purged_by TEXT, purge_reason TEXT);
CREATE INDEX ix_blobs_state ON blobs (state, created_at);

CREATE TABLE blob_refs (
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  owner_kind TEXT NOT NULL CHECK (owner_kind IN ('version','split','join_frame','run','upload','export')),
  owner_id TEXT NOT NULL, role TEXT NOT NULL,  -- 'table:bureau#0', 'manifest', 'source', 'legacy_csv', 'split', 'sample'
  team_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (blob_sha256, owner_kind, owner_id, role));
CREATE INDEX ix_blob_refs_owner ON blob_refs (owner_kind, owner_id);
CREATE INDEX ix_blob_refs_team ON blob_refs (team_id);

-- ═════ splits (insert-only; §5.7) ═══════════════════════════════════════════════════════════════════════════════════
CREATE TABLE splits (
  id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES datasets(id), table_name TEXT NOT NULL,
  split_key TEXT NOT NULL, target TEXT, strategy TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
  seed INTEGER NOT NULL, holdout_share TEXT NOT NULL, n_folds INTEGER NOT NULL,  -- share as decimal text: no floats in hashed data
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256), split_sha256 TEXT NOT NULL,
  legacy_sha256 TEXT,                         -- sha256 of the DL4 JSON bytes it was migrated from (old runs compare this)
  n_rows BIGINT NOT NULL, n_holdout BIGINT NOT NULL,
  carried_from TEXT REFERENCES splits(id), created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (version_id, table_name, split_key));

-- ═════ saved joins (C's spec on the root) and join frames (A's pinned view versions) ═══════════════════════════════
CREATE TABLE joins (
  id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES dataset_roots(id), team_id TEXT NOT NULL,
  name TEXT NOT NULL, spec_version INTEGER NOT NULL, spec_json TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (root_id, name, spec_version));
CREATE TABLE join_frames (
  id TEXT PRIMARY KEY, join_id TEXT NOT NULL REFERENCES joins(id), version_id TEXT NOT NULL REFERENCES datasets(id),
  grain_table TEXT NOT NULL, inputs_json TEXT NOT NULL,     -- [{version_id, table, table_sha256}] pinned
  frame_sha256 TEXT NOT NULL,                                -- sha(spec_sha256 + ordered input table shas): identity
  n_rows BIGINT NOT NULL, schema_json TEXT NOT NULL,
  cache_blob TEXT REFERENCES blobs(sha256),                  -- optional materialisation (a cache)
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (join_id, version_id), UNIQUE (frame_sha256));

-- ═════ what a run read (A §1.1 + B §7) ══════════════════════════════════════════════════════════════════════════════
CREATE TABLE run_inputs (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('train','score','reference','join_input')),
  version_id TEXT, table_name TEXT, join_frame_id TEXT,
  table_sha256 TEXT, read_sha256 TEXT, match INTEGER,   -- expected / computed over the bytes read / equal?
  legacy_csv_sha256 TEXT,                               -- CSV-era runs: the data.csv hash the fit read
  split_id TEXT, split_sha256 TEXT, split_legacy_sha256 TEXT,
  read_mode TEXT NOT NULL DEFAULT 'engine_v1',          -- frames.read compat mode (§4.2)
  sample_spec TEXT, sample_sha256 TEXT, rows_total BIGINT, rows_used BIGINT, frame_mb INTEGER, columns_dropped TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX ix_run_inputs_run ON run_inputs (run_id);
CREATE INDEX ix_run_inputs_version ON run_inputs (version_id, table_name);
CREATE INDEX ix_run_inputs_split ON run_inputs (split_id);
CREATE INDEX ix_run_inputs_jf ON run_inputs (join_frame_id);
ALTER TABLE runs ADD COLUMN team_id TEXT;
ALTER TABLE runs ADD COLUMN created_by TEXT;
CREATE INDEX ix_runs_team ON runs (team_id, created_at);

-- ═════ staging, caches, ops (C §2.2, B §5) ═════════════════════════════════════════════════════════════════════════
CREATE TABLE uploads (
  id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT NOT NULL, root_id TEXT, parent_version_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('staged','checked','committed','discarded','expired')),
  files_json TEXT NOT NULL, plan_json TEXT NOT NULL DEFAULT '{}', check_json TEXT, check_sha256 TEXT,
  committed_version_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE table_stats (table_sha256 TEXT PRIMARY KEY, stats_version INTEGER NOT NULL, computed_at TEXT NOT NULL,
  n_rows BIGINT NOT NULL, n_cols INTEGER NOT NULL, columns TEXT NOT NULL, seconds REAL NOT NULL);
CREATE TABLE diff_cache (a_sha256 TEXT NOT NULL, b_sha256 TEXT NOT NULL, key TEXT NOT NULL, computed_at TEXT NOT NULL,
  result TEXT NOT NULL, PRIMARY KEY (a_sha256, b_sha256, key));
CREATE TABLE storage_usage (team_id TEXT NOT NULL, computed_at TEXT NOT NULL, bytes_referenced BIGINT NOT NULL,
  bytes_unique BIGINT NOT NULL, n_blobs BIGINT NOT NULL, PRIMARY KEY (team_id, computed_at));
CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, team_id TEXT, resource TEXT NOT NULL, mem_mb INTEGER NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','leased','succeeded','failed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, not_before TEXT,
  leased_by TEXT, lease_until TEXT, started_at TEXT, finished_at TEXT, error TEXT, result TEXT,
  by_principal TEXT NOT NULL, ref_kind TEXT, ref_id TEXT);
CREATE INDEX ix_jobs_claim ON jobs (status, resource, priority DESC, created_at);
CREATE INDEX ix_jobs_ref ON jobs (ref_kind, ref_id);
CREATE TABLE workers (id TEXT PRIMARY KEY, host TEXT NOT NULL, started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
  slots TEXT NOT NULL, version TEXT NOT NULL);

-- ═════ governance (C §4) ════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE certifications (id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version_id TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('certified','approved_for_model')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','superseded','expired','revoked')),
  contract_id TEXT, contract_result TEXT, requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '', expires_at TEXT);
CREATE INDEX ix_cert_root ON certifications (root_id, status);
CREATE TABLE legal_holds (id TEXT PRIMARY KEY,
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('team','project','dataset','version','run','model')), scope_id TEXT NOT NULL,
  case_ref TEXT NOT NULL, reason TEXT NOT NULL, placed_by TEXT NOT NULL, placed_at TEXT NOT NULL,
  released_by TEXT, released_at TEXT);
CREATE INDEX ix_holds_scope ON legal_holds (scope_kind, scope_id) WHERE released_at IS NULL;
CREATE TABLE column_tags (root_id TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  tag TEXT NOT NULL CHECK (tag IN ('pii_direct','pii_quasi','protected','sensitive','key','none')),
  mask TEXT NOT NULL CHECK (mask IN ('redact','hash','last4','year_only','bucket','none')),
  source TEXT NOT NULL CHECK (source IN ('suggested','confirmed')), set_by TEXT NOT NULL, set_at TEXT NOT NULL,
  PRIMARY KEY (root_id, table_name, column_name));
CREATE TABLE data_contracts (id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE (root_id, version));
ALTER TABLE datasets ADD COLUMN contract_result TEXT;     -- JSON per rule, set at commit

-- ═════ audit (C §1.7) ═══════════════════════════════════════════════════════════════════════════════════════════════
ALTER TABLE audit_log ADD COLUMN team_id TEXT;   ALTER TABLE audit_log ADD COLUMN project_id TEXT;
ALTER TABLE audit_log ADD COLUMN object_kind TEXT; ALTER TABLE audit_log ADD COLUMN object_id TEXT;
ALTER TABLE audit_log ADD COLUMN principal_id TEXT; ALTER TABLE audit_log ADD COLUMN auth TEXT;
ALTER TABLE audit_log ADD COLUMN request_id TEXT; ALTER TABLE audit_log ADD COLUMN decision TEXT;
ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;  ALTER TABLE audit_log ADD COLUMN row_hash TEXT;
CREATE INDEX ix_audit_team_ts ON audit_log (team_id, ts);
CREATE INDEX ix_audit_object ON audit_log (object_kind, object_id, ts);
CREATE INDEX ix_audit_actor ON audit_log (actor, ts);

-- lineage_edges: rebuilt once (SQLite cannot alter a CHECK; same pattern as champions in F-P2):
--   kinds += 'join_frame'; relations += 'joined_from', 'merged_from'; column `inferred` INTEGER (CSV-era, no pairing)

-- ═════ immutability (the protecting rows must not be rewritable through the app or a stray UPDATE) ═══════════════
CREATE TRIGGER splits_ro_u BEFORE UPDATE ON splits BEGIN SELECT RAISE(ABORT, 'splits are immutable'); END;
CREATE TRIGGER splits_ro_d BEFORE DELETE ON splits BEGIN SELECT RAISE(ABORT, 'splits are immutable'); END;
CREATE TRIGGER vt_ro_u BEFORE UPDATE ON version_tables BEGIN SELECT RAISE(ABORT, 'version tables are immutable'); END;
CREATE TRIGGER tf_ro_u BEFORE UPDATE ON table_files BEGIN SELECT RAISE(ABORT, 'table files are immutable'); END;
CREATE TRIGGER ri_ro_u BEFORE UPDATE OF table_sha256, read_sha256, split_sha256, sample_sha256 ON run_inputs
  BEGIN SELECT RAISE(ABORT, 'run inputs are immutable'); END;
CREATE TRIGGER blobs_key_ro BEFORE UPDATE OF sha256, key, bytes ON blobs BEGIN SELECT RAISE(ABORT, 'blob identity is immutable'); END;
CREATE TRIGGER ds_hash_ro BEFORE UPDATE OF manifest_sha256, data_sha256, legacy_sha256 ON datasets
  WHEN OLD.manifest_sha256 IS NOT NULL OR OLD.legacy_sha256 IS NOT NULL
  BEGIN SELECT RAISE(ABORT, 'version hashes are immutable'); END;
CREATE TRIGGER audit_ro_u BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER audit_ro_d BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
```

**The migration step itself** runs before the triggers are created: `ds_hash_ro` is created after P10's backfill, and
the `legacy_sha256` backfill is a one-time step. The triggers stop app bugs and casual edits. They do not stop
someone with file access to the DB; §5.7 and the audit hash chain cover that.

**Backfill** (P01; idempotent):
1. `teams('tm_default')` and every project → it.
2. `principals` from every distinct actor in `audit_log`, `datasets.created_by`, `runs.config.by`, the request tables
   and `ENSEMBLE_ADMINS` (admins → owners, everyone else → member).
3. `dataset_roots` from `dataset_cards` plus root rows.
4. `datasets.version_no` (created_at order per root), `team_id`, `n_cols`, `legacy_sha256 = sha256`,
   `storage = 'csv'`.
5. `runs.team_id` via the run's dataset.
6. `audit_log.team_id` via `details.dataset_id`.

### 2.2 Postgres deltas (P23)

- Types: `TEXT` timestamps → `timestamptz`; `TEXT` JSON → `jsonb`; `REAL` → `double precision`; the two existing
  AUTOINCREMENT tables (`audit_log`, `lineage_edges`) → `bigint GENERATED BY DEFAULT AS IDENTITY`.
- Triggers (one function per message):

  ```sql
  CREATE FUNCTION ens_immutable() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN RAISE EXCEPTION '% is immutable', TG_TABLE_NAME; END $$;
  CREATE TRIGGER splits_ro BEFORE UPDATE OR DELETE ON splits FOR EACH ROW EXECUTE FUNCTION ens_immutable();
  -- same for version_tables, table_files, audit_log; column-scoped ones use
  -- BEFORE UPDATE OF <cols> ... FOR EACH ROW WHEN (OLD.manifest_sha256 IS NOT NULL OR OLD.legacy_sha256 IS NOT NULL)
  ```

- Defence in depth: the app role gets `INSERT, SELECT` only on `audit_log`, `splits`, `run_inputs`,
  `version_tables`, `table_files`. Migrations run as the owner role.
- `audit_log` is partitioned by month on `ts` once it passes 5M rows (B 6.1).
- Version-number allocation uses `UPDATE dataset_roots SET next_version_no = next_version_no + 1 WHERE id = $1
  RETURNING next_version_no - 1`, which takes the row lock. The job claim uses `FOR UPDATE SKIP LOCKED` (B 5.5).
- SQL that differs from SQLite: `IS NOT 'deleted'` (`datasets.py:405`, `catalog/dsets.py:698`) → `IS DISTINCT FROM`;
  `json_extract` → real columns; `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`; `rowid` ordering → `created_at, id`.
  P23 lists every occurrence with grep.

---

## 3. Lake layout, manifest and hashes

### 3.1 Keys (relative to the store root `file:///…/data/lake/` or `s3://bucket/prefix/`)

```
blobs/<aa>/<sha256>.parquet      table parts, split files, join caches, sample id lists   (aa = first 2 hex chars)
blobs/<aa>/<sha256>.json         manifests (canonical JSON)
blobs/<aa>/<sha256>.<ext>        kept upload sources (.csv/.xlsx/.parquet), legacy data.csv / splits JSON / lineage.json
tmp/<writer-uuid>/…              in-flight writes; never referenced; swept after 24 h
exports/<version_id>/<table>.<fmt>   export cache (re-creatable, LRU, not a blob, never served by path)
```

- Write-once: local `link` + `chmod 0444`; S3 `PutObject` with `If-None-Match: *` and `x-amz-checksum-sha256`.
- No Hive partitioning. Sort by `__row_id`. zstd level 3. 122 880-row groups (30 720 for tables over 200 columns).
- Parts are at most 8M rows or about 1 GB.
- Writer: DuckDB `COPY`. The two-step numbering (A §8 note 1) avoids a serialising `row_number()`.

### 3.2 Manifest (`ensemble.manifest/1`)

The manifest follows A §2.2, with these changes:
- `provenance.ops[*].match` records the **confirmed** match decision (§5.3);
- `engine` records `duckdb`, `pyarrow` and `pandas` versions;
- the `legacy` block holds `csv_sha256`, `csv_blob`, `schema_json_sha256`, `root_rows_source`,
  `read = "frame_io.read+schema.json" | "pd.read_csv"` and `frame_equal`.

Shortened example:

```json
{"format":"ensemble.manifest/1",
 "dataset":{"root_id":"ds_1a2b","version_id":"ds_9f8e","version_no":7,"team_id":"tm_credit","project_id":"prj_1a2b"},
 "data":{"primary_table":"applications",
   "tables":[{"name":"applications","table_sha256":"5e1c…","n_rows":10000000,"n_cols":30,
     "row_id":{"column":"__row_id","min":0,"max":10099999,"sorted":true},
     "schema":[{"name":"acct","type":"VARCHAR","nullable":false,"reason":"leading zeros"},{"name":"y","type":"INTEGER","nullable":false}],
     "schema_sha256":"0b77…",
     "files":[{"sha256":"a1f0…","key":"blobs/a1/a1f0….parquet","role":"base","bytes":712345678,"n_rows":10000000,"row_id_min":0,"row_id_max":10099999}],
     "keys":[{"kind":"primary","columns":["acct"],"valid":true,"declared_by":"pr_ana"}]}],
   "relations":[]},
 "data_sha256":"c0de…",
 "provenance":{"parent":{"version_id":"ds_5a5a","manifest_sha256":"beef…"},"other_parents":[],
   "op":"upload_version",
   "ops":[{"op":"upload_version","table":"applications",
           "match":{"method":"key","columns":["acct"],"confirmed_by":"pr_ana","candidates":[["acct",0.999],["row_hash",0.62]],
                    "matched":9900000,"changed":15,"added":100000,"removed":100000,
                    "holdout_identity_lost":0,"acknowledged":[]}},
          {"op":"carry","table":"bureau","from_version":"ds_5a5a"}],
   "sources":[{"filename":"applications_2026-11.csv","sha256":"9999…","bytes":2161000000,"kept":true}],
   "checks":{"warnings":[]},"created_at":"2026-11-21T10:00:00Z","created_by":"pr_ana","reason":"CHG-1182",
   "engine":{"duckdb":"2.0.1","pyarrow":"24.0.0","pandas":"2.3.3","parquet":{"compression":"zstd","level":3}}},
 "legacy":null}
```

**Canonical JSON:**
- `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`;
- strings NFC-normalised;
- **no floats anywhere in hashed JSON**: shares, thresholds and params are decimal strings; a writer that meets a
  float raises;
- integers fit in int64; timestamps are `YYYY-MM-DDTHH:MM:SSZ`.

### 3.3 Hash scheme

| Hash | Definition | Stored | Protects |
|---|---|---|---|
| **file** | sha256 of the bytes | blob key, `blobs.sha256` | the bytes; outside edits; S3 upload integrity (`x-amz-checksum-sha256`) |
| **table** | `sha256("ensemble.table/1\n" + "\n".join(f"{role}:{part_sha}" for parts in order))` | `version_tables`, manifest, `run_inputs.table_sha256` | the table's identity; carried-table detection |
| **schema** | sha256(canonical schema JSON) | `version_tables`, manifest | schema-only change detection |
| **data** | sha256(canonical `manifest.data`) | `datasets.data_sha256` | dedupe; version identity, independent of who and why |
| **manifest** | sha256(canonical manifest incl. `data_sha256`) | `datasets.manifest_sha256`, blob key, audit `dataset_version_created` | the commit; provenance |
| **split** | `sha256("ensemble.split/1\n" + int64-LE(__row_id) ‖ int8(fold) …)` over rows in `__row_id` order, streamed | `splits.split_sha256`, audit `split_created`, `run_inputs.split_sha256` | split identity, independent of the Parquet writer |
| **sample** | `sha256("ensemble.sample/1\n" + int64-LE selected __row_id, sorted)` | `run_inputs.sample_sha256` | what a sampled fit trained on |
| **join frame** | `sha256("ensemble.join/1\n" + spec_sha256 + "\n" + "\n".join(input table shas in spec order))` | `join_frames.frame_sha256` | the logical join input; the cache blob is checked by its own file hash |
| **upload source** | sha256 of the uploaded bytes, computed while streaming | manifest `sources`, `uploads.files_json` | evidence of what was received |
| **legacy CSV** | sha256 of data.csv (today's `datasets.sha256`) | `datasets.legacy_sha256`, `run_inputs.legacy_csv_sha256`, `legacy_csv` blob | old runs' `data_sha256` |
| **legacy split** | sha256 of the DL4 split JSON bytes | `splits.legacy_sha256` | old runs' `splits_sha256` |
| **audit row** | `sha256(prev_hash ‖ canonical(row))` per team | `audit_log.row_hash` | tamper evidence for everything above |
| logical (hint only) | DuckDB `sum(hash(__row_id, cols…))` | never stored as identity | "same content, different bytes" in the UI |

**Rule:** nothing stored or compared across time is computed with an engine's private hash function. Only sha256 over
defined bytes is used, plus splitmix64 for sampling (§5.9).

---

## 4. Absorbing DLA4

### 4.1 Every read goes through one typed reader (DLA4 #4)

`backend/app/frames.py` (C §0, with A's input refs):

```python
InputRef = Table(version_id, table) | JoinFrame(join_frame_id) | LegacyPath(path)   # LegacyPath only for old run cfgs
def read(ref, columns=None, rows=None, *, mode="engine_v1", hash_into=None) -> pd.DataFrame   # index = __row_id
def scan(con, ref, *, mask_for: Principal | None) -> duckdb.DuckDBPyRelation                  # masked unless allowed
def columns(ref) -> list[ColumnInfo];  def n_rows(ref) -> int
def read_upload(file, *, kind="batch") -> pd.DataFrame       # user batch files for scoring: typed import rules of §5.1
def resolve(run_cfg) -> InputRef                              # csv_path / dataset_id / join_frame_id → ref
```

- **Modes.**
  - `engine_v1` reproduces what the worker saw from CSV: `frame_io.read(raw, schema.json)` semantics for legacy
    tables. For Parquet tables: DATE/TIMESTAMP → ISO string, nullable INT with nulls → float64, VARCHAR → object,
    DECIMAL → float64.
  - `native` keeps the Arrow types.
  - Training and every analysis consumer use `engine_v1` until it is deliberately moved, so paired compares with
    older runs keep their features.
  - `run_inputs.read_mode` records the mode. A paired compare across different modes is labelled "read modes
    differ".
- `hash_into` hashes every part file while reading (DL3's rule, "hash exactly the bytes the fit reads").
- **Export** writes `schema.json` (the manifest's tables and schemas) into every zip. This closes the DLA4 #4 sub-item.
- **The ~60 schema-less sites** (127 `read_csv(` calls in 42 files today) move to `frames.read` **before** any
  Parquet exists (P03/P04), on the CSV store, with `frame_io` schemas. The ZIP-as-number bug is fixed independently
  of the storage switch, and the switch later changes one module.

### 4.2 `__row_id` and an explicit key choice (DLA4 #2)

- The silent guess goes away. `dataset_versions.guess_key` becomes `key_candidates()`, which proposes and never
  decides (§5.3).
- `POST /uploads/{u}/commit` on a next-version upload **requires** `match` per table:
  - `{"method":"key","columns":[…]}`;
  - `{"method":"declared"}` (the parent's confirmed `table_keys`);
  - `{"method":"row_hash"}`;
  - `{"method":"none"}` (all rows new; the edge is marked unpaired).

  Without it: `400 match_required`, with the candidates.
- The profile role 'ignored' no longer hides a column from candidates (`acct` "A00000" would rank first: unique,
  non-null, 100 % overlap).
- The check response shows, for the chosen method **and for the best candidate**, the counts matched / changed /
  added / removed and **`holdout_identity_lost`** (parent holdout rows that would get new ids, so the holdout would
  be redrawn for them).
- If the chosen method loses identity on more than 1 % of parent holdout rows, or a candidate key would match at
  least 1 % more rows than the chosen method, the commit needs `acknowledge: ["holdout_identity_loss"]` and the
  warning is stored in the manifest `checks`. **Nothing falls back silently.** A key that turns out non-unique or
  null at commit is `400 key_invalid` with the first 10 offending values, masked if PII.

### 4.3 Content-addressed split files, cross-checked (DLA4 #3)

- Splits are Parquet blobs. The `splits` rows are insert-only (triggers). Each split creation writes an audit row
  `split_created {split_id, split_sha256, blob_sha256}` into the team's hash chain. Every run pins its split blob
  (`blob_refs owner_kind='run', role='split'`) and records `split_sha256` in `run_inputs`.
- **At fit (`worker.data_gate`) and at `verify`**, five checks; any failure → `409 split_integrity` naming the check
  (algorithm in §5.7):
  1. re-hash the blob against its key;
  2. recompute `split_sha256` from the file;
  3. compare it with the `split_created` audit row (and that row's chain link);
  4. compare it with **every earlier `run_inputs.split_sha256` for the same `split_id`**, and for the same
     `(version, table, split_key)`;
  5. for a carried split, check that zero surviving rows changed fold against `carried_from`.
- **Restore** works because every earlier run pins its exact split blob. `restore-splits` offers the blob the
  earliest run used, which closes DLA4's "nothing to restore".
- **Paired compare honesty:** pairing is the intersection of the two runs' holdout `__row_id` sets. The response
  carries `overlap = n_common / max(n_a, n_b)`.
  - "paired on the same holdout rows" only when the sets are equal;
  - "paired on n of N rows (holdouts differ)" otherwise;
  - no paired Δ below 50 % overlap, only a labelled unpaired table.

### 4.4 Bounded value validation: 400, never 500 (DLA4 #1)

`backend/app/lake/values.py`, called by every edit op, by Data Doctor apply and by `add_rows`, before SQL is
compiled:

| Target type | Accepts | Refuses (400 `value_out_of_range` / `value_invalid`, `{op_index, column, type, min, max}`) |
|---|---|---|
| BIGINT / INTEGER | Python int, or a float with `is_integer()`, within [−2⁶³, 2⁶³−1] (INTEGER: ±2³¹) | 1e30, 1e308, 2**63, 2**64, non-integral floats, bools, NaN/±inf |
| DOUBLE | finite floats, \|x\| ≤ 1.7976931348623157e308 | NaN, ±Infinity (JSON `NaN`/`Infinity` tokens are rejected by the request parser too) |
| VARCHAR | strings ≤ 32 KiB; numbers only where the op says "as text" | longer strings |
| DATE / TIMESTAMP | ISO-8601 | anything else |
| BOOLEAN | true/false | — |

- **Formulas** (`add_column`) compile to SQL. The preview runs `count(*) FILTER (WHERE NOT isfinite(x))` on the
  result, and any non-finite result → `400 non_finite_result {count, examples}`.
- DuckDB `ConversionException` / `OutOfRangeException` raised inside an edit or Doctor-apply route → `400
  edit_invalid {op_index, column}`. The engine text is **logged, never returned**, which fixes the "400 leaks pandas
  internals" item.
- Every JSON response goes through one `json_safe` encoder: non-finite → `null` plus `"non_finite": true`.
  `allow_nan=False` everywhere.
- Verification (P03): replay all 28 DLA4 cases plus `fill_missing` / `replace_values` with the same values. Every
  one must give 400 with a typed code and no stack text.

**The smaller DLA4 items:**
- a one-class target edit → **block** (`400 target_one_class`) unless `acknowledge`;
- the reset request is marked `used`;
- `runs_made_irreproducible` is computed in the pending response;
- Build match by renamed columns stays as it is (safe), and the upload check lists "columns renamed?" pairs by
  identical `schema` type plus a value-overlap ≥ 95 %. This is a hint, never an automatic rename.

---

## 5. Algorithms

### 5.1 Upload and convert (streaming)

1. `POST /uploads` (multipart, N files): each part streams to `lake/tmp/<up>/<n>.src` in 8 MB chunks with sha256 on
   the fly. The cap is `ENSEMBLE_MAX_UPLOAD_GB` (5). The free-disk guard needs 3× the size. Nothing is held in
   memory (this replaces `datasets.read_upload(content: bytes)` and the 200 MB cap).
2. **Detect:** CSV/TSV (DuckDB sniff), Parquet (schema read; a column starting with `__` is refused), XLSX (calamine,
   one table per non-empty sheet, header row detected, hidden sheets listed and unticked, ≤ 200 MB per workbook).
3. **Type** (CSV; A §2.6 rules):
   - Pass 1: `sniff_csv` proposes types, then one `all_varchar` scan counts per column the leading-zero values, the
     `TRY_CAST` failures and the date-format ambiguity.
   - Decide per the rules: leading zero → VARCHAR; a failed cast → VARCHAR plus a warning with 5 examples;
     ambiguous `dd/mm` vs `mm/dd` → VARCHAR plus "declare the format"; NA tokens from the team policy.
   - Declared overrides from the dataset contract win.
4. **Write:** `COPY (SELECT * FROM read_csv(…, columns={decided})) TO tmp/typed.parquet`, then a second `COPY` adding
   `__row_id` (for a new dataset, `file_row_number`; for a next version, the ids from §5.3), `ORDER BY __row_id`,
   parts ≤ 8M rows.
5. **Hash** each part while it is moved into `blobs/` (state `pending`). Compute `table_stats` in the same job for
   ≤ 5M rows, otherwise as a `profile` job.
6. **Check** (`POST /uploads/{u}/check`) writes nothing but tmp files. It returns, per table: the decided schema with
   reasons; match results (§5.3); diff counts; contract results; PII suggestions; `blocks[]`. It stores
   `check_sha256` = the hash of the check result.
7. **Commit** (`POST /uploads/{u}/commit {reason, match, acknowledge, make_default}`) refuses with 409 if the plan
   changed after the check (`check_sha256`). It runs the VersionWriter commit (§5.2). It is idempotent on the upload
   id: a repeat returns the committed version.

### 5.2 The version writer and the commit point (A §3, §5.2)

```
w = VersionWriter(root, parent, op, by, reason, job_id)
  w.carry(t) | w.append(t, rel) | w.write(t, rel_sql) | w.write_arrow(t, arrow_with___row_id)
  w.split(...)                          # carry the parent's splits for the tables that survived (§5.7)
  w.commit():
     manifest → canonical → sha → blob
     BEGIN IMMEDIATE (SQLite) / BEGIN + row lock on dataset_roots (PG)
       dedupe: committed version in root with same data_sha256 and op ∈ DEDUPE_OPS → ROLLBACK, return it
       same job_id already committed → ROLLBACK, return it                         (lease re-run safety, B §9)
       allocate version_no; INSERT datasets, version_tables, table_files, table_keys, table_relations, splits
       INSERT blob_refs (team_id); UPDATE blobs SET state='live' WHERE sha IN (…) AND state IN ('pending','live')
         → if any referenced blob is 'purging'/'purged': ROLLBACK, re-put the bytes, retry once        (§7 R-S3)
       UPDATE dataset_root_tables SET next_row_id = max(next_row_id, :new)
       INSERT lineage_edges, audit_log (dataset_version_created + split_created rows, hash-chained)
     COMMIT; rm tmp; settle_default() with compare-and-set on dataset_roots.default_version_id
```

No file I/O and no pandas run inside the transaction (B 4.8).

### 5.3 Match: key with explicit confirmation, visible fallback warnings

1. **Candidates** (`key_candidates(parent_table, new_table)`, one DuckDB pass):
   - Every column shared by name with a compatible type family is a candidate, **including profile role
     'ignored'**, plus the parent's confirmed `table_keys` (composite included).
   - Statistics per candidate: `count`, `count(DISTINCT)`, nulls on both sides, and **overlap** (the share of new
     keys found in the parent, by semi join).
   - Score = uniqueness_both × non_null_both × overlap, with a tie-break on an id-like name (`KEY_NAME`) or an
     id-like shape (fixed width, leading zeros, ≥ 99 % distinct). The top 3 are shown.
2. **Simulate** the top candidate, the declared key, and row hash (A §3.1 SQL; DuckDB `hash()` with occurrence
   numbers, which is ephemeral and so allowed). Each gives matched / changed / added / removed /
   `holdout_identity_lost`. Measured costs: key match 0.84 s and row hash 3.4 s at 10M rows (A §8).
3. **The person chooses** (the UI preselects the declared key, else the top candidate, and **always shows the
   counts**). The commit needs `match`.
   - Warnings: `identity_loss` (≥ 1 % of parent holdout rows), `better_key_available`, `low_overlap` (< 50 %:
     "is this the same dataset?").
   - The first two need `acknowledge`.
   - The choice and the numbers go into the manifest `ops[].match`.
   - A chosen key is saved as a confirmed `table_keys` row of the new version.
4. **Assign ids:** matched rows keep the parent's `__row_id`; new rows get `next_row_id + k` in file order.
5. The drop folder (T3) needs `match` in its rule; a `row_hash` rule only matches when the identity loss is 0, and
   otherwise the file goes to the project inbox.

### 5.4 Diff (B §5.3)

1. Tables by name: equal `table_sha256` → unchanged. A removed and an added table with equal sha → rename.
2. Schema from the manifests.
3. Same root: key = `__row_id`. Pass 1:

   ```sql
   SELECT coalesce(a.__row_id,b.__row_id) k, a.__row_id IS NULL AS added, b.__row_id IS NULL AS removed,
          hash(a.cols…) <> hash(b.cols…) AS changed
   FROM a FULL JOIN b USING (__row_id)
   ```

   It gives the counts and a temp table `changed(k)`.
4. Pass 2: per-column `count(*) FILTER (WHERE a.c IS DISTINCT FROM b.c)` over `a SEMI JOIN changed ⋈ b SEMI JOIN
   changed`.
5. Cross-root, or CSV vs Parquet: a **confirmed** business key (same UI as §5.3), else a multiset row-hash diff that
   reports only added/removed, labelled "no key: changes appear as remove + add".
6. Sample rows: 50 per class by keyset, masked per §6.3.
7. Cache in `diff_cache(a_sha, b_sha, key)`.

### 5.5 Edit (A §3.3 + §4.4)

- Ops are validated (§4.4) and then compiled into **one** DuckDB query over the parent table:
  - `set_cells` → a VALUES join on `__row_id`;
  - `delete_rows` / `filter_rows` → a WHERE from the whitelisted AST;
  - `add_rows` → `UNION ALL BY NAME` with new ids;
  - rename / drop / add_column (formula → SQL) / `cast_column` (`TRY_CAST`, with a count of NULLs created) /
    `replace_values` / `fill_missing`.
- `_row` is accepted for one release and translated through `file_row_number`.
- Only the edited table is rewritten; the others are carried.
- The DL3/DL4 guards are kept:
  - target drop/rename/cast refused;
  - `set_target`;
  - cap per edit;
  - summary = diff.
- The parent's parts are re-hashed unless `blobs.verified_at` is less than 24 h old.

### 5.6 Delta (A §3.2, C §2.3; storage per R7)

- Input: per table, a key (confirmed, valid) and either an `_op` column (`I/U/D`, mapping chosen) or `upserts` +
  `deletes` files.
- Refused:
  - no valid key → 400;
  - duplicate keys in the delta → 400 with the first 10;
  - schema drift → the same warnings as an upload; a new column needs `allow_new_columns`.
- Computed in DuckDB: `kept = parent ANTI deletes ANTI upserts`, `upd = upserts ⋈ parent` (keep `__row_id`),
  `ins = upserts ANTI parent` (new ids).
  - Insert-only → one **append part** (`change='appended'`).
  - Otherwise → the table is rewritten (12.5 s at 10M rows, A §8).
- Reported: inserted / updated / deleted / missing deletes ("delete of a key not in the parent": warning) / upserts
  of missing keys (counted as inserts, with a warning).
- Compaction: a table with more than 16 parts is rewritten into ≤ 1 GB parts **the next time it is rewritten
  anyway**; there is never a background version.
- **T2 (trigger: versioned bytes > 20× live):** `table_files.role ∈ upsert/delete`, merge-on-read (B §5.6, 0.58 s for
  10 deltas at 10M rows), checkpoint at 20 deltas or 25 % of the base, and full extracts stored as their computed
  delta.

### 5.7 Splits

- **Draw** (lazily, as today, the first time a target/strategy is needed): the strategy code in `splits.py` is
  unchanged but indexed by `__row_id` instead of position. Write `(__row_id BIGINT, fold TINYINT)` sorted, as a
  Parquet blob. `fold` keeps its meaning: 0..k−1 CV fold, −1 holdout, −2 excluded, −3 tuning.
- One transaction does three things: `INSERT splits`; `INSERT blob_refs`; `INSERT audit_log split_created`.
- **Carry** to a derived version:

  ```sql
  SELECT c.__row_id, coalesce(p.fold, newfold(c.__row_id)) FROM child c LEFT JOIN parent_split p USING (__row_id)
  ```

  - `newfold` = splitmix64(`__row_id` ⊕ seed) → holdout with `new_holdout_share`, else a fold.
  - It is deterministic and engine-independent, computed in numpy over the new ids only.
  - `carried_from` is set.
- **Join frame:** the grain table's split, filtered by `SEMI JOIN` to the frame's `__row_id`s. No separate row is
  stored; `run_inputs.split_id` is the grain split and `split_sha256` is recomputed over the filtered rows.
- **Verify / fit gate:** the five checks of §4.3. Cost at 10M rows: re-hash ≈ 0.1 s (an 80 MB split file); streaming
  `split_sha256` ≈ 0.3 s; the run and audit lookups are indexed.

### 5.8 Join and grain (A §4.4 + C §2.6)

- **Spec** (`joins.spec_json`):

  ```json
  {"format":"ensemble.join/1","grain":"applications",
   "joins":[{"type":"n:1","table":"bureau","on":[["app_id","app_id"]],"columns":[…],"prefix":"bureau__"},
            {"type":"aggregate","table":"payments","on":[["app_id","app_id"]],"aggs":[{"name":"n_pay","fn":"count"},{"name":"pay_90d","fn":"sum","column":"amount","window":{"date":"paid_on","relative_to":"app_date","days":-90}}]}],
   "where":null}
  ```

  - Inputs may name another dataset as `{root_id, version_id, table}` (pinned; a `merged_from` edge).
  - There is no free SQL; `where` is the whitelisted AST.
- **Validate** against a concrete version: the n:1 side's `on` columns must be a valid key (from `table_keys` or a
  0.15 s uniqueness check); 1:n without an aggregate → 400; coverage per joined table is reported.
- **Materialise** (`frames.materialise(spec, inputs)`, **the only join code path, used by training, profiling,
  export and batch scoring**):

  ```sql
  SELECT g.__row_id, g.*, j1.cols, agg.* FROM grain g LEFT JOIN … LEFT JOIN (SELECT … GROUP BY key) agg … ORDER BY g.__row_id
  ```

  Then assert `count(frame) = count(grain after where)` and `count(DISTINCT __row_id) = count(*)`; otherwise refuse.
  Measured at 10M rows: 3.2–9.9 s (C §7, A §8).
- **Identity and caching:** `join_frames.frame_sha256` = hash(spec sha + input table shas); a cache blob only for
  frames of 1M rows or more (`JOIN_CACHE_MIN_ROWS`). A run records the cache file's hash as `read_sha256` and the
  inputs as `join_input` rows.

### 5.9 Training frame and sampling (B §5.4 with R12/R13)

```python
est = Σ_columns n_rows × bytes(col)   # 8 B numeric/bool; ≈ 58 + mean_len B object/compat-string; from table_stats
budget = ENSEMBLE_TRAIN_FRAME_SHARE (0.10) × worker RAM  (P11 calibrates with a real AutoGluon fit)
if est ≤ budget:  read all usable columns         # load peak ≈ 1.6 × est, covered by the 10 % rule (frames.read, mode engine_v1)
else:
  1. prune: contract-excluded, constant, >99 % null, ID-like (never the target or declared features)
  2. sample train rows only: ids = __row_id of folds ≥ 0 (pyarrow, one column)
       z = splitmix64(ids ⊕ seed·φ)  (numpy uint64; 0.19 s per 5M ids measured)
       keep where z mod 10⁶ < 10⁶ · rate[class(y)]   # stratified; rare classes kept whole; regression: deciles of y
     write the kept ids as a 'sample' blob → DuckDB SEMI JOIN → tmp parquet → pandas
  3. holdout: never sampled; scored in 500k-row batches through DuckDB → predictions Parquet
  record sample_spec {method:'splitmix64', seed, rates, rows_total, rows_used}, sample_sha256, columns_dropped
```

- The run card says "trained on 2.0M of 50M rows (stratified sample, seed 42); holdout scored on all 5M rows".
- Two runs with the same `sample_sha256` are on provably the same rows.

### 5.10 GC and purge (A §5.3–5.4, R17/R18)

- **GC** (`lake/gc.py`, daily and `POST /api/admin/lake/gc?dry_run=1` by default):
  - Phase 1 selects blobs with no refs and `state ∈ (pending, live)`, older than 24 h (pending) or **7 days**
    (live, unreferenced); marks them `purging` in one transaction; deletes the object; marks it `purged`. The row is
    kept forever.
  - Phase 2 is a weekly store sweep of objects without a `blobs` row, older than 24 h. A name that does not match
    its bytes' sha is **reported as an integrity finding**, not deleted.
  - **Deletes stay disabled** until `GET /api/admin/lake/verify-all` has passed once on the deployment
    (`catalog_meta.gc_armed`).
- **What holds a ref:**
  - versions not `deleted`, through their parts, manifests and sources (sources expire per team policy);
  - splits;
  - runs, through their split blob, sample blob, join cache and legacy CSV/JSON;
  - frozen holdouts;
  - uploads not yet committed (24 h).
- **Delete version** (four eyes, today's flow): `state='deleted'`, and the part/source refs are removed. The
  manifest, the splits pinned by runs and the frozen holdout stay. Shared parts survive while another version
  references them.
- **Purge** (four eyes, plus each other team's owner when refs cross teams; blocked by any legal hold in the set):
  1. Make a new version without the rows or columns.
  2. Compute the reference set of the affected blobs across all teams (versions, splits, join caches, runs' pinned
     blobs, frozen samples).
  3. On approval, all-or-nothing: every referencing version → `deleted`, run pins on those blobs → replaced by
     "purged" markers (run rows stay; re-verify then reports `purged`, not `mismatch`), blobs → `purging` → deleted
     immediately, including every S3 object version.
  4. When column names themselves are sensitive, the manifest is replaced by a redacted manifest that keeps
     `data_sha256`.
- The audit log is never purged; it never holds row values (§6.3).

### 5.11 Migrating CSV-era data (A §6, corrected by R14)

Per version, idempotent and resumable, root by root, v1 first, as a `migrate` job with progress:

1. **Verify:** `sha256(data.csv) == datasets.sha256`, and the split JSON files' hashes == `split_files.sha256`. On a
   mismatch → not migrated, reported (`dataset_migration_skipped`), resolved through the DL3 flow. Migration never
   launders an outside change.
2. **Read as the worker read it:** `df = frame_io.read(raw, frame_io.load(dir))` (`schema.json` when present).
3. **`__row_id`:**
   - `lineage.json.root_rows` when it has n entries;
   - `0..n-1` for roots;
   - fresh ids for versions with no mapping, marked `legacy.root_rows_source="none"` with the edge `inferred=1`,
     so compares refuse to pair them.

   Negative legacy ids are kept. The table is written **in data.csv order** (`row_id_sorted=0`), so old positions =
   `file_row_number`.
4. **Write** with pyarrow from that frame, so the dtypes are exactly the frame's. Then the **round-trip proof**:
   `frames.read(parquet, mode="engine_v1")` must `equals(df)` with equal dtypes, and
   `frame_io.to_csv_bytes(readback, schema)` is compared with the original bytes (`legacy.csv_reproducible`).
5. **Hashes:** `legacy_sha256` = the CSV hash; new `data_sha256` / `manifest_sha256`; `datasets.sha256` switches
   meaning with `storage='parquet'`.
6. **Keep the evidence:**
   - data.csv → a `legacy_csv` blob, referenced by every run whose `data_sha256` equals it;
   - each split JSON → a `legacy_json` blob plus a Parquet split with `legacy_sha256` = the JSON's sha (= the
     runs' `splits_sha256`);
   - `lineage.json` and `schema.json` → `legacy_json` blobs;
   - `TOMBSTONE.json` → a manifest with no files; `holdout_sample.csv` → a `frozen_holdout` blob.
7. **Commit** in one transaction. Audit `dataset_migrated {legacy_sha256, manifest_sha256, csv_reproducible}`.
   Rename the folder to `<id>.migrated`; it is deleted one release later, after a second `verify-all`.
8. **Old runs:**
   - `run_inputs` rows are backfilled (`legacy_csv_sha256` = cfg `data_sha256`, `split_legacy_sha256` = cfg
     `splits_sha256`);
   - `frames.resolve(cfg)` maps a `…/datasets/<id>/data.csv` path to `Table(id,'data')`, with the legacy blob for
     hash checks;
   - re-verifying an old run hashes the `legacy_csv` blob; explain, slices and the rest read the Parquet table in
     `engine_v1` mode, which equals what they read from CSV with the schema.

**Cost:** A §6.3 (a few seconds per 10M-row version). The POC's `data/` migrates in minutes.

---

## 6. Teams, permissions and the consumer migration

### 6.1 Roles and actions (C §1.2–1.3, adopted as written)

- Roles: viewer < member < maintainer < owner. `risk_reviewer` is a separate axis. Platform admin is
  `principals.is_admin`.
- The action keys and the matrix are C §1.3, plus two rows:
  - `data.match_confirm` (member: confirm a match/key on their own upload);
  - `data.purge` (request: maintainer; decide: owner of every team involved, ≠ requester).
- Four eyes everywhere goes through `authz.policy.decide_check` (C §1.5). `same_person` stays at least as strict as
  today.
- Shares, certifications, legal holds, contracts and PII tags follow C §1.4 and §4.

### 6.2 Enforcement modes

| `ENSEMBLE_AUTHZ` | Behaviour | When |
|---|---|---|
| `off` | today | until P19 |
| `audit` (default from P19) | every `require()` decides. A denial is written as `authz_would_deny` (hash-chained) and the request proceeds, with the header `X-Ensemble-Authz: would-deny <action>`. Lists are not filtered, but every row carries `team`. | POC, typed names |
| `enforce` | 403 `{code:'forbidden', action, team, needed}`, 401 without credentials, lists filtered by `visible_teams()`. The server **refuses to start** with typed names (unless the logged demo flag `ENSEMBLE_AUTHZ_ALLOW_TYPED=1`) or with any uncovered route. | after SSO (T3) |

- **Enforcement point:** a FastAPI dependency per route, `require(action, obj=path(...)|body(...))`. The middleware
  only resolves *who* (an `IdentityProvider` chain: typed → trusted header → OIDC; SAML only through a proxy;
  Kerberos on demand).
- Background jobs are authorised when queued; `jobs.by_principal` records who.
- The Vela agent calls the API in-process with the session owner's principal (a signed internal header), so it
  never has more rights than its user.
- **Coverage gate:** `GET /api/authz/coverage`. Every non-GET route and every row-returning GET carries `require()`
  or `public()`. It is checked in P19, X4 and X5.

### 6.3 PII

- Tags live per root, table and column. Suggested tags are masked until a maintainer confirms them.
- Masking is applied **inside the one DuckDB relation builder** (`frames.scan(mask_for=…)`), which serves grid,
  rows, diff samples, export, the agent CSV, holdout-prediction pages, the SHAP row view and Doctor examples.
- `table_stats` top-20 values and histograms of PII columns are masked when rendered.
- Error messages never echo values: validation errors give the column and type, never the value, when the column is
  tagged.
- Kept upload sources (unmasked by nature) have their own team retention (default 30 days, an open question).

### 6.4 Team policy JSON (defaults are today's behaviour)

```json
{"retention":{"versions":{"keep_days":730,"keep_min":5,"keep_certified":true},"sources":{"keep_days":30},
              "join_caches":{"evict_unused_days":30},"runs":{"slim_days":90,"evict_days":365},"staging":{"days":7}},
 "na_tokens":["","NA","N/A","NULL","null","NaN","-"],
 "maintainer_can_review":false,"require_independent_reviewer":false,"require_certified_inputs":false,
 "certify_days":365,"discoverable":true,"self_service_teams":false}
```

### 6.5 Consumer migration list (every read site, grouped; counts by grep at a2338a4)

Today there are **127 `read_csv(` calls in 42 files** and **202 `csv_path`/`load_frame` references**. Every one
moves to `frames.read` / `frames.scan` / `frames.read_upload`.

| Group | Files (`read_csv(` count) | Change | Phase |
|---|---|---|---|
| **R-A training engine** | `engine/worker.py` (1: `load_frames`, `data_gate`), `engine/api.py` (3: label read, cfg build, pseudolabel), `engine/serving.py` (1, `holdout_frame`), `engine/heavy.py`, `engine/custom_ensemble.py`, `engine/ladder.py` (via `load_frames`), `engine/importer.py` | `frames.read(resolve(cfg), hash_into=…)`; label-only reads use `columns=[label]`; cfg gains `input_ref` (`csv_path` kept as a key) | P03 |
| **R-B analysis on a run** | `engine/explain.py` (5), `engine/slices_api.py` (3), `engine/shift_api.py` (6), `engine/shift.py` (2), `engine/learning_curve.py` (1), `engine/active_learning.py` (1) + `_api.py` (3), `engine/annotations.py` (3) + `_api.py` (3), `fairlending/api.py` (1), `fairlending/tables.py` (2) | `usecols=lambda` → `columns=`; `shift` takes `{"ref":…}`; `annotations.py:138` hard-coded `csv_path("support-tickets")` → `Table(id,'data')` | P03 |
| **R-C Vela and the agent** | `vela/findings.py` (3), `vela/experiments.py` (3; `:376` builds paths next to `csv_path` → the lake tmp), `vela/doctor.py` (1), `vela/missing.py` (1), `agent/tools.py` (`_dataset_csv`, tool map) | frames reads; agent CSV through the masked scan | P03 |
| **R-D data tools** | `data_doctor.py` (1), `data_doctor_api.py`, `featurelab.py` / `featurelab_api.py`, `autopilot/engine.py` (5), `autopilot/audit.py` (2), `datasets.py` (4), `catalog/dsets.py` (1), `catalog/compare.py` (1), `catalog/datasets_api.py` | reads now; writes move to `derive()` / VersionWriter in P07 | P04 (reads), P07 (writes) |
| **R-E kinds** | `anomaly/api.py` (11), `anomaly/core.py` (2), `forecast/core.py` (1) + `api.py`, `survival/*`, `el/api.py` (1), `segmented/core.py` (1), `reject/core.py` (1), `multilabel/core.py` (1) | `detect()` and run cfgs use refs; `nrows=` → `frames.read(rows=range)`; forecast time column stays a string under `engine_v1` | P04 |
| **R-F registry** | `registry/scoring.py` (15), `registry/credit_report_api.py` (8), `registry/monitoring_api.py` (7), `registry/changes_api.py` (6), `registry/build.py` (6), `registry/changes.py` (1), `registry/card.py` (1), `registry/drift.py` | training reference → `frames.read`; **user batch files → `frames.read_upload`** (CSV/XLSX/Parquet, typed rules); build hashes from `run_inputs` | P04; join batches P15 |
| **R-G other** | `gym/export.py` (3; the exported script reads Parquet), `sqlsource/core.py` (3) + `api.py` (1; a refresh = upload-as-version, types from the DB), `frame_io.py` (1; becomes private to `frames`/`lake.migrate`), `samples*.py` (written as Parquet versions; plus one multi-table sample in P12) | | P04 / P05 / P12 |

**Grep gate** (run at the end of P04 and in every later phase; it must print nothing):

```bash
grep -rnE "read_csv\(|read_parquet\(|read_table\(|read_excel\(|open\([^)]*csv_path|\.csv_path\(|load_frame\(" \
     backend/app --include=*.py | grep -vE "^backend/app/(frames\.py|lake/)"
```

After P09 a second gate must print nothing outside `lake/migrate.py`:
`grep -rnE "split_files|splits\.json|root_rows" backend/app --include=*.py`.

---

## 7. Red team (as a skeptical bank architect)

| # | Risk | Failure mode | Mitigation | Owner phase |
|---|---|---|---|---|
| 1 | **Data loss** | GC deletes a blob that is still needed (a ref bug, or a race between a commit that dedupes onto a blob and GC); a metadata DB loss orphans the lake; migration deletes data.csv too early | GC disarmed until `verify-all` passes; 7-day grace for unreferenced live blobs; dry-run by default; `purged` rows kept; the commit re-asserts `state IN (pending, live)` inside its transaction and re-puts the bytes otherwise; S3 bucket versioning with 30 days noncurrent retention (undo for GC, not for purge); nightly SQLite `.backup` / Postgres PITR; manifests are self-describing (ids, team, project), so `lake rebuild-index` can recover version rows from blobs; legacy folders renamed, deleted one release later after a second `verify-all` | P02, P10, P24 |
| 2 | **Silent lineage breaks** | a derive path rebuilds a frame without `__row_id`; a key guess fails (DLA4 #2); a lineage CTE truncates (the depth-64 cap, B 2.4); pairing claimed over different holdouts | `derive()` refuses a rewritten table without `__row_id` unless `op` is declared all-new (edge `inferred`); explicit `match` + `holdout_identity_lost` + acknowledge; versions listed by `root_id` (no recursion), so the CTE walks only cross-object edges; paired compare reports overlap and refuses below 50 % | P06, P07, P08, P09 |
| 3 | **Hash drift across library versions** | DuckDB/pyarrow upgrades write different Parquet bytes; an engine `hash()` changes; float formatting changes canonical JSON | identity = sha256 of stored bytes, never recomputed by rewriting; a writer change costs only dedupe; split/sample hashes over defined int bytes; splitmix64 in numpy; canonical JSON without floats; versions pinned in requirements and recorded in each manifest; **a startup self-check** hashes fixed vectors (a canonical manifest, a 10-row split, a sample) against constants in code and refuses to start on a mismatch (a runtime guard, not a test suite) | P02, P09, P11 |
| 4 | **Memory blowups** | a pandas full read of 10M+ rows (today: OOM above 15 GB at 10M in matching); Top-N pages; one-pass wide diffs (10 GB); upload into memory; huge xlsx | DuckDB `memory_limit` + `max_temp_directory_size` per connection; interactive semaphore of 8 + 10 s interrupt; keyset pages only, no sort above 1M rows; two-pass diff; streaming upload with a disk guard; training budget + sampling; xlsx ≤ 200 MB; the spill directory on its own volume | P02, P05, P08, P11 |
| 5 | **Concurrency** | two uploads to one root; a lease expiry runs a job twice; SQLite writer stalls (a long transaction holds the lock); default pointer races | version numbers allocated in the commit transaction; branching is allowed (a tree); idempotent commit by `upload_id` and `job_id`; dedupe by `data_sha256`; no I/O inside transactions (review rule plus grep for `with db.connect()` blocks that read files); compare-and-set on the default; Postgres at the first shared deployment | P02, P05, P22, P23 |
| 6 | **Purge breadth** | content sharing means a PII purge must reach versions in other datasets/teams; a purge breaks another team's champion | the impact report lists every reference with its team; owners of every team involved approve; all-or-nothing; a legal hold blocks; runs keep rows and report `purged` on re-verify; champions on affected versions are listed before approval | P24 |
| 7 | **PII leaks** | previews, diff samples, stats top-20, error messages, agent CSV, SHAP row, logs, kept raw sources, exports | one masked scan builder; tagged columns are never echoed in errors; stats masked when rendered; sources expire (30 days default); unmasked reads need a reason and are read-audited; export masked by default; X4 checklist | P20, X4 |
| 8 | **Permission gaps** | a new route without `require()`; a body `by` spoof; the agent acting as a service; export cache served by path; filter expressions as SQL injection | coverage gate (startup refusal in enforce); body `by` ≠ principal → 409 in enforce; agent carries the user's principal; no file paths in any API, exports are streamed; filters are a whitelisted AST with quoted identifiers, no free SQL | P19, X4, X5 |
| 9 | **Migration of existing runs** | old runs' `data_sha256`/`splits_sha256` stop verifying; dtypes change, so re-scored or explained results differ; positions shift | read as the worker did (R14); round-trip `equals` + dtype equality + CSV byte reproduction; legacy CSV and JSON blobs pinned by runs; rows kept in CSV order; X2 replays compare/verify/explain on a copy of a populated `data/` and diffs JSON byte for byte | P10, X2 |
| 10 | **S3 consistency** | an overwrite or a partial upload; a multipart left dangling; Object Lock blocking a purge; the read cache serving stale data | write-once keys + `If-None-Match` + checksum; S3 is strongly read-after-write consistent; a lifecycle rule aborts incomplete multipart uploads after 1 day; the cache is keyed by sha and verified on fill (never stale); Object Lock in **governance** mode with a purge role (compliance mode would make purge impossible: open question) | T1 |
| 11 | **Serving skew for join models** | batch tables joined differently from training (other code, other aggregation window, other types) | a single `frames.materialise` for training and scoring; the join spec and its sha frozen in the model version; batch tables checked against the spec's schemas (400 naming the missing column); **a parity check at registration** re-materialises 1 000 grain rows through the serving path from the pinned inputs and requires byte-equal values with the training frame | P14, P15 |
| 12 | Typed names give false security | users think `audit` mode protects them | the UI banner "names are not verified"; enforce refuses typed names | P19 |
| 13 | Sampling weakens models silently | big data trains on 4 % of rows | the run card and registry show `rows_used/rows_total`; budget raisable per run; the learning curve exists | P11 |
| 14 | Synthetic benchmarks mislead | real data compresses and sorts differently | the P08 and P11 gates re-measure on an anonymised real export (B8's rerun moved into P11) | P11 |

### 7.1 What we deliberately do not build, and the trigger that would change that

| Not built | Trigger |
|---|---|
| lakeFS | a writer outside the app must change lake data with isolation, or cross-dataset branch-and-merge is required |
| Iceberg / Delta | an outside engine (Spark, Trino, Snowflake, a regulator job) must read versioned tables, or tables pass ~1B rows |
| Storage delta parts (T2) | a table's versioned bytes > 20× its live size, or team quota > 80 % mostly from re-uploads |
| S3 / MinIO (T1) | a second compute host, the lake > ~1 TB or > 70 % of disk, or WORM required |
| Celery / Ray / Kubernetes jobs | autoscaling, or > ~50 workers |
| Polars | the pandas hand-off becomes the bottleneck after sampling (it is the lowest-peak option today) |
| Deletion vectors / row-level copy-on-write, Hive partitioning, Z-order | same as T2 / Iceberg |
| Free SQL for users | never in the platform; `sqlsource` stays for database sources |
| In-app SAML, Kerberos | only if no proxy and no OIDC bridge is available (C §1.6) |
| MLflow as the system of record | never (platform-eval); it stays a derived mirror |
| OpenLineage emitter | the bank's governance tool asks for it (~100 lines) |
| Per-team buckets / KMS keys | regulatory isolation between teams (`teams.store_root` exists for it) |
| Browser-presigned uploads | uploads > 50 GB become routine |
| Background compaction jobs | never: they would create versions nobody asked for |
| Postgres row-level security | a pen test or policy asks for defence in depth beyond app checks |
| Production DB connectors / streaming ingest | out of the brief (file exports only) |

---

## 8. Escalation path (B §6, adopted)

1. **Postgres + DB-backed job queue** at the first shared (non-laptop) deployment. Triggers: a second writer host;
   p99 commit > 100 ms; any `database is locked`; or the bank's HA/PITR requirement. The queue (P22) is built in the
   POC anyway.
2. **S3/MinIO** with a sha-keyed read cache when a second compute host must read blobs, the lake passes ~1 TB, or
   WORM is required.
3. **Own delete/upsert delta parts** with checkpoints at 20× versioned/live bytes.
4. **Iceberg** only for outside readers or ~1B rows. The manifest's file list becomes `{iceberg_table, snapshot_id}`;
   version rows, `__row_id`, splits and joins stay.
5. **lakeFS: rejected** for this scenario (no outside writers; a second commit graph to audit).

---

## 9. One merged phase list (sessions of about 2 h)

**Rules for every phase:**
- No smoke or integration suites.
- Verify with manual API calls chained as the UI makes them, plus `npm run build` when the UI changed.
- Commit `docs/progress/<phase>.md` with the calls and outputs.
- Every phase from P04 on re-runs the grep gate.
- Conventions: `$A` = `curl -s -H 'X-Ensemble-Actor: Ana' -H 'Content-Type: application/json' localhost:8000/api`;
  `$B` the same as Ben.
- Big files come from `python -m app.lake.seed` (a data generator, not a test).

**Lanes:**
- **D** = data core;
- **R** = readers;
- **G** = governance;
- **O** = ops;
- **U** = UI;
- **⟂** = independent read-only check (a separate session that only reads code and calls the API, and writes
  `docs/progress/X<n>.md` with findings by severity).

Phases in different lanes run in parallel **only when their "Files" columns do not overlap**. `datasets.py` and
`catalog/schema.py` are touched by one lane at a time.

### 9.0 Mapping from the source plans

- P01 = A-L0 (schema) + C1 (schema)
- P02 = A-L0 (lake) + B1 (duck.py)
- P03/P04 = C3 (reader sweep)
- P05 = A-L1 + B1 (streaming)
- P06/P07 = A-L2
- P08 = B2 + B3 (diff) + B5 (lists, lineage)
- P09 = A-L2 (splits) + DLA4 #3
- P10 = A-L3
- P11 = B4 (+ B8's real-data rerun)
- P12 = A-L5 + C5
- P13 = A-L6 + C5 (delta)
- P14 = A-L7 + C6
- P15 = C7
- P18 = C1 (API)
- P19 = C2 (+ A-L8)
- P20 = C9
- P21 = C10
- P22 = B6 (job queue)
- P23 = B5 (Postgres) + A-L10
- P24 = A-L9 (GC, purge) + C11
- U1/U2 = C12
- X1–X5 = C4/C8/C13 widened to the whole platform
- T1 = B7
- T2 = B8 (delta parts)
- T3 = C14

### 9.1 The list

| # | Lane | Goal | Files touched | Manual API verification chain | Done when | Depends on |
|---|---|---|---|---|---|---|
| **P01** | D | Schema + backfill + dependencies: §2.1 DDL (except the `ds_hash_ro` trigger, created in P10 after the hash backfill), `tm_default`, principals, `dataset_roots`, version columns, lineage CHECK rebuild; add `duckdb==2.0.1` and keep `pyarrow==24.0.0` | `catalog/schema.py`, `db.py`, `requirements.txt/.lock`, `catalog/dsets.py` (read `dataset_roots`, fall back to cards) | restart the API twice (migration idempotent: `catalog_meta` step ids unchanged on the second start); `$A/datasets` unchanged; `$A/datasets/{id}/history` shows `version_no`; `$A/projects` rows carry `team_id`; `sqlite3 … "select count(*) from principals"` = distinct actors | all existing endpoints answer as before; the backfill counts match | — |
| **P02** | D | Lake core: `lake/store.py` (LocalBlobStore), `lake/manifest.py` (canonical JSON + every §3.3 hash + startup self-check), `lake/duck.py` (B §5.1 limits, semaphore, interrupt), `lake/writer.py` (VersionWriter + commit §5.2), GC dry-run only | `backend/app/lake/*` (new), `main.py` (startup self-check), `config.py` | `GET /api/admin/lake/selfcheck` → all vectors ok; via the admin seed endpoint, write a 1M-row table version from the seed → `GET /api/datasets/{id}/manifest` (hashes, schema); `POST /api/admin/lake/gc?dry_run=1` → 0 candidates; flip a byte of the blob → `GET …/verify` mismatch | the writer commits a Parquet version that the DB lists; the self-check is wired | P01 |
| **P03** | R | `frames.py` (the typed reader, `engine_v1` mode, `resolve(cfg)`, `read_upload`) on the **CSV store**; `lake/values.py` + error mapping + `json_safe` (DLA4 #1); sweep R-A, R-B, R-C | `frames.py`, `lake/values.py`, `dataset_edit.py` (validation calls), `main.py` (handlers), R-A/R-B/R-C files | replay the 28 DLA4 cases (`POST /datasets/{id}/edit` set_cells 1e30, 1e308, 2**63, 2**64 into an int column; add_column 1.797e308; fill_missing/replace_values with them) → all 400 with codes, no "pandas" in any body; `POST /runs` on a sample → succeeded; `GET /runs/{id}/explain` shows `zip` as text (DLA4 #4); slices and fairlending the same | zero 500s on the DLA4 list; R-A/B/C files contain no direct reads | — (parallel with P01/P02) |
| **P04** | R | Sweep R-D (reads), R-E, R-F, R-G; export zip gets `schema.json`; **grep gate to zero** | R-D/R-E/R-F/R-G files | grep gate prints nothing; one run each of anomaly, forecast, survival, EL, segmented, reject, multilabel → succeeded; `POST /registry/versions/{v}/batches` with a CSV → scored; credit report and monitoring open; `GET /datasets/{id}/export?format=zip` contains `schema.json` | gate empty; every kind runs | P03 |
| **P05** | D | Parquet for new uploads: streaming `/uploads` → `/check` → `/commit` (single table), typed import §5.1, `run_inputs`, worker hash-while-read + `data_gate` on `table_sha256`, export from Parquet; `POST /datasets` as a wrapper | `catalog/datasets_api.py`, `datasets.py` (`create_from_upload`), `engine/api.py` (`queue_run`), `engine/worker.py` (`load_frames`, `data_gate`), `frames.py`, `lake/writer.py` | upload a seeded 1.15 GB CSV (10M rows) → under 60 s, RSS under 3 GB (`ps -o rss`); `/check` shows `account_no` VARCHAR "leading zeros"; `/commit` → 201; a repeat `/commit` → the same version; `POST /runs` → events `data_hashed match:true`; corrupt one blob byte → next run 409; export CSV keeps the zeros | new datasets are Parquet end to end; the old `POST /datasets` shape is unchanged | P02, P03 |
| **P06** | D | Upload-as-next-version: DuckDB matching, `key_candidates`, explicit `match`, `holdout_identity_lost`, acknowledge, warnings in the manifest (DLA4 #2); dedupe by `data_sha256` | `dataset_versions.py`, `catalog/datasets_api.py`, `lake/writer.py` | reproduce DLA4 #2: v1 with `acct` "A00000…" + 15 corrected rows → `/check` lists `acct` first (overlap 1.0) and row_hash with `holdout_identity_lost` > 0; `/commit` without `match` → 400 `match_required`; with `row_hash` and no ack → 400; with `key acct` → 201, `GET …/diff` shows 15 changed, 0 removed; holdout rows unchanged (`GET …/splits`) | no silent fallback is possible; the manifest records the decision | P05 |
| **P07** | D | Edit SQL compiler (§5.5), `derive()` contract with a pandas adapter for Doctor / Feature Lab / Autopilot / relabel / active learning; R-D writes | `dataset_edit.py`, `datasets.py` (`derive`, `create_version`), `data_doctor_api.py`, `featurelab*.py`, `autopilot/engine.py`, `engine/active_learning_api.py` | `POST …/edit` set_cells + drop_column preview → commit; a Data Doctor apply → new version, `__row_id` kept (`GET …/diff` changed only); Feature Lab adds a column → only that table rewritten (`manifest.tables[].change`); repeating the same edit → dedupe returns the existing version; the DL3 target guards still refuse | every writer goes through the VersionWriter | P06 |
| **P08** | D | Read paths: keyset pages, search, filter preview, `table_stats` (+ background profile job), two-pass diff + `diff_cache`, light paginated lists, lineage by `root_id` (no depth cap on versions) | `catalog/datasets_api.py` (grid/rows/search), `main.py` (rows, health), `catalog/dsets.py` (diff, history), `catalog/lineage.py`, `db.py` (`list_datasets`), `lake/stats.py` | at 10M rows: `…/rows?after=5000000&limit=50` < 200 ms; `?q=st7` < 500 ms; 8 parallel page calls p95 < 300 ms; `GET /datasets/diff?a=&b=` < 5 s, repeat < 50 ms; `GET /lineage/dataset/{v1}` on a 100-version chain lists all of them; `GET /datasets?limit=50` < 50 ms with 20k versions seeded | no full-table pandas read on any request path | P07 |
| **P09** | D | Splits as Parquet (§5.7), carry by join, five-check gate + restore from run pins, paired-compare overlap (DLA4 #3) | `splits.py`, `datasets.py` (`ensure_splits`), `engine/worker.py` (`data_gate`), `catalog/compare.py`, `catalog/dsets.py` (`verify`), `catalog/datasets_api.py` (restore) | reproduce DLA4 #3: replace a split blob and update `splits` → the trigger refuses; write a new blob plus a new `splits` row with the DB opened directly (simulated tamper) → `POST /runs` → 409 `split_integrity: differs from runs r1,r2`; `GET …/verify` names the check; `POST …/restore-splits` offers r1's blob; `GET /compare?a=&b=` over 50-of-180 holdouts → "paired on 50 of 180", no "same holdout" claim; second grep gate empty | DLA4 #3 is closed with evidence | P07 |
| **X1** | ⟂ | Independent check of P01–P09: row identity, match decisions, split integrity, value validation, the reader sweep, memory under the 10M seed | read-only | the checker's own chain; report `docs/progress/X1.md` | no open major findings (fixes go in a follow-up slot before P10) | P09, P04 |
| **P10** | D | Migration (§5.11) as a resumable job + `POST /api/admin/lake/migrate?dry_run=1`; hash triggers; legacy path resolver; folder rename | `lake/migrate.py` (new), `frames.py` (`resolve`), `catalog/dsets.py` (`verify_all`), `catalog/schema.py` (triggers) | **before:** record `GET /compare` for 3 old run pairs, `GET …/verify` for 5 versions, `/explain` for 1 run, all to files; dry-run → plan; run → progress → done; **after:** the same calls, `diff` of the JSON files is empty except `storage`; re-verify an old run's data hash (legacy blob) → ok; train on a migrated version → `run_inputs.read_mode engine_v1`, holdout ids identical to the old run's | every version migrated or reported; old results byte-equal | X1 |
| **X2** | ⟂ | Migration check on a **copy of a populated `data/`** (many versions, deleted/tombstoned ones, DL4 schema.json and pre-DL4 versions mixed, tampered files) | read-only | report `X2.md`: per version migrated/skipped/why; spot-check of the round trip; GC dry-run shows no live blob as a candidate | no data loss path found | P10 |
| **P11** | D | Training frame + sampling (§5.9), the per-column estimator, batched holdout scoring, **AutoGluon peak-RSS calibration** at 1M/5M (default preset), rerun of B's table on an anonymised real export | `lake/frame.py` (new), `engine/worker.py`, `engine/serving.py`, `config.py`, `docs/plans/B-scale-performance.md` (append numbers) | `POST /runs {dataset_id, label, frame_budget_mb:300}` → `GET /runs/{id}` shows `rows_used ≈`, `rows_total`, `sample_sha256`, holdout metrics on every row; repeat → the same `sample_sha256`; record peak RSS per preset into config | the budget rule is calibrated; sampling is recorded | P09 (parallel with P10: disjoint files) |
| **P12** | D | Multi-table + Excel (calamine) + plan PATCH + carry/append/rewrite per table + keys & relations + the multi-table sample | `datasets.py` (`read_uploads`), `catalog/datasets_api.py`, `lake/writer.py`, `profiling.py`, `samples.py`, `requirements` | upload `book.xlsx` (3 sheets) + `bureau.csv` → `/check` 4 tables → `/commit`; `GET …/versions/{v}` lists 4 tables and shas; upload only `payments` as append → manifest: 3 carried (same sha) + 1 appended; `PUT …/tables/bureau/key`; `GET …/relations` with orphan counts; `POST /runs {dataset_id, table:"applications"}` → succeeded | multi-table versions work end to end | X2 |
| **P13** | D | Deltas (§5.6) | `dataset_versions.py`, `lake/writer.py`, `catalog/datasets_api.py` | a delta with `_op` → `/check` inserted/updated/deleted/missing; commit; `GET …/diff v(n-1) v(n)` equals the delta counts; a duplicate-key delta → 400 listing keys; an insert-only delta → `change:'appended'`, bytes written ≈ delta size | | P12 |
| **P14** | D | Saved joins + join frames + run on a join (§5.8): validation, `materialise`, cache, grain assert, `run_inputs` join rows, `joined_from` edges, usage/blockers follow joins | `catalog/joins.py` + `joins_api.py` (new), `frames.py` (`materialise`), `engine/api.py`, `catalog/dsets.py` (usage, blockers), `catalog/lineage.py` | `POST /datasets/{root}/joins` with 1:n and no aggregate → 400; fixed → coverage; `POST …/versions/{v}/frames {join_id}` → rows = grain rows, repeat `cached:true`; `POST /runs {dataset_id, join_id, label}` → succeeded; `GET /compare` table-run vs join-run → paired; request delete of the bureau version → blocked by the run | | P12 (parallel with P13: disjoint files except `lake/writer.py`, untouched by P14) |
| **P15** | D | Registry/batches/monitoring for join models; the model-risk input record (C §4.6); the **serving parity check** at registration; multi-file batch scoring | `registry/build.py`, `registry/card.py`, `registry/scoring.py`, `registry/api.py`, `registry/monitoring_api.py` | register the P14 run → `/card` shows tables, join sha and parity `ok`; `POST /registry/versions/{v}/batches` with 3 files → scored; drop a column → 400 naming it; `/monitoring` works; `GET …/inputs.zip` holds the manifest + spec + hashes, no rows | serving and training share one join path, proven at registration | P14 |
| **X3** | ⟂ | Check P10–P15: delta edge cases (update of a missing key, delete + insert of the same key), fan-out guard, skew, sampling determinism, multi-table carry | read-only | `X3.md` | no open major findings | P15 |
| **P18** | G | Teams/principals/members API, `/me`, team on every list row, project creation in a team (C1 API) | `catalog/teams_api.py` (new), `catalog/api.py`, `main.py` | `$A/me` → `tm_default`; `POST /teams {name:"Credit Risk"}` → 201, Ana owner; `POST /teams/{t}/members {name:"Ben",role:"viewer"}`; `$A/projects` and `$A/datasets` rows carry `team` | | P01 (parallel with P02–P09: disjoint files) |
| **P19** | G | `authz/` package, `TypedNameProvider`, `require()` on every data/registry/champion/retention/admin route, `/authz/coverage`, `/authz/check`, hash-chained audit with the new columns, agent principal; mode `audit` by default | `authz/*` (new), every `*_api.py` router (decorators only), `catalog/schema.py` (`audit()`), `agent/tools.py` | Ben (viewer) `POST /datasets/{id}/edit` → 201 + header `would-deny data.version`; `GET /teams/{t}/audit?action=authz_would_deny` shows it; `/authz/coverage` → 0 uncovered; `ENSEMBLE_AUTHZ=enforce` → refuses to start (typed); with `ALLOW_TYPED=1` → Ben 403 `{needed:'member'}`; `GET /teams/{t}/audit/verify` → ok | every route is covered | P18 (do not run in parallel with a lane editing the same routers: schedule after P09, or route-file by route-file) |
| **P20** | G | PII tags, suggestions, the masked `frames.scan`, unmasked reads with reason + read audit, export masking, stats masking | `lake/mask.py` (new), `frames.py` (`scan`), `catalog/datasets_api.py` (rows, export), `agent/tools.py`, `engine/explain.py` (SHAP row), `profiling.py` | upload with `ssn` → suggested `pii_direct`; Ben's `…/rows` shows `•••`; Ana with `unmasked=1&reason=QA` sees values and the audit has `dataset_rows_read`; export zip masked; diff sample masked; an invalid `set_cells` on `ssn` → 400 without the value | | P08, P19 |
| **P21** | G | Contracts, certifications, shares, `visible_teams()`, cross-team training into the grantee's project, `require_certified_inputs` | `catalog/contracts.py`, `catalog/certify.py`, `catalog/shares.py` (new), `catalog/datasets_api.py` (check step), `engine/api.py` (project rule), `registry/api.py` | freeze a contract from v1; v2 with a removed column (block) → `/check` blocks, `/commit` 409; Ana requests certification, Ana deciding → 409, Ben (owner) → active; share to Collections (`certified`); Carl sees only v1, trains into their own project → 201, edits → would-deny / 403 in enforce | | P20, P12 |
| **P22** | O | Durable job queue + `workerd` (B §5.5): runner, heavy pool, review, Feature Lab, active learning, learning curve; leases, janitor, fair share | `jobs/queue.py`, `engine/workerd.py` (new), `engine/runner.py`, `engine/jobs.py`, `engine/pool.py`, `vela/review.py`, `agent/loop.py`, `featurelab.py`, `engine/active_learning_api.py`, `engine/learning_curve_api.py` | start the API + 2 workers; POST 4 runs → 2 leased at once (`GET /jobs`); `kill -9` one worker → its job re-queued within 90 s and leased by the other; start a review, restart the API → it continues | no work dies with the API process | P01 (parallel with lane D after P09; it touches `engine/worker.py` callers only) |
| **P23** | O | Postgres dialect + pool + copy tool; `IS DISTINCT FROM`; §2.2 triggers and grants | `db.py`, `catalog/*.py` (portable SQL), `lake/migrate_pg.py` (new) | start on Postgres; chain: create team → project → upload → next version with match → edit → run → register → `GET /teams/{t}/audit/verify`; two concurrent uploads to one root → versions n and n+1; repeat the chain on SQLite | both engines pass the same chain | P10, P22 |
| **P24** | G | Retention per team, legal holds in every destructive path, GC armed, purge with the cross-team impact report (§5.10) | `catalog/retention.py`, `catalog/dsets.py` (`request_delete`, `decide_delete`), `lake/gc.py`, `catalog/holds.py` (new) | hold on a project → `/retention/plan?team=` 0 candidates there, delete decide → 409 naming the case; delete v7 whose bureau table is carried into v8 → GC dry-run does not list the shared part; purge `ssn` → new version + an impact report listing the Collections fork; approval by both owners → blobs purged, old run re-verify says `purged` | | P10, P21 |
| **X4** | ⟂ | Governance + authz pass: every route in `/authz/coverage` × the C §1.3 matrix, body-`by` spoofs, agent privilege, masking leaks (holdout predictions, SHAP row, agent CSV, error echoes, stats), purge breadth | read-only | `X4.md` | no open major findings | P24 |
| **U1** | U | Team switcher, Build › Data catalog, upload → check (**match confirmation with counts**) → commit, per-table tabs | `src/features/build/*`, `src/features/models/data/*`, `src/shared/api/catalog.ts` | `npm run build`; record the UI's API calls for upload → check → commit → run in `U1.md` | | P06, P18 |
| **U2** | U | Per-table diff, join builder, lineage tree with join frames, governance screens (members, holds, certifications, shares, PII tags) | `src/features/*` | `npm run build`; same recording for join → frame → run → register | | P15, P21, U1 |
| **X5** | ⟂ | Whole-system final pass: DLA4's four items re-run; migration replay; scale gates on the pinned stack; grep gates | read-only | `X5.md` | approval to call the platform done | all |

(The numbering skips P16/P17 so that the G lane keeps its own block; there are 29 sessions in total.)

**Triggered sessions** (not counted above; built only when §8's trigger fires):
- **T1** S3/MinIO + read cache + bundled httpfs (B7 verification);
- **T2** storage delta parts + checkpoints (B8);
- **T3a/T3b** SSO (`TrustedHeaderProvider`, `OIDCProvider`, group sync, service tokens) + the drop-folder watcher (C14), then `ENSEMBLE_AUTHZ=enforce`.

### 9.2 Count and wall-clock

| | Sessions | Hours (≈ 2 h each) |
|---|---|---|
| Build sessions (P01–P15 without the gaps, P18–P24, U1–U2) | 24 | 48 |
| Independent checks (X1–X5) | 5 | 10 |
| **Total, serial** | **29** | **≈ 58 h** |

**Critical path:** P01 → P02 → P05 → P06 → P07 → P09 → X1 → P10 → X2 → P12 → P14 → P15 → X3 → U2 → X5 = **15 sessions ≈ 30 h**,
plus about 10 % merge and fix overhead ≈ **32–36 h wall-clock with 3 lanes**:
- **D** carries the critical path;
- **R + O** runs P03, P04, then P22, then P23;
- **G + U** runs P18, then P19 after P09, P20, P21, P24, U1, U2.

Triggered work adds 4 sessions (≈ 8 h) when needed.

**Parallelism constraints:**
- P03/P04 touch only reader call sites. P05 waits for P03.
- P19 adds decorators across routers: schedule it after P09 (lane D quiet on `*_api.py`) or split it by router.
- P11 runs in parallel with P10.
- P13 runs in parallel with P14.
- U1 can start once P06 and P18 are done.

---

## 10. Open questions for the user

1. **Identity provider for later:** Entra ID / Okta / Keycloak via OIDC, or a reverse proxy (SAML/Kerberos)? This
   decides T3a and when `enforce` can be switched on.
2. **Retention periods** (model-risk/legal must approve):
   - versions (default 730 d, keep min 5);
   - **kept raw upload sources** (default 30 d: they hold unmasked PII);
   - runs (slim 90 d / evict 365 d);
   - audit (forever? archived to Parquet after N months on Postgres).
3. **Is S3/MinIO in scope for the POC?** If yes, T1 moves into the list after P10 (+1 session). **Object Lock:**
   governance mode (purge possible with a role) or compliance mode (purge impossible)?
4. **Postgres for the pilot?** B recommends it from the first shared deployment; P23 is planned either way.
5. **Native types for training:** when may consumers move from `engine_v1` (CSV-equivalent dtypes) to native dates
   and Arrow types? It changes AutoGluon features, so new runs are not directly comparable with old ones. Per
   project, or globally after a review?
6. **Sampling policy:** is a recorded, stratified train-row sample acceptable to model risk for production models,
   or must production fits use full data on a larger worker?
7. **Cross-team sharing in the POC**, or a single team plus `tm_default` only? (P21 shrinks without it.)
8. **Package approvals:** `duckdb` and `python-calamine` are new dependencies. Are they allowed by the bank's package
   review (fallback: openpyxl for Excel)?
9. **Per-team storage isolation** (separate buckets/KMS keys)? It disables cross-team dedupe; `teams.store_root`
   allows it.
10. **Purge sign-off:** is the owner of every affected team plus four eyes in the requesting team enough, or must
    compliance (a separate principal) also approve?
11. **Defaults to confirm:**
    - NA tokens;
    - ambiguous dates stay text;
    - `holdout_identity_loss` acknowledge threshold 1 %;
    - training frame budget 10 % of worker RAM;
    - upload cap 5 GB.
