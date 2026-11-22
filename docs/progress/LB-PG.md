# LB-PG: labeling on Postgres

**Setup:**
- Postgres 16.15, in a cluster made by the new `scripts/dev_pg.sh`. Roles `ens_owner` (owns the db, runs the migrations) and
  `ens_app` (the app, with the §2.2 grants), so `ENSEMBLE_DB_URL` + `ENSEMBLE_DB_OWNER_URL` + `ENSEMBLE_DB_APP_ROLE` are all set.
- Python 3.12 venv from `requirements.lock`. LB-X's environment: fake LLM, `CALIB_MIN` 100 / 20.
- Every pass uses a fresh database and data dir.

**Raw outputs:** `LB-PG/*.txt`. **Scripts:**
- `LB-X/chain.py` and `LB-FIX/chain_bool.py`, unchanged;
- `LB-PG/enforce.py` (new): the enforce-only checks;
- `LB-PG/triggers.py`: direct SQL as the app role and as the owner.

## Pass / fail

| Check | SQLite POC | PG POC | PG enforce |
|---|---|---|---|
| LB-X chain (`chain.py`) | 67/72 | 67/72 | 49/68 (see below) |
| Status totals on v3 | ok 12, absorbed 48, orphaned 3, disputed 13 | identical | identical |
| Yes/no variant (`chain_bool.py`, M1) | (LB-FIX) | 66/73; absorbed 51, base_changed 0 | n/a |
| Label DDL: 17 tables, run pins | ✓ | ✓ | ✓ |
| Triggers `le_ro_u`, `le_ro_d`, `ri_label_ro_u` exist and fire | ✓ | ✓ | ✓ (`triggers-pg.txt`) |
| Hash-only redaction allowed; any other UPDATE refused | ✓ | ✓ | ✓ |
| Carry-over: orphaned / stale / absorbed | ✓ | ✓ | ✓ |
| Snapshot + verify; pinned run; `label-runs/verify` incl. `labels_fitted` | ✓ | ✓ | ✓ |
| Materialise | ✓ | ✓ | ✓ |
| Proposal by bob, approved by rev | ✓ | ✓ | ✓ |
| LLM from a SQL query (preview, save, calibrate, budget, run) on fake | ✓ | ✓ | refused 409 `fake_refused`: by design in enforce |
| Purge: events redacted; run and snapshot verify → `purged` | ✓ | ✓ | ✓ (owner olu ≠ requester) |
| Audit verify | ok | ok | ok (tm_default and the risk team) |
| 46 bad inputs: 5xx | 0 | 0 | 0 |
| Tracebacks in the API log | 0 | 0 | 0 |
| Four eyes, guest and outsider (`enforce.py`) | n/a | n/a | **54/54** |

**The remaining FAILs are LB-FIX's documented chain artefacts, the same on both engines:**
- row 8 ×2;
- "absorbed = rows_changed";
- "new version has the proposed values" (`__row_id` vs `_row_id`);
- "after a failed approve…": the approve succeeds.

In the bool run there are also two more:
- `accept` → 409 `not_calibrated`, from the fake's run-to-run accuracy, as in LB-X;
- the holdout check compares `False` with `"no"`.

**Enforce run of `chain.py`:** bob and rev were made maintainers of tm_default first. Its extra FAILs are expected:
- Its status calls in steps 1–2 send no actor header, so enforce refuses them. `enforce.py` checks status as bob: ok = 40.
- The fake LLM is refused, so there are no LLM labels in the snapshot.
- Its purge is decided by a maintainer (bob), so it gets 403. `purge_decide` needs an owner who is not the requester.
- Its self-approve gives 409 `four_eyes`, which is correct.

**Covered by `enforce.py`, all PASS:**
- **Outsider eve** (not in the team) gets 403 on: layer, status, events GET/POST, snapshot, tasks, dataset rows, the dataset's
  layers, run verify, materialise. With no actor header: 401/403.
- **Guest vik** (task member only, `guest: true`) gets `next` and `answers`. He gets 403 on: layer, status, events, dataset rows,
  quality, `next` on another task, snapshot.
- **Proposals:**
  - bob deciding his own proposal → 409 `four_eyes`;
  - wen (member) → 403;
  - rev (maintainer) → applied, with a new version.
- **Purge:**
  - the requester (ana) → 409 `four_eyes`;
  - maintainer bob → 403;
  - owner olu → executed.
- **Snapshot approval:** the policy has `label.snapshot_approve` (maintainer, ≠ creator), but no route uses it yet. LB-A builds
  snapshots without an approval step, and `label.snapshot` = member. So nothing to test here; noted for LB-A.

## What broke on Postgres, and the fixes

1. **A fresh Postgres DB with an app role would not start.**
   - The cause: `backfill_p01` runs at startup as the app role. It does `UPDATE audit_log` (0 rows) and creates the audit
     triggers, so it fails with `permission denied for table audit_log`.
   - P23 only ran the app role on a DB that was copied over.
   - Fix: a new `Database.admin_connect()`. It is the owner connection on Postgres when `ENSEMBLE_DB_OWNER_URL` is set,
     otherwise `connect()`. `main.catalog_backfill` runs the three startup backfills through it.
2. **Every LB-B route returned 500 (54×) with `permission denied for schema public`.**
   - The cause: LB-B's tables (tasks, members, assignments, gold, proposals, prompts, LLM runs and cache) and
     `label_calibrations` were created lazily, on first use, by `lbb_schema.ensure` and `llm_label._ensure`. The app role
     has no CREATE, and a late table would have had no grants anyway.
   - Fix: `labels.schema.pg_ddl()` now emits that DDL and the `EXTRA_COLUMNS` (`ADD COLUMN IF NOT EXISTS`). So
     `pg_schema.migrate` makes them as the owner and grants them. Both lazy `ensure`s skip on Postgres. SQLite is unchanged.

Nothing else needed changing:
- the trigger twins, the grants (`label_events`: UPDATE on the redaction columns only) and the dialect layer worked as they were;
- the SQL in the label code ran as written.

## Run it yourself

`make dev-pg` (with `ENSEMBLE_POC_MODE=1` for the POC) runs `scripts/dev_pg.sh dev`. It:
1. runs `initdb` once into `~/.ensemble/pg` (`backend/data/pg` as root);
2. starts Postgres on 127.0.0.1:55432;
3. creates the db `ensemble` and the roles. The password is generated into `<dir>/password` (0600) and synced to the roles on
   every start;
4. exports the three env vars;
5. runs `make dev`.

`make dev-pg-stop` stops Postgres. `eval "$(scripts/dev_pg.sh env)"` sets the env vars in another shell.

Tested from a fresh directory: the API is up, the 15 samples are seeded into Postgres, there are 17 label tables, and no
SQLite file is created. Stop, then stop again, prints "not running".

**Not run:** the full pytest tier. Two tests fail on the untouched tree as well:
`test_concurrency::test_api_answers_while_shap_runs` and `test_runs::test_missing_labels_are_dropped`.
