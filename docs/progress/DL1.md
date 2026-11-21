# DL1 · Dataset lineage and dataset management

**Goal.** Give a bank analyst the basic dataset management they expect, on top of P1's versions and `lineage_edges`:
- upload a file as the next version;
- history and diff;
- the reverse lineage (who used this version);
- export with lineage;
- a default version and rollback;
- a dataset card;
- archive and four-eyes delete;
- an integrity check, contract warnings, search and an audit trail.

No hand editing of cells: that stays out by design, for audit.

**Status:** Built, plus one dataset page and Build's version strip:
- every "missing" and "partial" item in the audit except the two recommendations at the end;
- Models gets a **Data** tab (no new nav item);
- Build gets a version strip.

**Verification:**
- `npm run build` and `tsc --noEmit` pass.
- `ruff --select F` is clean on every file touched.
- No smoke or integration suites were run, per the user's rule. Every check below was a manual API call, chained the way the UI calls them, on a scratch data dir. The UI flows were then walked in Playwright at 1366 wide.

**Size:** about +1,680 / −80 lines.
- Backend: +906 in new files (`catalog/dsets.py`, `catalog/datasets_api.py`, `dataset_versions.py`), plus +238 / −69 in `datasets.py`, `catalog/store.py`, `catalog/schema.py`, `catalog/api.py`, `catalog/lineage.py`, `main.py`, `http.py` and `schemas.py`.
- UI: +439 in new files (`models/data/DataView.tsx`, `data.css`, `build/VersionStrip.tsx`), plus +93 / −9 in `RunList`, `ProjectTree`, `BuildPage` and the API client and types.
- `scripts/dl1_shots.cjs` takes the screenshots.

## Step 1 · Gap audit (before DL1, at `1cea30e`)

**Reference points:**
- DVC: `dvc diff`, `dvc get`, git tags as versions.
- lakeFS: commit, diff, revert, branch protection, object hash.
- MLflow datasets: `log_input`, digest, source, schema, profile.
- Delta time travel: `DESCRIBE HISTORY`, `VERSION AS OF`, `RESTORE`, `CHANGE DATA FEED`, constraints.

| # | Capability | Before | What existed / what was missing | Reference |
|---|---|---|---|---|
| 1 | Upload a file as the next version (parent lineage, same holdout on matched rows, schema check) | **missing** | Every upload became a new root with its own project and holdout. No row matching, so a re-delivered extract could never be paired with the first. | lakeFS commit, Delta `MERGE`/overwrite → new version |
| 2a | Version history (who, when, op, changes, rows × cols, hash) | **partial** | `datasets` had parent, root, op, sha256 and n_rows, and the edge had actor and n_changes. Nothing listed them per dataset. The project tree showed op and rows, but not who, when, columns or hash. The Build picker listed every version flat, by name only. | Delta `DESCRIBE HISTORY`, `dvc log`, lakeFS log |
| 2b | Current / default version | **missing** | There was no pointer. "Latest" was whatever row was picked. | lakeFS branch head, MLflow alias |
| 3a | Diff: schema | **missing** | — | `dvc diff` (files only), Delta schema history |
| 3b | Diff: rows added, removed and changed | **partial** | Only the SQL refresh had a diff, by row hash and for its own case. root_rows existed, but nothing used them for a diff. | lakeFS diff, Delta CDF |
| 3c | Diff: per-column stat shifts (missing %, mean/median, top categories, PSI) | **missing** | PSI existed in `registry/drift.py`, for monitoring only. | MLflow dataset profile (static), none of the four do shift |
| 4 | Reverse lineage: runs, registered versions and champions that used a version | **partial** | `lineage_edges` held trained_on and built_from edges. The tree drew them downward from the root. There was no per-version "used by" answer and no API for it. | MLflow `search_runs` on the dataset digest |
| 5 | Download or export a version (CSV + lineage.json + hash) | **missing** | No download route at all. | `dvc get`, lakeFS object get |
| 6 | Set the default version; roll back (pointer only) | **missing** | — | lakeFS revert (a new commit), Delta `RESTORE` (a new version); ours moves a pointer |
| 7 | Rename, describe and tag a dataset (card: description, owner, source, label definition, known issues) | **partial** | Tags and notes worked on `dataset` targets through the annotations API, but no UI showed them for datasets. Projects had a name and description. No dataset card, no owner, no rename. | MLflow dataset `source` + tags; Datasheets for Datasets |
| 8 | Archive or delete, guarded and four-eyes, with tombstones | **missing** | Nothing could be removed except by an admin reset of everything. | lakeFS branch protection; Delta `VACUUM` (no guard) |
| 9 | Integrity: re-hash on demand, flag drift | **partial** | sha256 was stored at creation, but nothing re-checked it. | DVC `status`, lakeFS checksums |
| 10 | Contract checks on a new version (target missing, type change, new target category, row drop > X %) | **missing** | Only the target check on upload (empty or single-valued). | Delta constraints (blocking); Great Expectations |
| 11 | Search and filter datasets (project, tag, owner, op, date) | **missing** | One flat select in Build. | MLflow `search_datasets`, lakeFS repo list |
| 12 | Audit entry for every dataset action | **partial** | Audited: SQL load and refresh (`dataset_loaded` / `dataset_refreshed`) and `dataset_moved`. Not audited: CSV upload, every in-app version (Doctor, Feature Lab, relabel, label queue, drift window), tags and notes on datasets. | Delta history (who/what), lakeFS audit (enterprise) |
| 13 | Lineage UI per dataset (versions + the runs and models using them), consistent with the project tree | **partial** | The project tree (Models → Lineage) covered it per project. There was no dataset-level page and no link from the tree to a dataset. | MLflow UI dataset tab |
| + | Who created a CSV upload | **missing** | `POST /api/datasets` took no name, so roots had no creator. | — |
| + | Version number (v1, v2 …) per dataset | **missing** | Only ids. SQL had its own `version` in lineage.json. | Delta version number |
| + | Archived or deleted datasets hidden from pickers; training on a deleted one refused | **missing** | Not applicable before, since nothing could be archived or deleted. | — |
| + | Hand editing of cells | not built, by design | Out of scope for audit: changes go through a transform (Doctor, Feature Lab, relabel) or a new upload. | — |

## After DL1

| # | Capability | After | How |
|---|---|---|---|
| 1 | Upload as the next version | **have** | `POST /api/datasets/{id}/versions` (file, by, row_key). Rows are matched by the row key, or by an id-like column guessed from its name, or by an exact row hash over the shared columns of unchanged type. `derive()` gained a `row_map` (each row's position in the parent, or −1), so matched rows keep their root row **and** their holdout or fold. New rows get a seeded 20 % of the holdout and root ids below the parent's. Contract checks run (see 10). UI: Data → **Upload a new version…** (the page's one primary), with "match rows by". |
| 2a | Version history | **have** | `GET /api/datasets/{id}/history`. For every version: v-number, op, parent, who (a new `created_by` column; CSV uploads now send the acting name), when, the changes (matched / changed / added / removed, or rows before → after), rows × cols, sha256 and its last verification, state, tags, warnings and what used it. UI: the Versions table. Build's picker lists datasets, and a **versions** strip under it opens any version. |
| 2b | Default version | **have** | `dataset_cards.default_version_id` holds the pointer. With none set, the default is the newest active version ("latest"). Choosing a dataset in Build opens its default. |
| 3a–c | Diff any two versions | **have** | `GET /api/datasets/{a}/diff/{b}`. **Schema:** columns added and removed, type changes (number / text / bool / date), dtype-only changes. **Rows:** matched by b's row map onto its parent (or the reverse), else by shared root rows, else by exact hash. It returns changed, added and removed counts plus five changed rows cell by cell. **Columns:** missing % A → B, mean and median, top value with its share, and PSI by `registry/drift.feature_psi` with the stable / watch / shift bands. PSI is suppressed when the type changed. UI: pick A and B in the table. |
| 4 | Reverse lineage | **have** | `GET /api/datasets/{id}/usage` follows trained_on → runs → built_from → registered versions → champions, for the version and every version derived from it, and lists what blocks an archive or delete. UI: "Used by vN" with links to the runs and the Registry. Per-version counts and a champion chip are in the table. |
| 5 | Export | **have** | `GET /api/datasets/{id}/export?format=zip` returns data.csv, lineage.json and manifest.json (the chain to the root with who, when, op and hash; the stored and current sha256; `integrity_ok`). `format=csv` returns the file alone, with an `X-Ensemble-SHA256` header. Audited as `dataset_exported`. UI: **Export with lineage (.zip)** and **CSV only** on the selected version. |
| 6 | Set default / roll back | **have** | `POST /api/datasets/{id}/default {version_id, by, reason}`. The pointer moves and the files are untouched (verified by hash). Moving to an older version is audited as `dataset_rolled_back`, otherwise as `dataset_default_set`. Refused for an archived or deleted version, or for the version that is already the default. UI: **Make vN the default** or **Roll back to vN**. |
| 7 | Card, rename, tags | **have** | `PATCH /api/datasets/{id}/card` sets name (it renames every version), description, owner, source, label definition and known issues, within length limits. Audited with before → after. Tags and notes use the existing annotations, now audited for datasets (`dataset_tagged` / `dataset_untagged` / `dataset_note_added`). UI: the Dataset card and the tags-and-notes strip. |
| 8 | Archive / delete | **have** | `POST …/archive {archived}`, `POST …/delete {by, reason}`, `POST /api/dataset-requests/{id}/decide`. Both are refused (409, with the list of blockers) while any of these comes from the version: a registered version that isn't rejected (or failed), an approved or pending champion, or a queued or running run. Archiving or deleting the default version is refused too. Archive hides the version from `GET /api/datasets`, Build and every picker; `?all=true` shows it. Delete needs four eyes (identity_key, as for champions) or an admin from `ENSEMBLE_ADMINS`, and the guard is checked again at approval. On delete the files are removed. **Kept:** the datasets row (`state = deleted`), lineage.json, TOMBSTONE.json (hash, rows, who asked, who approved, why), every edge, and the audit. A deleted dataset answers 410 on its routes, on training and on diff. Compare between runs trained on it still works from the stored predictions. |
| 9 | Integrity | **have** | `POST …/verify {by, all?}` re-hashes the file against the stored sha256. Results are `ok`, `MISMATCH` or `file missing`. `verified_at` and `verified_ok` are stored, and the check is audited (`dataset_verified` / `dataset_integrity_failed`). Export reports a mismatch too. UI: a ✓ or ✕ mismatch after each hash, and **Check every hash**. |
| 10 | Contract checks | **have** | These warn, never block, and are stored in lineage.json: target missing, type changed, a new value in the target, rows dropped by more than 20 %, a column removed, the target's missing share up by more than 5 points, and the same file as an existing version (`identical_file`). |
| 11 | Search / filter | **have** | `GET /api/data/search?q&project&tag&owner&op&since&until&state`. One row per dataset. Free text covers the name, file, ids, description, owner, source and tags. The tag filter is a substring match and case-insensitive. state is active, archived, deleted or all. UI: the filters above the Data list. |
| 12 | Audit | **have** | Every dataset action writes `audit_log` with `details.dataset_id` and `details.root_id`: `dataset_created` and `dataset_version_created` (every version, whichever path made it: Doctor, Feature Lab, relabel, label queue, drift window, SQL refresh, upload), default set / rolled back, card edits, tags and notes, archive and unarchive, delete requested / rejected / deleted, verify and integrity failures, export. UI: the dataset's Audit card. |
| 13 | Lineage UI | **have** | The Data page embeds the project tree for the dataset. The tree marks archived and deleted versions, and its root has a **Data →** link, from Models → Lineage and from the Registry. |
| + | Who created a CSV upload | **have** | `POST /api/datasets` takes `by`, and the client sends the acting name. |
| + | Version numbers | **have** | v1…vN by creation order within the dataset. |
| + | Hidden or deleted datasets in pickers and training | **have** | Archived and deleted datasets are hidden from the pickers. Training on a deleted one gets 410. |

## Manual checks (scratch data dir; files cut from the credit-default sample)

**Setup.**
- **v1:** the first 3,000 rows.
- **v2:**
  - 200 rows removed, 150 rows changed (`income_usd` ×1.25, 50 of them with `credit_score` −40) and 300 new rows (sample rows 3,000–3,299);
  - a new column `bureau_flag`, and `delinquencies_2y` changed from a number to text ("none" / "1+");
  - shuffled into another order.

1. **Upload v1** (`by=alice`): `ds_61cafce3`, audited `dataset_created` with its sha256.
2. **Upload v2** as the next version (`by=bob`, no key given):
   - The key was guessed as `applicant_id`. Result: matched 2,800, changed 144, added 300, removed 200. Of the 150 rows edited, 6 had a missing income and only that edit, so they did not change.
   - Warning: `type_changed delinquencies_2y`.
   - Holdout: all 2,800 matched rows kept their v1 membership. The v2 holdout has 623 rows: 566 of v1's 600 (34 were removed) plus 57 of the 300 new rows.
3. **Diff v1 → v2:**
   - **Schema:** +`bureau_flag`; `delinquencies_2y` number → text.
   - **Rows:** 144 changed, +300, −200 (by v2's row map). Example: C-501654 `income_usd` 45,900 → 57,400.
   - **Columns:** PSI for each column (all stable, ≤ 0.001). `delinquencies_2y` shows no PSI ("the type changed"). `income_usd` mean 56,457 → 57,238.
4. **Paired compare across versions:**
   - Runs: `run_79a416e4` on v1 and `run_254f2660` on v2, 45 s each, medium_quality, no bagging or stacking.
   - `POST /api/compare` → **paired**, "overlap only (566 of 600 rows)": accuracy 0.9435 → 0.9470, Δ +0.0035, 95 % CI [−0.0053, +0.0124], "no difference beyond noise".
   - A run on the hash-matched version (`run_dbe26062`) was also paired with v1, on 540 rows.
5. **Reverse lineage:**
   - Registered `credit-dl1 v1` (v2's run) and `credit-dl1-base v1` (v1's run). bob registered and carol approved.
   - alice requested `credit-dl1 v1` as champion and carol approved.
   - `GET …/usage` on v1 lists 5 datasets (v1 and its descendants), both runs, both models, the champion, and v1's own blocker. On v2 it lists its run, model and champion.
6. **Default and rollback:**
   - Set v2 → `rollback: true`, because the previous default was the implicit latest, v5.
   - v2 again → 409. Back to v1 → `dataset_rolled_back`. Forward to v2 → `dataset_default_set`.
   - A version of another dataset → 400.
   - The v1 file hash was unchanged throughout.
7. **Download:**
   - The zip holds data.csv, lineage.json (parent, root, root_rows, parent_rows, row_key, checks) and manifest.json (a chain of 2, `integrity_ok: true`). `sha256sum data.csv` equals the stored hash.
   - CSV: `X-Ensemble-SHA256` and the filename header.
   - With no `by` → 400.
8. **Archive and delete:**
   - Archive or delete v2 while it is the champion's source → **409**, with the model and the champion listed. Delete of the whole dataset → 409 with 4 blockers, including a running run.
   - The champion was moved to the v1 model. v2 is still refused, now only because `credit-dl1 v1 (approved)` comes from it. That is by design: see the recommendations.
   - **Four-eyes path** on v5 (the hash-matched copy):
     1. With a pending model `credit-dl1-hash v1` → 409.
     2. carol rejects the model.
     3. alice asks to delete.
     4. " A.LICE " approves → **403**. A second request while one is pending → 409.
     5. carol approves → deleted. Only `lineage.json` and `TOMBSTONE.json` stay on disk (v, sha256, asked by, approved by, reason). The datasets row is `state=deleted`. Both edges are kept (derived_from, trained_on).
     6. GET and training on it → **410**; diff → 410. Compare of its run with v1's still works (paired, 540 rows).
   - **Admin path:** admin `Dana` deleted v4 at once (`admin: deleted at once`).
   - **Archive:** v3 (no use) archived, and it is gone from `GET /api/datasets`. A whole dataset (`scratch500`) archived: hidden from Build, listed under `state=archived`, then unarchived.
9. **Integrity:**
   - Verify all → 5 × ok.
   - Edited two cells of v4's data.csv on disk → `MISMATCH` (stored 58e980a8…, now 1e0ada79…), audited `dataset_integrity_failed`. The export manifest said `integrity_ok: false`.
   - Restored the file → ok.
10. **Schema warnings:**
    - v3 (target dropped, 1,700 of 3,100 rows): `target_missing`, `rows_dropped` (45 %), `column_removed`.
    - v4 (30 rows of "Written off", `income_usd` as "57400 USD"): `type_changed income_usd`, `new_target_category` "Written off". It is a 30-row change, because the retyped column is not compared.
    - The v2 file uploaded again: `identical_file`.
    - Bad keys: `row_key=nope` → 400, "not a column". `row_key=region` → 400, "not unique (3,094 repeated values, some missing)".
    - Hash matching of v2 against v1: 2,656 matched, +444, −344. A changed row counts as removed plus added, and the response says so.
11. **Search:**
    - `q=credit app`, `tag=RETAIL-credit`, `tag=retail` (substring), `owner=alice` (the card owner), `op=upload_version`, `project=prj_61cafce3` and `q=LOS extract` (the card source) each return the dataset.
    - `until=2026-01-01` returns none. `state=deleted` returns the two deleted scratch datasets. `state=bogus` → 400.

**Other checks:**
- **An in-app version is audited:** a Data Doctor `drop_columns` on v2 (by frank) became v7, `dataset_version_created`.
- **UI walk (Playwright):**
  - Upload a new version through the page: the warnings and the result line, then the diff opened on vN−1 → vN.
  - Ask to delete as alice, then approve as alice → the four-eyes message. Approve as bob → deleted, with the tombstone line.
  - Refused archive → the message lists the blockers.
  - No console errors other than the expected 4xx.

## Screenshots (`docs/progress/DL1/`, 1366 × 900)

- `01-data-list-and-dataset`: Models → Data, the dataset list and the dataset page head, with **Upload a new version…**.
- `02-versions`: v1–v7 with op, who, when, changes, warnings, hash ✓, used by, champion; v3 archived, v4 and v5 deleted; the selected version's actions.
- `03-diff-v1-v2`, `03b-diff-card`: the diff (rows, schema chips, the warning, changed cells, per-column missing, mean, median / top value and PSI).
- `04-used-by-and-card`: reverse lineage for v2, and the dataset card.
- `05-archive-refused-in-use`: the 409 with its blocker.
- `06-delete-and-audit`: the lineage tree (archived and deleted marked), archive or delete, and the audit.
- `07-lineage-in-data`: the tree inside the Data page.
- `08-build-version-strip`: Build's picker (one entry per dataset) and the versions strip (v1, v2 ★, v6, v7; archived v3 hidden).
- `09-lineage-tree-data-link`: Models → Lineage with **Data →** on the root.
- `10-search-by-tag`: the filters, by tag.
- `11-upload-new-version-ui`: a new version uploaded in the UI, with its contract warning and the matched / changed / gone line.
- `12-delete-four-eyes-refused`: the requester trying to approve their own delete.
- `13-deleted-tombstone`: after a second person approved: both versions deleted, the tombstone line, upload disabled.

The scratch predictors (4 runs, 3 registered versions) and the scratch data dir were deleted after the checks.

## Recommendations (not built: not basic, or a policy call)

1. **A retire path for approved models.** An approved registered version can't become rejected, so its training data can never be deleted, even when Archived and not champion. That is safe for model risk, but there should be a policy: data may be deleted once the model is Archived for longer than the retention period, with four eyes. This is the same question as P1's registered-folder retention.
2. **Parquet or a column store for root_rows.** `lineage.json` holds `root_rows` and `parent_rows` as JSON lists, 35 KB at 3k rows. They should go in `.npy` beside data.csv before tables reach millions of rows (platform-eval step 1 already suggests this).
3. **Row-level change history (CDF).** The diff gives counts and five example rows. A full per-row change file (Delta's change data feed) would let an auditor list every changed cell. It is cheap to add to export when someone asks.
4. **Contract rules per dataset.** Thresholds are global (20 % row drop, 5-point target missing). A per-dataset contract (expected columns, types, allowed target values, row bounds), saved on the card, would turn the warnings into a checked contract. Blocking stays a separate decision.
5. **OpenLineage export.** Emit `dataset_version_created` and `trained_on` as OpenLineage events, so bank catalogues (Collibra, DataHub) see the versions (platform-eval: optional).
6. **The MLflow mirror (P1 step 6)** should carry our sha256 as `log_input` tags, so MLflow's 10k-row digest is never mistaken for the integrity hash.
7. **Sign-in.** Owner, "acting as", four eyes and admins are names, not identities, as everywhere else in the app.
8. **Scheduled verify.** Re-hash every active version nightly and alert on a mismatch. Today it runs only on demand and on export.

## Known limits

- The default pointer is per dataset (root), not per project. A project holding several uploaded roots has one default for each root.
- A version uploaded on top of a version whose own new rows were matched gets the parent's negative root id for those rows. Compare never pairs negative ids, so it is safe, but those rows show as unpaired across versions.
- `rollback: true` is reported when moving from the implicit "latest" to any older version, including the first time a default is set.
- In the Build picker, an archived dataset that is still open in the URL shows its versions strip, but it is not in the picker list.
- Pre-existing, not changed: a registered model may take a version of its own dataset, but not that dataset's parent. Registering the v1 run under the v2 model name was refused, so the checks used a second model name.
