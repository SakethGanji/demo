# F-X5b: X5 K3, J1, J2, J5 and the minors

**Lane G.** Fixes for `docs/progress/X5.md`: K3 (critical), J1, J2, J5 (major), the §2 minors and the sweep's 5xx
families. Base `75fc854`. Scripts and raw output are in `docs/progress/F-X5b/` (`out/` holds every output quoted below).

**Stack.** Venv from `backend/requirements.lock`. Postgres 16.15 in a scratch `initdb` cluster (`127.0.0.1:55432`).
Every API under test ran from a copy of `backend/app` taken at its start (`F-X5b/api.sh`), so no edit or pull reached a
running API.

## Verdict

| Item | Result |
|---|---|
| **K3** NUL in a name field breaks history on Postgres | **fixed**: refused at the edge (400), the audit writer can no longer store one, the reader tolerates an existing one. A DB already poisoned is repaired by restarting on this code (no rewrite): history 200, chain verifies |
| **J5** NaN / 1e400 / 1,000-deep / non-UTF-8 JSON → 500 | **fixed** centrally: 400 `bad_number` / `too_deep` / `bad_json` |
| **J1** edit drops a key / relation silently | **fixed**: edit preview lists `key_invalidated` / `relation_dropped` with `needs_ack`; commit needs `acknowledge` by code (400 `acknowledge_required`), as on the upload path |
| **J2** sync-s3 without a bucket stops the API | **fixed**: 400 `s3_not_configured`; no route can stop the server with SystemExit |
| Minors | m-a, X3 m10/m11/m17, X4 m3/m4, X2 m4, X1 m2, m-j, N2, the sweep families B1–B10 → fixed (details below) |
| Sweep | X5's sweep, SQLite × Postgres × audit × enforce: full passes 1 / 2 / 5 / 13 × 5xx (two causes, fixed after), the re-run of those routes **0 × 5xx** on all four (§6). X5's Postgres audit pass had 6,163 |
| Grep gates | gate 2: 0 lines; gate 1: 13 → 7 lines on my side; after merging F-X5a 8, all in F-X5a's files (§7) |
| No regression | P13 delta chain identical to P13's recording on SQLite and Postgres; U2 walk 18/18 in audit and enforce, 0 × 5xx (§8) |

## 1. K3: NUL in Postgres audit rows

**Repro first** (`out/k3_before_pg.txt`, fresh Postgres API, audit mode):

```
200 | history credit-default
400 | POST /projects {name:p, by:NUL}           ("'by' needs at least one letter or digit")
500 | POST /projects {name:NUL, by:Ada}
500 | GET /runs?status=%00
500 | history credit-default      ← every dataset, also after a restart
500 | history loan-losses
500 | history account-attrition
```

The refused request's would-deny audit row stored `"\u0000"` in `details`. Postgres stores that text fine but cannot
cast it to `jsonb`, and the history query reads details through `json_extract` (→ `t::jsonb`).

**Fix, three layers:**

1. **At the edge** (`app/input_guard.py`, new; outermost ASGI middleware):
   - path: NUL / control characters → 400 `bad_id`;
   - query: NUL / control characters / lone surrogates in any name or value → 400 `bad_text`;
   - JSON bodies: NUL / lone surrogate in any string or key → 400 `bad_text`;
   - name-like fields (`by`, `name`, `*_by`, `*_id`, `table`, `kind`, `action`, …) refuse every control character
     (a tab or newline is fine in a note or a reason);
   - form-urlencoded bodies are checked like a query.
   - Multipart bodies are not buffered (uploads stream). A NUL in a multipart field that reaches Postgres is caught
     by the database-error handler: psycopg's `DataError` (db_pg wraps it as a sqlite3 error) → 400 `bad_text`.
2. **At the writer** (`catalog/schema.audit`): before the row is hashed, every text column and every string (and key)
   in `details` / `decision` is sanitised: NUL → U+2400 `␀`, a lone surrogate → U+FFFD. The hash covers what is stored,
   so the chain stays valid. A fast path only re-dumps `details` when its JSON text shows `\u0000` or a surrogate
   escape (an emoji's surrogate pair survives the cleaning unchanged).
3. **At the reader** (`catalog/pg_schema.FUNCTIONS`): new `ens_jsonb(text)` (plpgsql): `t::jsonb`, and when that
   fails, the same text with `\u0000` → `␀` and surrogate escapes → `�` (escaped backslashes respected), and
   NULL if it still fails. `json_extract` uses it whenever the text holds a `\u` escape (else the plain cast, as
   before); `json_valid` uses it too.

**Repair of a poisoned database.** `audit_log` is append-only (triggers; the app role has SELECT/INSERT only on
Postgres), so the bad row is not rewritten: its stored text and `row_hash` stay exactly as written. The new
`FUNCTIONS` change the schema hash, so the next start re-runs `pg_schema.migrate`, which replaces `json_extract` /
`json_valid` with the tolerant versions. Reads then see `␀` in place of the NUL. **Restart on this code = repaired.**

Shown on the DB poisoned above (`out/k3_after_restart_poisoned_pg.txt`, `out/k3_repair_verify_pg.txt`), same data dir
and database, restarted on F-X5b:

```
row 46 still holds the \u0000 escape: t
200 | history credit-default / loan-losses / account-attrition
GET /teams/tm_default/audit/verify → ok: true, rows 46, anchored audit_heads 46, checkpoint 46
GET /teams/tm_default/audit → row 46 read back, details.reason "… a name field says \u0000 …"
400 | POST /projects {by:NUL} / {name:NUL}, GET /runs?status=%00, GET /datasets?project_id=%00 (bad_text)
```

**Writer, past the guard** (`k3_writer.py`, `out/k3_writer_pg.txt`): `schema.audit` called directly with NUL in
actor, comment, a details value and a details key, plus a lone surrogate:

```
row 46 actor 'Ada␀' comment 'c␀'
details as stored {"…", "name": "a␀b", "k␀": ["�", "😀"]}
has NUL / NUL escape: False
GET /datasets/credit-default/history → 200;  /teams/tm_default/audit/verify → 200 ok: true
```

Fresh Postgres API (`out/k3_after_pg.txt`): history 200 before and after the NUL requests, which are 400.

## 2. J5: NaN, 1e400, deep JSON

`input_guard.check_json` parses each JSON body once with `parse_constant` (NaN / Infinity → 400 `bad_number`) and
`parse_float` (a float overflowing to ±inf, e.g. `1e400` → 400 `bad_number`), then walks it iteratively: depth
> 64 (`ENSEMBLE_JSON_MAX_DEPTH`) → 400 `too_deep`; Python's own RecursionError → `too_deep`; not UTF-8 / not JSON with a
JSON content type → 400 `bad_json`. Bodies without a content type or `text/plain` are only checked when they parse as
JSON and are ≤ 8 MB (a raw upload is never buffered); `POST /api/uploads` streams and is skipped.

Exception handlers registered once (`input_guard.install`): `RequestValidationError` → 422 with `loc` / `msg` / `type`
only (FastAPI's echo of `input` / `ctx` was itself the 500 for NaN and bytes); `OverflowError` (a 2⁶⁴ int into
SQLite) → 400 `value_out_of_range`; `RecursionError` → 400 `too_deep`; database value errors → 400 `bad_text`;
`FrameError` → 404 `input_not_found` / 409 `blob_missing` / 410 `purged` | `version_deleted`; `PageError` → its own
4xx; `DatasetConflict` → 409; an uncaught `DatasetError` → 400.

`out/j2j5_before_sqlite.txt` → `out/j2j5_after_{sqlite,pg}.txt`:

```
before: 500 {"time_limit":1e400} | 400 {"dataset_id": NaN} | 500 {"time_limit": Infinity} | 500 1000-deep
after:  400 bad_number           | 400 bad_number         | 400 bad_number              | 400 too_deep
```

## 3. J1: the edit path acknowledges key / relation loss

`datasets.edit_version` now runs F-X3a's `upload._integrity_warnings` on the edited table as the edit leaves it (the
compiled DuckDB view; the other tables from the parent's parts):

- **preview**: `checks.warnings` gains `key_invalidated` / `relation_dropped` (`needs_ack: true`, `why`,
  counts, `source: "edit"`), plus `needs_acknowledge: [codes]`; `would_be_default` is then false.
- **commit**: `upload._need_integrity_acks` → 400 `{code: acknowledge_required, acknowledge: [codes], warnings}`;
  with the codes acknowledged, the writer gets `relations_acknowledged`, `_guard_keys` re-checks after the write, the
  warnings are recorded with `acknowledged: true`, and the manifest op says `relation_dropped … acknowledged: true`.
- the same code runs in the queued `edit` job (F-JOBS, > 200k rows): its refusal is `result.refused` with the same
  detail.
- key / relation PUTs (`declare_version`): the writer gets `relations_acknowledged = set()`, so a declaration can
  never drop a relation silently (only `drop_relation` names one). Carried tables are byte-identical, so their keys
  cannot become invalid.

**Repro first** (`out/j1_before_sqlite.txt`, X5's `x3/m1_edit.py` on unmodified code): preview warnings `[]`,
commit 201, key `cust_id` valid False, relations `[]`, manifest op without `acknowledged`.

**After** (`j1_edit.py`, `out/j1_after_sqlite.txt`; Postgres identical, `out/j1_after_pg.txt`):

```
1. preview, add_rows repeating C001 → 200 warnings [key_invalidated not_unique, relation_dropped relation_to_not_unique]
   needs_acknowledge ['key_invalidated', 'relation_dropped'] would_be_default False
2. commit, no acknowledge → 400 acknowledge_required ['key_invalidated', 'relation_dropped']
3. commit, acknowledge key_invalidated only → 400 acknowledge_required ['relation_dropped']
4. commit, both acknowledged → 201; key invalid; relations []; manifest op relation_dropped acknowledged: True
5. control: add_rows with a new cust_id, no acknowledge → 201, relation kept
6/7. drop the key column → preview key_invalidated + relation_dropped (column_gone); commit without → 400
8. control: from-side edit adds an orphan → 201, relation kept with n_orphans 1
9/10. PUT key on a repeating column → 400 key_invalid; PUT a second key → 201, relation kept
```

**New contract for U4 (UI):** `POST …/edit` preview may now carry `checks.warnings[]` with codes `key_invalidated` /
`relation_dropped` (`needs_ack`, `why`, `table`, `columns` / `relation`, counts, `message`) and top-level
`needs_acknowledge: [codes]`. The commit then needs `acknowledge: [codes]` (by code, the same list may also hold
`target_one_class`), else 400 `{detail: {code: "acknowledge_required", acknowledge: [...], warnings: [...]}}`. Same
shape as StagedUpload's integrity acknowledgement (F-X3a), so the same component can render it.

## 4. J2: sync-s3 without a bucket

`lake/sync_s3.s3_destination` raised `SystemExit` inside the async route, which stopped uvicorn. It now raises
`LakeError(400, "s3_not_configured")`; the CLI `main()` turns that into SystemExit itself. The route answers 400.

Belt and braces: `InputGuard` catches `SystemExit` from any route (argparse, a CLI helper) and answers 500
`internal_exit` while the server keeps serving (`out/systemexit_guard.txt`: a route calling
`argparse.parse_args(["--nope"])` and one raising SystemExit → 500 each, then `GET /ok` 200).

`grep -rn "sys.exit\|SystemExit\|parse_args" backend/app`: every hit is in a CLI `main()` / `__main__` (reset.py,
lake/seed.py, lake/sync_s3.py `main`, lake/migrate_pg.py), none imported by a route; migrate_pg's SystemExits are
in `copy()` called only from its `main()`.

```
before: 500 POST /admin/lake/sync-s3?dry_run=1 … 000 GET /health after
after:  400 {"code":"s3_not_configured",…} … 200 GET /health after   (SQLite and Postgres)
```

## 5. Minors

| Item | Fix | Check |
|---|---|---|
| m-a non-text `by` → 500 (shares, certify, contracts, admin lake) | `_who` gets the raw value (refuses non-text); `lake/api._admin` refuses non-text; the guard refuses a non-text top-level `by` / `actor` / `created_by` / `requested_by` / `decided_by` (400 `bad_by`) | repro.sh "K known" → 400 |
| X3 m10 `_min_rows_check` broadcast 500 | 409 `split_mismatch` when the split's length ≠ the table's | by reading (needs a table rewritten outside the app) |
| X3 m11 concurrent edits → 400 "try again" | a non-400 `LakeError` from the edit keeps its status and code | `out/m11_m17_*.txt`: 16 concurrent → 14 × 201 + 2 × 409 `commit_contended` (SQLite), 9 + 7 (PG); no 400 |
| X3 m17 edit / export ignore X-Ensemble-Actor | `_body_by` on the edit; export `by or current_actor()` | header only → 201 / 200 |
| X4 m3 discard upload under a hold | `holds.active(upload_keys)` → 409 `legal_hold` | `out/x4m3_sqlite.txt`: 409, released → 200 |
| X4 m4 explain / SHAP / export after purge / delete | purged blob → 410 `purged`; FrameError of a gone version → 410; a missing file of a deleted version → 410 `version_deleted` | `out/x4m4_sqlite*/X5_m4.txt` (X5's m4.py after F-X4c's purge): explain 500 → 410, export 409 → 410 |
| X2 m4 absolute paths in run configs | `RunStore._row` re-bases `run_dir` / `splits_path` / `csv_path` / … that are outside the current data dir and missing, when the same tail exists under it (read time; nothing rewritten). Only reads through `RunStore` are covered; code that parses `runs.config` itself is not | a direct call: `/old/place/data/runs/run_a` → `<data dir>/runs/run_a`; not re-run with X2's moved-data-dir harness |
| X1 m2 409 detail string | `integrity_gate` 409 → `{code: data_integrity, message}` | by reading |
| m-j `DatasetConflict` → 500 | handler → 409 `conflict` | |
| N2 multi-host SQL URL → 500 | `parse_url` wraps ValueError → `SqlError` (400) | |
| B1 export of an unknown id → 500 | 404 first | repro.sh R4 → 404 |
| B1b emoji in the card name → export 500 | `Content-Disposition` with an ASCII fallback + RFC 5987 `filename*` | repro.sh R4 → 200 |
| B2 `?table=<unknown>` | `PageError` handler | R5 → 404 |
| B3 non-text split target | 400 `bad_field` | R6 |
| B4 seed `project_id` 2⁶⁴, B6 2⁶⁴ ints | seed fields typed; OverflowError → 400 | R8, R13, R16 |
| B5 10 KB path ids / body ids | path segment > 512 → 400 `bad_id`; body `*_id` > 512 → 400 | R11 |
| B7 non-text action / kind / table | guard → 400 `bad_field` | R12 |
| B8 multipart parse errors | `_MultipartSink.feed` → 400 `bad_multipart` | R9 |
| B9 `Infinity` in a body | J5 | |
| B10 broken files on score routes | 422 without echo / FrameError handlers | R10 |
| events `?from=2⁶⁴` | `le=2**62` → 422 | |
| (new, sweep) `GET /retention/plan` 500 when a review is queued: `vela.review._tasks` is gone since P22 | treat as running (pinned) | sweep |
| R1 / R2 races (decisions.json, concurrent discard) | write by rename; ignore the tmp race | `out/r1_r2_sqlite.txt` |
| (new, sweep) seed `project_id: ""` → FK 500 | 400 `project_not_found` | sweep re-run |

X5's own repro scripts (`sweep/repro.sh`, `repro2.sh`, 66 calls) re-run on fresh SQLite and Postgres APIs with X5's
fixture: **0 × 5xx** on both (`out/repro_after_v1.txt`, `out/repro2_after_v1.txt` SQLite;
`out/repro_after_pgv.txt`, `out/repro2_after_pgv.txt` Postgres).

## 6. Sweep

X5's garbage sweep (`X5/sweep/fixture.py` + `sweep.py`, unchanged; 317 routes, 101,738 requests per pass) on fresh
APIs with the F-X5b code (`F-X5b/sweep.sh`), four passes in parallel. Raw output: `F-X5b/out/sweep/`.

| Pass | X5 (before) | F-X5b full pass | then | re-run of those routes on the final code |
|---|---|---|---|---|
| SQLite audit | 2,930 × 5xx in a 46,583-request part (+157 destructive) | **1 × 5xx**, 1 × "hang" (>60 s) | seed `project_id: ""` → FK 500 (fixed) | **0 × 5xx** |
| SQLite enforce | 2,937 × 5xx (46,583 requests) | **2 × 5xx** | decision-time race (R1, fixed), seed | **0 × 5xx** |
| Postgres audit | 6,163 × 5xx | **5 × 5xx** | decision-time race, seed | **0 × 5xx**, 1 × "hang" |
| Postgres enforce | not run by X5 | **13 × 5xx** | decision-time race (12), seed | **0 × 5xx** |

All 317 routes hit in every full pass; 0 leaks (no engine text in a 4xx body). The full passes ran on code taken at
their start, before two fixes found by them:

- **R1** (X5's own finding, hit here by the sweep's 4 threads): `POST …/decision-time` read a half-written
  `decisions.json` → now written by rename (`data_doctor_api._write_json`). `r1_r2.py`: 64 concurrent POSTs → 64 × 200.
- **seed** `project_id: ""` / an unknown project → 400 `project_not_found`.
- (also **R2**: concurrent `DELETE /uploads/{u}` raced tmp_dir's mkdir against rmtree → ignored; 32 concurrent
  DELETEs → 32 × 200, `out/r1_r2_sqlite.txt`.)

The re-run (`ONLY='decision-time|/admin/lake/seed|/mlflow|/uploads/{up}$'`, 1,927 requests × 4 passes) gives
**0 × 5xx on all four**.

**The ">60 s" hang** is `GET /registry/versions/{vid}/mlflow` (`variant: valid`), once on SQLite audit and once on
Postgres audit, each time among the first requests after the fixture. The route reads MLflow in the heavy pool,
which is then still busy with the fixture's jobs while four APIs, four sweeps and model fits share 4 vCPUs. On a quiet
API after the same fixture it answers in 1.7 s, then 0.01 s. Not a 5xx; the heavy pool
(timeouts, a busy pool) is F-X5a's lane (K2 / J4).

**The ~50 Postgres-only NUL routes** (X5 P2): `GET /runs?status=%00` etc. are now 400 `bad_text` on every route,
both engines, both modes (the sweep sends `\x00` in every query parameter and body field of every route).


## 7. Grep gates

Gate 2 prints nothing (the `pg_schema.py` docstring no longer names the CSV-era table). Gate 1 went from 13 to 7 lines:

- `catalog/artifacts.py` (2), `catalog/purge.py`, `catalog/contracts.py` → `lake.duck.parquet_rel(uris)`, the one
  place that writes DuckDB's Parquet reader into SQL (lake/ is outside the gate).
- `engine/api.py` scorecard.csv → `frames.run_artifact_csv` (a run's own artifact, not a version).
- `sqlsource/core.py` comment reworded.

**After merging F-X5a (`8e53b26`)** the gate prints 8: F-X5a's K1 fix added `engine/serving.py:181`
(`pq.read_table` of a lake part, column-pruned, for the holdout rows), and moved the build.py lines (now 1075,
1136–1143) and scoring.py (1112–1113). serving.py:181 does read version data outside `frames.py` / `lake/`, by part
path; it should go through `frames` (or a `lake/` helper) to keep the one-reader rule. Reported to the orchestrator.

**Left, allowed:** `registry/scoring.py:1100–1101` (the batch scratch file written by the scoring itself),
`registry/build.py:1021` (`_frame_v1`: a join frame's cache file) and `1082–1089` (the serving-parity comparison of two
frame files the build just wrote). None reads a dataset version; all are F-X5a's files (registry/build, serving) and
that lane is rewriting them for K1, so they were not touched here. A one-line follow-up for F-X5a: call
`lake.duck.parquet_rel(path)` there and the gate prints nothing.

## 8. No regression

- **P13 delta chain** (`P13/chain.sh` + `make_data.py`, run against the F-X5b API): output equal to P13's recording
  once ids, hashes and timings are normalised — **SQLite and Postgres identical** (`out/p13_chain_{sqlite,postgres}.txt`).
- **U2 walk** (X5's `ui/scripts/u2_walk.cjs` on the merged head with U4's UI, API :8660, Vite :5190, 1366 × 900):
  **audit 18/18, enforce 18/18**; console errors only the expected refusals (audit: 400 `one_to_many`, 409
  `legal_hold`; enforce: + the 403 of step 11b); **0 × 5xx, 0 tracebacks** in both API logs. The calls the UI made are
  in `out/u2walk_{audit,enforce}_calls.txt`. One harness change (`F-X5b/u2_walk.cjs`): U4 turned "Ask to delete" into a
  ConfirmButton, so step 13b also clicks `data-delete-confirm` (without it the first audit run stopped at 13b with no
  request sent).
- **X5 repro scripts** (66 calls) and X4 m4 (X5's `x4/m4.py` after F-X4c's purge): above.


## 9. Files touched

`app/input_guard.py` (new), `app/main.py` (install), `app/catalog/schema.py` (audit sanitiser),
`app/catalog/pg_schema.py` (`ens_jsonb`, `json_extract`, `json_valid`), `app/datasets.py` (edit integrity),
`app/lake/writer.py` (declare_version: no silent relation drop), `app/lake/api.py` (`_admin`, sync-s3, seed, 410s),
`app/lake/sync_s3.py`, `app/lake/duck.py` (`parquet_rel`), `app/frames.py` (`run_artifact_csv`),
`app/catalog/{datasets_api,shares,certify,contracts,dsets,artifacts,purge,retention}.py`, `app/data_doctor_api.py` (R1), `app/lake/upload.py` (R2), `app/engine/api.py`
(m10, events bound, scorecard read: 3 small edits in a shared file), `app/engine/store.py` (X2 m4),
`app/sqlsource/core.py` (N2).

## 10. Notes for other lanes

- **All lanes:** any request input with NUL / control characters / NaN / ±inf / > 64 levels is now refused before
  the route (400 `{detail: {code, message}}`); routes need no own check. Name-like body keys refuse every control
  character, including tab and newline.
- **All lanes:** audit rows are sanitised by `schema.audit`; never write `audit_log` another way.
- **F-X5a:** the 7 gate-1 lines above.
- **U4:** the edit acknowledgement contract in §3.
