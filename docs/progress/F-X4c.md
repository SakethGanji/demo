# F-X4c: purge breadth (X4 C3)

**Goal.** After an approved purge of a column (e.g. `loans.ssn`) or of rows, the purged values must be unreadable
everywhere: through the API, on disk, in the database, and in S3.

**Result.** Fixed. I re-ran X4's purge scenario on SQLite, on Postgres 16 and on S3 (moto server). Before the fix, 13
files under the data dir, 5 DB tables and the API still held raw SSNs. After the fix, a grep for the 600 purged SSNs
across the data dir, the DB (and `pg_dump`) and every S3 object version finds **0 hits**. The purge's own sweep reports
`clean`, and old runs re-verify as `purged`. Models that did not use `ssn` still predict. Models that did use it are
refused with 409 `purged_model`.

## What was built

### `catalog/artifacts.py` (new): registry, walker, scrub, sweep

**1. Registry of derived artifacts.** A new `derived_artifacts` table (catalog `P01_DDL`, so it exists on both engines).
Writers call `record_safe(db, kind, path, version_id/table/run_id/object_id, columns)`. The hooked writers are one line
each:

| Writer | Kind |
|---|---|
| `engine/store.RunStore.create` | the run dir |
| `registry/store.create_version` | the deploy copy |
| `registry/store.create_batch` | batch input / scored |
| `catalog/datasets_api._export_csv` | the unmasked export cache |

The purge's inventory reads the registry rows of the affected versions and runs, and marks them `purged` afterwards.

**2. Exhaustive walker.** The registry is not trusted to be complete.

- *Linked locations, by convention:* every affected run's dir and predictor, the model versions built from those runs
  (their dirs and batches), and the export caches and dataset dirs of the affected versions. These are listed in the
  impact report as `impact.derived.files`, each with an action (`delete` / `redact`) and a reason.
- *By value:* every text file under the data dir and the lake dir is searched; binary files are searched as int64 /
  float64 bytes. Every JSON document in **every** DB table is searched (any text column; the table list comes from
  `sqlite_master` / `information_schema`). Other SQLite files under the data dir (e.g. `mlflow.db`) are searched and
  redacted cell by cell.

**3. The purged values ("needles").** They are read from the lake **before** the bytes are deleted:

- for a column purge, the distinct values of the purged columns;
- for a row purge, every value of the purged rows.

Values the kept data (the new version) still holds are removed, so a hit always means a copy of a purged value. Only
distinctive values are used: numbers with at least 6 significant digits (`ENSEMBLE_PURGE_MIN_DIGITS`) and strings of
at least 6 characters (`ENSEMBLE_PURGE_MIN_CHARS`). Matching works on numbers in every form (`123456789`,
`123456789.0`, `123,456,789`, int64 and float64 bytes), on tokens, and on multi-word values. **The values are never
stored.** They live in process memory for the execution only.

**4. Actions on each artifact** (`scrub`):

| Artifact | Action |
|---|---|
| `lake/exports/<v>` of affected versions | deleted (re-creatable) |
| AutoGluon `utils/data/*` (X.pkl, X_val.pkl …) of affected runs and deploy copies | deleted. The predictor still loads and predicts. |
| Model of a run that used a purged column as a feature or label, or whose model files hold a purged value | **Chosen: `purged_model`.** The predictor dir and every deploy copy are deleted. The run's `config.purged_model` is set to `{purge_id, why}`. Its model versions become `failed` with the error `purged_model: …`. Predict, batch, SHAP, scorecard score, anomaly score, the card and recourse all answer **409 `purged_model`** ("retrain on the new version"). Keeping the pickles was not an option: LightGBM thresholds and RandomForest split values *are* raw SSNs. |
| JSON / CSV / text artifacts: `top.json`, the `shap/` cache, `scorecard/report.json`, the credit report, fairness / recourse results, AL / annotation / shift files, batch `input.csv` / `scored.csv` / `drift.json`, run `config.json`, agent conversations | **Redacted in place.** A purged value becomes `"purged"`. In linked artifacts, a dict key equal to the purged column becomes `"purged"`, and a record that names the column (`feature` / `column` / `name` / `group` …) keeps its metadata and counts while its value-like fields (min / max / bins / cuts / top / values / label …) become `"purged"`. CSV cells of the purged column become `purged`. For a row purge, CSV rows of purged `__row_id`s are dropped. |
| Other binary files that hold a value | deleted |
| DB JSON: `runs.result` / `config`, `batch_jobs.result` (the preview), `uploads.check_json`, `datasets.profile` / `contract_result`, `table_stats.columns`, `model_versions.card`, `jobs.payload` … | **Redacted in place; the rows stay.** Afterwards, SQLite runs `VACUUM` and a `wal_checkpoint(TRUNCATE)`, and Postgres runs `VACUUM FULL` of the touched tables, so old row versions don't keep the text. |
| `audit_log` | never rewritten (hash chain); the sweep checks it |

**5. Post-purge verification sweep** (`sweep`). After the bytes are deleted, a sample of the purged values (≤ 500,
`ENSEMBLE_PURGE_SWEEP_SAMPLE`) is searched in:

- every file under the data dir and the lake dir (text, plus int64 / float64);
- every row of every DB table, and on SQLite the database file and its WAL;
- every object version in the S3 bucket.

It also checks the structure: purged blobs have no object version, delete marker or cache file left, and nothing
marked `delete` still exists. Any hit gives **`purge_incomplete`**: the purge stays `approved` (not `used`), and every
involved team's chain gets a `purge_incomplete` audit row naming the places. A clean result gives a `purge_sweep`
audit row. The result is stored in `executed.sweep` and returned as `sweep` on `GET /purges/{id}`.

`POST /api/purges/{id}/sweep` (`data.purge_decide`) re-runs the sweep. After a clean run, or after a restart, the
values are gone from memory, so the re-sweep checks the structure only and says `values: "unavailable"`.

### `catalog/purge.py`

- `impact()` adds `derived`, the inventory above, to the impact report that the owners approve.
- `execute()` reads the needles before the transaction, then runs: DB changes → DL2 holdout files → **scrub** → bytes →
  **sweep**.
- New audit rows: `purge_derived_scrubbed` (counts, purged models and versions, redacted DB columns), then
  `purge_sweep` or `purge_incomplete`.
- `resume` scrubs again when the values are still in memory, then deletes the bytes and sweeps.

### Small hooks in other lanes' files

| File | Change |
|---|---|
| `registry/api.py` | `_ready_version`: 409 `purged_model` (2 lines) |
| `engine/api.py` | predict, SHAP, scorecard score: `refuse_purged` |
| `anomaly/api.py` | score: `refuse_purged` |
| `engine/store.py`, `registry/store.py`, `catalog/datasets_api.py` | `record_safe` (the registry hooks) |
| `registry/scoring.py` | `try_it`: when the training frame is gone (purged), predict without the SHAP background instead of a 500. `score_batch`: score without the training reference: feature PSI is `no_reference` and reason codes are `skipped`. **Both were broken on the base too:** `pd-x4-nossn` predict gave 500 and its batch failed after the purge. (F-X3b owns `registry/*`: these are try/except fallbacks only.) |
| `lake/mask.ensure` | For a **deleted** version whose bytes are gone, mask from the existing tags instead of re-profiling. On S3 the anomaly top rows gave 500 after the purge (the cache refill of a purged blob). |

## Verification

The scripts are in `docs/progress/F-X4c/`:

- `setup.py`, `x4lib.py`, `api.sh`: X4's, with one change. The client retries once when the API drops a keep-alive
  connection after a 500 (X4 recorded these as `ReadError`).
- `setup2.py`: a second run that **drops `ssn`**, registered as `pd-x4-nossn`, with a batch whose file still carries
  the `ssn` column; plus warm-ups (credit report, unmasked export, fairness on `ssn`, recourse, card).
- `purge.py`: X4's purge scan, plus `pg_dump`, an S3 scan of every object version, the sweep, and predict checks.
- `rowpurge.py`: a row purge.
- `run.sh`, `rerun.sh`, `restart.sh`: the drivers.

The raw output is in `out/`.

| Pass | Before (base `9c91e7e`, `out/sqlite_base`) | After |
|---|---|---|
| Disk: files holding raw SSNs | **13**: export cache, `X.pkl` (384), `X_val.pkl` (96), `LightGBMXT/model.pkl`, `top.json` (200), `scorecard/report.json`, `shap/*.json`, two batches' `input.csv` / `scored.csv`, `ensemble.sqlite3` (111), WAL | **0** (SQLite, Postgres, S3) |
| DB tables holding raw SSNs | datasets 2, runs 10, batch_jobs 100, uploads 5, table_stats 2 | **{}** on all three. `pg_dump --data-only`: **111 → 0**. |
| S3 object versions (moto, versioned and Object-Lock bucket) | 2 of 51 hold SSNs (the blobs) | **0 of 51** |
| API as Ana, `unmasked=1&reason` | anomaly top 50, batch 50, batch download 50, scorecard 2, card 2 | anomaly top **0** (the column shows `"ssn": "purged"`), batch **0**, download **0** (`ssn` column = `purged`), scorecard **0** (`"bin": "purged"`), card **409 `purged_model`**, recourse **409** |
| Purge sweep | not built | `clean`: 500 of 600 values searched, 171 / 193 / 206 files, S3 51 object versions. Recorded in `executed.sweep`; audit `purge_sweep` `clean`. |
| Old runs re-verify | `purged` | `purged` (run and run2) |
| `pd-x4-nossn` (no ssn) predict / run2 predict / batch | **500** / 200 / (failed: FrameError) | **200 / 200 / succeeded** (`out/sqlite/batch_after.txt`: 10 rows; feature PSI `no_reference`, reason codes skipped; prediction PSI against the holdout predictions) |
| `pd-x4` (used ssn) predict / run predict / batch | 200 (`ssn` missing) | **409 `purged_model`** on each |

**Impact report** (SQLite) `derived.files`, each with its action:

- run dirs (redact);
- `runs/<run>/predictor` and the anomaly `runs/<run>/model` (delete: purged models);
- `runs/<run2>/predictor/utils/data` (delete);
- `registry/pd-x4/v1/predictor` (delete);
- `registry/pd-x4-nossn/v1/predictor/utils/data` (delete);
- two batches (redact);
- `lake/exports/<v1>` (delete);
- `datasets/<v1>` (redact).

`purged_models` lists the PD run and the anomaly run (both used `ssn`). `model_versions` marks `pd-x4` as purged and
`pd-x4-nossn` as not.

**Row purge** (`out/sqlite/rowpurge.txt`): 10 rows purged by `_row_id`. Before: 30 erased values (ssn / email / name)
in 9 files and 4 DB tables. After: **0 files, 0 tables**, the sweep `clean` (50 values). The new version holds 590 rows
and none of the erased values; the `/rows` page returns 500 rows, all of them with kept SSNs. No model was purged (no
model file held a value of those rows). The training frames were deleted.

**Caveat on the row-purge run.** It ran on a copy of the SQLite work dir, so the copied run configs still pointed at the
original dir's absolute paths. The value walk still found and cleaned the copy's own files.

## Deviations and notes

- **Model choice: `purged_model`, not "retrain required".** Retraining is left to the user (409 says "retrain on the
  new version"). A model of a row purge is purged only when its files hold a purged value; otherwise only its saved
  training frame goes. After a purge, registering a **new** version of an affected run cannot refit (AutoGluon's
  `utils/data` is gone): retrain instead.
- **The card of a purged model answers 409** rather than a card with `purged` fields (`_card` uses `_ready_version`).
  The DB card JSON is redacted all the same.
- **Postgres physical files** live outside the data dir. `VACUUM FULL` rewrites the touched tables. The logical dump
  (`pg_dump --data-only`) is clean. WAL segments and backups keep old pages until they rotate: that is an ops matter.
- **S3:** moto refuses PUT with an Object Lock retention unless Content-MD5 is sent, so the S3 pass ran with
  `ENSEMBLE_S3_LOCK_DAYS=0` on a lock-enabled, versioned bucket. P24 showed the governance bypass on MinIO.
  `delete_blob` removes every version and the cache file, and the sweep checks both.
- **Short or common values are not swept.** Values shorter than the thresholds (e.g. `segment = retail`) are not
  value-redacted outside linked artifacts and are not swept. The keyed redaction covers them in linked artifacts.
- **First SQLite run.** `out/sqlite/purge.txt` is the final run on a snapshot of the set-up (`rerun.sh`). The first run
  found two bugs (DuckDB 2.0 has no `.arrow()`; `model_versions.status` has a CHECK constraint, so a purged version is
  `failed` with a `purged_model:` error). The post-purge batch (3g) in that file was queued before the `score_batch`
  fallback existed; `batch_after.txt` is the re-test.
- **Pre-existing and unchanged:** `GET /datasets/{deleted v}/export` and `/runs/{id}/explain` give 500 after a purge
  (X4 m4). An unmasked export of a deleted version recreates an **empty** `lake/exports/<v>` dir, which the sweep
  ignores.
- **Not built:** the redacted manifest for sensitive column *names* (§5.10 step 4, P24). Column names stay in schemas;
  only values are purged.
