# F-X5a: 10M register / Model Doctor, pool recovery, Autopilot audit, SIGTERM, edit preview (lane D)

**Scope.** The X5 findings K1, K2, J3, J4 and J6 (docs/progress/X5.md).

**Base.** `motion-components` @ 75fc854.

**Stack.**
- Pinned venv from `backend/requirements.lock` (Python 3.12; added only `ruff` and `py-spy` as dev tools).
- Postgres 16 in a scratch `initdb` cluster.
- Box: 4 vCPU, 15 GB, quiet apart from my own processes.

**10M seed.** `scripts/gen_seed.py` writes X5's shape with DuckDB: 13 columns (zip, account_no, branch, app_date, amount, …, y), 10,000,000 rows. The file is 0.83 GB against X5's 1.14 GB, because the values are synthetic. Its upload took 31.5 s and peaked at 2.03 GB.

**Processes.** The 10M API runs with `ENSEMBLE_WORKERD=external`, beside an external `python -m app.engine.workerd --slots train=1,duck=2,light=2`.

**Measuring memory.** Peaks come from `scripts/lib.py`'s `Peak`, which samples every 0.25 s the process trees of the API and the workerd (`X5_PIDS`). "Single" is the largest single process; "tree" is the sum over both trees.

## Results

| Gate | Before (X5) | After |
|---|---|---|
| **K1** register a single-table run at 10M | heavy-pool child OOM-killed at 13.5 GB; version stuck `building` | **ready in 246 s, peak single process 1.54 GB** (tree 2.61 GB). 2,000,000 holdout rows scored in batches; 100,009 held in memory; `holdout_predictions.csv` (2M rows) has the same lines as the run's own |
| **K1** register a join run at 10M (parity) | OOM at 12.6 GB | **ready in 365 s.** Parity `ok`: 10,000,000 = 10,000,000 rows, every cell compared in DuckDB, 0 unmatched ids. Predictions on the rebuilt frame, 2,000,000 rows in batches: max \|Δ\| 1.1e-16, ids and labels equal. Build process < 3.2 GB. The API was at 3.19 GB during the request's parity gate (DuckDB job limit 2 GB + the API's baseline) |
| **K1** a build that dies | left `building` until a restart | `failed`, with "The process running this build stopped: pool process 4855 was killed by SIGKILL (the kernel's OOM killer sends it when memory runs out)". The running step is marked failed with the same text |
| **K2** Model Doctor at 10M | OOM at 7.4–10.5 GB, GET never answers | `warm_doctor` completed by itself after both 10M fits, with 0 errors. `GET …/doctor`: **200 in 264 s**, peak single process 1.51 GB (the API), heavy-pool child below that. It reads a splitmix64 sample: 99,691 of 2M holdout rows and 100,171 of 8M training rows |
| **K2** kill a pool child mid-job | every later durable heavy job fails at once (`BrokenProcessPool`) until a restart | A build's process was killed mid-`measure` → that version `failed` with the reason. The 200k batch running in the same workerd went on unaffected. The **next build in the same workerd → `ready`**, with no restart |
| K2 pool hang (unit) | the killed call never answers (40 s cap) | both calls on that executor fail at 1.5 s; the next call answers (`out/pool_kill.txt`) |
| **J3** Autopilot audit alone, 12k rows | OOM-killed at 7.7 / 7.7 / 10.0 GB | **0.57–0.60 GB**, 440 items, 6.5–9.1 s (`out/audit_alone_fix.txt`) |
| **J3** full Autopilot on credit-default (12k) | (F-JOBS: 5.6 GB in the pool) | **finished**: profile → baseline → audit → apply → retrain → final, 161 s. Heavy-pool processes at VmHWM ≤ 0.71 GB; the largest process was the fit worker (0.82 GB) |
| **J4** SIGTERM with the heavy pool busy (2 open 50-shuffle importance requests) | waits as long as the work runs: 43.4 s here, for ever with a hung call (X5: > 3 min, then `kill -9`) | **exits 10.2 s after SIGTERM** with `ENSEMBLE_SHUTDOWN_GRACE_S=10` (default 20 s) |
| **J6** first edit preview at 10M (new version) | 44.6 s, 3.12 GB in the API (X5: 50.8 s) | **3.5 s, 1.24 GB**; repeat 2.8 s, 1.27 GB. Counts unchanged: matched 10M, changed 1 |
| batch scoring on the 10M model | (not reached in X5) | Before the fix, a 20k-row batch was **OOM-killed at 12 GB** (training table read whole for PSI and the reason codes' background, `out/batch20k_before_stages.log`). After: **20k rows succeeded at a peak of 1.34 GB** (`out/batch20k_after_stages.log`). **200k rows succeeded** (scoring, drift, reason codes for 84,903 adverse rows at ≈ 40 rows/s): peak 1.35 GB while it ran alone, 2.48 GB as the largest workerd process while a build ran beside it (`out/batch200k.txt`) |
| Postgres 16 pass (300k rows, external workerd) | — | upload → train (sampled, budget 20 MB) → register **ready** on the bounded path (`holdout` step, 60,000 holdout rows, peak 1.63 GB) → batch of 5,000 **succeeded** → Model Doctor **200** |
| small-data regression (12k rows, SQLite) | — | the same run registered on HEAD and on the fix: `holdout`, `holdout_ci`, the confusion matrix, `best_model`, `source.holdout_ci` and `data_sha256` are equal, and `holdout_predictions.csv` is **byte-equal** |
| edit preview regression | — | 8 op lists (cells, rename → set, set → rename, cast, replace, fill, delete + add rows, add_column): previews are **identical on HEAD and the fix** (`out/peq_*.json`) |

## What changed

### K1: the build never reads the table (`registry/build.py`, `engine/serving.py`, `frames.py`, `config.py`)

**`engine/serving.RunRows`.** It locates a run's rows without reading the table:
- the `__row_id` column and the label's validity (pyarrow, two columns);
- the run's folds by `__row_id`, from the pinned split blob (else the positional split file);
- column null flags from the Parquet footers, so a row subset is typed as the whole read types it.

It parses only the wanted rows (`lake.frame.iter_rows`), in batches when asked, and applies the wizard's column types as the fit applied them.

**`build_version` on a Parquet table above `ENSEMBLE_DOCTOR_ROWS`** (200k). A new `holdout` step:
- every holdout row with a label is located;
- at most `ENSEMBLE_BUILD_HOLDOUT_ROWS` (100k, new) are held in memory as a splitmix64 sample (salt 1). They feed latency, distillation, pruning checks, monotone and quantile;
- every holdout row is scored with P11's `lake.frame.score_holdout` in batches of `ENSEMBLE_HOLDOUT_BATCH_ROWS`. That gives `holdout_predictions.csv` and the version's metrics (`holdout_result`, `scored {rows, batches, of, in_memory}`).

A smaller table takes the old code path unchanged (verified byte-equal above).

**Serving parity at scale.**
- The frame-level comparison was already DuckDB (`IS DISTINCT FROM` over a FULL OUTER JOIN, under the DuckDB job memory limit).
- The prediction comparison is new: `prediction_parity_batched` reads the rebuilt frame's holdout rows by `__row_id` in batches (`serving.iter_file_rows`), scores them and compares by `__row_id`.

**`metrics_with_ci`.**
- The resample index array was (B, n): 500 × 2M × 8 B = 8 GB. It is drawn per resample now. The stream is the same; checked equal in numpy and by the byte-equal regression above.
- Above `ENSEMBLE_CI_ROWS` (100k, new) it is an m-out-of-n bootstrap (m = 100k, B ≤ 200), deviations scaled by √(m/n). The card's `method` says so.

**The other branches (EL / survival / multilabel / segmented / anomaly).** They read the whole table for a latency measurement that uses ≤ 2,000 rows. They now read `_head(cfg)`: the table up to `DOCTOR_ROWS`, else its first 100k rows. `frames.read(ref, rows=…)` now reads only the row groups in the window (`_read_window`) instead of reading everything and slicing.

**A build that dies.** See "pool recovery" below: the version is `failed` with the reason, never left `building`.

### K2: the Model Doctor and Vela read a bounded sample (`vela/rows.py` new; `vela/doctor.py`, `vela/missing.py`, `vela/findings.py`, `vela/api.py`)

**`vela.rows.load(cfg, hp)`.**
- Up to `ENSEMBLE_DOCTOR_ROWS` rows: the table is read whole, exactly as before.
- Above that: a splitmix64 sample of the holdout predictions (cap/2, salt 11) and of the training rows (cap/2, salt 12), parsed by `__row_id`. A positional split over the sample is handed back, so `_fit_gap`, the gap analysis (`missing.measure`) and Vela's findings run unchanged.

Bootstrap SEs on the sample are scaled to the whole holdout by √(m/n): the fit gap, `f_overfit`, `twin_se`. `sample` is recorded on the doctor's measurements and the gaps.

Two other whole-table reads are bounded the same way:
- **Smart Intake** for "next steps" (`vela/api._next_steps`) reads `rows.sample_table`.
- **`serving.holdout_frame`** above `DOCTOR_ROWS` returns a bounded holdout sample (`bounded_holdout`). Its callers are permutation importance, threshold calibration, the monotone check, latency benchmarks, reason-code stability, the recourse check and `scorecard holdout_index`; all of them read the holdout for diagnostics. On a big table, `holdout_index` positions index the sample.

**Batch scoring (`registry/scoring.py`).** Found while checking "batch scoring works". `_train_frame` read the whole training table for three things: the PSI reference, the column types the batch is coerced to, and the reason codes' background. The fix found the same 9–12 GB OOM there. Above `DOCTOR_ROWS` it is now a 200k-row splitmix64 sample of the training rows (`serving.bounded_train`). The flow kinds' reference read is bounded the same way.

### K2: pool recovery (`engine/pool.py`)

**The hang.** `HeavyPool`'s done-callback called `executor.shutdown()` on a broken executor. That callback runs on the executor's management thread inside `terminate_broken`, which holds the executor's shutdown lock, and `shutdown()` takes the same lock. The management thread deadlocked, so the other futures of that executor were never resolved. This is the X5 "GET hangs forever" (warm_doctor and the page's GET on one executor), reproduced in `scripts/pt2.py` against HEAD's pool. `_reset` now shuts down on a thread of its own.

**`HeavyPool.call` / `run` watch the pool processes.**
- A process that has exited (they only exit when killed) ends the wait with `BrokenProcessPool("pool process N was killed by SIGKILL (…OOM killer…)")`.
- The executor is replaced and its other processes are killed.
- A stopped or cancelled job ends the wait too.

**Durable `heavy` jobs (builds, batches).** Each job runs in a one-process pool of its own, embedded or in a workerd. Before, embedded jobs shared the interactive pool, and a workerd ran them in its own process.
- So a kill fails only that job (shown above: the build died, the concurrent batch went on).
- `run_job` marks the version or batch `failed` with the reason, and fails the job. The next job starts a new process.
- `ENSEMBLE_HEAVY_IN_PROCESS=1` restores the in-process call.

### J3: the Autopilot audit (`autopilot/audit.py`)

Two blow-ups, measured stage by stage (`scripts/audit_stages.py`):
1. **The embedding's one-hot.** `pd.get_dummies` over every category column gave a dense 12k × ≈ 21k matrix (applicant_id, customer_id, application_date). Now:
   - ID-like columns (distinct values > half the rows) are left out of the embedding;
   - the other category columns keep their 30 most frequent values plus one "other" level;
   - this took the peak from ≥ 10 GB to 2.8 GB.
2. **sklearn's pairwise-distance chunks.** The cosine k-NN and cleanlab's `OutOfDistribution` chunk at sklearn's default working memory of 1 GB, which came to 2.5 GB here. The audit now runs under `sklearn.config_context(working_memory=128)`. The neighbours are the same; the peak went from 2.8 to 0.6 GB.

The exact-duplicate check still hashes all feature columns.

### J4: SIGTERM (`engine/shutdown.py` new; two lines in `main.py`'s lifespan)

At the end of startup, `install(app)` wraps uvicorn's SIGTERM / SIGINT handler:
1. **At once:** uvicorn's own handler runs (no new connections), and the embedded worker stops claiming jobs.
2. **After `ENSEMBLE_SHUTDOWN_GRACE_S`** (20 s): held jobs go back on the queue and their processes are stopped; the heavy pool's processes are killed (a waiting request gets its error); uvicorn's `force_exit` is set.
3. **`ENSEMBLE_SHUTDOWN_HARD_S`** (10 s) **later:** `os._exit(0)`.

A normal shutdown finishes before the grace period and is unchanged.

### J6: the edit preview (`datasets.py`, `dataset_edit.py`, `lake/derive.py`, `lake/upload.py`)

The 45–51 s first preview was not the re-hash, which costs < 1 s here: profiled with `py-spy` and `scripts/prof_preview2.py`. It was the parent's split being drawn and committed, plus a 10M-entry JSON view written, all inside the preview request.

- **No split in a preview.** A preview builds `ParentRows(..., split=False)`; the commit still draws or carries the split as before.
- **Only changed columns are compared.** The diff compared every column over a 10M × 10M join (5.6 s). The compiler now records the columns each op changed (`Compiled.touched`, already computed per op for `check_columns`). The diff compares only touched, renamed and cast columns: the same counts (8 op lists equal on HEAD) from a narrow join (< 1 s).

## Deviations and notes

**Files outside the lane's list** (small, said here):
- `frames.py`: `_read_window`.
- `config.py`: two knobs.
- `dataset_edit.py`, `datasets.py`, `lake/derive.py`, `lake/upload.py`: the preview, a few lines each.
- `registry/scoring.py`: bounded training reference.
- `main.py`: the two lines that install the shutdown hook. They are in the lifespan, not the middleware F-X5b owns.

**New config:**
- `ENSEMBLE_BUILD_HOLDOUT_ROWS` (100,000);
- `ENSEMBLE_CI_ROWS` (100,000);
- `ENSEMBLE_SHUTDOWN_GRACE_S` (20), `ENSEMBLE_SHUTDOWN_HARD_S` (10);
- `ENSEMBLE_HEAVY_IN_PROCESS` (off).

**What a big table means.** It is the same threshold everywhere: a Parquet table above `ENSEMBLE_DOCTOR_ROWS` rows. Below it, every path is the old one.

**Sampled diagnostics are recorded as such.** The Doctor's numbers on a 10M run come from a sample of about 200k rows. They are recorded under `sample` and in the check's `method` text. The version's metrics and `holdout_predictions.csv` still cover every holdout row.

**Interactive calls still share one executor.** A killed interactive call (SHAP, Doctor) still fails the other interactive calls in flight on that executor; they now get an error instead of hanging. Durable jobs are isolated.

**Open, not in this lane:**
- **`POST /runs` on the 10M join version** held 3.1 GB in the API request. It reads an ID column whole and the 10M split JSON (`engine/api.py` 560–700).
- **The parity gate in `POST /runs/{id}/register`** runs in the request (DuckDB job limit 2 GB + the API's baseline, 3.19 GB peak).
- **The 200k batch's reason codes run at ≈ 40 rows/s** (84,903 adverse rows ≈ 35 min). Memory stays ≤ 2.5 GB, but it is slow.
- **The Doctor at 10M takes 264 s**, mostly the gap analysis on its 200k-row sample.

**Not covered:**
- the join chain on Postgres (the single-table chain was run);
- S3;
- 50M rows.

## Files

- **`F-X5a/scripts/`:**
  - `api.sh`, `api_base.sh`, `workerd.sh`: start the API, the HEAD API and the workerd;
  - `gen_seed.py`: the 10M seed;
  - `scale.py`, `scale_pg.py`, `lib.py`: X5's scale gates, with the peak sampler extended to the workerd's tree;
  - `doctor10m.py`, `preview.py`, `batch.sh`, `stagemem.sh`: the Doctor, preview and batch gates, and batch memory per stage;
  - `pt.py`, `pt2.py`, `pt2_base.py`, `pt_helpers.py`: the pool kill tests;
  - `sigterm.py`;
  - `audit_alone.py` (X5's), `audit_stages.py`;
  - `preview_eq.py`, `prof_preview2.py`.
- **`F-X5a/out/`:** raw outputs.
- **Removed afterwards:** the scratch data dirs, the Postgres cluster and the HEAD worktree.
