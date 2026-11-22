# X5b: short re-check of X5 K1–K3, J1–J6 (read-only)

**Lane ⟂.** Scope: X5's critical and major findings, after F-X5a and F-X5b (`docs/progress/X5.md`, `F-X5a.md`, `F-X5b.md`).

**What was tested.**
- **Main code: `motion-components` @ `ced8fe7`** (F-X5a + F-X5b merged). It ran from a detached git worktree, so no pull reached a running API.
- **U5 landed while this ran (`274a07d`).** It touches `registry/build.py`, `engine/serving.py`, `registry/scoring.py`, `frames.py` and `catalog/dsets.py`. So after the main pass, on a second worktree at `274a07d`, I re-ran:
  - the U2 walk;
  - a 10M join register + 20k batch;
  - K3, J1, J2 and J5 on Postgres.

**Stack.**
- Venv from `backend/requirements.lock` (Python 3.12); no pin changed.
- Postgres 16.15 in a scratch `initdb` cluster (`127.0.0.1:55433`).
- Chromium/Playwright as pre-installed; Node 22.
- Box: 4 vCPU, 15 GB.
- The 10M seed is `F-X5a/scripts/gen_seed.py` (X5's 13-column shape, 0.83 GB CSV).

**Scripts.** X5's own repro scripts wherever they exist, with only ports, paths and the venv changed:
- `X5/scale/scale.py`, `lib.py`, `doctor10m.py`, `preview.py`, `audit_alone.py`;
- `X5/x3/m1_edit.py`, `X5/dla4.py`, `X5/sweep/*`;
- F-X5a's `sigterm.py` and F-X5b's `k3.sh`, `j2j5.sh`, `j1_edit.py`, plus the U5 copy of the U2 walk.

Mine are in `X5b/scripts/`. Raw output is in `X5b/out/`.

**10M processes.** As in F-X5a: the API runs with `ENSEMBLE_WORKERD=external`, beside an external `workerd --slots train=1,duck=2,light=2`. Peaks come from `lib.Peak` over **both process trees** (`X5_PIDS` = API + workerd), sampled every 0.25 s:
- "single" is the largest single process;
- "tree" is the sum over both trees.

The pool-kill test was also run on an **embedded** API (the default configuration).

**Contention.** The two garbage sweeps ran from 08:22 to 09:08, beside the 10M trainings, the registers and the Doctor. Times in that window are contended. Memory peaks are per process and not affected.

## Verdict: **done**

- All three X5 criticals (K1, K2, K3) and all six majors (J1–J6) pass, on the paths X5 used to find them.
- Both sweeps answer 0 × 5xx.
- The regression spot checks pass: DLA4 on SQLite, and the U2 walk on the U5 head.
- No new finding is critical or major enough to block. The most notable one, N1 (POST /runs at 10M holds about 3 GB in the API request), was already listed by F-X5a as open and out of its lane. It is a scale-headroom item, not a failure.

**Confidence.**
- **Core flow: 0.88.** The X5 gap is closed: at 10M, upload → join frame → sampled train → register (parity ok) → Doctor → batch completes with no process above ≈ 3.1 GB, and a killed job no longer takes the pool down. ≤ 1M stays at X5's ≈ 0.9. It is not higher because:
  - the API request peaks at 3.0–3.7 GB when training at 10M (N1);
  - the Doctor peak is twice F-X5a's figure (N2);
  - S3 and 50M rows were not covered.
- **Governance: 0.90** (X5: 0.80). The NUL break is refused at the edge, a poisoned DB is repaired by a restart, and the audit chain verifies. J1 closes the silent key/relation loss on the edit path. Postgres enforce is swept with 0 × 5xx. What keeps it below 0.95: multipart text fields are not inspected by the guard (N4); X5's minor N1 agent-principal item was not re-checked here.

---

## 1. Pass/fail

| Item | Result | Evidence |
|---|---|---|
| **K1** 10M single-table run → register | **pass.** Upload 45.3 s, single 2.08 GB. Train (300 MB budget): rows_used 576,695 of 10M, `sample_sha256` recorded, `over_budget` true. Register → **`ready`** in 500 s (contended).<br>• The `holdout` step located all 2,000,000 holdout rows by `__row_id` and held 100,009 in memory; every row was scored in batches.<br>• The version's `holdout_predictions.csv` (2,000,001 lines) is **byte-equal** to the run's.<br>• Serving parity (P15) is join-only by design, so the byte-equal holdout predictions are the parity check for this model. | `out/k1_chain_10m.txt`, `k1_build_steps_single.txt`, `k1_holdout_pred_eq.txt` |
| **K1** 10M join run → register, parity | **pass.** Multi-file upload (10M apps + 10k branches) 43.9 s / 2.11 GB; frame build 12.1 s / 2.61 GB, coverage 1.0. Train: rows_used 498,639. Register → **`ready`**, parity **`ok`**:<br>• 10,000,000 = 10,000,000 rows, 0 unmatched ids, 15 columns, types equal, 0 mismatched cells, byte-equal frame;<br>• predictions on the rebuilt frame: 2,000,000 rows in batches of 500k, max \|Δ\| 8.3e-17, ids and labels equal. | `out/k1_build_steps_join.txt` |
| K1 peak RSS over both trees | **≤ 3.72 GB per process; no OOM** (X5: killed at 12.6 / 13.5 GB). Single / tree:<br>• upload 2.08 / 2.82; multi 2.11 / 2.85; frame 2.61 / 3.35;<br>• train 3.46 / 4.45 and trainjoin 3.72 / 6.06: in both, the peak is the API process (N1);<br>• register single 3.09 / 6.88 and join 3.02 / 6.38, overlapping the automatic Doctors.<br>Clean join register on `274a07d`, nothing else running: **2.58 / 3.72 GB**. | `out/k1_chain_10m.txt`, `k1_u5head_register_batch_10m.txt` |
| **K1** 20k-row batch | **pass.** Single-table model: succeeded, 20,000 rows, 215 s, batch process 1.41 GB. Join model (apps.csv + branches.csv): succeeded, 90 s, 1.55 GB. On `274a07d`: 58 s, 1.52 GB | `out/k1_batch20k.txt` |
| **K2** Model Doctor at 10M, automatic after the fit | **pass.** `warm_doctor` finished by itself after both fits:<br>• `doctor.computed_at` 08:32:47 and 08:37:51;<br>• 0 errors in the API log;<br>• no process killed. | `GET /runs/{id}` → `doctor` |
| **K2** Model Doctor at 10M, GET | **pass.** A third 10M fit, then `GET …/doctor` at once (it waits on the warm-up's lock): **200 in 384 s, peak single 2.96 GB** (a pool child), tree 5.75 GB. 12 checks answered. Repeat: 0.3 s from the cache (X5: OOM at 7.4–10.5 GB, never answered) | `out/k2_doctor_10m.txt` |
| **K2** kill a heavy-pool child mid-job, 10M workerd | **pass:**<br>• the build was SIGKILLed during `measure` → `failed`: "The process running this build stopped: pool process 22566 was killed by SIGKILL (…OOM killer…)", step `measure` failed;<br>• the 20k batch running beside it **succeeded**;<br>• next build → **`ready`**, next batch → **succeeded**, with no restart. | `out/k2_kill_workerd_10m.txt` |
| K2 same, embedded API (default) | **pass:** the killed build → `failed` with the reason; the batch beside it succeeded; next build ready, next batch succeeded; health 200 | `out/k2_kill_embedded_sqlite.txt` |
| K2 kill the interactive pool child under a waiting GET | **pass (no hang):**<br>• the waiting `feature-importance` got an answer after 3.0 s (X5: hung for ever), but it is a plain 500 (N3);<br>• the next interactive call 200, next build ready, next batch succeeded. | `out/k2_kill_interactive_sqlite.txt` |
| **K3** NUL repro, fresh Postgres | **pass:** `{by: NUL}`, `{name: NUL}`, `?status=%00`, `?project_id=%00` → **400** `bad_text`. History **200 for all 15 datasets** before, after, and after a restart. Audit verify ok (46 rows, anchored). Extra vectors (multipart NUL, form-urlencoded, text/plain JSON, lone surrogate, path NUL) → 400; 0 NUL rows stored | `out/k3_fresh_pg.txt`, `k3_nul_paths_pg.txt` |
| **K3** repair-by-restart, poisoned with the pre-fix code | **pass.** Old code `75fc854` in its own worktree, fresh Postgres DB:<br>• the repro → 400 / 500 / 500, then history **500 for all 15 datasets**, still 500 after restarting the old code; row 46 holds `\u0000`.<br>Same data dir and DB, restarted on `ced8fe7`:<br>• row 46 still holds the escape (not rewritten);<br>• history **200 × 15**; **audit verify `ok: true`** (47 rows, checkpoint 46);<br>• the repro again → all 400, history 200 × 15, verify ok. | `out/k3_poisoned_repair_pg.txt` |
| **J1** edit add_rows invalidating a key | **pass, SQLite and Postgres:**<br>• X5's `m1_edit.py`: preview lists `key_invalidated`; commit without acknowledge → **400** `acknowledge_required` (X5: 201, silent drop);<br>• F-X5b's full script: preview `needs_acknowledge [key_invalidated, relation_dropped]`; no ack → 400; partial ack → 400; **both → 201**; manifest `relation_dropped acknowledged: true`; the controls keep the relation. | `out/j1_x5script_*.txt`, `j1_full_*.txt` |
| **J2** sync-s3, no bucket | **pass, both engines:** `?dry_run=1`, no flag and `dry_run=0` → **400** `s3_not_configured`. `/health` and `/datasets` 200 afterwards (X5: 500, then the API stopped serving) | `out/j2j5_*.txt`, `j2_extra.txt` |
| J5 (with J2's script) | **pass:** `1e400`, `NaN`, `Infinity`, 1,000-deep → 400 on both engines | `out/j2j5_*.txt` |
| **J3** Autopilot on 12k rows | **pass:**<br>• full Autopilot (auto, layer1) on credit-default: profile → baseline → audit → apply → retrain → final, **finished in 167 s**. Largest process 0.89 GB (VmHWM ≤ 0.92 GB, the fit worker); tree sum 5.50 GB, mostly AutoGluon's fit processes;<br>• X5's `audit_alone.py`: **0.60 GB**, 440 items, 8.2–8.7 s, twice (X5: ≥ 10 GB). | `out/j3_autopilot_full.txt`, `j3_audit_alone.txt` |
| **J4** SIGTERM with the pool busy | **pass:**<br>• 2 importance calls in flight → the API exits **14.0 s** after SIGTERM (both requests finished first);<br>• 8 calls queued, longer than the grace → "grace period over: stopping held jobs and the heavy pool" → **exits 20.3 s** after SIGTERM (default grace 20 s; X5: > 3 min, then kill -9). No orphaned pool process. | `out/j4_sigterm_10m.txt`, `j4_sigterm_10m_long.txt` |
| **J5 + sweep**, Postgres **enforce** | **pass: 101,738 requests, 317/317 routes, 0 × 5xx, 0 hangs, 0 leaks** (F-X5b's full pass: 13 × 5xx). 10 "slow" entries (> 20 s), all `POST …/benchmark` returning 200 | `out/sweep/pg_enforce.*` |
| **J5 + sweep**, SQLite **audit** | **pass: 101,738 requests, 317/317, 0 × 5xx, 0 hangs, 0 leaks.** 9 slow, all `…/benchmark` 200 | `out/sweep/sq_audit.*` |
| **J6** first 10M edit preview | **pass:**<br>• never-edited 10M version: **3.5 s**, API 1.57 GB (its baseline);<br>• repeat 2.8 s;<br>• the join's grain table, first preview: 2.8 s;<br>• counts matched 10,000,000, changed 1. (X5: 50.8 s at 3.17 GB) | `out/j6_preview.txt` |
| Regression: DLA4 chain, SQLite | **pass, same as X5:**<br>• #1 54 calls → 47 × 400 + 7 × 200;<br>• #2 commit without `match` → 400 `match_required`; with `key acct`: matched 3,000, changed 15 (`x1`), added 0, removed 0;<br>• #3 compare "paired on 535 of 600 rows (holdouts differ)";<br>• #4 explain / slices `021•••` text masked; model ready; the export zip has `schema.json` zip VARCHAR. The export now also works with the actor header only (X3 m17 fixed).<br>0 tracebacks. | `out/dla4_sqlite.txt` |
| Regression: U2 walk, audit, on a fresh pull (`274a07d`, with U5) | **pass, 18/18 steps.** Console errors only the intended 400 `one_to_many` (step 3) and 409 `legal_hold` (step 13b); API log 0 × 5xx, 0 tracebacks; 28 screenshots (not committed) | `out/u2/walk_audit.txt`, `calls_audit.txt` |
| U5 head re-check (`274a07d`) | **pass:**<br>• 10M join register: ready, parity ok, predictions max \|Δ\| 8.3e-17, peak 2.58 GB; 20k batch succeeded, 1.52 GB;<br>• Postgres: K3 repro → 400 and history 200 × 15 (× 22 after J1's datasets, with U5's per-table history); verify ok; J2/J5 400; J1 400 → 201. | `out/k1_u5head_register_batch_10m.txt`, `u5head_pg_k3_j1_j2_j5.txt` |

## 2. New findings (ranked; none blocks "done")

**N1 (major, scale headroom; F-X5a's open note, wider than stated). `POST /runs` at 10M holds ≈ 3.0 GB in the API process, on the single-table version too.**
- Repro: `scripts/postruns.py`, a Peak over the API tree around the request only:

  ```
  POST /runs {dataset_id: <10M single-table>, frame_budget_mb: 300} → 201 in 8.8 s, API process 3.04 GB (baseline 1.4 GB)
  POST /runs {dataset_id: <10M apps>, join_id}                      → 201 in 8.3 s, API process 3.03 GB
  ```

- During the chain the API process peaked at 3.46 GB (train) and 3.72 GB (trainjoin), the highest of any 10M step. Its VmHWM stayed 3.77 GB.
- F-X5a recorded this for the join version only. The cause it gave fits: an ID column and the 10M split JSON are read whole in the request (`engine/api.py` 560–700).
- Impact: every other 10M gate keeps a process ≤ 3.1 GB. This one is in the API itself, so concurrent trainings on large tables add ≈ 1.6 GB each to the API.
- Fix: do the run's split / min-rows prep in the train job or by DuckDB, not in the request.

**N2 (minor). The 10M Model Doctor peak is ≈ 2× F-X5a's figure.**
- Here: 2.96 GB in a pool child and 384 s (F-X5a: 1.51 GB, 264 s). The two interactive pool children ended with VmHWM 2.90 and 3.15 GB.
- The time is contended (both sweeps ran); the memory is per process, so contention should not change it.
- It is still far below X5's OOM and the 15 GB box, and no process died. But it sits at the same ≈ 3 GB level as N1, not at 1.5 GB.
- Worth a stage-by-stage memory profile (gap analysis on the 200k sample) before calling 10M routine.

**N3 (minor). A request whose interactive pool process dies answers a bare 500.**
- `GET /runs/{id}/feature-importance?shuffles=50`, then SIGKILL the busy pool child → after 3.0 s: `500 Internal Server Error` (text/plain). The log has `BrokenProcessPool: pool process 14930 was killed by SIGKILL (…OOM killer…)`.
- The hang is fixed, and the next call works. But the caller gets no `{code, message}`: the UI cannot say "ran out of memory, try a smaller sample".
- Fix: map `BrokenProcessPool` to 503 `{code: "worker_died", message}` in the central handlers.
- Repro: `out/k2_kill_interactive_sqlite.txt`.

**N4 (nit). Multipart text fields skip the input guard's control-character rule.**
- `POST /uploads -F name=$'p\x01q'` → 201; the name is stored with the `\x01`.
- The same field in JSON would be refused (name-like keys refuse every control character).
- NUL in multipart is still caught at the database (400 `bad_text`, verified on Postgres), so K3 holds. This is consistency only.
- Repro: `out/k3_nul_paths_pg.txt`.

**Notes (no action):**
- Each sweep pass records one `fit exited with code 1` traceback from a garbage-driven train job. The run is recorded as `failed`; no request 5xx.
- The single-table build has no `parity` block (P15 is join-only), so "parity ok" for it was checked as byte-equal holdout predictions (above).

## 3. Not covered

- S3 at scale.
- 50M rows.
- Postgres at 10M (only K3, J1, J2, J5 and the sweep ran on Postgres).
- The U2 walk in enforce mode (asked: audit).
- X5's minors that F-X5b fixed and that are outside K/J: they were covered only as far as the sweeps and DLA4 reach them.

## 4. Files

- **`X5b/scripts/`:**
  - `api.sh`, `workerd.sh`: start an API / workerd from a frozen worktree;
  - `chain.sh`, `scale.py` (X5's, paths only), `batchpk.py`, `doc3.sh`, `postruns.py`, `sigterm8.py` (F-X5a's with 8 queued calls), `u5.sh`;
  - `kill.py`, `kill_interactive.py`, `autopilot.py`, `hist_all.sh`;
  - `k3.sh`, `j2j5.sh`, `j1_edit.py` (F-X5b's), `sweep.sh`, `dla4_port8701.py`.
- **`X5b/out/`:** raw outputs as cited. The sweep jsonl files hold only the "slow" entries (there were no 5xx).
- **Removed afterwards:** the scratch data dirs, the Postgres cluster, the three worktrees (`ced8fe7`, `75fc854`, `274a07d`) and the Vite server.
