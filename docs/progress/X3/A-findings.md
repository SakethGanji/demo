# X3 lane A: joins, registry (join models), sampling

**Scope:** joins (fan-out guard, skew, NULL keys, date windows, cross-dataset pins after archive/delete), registry for
join models (parity after new input versions, batches with extra/reordered columns and coercion failures), sampling
determinism (processes, SQLite vs Postgres), and 500s on the join / frame / batch / inputs.zip / predict routes.

**Code under test:** `motion-components` @ 55c491a. No product code was changed.

**Setup:**
- A venv from `requirements.lock` (duckdb 2.0.1, pyarrow 24.0.0, AutoGluon 1.6.3, Python 3.13).
- SQLite API on :8740 and Postgres-16 API on :8741 (`ENSEMBLE_DB_URL`, initdb in `/var/tmp/x3a_pg`, port 5441).
- Each had its own scratch data dir. All were removed afterwards.

**Data:** `A/make_data.py` builds a 4-table dataset `jx`:
- `apps` is the grain: 3,000 rows, with 40 NULL `cust` values. It has a DATE `applied_on` and a TIMESTAMP `applied_day`.
- `cust` has 1,100 rows.
- `pays` has 38,594 rows. App A00001 has 30,000 of them (skew). 25 rows have a NULL `app_id` and 10 % have a NULL `paid_on`. Many dates sit exactly on the window boundaries. It also has `paid_serial` (an Excel serial, BIGINT) and `paid_dmy` ('31/01/2024', VARCHAR).
- `daily` has a TIMESTAMP key.

`A/make_batch.py` builds the batch files. `A/*.sh` are the call chains. `A/*_sqlite.txt` and `A/*_postgres.txt` are the raw output.

## Findings

| Rank | # | Finding |
|---|---|---|
| major | A-M1 | `/predict` and `/score` on a **join model** score a grain row without the join. The joined features are treated as missing (or taken from the caller), and the answer is 200. This is the serving skew of §7 risk 11, through a second path. |
| major | A-M2 | A date **window** on a column that is not a DATE/TIMESTAMP is accepted. Examples are an Excel serial (BIGINT) or 'dd/mm/yyyy' text. `TRY_CAST(… AS DATE)` gives NULL, so every windowed `sum` is NULL and every windowed `count` is 0 on all 3,000 rows. Validation passes, and coverage looks normal. |
| major | A-M3 | An **n:1 join DATE = TIMESTAMP** is accepted: both are kind "text", so `_key_expr` casts both sides to VARCHAR. '2024-01-05' ≠ '2024-01-05 00:00:00', so 0 of 3,000 rows match, and the feature is all NULL. Only `coverage 0.0` hints at it. There is no 400 and no warning. |
| major | A-M4 | A join that reads **two tables with the same name** (a local `cust` and another dataset's `cust`) trains, registers and passes parity ("ok"), but it can never be batch-scored: the batch answers 400 `ambiguous_tables`, "not supported yet". A registered model can therefore be unservable. |
| major | A-M5 | **Deleting a pinned input version does not remove its data from join frames.** The frame's `join_cache` blob holds a copy of the deleted table's columns. It stays `live`, is pinned by `blob_refs (join_frame, cache)` so GC never lists it, and `GET …/frames/{id}?verify=1` still answers `ok: true` for the deleted input. Hand-off to P24. |
| minor | A-m1 | The join batch path answers **500 on an unreadable file**: binary bytes as `apps.csv`, `PAR1garbagePAR1` as `.parquet`, `PK\x03\x04garbage` as `.xlsx`. The `DatasetError` from `frames.read_upload` is not mapped to 400 in `join_batch_frame`. The behaviour is the same on SQLite and Postgres. |
| minor | A-m2 | The batch's `cast_failures` under-reports. Numeric text that pandas coerces (`x1 = "abc"`, `amount = "n/a"`) becomes NULL **before** the DuckDB `TRY_CAST` count, so only failures of the date and type cast are counted. In the summary, `applied_on: 10` is reported, but the 10 bad `x1` and 50 bad `amount` values are not. There is no warning in the batch summary. The single-table path coerces silently too, so this is pre-existing. |
| minor | A-m3 | Postgres-only 500s: `POST …/joins` and `…/joins/{id}/validate` with `"version_id": 5` (a number) fail with `operator does not exist: text = smallint`, where SQLite gives 404. A `%00` in a dataset path segment fails with `PostgreSQL text fields cannot contain NUL` in `authz/scope._one`. The NUL case affects every dataset route on Postgres. |
| minor | A-m4 | A delta of one row is refused: 400 `too_few_rows`, "This file has fewer than two data rows." A one-record correction is the commonest delta. This was found on the way; it belongs to the deltas lane. |
| minor | A-m5 | A join batch does not check the grain table's declared key. An `apps.csv` with 2 repeated `app_id` rows is scored as 302 rows, so one application gets two scores. |
| info | A-i1 | Skewed aggregate: windowed aggregates group by grain row over `grain ⋈ table`. With 20,000 grain × 20,000 table rows on one key value (400M pairs) the frame took 5.0 s with peak RSS 0.52 GB, and the answer was correct. The cost is Σ grain_k × table_k, and `POST …/frames` is synchronous with no estimate or guard. At 10⁵ × 10⁵ on one key, expect minutes in-request. |

**What held:**
- **Fan-out guard.** These cases all gave 400 `one_to_many`, both on `POST …/frames` with the saved join and on `…/validate`:
  - a version whose `cust` table was rewritten with one repeated `cust`;
  - a delta on a composite key `[cust, score]` that keeps the key valid but repeats `cust`, joined n:1 on `cust`.

  The earlier version still serves its cached frame.
- **Window correctness.** `A/check_j4.py` recomputed the J4 frame in pandas: n:1 plus count, sum, sum over −90 d, count over −90 d, count over +30 d, and max date. Every cell was equal (0 differing of 3,000 × 7) on SQLite and Postgres. This covered:
  - the boundaries: −90 included, the same day excluded, +30 included;
  - NULL dates and NULL keys on both sides;
  - the skewed key (A00001: 29,982 rows after the NULL keys).
- **Engine parity.** The J4 frame's `frame_sha256` and cache-blob sha were identical on SQLite and Postgres (`80645d2e…`, `d24e04aa…`).
- **Pinning.** After new versions of the pinned ext `cust` (a delta of 60 scores) and of `jx` (a `pays` delta):
  - a re-registration of the same run → parity `ok`, with tables still at the pinned `dv`s;
  - a clean batch → **byte-equal** `scored.csv` to the batch before the new versions;
  - the saved join applied to the new `jx` version → still reads the pinned ext version `ds_6dbb0b2c`, not the new one.
- **Batches.**
  - Reordered columns, an extra column and an extra label column → succeeded. Predictions were equal to the clean batch on all 300 rows, and `extra_columns` was reported.
  - A duplicate `cust` key in a batch file → 400 `batch_grain_violated`, not 500.
  - Duplicate grain keys (two rows of `apps.csv` repeated) are accepted: 302 rows are scored, and the declared key `app_id` is not checked on the batch grain. See A-m5.
- **Archive and delete of a pinned version in use** are refused (409), with blockers that name the registered versions and runs. That includes runs reached through `merged_from` and a join frame.
  - A delete request with `irreproducible` still lists the registered model and any running run.
  - A run on a pinned version that is deleted → 409 `version_deleted`. The same answer comes for a frame or validate on the saved join.
- **Sampling determinism.** Under a forced budget the results were:

  | Case | `sample_sha256` |
  |---|---|
  | 3,000-row table and join runs, seed 42 (floor rates) | `7a91293e…` |
  | the same, seed 7 | `6e8b5556…` |
  | 20,000-row table, 0.5 MB budget, seed 42 (a budget-derived rate of 0.312076) | `889575f1…` |

  Each sha was the same in every run, every worker process, and on SQLite and Postgres. The rates were equal too. `data_sha256` was the same within each kind.
  - Note: the run field is `sample_seed`. A field named `seed` is silently ignored, so the default 42 is used.
- **Fuzz.** 233 calls each on SQLite and Postgres: garbage join specs (65 shapes), bodies, ids, frames with `?verify=` variants, batch file shapes, inputs.zip, and `predict`/`score` bodies. The only 5xx were A-m1 and A-m3.

## Repros

### A-M1: predict/score on a join model skips the join (`A/reg_sqlite.txt`; model `mv_d7d0c35a` = join `model_join`, an n:1 to ext `cust` plus aggregates of `pays`)

```
POST /api/registry/versions/mv_d7d0c35a/predict {"row":{"app_id":"A00001","cust":"C0638","applied_on":"2024-12-10","x1":0.3}}
→ 200 {"missing_columns":["applied_day","x__score","p__n","p__amt90"], "prediction":0, "probabilities":{"0":0.751,"1":0.249}, …}
POST /api/registry/versions/mv_d7d0c35a/score {"rows":[{…same row…}]}
→ 200 {"n_rows":1,"predictions":[0],"probabilities":[{"0":0.751,"1":0.249}]}    (no missing_columns at all)
```

The joined features are never computed. A client may also send `x__score`, `p__n` and `p__amt90` it computed itself, which is a second join implementation (other code, other window). Expected: 400 "a join model scores through its join: send a batch with one file per table", or a single-row join path that goes through `frames.materialise`.

### A-M2: a window on a non-date column (`A/joins1_*.txt` J2/J3; `A/frame_of.py`)

```
POST /api/datasets/{jx}/joins {"name":"j_serial","spec":{"grain":"apps","joins":[{"type":"aggregate","table":"pays","on":["app_id"],
  "aggs":[{"name":"amt90","fn":"sum","column":"amount","window":{"date":"paid_serial","relative_to":"applied_on","days":-90}}]}]}}  → 201
POST …/versions/{v}/frames {join_id} → 201;  frame: s__amt90 non-null 0 of 3000
(window.date = paid_dmy 'dd/mm/yyyy' text): m__amt90 non-null 0 of 3000, m__n90 = 0 on all 3000 rows
```

`joins.validate` checks only that `window.date` / `relative_to` exist. The fix is to require a date kind (or ISO text that casts) and refuse otherwise with 400 `window_type`.

### A-M3: DATE = TIMESTAMP n:1 (`A/joins1_*.txt` J1)

```
spec {"grain":"apps","joins":[{"type":"n:1","table":"daily","on":[["applied_on","day"]]}]}     applied_on DATE, day TIMESTAMP
→ 201, coverage: matched 0 of 3000, orphans 3000        (control: applied_day TIMESTAMP = day → matched 3000)
```

`frames._key_expr` casts both sides to VARCHAR when the types differ and the kind is not a number. Dates should compare as TIMESTAMP, as `dataset_versions._pair` does.

### A-M4: two input tables with the same name (`A/reg_*.txt` R2/R3)

```
join two_custs = n:1 local cust (prefix l__) + n:1 {root_id: ext, version_id: ds_6dbb0b2c, table: cust} (prefix x__) → 201
POST /runs {join_id} → succeeded;  POST /runs/{id}/register → mv_9ff13699 ready, card parity "ok"
POST /registry/versions/mv_9ff13699/batches files=apps.csv,cust.csv → 400 ambiguous_tables "… name the files by role is not supported yet."
```

The fix is to refuse this at join save or registration, or to accept files named by role (`join:0.csv`).

### A-M5: a deleted pinned version survives in the join cache (`A/pins3_sqlite.txt`)

```
upload cust.csv as dataset ext2 (ds_289dfca4); join pin_e9 pinned to it; POST …/frames → jf_c5a39c5115 (cache 2d605ebe…); no run
GET /datasets/ds_289dfca4/usage → runs [], join_frames ['jf_c5a39c5115'], blocks []
POST /datasets/ds_289dfca4/delete (Ada) → dsr_…; POST /dataset-requests/dsr_…/decide (Ben, approve) → approved; GET → 410 deleted
POST …/frames {join_id} → 409 version_deleted              (good)
GET /datasets/{jx}/frames/jf_c5a39c5115?verify=1 → 200 verify ok: true, inputs [('apps', True), ('cust', True)], cache ok
DB: blobs 2d605ebe… kind join_cache state live; blob_refs (join_frame, jf_c5a39c5115, cache) → never a GC candidate
```

The frame (with the deleted table's `score` column for every grain row) is still listed by `GET …/versions/{v}/frames`. `check_inputs` re-hashes the blob but ignores the version's `deleted` state; here the bytes were shared with another dataset's identical table. Expected: delete (and P24 purge) releases or drops frames whose inputs it removes, and verify reports `version_deleted`.

### A-m1: 500 on an unreadable batch file for a join model (`A/fuzz_*.txt`)

```
POST /registry/versions/{join model}/batches files=apps.csv (bytes 0..255 ×10), cust.csv, pays.csv → 500
  app.datasets.DatasetError: This looks like a binary file, not a CSV.   (scoring.join_batch_frame → frames.read_upload, uncaught)
files=apps.parquet (b'PAR1garbagePAR1'), … → 500 DatasetError "Could not read apps.parquet as Parquet."
files=book.xlsx (b'PK\x03\x04garbage') → 500
```

### A-m2: cast_failures under-reported (`A/reg2_sqlite.txt` R9)

```
batch coerce/: apps x1 = 'abc' (10 rows), applied_on dd/mm/yyyy (10 rows); pays amount = 'n/a' (50 rows)
summary.join.tables: apps cast_failures {'applied_on': 10}, pays {} ; no warning; 1 of 300 predictions changed (A00010)
```

### A-m3: Postgres-only 500s (`A/fuzz_postgres.txt`)

```
POST /datasets/{root}/joins {"name":"fzv","version_id":5,"spec":{…}}          → 500 operator does not exist: text = smallint (SQLite: 404)
POST /datasets/{root}/joins/{id}/validate {"version_id":5}                     → 500 (same)
GET  /datasets/%00/joins                                                       → 500 PostgreSQL text fields cannot contain NUL (authz/scope._one)
```

### A-m4: a one-row delta is refused (`A/joins2_sqlite.txt`)

```
cust_delta_dup.csv = "_op,cust,score\nI,C0005,1\n"; plan {tables: {cust: {action: delta, key: [cust, score]}}}
POST /uploads/{u}/check → 400 too_few_rows "cust_delta_dup.csv: This file has fewer than two data rows."
```

**Confidence: 0.8.**
- Every claim above was reproduced through the API, on both engines where it says so.
- The frame cells and sample shas were checked independently.
- Not covered:
  - 10M-row join frames, and the parity time at scale (P15 noted ~2× materialise);
  - joins on S3;
  - cross-team pins (F-PII's lane);
  - Excel batch workbooks with a sheet per table (only garbage .xlsx).
