# LB-X: independent end-to-end check of labeling (read-only)

**Scope:** a black-box API chain over LB-A and LB-B on head `1ffaf13` (motion-components). No product code was changed.
**Setup:** a fresh uvicorn on SQLite with a scratch `ENSEMBLE_DATA_DIR` and `ENSEMBLE_POC_MODE=1`. The env also had
`ENSEMBLE_LABEL_LLM_DESTINATIONS=llm,fake`, `ENSEMBLE_LABEL_CALIB_MIN=100` and `ENSEMBLE_LABEL_CALIB_MIN_CLASS=20`.
Only the `fake` destination was used. The deps were installed on Python 3.12: `requirements.txt` pins numpy 2.5.5, which
needs Python ≥ 3.12, and scipy has no wheel for 3.13.
**Data:** a generated 3-table dataset (`applications` 400 rows with target `default`, `payments` 1 200 rows, `bureau`
400 rows) with n:1 and 1:1 relations on `app_id`.
**Chain:** `docs/progress/LB-X/chain.py`, written from scratch (the LB-A and LB-B scripts were not used).
**Outputs:**
- `LB-X/run-final.txt`: the chain with string classes `bad` / `good`.
- `LB-X/run-boolean-target.txt`: an earlier run whose `yes` / `no` target was inferred as BOOLEAN (finding M1).

## Pass / fail

| # | Step | Result | Notes |
|---|---|---|---|
| 1 | 3-table upload (stage → plan relations → check `multi` → commit) | PASS | |
| 1 | layer on `applications.default`; 50 human labels; status v1 ok = 50 | PASS | |
| 2 | edit v2 (3 rows deleted, `income` / `purpose` / `age` cells set) | PASS | orphaned 3 (rows 10, 12, 14); stale 2 (rows 2 and 4, context columns); row 6 (`age`, not in context) stays ok |
| 2 | re-label stale row 2 on v2 → ok | PASS | |
| 2 | base target changed → `base_changed` | PASS (boolean run) | In the final run my row-8 edit was a no-op (a test-design error, so its two FAIL lines there don't count). The boolean run shows row 8 → `base_changed`. |
| 3 | task: 2 annotators, reviewer, 10 gold qa, random + gold fills | PASS | |
| 3 | vik fails gold 0/10 → auto-paused; `next` → 409 `annotator_paused` | PASS | |
| 3 | reviewer sees 17 disputed, adjudicates, disputed → 0; annotator `quality` → 403 | PASS | |
| 4 | SQL preview: join of applications + aggregated payments + bureau, `__row_id` from applications | PASS | 20 rows, 7 columns |
| 4 | `llm/preview` with `call_llm` on fake | PASS | |
| 4 | save prompt version (v1, `query_sha256`) | PASS | |
| 4 | calibrate (200 calibration rows) | PASS | The fake's accuracy varies between runs: 0.56 (failed) in one run, 0.82 with τ* = 0.8 certified in the other. |
| 4 | budget: tiny `max_usd` → 409 `over_budget`; run of 100 rows at `max_usd` 3 → spent $0.078 | PASS | |
| 4 | accept | PASS / see m1 | It returns 409 `not_calibrated` when calibration failed (correct); 82 accepted when it passed. |
| 4 | LLM events carry `prompt_id`, `prompt_version`, `query_sha256` | PASS | |
| 4 | bad SQL → 400 `sql_error` with DuckDB's message | PASS | |
| 4 | `read_csv('/etc/passwd')`, `FROM '/etc/passwd'`, `read_parquet` → 400 `sql_function`, both at preview and at save | PASS | |
| 5 | snapshot with `exclude_simulated: false`; snapshot verify ok | PASS | 64 applied, 13 disputed, 100 `prelabel_only` (m1) |
| 5 | run pinned to the snapshot succeeds | PASS | |
| 5 | `/label-runs/{id}/verify` ok, with `labels_fitted.ok` | PASS | 47 rows applied |
| 5 | overlay on training rows only; holdout unchanged | PASS | 13 holdout rows carry a snapshot label different from base; every one keeps the base `y_true` in `holdout-predictions` |
| 5 | register → 202; the version is `ready` and its hashes carry the labels fields | PASS | |
| 6 | materialise → v3 (31 changed, 14 holdout cells skipped, 16 already absorbed) | PASS | |
| 6 | status on v3 shows absorbed | PASS (string classes) / **FAIL (boolean target, M1)** | Final run: absorbed 48 = 31 written + 16 already equal + 1. My "absorbed == rows_changed" assertion was wrong. |
| 7 | correction proposal by bob | PASS | |
| 7 | approval by rev → new version | **FAIL (C1)** | 500 |
| 7 | the failed approval leaves no partial state | **FAIL (C1)** | The proposal reads `approved` with no version. |
| 8 | purge of the labelled column: 234 events redacted, 3 snapshots and 1 overlay blob purged, 305 LLM cache rows deleted, 0 events keep a value | PASS | |
| 8 | run verify → `purged`; snapshot verify → `purged` | PASS | |
| 9 | 46 bad inputs across the label routes: no 5xx | **FAIL (M2)** | 9 × 500 |

## Findings

### Critical

**C1. Approving a correction proposal returns 500, and the proposal is left `approved` with no new version.**
- Where: `labels/workflows.py:179` calls `L.materialise(...)`, which raises `TypeError: 'module' object is not callable`.
- Cause: `app/labels/__init__.py` defines a function `materialise`. Any `from .materialise import …` (for example the
  `POST /label-snapshots/{id}/materialise` route) binds the submodule `app.labels.materialise` over that package
  attribute.
- Effect: once a snapshot has been materialised in the process, every proposal approval fails. This is order-dependent,
  which may be why LB-B's chain 5 passed. The proposal row is updated to `approved` before the crash, so it cannot be
  retried.
- Repro:
  1. Materialise any snapshot: `POST /api/label-snapshots/{sn}/materialise {"version_id": v, "reason": "x"}`.
  2. `POST /api/label-layers/{L}/proposals {"version_id": v, "reason": "fix", "cells": [{"row_id": 51, "column": "default", "value": "bad"}]}` → 201.
  3. As another actor, `POST /api/label-proposals/{id}/decide {"approve": true}` → **500**.
  4. `GET /api/label-layers/{L}/proposals` shows the proposal as `approved`.
- Also: after this 500 (and after every 500 below), the server drops the keep-alive connection. The client's next
  request on that connection fails with `ReadError: connection reset`.

### Major

**M1. Boolean-inferred target: the layer accepts string classes, and after materialise the status reports
`base_changed` instead of `absorbed`.**
- Upload inferred a CSV `default` column of `yes` / `no` as BOOLEAN (`true` / `false`). The layer was created with
  `classes: ["yes", "no"]` with no type check or warning.
- Materialise wrote `"no"` into the BOOLEAN column, which became `false`.
- Status on v3 then compares the string label with the boolean base: 0 absorbed and 13 `base_changed`, with
  `carry_reason: base_value_changed`.
- Repro:
  1. Upload `applications.csv` with `default` ∈ {yes, no}.
  2. Create a layer with `classes: ["yes", "no"]`.
  3. Label rows with the opposite value.
  4. Snapshot, then materialise.
  5. `GET …/status?version_id=<v3>` → `absorbed: 0`, `base_changed: 13`. See `LB-X/run-boolean-target.txt`.
- Two possible fixes: refuse or class-map a class schema on a BOOLEAN column, or compare values after casting.

**M2. Nine bad inputs give 500 instead of 400.** All are `ValueError` / `TypeError` from an unchecked `int()` or a
shape assumption:

| Request | Traceback |
|---|---|
| `POST /label-layers/{L}/snapshots {"policy": "x"}` | `resolve.py:29 normalise_policy`: dictionary update sequence |
| `POST /label-layers/{L}/tasks {"columns": ["default"], "votes_per_row": "x"}` | `tasks.py:67` |
| `POST /label-tasks/{T}/fill {"strategy": "random", "n": "lots", "version_id": v}` | `tasks.py:205` |
| `POST /label-tasks/{T}/fill {"strategy": "manual", "row_ids": ["a"], "version_id": v}` | `tasks.py:227` |
| `POST /label-tasks/{T}/gold {"pool": "qa", "items": [{"row_id": "x", "value": "bad"}]}` | `tasks.py:172` |
| `POST /label-layers/{L}/sql/preview {"version_id": v, "query": "...", "limit": "abc"}` | `api_llm.py:129` |
| `POST /label-prompts/{P}/runs {"version_id": v, "max_usd": 1, "k": "z"}` | `llm_label.py:632` |
| `POST /label-layers/{L}/proposals {… "cells": [{"row_id": "a", "column": "default"}]}` | `workflows.py:134` |
| `POST /label-layers/{L}/proposals {… "cells": "x"}` | `workflows.py:134`: string indices |

The two proposal cases INSERT the `label_proposals` row before the crash, so a pending proposal with garbage cells is
left behind.

The other 37 bad inputs gave 4xx: unknown table, class, column or ids; > 500 events; ATTACH; multi-statement; non-SELECT;
`{nope}` template; destination `anthropic`; batch mode; unknown selector; unknown run, task or proposal.

### Minor

**m1. `accept` changes nothing on the server; it only returns a `snapshot_policy`.**
- A snapshot built with `{exclude_simulated: false}` alone still shows all 100 LLM labels as `prelabel_only`, even
  after an accept of 82.
- The caller must pass the returned `accept.llm.min_confidence` policy to the snapshot. That works as documented in
  LB-B, but the name "accept" suggests a state change.
- Not re-verified end to end: the root had already been purged when I tried.

**m2. LLM events' `provenance._ctx` lists the context columns of `applications`, not the query's columns.**
- In this run `_ctx` was `[age, app_id, income, purpose, region]`, while the query sent income, age, purpose, n_pay,
  late_share and score.
- So the LLM label goes `stale` when `region` changes, although `region` was never sent. It does not go stale when
  `payments` or `bureau` change, although their aggregates were sent.

**m3. `absorbed` takes precedence over `stale`.**
- A label equal to the base value reports `absorbed` even when its context columns were edited. This came up in a run
  where the labels matched the base.
- Defensible, but `ok` versus `absorbed` hides staleness for those rows.

**m4. The fake destination's calibration accuracy varies between runs on the same seeded data** (0.56 versus 0.82), so
whether `accept` passes is not reproducible.

**m5. In POC mode, self-approval of a proposal is allowed (four-eyes is off by design).** Separation of duties was not
tested in enforce mode.

## Not covered
- Postgres and enforce mode.
- The real `llm` destination.
- Guests, rules, the F14 migration and `send-to-task`.
