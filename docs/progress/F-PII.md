# F-PII: PII masking on the pages P20 left out, and share checks on cross-team joins (lane G)

Plan: `docs/plans/data-platform-final.md` §6.3 (PII), §6 (shares, C §1.3–1.5). Carried notes closed: **"PII not masked
yet (P20 left these out)"** and **"P14 → P21: a join's pinned input from another dataset is only checked to exist and be
live"**. Base: `motion-components` @ fcf8357, merged with P15 (55c491a) before the final runs.

## What was built

### 1. PII masking on the remaining pages

Every page uses P20's rules unchanged:
- PII-tagged columns are masked by default. A suggested tag masks like a confirmed one.
- `unmasked=1&reason=…` (2–500 characters) is decided as `data.read_unmasked` through `deps._decide`, on the run's
  input version (or on the dataset).
- **Allowed:** the read is audited as `dataset_rows_read {read, reason, columns, action}`.
- **Denied, audit mode:** the page stays masked, with `unmasked_refused` and a would-deny row.
- **Denied, enforce mode:** 403.
- No reason gives 400 `reason_required`.
- Responses carry `masked_columns` / `masks` / `unmasked`.

| Page | Route | What is masked |
|---|---|---|
| **Anomaly top rows** | `GET /anomaly/runs/{id}/top` | The rows' `values`, every reason's `value` and the `why` sentence. A masked feature's `median` is nulled. |
| **Shift check** | `GET /runs/{id}/shift/{key}` | A masked feature's `mean_expected`, `mean_actual`, `std_expected` and `std_actual` are nulled. PSI and importance stay. |
| **Shift rows** | `GET …/shift/{key}/rows` | The page holds no column values (set, origin, row number, p, w). It carries the masking state, and the unmasked decision and audit apply as on the other run pages. |
| **Active-learning queue** | `GET /runs/{id}/active-learning` | The items' `features`. Their joined `text` is masked by replacing the items' stored values, read by `__row_id`. Slice conditions are masked too. |
| **Annotations queue** | `GET /runs/{id}/annotations` | The items' `fields`, and `given` when the label is tagged. |
| **Feature Lab** | `GET /datasets/{id}/feature-lab/{job}` | A bin's `edges` on a masked column, in `kept` and in the journal's transforms (keyed by the transform's `col`). |
| **Split options** | `GET /datasets/{id}/split-options` | A masked date column's `from` / `to`, for example dob `1950` → `1999`. Also in any sentence that quotes them. |
| **Smart Intake** | `GET /datasets/{id}/intake`, and `POST` with `{roles, by, unmasked, reason}` | Values that the evidence and reasons quote of a masked column. The scrub also gets that column's top-20 / min / max / median, including the ISO date form, so it can replace them in text. For example `non-negative, median •••`. |
| **`q` in `dataset_read` audit rows** | `governance.read_audit` (rows page, grid) | The search text is stored as `{hash: sha256(team salt ‖ q)[:12], len, redacted: true}`. This is the `hash` mask's value, so an investigator who knows a value can check it. The text as typed is never stored. |

**Shared helpers (`lake/mask.py`):**
- `run_ref` and `for_run_request` (run pages), `for_version_request` (computed dataset pages).
- `raw_values` (the stored values of the masked columns at given rows, for the free-text net).
- `audit_text`.

**The `Mask.scrub` net got stricter:**
- `STAT_KEYS` (median / mean / std / means) of a masked column are nulled.
- `min`, `max` and `edges` count as value keys.
- `col` names a dict's column (Feature Lab transforms).
- Column → kind / type / role maps (`kinds`, `types`, `column_types`, `roles`) are left alone. Before, a `{ssn: "number"}`
  kinds map would have put "number" into the string-replacement list.
- Number forms `g` and `,.4g` were added.

### 2. Cross-team joins (`catalog/joins.py`)

**The share check.** `inputs_for` (and so `validate`, `save`, `frame` and `POST /runs {join_id}`) takes the joining
team from the grain version. For every pinned input of another team, it needs a live share to that team that covers
the version: `shares.grant_for_version` (scope certified / default / pinned / all, not expired, not revoked).
- With no such share: **403 `input_not_shared`** `{version_id, owner_team, team, inputs}`, in every authz mode. A join
  copies the other team's rows into this team's frame, so audit mode does not let it through.
- The input carries `share {share_id, pii, owner_team, scope}`.
- The frame's pinned inputs record `share_id` / `share_pii`.
- `validate` answers `shared_inputs` (with `pii_columns`).

**PII under a masked share: the refuse option.** A joined table shared with `pii='masked'` may not bring a PII-tagged
column (mask ≠ none) into the frame or be joined on one. That covers:
- `on` keys: a match count is an oracle on the values;
- listed `columns`, or every column when none are listed;
- aggregate columns;
- window dates.

This answers **409 `pii_masked_share`** with the columns and the share. An unmasked share (approved with
`grant_unmasked`) allows them. Frames are never written masked: a frame holds stored values like any version, its
identity stays `hash(spec + input shas)`, and P15's serving parity rebuilds it unchanged.

**Revoke.** A revoke blocks every new frame, including the reuse of a cached frame (`frame()` validates first), and
every new run on the join. Existing frames and runs still verify:
- `GET …/frames/{f}?verify=1` re-hashes the pinned inputs and needs no share;
- so does `GET /admin/lake/runs/{id}/verify`.

**Audit.** Refusals are audited as `join_input_refused {code, version_id (grain), inputs}` in the **joining team's**
chain.

**`shares.runs_on`** (the revoke's `runs_kept`, and P24's purge-impact list) now also lists the grantee's runs on a join
frame that read the shared version (`via_join: true`, from `run_inputs` role `join_input`).

## Verification (manual API calls, chained as the UI makes them)

**Files** in `docs/progress/F-PII/`:
- `chain.py`, the whole chain, and `run.sh`, which starts a fresh API, runs the chain, stops the API, and counts
  tracebacks and HTTP 500s in the API log;
- raw outputs `chain_sqlite_audit.txt`, `chain_sqlite_enforce.txt`, `chain_postgres_audit.txt` and
  `chain_postgres_enforce.txt`.

**Setup:**
- Postgres 16 in a scratch `initdb` cluster, with `ENSEMBLE_DB_URL`.
- Pinned stack (requirements.lock), `ENSEMBLE_ADMINS=Ada`.
- Enforce mode with `ENSEMBLE_AUTHZ_ALLOW_TYPED=1`.

**People:** Credit Risk has Ben and Bo (owners), Ana (maintainer) and Vic (viewer). Collections has Cole (owner).

**Data:** 600 loan applications: `app_id`, `applicant_name`, `ssn` (9-digit BIGINT), `email`, `dob`, `income`, `age`,
`segment`, `y`, and `tier`. `tier` is the upper half of SSNs with 20 % flipped, so that Feature Lab keeps a bin of
`ssn`.

**The leak scan.** `pii N` after each call counts the raw names / SSNs / e-mails / dates of birth in the JSON body.

**All four runs: 0 tracebacks and 0 HTTP 500s. Every masked call has `pii 0`.** Raw values appear only in Ana's five
allowed unmasked reads.

| # | call | result (SQLite audit; enforce differs only where shown) |
|---|---|---|
| 1 | Ana commits loans v1; `GET …/pii` | suggested: applicant_name / email / ssn pii_direct redact, dob pii_quasi year_only |
| 2b | Vic `GET /anomaly/runs/{id}/top?limit=3` | `values {email •••, dob 1950, ssn •••, income 30000, …}`; reasons `(ssn, •••, median None)`, `(income, 30000, 41081.5)`; pii 0 |
| 2c | Vic `…&unmasked=1&reason=QA` | audit: 200, still masked, `unmasked_refused {needed maintainer, role viewer}`, would-deny `data.read_unmasked` · enforce: **403** |
| 2d / 2e | Ana `…&unmasked=1` (no reason) / `&reason=QA` | 400 `reason_required` / 200: `ssn 123456789`, `email user0000@example.com`, `dob 1950-01-01` (pii 12) |
| 3c / 3d | Vic `GET /runs/{id}/shift/holdout` / Ana `…&unmasked=1&reason=QA` | ssn `mean_expected / mean_actual / std` null (psi 0.135 kept; income's means shown) / ssn means 531261846.26 → 496455324.13 |
| 3e / 3f | Vic `…/shift/holdout/rows` / `…&unmasked=1&reason=QA` | rows `{set, origin, row, p_new_oof, w}`, `masked_columns` / would-deny · enforce **403** |
| 3h / 3i / 3j | `GET /runs/{id}/active-learning`: Vic / Ana `unmasked&reason=labelling` / Vic unmasked | `features {ssn •••, income 51571, …}` / `ssn 459505452` (pii 4) / would-deny · enforce **403** |
| 3l / 3m | `GET /runs/{id}/annotations` (60 rows × 3 annotators uploaded): Vic / Ana `reason=adjudication` | `fields {ssn •••, income 31258, …}` / `ssn 774272663` |
| 4b / 4c | `GET /datasets/v1/feature-lab/{job}` (target tier, `bin ssn k=4` **kept**): Vic / Ana `reason=QA` | `kept ssn_bin4 edges [•••, •••, •••]` / `[319850896.25, 518630273.0, 719371043.5]` |
| 4d / 4e | `GET /datasets/v1/split-options`: Vic / Ana `reason=QA` | dob `from 1950, to 1999` / `1950-01-01 → 1999-12-20` |
| 5 | Vic `…/rows?q=Person0007`, `…/grid?q=user0042` | `dataset_read` details `q: {hash 7198140b6171, len 10, redacted true}`; raw search text in any audit row: **0** |
| 6b | Cole `POST /datasets/{recoveries}/joins` pinning loans v1 `{income, segment}`, **no share** | **403 `input_not_shared`** "ds_… belongs to team tm_… and no live share to tm_… covers it" |
| 6c–e | Ben shares loans → Collections `{scope all, pii masked}`, Bo approves; Cole saves the join | 201; `shared_inputs [{share_id, pii masked, pii_columns []}]` |
| 6f / 6g / 6h | `columns [ssn, income]` / every column / aggregate `on [app_id, email]` | **409 `pii_masked_share`** columns `[ssn]` / `[applicant_name, dob, email, ssn]` / `[email]` ← **chosen behaviour: refuse** |
| 6i / 6j | Cole `POST …/frames {rec_loans}` / `POST /runs {join_id, label paid_back}` | 201, 300 rows, input `share_id`, `share_pii masked` / succeeded |
| 6k | Ben revokes | 200, `runs_kept [{run, via_join: true}]` |
| 6l / 6m / 6n | Cole frames (the cached one) / `POST /runs {join_id}` / `…/validate` after revoke | **403 `input_not_shared`** ×3 |
| 6o / 6p | Cole `GET …/frames/{f}?verify=1` / Ada `GET /admin/lake/runs/{join run}/verify` | verify ok / **ok, data ok, split ok**; the run stays succeeded |
| 6q–t | Ben shares `{pii unmasked}`, Bo approves with `grant_unmasked`; Cole joins `{ssn, income}`; frames again | 201 with `pii_columns [ssn]` allowed; the frame is served from cache (200) |
| 6 audit | Collections `join_input_refused` | input_not_shared ×4 (6b, 6l, 6m, 6n) + pii_masked_share ×3 (6f–6h), all in Collections' chain |
| 7a / 7b / 7c | `GET /datasets/v1/intake`: income untagged / tagged `sensitive` (Vic) / Ana `reason=QA` | `non-negative, median 41,081.50` / `median •••` / `41,081.50` |
| 8 | Credit Risk `dataset_rows_read` | Ana: anomaly_top, shift_check, active_learning_queue, annotations_queue, feature_lab, split_options, intake, each with its reason and columns; would-deny `data.read_unmasked` rows for Vic (audit) / `authz_denied` (enforce) |
| 8 | `GET /teams/{t}/audit/verify`, both teams | **ok** (all four runs) |

**SQLite and Postgres:**
- Every call's status code is the same on SQLite and Postgres in each mode (step names and codes diffed).
- Audit and enforce differ only at 2c, 3f and 3j: would-deny 200 against 403.
- The first Postgres enforce pass ran at the same time as the Postgres audit pass. Its shift check's AutoGluon fit
  failed ("No models were trained", 10 s per fit on a shared CPU), so 3e / 3f answered 404 (no finished check). That is
  not a masking path. It was rerun alone on a fresh database: shift succeeded and every step matches SQLite enforce. The
  file is the rerun.

## Deviations and notes for dependants

- **Masked share + PII in a join: refuse (409), not masked columns in the frame.** A masked frame column would make the
  frame's bytes depend on the reader's share. It would break the frame's identity (hash of spec + input shas), P15's
  serving parity (rebuild = the same bytes) and the model's inputs. A join key on a PII column is refused too, because a
  coverage count leaks values.
- **The share check refuses in audit mode as well.** It is a data rule, like P20's "audit mode stays masked".
- **The `pii_masked_share` audit rows** were first written in the **input's** team chain (the details' `version_id`
  was the input's). This was found in a development pass and fixed before the final runs: `join_input_refused` names the
  grain `version_id` and sets the joining team. All four final outputs show the 7 rows in Collections' chain.
- **P15 (`registry/build.py`, model-risk input record)** calls `J.validate` in a try / except. After a revoke the
  record's `join.coverage` holds `{"error": "JoinError: …input_not_shared…"}` and a `join_input_refused` row is written.
  The registration itself is unaffected. Not changed (P15's file).
- **Shift rows have no column values.** Like holdout predictions (P20), the page holds row numbers and scores only. It
  gets the masking state and the unmasked decision for consistency. The values a shift check does show (feature means)
  are masked on `GET …/shift/{key}`.
- **Intake and Feature Lab are scrubbed, not masked at the source.** `intake.py` and `featurelab.py` (and
  `autopilot/audit._text_of` for the active-learning text) are other lanes' compute code. The net replaces:
  - the values a column-keyed dict holds;
  - the column's quoted statistics in sentences.

  Over-masking is possible: a masked number's printed form inside an unrelated sentence is masked too.
- **F-JOBS coordination.** The edits to `shift_api.py`, `annotations_api.py` and `active_learning_api.py` are limited to
  the GET views (mask calls and three query parameters). No job / queue code was touched. Pulled and merged
  motion-components before the final runs: no conflicts.
- **Not masked (still outside this note):**
  - the upload `/check` preview of the uploader's own file;
  - fairness group values of protected columns (mask none by default);
  - runs on a **join frame**: `for_run` returns None for a frame, so frame-run pages are not masked. A frame now only
    holds another team's PII under an unmasked share, but the grain team's own tagged columns in a frame run's pages are
    a follow-up;
  - the edit preview's frames (P20: scrubbed).
- **For U2:**
  - "Unmask (reason)" on the anomaly top rows, the shift check, the active-learning and annotations queues, the Feature
    Lab job, split options and Smart Intake (`unmasked=1&reason=…&by=`);
  - `unmasked_refused`;
  - the join builder's 403 `input_not_shared` (ask for a share) and 409 `pii_masked_share` (columns);
  - `validate.shared_inputs`.
- **For P24:** `shares.runs_on` now includes `via_join` runs.
- **Portable SQL:**
  - the new queries use no `json_extract`, `rowid` or `json_set`;
  - `SELECT DISTINCT … ORDER BY` names its order column;
  - verified on Postgres 16.
- **Scratch:** the data dirs, the Postgres cluster (`/var/tmp/fpii_pg`), the venv and the API processes were in the
  scratchpad / `/var/tmp` and are removed.
