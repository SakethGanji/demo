# Data platform build tracker

**Plan:** docs/plans/data-platform-final.md. §9.1 is the phase list; §11 holds the user decisions.

**Orchestrator:** session_018upNdRigdGd9A2nGzFSQXq.

## Rules for every phase session

- Implement exactly the phase's row in §9.1 (goal, files, verification chain, done-when), plus the §11 decisions.
- **Verify** with manual API calls chained the way the UI calls them. No smoke or integration suites. Add `npm run build` when the UI changes.
- Write **docs/progress/<phase>.md** with the calls and their outputs.
- **Git:** run `git pull` (merge, never rebase or force) before every push. Push only to motion-components. No PRs. Keep commits small.
- **Commit message trailer:**

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018upNdRigdGd9A2nGzFSQXq
  ```

- **Parallel work:** other lanes may run at the same time. Stay inside your phase's "Files touched". If you must touch a file another lane owns, keep the change minimal and say so in the report.
- **Long jobs:** stay in-turn and poll them. Don't end the turn waiting on a scheduled wakeup. Delete scratch predictors and seeds you made.
- **Report:** send_message to the orchestrator with the head commit, what was done, the verification results, any deviations, and anything that blocks dependants.

## Status

| Phase | Lane | Status | Session | Head |
|---|---|---|---|---|
| P01 | D | done | session_018upNdRLvgWS6ojVV61QiZu | 76b3dc6 |
| P03 | R | done | session_018upNdMfWmu4csFiRDB9cYA | 884e586 |
| P04 | R | done (grep gate 0) | session_018upNdQ4YR8uygzkhyFodYa | ee570d0 |
| P02 | D | done | session_018upNdMxLoGmBn2rfMZrStw | ab61873 |
| P05 | D | done | session_018upNdR5aiQ2E9Luej6fmJ6 | 112b4d0 |
| P06 | D | done | session_018upNdPmVFDA7q7c2wULNRM | eb59e9c |
| P07 | D | done (U2: send __row_id in set_cells/delete_rows) | session_018upNdQzFyc3sTFn8F8WBVu | 858200d |
| P08 | D | done | session_018upNdRRN6S5Y3rTnnk29BM | 656ab64 |
| P09 | D | done (split_files read-only for P10) | session_018upNdSNHpt3EuRkTqNbK1w | 7da74bb |
| X1 | ⟂ | done: 2 critical, 6 major → F-X1 | session_018upNdQEzTw3dUV29ygL2YH | 6822049 |
| F-X1 | D | done: C1, C2, M1, M2, M4, M5, M6 fixed; M3 re-verified (P10 unblocked) | session_018upNdRigdGd9A2nGzFSQXq (F-X1) | see report |
| P10 | D | done (25 CSV-era versions: 23 migrated + 2 tampered reported, then migrated once resolved; old results byte-equal) | session_018upNdQ3GmVAB5qkPvPTGac | 2f50d87 |
| P11 | D2 | done (AG peak ≈ 2.13 GB + 2.43×frame) | session_018upNdQkfQxUCUaQxHcgsFb | 5deecb5 |
| P19 | G | done (mode audit) | session_018upNdPhxRCU1Z9oW9qWLpN | f9cbfe7 |
| P20 | G | done | session_018upNdN6AqeeL2rkTsjNjmp | f314861 |
| P22 | O | done | session_018upNdPj3uWNkfz67KZVANF | b22d64d |
| U1 | U | done | session_018upNdQpQQ3Pdy7XWP9nE4H | a9eed11 |
| P18 | G | done (note: U1 must send team_id on New project) | session_018upNdRkZyzaZ19U6LU2cj7 | cbaffa2 |
| P10b | S | done (S3BlobStore + verified LRU cache + httpfs; chain equal on s3/local; Object Lock tested on MinIO built from source) | session_018upNdRLNhdMckTGcyQS5SA | see report |
| P23 | O | done (both engines pass the chain; migrate_pg copy verified; SKIP LOCKED; TEXT timestamps/JSON, global advisory lock) | session_018upNdQtu1bjzLTGxWxh2gv | 6a94fcc |
| F-JOBS | O | done: edit / Doctor apply / Feature Lab 10M writes are jobs (202 + progress, idempotent by job_id, ≤ 2.2 GB); profile, labels, annotations, shift, Autopilot on the queue; P22 attempt-scoped lease fix; missing.py fixed | session_018upNdMmZAHQ2H5ofowVFXx | see report |
| F-X2 | D | done: C1 (per-version migration lock + commit re-check + run pins/re-verify), M1–M6, m1–m3 fixed and shown against base repros; replay identical, GC 0 candidates | session_018upNdNvnaG9wjQHpViW7q9 | see report |
| P13 | D | done (delta action per table by key + _op; check counts = diff counts; insert-only → append part; holdout ids kept; dup keys / no key 400; identical on SQLite and Postgres) | session_018upNdMmQyXrKkqxx22sMTS | see report |
| P14 | D2 | done (joins validated with coverage, 1:n/non-key/missing column 400; frames = grain rows, cached blob; runs on a join: cache sha as read_sha256, join_input rows, paired with table run, re-verify ok; delete of a joined input blocked via joined_from/merged_from; identical on SQLite and Postgres) | session_018upNdRR2Vg6fMS5HYdUAeG | see report |
| P15 | D | started (registry/serving parity/batches for join models) | session_018upNdQvtpPYsz4hqNgnPYo | |
| P21 | G | done (contract block → /check blocks, /commit 409; four eyes 409 on cert/share; Carl sees only v1, trains into own project 201, edit would-deny/403; unmasked only via an explicit pii=unmasked share; revoke stops access, run still verifies; every decide path via policy.four_eyes; SQLite = Postgres in audit and enforce) | session_018upNdNeZFRrGECEyFj5531 | see report |
| P12 | D | done (4 tables from book.xlsx + bureau.csv; append = 3 carried same sha + 1 appended; key/relation as new versions; runs on a named table; same output on SQLite and Postgres) | session_018upNdQBD1YfmthVvNNLCxe | see report |
| X2 | ⟂ | done: 1 critical (a run created mid-migration loses its split), 6 major (cleanup gate global, Feature Lab records + tombstone split not kept, pre-DL4 / DB-hash laundering, no-map diff), 4 minor → fixes needed before P12 | session_018upNdNbAs6Mvojf6VJc3XR | see report |

## Open notes carried forward
- **10M edit commit takes 99 s** (P07 path). Full pandas reads remain in data-doctor (and its apply), the edit preview and Feature Lab. X1 to judge whether this is a fix before P10.
- **U lane:** switch the UI's GET /datasets to ?limit=50&after= (the full list takes 1.15 s at 20k versions). Send __row_id in set_cells/delete_rows.
- **P23:** carry ix_datasets_list into the Postgres DDL.
- **P20:** mask diff samples.
- **Still on in-process threads (not on the queue):** stats profile, annotations, shift, Data Doctor label job, Autopilot. Move them in P23 or a follow-up.
- **U lane:** send project_id in the /uploads query (it authorises from the query). Grey out "New team" via /authz/check unless self_service_teams.
- **P23:** keep audit_log.details/decision as TEXT (not jsonb), copy prev_hash/row_hash/audit_head verbatim, and use FOR UPDATE on the head row.
- **X4 (done in P21):** every four-eyes decide path goes through `policy.four_eyes` → `decide_check` (decider name and request principal).
- **PII not masked yet (P20 left these out):** anomaly top rows, shift rows, the active-learning queue/annotations, Feature Lab previews, split-options/intake examples, q search text in dataset_read audit rows. Pick them up in a follow-up before X4.
- **P21 (done):** the share pii='unmasked' case of data.read_unmasked / data.export_unmasked.
- **P23:** copy the catalog_meta pii_salt:* rows verbatim.
- **U2:** masked indicators + "Unmask (reason)" action (greyed out via /authz/check), unmasked_refused, a PII tags panel, tables[].pii in the upload check, pii_features in the Doctor.
- **F-X1 → P11 scale gate:** 10M Data Doctor apply (SQL) takes 28–103 s and the Feature Lab SQL write about 50 s, at ≤ 2.1 GB. Run them as jobs with progress, alongside the 10M edit commit.
- **P10 → P24:** verify-all passes are recorded in `catalog_meta verify_all:passes`; arm GC from them. **P10 → P23:** carry `lake_migrations`, `ds_hash_ro` (its WHEN clause) and `split_files` (only while CSV-era versions remain).
- **P10 → U lane:** a migrated table in data.csv order still pages by `__row_id` (rows shown in id order); `file_row_number` paging (R5) not built.
- **All lanes:** a `duck.job()` connection (preserve_insertion_order = false) does not keep `ORDER BY` order into `.to_arrow_table()` / `.fetchnumpy()`. Sort in Python when order matters.
- **New config (F-X1):** ENSEMBLE_MAX_COLUMNS (2,000), ENSEMBLE_DOCTOR_ROWS (200,000: above it the Doctor and Feature Lab use a recorded splitmix64 sample and SQL writes), ENSEMBLE_DOCTOR_WRITE_MB (1,024).
- **Long 10M writes should become jobs with progress:** edit commit (≈38 s), Doctor apply (28–103 s), Feature Lab SQL write (≈50 s). Do this in P23 or a follow-up before X5's scale gates.
- **duck.job() connections don't preserve ORDER BY into arrow/numpy:** sort explicitly wherever row order matters.
- **Data Doctor label-quality thread:** still in-process (≤200k rows). It belongs on the queue.
- **vela/missing.py clean-labels:** shape mismatch (622,2) vs (622,1) on a group-split gapped run. It is caught and recorded in doctor_gaps.errors. Fix it in a follow-up.
- **P23 → all lanes:** SQL is still written once in the SQLite dialect; on Postgres (`ENSEMBLE_DB_URL`) `db_pg.translate` rewrites it. Write portable SQL anyway:
  - `CAST(json_extract(…) AS INTEGER/REAL)` wherever a JSON value meets a number (Postgres's json_extract returns text);
  - no `rowid`, no `json_set`, no output alias inside an ORDER BY expression;
  - no catch-and-continue after a failed statement inside one `with db.connect()` (it aborts a Postgres transaction).
- **P23 → follow-up:** the long 10M writes as jobs with progress (edit commit, Doctor apply, Feature Lab SQL write) were not built in P23. With grants on (`ENSEMBLE_DB_APP_ROLE`), split restore and admin reset need the owner role (they DROP TRIGGER).
- **P10b → P24:** delete bytes only through `lake.store.delete_blob(db, store, sha, by, reason, bypass_governance=True)`: it removes every S3 version and the cache file, and audits `blob_deleted` / `blob_delete_refused`. Under Object Lock (GOVERNANCE, `ENSEMBLE_S3_LOCK_DAYS` = 30, needs model-risk approval) GC also needs the bypass role. Run `S3BlobStore.abort_stale_uploads()` with GC (MinIO refuses the abort-multipart lifecycle rule).
- **P10b → all lanes:** never build a path from a key. Use `store.local_copy(key)` for a local file (on S3, the sha-verified cache) and `store.uri(key)` for DuckDB. Don't memoise a cache path without re-checking it (`pages._ensure_cached`).
- **P10b → deployment:** `ENSEMBLE_BLOB_STORE=s3://bucket/prefix`, `ENSEMBLE_S3_ENDPOINT`; credentials only from the AWS chain. Bucket policy: the app role gets `PutObject` only with `If-None-Match`, and nobody else writes to `blobs/` (Object Lock keeps the original but a newer version can be stacked on it; verify catches that). Upstream MinIO OSS is archived (dl.min.io answers 410), so pick a maintained S3 store. Bundle httpfs (`ENSEMBLE_DUCK_EXTENSION_DIR`) if `ENSEMBLE_S3_READ=httpfs`.
- **P12 → F-JOBS:** `POST …/edit` is now `edit` (a `{table}` wrapper) → `_edit`. When the edit commit becomes a job, put `table` in the payload and wrap the job body in `frames.use_table(table)`: the context variable reaches `run_in_threadpool`, not a queued job.
- **P12 → U2:** table picker on runs (`POST /runs {table, label}`), diff (`?table=`) and edit (`{table}`); the multi-table check (`tables[].action`, `sheets`, `carried`, `dropped`, `keys`, `relations`, `suggested_relations`), the plan PATCH (`tables`, `relations`, `primary_table`), `commit {match: {<table>: …}}`; `GET /datasets/{root}/versions/{v}`, `PUT …/tables/{t}/key`, `GET`/`PUT …/relations`.
- **P12 → all lanes:** a key or a relation declared on a version makes a NEW version (all tables carried, zero bytes). The writer now carries the parent's relations (recounted), re-checks keys on an append, and keeps the parent's table order, for every op.
- **P12 → P13:** `_commit_multi`'s append branch (cast to the parent's types, ids above the table's max, `w.append`) is the insert-only delta path.
- **F-X2 → all lanes:** a CSV-era split draw or run creation is refused with 409 `version_migrating` while its version is migrated (`lake/migrate.csv_write_hold`; catalog_meta `lake_migrate:lock:<id>` / `lake_migrate:hold:<id>:<token>`). Any new CSV-era writer must hold it. An edit of a CSV-era version with no row map answers 409 until it is migrated.
- **F-X2 → P24:** `verify_all:passes` stays a list of end times; the start times are in `verify_all:passes_started`. Cleanup counts, per folder, the passes that started after that folder's commit. Verify-all does not re-hash `legacy_json` blobs (cleanup now does): consider it for GC arming. **P23:** carry `verify_all:passes_started`.
- **F-X2 (pre-existing):** two versions created in the same second get their `v` numbers in a different order on the head than on acfe988 (`POST …/verify` `v` 4↔5); `datasets.version_no` is unchanged.
- **P13 → U2:** the `delta` action (plan `tables.<t>: {action: 'delta', key?, op_column?, ops?, allow_new_columns?}`; a single file takes the plan's `table`), `tables[].delta` in the check (counts, `mode`, `key_from`), `deltas` in the commit answer, 400s `delta_key_required` / `delta_duplicate_keys` (`keys`) / `delta_op_unknown`, the `looks_like_delta` warning on a plain upload.
- **P13 → all lanes:** a table can hold parts that differ only in Parquet nullability (an append part next to its base). Concatenate parts with `pa.concat_tables(…, promote_options="default")` (frames.read and lake/frame do now); DuckDB `read_parquet([...])` is unaffected.
- **P13 → T2 (triggered, not built):** delta parts as `table_files.role ∈ upsert/delete` with merge-on-read, and the two-file upserts + deletes form.
- **P14 → P15:** a join run has `input_ref {kind: join_frame}`, `input_table.join_frame_id`, `cfg.join` (join id, spec sha, `frame_sha256`, `cache_blob`, pinned inputs) and `join_input` rows; `frames.input_sha256` returns the cache file's sha. Serving parity: rebuild with `frames.materialise(spec, inputs, out)` (the one join path) and compare by `__row_id`; batch scoring builds `frames.join_sql` over the batch files.
- **P14 → P21:** a join's pinned input from another dataset is only checked to exist and be live; apply `visible_teams` / shares in `joins.inputs_for` for another team's input.
- **P14 → P24:** `join_cache` blobs are pinned by `blob_refs (join_frame, cache)` and `(run, join_frame)`; a frame without runs is re-creatable (GC candidate).
- **P14 → U2:** join builder on `POST /datasets/{root}/joins` (400 codes listed in P14.md), `POST …/versions/{v}/frames`, run form `join_id` (label from the grain), usage `via_join`, lineage `join_frame` nodes. `where` is refused (400) until frames get their own split.
- **P21 → U2:** contract editor + freeze, `check.contract` / `tables[].contract`, certification request/decide (grey out with `/authz/check data.certify_decide_certified|_model`), share request/decide (`grant_unmasked` for pii unmasked)/revoke, `access: shared` on list rows, `project_required` when training a shared version (send `project_id`).
- **P21 → P24:** `shares.runs_on(share)` = the grantee's runs on a shared object (purge impact report); `shares.live_certification()` for `keep_certified`.
- **P21 → follow-up:** not built: forks of a shared dataset, discoverable "request access" rows in search, contracts on the edit path, `project_id` on the other kinds' `/{kind}/runs`, the model card rendering `result.input_certifications`, a certification revoke route.
- **P21 → enforce semantics:** role is checked before four eyes (C §1.5), so a maintainer deciding their own certification gets 403 in enforce and 409 in audit.
- **F-JOBS → all lanes:** above `ENSEMBLE_JOB_ROWS` (200,000) `POST …/edit` and `…/data-doctor/apply` answer 202 `{job_id, poll}`; poll `GET /api/jobs/{id}` (`progress {pct, phase}`, `result` = the old body, `result.refused` = the old 4xx). UI helper: `src/shared/api/jobs.ts` `settle()`. Leases are now (worker, attempt): pass `attempt` to `Q.heartbeat/finish/requeue/set_progress` if you call them directly.
- **F-JOBS → X5/ops:** `autopilot.audit.audit` peaks at 5.6 GB on 12k rows in the API's heavy-pool child (two OOM kills under a 14.3 GB cgroup); an API with a busy heavy pool ignores SIGTERM. Both pre-existing.
