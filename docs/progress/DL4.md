# DL4 · Fixes for DLA3 (M-a–M-d, m-e–m-i, reset), and upload-as-new-version from Build, motion-components @ 9a01901 → this commit

**User principle:**
- Users can change data.
- Every change creates a new version.
- Nothing is ever modified in place.
- Changes made outside the app are detected.

DLA3's findings are in `docs/progress/DLA3.md`.

**Verification** (user rule: no smoke or integration suites):
- Manual API calls, chained the way the UI calls them. Every run used a scratch data dir, with `ENSEMBLE_ADMINS=Dana` and `ENSEMBLE_DEMO_MODE=1`.
- **Before** is the old build, 9a01901, run from a git worktree on its own scratch dir. **After** is this commit. Both ran the same repro script at the same time.
- Data: a self-generated 1,200-row `loans.csv`:
  - `loan_ref`;
  - **`zip` with leading zeros** ("02139", "00501", …);
  - income, with 40 missing;
  - loan_amount and credit_score;
  - region;
  - **an int 0/1 `default`** target.

  Plus `loans_2026-11.csv`, a later extract: 1,100 of the same rows (20 incomes corrected) and 60 new ones.
- Runs: 20 s, `medium_quality`, no bagging or stacking.
- The edit module was fuzzed directly: 4,023 formulas (23 fixed, the rest random) and 176 op / value cases.
- `npm run build` and `tsc --noEmit` pass.
- `ruff --select F` is clean on the touched files, except `engine/worker.py` `TabularPredictor`, an F401 that was already in HEAD.
- Every scratch dir, with its runs and predictors, was deleted afterwards, and so was the worktree.

## Before → after (each DLA3 repro, same calls on both builds)

| # | Repro | Before (9a01901) | After (DL4) |
|---|---|---|---|
| M-a | edit preview `add_column z = round(income, 1e18)` | **500** (OverflowError) | **400** "round(x, digits) takes a whole number of digits from -15 to 15." The fuzz gave **0 non-400 exceptions** across 4,023 formulas and 176 op cases. It also caught two more 500s, now fixed: `set_cells` of 1e308 or 2^63 into an int column |
| M-b | `add_rows` one row (default = 1) | 201, but `default` was now "0.0 / 1.0", with a `new_target_category` warning and **not the default**. data.csv: `L-00000,33101.0,…,642.0,north,0.0` | 201, **no warnings, made the default**. Target values {0, 1}. data.csv: `L-00000,33101,…,642,north,0`. The schema keeps `default: integer` and `credit_score: integer` |
| M-c | `drop_column default` | **201, target silently became "region"** | **400** "Operation 1 drops the target 'default'. Runs on this version would predict something else, so this is refused … send "set_target": {"column", "reason"}". Audited `dataset_edit_refused` |
| M-c | `rename_column default → is_default` | 201, target "is_default" | **400** (same rule, "renames (to 'is_default')") |
| M-c | `cast_column default → text` | 200, deduped to the parent | **400** (same rule, "casts (to text)") |
| M-c | rename + `set_target {column: is_default, reason}` | (no such field) | **201**: target `is_default`, `target_source: confirmed`, a `target_changed` warning (so it is not made the default), audited `dataset_target_changed` (from, to, reason) |
| M-d | truncate the holdout in `splits.json` and `splits/*.json` (240 → 50 rows), then verify | **verify "ok"** | **verify "SPLIT MISMATCH"**, listing both files. The remedy points to `restore-splits` |
| M-d | then train | **201, succeeded on 50 holdout rows** | **409** at run creation: the last verify was a mismatch |
| M-d | tamper again **without** a verify, then train | — | Run created, then **failed at fit start**: "the split file of version v1 … differs from when it was drawn: sha256 7411d8b001e9… then, 669964d8e4a6… now … its holdout would not be the one other runs on this version use. Restore it …; an admin may train anyway …". Audited `dataset_integrity_failed` (what: split file) and `training_refused`. The version is flagged |
| M-d | override | — | bob with a reason → **403**. Dana with a reason → 201 and succeeded; `splits_integrity.match: false` is recorded on the run; audited `training_integrity_override` |
| M-d | remedy | — | `POST …/restore-splits {by, reason}` writes the files back from the copy recorded at draw time (hash-checked), re-verifies (→ "ok") and is audited `dataset_splits_restored` |
| M-d | what the run records | data_sha256 only | `splits_sha256` next to `data_sha256`, plus `splits_integrity {expected, match, file}`. Both are also in the run config and the trained_on edge |
| m-e | upload a file with ZIP codes | `zip` read as a **number** (02139 → 2139) | `zip` is **text**: grid "02139", 133 rows match "02139". Profile warning "Kept as text (codes with leading zeros …): zip". Preview line "Kept as text …" |
| m-e | `cast_column credit_score → text` | 200 `created: false`, **deduped to the parent** | **201, a new version**, `credit_score` kind text, `types_changed` [integer → text] |
| m-f | two `add_rows` of 800 each | 200 (2,800 rows) | **400** "At most 1,000 added rows per version (1,600 with this operation) …" |
| m-g | rename region → branch_region + replace north → N | summary **changed 0** vs diff 312 | summary **312** = diff **312** |
| m-h | delete a dataset a succeeded run trained on | pending → carol approves → **files deleted** | **409**, one blocker per run: "run … (succeeded) was trained on …: deleting the data makes it irreproducible. Delete the run, or retire the model built from it, or send "irreproducible": {"reason"} …" |
| m-i | upload-version preview | **201** | **200** |
| reset | Dana (admin + flag), nothing registered | **200, wiped 19 dataset rows and 2 runs** | **409** "Can't reset: user data, live models or champions would be deleted. Get a four-eyes approval …". Blockers: "3 user dataset version(s) …", "3 run(s) …" |
| reset | names | — | "D a n a" approves Dana's own request → **403** (identity_key folds spaces; checked). carol approves → Dana runs → **200**, `four_eyes: arq_…` |

## Regression chain (after; a fresh scratch dir)

1. **Upload v1** (`loans.csv`, bob): `zip` stays text ("02139"). The profile warns "Kept as text: zip".
2. **Build upload of `loans_2026-11.csv`:** `POST /api/datasets/match` → **loans v1**, `next_v` 2. Reasons: "the same 7 columns · its target 'default' is in the file · a similar name ('loans_2026-11' ~ 'loans')". An unrelated `inventory.csv` → `match: null`, so it uploads as before.
3. **Preview** → **200**: v2, matched 1,100 by `loan_ref`, changed 20, +60, −100, no warnings. **Create** with reason "CHG-5001 November extract" → v2, kept off the default as asked (`make_default: no`).
4. **Edit v2:** add 2 rows (zip "00501" / "02139", default 1 / 0) and set one cell.
   - Preview → 200: changed 1, +2, no warnings.
   - Schema: zip text, credit_score integer, default integer.
   - Create → **v3**, made the default (no warnings).
   - data.csv's last rows: `L-90001,00501,41000.0,8000.0,712,east,1` and `L-90002,02139,…,640,north,0`.
5. **Diff v2 → v3:** matched 1,160, changed 1, +2, −0. The edit block is by bob. No type changes.
6. **Train** v1 and v3 (20 s each): both succeeded. Each records `data_sha256` and `splits_sha256`. **Paired compare** → paired, "overlap only (219 of 240 rows)".
7. **Split tamper** (v3's split truncated) → the next run **failed at fit start** with the plain message.
8. **Verify** → v3 "SPLIT MISMATCH". **restore-splits** → restored → verify "ok".
9. **Archive and delete:**
   - archive v2 → 200;
   - delete the dataset → **409** (two succeeded runs);
   - delete with an ack shorter than 10 characters → 400;
   - delete with `irreproducible {reason: "CHG-5003 …"}` → **pending**, and the request lists `runs_made_irreproducible`;
   - "B o b" approves his own → **403**;
   - carol approves → v1–v3 deleted (tombstones), audited with the acknowledgement.
10. **Reset:** Dana alone → 409; request; "D a n a" approves → 403; carol approves; Dana runs → 200 (split_files cleared too). The audit log is kept.

## UI (screenshots `docs/progress/DL4/`, 1366 wide, `scripts/dl4_shots.cjs`; 0 console errors besides the deliberate 400 of shot 07)

- `01-build-add-as-version-prompt`: Build after dropping `loans_2026-11.csv`. "loans_2026-11.csv looks like **loans** v1 (its default) · 1,200 rows", with the reasons. **One primary action: Add as new version of loans (v2)**, then Create a new dataset and Cancel. No new nav item.
- `02-build-add-as-version-preview`: the same `UploadVersion` component as Data → "Upload a new version…": matched 1,100 / changed 20 / +60 / −100, "Kept as text: zip", no contract warnings, a reason, and Create v2 / Create v2 and make it the default.
- `03-build-version-created`: Build opens v2, with the result line.
- `04-split-mismatch-banner`: Data → v1 after the split file was truncated on disk → Verify hash. The banner offers **Restore the split as drawn** (and, for data.csv, Create a new version from this file).
- `05-split-restored`: "Restored splits/… as drawn; verify: v1 ok".
- `06-edit-grid-zip-text`: the editor's grid searched for "02139": `zip` is a TEXT column with its zeros.
- `07-edit-drop-target-refused`: dropping `default` in the editor → the 400 in words.

## What changed

**Backend**
- `frame_io.py` (new): the per-version column schema `schema.json` {column: text | integer | number | bool}.
  - `read()` reads data.csv with it (text columns as strings).
  - `to_csv_bytes()` writes integers without ".0" (nullable while a cell is missing).
  - `schema_of()` derives a schema, with the parent's as the hint.
  - `leading_zero_columns()` finds codes with leading zeros.
- `datasets.py`:
  - `read_upload` keeps leading-zero columns as text, and `coerce_numeric` can't undo that. `create_from_frame` / `create_version` / `derive` write data and schema together.
  - `load_frame` reads with the schema.
  - Dedupe (`_same_bytes`) requires the same bytes **and** the same schema.
  - `edit_version`:
    - carries the schema through renames, drops and casts;
    - refuses target drop / rename / cast without `set_target` (audited);
    - counts `changed` with renamed columns mapped back;
    - reports `schema`, `types_changed` and `target`.
  - Split files are hashed and copied when drawn (`record_split_file`, `split_files` table, `datasets.splits_sha256`). Also `check_splits` and `restore_split_files`.
- `dataset_edit.py`:
  - ints stay ints through `add_rows` and `set_cells`;
  - the add_rows cap is per version;
  - `round` digits are limited to −15..15;
  - overflow-safe constants and values;
  - any non-EditError in an op becomes a 400;
  - it reports `target_touched` and `casts`.
- `dataset_versions.py`: target categories compare 1.0 as 1.
- `catalog/dsets.py`:
  - verify checks the split files ("SPLIT MISMATCH", remedy);
  - the integrity gate's message covers split files;
  - the delete guard blocks on succeeded, non-retired runs unless `irreproducible {reason}` is sent (four eyes, audited with the runs);
  - `match_upload()`.
- `catalog/datasets_api.py`:
  - upload-version preview → 200;
  - `set_target` on edit;
  - `POST /datasets/{id}/restore-splits`;
  - `POST /datasets/match`;
  - `irreproducible` on delete.
- `engine/worker.py`: reads data.csv with its schema; hashes the split bytes it reads; `splits_gate()` at fit start. `engine/api.py`: `queue_run` records the split file's expected hash; `run_out` returns `splits_sha256` / `splits_integrity`.
- `reset.py`: `user_data()`: any user dataset version or run makes a reset need four eyes. `split_files` is reset with the catalog. `main.py`: the reset-request list shows it.
- `catalog/schema.py`: the `split_files` table and `datasets.splits_sha256`.

**UI**
- `BuildPage.tsx`: dropping a CSV first asks `POST /datasets/match`. On a match, a full-width card offers **Add as new version of <dataset> (vN+1)** (primary) or Create a new dataset. "Add" opens the Data page's own `UploadVersion` (preview, schema, warnings, reason, the same rules) with the file already loaded. No match → upload as before.
- `DataView.tsx`:
  - `UploadVersion` is exported, takes an `initialFile`, and shows "Kept as text";
  - the integrity banner adds **Restore the split as drawn**.
- `catalog.ts`: `data.match`, `data.restoreSplits`, and the types.

## Remaining limits

- **Names, not identities:** still no sign-in. Four eyes, admins and the reset gate compare names folded by `identity_key`.
- **Schema coverage:**
  - The schema is read wherever a version's rows are loaded through the store (`load_frame`: grid, diff, edit, intake, profile) and in the training worker.
  - Some other run kinds and analysis endpoints still call `pd.read_csv(csv_path)` directly (anomaly, forecast, explain, slices, …). For them a ZIP column still reads as a number.
  - Versions made before DL4 have no `schema.json` and read as before. The samples are unchanged, so the G4 sample's `zip` is still a number.
- **Split files made before DL4** are recorded on first sight ("first seen (drawn before DL4)"), so a split altered before that first sight is trusted. New splits are recorded when they are drawn.
- **The split copy lives in the same SQLite file as its hash.** Someone who can edit both the file and the DB can defeat it; the threat covered is a file changed outside the app.
- **The table's sha256 column** shows "× mismatch" when the version's last check failed, whether it was data.csv or a split file. The banner and Verify say which.
- **set_target is API-only:** the editor shows the refusal but has no control to name a new target.
- **Build's match** compares column names, target presence and the name (a heuristic: 60 % shared columns plus the target, or a similar name plus 40 %). It matches against each dataset's default version only. A built-in sample with the same columns ranks below the user's own datasets.
- **m-h:** "retired" means a registered version built from the run was retired. A run with no registered version clears the block only by being deleted, or with the acknowledgement.
- **The paired-compare Δ** was not re-read in this session. Pairing and overlap were checked (219 of 240 rows).
- **m7** (sample regeneration) was not re-tested, as in DLA3.
