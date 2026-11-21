# F-X1: the fixes X1 asked for before P10

**Lane D.** Input: `docs/progress/X1.md` (2 critical, 6 major). Every MUST-FIX-BEFORE-P10 item is fixed or re-verified:
C1, C2, M1, M2, M4, M5, M6 fixed; M3 re-verified.

- **Code:** `motion-components`, from 818a105. Commits 656e753 … 4d15eab, merged with P11 and P20 at 97e7132. The merged head was re-checked (end of this file).
- **How it was checked:** manual API calls, chained the way the UI makes them. Each repro ran on the **old code** first, then on the fix.
  - Old code: a worktree of 818a105, run from fresh data dirs.
  - API: `ENSEMBLE_DATA_DIR=<scratch>/<dir> ENSEMBLE_ADMINS=fx1 uvicorn app.main:app` (`F-X1/run_api.sh`).
  - Box: 4 CPUs, 15 GB RAM.
  - Peak RSS: sampled from `/proc/<api pid>/status` every 50 ms by `F-X1/c.py` (`Peak`), with a watchdog that kills the API above a set size.
- **Scripts and raw output:** `docs/progress/F-X1/`. In the copies, `$SCRATCH` stands for the scratch directory; set it to run them.
- **Scratch data:** the data dirs and the 1.14 GB seed were deleted afterwards.

## Summary: before → after

| Item | Before | After |
|---|---|---|
| **C1** `/check`, 5,000 cols × 2 rows | OOM kill at 12.86 GB (X1) | **400 `too_many_columns`**, 0.2 s, 0.32 GB |
| C1 `/check`, 1,000 cols | 13.7 s, **3.09 GB** | **1.9 s, 0.39 GB** |
| C1 stats profile job, 1,000 cols × 50 rows (found here) | **OOM kill at 13.66 GB** (`dmesg`) | 16.7 s, 0.56 GB, including upload, check and commit |
| C1 `/check`, 10M × 13 (narrow; no regression) | 23.4 s | 23.7 s (same box, back to back) |
| **C2** `GET /data-doctor`, 10M rows | > 12 GB, killed (X1); watchdog kill at 6.03 GB here | **10.2 s, 0.64–0.81 GB**, on a recorded 200,089-row splitmix64 sample |
| C2 `POST /data-doctor/apply` `drop_columns`, 10M | > 12 GB, killed (X1) | **27.7 s, 2.10 GB** (SQL path) |
| C2 apply, 5 fixes incl. `drop_duplicates`, 10M | — | 102.6 s, 2.10 GB. Verify ok; the diff equals the fixes exactly |
| C2 `POST /feature-lab`, 10M | **174.6 s before the 202, 10.68 GB** (X1) | **202 in 0.1 s**. The job runs 22 s on the sample, peak 1.19 GB |
| C2 Feature Lab SQL apply, 6 features × 10M | — | 50.4 s, 2.09 GB |
| **M1** 3 concurrent sibling uploads, 50 new accounts each | all three get ids **3000–3049** | 3000–3049 / 3050–3099 / 3100–3149. Diff: 0 changed, 50 added, 50 removed |
| **M2** `restore-splits` on a carried-from split | **500**, FOREIGN KEY constraint failed; child stuck at 409 | **200** restored. Both versions verify ok; a run on the child **succeeds** |
| **M4** Doctor apply fuzz (10 cases) | one 500; drop_rows `[1.5]`, `[true]` and `['3']` drop row 1; `[-1]`, `[1e30]`, `[10**12]` and `null` make no-op versions | **all 10 → 400** with a code. A repeat apply returns 200 with the same version; a no-op returns the version itself |
| **M5** `_row_id` / `_ROW` / `__row_id` column | `_row_id` accepted, then hidden by the system id | **400 `reserved_column`** on the upload, the legacy `POST /datasets`, edit rename and edit add_column |
| **M6** `DELETE FROM run_inputs` | deletes the row | **refused**: "run inputs are immutable". With the trigger dropped by hand, P19's chain still fails check 3, and POST /runs → 409 |
| **M3** overfit check on a `__row_id`-gapped version | "n/a", fit-gap KeyError (X1) | computes: training 0.8294 vs holdout 0.7349, z = 5.0. No KeyError in the log |

---

## C1: wide CSV at `/check` (and the stats job behind it)

**Cause.**
- The typing scan ran one `SELECT` holding every aggregate, about 7 per column.
- DuckDB's *planning* of that query is quadratic in the column count. `prof.py` at 1,000 columns: one query 7.6 s / 2.2 GB; chunks of 100 columns 1.0 s.
- P08's table-stats profile (`lake/stats.py compute`) has the same shape, with up to 8 aggregates per column.
- The stats job runs in the API process right after a commit. A 1,000-column table committed in seconds and then **took the API to 13.66 GB in the background**:

  ```
  Killed process 2084 (uvicorn) … anon-rss:13663140kB
  ```

  X1 could not reach this, because `/check` died first.

**Fix.**
- `upload._check_file`: aggregates in chunks of 64 columns (`SCAN_CHUNK_COLS`). `count(*)` rides with the first chunk, so a narrow file is still one CSV scan.
- `stats.compute`: chunks of 48 columns (`STATS_CHUNK_COLS`).
- `config.max_columns()` = `ENSEMBLE_MAX_COLUMNS`, default 2,000. Above it, `400 too_many_columns`, on CSV and on Parquet (`_guard_names`).
- DuckDB keeps running under the existing job memory limits (upload 1,536 MB, job 2,048 MB). The memory limit was never the problem: planning memory is not governed by it, which is why chunking was needed.

| Run | Before (818a105) | After |
|---|---|---|
| `wide.py 250` | 0.9 s, 0.56 GB | — |
| `wide.py 1000` | 13.7 s, 3.09 GB | 1.9 s, 0.39 GB (2.2 s, 0.41 GB on the merged head) |
| `wide.py 2000` | — | 5.3 s, 0.46 GB |
| `wide.py 5000` | (X1: OOM at 12.86 GB) | `400 {"code": "too_many_columns", "message": "This file has 5,000 columns; the limit is 2,000 (ENSEMBLE_MAX_COLUMNS). Split it into narrower files."}`, 0.2 s, 0.32 GB |
| `c1e2e.py 1000 50` (upload → check → commit → stats ready) | stats job OOM-killed at 13.66 GB | check 2.1 s, commit 1.1 s, stats ready in 16.7 s; peak 0.56 GB |
| `c1e2e.py 2000 200` | — | check 5.9 s, commit 2.5 s, stats 32.3 s; peak 0.61 GB |
| `chk10m.py` (10M × 13 seed) | 23.4 s | 23.7 s |

## C2: no full pandas read in Data Doctor or Feature Lab

**Design.** Up to `config.doctor_rows()` rows (`ENSEMBLE_DOCTOR_ROWS`, default **200,000**), every path is unchanged. Above it:

- **The report** (`doctor_frame.load`) reads a deterministic sample.
  - Rule: a row is in the sample when `splitmix64(__row_id XOR (seed+salt)·φ) mod 10⁶ < 10⁶·rate`. This is P11's `lake.frame._u`, with seed 42 and salt 7.
  - Only `__row_id` is read in full; the sampled rows come back by a semi join.
  - The sample is recorded in the report: `sample: {rows, of, rate, seed, salt, rule, ids_sha256, note}`.
  - The checks and the label-quality job (`start_labels(frame=…)`) run on the sample.
  - Evidence row numbers and drop_rows / relabel fix rows are mapped back to **the version's positions** (`remap_report`), so a suggested fix applies to the right rows.
- **Apply** (`doctor_sql.apply`) compiles every fix into a chain of DuckDB views over the parent table (`_d0 … _dN`). It is written through the VersionWriter, as P07's edit path is:
  - kept rows keep their `__row_id`;
  - the parent's split is carried by join;
  - the other tables are carried;
  - dedupe is on;
  - lineage kind `data_doctor` / `clean_labels`, name "· fixed vN", the derived_from edge, and decisions.json carried over.
  - Each fix's effect (cells / rows / changed) is counted in DuckDB.
  - `drop_near_duplicates` is pandas-only (it was already capped at 120k rows) and gets **400 `too_many_rows`** above the budget.
  - The SQL writer runs DuckDB at `ENSEMBLE_DOCTOR_WRITE_MB` (1 GB). At 2 GB the 10M apply peaked at 3.20 GB; at 1 GB, 2.10 GB.
- **Feature Lab:**
  - The route only checks the schema plus one `SELECT DISTINCT … LIMIT 2`, then enqueues the P22 job and returns the 202.
  - The job searches the bounded frame. The holdout is the version's holdout mask taken at the sampled positions.
  - Kept features are written to **every** row as SQL (`featurelab_api._feature_sql`, through `doctor_sql.write_chain`): window aggregates for `agg`, the search's fitted edges for `bin`, and pandas' `dayofweek` (Monday = 0).

**The SQL paths give the same data as the pandas paths.** The same 3,000-row dataset was run through both; the SQL path was forced with `ENSEMBLE_DOCTOR_ROWS=1000`. Output from `c2eq.py`, compared with `GET /diff`:

```
to_missing         pandas ds_7ae489b9 vs sql ds_3b39b9f4: matched 3000 changed 0 added 0 removed 0 changed_by_column {} schema {'n_columns': [9, 9]}
split_codes        … matched 3000 changed 0 …      add_missing_flag … matched 3000 changed 0 …
drop_duplicates    … matched 2979 changed 0 …      drop_rows        … matched 2996 changed 0 …
drop_columns       … matched 3000 changed 0 …      merge_classes    … matched 3000 changed 0 …
rescale            … matched 3000 changed 0 …      relabel          … matched 3000 changed 0 …
all (6 fixes)      pandas ds_0afc361f vs sql ds_0d27e014: matched 2977 changed 0 added 0 removed 0
```

- The effects counts are identical too: 182 / 252 / 569 / 21 / 4 / 1,533 / 1,498 / 3 relabelled with 2 changed.
- A repeat of each SQL apply returns 200 with the same version (dedupe).

Feature Lab, all 10 candidates (every op: agg mean / count / std, ratio, diff, date_part dayofweek / month, bin, log, cross), via `fl_sql_eq.py`. `_version_sql` against `featurelab.compute` on the full 10k-row parent:

```
a_mean_by_cust same · cust_count same · b_std_by_cust same · a_per_b same · a_minus_b same · d_dayofweek same ·
d_month same · b_bin5 same · log_b same · c1_x_c2 same · other columns unchanged: True     (verify ds_eeeb4b9f → ok)
```

The pandas Feature Lab path (10k rows, at the default budget) still keeps `a_mean_by_cust`, as X1 found. 20 s, 0.35 GB.

**Row numbers on a sample point at the right rows.** `remapchk.py` looks up each evidence row with `GET /rows?offset=<row>` and compares the values:
- 3,000 rows with a 1,000-row budget: 15 same, 0 different.
- 10M: **20 same, 0 different**, including the label-quality examples.

**10M** (the `app.lake.seed` 10M × 13 seed, 1.14 GB; `big.py`):

```
upload 4.4 s · check 25.5 s · commit 19.5 s            peak 1.94 GB
GET /data-doctor → 200 (10.2 s)                        peak 0.64 GB (0.81 GB first call)
  sample {'rows': 200089, 'of': 10000000, 'rate': 0.02, 'seed': 42, 'salt': 7,
          'rule': 'splitmix64(__row_id XOR (seed+salt)*0x9E3779B97F4A7C15) mod 1000000 < 20000', 'ids_sha256': '7d613dae…'}
  label job (background, on the sample) → done        peak 0.86 GB; "cleanlab flags 2,806 labels (5.6%)", rows in 0…10M
  repeat report 9.5 s
POST /data-doctor/apply drop_columns [note, zip] → 201 (27.7 s)     peak 2.10 GB   how=sql
POST /data-doctor/apply [drop_duplicates, drop_rows [0,1,9999999], relabel [5,6], merge_classes segment, to_missing tenure_months [0]]
  → 201 (102.6 s)  peak 2.10 GB; rows 10,000,000 → 9,999,997; GET /verify ok;
  GET /diff: removed 3, changed_by_column {tenure_months: 27700, segment: 4999999, y: 2}, columns_added [tenure_months_was_missing]
POST /feature-lab {max_candidates: 2} → 202 (0.1 s); job succeeded 22 s later on 200,089 rows   peak 1.19 GB
_version_sql, 6 kept features × 10M rows (in-process, fl_big_sql.py) → 50.4 s, peak 2.09 GB
old code, GET /data-doctor on the same version → WATCHDOG killed API at 6.03 GB
```

**Found and fixed during C2.**
1. **The job connection does not keep `ORDER BY` order into Arrow or NumPy.**
   - DuckDB job connections run with `preserve_insertion_order = false`. `SELECT … ORDER BY __row_id` then `.to_arrow_table()` or `.fetchnumpy()` returned rows out of order.
   - The first 10M report showed label examples whose values belonged to other rows.
   - `doctor_frame` and `doctor_sql` now sort in Python and assert the ids.
   - **Caution for other lanes:** do not rely on `ORDER BY` reaching Arrow or NumPy on a `duck.job()` connection.
     - P08's `stats.sample_frame` takes its index from the result itself, so it is consistent.
     - P07's `edit_version` passes such ids only to `split_into`, which carries by join when no split is given, so nothing is broken today.
2. **A lake version may have no `datasets/<id>/` folder.** The report's write of `data_doctor_suspects.json` then gave a 500. The folder is now created first. X1 never reached this, because the report died first.

## M1: concurrent siblings get the same new `__row_id`s

**Fix.** `writer.reserve_row_ids(db, root, table, floor, n)` takes a range in one statement:

```sql
INSERT … ON CONFLICT DO UPDATE SET next_row_id = MAX(next_row_id, floor) + n RETURNING next_row_id
```

- It runs on `dataset_root_tables`, in a transaction committed at once.
- Every path that adds rows uses it:
  - next-version upload (`_commit_next`: the number of unmatched rows);
  - `datasets.derive` (Doctor, Feature Lab, Autopilot: `ParentRows.reserve`);
  - edit `add_rows` (`_n_added(ops)`, before the compile);
  - admin seed append.
- **Deviation from the brief.** The brief said "inside the commit transaction". The ids are part of the Parquet bytes, which are written *before* the version's commit transaction, so the range is reserved just before writing. A version that then fails leaves a gap; gaps are allowed (§5.6). The writer's commit still raises the counter to max + 1 (unchanged).

`m1.py`: v1 has 3,000 accounts. Three next-version uploads each add 50 *different* accounts (Z0…, Z1…, Z2…), and all three commit at once with `match {key: acct}`.

```
before:  ds_fa8a43a3 new ids 3000 - 3049 (Z0…) · ds_b52f3229 new ids 3000 - 3049 (Z1…) · ds_c66a18d9 new ids 3000 - 3049 (Z2…)
after:   ds_01db03d9 3100 - 3149 (Z0…) · ds_95ca32f3 3000 - 3049 (Z1…) · ds_6c215cc5 3050 - 3099 (Z2…)
         GET /diff ds_01db03d9 ds_95ca32f3 → matched 3000, changed 0, added 50, removed 50
```

X1's diff on the old code: "matched 3,050, changed 50". Three concurrent edits that each `add_rows` 5 rows (`m1e.py`) get 3155–3159, 3160–3164 and 3150–3154.

## M2: restore-splits on a split carried into a child

**Fix.** `splitstore.restore` no longer deletes and re-inserts the row.
- When the restored row keeps its id (the usual case: the row was rewritten in place), it is **updated in place**.
- Otherwise the restored row is inserted first, the children's `carried_from` is repointed, and then the bad row is deleted, all in the same audited transaction (`children_repointed` in the audit).
- A remaining integrity error is a **409 `restore_conflict`** naming the child splits, with nothing changed. It is never a 500.

`m26.py`:
1. v1 (3,000 rows) → run r1 → edit child (every 7th row deleted; v1's split carried into it).
2. Forge v1's split **in place**: a new self-consistent blob with 50 of 600 holdout rows, then `UPDATE splits` with `splits_ro_u` dropped.

```
before (818a105):
  verify v1: check 3 FAILED (audit records 8f118d541b9b…), check 4 FAILED (differs from runs run_e01820b7)
  verify child: check 5 FAILED: 2,571 surviving rows of spl_8c16e34faf73: 466 changed fold
  POST /restore-splits (offer) → 200;  POST /restore-splits (apply) → 500   sqlite3.IntegrityError: FOREIGN KEY constraint failed
  POST /runs on the child → 409
after:
  POST /restore-splits (apply) → 200 {"applied": true, "restored": {"id": "spl_b640f849e934", … "n_holdout": 600}, "from_run": "run_b44124b7",
                                      "replaced": {"id": "spl_b640f849e934", … "n_holdout": 50}}
  verify v1 → ok=True · verify child → ok=True
  POST /runs on the child → 201; run_1ee39f22 succeeded
```

## M4: Data Doctor apply: 400, never 500; strict row ids; no-op dedupe

**Fix.** `data_doctor.check_args(transforms, n_rows)` runs before anything else, on both the pandas and the SQL path:
- every whole number in a fix must fit in int64 (it is recorded in the manifest);
- `drop_rows` / `relabel` rows must be a non-empty list of whole numbers (not bool, float or string) in `0 … n_rows-1`, with no repeats.

Further:
- A `CanonicalError` that still reaches the route is mapped to **400 `value_invalid`**.
- `derive` is called with `dedupe=True`. A repeat returns **200** with `created: false` and `deduplicated: {existing}`.
- An apply whose result equals the parent's rows, columns and values returns **the version itself** (200, `deduplicated.reason`). Only the pandas writer re-encodes the table, so the data hash alone never matched the parent.

`m4.py`, v1 with 3,000 rows:

| Case | Before (818a105) | After (pandas path; the SQL path gives the same, see `m4_sql.txt`) |
|---|---|---|
| rescale from = factor = 2⁶³ | **500** | 400 `value_out_of_range` "Fix 1: from 9223372036854775808 is too large…" |
| rescale factor = 2⁶⁴ | 400 (for a different reason: from = 0) | 400 `value_out_of_range` (factor) |
| relabel label 2⁶³ | 400 | 400 `value_out_of_range` |
| drop_rows `[1.5]` | **201**, dropped row 1 | 400 `value_invalid` "1.5 is not one" |
| drop_rows `[true]` | **201**, dropped row 1 | 400 `value_invalid` |
| drop_rows `['3']` | **201**, dropped row 3 | 400 `value_invalid` |
| drop_rows `[-1]` | **201**, no-op version | 400 `value_out_of_range` "row -1 is not a row of this version (rows 0 to 2,999)" |
| drop_rows `[1e30]` | **201**, no-op version | 400 `value_invalid` |
| drop_rows `[10**12]` | **201**, no-op version | 400 `value_out_of_range` |
| drop_rows `null` | **201**, no-op version | 400 `value_invalid` "rows is a non-empty list of row numbers" |
| drop_rows `[1, 2]`, twice | 201 ds_4629ff51, then 201 ds_b121a286 | 201 ds_232241ad, then **200 ds_232241ad** `created: false` |
| relabel row 5 to its own label | 201 ds_5b04a51f (a new version) | **200 ds_6e470dd9** (the version itself), "these fixes change nothing" |

## M5: a user column named `_row_id`

**Fix.** `writer.is_reserved_name`: `__…` plus the API's id keys `_row_id` and `_row`, in any case. It is used by:
- the upload check (CSV and Parquet, `_guard_names`);
- the writer (`_schema_of`: every write path);
- edit `rename_column` / `add_column`;
- `derive.frame_arrow`.

The choice is a refusal, not a rename, so nothing is hidden or silently changed.

```
upload `_row_id,b` → 400 reserved_column "These column names are reserved for the row id: _row_id. Rename them…"
upload `_ROW,b` → 400 · upload `__row_id,b` → 400 · upload `row_id,b` → 200 (allowed)
legacy POST /api/datasets (multipart) `_row_id,b` → 400 (same message)
edit rename_column x2 → _row_id → 400 "'_row_id' is reserved…" · edit add_column _Row → 400
```

## M6: `run_inputs` rows cannot be deleted; the forged-root-split attack

**Fix.** `ri_ro_d` (BEFORE DELETE ON run_inputs → RAISE 'run inputs are immutable') is in `IMMUTABLE_TRIGGERS`, so it is created at startup. The demo reset lifts and restores it, as it does `splits_ro_d`.

X1's attack, `m26.py attack`:
1. Forge v1's split in place.
2. Rewrite its `split_created` audit row to the forged hashes (`audit_ro_u` dropped).
3. `DELETE FROM run_inputs WHERE split_id = …`.

```
before (818a105, P19 already in):
  (a) DELETE FROM run_inputs: deleted 1 row(s)                                      ← M6
  verify v1: check 3 FAILED: … chain link: the row does not re-hash to its row_hash (edited)   ← P19 catches the rewrite
  POST /runs → 409;  GET /teams/tm_default/audit/verify → ok: false, first_break #19 split_created "it was edited"
after:
  (a) DELETE FROM run_inputs → refused: run inputs are immutable
      verify v1: check 3 FAILED (chain link: edited), check 4 FAILED (differs from runs run_1cf0c637)   POST /runs → 409
  (b) the same with ri_ro_d dropped by hand (file access): deleted 1 row
      verify v1: check 3 FAILED (chain link: edited); child check 5 FAILED (466 changed fold)          POST /runs → 409
      GET /teams/tm_default/audit/verify → ok: false, first_break {"id": 19, "action": "split_created", "problem": "the row does not re-hash to its row_hash (it was edited)"}
```

The attack now fails at the trigger. Even with the trigger dropped, P19's audit chain catches the audit rewrite (check 3), so the forged split can't be trained on.

## M3: Model Doctor overfit check on a gapped version (re-verify only; `vela/doctor.py` is P11's)

`GET /api/runs/{run}/doctor` → `overfit`:
- **Gapped child** (every 7th row deleted, `__row_id` gaps), run_1ee39f22: status clear, "accuracy on the training rows 0.8294 vs the holdout 0.7349: gap 0.0946, z = 5.0 (SE 0.0191)", source "fit gap".
- **Parent**, run_b44124b7: "0.7650 vs 0.7567, z = 0.5".
- No `fit gap: KeyError` in the API log.

b38e2bc's `.iloc` fix holds.

## Merged head (97e7132, with P11 and P20)

Fresh data dir, `ENSEMBLE_DOCTOR_ROWS=1000`:
- `wide.py 5000` → 400 `too_many_columns` (0.3 s, 0.35 GB).
- `wide.py 1000` → 200 (2.2 s, 0.41 GB).
- `m1.py` → 3000–3049, 3050–3099, 3100–3149.
- `remapchk.py` → 15 same, 0 different.
- `m4.py` → all 10 junk cases 400.
- A 2-fix SQL apply → 201.

P20's masking of the Doctor's example rows wraps `_report`'s output, so it applies to the sampled report too.

## Deviations and notes for the orchestrator

- **M1:** the range is reserved by an atomic UPSERT … RETURNING just before the parts are written, not inside the version's commit transaction (see M1). Same guarantee; a failed version leaves a gap.
- **C2, below the budget:** at or below 200,000 rows the Doctor and Feature Lab paths are unchanged. Above it:
  - the report's counts are the **sample's** (the report says so in `sample.note`);
  - `drop_near_duplicates` is refused (400);
  - the SQL apply is used. It gives the same data as pandas (above).
- **C2 timing:** a 10M Doctor apply is 28–103 s and the Feature Lab SQL write about 50 s, inside a request or job, at ≤ 2.1 GB.
  - Each effect count re-runs the view chain, which is why a `drop_duplicates` mix is slow.
  - Fold this into X1's P11 scale-gate item (the 10M edit commit as a job with progress). The memory gate is met; the latency is the same class as the edit commit's.
- **Still in-process (not on the queue):** the label-quality thread. It now runs on the bounded frame (≤ 200k rows), peak 0.86–0.96 GB at 10M. It stays on P23's list.
- **New config:**
  - `ENSEMBLE_MAX_COLUMNS` (2,000);
  - `ENSEMBLE_DOCTOR_ROWS` (200,000);
  - `ENSEMBLE_DOCTOR_WRITE_MB` (1,024).
- **New files:** `app/doctor_frame.py`, `app/doctor_sql.py`.
- **Touched files (lanes noted):**
  - `lake/upload.py`, `lake/stats.py` (P05/P08);
  - `lake/writer.py`, `lake/derive.py`, `lake/api.py`, `lake/splitstore.py`;
  - `catalog/schema.py` (one trigger), `reset.py`;
  - `datasets.py`, `dataset_edit.py` (two reserved-name checks);
  - `data_doctor.py`, `data_doctor_api.py`, `featurelab_api.py`, `config.py`.
  - None of P11's or P20's files were edited. `vela/doctor.py` was only re-verified.

## Extra items from the orchestrator (after P11)

**Active learning on a gapped version** (`engine/active_learning.py:262`, `_missing_pool`; found by P11).
- **Cause:** `fold[pool.index]` used `__row_id` as a position.
- **Fix:** `fold[np.flatnonzero(sel)]`.
- **Repro** (`F-X1/al.py`):
  1. 3,000 rows, 300 customers, about 10 % blank labels.
  2. An edit deletes every 7th row.
  3. A run on the gapped child with `split_spec {strategy: group, group_column: cust}`.
  4. `POST /runs/{id}/active-learning {pool: missing, budget: 10, rounds: 1}`.

```
before (head without the fix): active learning: failed  error IndexError: index 2573 is out of bounds for axis 0 with size 2571
after:  active learning: awaiting_labels; queued rows [82, 1672, 87, 1186, 471, 1363, 50, 2095, 45, 2353]
        blank-label rows of the gapped version, features matching (GET /rows by __row_id): 10 of 10
```

**M3 against P11's code** (merged head, `_fit_gap` rewritten by P11):
- A random-split run on the same gapped child (run_50778423): overfit is "clear", training 0.8164 vs holdout 0.7970, z = 0.8, source "fit gap".
- No `fit gap: KeyError` in the log.

**Found, not fixed** (`vela/doctor.py`, P11's file, a line older than P11):
- `GET /runs/run_078f82fc/doctor` → **500** for the group-split run above. The cause is `TypeError: '>' not supported between 'NoneType' and 'int'` at `_missing`: `mr["ci"][1] > 0` (doctor.py:1473), when `_more_rows` returns a CI with `None` bounds.
- It does not depend on the gaps: the random-split run on the same version gets a 200.
- Fix: `mr["ci"][1] is not None and mr["ci"][1] > 0`.
