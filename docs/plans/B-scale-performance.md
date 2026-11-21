# Plan B: scale, performance and the escalation path

Lens: how far Parquet + DuckDB + our metadata DB goes, where today's code breaks first, and the measured trigger for
each heavier component. This plan **builds on Plan A** (manifests, content-addressed blobs, `__row_id`, split
Parquet files) and **Plan C** (teams, frames, consumers), and does not repeat their schemas. Where a number here
differs from theirs, the reason is given. Everything marked *measured* ran in this container. Everything marked
*estimate* says what it is derived from.

**Bench box:** 4 vCPU, 15 GB RAM, local SSD, warm page cache.
**Versions:** DuckDB 2.0.1, Polars 2.1.0, pyarrow 26.0.1, pandas 3.1.1, PostgreSQL 16.15, Python 3.13.
**Synthetic loan table** (22 columns):
- `loan_id` BIGINT and `account_no`, a 10-char string with leading zeros;
- 1 date column;
- 12 int columns and 6 float columns (one of them with 10 % NULL);
- 4 low-cardinality string columns;
- a 0/1 target.

**v2 of each table:**
- 1 % of rows have `income` changed;
- 0.5 % of rows deleted;
- 1 % new rows.

A second table, `bureau`, has N rows on a random `loan_id`. Values are hash-random, so they compress worse than real
data. A measured 0.35× of CSV on its table; we measure 0.26×. Every operation ran in its own process, and peak
memory is the process's max RSS.

---

## 0. Summary

1. **Parquet + DuckDB on one host covers the POC envelope with room to spare.** At 10M rows, every interactive data
   operation takes 0.04–2.3 s:
   - preview, page, profile one column, diff, join, search.

   At 50M rows they take 0.07–12 s. A per-query `memory_limit` keeps each one at 2 GB or less.
2. **Today's code breaks long before storage does.** Measured at 10M rows:
   - the dataset page/search endpoint takes **50 s and 4.4 GB** (DuckDB: 0.08 s, 245 MB);
   - the `sha256(to_csv(df))` dedupe hash takes **61 s and 5.5 GB** (hashing the written file: 0.27 s);
   - JSON `root_rows` takes 1.4 s and 1 GB to read; at 50M rows it is 489 MB on disk, 8 s and 4.8 GB.

   These are the first fixes, and they come before any platform change.
3. **The training hand-off is the real ceiling.** AutoGluon needs a pandas frame:
   - about **9 bytes per cell**;
   - load peaks at about **1.7× the frame**.

   Above a per-worker budget, a run trains on a **deterministic, stratified hash sample** that it records (2.4–4.4 s
   to draw 2M rows from 50M) and scores the full holdout in streamed batches.
4. **Metadata volume is not why we would move to Postgres.** SQLite answers every indexed list, lineage and audit
   query in under 1 ms at 10k datasets, 100k versions, 1M edges and 5M audit rows. Postgres is needed for:
   - **concurrent writers:** SQLite's p99 commit goes from 5 ms to 730 ms at 32 writers, with 3–4 s stalls, while
     Postgres stays at 24 ms;
   - **more than one host;**
   - **full-scan audit reports** (6× faster on Postgres);
   - **bank operations:** HA, point-in-time recovery (PITR), roles.
5. **Escalation order for the user's scenario** (multi-team, multi-table, file exports only, a bank):
   1. Postgres plus a DB-backed job queue with leases, at the first shared deployment;
   2. S3 or MinIO for blobs, with a sha-keyed local read cache, once there is more than one compute host or the lake
      outgrows local disk;
   3. our own delete-and-upsert delta parts in the manifest, with checkpoints, once versioned bytes per table reach
      about 20× its live size;
   4. Iceberg, only when outside engines must read the tables or tables pass about 1B rows;
   5. lakeFS, only if something other than this app writes to the lake. It does not fit our scenario.

---

## 1. Target scale envelope

| Dimension | POC (one host, SQLite, local disk) | Tier 2 (Postgres, S3/MinIO, API + N workers) | Beyond (escalation path) |
|---|---|---|---|
| Teams | 10 | 50–200 | — |
| Projects per team | 20 | 50 | — |
| Datasets (all teams) | 2 000 | 10 000–50 000 | — |
| Versions per dataset | ≤ 100 (typ. 5–20) | ≤ 1 000 (typ. 10) | — |
| Versions in total | 20 000 | 100 000–500 000 | — |
| Tables per version | ≤ 20 | ≤ 50 | — |
| Rows per table | ≤ 10M typical, 50M max | ≤ 100M | > 1B → Iceberg |
| Columns per table | ≤ 500 (1 000 with column pruning) | ≤ 1 000 (training through pruning or sampling) | wide > 5 000: out of scope |
| Uploaded file size | ≤ 5 GB (streamed to disk) | ≤ 50 GB (multipart / drop folder) | — |
| Runs | 10 000 | 100 000+ | — |
| Registered model versions | 1 000 | 10 000 | — |
| Lineage edges | 200 000 | 1M–10M | — |
| Audit rows | 1M | 5M–50M (monthly partitions) | archive to Parquet |
| Concurrent UI users | 20 | 200 | — |
| Concurrent training jobs | 1–2 (`cores/4`, RAM-bound) | 8–32 across workers | — |
| Concurrent DuckDB jobs | 4 × 2 GB | per-worker `cores/2` | — |
| Lake size | ≤ 1 TB local | 10–100 TB object store | — |

**Org-level storage estimate** for Tier 2, 10 000 datasets with an average of 10 versions:

| Share of datasets | Rows | Average Parquet size |
|---|---|---|
| 80 % | under 1M | 10 MB |
| 18 % | 1–10M | 100 MB |
| 2 % | 10–100M | 1.5 GB |

- Full copy of each changed table: about **5.6 TB**.
- With delta parts on the 2 % (section 3): about **3 TB**.
- Today's CSV copy of every version: about **20 TB**.

That puts Tier 2 on an object store, not local disk.

**In scope for the POC**, on one host:
- everything in the POC column;
- the streaming fixes (section 4);
- sampling for training;
- SQLite with short transactions;
- a DB-backed job queue on one host.

**Needs the escalation path:**
- more than one API or worker host (Postgres plus object store);
- more than about 16 concurrent writers;
- tables over 100M rows with frequent small changes (delta parts, then Iceberg);
- readers outside the app (Iceberg).

---

## 2. Benchmarks (measured)

### 2.1 DuckDB over Parquet

Each cell shows time / peak RSS.

| Operation | 1M | 10M | 50M |
|---|---|---|---|
| **Profile one column**: count, nulls, min/max/mean/std, 5 quantiles, distinct; top-20 of a category; histogram | 0.06 s / 103 MB | 0.38 s / 125 MB | 1.83 s / 129 MB |
| Profile all 22 columns (`SUMMARIZE`) | 1.15 s / 184 MB | 10.3 s / 277 MB | 45.9 s / 292 MB |
| **Diff by key**: added / removed / changed counts, per-row hash of non-key columns | 0.25 s / 233 MB | 1.42 s / 760 MB | 6.8 s / 2.4 GB (2 GB cap: 9.6 s / 2.2 GB) |
| Diff, per-column change counts, one pass over all columns | 0.70 s / 548 MB | 2.32 s / 2.2 GB | **18.7 s / 10.0 GB** (2 GB cap: 56 s) |
| Diff, per-column, **two-pass**: hash pass, then cell compare for changed keys only | 0.52 s / 388 MB | 2.10 s / 784 MB | **11.8 s / 2.5 GB** |
| **Diff by row hash**: no key, multiset (`GROUP BY hash` + full join) | 0.38 s / 291 MB | 2.28 s / 1.1 GB | 10.1 s / 4.2 GB |
| **Two-table join** + group-by (`loans ⋈ bureau`) | 0.15 s / 159 MB | 0.69 s / 541 MB | 8.4 s / 1.9 GB (4.6 s on a re-run) |
| Join, materialised to Parquet (training frame) | 0.73 s / 445 MB | 5.3 s / 1.1 GB | 27.5 s / 2.8 GB (2.3 GB file) |
| **Filter-preview**: first 100 rows `WHERE fico > 800 AND state = 'ST7'` | 0.03 s / 146 MB | 0.06 s / 180 MB | 0.07 s / 189 MB |
| Page 100 rows at offset N/2 (no sort) | 0.02 s | 0.04 s | 0.08 s |
| Top-100 by a column (`ORDER BY income DESC LIMIT 100`) | 0.06 s | 0.46 s | 1.6 s |
| Search every column (`concat_ws(...) ILIKE '%st7%'`), first 50 hits | 0.07 s | 0.08 s | — |
| Search, count all hits | 0.35 s | 2.88 s | 15.4 s / 352 MB |
| **sha256 streamed** (1 MiB chunks) of the Parquet file | 29 MB: 0.03 s | 294 MB: 0.27 s | 1.47 GB: 1.41 s, **80 MB RSS** |

- **sha256 of 1 GB:** about **1.0 s** (1.05 GB/s), in constant memory.
- **Cold reads** from network disk or S3 are I/O-bound instead. Budget 100–500 MB/s; not measured, since there is no
  bucket here.
- **Per-column diff:** the two-pass form uses a quarter of the memory of the one-pass form at 50M rows. Use it (section 5.3).
- **Profiling all columns** of a 50M-row table costs 46 s. Profiles are computed once per table sha, at commit, and
  cached (section 5.2). No request ever profiles.

### 2.2 CSV vs Parquet sizes

| Rows | CSV | CSV gzip -1 | Parquet zstd (DuckDB, 122 880-row groups) | Ratio |
|---|---|---|---|---|
| 1M | 113.9 MB | — | 29.5 MB | 0.26× |
| 10M | 1 148.8 MB | 513.5 MB | 294.2 MB | 0.26× |
| 50M | about 5.7 GB (estimate) | — | 1 470 MB | 0.26× |

The CSV path also loses types. Measured: `pd.read_csv` turns `account_no` `"0000000001"` into int64 `1`.

### 2.3 Building the DataFrame AutoGluon needs (10M rows × 22 columns; 1M in brackets)

| Path | Time | Peak RSS | Frame |
|---|---|---|---|
| `pd.read_csv` (today's path) | 10.4 s (3.1 s) | 4.48 GB (594 MB) | 1.99 GB, **wrong types** |
| `pd.read_parquet` (pyarrow) | 5.2 s (1.1 s) | **3.30 GB** (542 MB) | 1.95 GB |
| DuckDB `.df()` | 11.9 s (1.35 s) | 5.39 GB (798 MB) | 1.63 GB |
| DuckDB `.to_arrow_table().to_pandas()` | 3.2 s (0.76 s) | 4.84 GB (702 MB) | 1.96 GB |
| Polars `read_parquet().to_pandas()` | **1.9 s** (1.45 s) | 4.49 GB (701 MB) | 1.63 GB |
| Polars only (no pandas) | 0.9 s (0.18 s) | 1.81 GB | 1.20 GB |
| `pd.read_parquet(columns=4 of 22)` | **0.58 s** | **768 MB** | 250 MB |

At 50M rows:

| Path | Time | Peak RSS | Result |
|---|---|---|---|
| Full frame | not run | — | about 9.8 GB frame + 1.7× peak: does not fit 15 GB |
| `pd.read_parquet` of 4 columns | 2.3 s | 2.7 GB | — |
| `USING SAMPLE reservoir(2M)` | **71 s** | 1.7 GB | 2M rows |
| **DuckDB hash sample** `WHERE hash(__row_id, seed) % 10⁶ < k` | **4.4 s** | 1.37 GB | 2.0M rows, 326 MB frame |
| Polars lazy hash sample | 2.4 s | 1.21 GB | 2.0M rows |

**Choice:** `pd.read_parquet(path, columns=…)`, which has the lowest peak and is already a dependency. The sample is
selected in DuckDB by `__row_id` hash and written to a temp Parquet file, then read with pandas. Polars is not
added: it is faster but has a higher peak, and it would be one more dependency.

**Training-memory ceiling:**
- Frame ≈ 9 bytes per cell. Numbers are 8 B; strings are Arrow-backed, so low-cardinality strings cost about the same.
- Load peak ≈ 1.7× the frame.
- AutoGluon's own copies during feature generation, bagging and model fits were **not measured here**. AutoGluon is
  not in the scratch venv; B4 measures it on the real worker.
- Planning rule until then: **frame ≤ 10 % of worker RAM**.

| Worker RAM | Frame budget | Examples |
|---|---|---|
| 16 GB | 1.6 GB ≈ 180M cells | 8M × 22, 1.8M × 100, 180k × 1 000 |
| 64 GB | 6.4 GB ≈ 700M cells | 10M × 70, 700k × 1 000 |

**Above the ceiling**, in order (algorithm in section 5.4):
1. Prune columns: excluded by the contract, constant, ID-like, or over 99 % null.
2. Draw a stratified hash sample of the **training rows only**.
3. Cap the holdout and score it in streamed batches of 500k rows, so metrics stay on every holdout row.

The run records what it trained on. Nothing is silent.

### 2.4 Metadata: SQLite vs Postgres

**Scale:** 50 teams, 1 000 projects, 10 000 datasets (one with a 1 000-version chain), 100 000 versions,
300 000 `version_tables` rows, 200 000 runs, **1 000 000 lineage edges** and **5 000 000 audit rows**.
- Same DDL and indexes on both engines.
- Load: 43 s on each (SQLite 1.25 GB, Postgres 1.18 GB).
- Single connection; p50 / p95 over 50 random keys.

| Query | SQLite p50 / p95 | Postgres p50 / p95 |
|---|---|---|
| Team datasets page (join latest version, 50 rows) | 0.27 / 0.57 ms | 0.54 / 1.17 ms |
| Project datasets | 0.02 / 0.02 ms | 0.10 / 0.16 ms |
| Versions of a dataset (50) | 0.02 / 0.03 ms | 0.09 / 0.17 ms |
| Versions sharing a table file (by sha) | 0.01 / 0.02 ms | 0.10 / 0.20 ms |
| Runs of a project page | 0.13 / 0.18 ms | 0.17 / 0.50 ms |
| Lineage down from a typical version (today's CTE) | 0.37 / 0.89 ms | 0.63 / 1.62 ms |
| Lineage up from a run | 0.09 / 0.31 ms | 0.25 / 0.65 ms |
| Lineage down, 1 000-version chain, depth ≤ 64 (today's cap) | 4.1 ms, **694 nodes (truncated)** | 3.2 ms |
| Same, depth ≤ 1 000 | 68 ms, 10 904 nodes | 56 ms |
| Audit by target (100 latest) | 0.21 / 0.36 ms | 0.82 / 0.97 ms |
| Audit, team latest 100 | 0.29 / 0.36 ms | 0.60 / 1.48 ms |
| Audit, actor in a month (count) | 0.01 / 0.87 ms | 0.19 / 0.87 ms |
| **Audit, full aggregate over 5M rows** | **1 854 ms** | **288 ms** (parallel scan) |
| `SELECT * FROM datasets` (today's `list_datasets`, without the profile JSON) | 14 ms | 11 ms |

**Concurrent writers.** Separate processes, 8 s per level. Each transaction:
- inserts a version, 3 `version_tables` rows, 1 edge and 1 audit row;
- updates `datasets.latest_v`.

SQLite runs WAL, default `synchronous`, `timeout=30`, `BEGIN IMMEDIATE`. Postgres runs with `synchronous_commit=on`.

| Writers | SQLite txn/s | SQLite p50 / p99 / max | Postgres txn/s | Postgres p50 / p99 / max |
|---|---|---|---|---|
| 1 | 1 419 | 0.6 / 5.3 / 52 ms | 698 | 1.4 / 2.7 / 69 ms |
| 4 | 1 475 | 0.5 / 5.9 / **4 341 ms** | 3 102 | 1.2 / 3.2 / 33 ms |
| 16 | 1 541 | 0.5 / **231** / 3 240 ms | 3 860 | 3.8 / 10.3 / 55 ms |
| 32 | 1 528 | 0.5 / **732** / 3 437 ms | 3 700 | 7.8 / 23.9 / 53 ms |

What the numbers say:
- **Throughput.** SQLite's is flat: one writer at a time. Its tail grows with the number of writers, and a single
  slow transaction stalls every writer for seconds.
- **Reads** are not a reason to move. SQLite wins single-connection point queries.
- **Locked errors.** No `database is locked` errors appeared, only because `timeout=30` hides them. Any transaction
  held across file I/O or a pandas step turns into lock waits for every other request.
- **Postgres** scales writes to about 4k txn/s with p99 under 25 ms.
- **Lineage depth.** The depth-64 cap in `catalog/lineage.py:13` already **truncates** datasets with more than 64
  versions (section 4.6).

### 2.5 Today's code paths, measured at scale

| Path | 1M | 10M | 50M |
|---|---|---|---|
| Dataset page with `q=` (`catalog/datasets_api.py:262-280`): full `load_frame` + `str.contains` on every column | 4.6 s / 560 MB | **50 s / 4.4 GB** | — |
| The same in DuckDB (first 50 hits) | 0.07 s / 213 MB | 0.08 s / 245 MB | — |
| Dedupe hash `sha256(df.to_csv())` (`datasets.py:651, 706, 804`) | 6.1 s / 734 MB | **61 s / 5.5 GB** | — |
| sha256 of the written Parquet | 0.03 s | 0.27 s | 1.4 s |
| `root_rows` as JSON in `lineage.json`: size; write / read | 7.9 MB; 0.05 / 0.12 s | 89 MB; 0.8 / 1.4 s, 1.0 GB | **489 MB; 4.6 / 8.0 s, 4.8 GB** |
| `root_rows` as `.npy`: size; mmap open / full read | 8 MB; < 1 ms / 0.01 s | 80 MB; < 1 ms / 0.18 s | 400 MB; < 1 ms / 1.7 s |
| `__row_id` column in the Parquet (Plan A) | ≈ 0 extra (sorted ints, delta-encoded) | | |

### 2.6 Concurrency of interactive reads (one process, one shared DuckDB, cursor per thread, 2 GB cap, 10M rows)

| Read | 1 user | 8 users | 32 users |
|---|---|---|---|
| Filter preview (`WHERE state = ? LIMIT 100 OFFSET ≤ 2000`) | 30 ms p50 | 52 q/s; p50 139 / p95 238 ms | 54 q/s; p50 559 / p95 817 ms |
| **Key-range page** (`WHERE __row_id BETWEEN x AND x+49`; row-group pruning) | **24 ms** | 132 q/s; p50 50 / p95 91 ms | **164 q/s; p50 168 / p95 293 ms** |
| Top-N page (`WHERE id >= x ORDER BY id LIMIT 50`) | 879 ms | p50 8.8 s / p95 27 s | **out of memory at 2 GB, and the spill filled 20 GB of disk** |

Lessons, which become rules in section 5.1:
- Pages are **key ranges**, never `ORDER BY … LIMIT` over a whole table.
- Every DuckDB connection gets a `memory_limit` **and** a temp-directory size cap.
- One API process saturates at about 50–160 interactive queries per second on 4 cores.

---

## 3. Storage cost model

One 10M-row table: 294 MB Parquet, 1.15 GB CSV. Each version changes 1 % of rows, deletes 0.5 % and adds 1 %.
Measured delta files: **upserts 5.9–6.2 MB + deletes 0.09 MB per version**.

| Model | Bytes per version | 100 versions | Read cost (10M rows) | Notes |
|---|---|---|---|---|
| Today: full CSV copy | 1.15 GB | **115 GB** | CSV parse 10 s | types lost |
| Full Parquet copy of the changed table (Plan A default) | 294 MB | **29.4 GB** | 0.05 s scan; 5.2 s to pandas | simplest; file sha = table identity |
| + shared unchanged tables (multi-table) | only the changed table | see the example below | same | Plan A's content addressing |
| Insert-only delta parts (Plan A §3.7) | 1 % new rows ≈ 3 MB | 0.6 GB | +1 file per version; compact at 16 parts | append-only extracts only |
| **Upsert + delete delta parts, checkpoint every 20 versions** | 6.3 MB (+ 326 MB per checkpoint) | **2.5 GB** (0.9 GB with no checkpoints) | merge-on-read of 10 deltas: **0.58 s** vs 0.05 s compacted; compaction 4.2 s | section 5.6; the escalation step before Iceberg |

**Multi-table example** (`applications` 294 MB + `bureau` 85 MB at 10M rows; 100 versions, 80 change `applications`,
20 change `bureau`):

| Model | Total |
|---|---|
| Full copy of both tables per version | 37.9 GB |
| Shared unchanged tables | 25.9 GB |
| Delta parts with checkpoints on both | about 3.5 GB |

**Delta rewrite cost:**
- **The cost is storage, not compute.** A full rewrite of 50M rows took 19.5 s (`v2.py`). The estimate for 100M rows
  is about 40–60 s. That is far below Plan A's estimate of 5–10 min, so rewriting is never too slow for a
  human-paced version. What grows is storage: 100 versions × 3 GB = 300 GB for one 100M-row table.
- **Uploads are full extracts.** In the user's scenario every upload is a full extract, so a full copy stores the
  whole table even when 1 % changed. Diff-by-key computes the delta of a full extract against its parent in 1.4 s at
  10M rows. Storing that delta instead of the full file cuts storage by about 47× per version, at a read cost of
  under 1 s per 10 deltas. That is the trigger in section 6.3.

---

## 4. Bottlenecks in today's code at that scale, and the fix for each

| # | Bottleneck | Where (current line numbers) | At 10M rows | Fix (phase) |
|---|---|---|---|---|
| 4.1 | **Full-table loads per request.** `load_frame` = `pd.read_csv` of the whole file; 30 call sites in 15 files. | `datasets.py:394`; page/search `catalog/datasets_api.py:272`; diff `catalog/dsets.py:285` (both frames); freeze-holdout `catalog/dsets.py:639`; Data Doctor, Feature Lab, the kind APIs (A's appendix A worklist) | 10 s and 4.5 GB per call; a page with search 50 s | DuckDB reads behind `lake.read`: page, search, preview, column stats, diff (B2/B3). Consumers that truly need pandas use `load_training_frame(columns=…)` (B4). |
| 4.2 | **Hashing by re-serialising.** `sha256(FIO.to_csv_bytes(df))`, plus `df.to_csv` → `pd.read_csv` round trips to "see what will be read back". | `datasets.py:651, 706, 804` (hash); `:482-485, :503, :536, :890` (round trips) | 61 s and 5.5 GB per hash; preview + commit hash twice | Hash the Parquet file as it is written (`file_sha256`, 0.27 s) plus Plan A's logical `data_sha256` (DuckDB sum of row hashes, 1.1 s). Round trips are gone, because Parquet keeps types (B1). |
| 4.3 | **`root_rows` / `parent_rows` as JSON lists** in `lineage.json`; per-row Python loops in `derive`. | `datasets.py:607` (`root_rows = [int(...) for p in rmap]`), `:620` (fold loop); readers `catalog/compare.py:47-62`, `autopilot/engine.py`, `vela/experiments.py`, `engine/annotations*.py`, `catalog/schema.py` | 89 MB of JSON, 1 GB to read; Python loops of about 10M iterations | `__row_id` column (Plan A §2.3); splits as Parquet `(__row_id, fold)`, carried by a SQL join (Plan A §2.4). `root_info()` returns a memory-mapped `__row_id` column read with `pd.read_parquet(columns=['__row_id'])` (0.1 s). No JSON row lists anywhere (B3). |
| 4.4 | **Profiling a whole pandas frame** on every create; `profile_frame` is Python per column. | `datasets.py:378, 421, 540` → `profiling.py:157` | seconds to minutes; 46 s even in DuckDB for 50M × 22 | `table_stats` cached by table sha (section 5.2). One DuckDB pass at commit, as a background job for tables over 5M rows; the UI shows "profiling…" (B2). |
| 4.5 | **Unpaginated lists with the profile JSON.** `SELECT * FROM datasets` parses every profile; `/api/health` counts datasets that way. | `db.py:list_datasets`; `main.py:178, 186`; `featurelab_api.py:124` (counts versions by scanning all datasets) | 14 ms at 10k rows without profiles; with 10–500 KB of profile JSON each, hundreds of MB per call | Light list queries with keyset pagination (`WHERE (created_at, id) < (?, ?) LIMIT 50`); `COUNT(*)`; `profile` only on the detail route (B5). |
| 4.6 | **Lineage CTE walks version chains**, capped at depth 64. | `catalog/lineage.py:13-31` | 4 ms, but **truncates** after 64 versions; 68 ms uncapped at 1 000 | Versions of one dataset come from `dataset_versions WHERE dataset_id = ?` with no recursion. The CTE recurses only over cross-object edges (version→run→branch→model, dataset→dataset across roots). Depth is then the number of derivation hops, not the number of versions (B5). |
| 4.7 | **In-process loops and jobs that die with the API process**: review loops and agent sessions as asyncio tasks; Feature Lab threads; active-learning and learning-curve `Popen` dictionaries. | `vela/review.py:74, 1002`; `agent/loop.py:415`; `featurelab.py:70, 585`; `engine/active_learning_api.py:29`; `engine/learning_curve_api.py:24` | — | One durable `jobs` table with leases (section 5.5). A review becomes a job whose steps call the same routes; its journal is already on disk, so a lease expiry resumes from the last step (B6). |
| 4.8 | **The SQLite single writer.** One connection per call, WAL, `timeout=30`. | `db.py:129-140`, every `with db.connect()` | p99 commit 230–730 ms at 16–32 writers; 3–4 s stalls | Short transactions only: no file I/O or pandas inside `with connect()`. `PRAGMA synchronous=NORMAL` (safe in WAL). `ENSEMBLE_DATABASE_URL=postgresql://…` through a thin dialect layer (B5; trigger in section 6.1). |
| 4.9 | **The job runner is one host**: an in-memory `queue.Queue`; `ENSEMBLE_MAX_PARALLEL_RUNS=1`; recovery by local pid. Heavy pool of 2 processes. | `engine/runner.py:54-75`; `engine/jobs.py:44`; `engine/pool.py:135` | one fit at a time; a lost API process loses the queue order | A DB-backed queue; `python -m app.engine.workerd` on any host; heartbeat leases instead of pids; slots by resource class (B6). |
| 4.10 | **Uploads held in memory**, with a 200 MB cap. | `catalog/datasets_api.py:91, 402`; `main.py:201`; `config.MAX_UPLOAD_BYTES`; `datasets.py:117` (`read_upload(…, content: bytes)`) | a 10M-row CSV is 1.15 GB and cannot be uploaded | Stream `UploadFile` to `tmp/<uuid>` in 8 MB chunks, hashing on the way; DuckDB `read_csv` from disk; cap from config (5 GB POC); drop folder for larger files (B1). |
| 4.11 | **Unbounded DuckDB queries** (new risk): Top-N pages and wide one-pass diffs can take 10 GB, or fill the disk with spill. | (new code) | measured: 10 GB diff; a page load filled 20 GB of disk | One connection factory with `memory_limit`, `threads`, `temp_directory` and `max_temp_directory_size`; query templates only (no `ORDER BY` pages) (B1). |

---

## 5. Key algorithms (performance-specific; data model per Plan A)

### 5.1 DuckDB runtime (`backend/app/lake/duck.py`)

```python
def connect(purpose: Literal["interactive", "job"]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()   # in-memory; data stays in Parquet
    if purpose == "interactive":
        mem, thr = settings.duck_interactive_mb, 2   # 512 MB, 2 threads
    else:
        mem, thr = settings.duck_job_mb, settings.duck_job_threads   # 2 GB, cores / 2
    con.execute(f"SET memory_limit='{mem}MB'; SET threads={thr}; SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{spill_dir}'; SET max_temp_directory_size='{settings.duck_spill_gb}GB'")
    con.execute("SET autoinstall_known_extensions=false; SET enable_object_cache=true")
    return con
```

- **Interactive:** one process-wide connection; one `cursor()` per request; a semaphore of 8 concurrent interactive
  queries; `statement_timeout`-style cancel at 10 s (`con.interrupt()` from a timer).
- **Jobs:** a fresh connection per job in the worker process.
- **Paging:** `WHERE __row_id BETWEEN :lo AND :hi`, using the row-id range from the manifest part stats. With a
  filter: `WHERE <filter> AND __row_id > :after LIMIT n` (keyset). Never `ORDER BY` a whole table to page.
- **Search:** `concat_ws('|', cols) ILIKE ?`, first n hits by keyset. The total count is a separate, cancellable
  request with a 3 s budget, shown as "≥ n".
- **Disk guard:** before a write job, check that free disk ≥ 3× the input size, else fail with a message (Plan A §10).

### 5.2 Statistics cache (`table_stats`)

```sql
CREATE TABLE table_stats (
  table_sha256  TEXT PRIMARY KEY,        -- Plan A's table sha (ordered part hashes): content-addressed, shared across versions
  stats_version INTEGER NOT NULL,        -- bump to recompute after a profiler change
  computed_at   TEXT NOT NULL,
  n_rows        BIGINT NOT NULL,
  n_cols        INTEGER NOT NULL,
  columns       TEXT NOT NULL,           -- JSON: per column {type, nulls, distinct≈, min, max, mean, std, q01..q99, top20, hist}
  seconds       REAL NOT NULL
);
```

- Computed once per table sha, in one DuckDB pass:

  ```sql
  SELECT count(*), count(c), approx_count_distinct(c), min(c), max(c), avg(c), stddev(c),
         approx_quantile(c, [...])
  ```

  This runs for every column at once, plus one `GROUP BY` per low-cardinality column (top-20).
- **Over 5M rows:** the commit returns at once with `stats: "pending"`, and a `profile` job fills the cache.
- **Over 1 000 columns:** stats for the first 200 columns plus the target come first; the rest are computed lazily
  per column (0.4 s per column at 10M rows).
- Unchanged tables carried into a new version reuse the row, so their cost is zero.

### 5.3 Diff of two table versions (replaces `catalog/dsets.py:_pairs` and the pandas loads)

1. Schema diff from the manifests (no scan).
2. If the table sha is the same: identical, no scan.
3. **With a key** (`__row_id`, or a declared business key), pass 1:

   ```sql
   SELECT key, hash(non-key cols) AS h FROM a FULL JOIN b USING (key)
   ```

   This gives counts of added, removed and changed rows, and `CREATE TEMP TABLE changed(key)`.
4. Pass 2: per-column `count(*) FILTER (WHERE a.c IS DISTINCT FROM b.c)` on `a SEMI JOIN changed` ⋈
   `b SEMI JOIN changed`. Measured at 50M rows: 11.8 s / 2.5 GB, against 18.7 s / 10 GB in one pass.
5. **Without a key:** a multiset row-hash diff (`GROUP BY h` with counts, then a full join on `h`). Reports removed
   and added counts; no "changed" class.
6. Sample rows for the UI: `LIMIT 50` per class from the changed-key table, by keyset.
7. Results are cached in `diff_cache(a_table_sha, b_table_sha, key)` → JSON, so repeated views cost nothing.

### 5.4 Training frame and sampling (`backend/app/lake/frame.py`)

```python
def load_training_frame(version_id, table_or_view, target, columns=None, *, budget_mb=None) -> TrainingFrame:
    stats = table_stats(...)
    cols = columns or usable_columns(stats)       # drop constant / ID-like / >99% null / contract-excluded
    est = estimate_bytes(stats, cols)             # n_rows × Σ per-column bytes (8 B numeric; string avg len from stats)
    budget = budget_mb or worker_ram_mb * 0.10
    split = splits(version_id, target)            # Parquet (__row_id, fold); -1 = holdout
    if est <= budget:
        return read(cols + ['__row_id'])          # pd.read_parquet(columns=…)
    # 1. train rows: deterministic stratified hash sample
    k_train = floor(budget / est_row_bytes)
    per_class = allocate(k_train, class_counts)   # keep every row of classes with ≤ k/(2·n_classes) rows; others proportional
    #   SELECT t.* FROM t JOIN split USING (__row_id)
    #   WHERE fold >= 0 AND hash(__row_id, seed) % 1e6 < 1e6 * per_class[y] / n_class[y]
    #   → COPY TO tmp/sample.parquet → pd.read_parquet
    # 2. holdout: all rows are scored in batches of 500k via DuckDB → predictor.predict(batch) → predictions Parquet
    return TrainingFrame(df, sample_spec={method: 'hash', seed, per_class, rows_total, rows_used,
                                          sample_sha256, columns_dropped})
```

- **Deterministic.** The same version, split and seed give the same rows. `sample_sha256` (the hash of the sample's
  `__row_id` column) goes into `run_inputs`, so a rerun is provably the same input, and paired comparisons between
  two runs on the same sample stay paired.
- **Regression targets** are stratified by deciles of y. Folds come from the split file restricted to the sample.
- **AutoGluon** gets the frame with `__row_id` as the index (Plan A). Class weights are corrected for the sampling
  rate when `per_class` is not proportional: `sample_weight = n_class / k_class`.
- **The run card states it:** "Trained on 2 000 000 of 50 000 000 rows (stratified hash sample, seed 42); holdout
  scored on all 5 000 000 rows."

### 5.5 Durable job queue (`backend/app/jobs/queue.py`): no Redis, works on SQLite and Postgres

```sql
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,          -- train | review | featurelab | profile | diff | export | batch_score | ingest | al | lc
  team_id       TEXT,                   -- fair share
  resource      TEXT NOT NULL,          -- 'train' | 'duck' | 'light'  (slot class)
  mem_mb        INTEGER NOT NULL,       -- reservation, from the estimator in 5.4
  priority      INTEGER NOT NULL DEFAULT 0,
  payload       TEXT NOT NULL,          -- JSON
  status        TEXT NOT NULL CHECK (status IN ('queued','leased','succeeded','failed','cancelled')),
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 2,
  created_at    TEXT NOT NULL,
  not_before    TEXT,
  leased_by     TEXT,                   -- worker id
  lease_until   TEXT,                   -- heartbeat extends it by 60 s every 20 s
  started_at    TEXT,
  finished_at   TEXT,
  error         TEXT,
  result        TEXT,
  ref_kind      TEXT,
  ref_id        TEXT                    -- the run / review / batch it drives
);
CREATE INDEX ix_jobs_claim ON jobs (status, resource, priority DESC, created_at);
CREATE INDEX ix_jobs_ref ON jobs (ref_kind, ref_id);

CREATE TABLE workers (
  id            TEXT PRIMARY KEY,
  host          TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  heartbeat_at  TEXT NOT NULL,
  slots         TEXT NOT NULL,          -- JSON {"train":1,"duck":2,"light":4,"mem_mb":12000}
  version       TEXT NOT NULL
);
```

**Claim.** Postgres:

```sql
UPDATE jobs SET status = 'leased', leased_by = :w, lease_until = now() + 60 s, attempts = attempts + 1
WHERE id = (SELECT id FROM jobs
            WHERE status = 'queued' AND resource = :r AND mem_mb <= :free
              AND (not_before IS NULL OR not_before <= now())
            ORDER BY <fair share: running jobs of the team ASC>, priority DESC, created_at
            LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING *;
```

SQLite runs the same statement without `SKIP LOCKED`, inside `BEGIN IMMEDIATE`. The claim is one short write, and
there is one writer anyway.

**Recovery.** A janitor in every worker re-queues `leased` jobs whose `lease_until < now()`, while
`attempts < max_attempts`. The local-pid recovery in `engine/runner.py:recover()` goes away.

**Resumable kinds:**
- `train` already writes its own events and results;
- `review` resumes from its journal;
- `featurelab` restarts.

**Cancel** sets `status = 'cancelled'`. The holder sees it on its next heartbeat and kills its process group (the
`jobs.kill_group` it uses today).

**Fair share.** A team cannot hold more than `team_max_train_slots` (default: half the slots) while others queue.

### 5.6 Delta parts with checkpoints (escalation step 3; behind a flag)

This extends Plan A's manifest `files` from parts to **parts + deletes**:

```json
"files": [
  {"role": "base",   "sha256": "…", "n_rows": 10000000},
  {"role": "upsert", "sha256": "…", "n_rows": 200000, "seq": 1},
  {"role": "delete", "sha256": "…", "n_rows": 50000,  "seq": 1}
]
```

**Read (merge on read)**, measured at 0.58 s for 10 deltas over 10M rows:

```sql
WITH up  AS (SELECT * FROM read_parquet([upserts]) QUALIFY row_number() OVER (PARTITION BY __row_id ORDER BY _seq DESC) = 1),
     del AS (SELECT __row_id, max(_seq) AS dseq FROM read_parquet([deletes]) GROUP BY 1),
     cur AS (SELECT *, 0 AS _seq FROM base b WHERE NOT EXISTS (SELECT 1 FROM up WHERE up.__row_id = b.__row_id)
             UNION ALL SELECT * FROM up)
SELECT * EXCLUDE (_seq) FROM cur LEFT JOIN del USING (__row_id) WHERE dseq IS NULL OR dseq < cur._seq
```

**Checkpoint:** write a new base (4.2 s at 10M rows) when either holds:
- the chain has more than 20 deltas;
- the delta bytes exceed 25 % of the base.

**Identity:** `table_sha256` stays the hash of the ordered file list, so integrity checks are unchanged. The logical
`data_sha256` is computed on the merged rows, so a checkpoint does not change it.

---

## 6. Escalation path, with triggers (evaluated on: multi-team, multi-table, file exports only, a bank)

### 6.1 Metadata → Postgres

**Triggers** (any one is enough):
1. A second process writes from another host: a separate worker host, two API replicas, or a drop-folder ingester
   on another machine. A SQLite file on network storage is not safe.
2. p99 commit latency above 100 ms sustained, or any `database is locked`. Measured at about 16 or more concurrent
   writers.
3. Audit or reporting queries scan millions of rows interactively: 1.9 s on SQLite vs 0.29 s on Postgres at 5M rows.
4. Operations requirements: HA, PITR, backups without stopping writes, per-role DB grants, row-level security as
   defence in depth for teams.

**For the bank:** trigger 4 applies at the first shared (non-laptop) deployment. Plan Postgres for the **pilot**, and
keep SQLite for dev and demo.

**How:** `backend/app/db.py` becomes a `Database` with two dialects:
- `?` → `%s`;
- `INSERT … ON CONFLICT` (both engines support it);
- no `PRAGMA` on Postgres;
- `AUTOINCREMENT` → `GENERATED ALWAYS AS IDENTITY`;
- `IS NOT 'deleted'` → `IS DISTINCT FROM 'deleted'`, a SQLite-only form used in `datasets.py:405` and `catalog/dsets.py:698`. Grep
  for it as part of the B5 work.

Connections come from `psycopg_pool` with a pool of 10. Migration is a one-off copy (`COPY` per table, 43 s for the
benchmark volume).

**Audit at 50M rows:** monthly range partitions on `audit_log(ts)` in Postgres. Partitions older than the retention
window (Plan C §4.1) are exported to Parquet blobs (sha recorded) and detached.

### 6.2 Parquet on S3 / MinIO

**Triggers:**
1. More than one compute host must read the blobs (workers, a second API).
2. The lake outgrows about 70 % of local disk, or passes about 1 TB.
3. Retention or immutability is required. For a bank, S3 Object Lock (WORM) on `blobs/` satisfies "nothing is
   modified in place", which a local disk cannot prove.

**How:**
- Plan A's `blobs/<aa>/<sha>.parquet` keys are write-once, which makes them safe for S3 with no rename. The blob
  store interface `lake/blobs.py` has two implementations: `LocalBlobs` and `S3Blobs`.
- DuckDB reads `s3://` through **httpfs**:

  ```sql
  CREATE SECRET (TYPE s3, PROVIDER credential_chain)
  ```

  This uses the instance role or IRSA in AWS. For MinIO:

  ```sql
  CREATE SECRET (TYPE s3, KEY_ID …, SECRET …, ENDPOINT 'minio:9000', URL_STYLE 'path', USE_SSL false)
  ```

  Extensions are bundled in the image (`autoinstall_known_extensions=false`), since the bank network will block
  downloads.
- **Caching:** content-addressed blobs never change, so a **read-through local cache** (`cache/blobs/<aa>/<sha>.parquet`,
  LRU, `ENSEMBLE_CACHE_MAX_GB`) is always coherent. It is verified by sha on fill, then trusted, because the key is
  the hash.
  - Interactive previews read S3 directly with range requests (row-group pruning).
  - Training and diff jobs pull the whole blob into the cache first, then read locally.
  - DuckDB's `enable_http_metadata_cache` is on.
- **Credentials:** never in the DB. They come from the environment or the role. Uploads go through the API (the API
  writes the blob); there are no browser presigned PUTs in the POC.
- **Not measured** (no bucket here). B7's verification records the throughput.

### 6.3 Our own delta parts (section 5.6) before Iceberg

**Trigger:** for any table, versioned bytes exceed **20×** its live size. Example: 30 full versions of a 3 GB table.
Alternatively, the team's storage quota (Plan A `teams.storage_quota_bytes`) passes 80 % mainly because of
full-extract re-uploads.

**For the bank:** full extracts are uploaded repeatedly, so this is likely the **first storage escalation** for the
largest 2 % of tables. It reuses diff-by-key, which we already have, and adds no service.

### 6.4 Iceberg

**What it gives over our manifests:**
- snapshots and time travel, which we already have through manifests;
- row-level deletes (position or equality delete files), which section 5.6 covers;
- schema and partition evolution;
- most importantly, a **standard table format other engines read**: Spark, Trino, Snowflake, Athena.

**Triggers** (either one):
1. A consumer outside this app must read versioned tables directly. Examples: the bank's data platform, a regulator
   extract job, Spark feature pipelines.
2. Tables pass about 1B rows, or need partition pruning by business columns across thousands of files, where our
   flat file lists become the bottleneck.

**Cost:**
- a catalog (REST catalog, Glue, or Nessie) plus pyiceberg for writes;
- DuckDB's `iceberg` extension for reads (DuckDB's write support is newer; verify before relying on it);
- our manifest becomes a pointer `{iceberg_table, snapshot_id}` per table, and the rest of Plan A is unchanged:
  version rows, `__row_id`, splits and views;
- one engineer for about 2–3 weeks.

**For the user's scenario:** not needed for the POC or Tier 2. File exports only means no outside writer, and nothing
named needs outside readers yet.

### 6.5 lakeFS

**What it gives:**
- git-like branches, commits and merges over a whole object store prefix;
- atomic multi-object commits;
- isolation for **outside writers** (Spark jobs, notebooks) writing into the lake.

**Our design already has** atomic multi-table commits: content-addressed blobs plus one DB transaction (Plan A D1).
It also has per-dataset linear history, which is what the brief asks for: "every change creates a new version".

**Triggers** (either one):
1. Writers outside the app must change lake data with isolation.
2. Teams need **branch-and-merge across many datasets**, for example "fork the whole project's data, change 30
   tables, review, merge", with conflict detection.

**Cost:** a lakeFS server and its KV/Postgres store, credentials, and a second commit graph to keep consistent with
ours.

**For the user's scenario:** reject. File uploads come only through the app, versions are per dataset, and a bank
gains another system of record to audit.

### 6.6 Job queue and more workers

**Step 1, the DB-backed queue in section 5.5: build it in the POC.** It fixes:
- review loops dying with the API;
- one fit at a time;
- pid-based recovery.

Triggers for step 1: any of
- p95 queue wait above 10 min;
- more than 1 training job needed at once;
- the API restarting during reviews.

**Step 2, more workers:**
- **Trigger:** concurrent training demand exceeds one host's RAM, using the section 5.4 estimator (sum of `mem_mb`
  queued over 30 min > worker RAM).
- **Needs:** Postgres (6.1) and S3 (6.2). Add hosts running `workerd`, each with declared slots. GPU or high-memory
  hosts declare a `train-large` resource class.

**Step 3, Celery, Ray or Kubernetes jobs:**
- **Only if** more than about 50 workers, autoscaling, or cross-cluster scheduling is needed.
- At that size, `SKIP LOCKED` polling every 2 s by 50 workers is 25 queries/s, which Postgres handles easily. So the
  practical trigger is **autoscaling**, not throughput.

---

## 7. Schema additions (beyond Plans A and C)

| Table | Purpose | Section |
|---|---|---|
| `table_stats` | column statistics, cached by table sha | 5.2 |
| `diff_cache(a_sha, b_sha, key, computed_at, result JSON)`, PK `(a_sha, b_sha, key)` | repeated diffs are free | 5.3 |
| `jobs`, `workers` | durable queue, leases, slots, fair share | 5.5 |
| `run_inputs` + `sample_spec JSON`, `sample_sha256 TEXT`, `rows_total BIGINT`, `rows_used BIGINT`, `frame_mb INTEGER`, `columns_dropped JSON` | what a run actually trained on | 5.4 |
| `storage_usage(team_id, computed_at, bytes_referenced, bytes_unique, n_blobs)` | quota and trigger 6.3 | 6.3 |
| `audit_log` + `team_id`, indexes `(team_id, ts)`, `(target_kind, target_id, ts)`, `(actor, ts)`; monthly partitions on Postgres | audit at 5–50M rows | 6.1 |

**Config knobs** (`config.py`):

| Knob | Default |
|---|---|
| `ENSEMBLE_DATABASE_URL` | `sqlite:///data/ensemble.db` |
| `ENSEMBLE_BLOB_STORE` | `file://data/lake` or `s3://bucket/prefix` |
| `ENSEMBLE_CACHE_MAX_GB` | — |
| `ENSEMBLE_DUCK_INTERACTIVE_MB` | 512 |
| `ENSEMBLE_DUCK_JOB_MB` | 2048 |
| `ENSEMBLE_DUCK_SPILL_GB` | 20 |
| `ENSEMBLE_TRAIN_FRAME_SHARE` | 0.10 |
| `ENSEMBLE_MAX_UPLOAD_GB` | 5 |
| `ENSEMBLE_WORKER_SLOTS` | — |

---

## 8. Migration from today's code (performance items; storage migration is Plan A §6)

| File / module | Change |
|---|---|
| `backend/app/lake/duck.py` (new) | connection factory, limits, interrupt, query templates for page, search and preview |
| `backend/app/lake/frame.py` (new) | `load_training_frame`, estimator, hash sampler, batched holdout scoring |
| `backend/app/lake/stats.py` (new) | `table_stats` compute and cache; replaces the `profile_frame` calls in `datasets.py:378, 421, 540` |
| `backend/app/lake/blobs.py` (new) | `LocalBlobs` / `S3Blobs` with read-through cache |
| `backend/app/jobs/queue.py`, `backend/app/engine/workerd.py` (new) | queue, claim, lease, janitor, worker main |
| `backend/app/datasets.py` | streaming `read_upload` from a path; remove the `to_csv`/`read_csv` round trips and the hash-by-reserialisation (`:482-536, :651, :706, :804, :890`); `derive` carries splits by SQL (`:568-660`) |
| `backend/app/catalog/datasets_api.py` | stream uploads (`:91, :402`); page, search and preview through `lake.duck` (`:262-300`) |
| `backend/app/main.py` | stream uploads (`:201`); light `list_datasets` + `COUNT(*)` (`:178, :186`) |
| `backend/app/catalog/dsets.py` | diff through 5.3 (`:278-300`); freeze-holdout via DuckDB `SEMI JOIN split` (`:633-660`) |
| `backend/app/catalog/compare.py` | `root_info` reads the `__row_id` column (`:47-62`) |
| `backend/app/catalog/lineage.py` | recurse over cross-object edges only; versions by `dataset_id` (`:13-31`) |
| `backend/app/db.py` | dialect layer, pool, `synchronous=NORMAL`, no I/O inside transactions; `list_datasets` paginated |
| `backend/app/engine/runner.py`, `engine/jobs.py`, `engine/pool.py` | submit → `jobs` row; workers claim; pid recovery replaced by leases; the heavy pool becomes the `duck`/`light` slots |
| `backend/app/vela/review.py`, `agent/loop.py`, `featurelab.py`, `engine/active_learning_api.py`, `engine/learning_curve_api.py` | start = enqueue a job; the in-memory `_tasks`/`_jobs`/`_PROCS` dictionaries are removed |
| `backend/app/featurelab_api.py:124` | version count via `SELECT count(*) … WHERE root_id = ?` |
| consumers that call `load_frame` (Plan A appendix A) | `load_training_frame(columns=…)` or a DuckDB query |

---

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| AutoGluon's real memory multiple is above the 10 % rule | OOM-killed fits | B4 measures peak RSS of a 1M and a 5M fit per preset; the worker sets the rule from that, and the estimator reserves `mem_mb` |
| Sampling changes model quality silently | weaker models on big data | the run card and registry show `rows_used/rows_total`; the learning-curve feature (exists) shows whether more rows help; users can raise the budget per run when a larger worker exists |
| DuckDB spill fills the disk (seen in this bench) | every job on the host fails | `max_temp_directory_size`; the spill dir is on its own volume; disk guard before write jobs |
| One shared interactive DuckDB saturates (about 54–164 q/s on 4 cores) | slow pages for everyone | semaphore + 10 s interrupt; key-range pages; stats cache; more API replicas need Postgres + S3 |
| SQLite stalls from a long transaction | seconds of lock wait on every write | review rule: no file I/O or pandas inside `with db.connect()`; Postgres at trigger 6.1 |
| Lease expiry runs a job twice (a GC pause, a network partition) | duplicate versions or runs | idempotent commits: dedupe by `data_sha256` (Plan A) and `jobs.id` written into the version/run rows; a run checks it still holds the lease before committing |
| S3 latency on interactive previews | slow first page | row-group pruning; metadata cache; warm the cache on commit for the newest version |
| Delta-part chains slow reads | training reads at 2–10× | checkpoint rule (20 deltas or 25 %); merge-on-read measured at 0.58 s for 10 deltas over 10M rows |
| Benchmarks on synthetic data | real compression and cardinality differ | B8 reruns the table in section 2.1 on a real export (anonymised) and records it next to these numbers |

---

## 10. Phased build plan (sessions of about 2 h)

These phases come after Plan A's L1 (the Parquet writer and manifests) unless noted. Each phase ends with manual API
calls chained the way the UI calls them, plus `npm run build`. No smoke or integration suites.

- `$API` = `http://localhost:8000`.
- Test files come from a **seed script** (`python -m app.lake.seed --rows 10000000 --out /tmp/big.csv`). The script
  generates data; it is not a test suite.

### B1: DuckDB runtime, streaming upload, file hashing (after A-L1)
- `lake/duck.py` with limits; streaming upload to a temp file with sha256 on the fly; `MAX_UPLOAD_GB`.
- Remove the `to_csv` hashes and round trips from `create_version`, `upload_version` and `edit_version`.

**Verify:**
1. Upload the 1.15 GB CSV:

   ```
   curl -F file=@/tmp/big.csv -F name=big $API/api/datasets
   ```

   It returns in under 60 s, and `GET $API/api/datasets/{id}` shows `sha256`, `rows = 10000000` and
   `account_no` typed as a string with its leading zeros.
2. Edit the dataset:

   ```
   POST $API/api/datasets/{id}/edit {"ops":[{"op":"drop_columns","columns":["memo"]}],"preview":true}
   ```

   It returns in under 10 s (today: over 60 s).
3. Process RSS stays under 3 GB during both calls (`ps -o rss`).

### B2: Read paths and the stats cache
- Page, search and filter-preview through DuckDB key-range templates.
- `table_stats` at commit; a `profile` background step for tables over 5M rows.

**Verify:**
1. `GET $API/api/datasets/{id}/rows?offset=5000000&limit=50` in under 200 ms.
2. `…/rows?q=st7&limit=50` in under 500 ms.
3. `GET $API/api/datasets/{id}` shows `stats.status` pending → ready.
4. A second version that carries the table shows stats immediately (same sha).
5. Eight parallel `curl` page calls in a shell loop: p95 under 300 ms.

### B3: Diff, splits and row identity
- Two-pass diff with `diff_cache`.
- Split carry by SQL.
- `root_info` from `__row_id`; delete JSON `root_rows` reads; freeze-holdout via DuckDB.

**Verify:**
1. Upload v2 with `row_key=loan_id`.
2. Diff the two versions:

   ```
   GET $API/api/datasets/diff?a=v1&b=v2
   ```

   It shows added 100 000 / removed 50 000 / changed 100 000, `changed_cols = {income: 100000}`, in under 5 s. A
   repeat call takes under 50 ms (cache).
3. `GET /api/runs/compare?a=…&b=…` on runs trained on v1 and v2 pairs rows by `__row_id`.

### B4: Training frame, sampling and the memory measurement
- `load_training_frame`; estimator; hash sample; batched holdout scoring; `run_inputs.sample_*`.
- Measure an AutoGluon fit's peak RSS at 1M and 5M rows with the default preset, and write the multiple into config.

**Verify:**
1. Start a run with a deliberately small budget:

   ```
   POST $API/api/runs {"dataset_id": big, "label": "defaulted", "frame_budget_mb": 300}
   ```

2. `GET $API/api/runs/{id}` shows `inputs.rows_used ≈ 1.5M`, `rows_total = 10M`, a `sample_sha256`, and holdout
   metrics computed on every holdout row.
3. A second identical POST gives the same `sample_sha256`.

### B5: Metadata dialect, Postgres, light lists, the lineage fix
- `db.py` dialect layer and pool; `ENSEMBLE_DATABASE_URL`; a one-off copy command.
- Paginated lists; `COUNT(*)` health; the lineage CTE over cross-object edges; `IS NOT` → `IS DISTINCT FROM`.

**Verify:**
1. Start the API on Postgres.
2. Chain these calls and check each response:
   1. create team → project → upload → edit;
   2. `GET /api/lineage/dataset/{v1}`: every version is present, including when there are more than 64;
   3. start a run, then register the model;
   4. `GET /api/audit?team=…`.
3. Repeat the chain on SQLite.
4. Seed 100k synthetic version rows (`python -m app.lake.seed --meta`), then:

   ```
   GET /api/datasets?team=…&limit=50
   ```

   It responds in under 50 ms on both engines.

### B6: Durable job queue and workers
- `jobs`/`workers`; `workerd`; runner, pool, review, Feature Lab, active learning and learning curve move onto it;
  leases, the janitor, fair share.

**Verify:**
1. Start the API plus two workers:

   ```
   python -m app.engine.workerd --slots train=1
   ```

2. POST 4 runs. Two run at once, and `GET /api/jobs` shows the lease holders.
3. `kill -9` one worker. Within 90 s its job is `queued` again, then `leased` by the other worker.
4. Start a review (`POST /api/runs/{id}/reviews`), restart the API, and check that `GET …/reviews/{rid}` continues.

### B7: Object store and cache
- `S3Blobs` + httpfs secret + read-through cache; MinIO in an optional compose profile; bundled extensions.

**Verify** with `ENSEMBLE_BLOB_STORE=s3://lake/` against MinIO:
1. Upload the dataset, open a preview page, and start a run.
2. Check the blob's presence:

   ```
   mc ls minio/lake/blobs/<aa>/
   ```

3. Delete the local cache directory. The next preview still works, and the cache refills (its size is shown in
   `GET /api/admin/storage`).
4. Record the S3 read throughput.

### B8: Delta parts behind a flag, plus a rerun on real data
- Section 5.6 reader and writer; the checkpoint rule; `storage_usage`.
- Rerun the table in section 2.1 on an anonymised real export and append the results to this file.

**Verify:**
1. Upload 25 successive full extracts with `delta_storage=true`.
2. `GET /api/admin/storage` shows about 0.9 GB stored (vs about 7 GB full).
3. `GET /api/datasets/{v25}/rows` matches a full upload's sha (`data_sha256` equal).
4. A checkpoint appears after version 20.

Postgres comes in B5 and the queue in B6. Both are listed for the POC because the job-queue fixes in section 4.7 and
4.9 matter even on one host. B7 and B8 are escalation work, built when their triggers in section 6 fire.

---

## Appendix: reproducing the benchmarks

The scripts were in the planning session's scratch directory (`bench/gen.py`, `v2.py`, `bureau.py`, `bench.py`,
`delta.py`, `meta.py`, `today.py`, `conc.py`). They are not committed, by instruction. Their logic is above:

- **Data** is generated by DuckDB `COPY (SELECT … hash(i, k) … FROM range(N)) TO … (FORMAT parquet, COMPRESSION zstd)`.
  Generating 50M rows takes 67 s.
- **Each operation** runs in a fresh Python process and reports `resource.getrusage(RUSAGE_SELF).ru_maxrss`.
- **Metadata** uses the same DDL on SQLite (WAL) and Postgres 16 (`initdb`, `shared_buffers=1GB`), loaded with
  `executemany` / `COPY`. Writers are separate processes, 8 s per level.

B8 commits the generator as `app/lake/seed.py`, so the numbers can be rerun.
