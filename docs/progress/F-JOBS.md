# F-JOBS: long writes as queued jobs, in-process threads onto the queue, the missing.py fix

Lane O, a follow-up after P22/P23 (the tracker's carried notes, before X5's scale gates).

**Done:**
- The three long 10M writes are queued jobs with progress, idempotent by job id:
  - edit commit;
  - Data Doctor apply;
  - the Feature Lab SQL write (already a `featurelab` job since P22; it now reports progress and commits by job id).
- The five in-process threads run on the P22 queue: stats profile (already there), Data Doctor labels, annotations,
  shift and Autopilot. Each ran through the queue with one forced lease expiry and no double effect.
- The `vela/missing.py` clean-labels shape bug is reproduced and fixed.
- A P22 worker bug found on the way is fixed: a worker that re-leased a job it had just lost ran it twice.
- Every new statement ran on SQLite and on Postgres 16.

## What was built

| Piece | File | Notes |
|---|---|---|
| Progress column | `catalog/schema.py` (`P01_COLS["jobs"]`) | `jobs.progress TEXT`: JSON `{pct 0–1, phase, at}`. Added by the existing ALTER path on SQLite and by `ADD COLUMN IF NOT EXISTS` in `pg_schema.ddl()` on Postgres. A claim resets it to NULL. |
| Queue | `jobs/queue.py` | **New:** `set_progress`. `progress` is parsed in rows and listed by `GET /api/jobs`.<br>**Attempt-scoped leases (fix):** `heartbeat`, `finish`, `set_progress` and `requeue` take `attempt` (`AND attempts = :a`), and `requeue` takes `worker_id`. |
| Job context | `jobs/progress.py` (new) | `bind(db, job, worker, attempt)` is a ContextVar Reporter. `job_id()` gives the VersionWriter its idempotency key; `mark(pct, phase)`; `band(lo, hi)` (Feature Lab: search 0–60 %, write 60–100 %); `track(con, lo, hi, phase)` follows a DuckDB statement through `con.query_progress()` from a poll thread.<br>Writes at most 1 per second, and only while the attempt still holds the lease. Outside a job every call is a no-op.<br>`threshold_rows()` reads `ENSEMBLE_JOB_ROWS` (new, default 200,000). |
| Write jobs | `jobs/writes.py` (new) | `enqueue` / `accepted` (the 202 body). Handlers `run_edit` and `run_doctor_apply`.<br>`JobRefused`: the 4xx the synchronous route would have sent becomes `result.refused {status, detail, message}` (PII-scrubbed like the route).<br>`_finished_version`: a re-leased job whose version is committed and finished (lineage edge written) returns at once.<br>`_default_after`: the default is settled once; a re-run that finds the version already default writes and audits nothing. |
| Edit route | `catalog/datasets_api.py` (one 11-line hunk) | Not a preview and parent rows > threshold → enqueue `edit` with the current default captured, then **202**. Otherwise unchanged (synchronous). |
| Doctor route | `data_doctor_api.py` | Rows > threshold → validate `by` and `transforms`, enqueue `doctor_apply`, **202**. The label job is now queued (`db=` passed). |
| Edit write path | `datasets.py` `edit_version` | `VersionWriter(job_id=JP.job_id(), duck_kw={"mem_mb": config.doctor_write_mb()})`: the 1 GB DuckDB cap the Doctor and Feature Lab writes already use.<br>Progress marks: verifying → compiling → writing (tracked) → carrying → committing → finishing.<br>`deduped == "job"` (this job's own earlier commit) now **finishes the post-commit steps** (all upserts) instead of being reported as a duplicate. The target-changed audit row is written once. |
| Doctor / Feature Lab SQL write | `doctor_sql.py` `write_chain` | The same: job id on the writer, progress marks, and `deduped == "job"` finishes rather than returning a duplicate. |
| Feature Lab | `featurelab_api.py` `run_job` | Progress: reading → searching (by journal length / candidates) → the write in the 60–100 % band.<br>A re-leased job whose version is finished skips the search.<br>The search frame is released before the SQL write (peak 2.52 → 2.14 GB). |
| Worker | `engine/workerd.py` | Handlers: `edit`, `doctor_apply` (duck); `doctor_labels` (light); `annotations`, `shift` (train); `autopilot` (api). Dead hooks for annotations, shift and autopilot.<br>`JP.bind` around every handler; `ctx.progress()`; a `job_refusal` exception fails the job with `result.refused`.<br>**Fix:** `held` is keyed `id#attempt`, an entry is removed only by its own context, and a lost attempt never records its result. |
| Label quality | `data_doctor.py` | `start_labels(db=…)` queues `doctor_labels`: one live job per `version\|target`, and `label_state(db=…)` asks the queue.<br>`run_labels_job` loads the same bounded frame (`doctor_frame.load`). The file is written whole (tmp + rename), so a re-run rewrites the same file. With no queue: the thread, as before. |
| Annotations | `engine/annotations_api.py`, `engine/annotations.py` | `_spawn` queues `annotations` (one per run). `run_job` runs `python -m app.engine.annotations <run_dir> <worker pid>`; the process now watches the worker.<br>`_status`, `active` and `shutdown` ask the queue. `bind(db)` is called from `main.py`. |
| Shift | `engine/shift_api.py` | `start` queues `shift`; one at a time across the workbench by the queue (409 as before). `run_job` runs `python -m app.engine.shift <dir> <worker pid>` after clearing a killed attempt's `fits/`.<br>Cancel: `Q.cancel`, and a queued check is closed at once. `_state`, `active` and `shutdown` ask the queue. |
| Autopilot | `autopilot/engine.py` | `Manager.start` queues one `autopilot` job (resource `api`: API processes only, because it uses the heavy pool; `max_attempts` 5). `run_job` runs the Pilot. `stop` also cancels the job. `resume_all` re-queues autopilots that have no live job.<br>A lost or shutdown attempt raises `Yield` from `cancelled()` and `save()`: it never overwrites the state again, never cancels its in-flight run, and never posts a run.<br>Re-lease: `_recover` adopts a run posted but not recorded (by its name, created after the step started). The apply derives with `dedupe=True`; apply and synthesize first look for a version they already derived (name + `Autopilot <id>:` note). |
| `main.py` | one line | `bind(db)` for `annotations_api` and `shift_api`. |
| missing.py | `vela/missing.py` | `_proba_into` / `oof_proba` (details below). |
| UI | `src/shared/api/jobs.ts` (new), `EditVersion.tsx`, `DataReport.tsx` | `settle(promise, onProgress)` follows a 202 by polling `GET /api/jobs/{id}` every 1 s, then resolves to the route's body or throws the same `ApiError` (from `result.refused`). It tolerates an API restart (30 × 2 s).<br>The Create / Apply buttons show `Creating… writing 45 %`. `npm run build` passes. |

### SQL portability (P23)
Every new statement uses `:name` / `?` parameters and plain `UPDATE … WHERE`. One reads JSON:
`json_extract(details, '$.dataset_id') = ?`, a text comparison, so it needs no cast. They also use `IS DISTINCT FROM` and
`LIKE`. There is no `rowid` and no `json_set`, and nothing catches and continues inside one connection. All of them ran on
Postgres 16 (below).

## Verification

### Setup
- A scratch venv from `requirements.lock`.
- `ENSEMBLE_DATA_DIR=<scratch>/data`.
- API: `ENSEMBLE_WORKERD=external uvicorn app.main:app --port 8000`; one or two `python -m app.engine.workerd` (`train=1,duck=1,light=2`).
- The 10M rows come from `python -m app.lake.seed --rows 10000000` (1.14 GB CSV) through `/uploads` (4.2 s) → `/check` (20.9 s) → `/commit` (17.8 s), giving `ds_73b99d23` (10,000,000 × 13).

`drive.py` (scratch) does the POST, then polls `GET /api/jobs/{id}` every 0.5 s (0.1 s around a kill) and samples the
worker's `VmRSS` / `VmHWM`. A fresh worker is started before each measured job, so VmHWM is that job's peak (except where
noted).

### 1. 10M edit commit → 202, progress rises, one version

```
POST /datasets/ds_0f399aa5/edit {replace_values channel broker→Broker} → 202 in 0.03 s
  {"async": true, "job_id": "jb_1cfad1180637", "rows": 10000000, "threshold_rows": 200000, "poll": "/api/jobs/jb_1cfad1180637"}
  +0.5s leased  workerd-a85251@vm:2838
  +1.5s verifying 0.01 · +2.6s compiling 0.08 · +12.6s writing 0.12 → 0.20 → 0.31 → 0.39 → 0.46 → 0.50 → 0.53 → 0.60 → 0.65 → 0.70
  +31.8s carrying 0.72 · +35.4s committing 0.82 · +35.9s finishing 0.9 · +42.9s succeeded done 1.0
  result: {status_code 201, id ds_9b963a45, created true, rows 10000000, default {is_default true}}
  worker VmHWM 2.13 GB (sampled peak 2.07 GB)
```

The first run, before the edit writer got the 1 GB cap (default `ENSEMBLE_DUCK_JOB_MB` 2 GB):
- 78.9 s, of which 41 s was the parent's first re-hash in `verifying`;
- peak **3.04 GB** (X1 measured 3.85 GB in the API for the same commit);
- with the cap: **2.13 GB**, the class of the Doctor and Feature Lab writes.

### 2. Kill workerd mid-job, restart it → re-run, no duplicate version

`killtest.sh` (scratch):
- start a fresh worker and drive the job;
- `kill -9` the worker at the given progress;
- start a new worker 5 s later;
- count the root's versions and `datasets.job_id`.

```
edit, kill at writing 0.31:
  +15.2s KILL -9 workerd 2994 · +21.8s leased attempt 2 workerd-8ca701@vm:3156 · … writing … · +65.7s succeeded
  janitor (the API's): "worker workerd-c137a4@vm:2994 is gone (its process is not running on vm) (attempt 1 of 2 …)"
  versions of the root 3 → 4 · datasets with job_id=jb_487ba302f042: [('ds_bfbbe1f3', 4, 'edit')] · job succeeded, attempts 2
edit, kill at finishing 0.9 (after the commit):
  versions 4 → 5 · [('ds_d98aa65d', 5, 'edit')] · result resumed: true (attempt 2 found the version finished)
  edges parent→new 1 · audit for the version: dataset_version_created 1, split_created 1, dataset_default_set 1 (settled once, by attempt 2)
edit, kill at committing 0.82: versions 5 → 6 · [('ds_6ee2ccca', 6, 'edit')] · resumed: true
after the attempt-scoped fix (§6), kill at writing 0.44: versions 12 → 13 · [('ds_013c16ce', 13, 'edit')] · attempts 2 · new worker VmHWM 2.12 GB
```

The commit and its lineage edge land within the same second here. To exercise "committed, edge not yet written", I
called the write twice in-process under one job id (`rerun_window.py`), which is what the next holder does:

```
edit attempt 1:   id ds_e92c63f0 created True | versions with job_id: [ds_e92c63f0] | edges: 1
edit attempt 2:   id ds_e92c63f0 created True | versions with job_id: [ds_e92c63f0] | edges: 1
doctor attempt 1: id ds_c74321cb created True how sql | versions with job_id: [ds_c74321cb] | edges: 1
doctor attempt 2: id ds_c74321cb created True how sql | versions with job_id: [ds_c74321cb] | edges: 1
```

Before this change, attempt 2 was answered "deduplicated" and skipped the post-commit steps.

### 3. 10M Data Doctor apply

```
POST /datasets/ds_73b99d23/data-doctor/apply {drop_columns [note, zip]} → 202 in 0.02 s
  verifying 0.01 · compiling 0.05 · writing 0.30 → 0.74 → 0.75 · carrying 0.76 · finishing 0.9 · succeeded in 25.8 s
  result: {status_code 201, how sql, rows 10,000,000 → 10,000,000, dataset ds_927d7d79 'applications 10M · fixed v1'}
  worker VmHWM 2.16 GB          (F-X1, in the request: 27.7 s, 2.10 GB)
kill -9 at writing 0.52 → +16.3s leased attempt 2 on the new worker → succeeded at 48.9 s
  versions 7 → 8 · datasets with job_id=jb_321d716ff3c1: [('ds_3f7d28d1', 8, 'data_doctor')] · new worker VmHWM 2.21 GB
```

### 4. 10M Feature Lab SQL write

**Data.** The seed's columns carry no signal for a ratio, so I made one with a 10M edit job:
- `scaled2 = income * (1 + y + tenure_months / 120)`, giving `ds_d26a0e70`;
- that edit job also showed the contract-warning path: `default {is_default: false, why: "1 contract warning(s): column_removed"}`;
- a perfect `income × (1 + y)` was first rejected by Feature Lab's leakage guard (single-feature AUC 1.000 ≥ 0.9).

```
POST /datasets/ds_d26a0e70/feature-lab {candidates [ratio scaled2/income]} → 202 in 0.05 s (fl_61145c56 → queue job jb_da91f52014ef)
  reading 0.01 · searching 0.05 → 0.6 · verifying 0.604 · compiling 0.62 · writing … · carrying 0.904 · committing 0.94 · finishing 0.96 · succeeded 64.2 s
  version ds_326cfd65 'applications 10M · features v1', columns_added [scaled2_per_income], rows 10,000,000, how sql
  worker sampled peak 2.44 GB, VmHWM 2.52 GB → the search frame is now released before the write:
kill -9 at compiling 0.62 → attempt 2 on the new worker (search re-run, then the write) → succeeded at 97.3 s
  versions 11 → 12 · datasets with job_id=jb_8e0d259004c4: [('ds_af2526ba', 12, 'feature_lab')]
  sampled peak 2.14 GB during the job (F-X1, the write alone: 2.09 GB)
```

The new worker's VmHWM was 2.46 GB. That includes the 10M `profile` job (`table_stats` of `ds_af2526ba`, 12.7 s), which
ran on the same worker's duck slot while Feature Lab, a train-slot job, was finishing.

### 5. Small datasets stay synchronous; a refusal inside a job

```
POST /datasets/account-churn/edit {add_column one=1}                 → 201 in 0.44 s (ds_04049f31, the full body as before)
POST /datasets/account-churn/data-doctor/apply {drop_columns [account_id]} → 201 in 0.33 s (ds_4fe0c08a)
POST /datasets/ds_73b99d23/edit {rename_column no_such}              → 202 → job failed in 2.6 s,
  result.refused {status 400, detail "Operation 1 (rename_column): no column 'no_such' (columns: zip, …)"}
  (the UI's settle() rethrows it as ApiError(…, 400), exactly like the synchronous 400)
```

### 6. Each moved thread kind on the queue, with a forced lease expiry

`expire.py` (scratch) forces an expiry:
- SIGSTOP the holding workerd;
- set `lease_until` in the past until the janitor re-queues the job;
- wait for the next lease;
- SIGCONT the old holder (its heartbeat then finds the lease lost).

Two workers were running.

| kind | trigger | after the forced expiry | no double effect |
|---|---|---|---|
| `profile` | delete `ds_013c16ce`'s `table_stats`, `GET …/stats?wait=0` | `lease … expired (attempt 1 of 2)` → attempt 2 on the other worker → succeeded | `table_stats` rows for the table: 1 (10,000,000 rows) |
| `doctor_labels` | `GET /datasets/ds_9b963a45/data-doctor` (10M; the label job runs on the 200,089-row sample) → `label_quality: running` | attempt 2 on `workerd-c29e60` → succeeded; old holder `job … is lost: stopping it` | one `data_doctor_labels.json` (written whole); the report then shows `label_quality done: cleanlab flags 2,806 labels (5.6%)`, the figure F-X1 recorded |
| `annotations` | `POST /runs/run_473e41df/annotations/sample {n_rows 400, per_row 3}` | attempt 2 on the other worker. The old holder's p̂ process (pid 6715) was killed when it found the lease lost; only the new one (6805, watching worker 5348) remained | status `ready`, 400 rows |
| `shift` | `POST /runs/run_473e41df/shift {holdout}` → 202; a second start → **409** "A shift check is already running" | attempt 2 on the other worker; the old shift process was killed by its holder | succeeded, AUC 0.497, "no shift" (holdout vs training, as expected) |
| `autopilot` | `POST /autopilot {credit-default, roc_auc ≥ 0.99, 120 s, auto, layer1}` | expired while the baseline fit was in flight → re-leased by the same API process. Its log: "Resumed on api-16cd30 (attempt 2) … following run_051473b5" | final run: **2 runs** (baseline `run_051473b5`, retrain `run_c2b683fe`); `finished`, verdict not met (0.9297) |

**The bug the Autopilot found (P22 worker), now fixed.**
- **Trigger.** On the first runs, the forced expiry was re-applied to the next attempt, and the same API process
  re-leased the job.
- **Why it ran twice.**
  - `Worker.held` was keyed by job id, so the old attempt's thread removed the new attempt's entry when it exited.
  - Nothing then extended the new lease; it expired naturally 60 s later (`lease … expired at 00:07:36`).
  - `heartbeat` / `finish` matched only `(id, leased_by)`, so the stale attempt still looked "ok".
- **Effect.** Two pilots ran at once, and `ap_a0ebf618` posted three "Retrain" runs (`run_38dccdd3`, `run_cb0ddd4c`,
  `run_ac761d3d`).
- **Fix.** Leases are now `(worker, attempt)` end to end, plus the Autopilot `Yield` rules above. Re-run `ap_95e7fecc` went
  through two consecutive forced expiries (my helper again caught attempt 2): attempts 3, both old attempts "lost:
  stopping it", 2 runs, one state.

**Environment note.**
- Two earlier Autopilot runs (`ap_c350b448`, `ap_2896f851`) failed in the audit step with `BrokenProcessPool`. The kernel
  OOM-killed the API's heavy-pool child: the session's cgroup limit is 14.3 GB.
- `autopilot.audit.audit` alone peaks at **5.6 GB** on credit-default's 12,000 rows (`audit_alone.py`, 47 s). That code is
  unchanged by this phase. A pool child also kept 4.6–6.3 GB from earlier work.
- With only the API and one worker running, the audit passed.

### 7. Postgres 16 (scratch instance, port 5434)

A second API on `ENSEMBLE_DB_URL=postgresql:///ensemble?host=/var/tmp/fjobspg…`, with an embedded worker and
`ENSEMBLE_JOB_ROWS=1000` / `ENSEMBLE_DOCTOR_ROWS=1000`, so a 10k-row sample takes the job and SQL paths:

```
information_schema: jobs.progress exists (migration)
POST /datasets/account-churn/edit → 202 → succeeded attempt 1 api-33c2ce, result 201 ds_1cf099fb, default set
POST /datasets/account-churn/data-doctor/apply → 202 → succeeded, how sql, ds_9e2b6448
GET /datasets/credit-default/data-doctor → doctor_labels jb_fce7eac0ad01 succeeded (progress {pct 0.2, phase fitting})
dialect postgres: audit json_extract query ok · Autopilot _derived query ok · _finished_version → ds_1cf099fb
set_progress on a finished job → False (no-op) · progress parsed {'pct': 1.0, 'phase': 'done', …}
```

### 8. `vela/missing.py` clean labels: (622,2) vs (622,1)

**Cause.** A fold whose training part holds one class: a class with a single training row, as a group split with a gap
can leave. LightGBM fit on one class reports `classes_ = [0]` but `predict_proba` still answers two columns, so
`Pt[np.ix_(b, classes_)] = pp` failed.

**Fix.** `_proba_into` writes the columns by class code; one class seen means that class with probability 1. `oof_proba`
holds the clean-labels OOF loop. `Q.predict` binary has the same guard: `p[:, 1]` of a one-class model was P(encoded 1)
of the wrong class.

`repro_missing.py`: 1,866 training rows (3 folds of 622), one positive:

```
before (git stash of missing.py): ValueError shape mismatch: value array of shape (622,2) could not be broadcast to indexing result of shape (622,1)
after:                            P (1866, 2) row 7 [1. 0.] rows sum to 1: True
```

This is the exact error recorded in `doctor_gaps.errors`. The full gaps path needs a group-split gapped run, and I did
not train one. The fix is in the function that path calls.

## Files touched

**New:**
- `backend/app/jobs/progress.py`
- `backend/app/jobs/writes.py`
- `src/shared/api/jobs.ts`
- `docs/progress/F-JOBS.md`

**Changed:**
- `backend/app/jobs/queue.py`, `backend/app/engine/workerd.py` (P22's; the attempt fix);
- `backend/app/catalog/schema.py` (one `P01_COLS` line);
- `backend/app/catalog/datasets_api.py` (**the edit route: one 11-line hunk**; nothing in upload/commit);
- `backend/app/datasets.py` (`edit_version` only; not `read_uploads`);
- `backend/app/doctor_sql.py`, `backend/app/data_doctor.py`, `backend/app/data_doctor_api.py`, `backend/app/featurelab_api.py`;
- `backend/app/engine/annotations_api.py`, `backend/app/engine/annotations.py` (`main` watches the worker pid);
- `backend/app/engine/shift_api.py`, `backend/app/autopilot/engine.py`;
- `backend/app/main.py` (one line: `bind`);
- `backend/app/vela/missing.py`;
- `src/features/models/data/EditVersion.tsx`, `src/features/build/DataReport.tsx`;
- `docs/plans/build-tracker.md`.

**Not touched (other lanes):** `lake/store.py`, s3store, `config.py` (P10b); `lake/writer.py`, `profiling.py`,
`samples.py`, `read_uploads`, upload/commit (P12).

## Deviations and notes for dependants

- **The edit writer now uses `ENSEMBLE_DOCTOR_WRITE_MB` (1 GB DuckDB)**, synchronous edits included. At 10M this brings
  the commit from 3.04 to 2.13 GB at about the same time (≈ 43 s without a re-hash).
- **No progress inside the parent re-hash.** A first edit of a 10M version spends ≈ 41 s in `verifying` at 1 %
  (`frames.hash_table`); its phase is named.
- **The 202 path validates less up front.** Errors in the ops arrive as `failed` with `result.refused` {status, detail}.
  `jobs.ts` rethrows them as the same `ApiError`.
- **New config: `ENSEMBLE_JOB_ROWS`** (200,000). The 202 decision uses the parent's `datasets.rows`.
- **Resources:**
  - `edit` and `doctor_apply` on `duck` (`mem_mb` 0, as every job until P11's estimator);
  - `annotations` and `shift` on `train` (they share train slots with fits);
  - `doctor_labels` on `light`;
  - `autopilot` on `api`: API processes only, because it uses the API's heavy pool (like agent turns).
- **Re-run leftovers.**
  - A killed attempt's blobs stay `pending` for GC.
  - An edit with `add_rows` reserves its new `__row_id`s again on a re-run, leaving a gap in ids.
  - The Feature Lab pandas path (≤ 200k rows) is not keyed by job id; data dedupe returns the same version.
- **Autopilot.**
  - The apply derives with `dedupe=True`: identical corrected data returns the existing version, even one another
    autopilot made (`ap_95e7fecc` reused `ds_d2c7ffb1` from `ap_a0ebf618`).
  - Remaining window: a holder learns it lost its lease within 2 s (one heartbeat check). In that window it can still
    post at most one run that the new holder does not adopt (adoption happens only in `_recover` at its start).
- **Label job across API processes.** The check-and-enqueue holds a lock per process only. Two API processes can each
  queue one job; both write the same file.
- **The X1/F-X1 note is resolved:** "Long 10M writes should become jobs with progress". So is the "still on in-process
  threads" list.
- **For X5 / ops.**
  - `autopilot.audit.audit` peaks at 5.6 GB on 12k rows. It runs in the API's heavy-pool child, which kept 4.6–6.3 GB
    afterwards. Two OOM kills came from it under a 14.3 GB cgroup.
  - An API with a busy heavy pool did not exit on SIGTERM (I had to `kill -9` it).
  - Both are pre-existing.
- **Scratch:** the venv, data dirs, the Postgres instance (`/var/tmp/fjobspg`), the CSV, the scripts (`drive.py`,
  `killtest.sh`, `expire.py`, `rerun_window.py`, `audit_alone.py`, `repro_missing.py`) and all API/worker processes were
  removed or stopped. No predictors or seeds were added to the repo.
