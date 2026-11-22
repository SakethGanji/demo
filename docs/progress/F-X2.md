# F-X2: fixes for X2's findings in the CSV-era migration (C1, M1–M6, m1–m3)

**Plan:** `docs/plans/data-platform-final.md` §5.11, §7 risk 9. **Findings:** `docs/progress/X2.md`.
**Lane:** D. **Base:** `c386e96`.

**Stack:** duckdb 2.0.1, pyarrow 24.0.0, pandas 2.3.3, AutoGluon 1.6.3, Python 3.12, in a scratch venv.

**Data:** all data dirs were under the session scratchpad, on ports 8710–8713.

## Verdict

Every X2 finding is fixed and was shown fixed, each against a repro on the base head with the same data and the same script.

| # | At `c386e96` (repro) | Now |
|---|---|---|
| C1 | Run `run_d0993633` was created during A3's migration window. It succeeded, but its split JSON had no blob and no Parquet split, `split_id` was NULL, it had no pins, and re-verify gave `ok: false`. The job said `migrated`. | The same POST during the window gets **409 `version_migrating`**. Retried after the job, the run is pinned and re-verify gives `ok: true`.<br>With the lock **turned off**, the commit's re-check rolls back and starts A3 over. The run then gets `split_id`, the `legacy_csv` + `split` + `legacy_json` pins, and `ok: true`. |
| M1 | A cleanup during a later job deleted `ds_d2926b37.migrated`, which that job had migrated seconds earlier, with 0 passes since. | During a job, cleanup gets **409 `migration_running`**. After the job, the folders it just migrated are kept ("0 verify-all pass(es) started since its migration"). The older folders are deleted. |
| M2 | `GET …/feature-lab` listed 0 jobs, and `featurelab/fl_*.json` had no other copy. | It lists 1 job. The record is a `legacy_json` blob, referenced by the version and copied into the fresh folder. 0 files lack another copy. |
| M3 | The deleted version's split existed only in `split_files.content`. The run had no pins and its split was never checked. | The split is a `legacy_json` blob plus Parquet split `spl_d395af8d5d61`. The run is pinned (`split_id`, `split` + `legacy_json`), and re-verify checks the split (`ok: true`). |
| M4 | Outside change e (a pre-DL4 split JSON) was migrated. The Parquet holdout ≠ rL1's `holdout_predictions` by 1 row. | Skipped as **`split_conflict`**: "run run_42abff2f's holdout_predictions.csv names 1 row(s) the file does not hold out (5)". After it was resolved, Parquet fold = original and holdout = prediction rows. |
| M5 | Change c (split + `split_files.sha256`) and change f (data + `datasets` hashes) were both migrated. | c → **`split_conflict`** (the recorded copy hashes to 9ab1…, not the recorded 55b5…). f → **`data_conflict`** (audit row #27 recorded 2aceec23…, the DB holds 07b945de…). |
| M6 | `diff Q1→Q2` and `diff Q1→Q3` gave `identical: false`, 0 matched, 3000 added, 3000 removed. | Both give `identical: true` with 3000 matched (row-hash match against the parent). |
| m1 | Pre-DL4 bool NA came back as `None` (`astype(str)` gave `"None"`). | It comes back as `NaN` (`"nan"`). The round trip's `na_kinds_equal` is 35/35. |
| m2 | `POST …/edit` on CSV-era refresh v2 (no row map) → 500 `'_FreshParent' object has no attribute 'attach'`. | → **409** with an explanation. After its migration the same edit gives 201. |
| m3 | With a corrupted `legacy_json` blob, the cleanup dry run said `delete: true`. | `delete: false`, "1 of its legacy blobs do not re-hash (legacy_json:lineage.json)". |

**The replay still holds:**
- After all jobs and the cleanup, compared with X2's "before" (acfe988, the tampered copy), these calls are byte-identical outside `storage`:
  - compare 12/12;
  - explain 11/11;
  - runs 11/11;
  - holdout 11/11;
  - dataset 37/37;
  - feature-lab 37/37.
- `POST …/verify` is 32/37. The 5 that differ are:
  - 3 are the versions whose outside changes were undone: `ok` false → true, or the laundered hashes → the original ones;
  - 2 are a pre-existing `v` tie, explained under "Notes".
- The GC dry run has **0 candidates** now and at +60 days, with 0 orphans and 0 integrity findings.
- Every run is pinned, including the deleted version's run.

## What changed

All of the new logic is in `backend/app/lake/migrate.py`. The hooks in the other files are small. `splitstore.py`, `gc.py` and `verify.py` needed no change.

### C1: the per-version migration lock

- **The lock.** `migrate_version` takes `catalog_meta lake_migrate:lock:<id>` and holds it from the first check to the folder step.
- **The writers.** Each CSV-era writer first writes its own `lake_migrate:hold:<id>:<token>` row, then reads the lock and the version's storage. The writers are:
  - `datasets.ensure_splits` (CSV branch), which draws a split;
  - `engine/api.queue_run`, for a run whose split was drawn from CSV-era files (`cfg.split` without `split_id`);
  - `legacy_restore_splits`.
- **Why this is enough.** Each side writes before it reads, so at least one of them sees the other.
  - A writer that sees the lock gets 409 `version_migrating`.
  - A writer that sees the version is now Parquet gets 409 `version_migrated`. For a split draw, the request is served from the lake instead.
  - The migration waits for any live holds, for up to `ENSEMBLE_MIGRATE_LOCK_WAIT_S` (120 s), then skips the version as `busy`.
- **Stale locks and holds.** A lock whose job is no longer queued or leased is ignored, and so is any lock older than 1 h. A hold older than 15 min is ignored.
- **Re-check inside the commit transaction** (`_same_inputs`). The runs, the split files on disk and the `split_files` hashes are listed again. If anything changed, the transaction rolls back (`_Changed`) and the version is started over, up to 3 times; after that it is skipped as `busy`.
- **Run check before the commit** (`_check_runs`). Every run must have:
  - a `run_inputs` row;
  - its `split_id`: the split made from the file it read, or from the hash it recorded, or a twin file with the same folds;
  - its pins.

  Otherwise the transaction rolls back and the version is skipped as `runs_unpinned`.
- **Re-verify after the commit** (`_post_check`). Every run must pass `verify_run`. If one does not, `lake_migrations` is set to `failed` / `run_check`: the version is not reported migrated, and cleanup keeps its folder.
- **No blob-less split moves** (`finish_folder`). Before the rename, any split JSON with no blob is first kept as a `legacy_json` blob of the version.
- **New 409 code in run creation.** `create_run` answers `code: version_migrating` here, not `split_integrity`.

### M1 and m3: cleanup

- `verify-all` now also records when each pass started, under `catalog_meta verify_all:passes_started`. `verify_all:passes` stays a list of end times, the format P24 reads.
- Cleanup refuses with 409 while a `migrate` job is queued or leased.
- Per folder, cleanup deletes only when all of these hold:
  - the version is recorded migrated;
  - two passes **started** after that folder's `lake_migrations.at`;
  - every legacy blob the version or its splits hold re-hashes (`legacy_csv`, `legacy_json:*`, `frozen_holdout`, the split blobs);
  - every file under the folder is kept elsewhere: a live blob that re-hashes, or the same bytes in the fresh folder.

### M2: Feature Lab job records

- `_evidence` also takes the files under `featurelab/`. Each becomes a `legacy_json` blob with role `legacy_json:featurelab/<file>`.
- `finish_folder` copies `featurelab/` into the fresh folder.
- `repair_folders` runs at the start of every job. For data dirs migrated before this fix, it copies the records over and makes a blob of any JSON file that has none.

### M3: a tombstoned version's split

`_tombstone_splits` handles each `split_files` row of a deleted version:
- It decompresses `content` and requires sha(content) = the recorded sha. It also requires no run to have recorded a different hash. Otherwise the version is skipped as `split_conflict`.
- The copy becomes a `legacy_json` blob.
- When the rows can be named, it also becomes a Parquet split (`legacy_sha256` = the JSON's sha). The rows come from the tombstone's lineage row map, or 0..n-1 for a root. The split gets its `split_created` audit row.
- The runs are backfilled and pinned to it.
- `verify_run` now checks the split of a run on a deleted version: `ok: null` for the data, as before, but `ok: false` if the split fails.

### M4 and M5: cross-checks that stop laundering

- **Data.** The stored hash must equal the hash in the version's latest `dataset_created` / `dataset_version_created` audit row, and that row's chain link must hold (`S.audit_link`). Otherwise the version is skipped as `data_conflict`.
- **The split's recorded copy.** For each split file, `split_files.content` must decompress, hash to the recorded `sha256`, and equal the file. Otherwise the version is skipped as `split_conflict`.
- **Old runs' holdout rows.** Every succeeded run that read the file names its holdout rows in `holdout_predictions.csv`. Each such row must be a holdout row of the file: fold −1, and in its `holdout` list. With the frame read, every labelled holdout row must also be one that the run scored. Otherwise the version is skipped as `split_conflict`.
- **About "the split_created audit row".** No CSV-era split has one: DL4 never wrote it. The evidence for those splits is `split_files.content`, the hashes the runs recorded, and the runs' holdout predictions. Migrated splits do get a `split_created` row, which P09's check 3 then verifies.
- **How a person resolves a skip.** Restore the file: `POST …/restore-splits` for c, or from a backup. Or adopt the file as a new version (`POST …/adopt-disk-file`). The next job then picks the version up, as for the DL3/DL4 skips.

### M6: versions with no row map

- **Matching.** `_match_parent` matches the rows against the parent with the upload `Matcher`. The parent is already in the lake, because roots are migrated v1 first. The parent's declared key is used when it has one, else `row_hash`; duplicates are paired in order.
- **Ids.** Matched rows keep the parent's `__row_id`; the rest get fresh ids.
- **Recorded as.** `root_rows_source = "matched"`, with `legacy.matched` holding the method and counts. The edge stays `inferred`.
- **Compare.** `legacy_mapping` still reports "none" for these versions, as CSV-era compare did, so compare output is unchanged.

### Minors

- **m1:** `frames._v1_column` turns a missing bool cell into NaN.
- **m2:** `_edit_base` answers 409 for a CSV-era version with no row map: "…until it is migrated…, which matches them to its parent's rows".

**Files changed outside the three owned modules:**
- `datasets.py`: the `ensure_splits` hold and `_edit_base`'s 409. P12 owns other functions in this file.
- `engine/api.py`: `_csv_hold` around `store.create` in `queue_run`, and the 409 code in `_create_run`.
- `frames.py`: m1.
- `catalog/dsets.py`: passes the pass start time.

## Verification

### Setup

**The populated data dir.**
- X2's `gen.py dl3 / dl4 / head` drove the APIs of `9a01901`, `1270b77` and `acfe988` against one data dir.
- Result: 37 versions in 22 roots (36 CSV-era, including 1 tombstone, and 1 Parquet P06 child) and 11 runs.
- **Differences from X2's dir:**
  - Both champion steps answered 409 "v1 is building" (a timing issue in the old registry). So there are no champions.
  - X2's extra runs on loans v2 (rL2x0–2, and the evicted `run_17eaa284`) are not in `gen.py`, so they are not here.
- Ids: `output/state.json`.

**Scripts** (`docs/progress/F-X2/scripts/`, beside X2's in `docs/progress/X2/scripts/`):

| Script | What it does |
|---|---|
| `hook.sh` + `fx2_hook.py` | X2's `hook_api.py` on either code, plus `FX2_NO_LOCK=1` (turns off the lock and the holds) |
| `c1_check.py` | The C1 checks |
| `fx2_checks.py` | The finding checks through the API |
| `fx2_inproc.py` | m1 and M4, in-process |
| `fx2_untamper.py` | Undoes c, e and f |
| `m1_cleanup_midjob.sh` | X2's M1 repro |

### C1

**Run:** `hook.sh <code> data 8712 X2_SLOW=1.5 X2_SLEEP_AFTER_RUNS=ds_877f080f:25`, then X2's `during_job.py`.

**At `c386e96`** (`output/c1_base_check.txt`):
```
edit SQL refresh v2 (not migrated yet) 500 Internal Server Error                     ← m2
POST /runs ds_877f080f active → 201 run_d0993633 ; job finished: migrated 36, skipped 0
rA3c run_d0993633: verify ok=False data=True split={"recorded": "d90a3a1c…", "now": null, "ok": false}
   run_inputs: [('ds_877f080f', None, None, 'd90a3a1c…', 'engine_v1')]   pins: []
ds_877f080f.migrated/splits/random-stratified__active-2bb6b986.json d90a3a1ca5cc blob: [] split row: []
```

**Fixed** (`output/c1_fix_check.txt`):
```
edit SQL refresh v2 (not migrated yet) 409 {"detail":"ds_d2cd260e was made before row maps … until it is migrated …"}
POST /runs ds_877f080f active → 409 {"code":"split_integrity","message":"… is being migrated into the lake right now (job jb_aa429818c8bd): drawing a split is refused …"}
  (that run was before the code change: now code "version_migrating")
retry after the job: run_eeaed1b4 → verify ok True (the pinned Parquet split blob); run_inputs split_id spl_74d2c34103f8, pins ['split']
edit SQL refresh v2 after the job → 201
lake_migrations A3: ('migrated', None, 1)
```
After the change to the code, with a lock planted on A3 in `catalog_meta` (`output/lock_409.txt`):
```
POST /runs ds_877f080f active → HTTP 409 {"code":"version_migrating","message":"version_migrating: ds_877f080f is being migrated into the lake right now (job -): drawing a split is refused …"}
ds_877f080f/splits: random-stratified__y-95cb0bfd.json only (no split drawn)
```

**Fixed, with `FX2_NO_LOCK=1`** (`output/c1_nolock_check.txt`, `c1_nolock_hook_lines.txt`). This shows the commit's re-check working alone:
```
FX2 HOOK: migration lock and writer holds off
X2 HOOK: ds_877f080f runs listed (0); sleeping 25 s before the commit
WARNING ensemble.migrate: migration of ds_877f080f: runs run_58cb305b changed before the commit; starting it again
rA3c run_58cb305b: verify ok=True data=True split ok=true ; run_inputs split_id spl_892a6c563455 ; pins ['legacy_csv','legacy_json','split']
ds_877f080f.migrated/splits/random-stratified__active-2bb6b986.json 66430aed6c88 blob: ['legacy_json'] split row: ['spl_892a6c563455']
```

### M2–M6, m1: X2's outside changes a, c, e and f

**Setup:**
- `tamper.py state.json data a c e f` was applied to a copy of the populated dir.
- "Before" was recorded on that copy with `acfe988` (`record.py`, 283 calls).
- `frames_before.py` was run on the tampered copy and on the original.

**Dry run and job at `c386e96`** (`output/plan_base.json`, `output/checks_base.txt`):
```
by_action {'migrate': 34, 'skip': 1, 'tombstone': 1}
ds_18c0260f migrate hashes match   (e)   ds_088264c3 migrate hashes match (f)   ds_877f080f migrate hashes match (c)
GET /datasets/ds_cea09414/feature-lab → 0 job(s); ds_cea09414.migrated/featurelab/fl_c2429ac5.json: blob []
split_files splits/random-stratified__y-95cb0bfd.json 82fe5512a3c3…: blob [], Parquet split []; run_0c84a824 pins []
GET /datasets/diff a=Q1 b=Q2 → method=row_id identical=False rows={'matched': 0, 'added': 3000, 'removed': 3000}
m1 L1 flag: kinds=['NoneType'] astype(str) of a missing cell=['None']
M4 rL1 pinned to spl_e8fa3135c4d9: Parquet holdout 119 rows, holdout_predictions 120 rows, preds not in the Parquet holdout: 1
unpreserved.py: files that cleanup deletes with no other copy: 1 (ds_cea09414.migrated/featurelab/fl_c2429ac5.json)
```

**Fixed** (`output/plan_fix.json`, `output/checks_fix.txt`):
```
by_action {'migrate': 31, 'skip': 4, 'tombstone': 1}
ds_18c0260f skip split_conflict … run run_42abff2f's holdout_predictions.csv names 1 row(s) the file does not hold out (5) …
ds_088264c3 skip data_conflict … audit row #27 (dataset_version_created) recorded 2aceec23f519… when it was made, the database holds 07b945de4979… now …
ds_877f080f skip split_conflict … the copy recorded when it was drawn hashes to 9ab1909af92b…, not the recorded 55b5e7f67f04… (split_files was changed outside the app) …
ds_d2926b37 skip data_changed …                                                       (a, as before)
GET /datasets/ds_cea09414/feature-lab → 1 job(s): ['fl_c2429ac5']
  ds_cea09414/featurelab/fl_c2429ac5.json: blob ['legacy_json'], refs ['legacy_json:featurelab/fl_c2429ac5.json']
split_files splits/random-stratified__y-95cb0bfd.json 82fe5512a3c3…: blob ['legacy_json'], Parquet split ['spl_d395af8d5d61']
  run run_0c84a824: split_id spl_d395af8d5d61, pins ['legacy_json', 'split']
  GET /admin/lake/runs/run_0c84a824/verify → ok=None deleted=True split={"recorded": "82fe…", "now": "82fe…", "ok": true}
GET /datasets/diff a=Q1 b=Q2 → identical=True rows={'matched': 3000, 'added': 0, 'removed': 0, 'changed': 0}
GET /datasets/diff a=Q1 b=Q3 → identical=True rows={'matched': 3000, 'added': 0, 'removed': 0, 'changed': 0}
m1 L1 flag: kinds=['float'] astype(str) of a missing cell=['nan']
unpreserved.py: files that cleanup deletes with no other copy: 0
```

### M1: resolve, then cleanup during the job

**Steps** (`m1_cleanup_midjob.sh`):
1. Undo the outside changes (X2's `untamper.py`, and `fx2_untamper.py` for c, e and f).
2. Run `verify-all` twice.
3. Start the job with `X2_SLOW=5`.
4. Call `cleanup?dry_run=0` as soon as the first folder of this job exists.

**X2's own setup (changes a, b, d; 3 versions skipped in job 1):**
- **At `c386e96`** (`output/m1_base.log`): `HTTP 200 … ds_d2926b37.migrated DELETED`.
- **Fixed** (`output/m1_fix_abd.log`):
  ```
  HTTP 409 {"code":"migration_running","message":"A migrate job is leased (jb_a5c62372fcc1): cleanup waits for it to finish."}
  ds_d2926b37.migrated still there
  after the job, dry run: deletable 33 kept [('ds_874aaf2d.migrated', '0 verify-all pass(es) started since its migration (…00:00:32…); 2 are needed'), ('ds_d2926b37.migrated', …), ('ds_f37c4091.migrated', …)]
  ```

**The a/c/e/f dir:**
- The run gave the same 409.
- After the job, `cleanup?dry_run=0` deleted 32 folders and kept the 4 just migrated (`output/cleanup_after_job3.json`).
- After `verify-all` twice more, it deleted those 4 too (`output/cleanup_final.json`; 0 `.migrated` folders are left).

### m3: a corrupted legacy_json blob after two passes

The test appended a byte to the `legacy_json:lineage.json` blob of loans v2.

| Code | Dry-run result for that folder |
|---|---|
| `c386e96` | `('ds_ce0294a5.migrated', True, None)` |
| fixed | `('ds_ce0294a5.migrated', False, '1 of its legacy blobs do not re-hash (legacy_json:lineage.json)')` |

### The replay after all jobs and the cleanup

Compared with "before" (`output/jdiff_before_vs_cleanup.txt`, X2's `jdiff.py`):
```
compare 12/12 · explain 11/11 · run 11/11 · holdout 11/11 · dataset 37/37 · featurelab 37/37 identical outside storage
verify_post 32/37: ds_d2926b37 (a undone: ok false → true), ds_088264c3 (f undone: stored/now 07b945de… → 2aceec23…),
  ds_877f080f (c undone: split stored/now 55b5… → 9ab1…), ds_a9da3943 / ds_adf58078 (`v` 5↔4, see Notes)
diff: row results equal for 13 of 15 (incl. both SQL refresh pairs, identical/matched 3000 as before); the 2 others
  are the pairs whose outside change was undone (changed 3 → 2, 1 → 0); column statistics are richer, and the
  diff cache is warm, as X2 noted
```

**Round trip** (`roundtrip.py`, `output/roundtrip.json`):
- `dtypes_equal` 35/35 and `na_kinds_equal` 35/35 (m1).
- `legacy_equal`, `text_equal` and `table_values_equal` are 33/35. The 2 that differ are C1 and LR, which were read before on the tampered copy.
- **rL1:** `parquet_fold_eq_original` true and `parquet_holdout_eq_pred_rows` true (M4). At the base it was the tampered fold.
- **Every other run:** Parquet fold = before = original, and Parquet holdout = prediction rows.

**Run re-verify after the cleanup** (`output/checks_final.txt`):
- 10 of the 10 live-version runs give `ok: true`, each with a `split_id`.
- The deleted version's run gives `ok: null` (by design), with its split `ok: true`.

**GC dry run** (`output/gc_api.json`, `output/gc_future60.txt`):
```
API now: blobs {'live': 236} candidates 0 orphans 0 integrity 0 stale_tmp 0
+60 days: candidates [] orphans [] integrity []
legacy_csv 32/32 · legacy_json 100/100 · split 32/32 · frozen_holdout 1/1 · table 33/33 · manifest 37/37 referenced
runs and their pins: every CSV-era run legacy_csv,split,legacy_json; run_0c84a824 (deleted version) split,legacy_json; run_fa0ba5c5 (P06 child) split
```

## Notes

- **Pre-existing, not from this change: `v` ties.** Accounts AR and AD were created in the same second. `acfe988` numbers them v5/v4; `c386e96` numbers them v4/v5 in `POST …/verify`. The base head shows the same swap on the same dir. `datasets.version_no` still says 5/4.
- **Pre-existing: verify-all doesn't re-hash `legacy_json` blobs.** In the m3 test, verify-all passed with a corrupted lineage blob. Cleanup now catches it. P24 may want verify-all to re-hash every legacy blob too.
- **New `catalog_meta` keys, for P23:**
  - `lake_migrate:lock:<id>` and `lake_migrate:hold:<id>:<token>` are transient;
  - `verify_all:passes_started` is new.
  - The SQL is portable: `substr`, `json_extract` cast-free string compares, and no catch-and-continue inside a Postgres transaction (the `split_files` and `jobs` tables exist on both engines).
- **Cleanup's response:** each folder now carries `migrated_at`, `verify_all_passes` and `unkept_files`. The 409 for missing passes is answered only when every folder is waiting only on passes. When some folders can be deleted, the call deletes those and reports why the others were kept.
- **Not covered:**
  - multi-million-row versions;
  - S3;
  - Postgres;
  - a crash during cleanup;
  - concurrent Doctor or Feature Lab writes during the job. A Feature Lab record written into a folder mid-migration is caught by cleanup's file check and by `repair_folders`, not by the lock.

## Files

| Path | What it holds |
|---|---|
| `docs/progress/F-X2/scripts/` | `hook.sh`, `fx2_hook.py`, `c1_check.py`, `fx2_checks.py`, `fx2_inproc.py`, `fx2_untamper.py`, `m1_cleanup_midjob.sh` |
| `docs/progress/F-X2/output/` | The C1 runs (base, fixed, no lock); both plans; the checks (base, fixed, final); `unpreserved`; M1 and m3; GC; round trip; the jdiffs; `state.json` |
| `docs/progress/F-X2/output/before`, `after_cleanup/` | compare, explain, run, holdout and `POST …/verify` JSON |

The scratch venv, worktrees and data dirs were in the session scratchpad and are removed.
