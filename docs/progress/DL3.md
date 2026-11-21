# DL3 · Edit-as-version, and fixes for DLA2 (C1, M1, m1–m7), motion-components @ fba1ed1 → this commit

**User decision:** "We should be able to change (tamper with) datasets. Every change should create a new version."

What that means here:
- Users edit data in the app.
- Every edit commits as a **new version** through `derive()`, with lineage, a diff, a reason and an audit row.
- Nothing is modified in place.
- A change made on disk outside the app is **detected** (at verify time and at fit time) and never trusted. The remedy offered is "create a new version from this file".

DLA2's findings are in `docs/progress/DLA2.md`.

**Verification** (user rule: no smoke or integration suites):
- Manual API calls, chained the way the UI calls them, on a scratch data dir (`ENSEMBLE_ADMINS=Dana`, `ENSEMBLE_DEMO_MODE=1` unless stated).
- Data: a self-generated 1,200-row `loans.csv` (`loan_ref`, income with 40 missing, loan_amount, credit_score, region, default), plus 4-row and 7-row cuts of it.
- Runs: 15–20 s, `medium_quality`, no bagging or stacking.
- UI walked in Playwright at 1366 wide (`scripts/dl3_shots.cjs`), with 0 console errors.
- `npm run build` and `tsc --noEmit` pass.
- `ruff --select F` is clean on every touched file, except two F401s that were already in HEAD (`registry/api.py` `unicodedata`; `engine/worker.py` `TabularPredictor`).
- The scratch dir was deleted afterwards. It held 7 runs, 3 registered versions and a champion; the four-eyes reset had already wiped them.

## Before → after

| # | Repro | Before (DLA2) | After (DL3) |
|---|---|---|---|
| Edit | Change cells, rows or columns in the app | impossible ("no hand editing, by design") | `POST /api/datasets/{id}/edit {ops, reason, by, preview?, make_default?}` takes 10 typed ops. The result is a new version via `derive()`; the parent is untouched (sha256 checked). UI: Data → a version → **Edit vN…** |
| C1 | `POST /api/admin/reset {"confirm":"reset"}` | 200. Wiped `audit_log` 108 → 14 and left a dangling champion, 20 dangling edges and no audit row | Flag off → **403** "on only when the API runs with ENSEMBLE_DEMO_MODE=1". No `by` → **400**. bob → **403** (not an admin). Dana with a live champion → **409**, listing the model and the champion. Four eyes: Dana asks; "D.A.N.A" approves → **403**; carol approves; bob runs → 403; Dana runs → **200**. `audit_log` went 75 → 93: nothing deleted, plus its own `admin_reset` row (actor, per-table counts, `audit_rows_kept: 78`, the four-eyes ids). The request is marked `used`. Champions 1 → 0, edges 13 → 0, cards / requests / annotations / projects cleared, so nothing dangles. Sample projects come back. Every refusal is audited (`admin_reset_refused`, `admin_reset_decide_refused`) |
| C1 | `python -m app.reset` (and `make reset` offline) | ran with no checks | The same rule. Flag off → refused (exit 1, audited, `via: cli`). `--by bob` → 403. `--by Dana` → reset, audited `via: cli`. `make reset BY=… [REQUEST=…]` documented in the Makefile and README |
| M1 | Tamper with v2's data.csv after an OK verify, then train | 201, trained, and the run recorded integrity "ok" | Run created, then **failed at fit start**: "data on disk differs from version v2 (ds_8ce3eb13): its sha256 was 05d92553bd52… when the version was made, the file read now is eb44a73b7232…. … Create a new version from this file …, or restore the file; an admin may train anyway with integrity_override_reason (audited)". Audited `dataset_integrity_failed` (by `system:train-time hash`, with stored and read sha) and `training_refused`. The version is flagged `verified_ok = 0` |
| M1 | Override | — | No reason → 409. bob with a reason → 403. Dana with a reason → 201; the fit runs on her override, audited `training_integrity_override` at request and at fit. An admin can also **pre-arm** an override on a verified-OK version: on a later mismatch the fit proceeds, audited at fit |
| M1 | Which bytes did the run read? | not recorded (undetectable after a restore) | The worker hashes the bytes it actually parses (`load_frames` reads the file once and hashes those bytes). The hash is stored in `result.data_sha256` + `data_integrity {expected, match, basis}`, in `config.data_sha256`, and in the trained_on edge's meta (`data_sha256`, `data_sha256_match`). Shown on the run page ("data 05d92553bd52 ✓" / "✕ differs from its version") and in the lineage tree (screenshots 08–10). Other run kinds hash the file at run start (`basis` says so) |
| M1 | Remedy | "restore the file" | Verify gives `remedy` on MISMATCH. **Create a new version from this file** (`POST …/adopt-disk-file {by, reason}`) commits the on-disk bytes as a new upload-as-version, with its own hash, a reason and `dataset_disk_file_adopted`. It is not made the default. The parent stays flagged until its file is restored. Editing a mismatched version is refused (409) |
| m1 | Dana asks to retire, alone | retired at once | **pending** with "you are an admin, but a retirement still needs a second person". Dana approves → 403. carol approves → retired. With `ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE=1`: retired at once, with comment "SELF-APPROVED BY ADMIN (no second person; …)" and `self_approved_by_admin: true` in the audit. The UI shows a red banner. The same rule now applies to dataset delete |
| m2 | Reads without a name; dedupe on the raw name; three reads unlogged | allowed / raw / unlogged | Anonymous → **400** on row lookup, grid, diff, holdout-predictions, anomaly top and shift rows. "Erin" and "e.rin" → one audit row (dedupe on `actor_key` = identity key). `holdout_predictions`, `anomaly_top` and `shift_rows` are logged against the run's dataset, written only after the read succeeds. Autopilot's HTTP client reads as `system:autopilot <id>` |
| m3 | Derived version with no name | "system" | `derive()` refuses (400): "Say who is making this data_doctor version…". Data Doctor and Feature Lab no longer default to "you". Only genuine background jobs (autopilot, sample regeneration) are `system:<job>`. Identical bytes: edit, shift weights, anomaly review and Feature Lab return the existing version (`dataset_version_deduplicated`). The same edit sent twice → 200 `created: false, duplicate_of: ds_8ce3eb13`. Other paths flag it (`identical_file` warning, `duplicate_of`) |
| m4 | 4-row upload | raw sklearn `n_splits` error | **400** "This file has 4 data row(s): after setting 20% aside as the holdout, 3 are left, and at least 5 (one per cross-validation fold) are needed. Upload at least 8 rows …" |
| m5 | Train on a 7-row version | accepted, failed later | **400** "This version has 7 labelled rows, and 5 of them are left for training after the holdout: at least 20 are needed …". Classification also needs ≥ 2 training rows per class in at least two classes |
| m6 | erin withdraws bob's delete request | allowed | `{"withdraw": true}` by erin → **403** "Only bob (who asked) or an admin may withdraw … erin may reject it as a reviewer instead". "B.O.B" → withdrawn; Dana (admin) → withdrawn. A reviewer's `approve: false` stays a recorded rejection (four eyes). Also fixed: the reject response showed `pending` (read before commit) |
| m7 | A sample in use whose generator changed | rewritten in place | `loan-stream`, trained on: v1's file is unchanged (sha checked). With the generator unchanged, the regenerated rows equal v1 → deduped, no new version. With a changed generator (driven through the same code path from a manual script: 50 rows fewer, 30 values ×1.1) → **v2** `sample_regenerated` by `system:sample generator`, "the sample generator changed", matched 8,950 by `application_id`, changed 30, removed 50. The sidecar is copied |

## Edit flow (manual, on `ds_83ed60fc`, 1,200 rows)

1. **Grid.** `GET …/grid?limit=3` with no name → 400. As bob → 6 typed columns, `_row` ids, total 1,200.
2. **Preview** (bob): `set_cells` on row 0 income 49,045 → 61,000, row 5 credit_score 669 → 700 and region south → north-east; `delete_rows` [1, 2, 3]; `delete_rows where credit_score < 520` (3 rows); `add_column dti = loan_amount / income`; `rename region → branch_region`; `add_rows` × 2.
   - Result: v2, 1,196 × 7, matched 1,194, changed 2, +2, −6.
   - Warnings: one, "'region' was renamed to 'branch_region' …", so `would_be_default: false`.
   - **Nothing written:** 16 dataset dirs before and after.
3. **Create** with reason "CHG-2001 …" → `ds_8ce3eb13`, `created: true`.
   - Not the default: "1 contract warning(s): column_removed". The default stays pinned to v1.
   - **v1's sha256 is unchanged.**
4. **Diff v1 → v2** (needs a name):
   - matched 1,194, **changed 2, +2, −6**;
   - schema +`dti`, **renamed region → branch_region** (compared under the old name, not as removed + added);
   - changed cells: row 0 income 49,045 → 61,000; row 5 credit_score 669 → 700 and `region → branch_region` south → north-east;
   - an `edit` block: by bob, the reason, and the 6 ops with old → new examples.
5. **Root / holdout carry-over** (from lineage.json and the split files):
   - every matched row keeps its root row (its v1 position) and its holdout / fold membership;
   - the 2 new rows have root ids −1 and −2, and 1 of them is in the holdout;
   - deleted rows 1–3 are gone;
   - holdout 240 → 239 (2 deleted, 1 new).
6. **History:** v2 `edit` by bob, the reason, and `edit_ops` (6 summaries with rows and cells). `n_changes` is 6. Audit: `dataset_version_created` (bob). Reads are audited as `dataset_read`.
7. **Train on both, then paired compare:**
   - `run_3e9796f6` (v1) accuracy 0.8042; `run_1b34d1ba` (v2) 0.8243.
   - `POST /api/compare` → **paired** on the same original holdout rows, "overlap only (238 of 240 rows)".
   - Δ accuracy +0.021, 95 % CI [−0.0085, +0.0504], so the gain is within noise.
8. **The other ops** in one preview:
   - fill_missing median: 40 cells;
   - replace exact north → N: 293 cells;
   - replace contains L- → LN-: 1,200 cells;
   - cast credit_score to text;
   - add a constant column, then drop it;
   - `fn: difference`;
   - filter_rows with an `in` and a `not_missing` condition: 0 removed.
9. **Invalid input → 400 with a plain message:**
   - formulas: `__import__("os").system("id")`, `income ** 2`, `income.__class__`, `region / income` (a text column);
   - set_cells: text in a number column; row 99999; a row deleted earlier in the batch;
   - other ops: an unknown op; a rename onto an existing name; a cast to `date`; a filter that removes every row;
   - the request: no reason, no `by`, empty ops.

   Formulas are parsed with `ast` and evaluated by walking the tree: never `eval`.
10. **UI** (screenshots 01–06): Edit v1 → cell edits (income, region), ✕ on row 4, `lti = loan_amount / income`, rename credit_score → fico → Preview (v4: changed 2, −1, 1 warning) → reason → **Create v4** → the diff opens on v1 → v4 with the edit block, and the versions table shows "4 edits: …" and the reason.

## What changed

**Backend**
- `dataset_edit.py` (new): the 10 ops, safe formulas (`+ − × ÷`, `abs sqrt log log1p exp round min max`, column names, numbers), filters, typed value coercion, and a change log with up to 20 old → new examples per op.
  - Caps: 200 ops, 5,000 cell edits, 1,000 added rows.
- `datasets.py`:
  - `edit_version()`: refuses a mismatched parent, preview, contract checks with rename-aware messages, dedupe by sha, `derive()` with a row map, `edit_ops.json` (the full ops as sent, beside lineage.json).
  - `derive()` requires a name (m3) and dedupes or flags identical bytes.
  - m4 plain error.
  - m7 `_sample_as_version()`.
- `catalog/datasets_api.py`: `GET …/grid`, `POST …/edit`, `POST …/adopt-disk-file`. Diff needs a reader name. Delete withdraw (m6).
- `catalog/dsets.py`:
  - the diff maps renamed columns back and includes the `edit` block;
  - history gains `edit_ops` and `duplicate_of`;
  - verify gives a `remedy`;
  - `integrity_gate()` can pre-arm an admin override;
  - delete four eyes for admins (m1);
  - withdraw rules (m6).
- `catalog/governance.py`: `reader()`, `read_audit()` dedupes on the identity key, `run_read_audit()`, `allow_admin_self_approve()` / `SELF_APPROVED`.
- `catalog/schema.py`: the `admin_requests` table (kept through a reset).
- `reset.py`: rewritten (C1). `main.py`: `/api/admin/reset-requests` (+ decide, list) and the governed `/api/admin/reset`.
- `engine/worker.py`: `load_frames` hashes the bytes it reads; `data_gate()` (M1). `engine/api.py`: `queue_run` records the expected sha and the version label; m5 pre-check; `run_out` returns `data_sha256` / `data_integrity`; holdout-predictions read audit.
- `registry/api.py`: retire needs a second person, admins included (m1).
- `anomaly/api.py` and `engine/shift_api.py`: read audit (m2).
- `data_doctor_api.py` and `featurelab_api.py`: no "you" default (m3).
- `autopilot/engine.py`: HTTP actor `system:autopilot <id>`.
- `splits.py`: the m4 message.
- `Makefile` and `README.md`: `make reset BY=… [REQUEST=…]` and the flag.

**UI** (no new nav item; Data page only, plus two small additions)
- `models/data/EditVersion.tsx` (new):
  - grid (25 a page, search, ‹ ›);
  - click a cell to edit; ✕ / undo per row; + Row;
  - column tools (rename, drop, cast, fill missing, replace value);
  - new column (formula or constant);
  - **Pending** list (each removable);
  - **Preview** → **Create vN** (reason required). One primary at a time.
- `DataView.tsx`:
  - **Edit vN…** on the selected version;
  - the integrity banner with **Create a new version from this file…**;
  - "N edits: …" in the versions table;
  - the diff shows renamed chips and the edit block;
  - copy updated (cells change only through a new version).
- `RunPage.tsx`: "data <sha> ✓ / ✕". `ProjectTree.tsx`: a data-sha chip per run. `VersionOverview.tsx`: the self-approved banner and copy.

## Screenshots (`docs/progress/DL3/`, 1366 wide)

- `01-edit-grid`: Edit v1, the row grid with search and pages.
- `02-pending-ops`: two edited cells (highlighted), row 4 struck through, and the Pending list (cells, delete, `lti` formula, rename).
- `03-preview`: Preview v4 (changed 2, −1, sha), each op with old → new, schema chips, the rename warning, the reason, **Create v4**.
- `04-created-message`: the result line after creating.
- `05-diff-of-the-edit`: diff v1 → v4. "Edited by bob" with the reason and the ops; row 0 income 49,045 → 52,000; row 6 region west → north-east; row 4 deleted; `lti` new; credit_score → fico.
- `06-versions-history`: v2 / v4 "edit from v1" with "N edits: …", the reasons, v3 from the disk file.
- `07-integrity-mismatch-remedy`: v3's file changed on disk. The banner and **Create a new version from this file…** with its reason.
- `08-lineage-data-sha`: the lineage tree with the sha of the bytes each run read. Red ✕ on the runs that read altered bytes.
- `09-run-data-sha`: the run page with "data 05d92553bd52 ✓".
- `10-run-refused-tampered`: the refused run with the plain message.

## Remaining limits

- **Names, not identities.** There is still no sign-in, so anyone can type "Dana". Four eyes, admins and the reset gate compare names.
- **CSV round-trip types.** A `cast_column` to text of a column of numbers reads back as numbers, because the store re-reads data.csv and lets pandas infer types. Casting text → number works. A per-dataset schema would fix this.
- **Edits are capped:** 5,000 cell edits and 1,000 added rows per version. More goes through upload-as-version. Grid search is a substring match over all columns, computed per page request (fine for ~1M cells; no index).
- **Rename across versions.** The diff maps a rename back only for a direct edit parent → child. Across several hops, a renamed column shows as removed + added.
- **The fit-time hash covers data.csv, not the split file.** Split files are drawn once and never redrawn, but they are not hashed. Other run kinds (forecast, survival, …) hash the file at run start rather than the exact bytes parsed.
- **adopt-disk-file can't recover the original bytes.** It matches the on-disk file against itself, so the new version shows 0 changes against its parent. The real change is visible only by diffing against an older export. The parent stays MISMATCH until its file is restored.
- **Reset with nothing live** needs only an admin and the flag. Four eyes applies only while a model or champion is live (as specified). `scripts/smoke.py --only reset` and any test that called the old reset now need the flag and `by`. Not run, per the rule.
- **Sample regeneration** of a sample in use was exercised through the same code path from a manual script, with a perturbed generator. A real generator-code change was not made. The new version becomes the default only as "latest" when no default was pinned.
- **The admin pre-armed override** is validated at run creation but audited only if a mismatch is actually found at fit.
