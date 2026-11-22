# F-X3b: X3 fixes for joins, the registry for join models, S3 and the cache (lane D2)

**Findings fixed:** X3 C1, M5, M6, M7, M8, M9, and the minors on join batches, S3 and the cache (m5, m6, m7, m8, m9,
m15, m16). See `docs/progress/X3.md`.

**Base:** `motion-components` @ 4839e75 (the X3 tracker head).

**Stack:**
- scratch venv from `requirements.lock`: duckdb 2.0.1, pyarrow 24.0.0, AutoGluon 1.6.3, Python 3.13;
- Postgres 16, a scratch cluster (initdb, port 5461);
- MinIO `RELEASE.2025-09-07T16-13-09Z`, built from source with Go. Buckets were made with Object Lock (GOVERNANCE)
  and versioning.

## Result

| # | Before (at 4839e75) | After |
|---|---|---|
| **C1** | `/predict` and `/score` on a join model scored the grain row without the join: 200, joined features missing; `/score` gave no `missing_columns`. | **Online scoring goes through the join.** The request sends the grain rows plus the other input tables, each as rows or `"pinned"` (read from the model's pinned version, re-hashed first). They go through `scoring.join_batch_frame`, the batch path: the CSV reader, the pinned types and `frames.materialise`. They are then read and cast as `score_batch` reads a batch. A missing table gives 400 `batch_table_missing` naming it, with `inputs` listing every table; a missing column gives 400 `batch_column_missing`. `/score` now lists `missing_columns` for every model. **Parity on rows:** 600 holdout rows scored online from the pinned tables equal the version's holdout predictions (max \|Δp\| 1.1e-16). Online scores equal the batch of the same files: 300 rows, the same predictions, \|Δp\| ≤ 5e-7 from the 6-decimal rounding in scored.csv. |
| **M5** | A date window on a BIGINT Excel serial or on `dd/mm/yyyy` text: 201, every windowed value NULL / 0. | **400 `window_type`.** DATE and TIMESTAMP columns pass. A text column passes only when every value reads as an ISO date; it is checked on the data, and the message counts the values that fail with an example (P14's `paid_on` is ISO text and still works). Numbers, bools and zoned timestamps are refused. A join batch counts window text that is not a date in `cast_failures` (`<col> (as a date)`). |
| **M6** | n:1 `DATE = TIMESTAMP`: compared as text, 0 of 3,000 matched. | **Compared as TIMESTAMP**: 3,000 of 3,000 matched, and the frame cells are right (`d__rate`). Keys of different families (a date with text, a zoned timestamp with a plain one, a bool with a number) get 400 `key_type_mismatch` naming both columns and types. A number with text keeps the old `join_types`. Frames built with the cast get a new identity (`engine: temporal_key_cast/1` in `frame_sha256`), so a 0-match frame from the old rule is never reused. Every other frame keeps its identity: the J4 frame is still `80645d2e…`, cache `d24e04aa…`, and P14's is still `58044424…` / `6fbecdf9…`. |
| **M7** | Two inputs named `cust` (one local, one from another dataset): trained and registered, but every batch failed with 400 `ambiguous_tables`. | **Aliased consistently.** A name used once is its own alias. A name used twice gets its role: `cust.join0`, `cust.join1` (and `<name>.grain`). The rule is `joins.input_aliases`, used by validation (`inputs[].alias`), batches and `/predict`/`/score`. A batch with `cust.join0.csv` and `cust.join1.csv` is scored (300 rows), and online with both tables pinned gives the same score. A bare `cust.csv` gives 400 `ambiguous_tables`, naming each alias with its version. |
| **M8** | A frame built while an input part held other bytes: 201, recorded under the clean shas. After the restore, frame verify said ok and the frame was reused. | **The inputs are re-hashed before and after the build**, and on the cached path too. On S3 that means the read-cache files DuckDB reads. A difference gives 409 `input_integrity` naming the table, audited `join_frame_refused`. **Frame verify** (`?verify=1`) adds `derived`: the frame is rebuilt from the pinned inputs with `frames.materialise` and compared with the cache by `__row_id`, every cell. The poisoned pre-fix frame `jf_8370f2713a` now verifies `ok: false`, "cells differ: y (3,000 rows)". |
| **M9** | Deleting a pinned input left its join cache live, pinned by `(join_frame, cache)`; frame verify said ok. | **The delete releases the cache.** `dsets.drop_version_refs` (delete, P24 purge, GC's stale-ref sweep) calls `joins.release_frames_of`, which drops the `(join_frame, cache)` refs of every frame that read the version. A cache no run pins becomes a GC candidate: the dry run lists `2d605ebe…` and the run deletes it. Frame verify reports the input `state: deleted` (`ok: false`, `inputs_deleted`). Versions deleted before the fix are listed by GC as `deleted_versions_with_refs`; the run releases them, and the next dry run lists the cache. A version a run trained on is still a delete blocker. |
| m5 | Binary `.csv`, garbage `.parquet` or garbage `.xlsx` in a join batch: 500. | 400 `batch_file_unreadable` naming the file. |
| m6 | Postgres: `{"version_id": 5}` to save or validate a join: 500 (`text = smallint`). | 400 `bad_request` on both engines. |
| m7 | S3: a blob missing from the bucket gave 500 on grid, rows/count, stats, manifest, join save/frames and delta check. | 409 `blob_missing` with the sha. A handler covers `BlobMissing` and the `FileNotFoundError` / DuckDB "No files found" errors on a blob path; other errors stay 500. |
| m8 | S3: a version with other bytes stacked on a blob key made every later commit that dedupes onto it answer 500. | 409 `blob_stacked`, naming the version that still matches and the fix path. `POST /api/admin/lake/blobs/{sha}/restore {by, confirm: "restore"}` (admin, audited `blob_restored`) verifies the newest version that hashes to the key and removes the versions stacked above it. The retried commit then gives 201. |
| m9 | S3 cache: rewriting the `.meta` sidecar along with the bytes passed the `stat` check. Previews served the tampered bytes and a frame was built from them. | The trusted stat record (size, mtime, ctime, inode) is kept **in process memory**, set by this process's fill or re-hash. The sidecar only orders the LRU. A file is re-hashed on its first use in each process. The tamper is now caught (`tamper_refetch` 1): the grid and a new frame read the bucket's bytes. |
| m15 | Numeric text that pandas coerced in a join batch was not counted in `cast_failures`. | Counted (`x1: 10`), and the summary has a `warnings` line. `"n/a"` is still read as missing: it is a standard NA token. |
| m16 | A join batch with repeated grain-key rows scored 302 rows. | 400 `batch_grain_key_repeated`, naming the key and examples. |

**Batches are byte-equal before and after the fix.** The X3 join model `mv_dbf1e70d` was scored three times, on one
data dir, by the head code and then by the fixed code. The `clean`, `coerce` and `reorder` file sets each gave a
`scored.csv` that `cmp` finds byte-equal (`batch_equal.txt`).

**P14 and P15 chains re-run on SQLite and Postgres.** Masking ids and hashes, the output equals each phase's recorded
output, except for:
- the trained models' numbers (accuracies, predicted counts, and the Doctor's leak flag that depends on importance);
- P15's `batch_table_missing` line: the runs above had "…, named after it:" in it. The wording was restored after the
  runs, and was then checked on a fresh API to give P15's exact text (the only change made after the runs; it touches
  messages only).

In detail:
- P14: frame `580444248db84f11`, cache `6fbecdf9e34dc5f4` (81,109 bytes), as recorded.
- P15: parity ok: the serving frame is byte-equal to the training frame, 800 holdout predictions equal; `check.py` finds
  0 mismatched cells. The tampered cache and the tampered input are still refused with 409 `parity_mismatch`.

## What was built

| Piece | Where |
|---|---|
| Online join scoring | `registry/scoring.py`: `score_join_rows`, `join_inputs_expected`, `_rows_upload`, `_predict_frame` (shared with `score_with`). `registry/api.py`: `_join_online`; `/predict` and `/score` branch for `cfg.join`. Request field `tables`; `/predict` also returns `frame_row`, `join`, and `explained_prediction` (SHAP runs on the joined row). |
| Batch path | `scoring.join_batch_frame` (aliases, `pinned_tables`, DataFrame uploads, unreadable files → 400, the grain key check, cast-failure counts, window-text counts). `BatchInputError.status`, and it pickles across the predict pool. Batch form field `pinned` (comma-separated aliases). |
| Join validation | `frames.key_family`, `is_date_type`, `_key_expr` (TIMESTAMP cast). `joins.validate`: `key_type_mismatch`, `window_type`, and `_window_text_check` on the data. |
| Frames | `joins.frame_engine` / `frame_sha256(…, engine)`, `_inputs_intact` (before/after), `derived_check`, `release_frames_of`, `check_inputs` (state `deleted`), `input_aliases`. `frames.compare_frames`. `joins_api`: verify adds `derived` and `inputs_deleted`; `_vid_ok`. |
| GC / delete | `dsets.drop_version_refs` → `joins.release_frames_of` (one call, in P24's file). `gc.stale_owner_refs` also lists deleted versions whose frames still hold a cache ref. |
| S3 / cache | `s3store.BlobStacked`, `_versions`, `_stack`, `restore_stacked`. `lake/api.py`: handlers (`blob_missing`, `blob_stacked`) and `POST /admin/lake/blobs/{sha}/restore`. `blobcache`: `_trusted` in memory, `stats.first_use_rehash`. |

## Verification (manual API calls, the way the UI chains them)

**Scripts** (`docs/progress/F-X3b/`):
- `chain.sh` runs X3 lane A's own repro scripts (`setup.sh`, `joins1.sh`, `reg.sh`, `pins3.sh`) unchanged, then:
  - `c1.py`: online versus batch versus training, on rows;
  - M7 role-named files, M9 + GC, `m8.py`, and the minors.
- `s3.py`: the S3 items, on MinIO.
- `batch_equal.sh`: head versus fix batches.

**Outputs:**
- the repros at the head: `base_sqlite.txt`, `base_postgres.txt` and `s3_base.txt`;
- the fixed code: `chain_sqlite.txt`, `chain_postgres.txt` and `s3_fix.txt`;
- the P14 and P15 chains: `p14_*.txt` and `p15_*.txt`.

Shortened from `chain_sqlite.txt`:

```
# C1 on rows (c1.py)
## 1. /predict without the other tables → 400 batch_table_missing ['cust', 'pays'] | inputs [('apps','grain'), ('cust','join:0'), ('pays','join:1')]
## 2. /score with cust lacking score → 400 batch_column_missing cust ['score']
## 3. /score 300 grain rows + cust rows + pays rows → 200 missing_columns [] | grain_rows 300 frame_rows 300 spec_frozen True
   batch of the same files: predictions equal True | max |Δp| 4.99e-07 (scored.csv rounds to 6 dp)
## 4. cust "pinned" → source pinned ds_872a95cc 1100 rows | max |Δp| vs rows sent 0.0
## 5. /predict one row at a time (its pays only) = /score: |Δ| ≤ 5.6e-09; A00001 frame {x__score 383, p__n 29982, p__amt90 2338004.0}; SHAP 5 reasons
## 6. the version's 600 holdout rows scored online from the pinned tables: predictions = holdout predictions True | max |Δp| 1.11e-16
# M5/M6: J1 DATE=TIMESTAMP → coverage 1.0 (3000 of 3000); J2 BIGINT serial → 400 window_type; J3 dd/mm text → 400 window_type
  ("34,735 of 34,735 values don't read as an ISO date (e.g. '01/01/2024')"); DATE = VARCHAR key → 400 key_type_mismatch
# M7: batch apps.csv + cust.join0.csv + cust.join1.csv → succeeded 300; predict with both pinned = the batch's row (0.797912)
# M9: frame verify ok False, cust state deleted; gc dry run join_cache 2d605ebe…; gc run deleted 1; delete of the trained-on pin → blockers
# M8: POST frames while apps holds other bytes → 409 input_integrity; restored → 201 (built from the right bytes)
# minors: 400 batch_file_unreadable ×3; 400 batch_grain_key_repeated; cast_failures {applied_on 10, x1 10}; 400 bad_request ×2
tracebacks in api.log: 0
```

`s3_fix.txt` (MinIO, Object Lock):

```
m9: cache part + .meta rewritten → grid serves the bucket's bytes True; POST frames 201, frame y = the bucket's True; tamper_refetch 1
m7: blob deleted → grid / rows/count / stats / manifest / POST joins / delta check: 409 blob_missing; verify MISMATCH (cust missing)
m8: stacked version → re-upload 409 blob_stacked (matching version named); restore without confirm 400; restore 200
    {restored True, removed_versions [the stacked one]}; re-upload 201; grid pays 200 (38,594 rows)
```

At the head, the same `s3.py` run (`s3_base.txt`) gives:
- m9: the grid serves the tampered bytes, and the frame is built from them;
- m7: six 500s;
- m8: 500, and no restore route (404).

**Postgres:** the same chain. `chain_postgres.txt` shows the same results, with the same frame and cache shas.

**After merging the branch** (F-X3a, F-X4b, F-X4c, U3), `chain.sh` and P15's chain were re-run on SQLite
(`chain_sqlite_merged.txt`, `p15_sqlite_merged.txt`). Masking ids, they equal the pre-merge output, except for:
- the trained models' numbers;
- m6: F-X3a's `_vid` guard answers first now (400 `bad_id`). Its guard replaced mine in `joins_api`.

P15's text now equals its recorded run, the `batch_table_missing` message included. F-X4c's purged-training-data
fallback (`_numeric_like`) is used by the online join path too.

## Deviations and notes

- **Online request shape (U lane).**
  - `/predict`: `{row, tables: {<alias>: [rows] | "pinned"}, explain}`.
  - `/score`: `{rows, tables}`.
  - The aliases are in a 400's `inputs` and in `validation.inputs[].alias`. They are the batch file stems.
  - A join model's `/score` always runs in a predict worker (`where: "worker"`): it builds a small frame with DuckDB.
  - A related table with no rows for the grain (an application without payments) is sent as `[]`.
- **`/predict` and `/score` differ by ≤ 6e-9.** One row scored alone and the same row in a 300-row `/score` call
  differ by up to 5.6e-09 in p̂. This is float32 batch composition in the predictor, not the join: the frame cells are
  equal. `/score` versus the training holdout is exact (1e-16).
- **The pinned lookup re-hashes the table on every call.** It is cheap for lookup tables. For a large table, send the
  rows instead.
- **`join_types` (a number with text) is kept for the UI's message map.** The new mixes use `key_type_mismatch`.
- **Frame verify now rebuilds the frame** (`derived`). This costs about one materialise, and runs only on
  `?verify=1`.
- **One small hunk in another lane's file:** `catalog/dsets.py` (P24) has 3 lines in `drop_version_refs`.
- **Not covered:**
  - m10 (`_min_rows_check` 500, in `engine/api.py`) and m11 (the edit's 400 versus 409): outside this lane's files.
  - m1–m4, m12–m14, m17 belong to F-X3a.
- **`"n/a"` stays an NA token** in batch files (m15's 50 `amount` values): pandas and the upload reader treat it as
  missing on purpose.
- **Clean-up.** The scratch APIs, the Postgres cluster, MinIO, the venv and the data dirs were stopped and removed
  after the run. The base worktree was also removed.
