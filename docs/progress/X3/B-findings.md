# X3 / lane B: storage (MinIO), queue (10M crash / lease), 2 APIs + 2 workers, Postgres vs SQLite parity

**Scope:** fork B of X3. It was read-only: no product code was changed.

**Code under test:** `motion-components` at `55c491a`.

**Stack:**
- scratch venv from `requirements.lock`: duckdb 2.0.1, pyarrow 24.0.0, Python 3.13;
- Postgres 16, port 5442;
- MinIO `RELEASE.2025-09-07T16-13-09Z`, built from source with Go, on 127.0.0.1:9150. The bucket was created with Object Lock (GOVERNANCE, 30 days) and versioning.

**Ports:**
- APIs on 8750–8754:
  - 8750: SQLite, local store;
  - 8751: Postgres, local store;
  - 8752: SQLite, S3;
  - 8753 and 8754: two APIs on one Postgres DB and one lake, `ENSEMBLE_WORKERD=external`, `ENSEMBLE_JOB_LEASE_S=30`, heartbeat 10 s.

**Scripts and raw output:** `X3/B/`. The files are listed in the table below.

| File | What it is |
|---|---|
| `chain.py` | Core chain over HTTP (see below) |
| `chain_*.json` | Normalised chain output: no ids, no timestamps |
| `qdrive.py` | 10M job kill / SIGSTOP driver |
| `q*.txt` | Raw output of the queue runs |
| `conc.py`, `conc.txt` | Two APIs, concurrent writers on one root |
| `conc_put.py` | Concurrent identical S3 puts |
| `tamper_cache.py`, `s3_mutate.py` | Outside changes to the cache and the bucket |

The core chain in `chain.py` runs these steps:
1. a 3-table upload (`applications`, `bureau`, `payments`) with keys and a relation in the plan;
2. a `PUT` key;
3. an `_op` I/U/D delta on `applications`;
4. an upsert delta on `bureau`;
5. a `PUT` relation;
6. a join (n:1 + aggregate, with a 180-day window);
7. a frame and a join run;
8. the frame verify, the run verify and the dataset verify.

## Findings

| Rank | # | Finding |
|---|---|---|
| major | B-M1 | A join frame materialised while an input's bytes are wrong is kept as the frame of the clean inputs. It passes frame verify once the bytes are back, and a join run then trains on it. The run's re-verify answers ok. Registration (P15 parity) is the only gate that catches it. |
| minor | B-m1 | A blob missing from the bucket gives 500s, not a typed 409: grid, rows/count, stats, manifest, frame `POST`, delta `/check`. |
| minor | B-m2 | A key that holds other bytes (a stacked object version) makes every later commit that dedupes onto it answer 500, with no repair path. |
| minor | B-m3 | The cache's `stat` hit check is defeated by rewriting the `.meta` sidecar. Previews then serve the tampered bytes, and `GET /datasets/{id}/verify` (which checks the bucket) says ok. P10b's report says only a raw-device edit gets past `stat`. |
| minor | B-m4 | `POST /runs` on a tampered table whose row count differs answers 500 (`_min_rows_check` broadcast error), before the integrity gate's 409 `data_integrity`. |
| minor | B-m5 | Under contention, `POST …/edit` answers **400** with the text "Other versions of this dataset kept being committed; try again." Uploads answer 409 `commit_contended` for the same condition. A client can't tell that the 400 is retryable. |
| info | B-i1 | 16 writers at once on one root: 5 of 6 delta commits got 409 `commit_contended` and 3 of 6 edits failed. The writer retries 4 times with no backoff. With 4 writers at once, all succeeded. |
| info | B-i2 | A no-op 10M edit (`replace_values` that matches 0 cells) runs the whole job (≈ 95 s: verify, compile, write, carry) before it dedupes to "same bytes, nothing new". |

**No critical findings.** No major finding in the queue, Postgres or S3 put paths.

**Confidence: 0.8.**
- Every claim below was reproduced on a running stack.
- B-M1 was reproduced on both stores.
- The timing orders of the lease test were forced with SIGSTOP/SIGCONT. Two of the three orders are covered. Attempt 1 committing strictly before attempt 2 was not forced; by code, that case dedupes by `job_id`.

### B-M1: a poisoned join frame verifies, and a run trains on it (major)

**Why it happens:**
- `joins.frame()` computes `frame_sha256` from the spec sha and the **committed** `table_sha256` of each input. It then materialises from whatever bytes the store serves, without re-hashing the inputs first.
- After that the frame is reused by identity (`cached: true`).
- Frame verify only checks two things: the cache blob equals the recorded sha, and each input's bytes re-hash to its pin. It never checks that the cache blob was derived from those inputs.

**Repro on the local store** (SQLite API 8750; `ROOT=ds_4410cf0d`, `V5=ds_608cb780`, chain data):

1. Flip `defaulted` (1 − y) in the `applications` part `blobs/01/0148260b….parquet`, and keep a copy of the original.
2. `POST /datasets/$ROOT/joins` → `jn_…` (n:1 bureau, prefix `lt__`).
3. `POST /datasets/$ROOT/versions/$V5/frames {join_id}` → **201**.
4. Restore the original blob (its sha256 is `0148260bd04774e8…` again).
5. `GET /datasets/$ROOT/frames/$FID?verify=1` → `ok: True`.
6. `GET /datasets/$V5/verify` → `ok: True`.
7. `POST /runs {dataset_id: V5, join_id, label: defaulted}` → `run_be2f61a1` **succeeded**.
8. `GET /admin/lake/runs/run_be2f61a1/verify` → `ok: True`.
9. Mean of `defaulted`:
   - in the frame cache `2497f3dd…`: **0.844**;
   - in the table: 0.156.
10. `POST /runs/run_be2f61a1/register` → 409 `parity_mismatch` "cells differ: defaulted (3,010 rows)" (`X3/B/register_poisoned.json`).

**Repro on S3** (API 8752): the same, with the bytes replaced in the **read cache** and its `.meta` rewritten (`tamper_cache.py`):
- the frame `jf_6cb3770b9d` is built from the tampered cache;
- `POST /admin/lake/cache/verify` drops the bad cache file;
- frame verify then answers ok, and `POST …/frames` returns the same frame with `cached: true`;
- the join run `run_e06feb25` succeeds with `data_sha256` = the poisoned cache blob, and its re-verify answers ok.

While the tamper is live, some gates do catch it:
- a table run → 409 `data_integrity`;
- a join run → 409;
- frame verify → `inputs.applications ok: false`.

**Impact:**
- §7 risk 2 / risk 11: a run's evidence ("re-verify ok") is false. The model is trained on other data than the pinned inputs.
- P15's registration parity is the only safety net.
- A run that is never registered, such as a compare, a champion analysis or Autopilot, is never caught.

**Fix direction:**
- Re-hash each input table, or read it through `verified_read`, inside `frames.materialise` before the frame is recorded.
- Or record the input bytes actually read, and refuse when they differ from the pins.
- Optionally, make frame verify re-materialise a sample of the frame, as parity does.

### B-m1: a missing blob answers 500 (minor)

Repro on API 8752: `s3_mutate.py delete dd81a51d…` (the `bureau` part of v5) removes every version, with the governance bypass, and the cache file. Then:

| Call | Answer |
|---|---|
| `GET …/grid?table=bureau` | 500 |
| `GET …/rows/count` | 500 |
| `GET …/stats` | 500 |
| `GET …/manifest` | 500 |
| `POST …/frames` (join on bureau) | 500 |
| delta upload `/check` on bureau | 500 |
| `GET …/verify` | 200, `MISMATCH`, `bureau: missing` (correct) |
| `POST /runs` | 400, "A file … is missing from the lake." (a 400, though nothing is wrong with the request) |

The log shows `app.lake.s3store.BlobMissing` ×4, `botocore NoSuchKey`, and a DuckDB `IOException: No files found` on the cache path. Map `BlobMissing` to a typed 409 (`blob_missing`), as `CacheIntegrityError` already is.

### B-m2: an existing key with other bytes blocks future commits (minor)

Repro:
1. `s3_mutate.py stack cb902c62…`: a newer object version of the `payments` key with other bytes (`blob_integrity` 409 on reads: correct).
2. Upload the same `payments.csv` again (6 commits at once, `conc_put.py`). All 6 answer **500**. The log shows `StoreError: blobs/cb/cb902c62….parquet exists with other bytes (length 40740, sha None)`.

The writer holds the right bytes at that moment. It could re-put them as a new current version (the original stays under Object Lock), or answer a typed 409. As it stands, every upload of that content fails until an admin intervenes.

### B-m3: the cache sidecar can be rewritten (minor)

Repro: `tamper_cache.py <cache> 0148260b… x3lake` replaces the cached part and rewrites `<sha>.parquet.meta` with the new stat. Then:
- `GET /datasets/ds_9200ac89/grid?table=applications` serves the flipped `defaulted`;
- `GET /datasets/ds_9200ac89/verify` → `ok: true`. It hashes the bucket, not the cache that reads are served from;
- `cache.stats.tamper_refetch` stays 0;
- `POST /admin/lake/cache/verify` does catch it (`dropped: [0148…]`).

The run gate re-hashes, and refuses with 409 `data_integrity`.

The threat needs write access to the API host's cache dir, so this is minor. The `.meta` file is as writable as the data. P10b's claim that only a raw-device edit gets past `stat` should be corrected, or the sidecar should be keyed (an HMAC with a process secret). This is how B-M1's S3 variant was triggered.

### B-m4, B-m5: wrong status codes (minor)

**B-m4:** a tampered `applications` part with 3,000 rows instead of 3,010. `POST /runs {table: applications}` → 500. The log shows `ValueError: operands could not be broadcast together with shapes (3000,) (3010,)` at `engine/api.py:694 _min_rows_check`.

**B-m5:** `conc.txt`. Under 16 concurrent writers, `POST /datasets/{v}/edit {add_rows}` → `400 {"detail": "Other versions of this dataset kept being committed; try again."}`. The 409 from `writer.commit` (`commit_contended`) is flattened to a 400 string by the edit route.

## What held

**Postgres = SQLite** (`chain_sqlite.json` vs `chain_pg.json`):

Every normalised field is equal:
- the check;
- the 5 versions' `data_sha256`, `table_sha256`, keys and relations;
- the delta counts (`applications`: rewrite, +30 / ~50 / −20; `bureau`: rewrite, 40 updated);
- the join's `spec_sha256` and coverage;
- the frame's `frame_sha256` and inputs;
- the run's `data_sha256`, `split_sha256` and rows used (2,408);
- frame verify, run verify and dataset verify.

**S3 (MinIO) = local** (`chain_s3.json`): the same whole chain, every field equal to local.

Stacked overwrite:
- with the cache empty, a read answers 409 `blob_integrity` (not cached);
- verify answers `MISMATCH`.

Concurrent identical puts:
- 6 identical 3-file commits at one barrier → 6 × 201, all the same `data_sha256` and table shas, all verify ok;
- `put_existing` was counted, with no errors.

**10M edit job, kill -9** (Postgres, one worker; `qkill.txt`):
- Killed at writing 0.36: the janitor re-queued it after the lease, attempt 2 re-ran it and it succeeded. Versions 3 → 4: exactly one with this `job_id`, one `dataset_version_created` audit row, no duplicate `version_no`.
- Killed at finishing 0.9, after the commit (`qkill2.txt`): attempt 2 finished in 0.8 s. Versions 6 → 7: one version, one audit row.

**Attempt-keyed lease** (two workers; `qstop.txt`, `qstop2.txt`, `lease_worker_logs.txt`):
- SIGSTOP the holder at writing 0.31, so its lease expires (30 s); the janitor re-queues it; attempt 2 is leased on the other worker. Then SIGCONT the old worker, either at attempt 2's writing 0.35 or the moment attempt 2 is leased.
- In both orders the old worker logs "is lost: stopping it" and then "attempt 1: finished after losing its lease; the result is not recorded".
- The job is recorded once (attempt 2). Versions +1, one audit row, no duplicate `version_no`.
- Note: "stopping it" does not interrupt the old attempt's DuckDB write. It runs to its end (≈ 40 s of wasted I/O) and is then discarded.

**Two APIs + two workers, one Postgres and one lake** (`conc.txt`). One barrier fired 16 calls through both APIs:
- 6 deltas, 6 `add_rows` edits;
- 2 key PUTs, 2 relation PUTs;
- one upload committed through both APIs at once.

The results:
- no duplicate `version_no`;
- every version's `__row_id`s unique;
- **0** `__row_id`s given to two different new rows across sibling versions (the root counter holds);
- the double commit gave one version (201 + 200, the same id);
- the duplicate key and relation PUTs dedupe (201 + 200).

Contended uploads are retryable, and 4 writers at once all succeed (`conc_retry.txt`).

## Environment notes

- The harness reset `/tmp/claude-0` to mode 700 mid-run. Postgres, running as the postgres user, PANICked on `pg_control` ("Permission denied"). I moved the cluster to `/var/tmp/x3b-pg`. Nothing in the product is involved.
- The job left behind by that crash (`jb_2cb91f81a749`) was re-queued with `attempts 0` ("worker … stopped") and later succeeded on a new worker.
