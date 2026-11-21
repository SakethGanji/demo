# DL2 · Fixes for DLA1 (dataset governance), motion-components @ 169109a → this commit

**Goal.** Close DLA1's findings on DL1's dataset management and lineage. DLA1 put governance confidence at 55 %. Its findings:
- B1: a run delete removes the guard on its data.
- B2: a careless upload becomes the default.
- B3: training on a MISMATCH version is accepted.
- The retire dead end.
- The missing basics.
- B4–B8 and search.

**Verification** (user rule: no smoke or integration suites):
- Every repro below was a manual API call, chained the way the UI calls them, on a scratch data dir: `ENSEMBLE_ADMINS=Dana`, a fresh `ENSEMBLE_DATA_DIR`.
- The data was small self-generated CSVs:
  - loans: 1,500 rows with `loan_ref`.
  - v2: −150 rows, 100 incomes × 1.1, +200 rows, a new `bureau` column, shuffled.
  - a 3-row, 2-column file.
  - an unrelated 300-row table.
- Runs were 20 s, `medium_quality`, no bagging or stacking.
- The UI flows were walked in Playwright at 1366 wide (`scripts/dl2_shots.cjs`).
- `npm run build` and `tsc --noEmit` pass.
- `ruff --select F` is clean on every touched file except one F401 in `registry/api.py` (`unicodedata`) that was already in HEAD.
- The scratch dir, with its 5 runs and 3 registered versions, was deleted afterwards.

## Before → after (DLA1's repros, re-run)

| # | Repro | Before (DLA1) | After (DL2) |
|---|---|---|---|
| B1 | Train on v2, register, approve (carol), champion (alice → carol); archive v2 | 409 | **409**. Each blocker says how to clear it: "approve another champion … (POST /api/projects/{pid}/champion), then retire this version" |
| B1 | `DELETE /api/runs/<run>` with no `by` | 204 | **400**: `by` is required |
| B1 | `DELETE /api/runs/<run>?by=bob` while it is the source of an approved model and the champion | 204, no audit row | **409**: "loans-dl2 v1 (approved) was built from it: reject it or retire it … (POST /api/registry/versions/…/retire)", plus the champion. Audited as `run_delete_refused` |
| B1 | `GET usage` after the run row is gone | runs [], versions [], champions [] | After a legitimate delete (the model was retired), usage still lists the run as `status: deleted`, the model as `retired: true`, and the delete blocker (retention). It is driven by `lineage_edges` with LEFT JOINs. The run delete is audited as `run_deleted` (who, the reason, its registered versions) |
| B2 | Upload a 3-row file (rows_dropped 99.9 %, column_removed × 5) | became "default v8 (latest)" at once | `preview=true` → 201, nothing written (versions 2 → 2), `would_be_default: false`. Create → v3 is created, `upload.default = {is_default: false, why: "6 contract warning(s): …", how_to_promote: "… POST /api/datasets/{root}/default …"}`. The default stays v2 |
| B2 | A clean upload (no warnings) | default as "latest" | becomes the default explicitly (`dataset_default_set`, `auto: true`). `make_default=no` keeps it off. In the UI: "Create vN" / "Create vN and make it the default" (the second only with no warnings) |
| B3 | Edit 3 cells of v1's data.csv, verify → MISMATCH, then `POST /api/runs` on v1 | 201 | **409**: "…last integrity check … was a MISMATCH … Restore the file and verify again, or an admin sends integrity_override_reason …". As bob with a reason → **403** (not an admin). As Dana with a reason → **201**, audited `training_integrity_override`, recorded on the run (`config.data_gate`). File restored → verify ok |
| B3 | Verify all | per dataset only | `POST /api/data/verify-all {by}`: bob → 403 (audited); Dana → 15 datasets, 17 versions, 0 failed. `ENSEMBLE_VERIFY_EVERY_HOURS` re-hashes on a timer. It has no default (off) |
| Retire | Archive v2, whose model is approved, not champion, stage None | 409 "Reject or retire these first" (neither was possible) | 409: "retire it (Registry → the version → Retire…, POST /api/registry/versions/…/retire; four eyes), or move it to Archived" |
| Retire | Retire the (requested) champion's version | (no retire) | **409**: "…is the requested champion … approve another champion first" |
| Retire | Move the current champion's version to Archived | 200: the champion stayed approved | **409**: "Version 1 is the champion of project … approve another champion first" |
| Retire | alice asks to retire loans-dl2 v1; "A.LICE" approves; carol approves | — | 201 pending; **403** four eyes; **200** retired (stage → Archived, `retired_at/by`, audit `retired`). A later move to Staging → **409** "was retired … stays Archived" |
| Retire | Archive v2 after the retirement | 409 | 409 only because v2 was the default ("make another version the default first … POST /api/datasets/{root}/default"). After moving the default: **200** |
| Retire | Delete v2 within the retention period (default 2,557 days) | 409 (dead end) | **409**: "…retired on 2026-11-21 by carol; data behind a retired model may be deleted 2557 days after retirement … ENSEMBLE_RETIRED_DATA_RETENTION_DAYS (retention policy: needs model-risk approval); archive hides it now" |
| Retire | With retention 0 (test only): bob asks with `freeze_holdout`, bob approves, carol approves | — | **403** four eyes, then deleted. On disk: `TOMBSTONE.json` (sha256 kept), `lineage.json`, `holdout_sample.csv` (300 holdout rows plus their row positions; the sha is in the tombstone) |
| 5a | Delete v1 alone (PII purge) while v2 and v3 stay | impossible (the root id meant the whole dataset) | `{only_version: true}`: bob asks, carol approves. v1 is deleted (TOMBSTONE.json only), the dataset stays **active**, and the default is untouched. v2's lineage still holds 1,550 `root_rows`. Diff v2 → v3 and an upload on top of v2 work. The tree shows the root as a deleted node (depth 0) |
| 5a | Archive v1 alone | impossible | `{only_version: true}`, or "Archive v1" on the version row |
| 5b | A reason / change ticket | none | Upload form field `reason`. Every derived version takes `reason=`, or the `X-Ensemble-Reason` header. It is stored in lineage.json and in the `dataset_version_created` audit row. History shows it: "CHG-1042 monthly LOS extract" |
| 5c | Row lookups, previews, exports | not logged | `GET …/rows` and a diff with cell-level examples are audited as `dataset_read` (who, what, which version), at most once per person, version and kind every 10 min. Three lookups by erin → **1** row. Exports were already audited on every call (`dataset_exported`) |
| 5d | Anomaly-review and shift-weights versions | audited as "system" | `by=body.by` (anomaly) and `?by=` (shift weights; the client sends it). Every path that passes no name falls back to the UI's acting name (`X-Ensemble-Actor` header, set by the client on every request). *Checked by reading the code; not run live (it needs an anomaly or shift run)* |
| 5e | Refused governance actions | not audited | `<action>_refused` with the status and the message. Seen: `dataset_archive_refused`, `dataset_delete_refused`, `dataset_delete_decide_refused`, `run_delete_refused`, `training_refused`, `dataset_verify_all_refused`, `retire_refused`, `retire_decide_refused`, `stage_change_refused` (also on review 403/409) |
| B4 | Diff of unrelated datasets (no shared same-type columns) | "matched 2,600 over 0 shared columns" | matched 0, added 1,500, removed 300: "no rows matched: the two versions share no column of the same type" |
| B5 | Two siblings uploaded from the same file | +400 / −400 | matched 1,550, 0 / 0 / 0: "identical files (the same sha256)" |
| B5 | Cousins whose new rows got different root ids (one income edited) | new rows unpaired | matched 1,550, changed 1: "the same original rows …; 200 more rows identical by exact hash" |
| B6 | The diff card after a change of A/B | the old numbers under the new heading; kept on error | The card remounts per pair (`key`): "Diffing vA → vB…" while loading, and an error shows alone |
| B7 | Archive v3 alone, archive the dataset, unarchive the dataset | v3 unarchived too | v3 stays archived. Only what the dataset archive hid (`state_scope = 'dataset'`) comes back |
| B8 | The default diff | the last two active versions | parent → child of the default (screenshot 05) |
| B8 | ✓ on a deleted row | shown | gone (screenshot 04, v2) |
| B8 | The requester withdraws their own delete request | 403 | 200 `withdrawn`, audited `dataset_delete_withdrawn`. The UI shows "Withdraw my request" to the requester. Approving their own request is still 403 |
| B8 | "Last change" at 1366 | cut off | fits: the id moved under the name, a short date, `scrollWidth == clientWidth` on every table on the page, the audit card included (screenshot 13) |
| B8 | Training on an archived version | allowed, nothing said | still allowed. The 201 carries `notes: ["… is archived (hidden from pickers by bob …); training on it is allowed and recorded."]`, and Build's version strip says so (screenshot 12) |
| Search | `since=garbage` | 200, empty | **400** "since is a date (YYYY-MM-DD) or an ISO date-time" |
| Search | `state=deleted` | whole deleted datasets only | any version in that state. It finds both test datasets (the deleted v2, the purged v1), with `versions_in_state`. The dataset's own state comes from its versions (deleted only when all are) |

### Other delete paths checked for the same hole

- **Retention evict** (`/api/retention/run`): it removes predictor folders only. The run row and edges stay, and runs of non-rejected versions are pinned.
- **Annotations delete:** soft (`deleted_at`).
- **`drop_if_phantom`:** only removes a system-made project that holds nothing.
- **Admin reset:** wipes everything by design.
- **Registry:** has no version delete. Champion requests are withdrawn, never deleted.

Run delete was the only hard delete that could orphan a guard.

## What changed

**Backend**
- `catalog/dsets.py`:
  - `usage()` is driven by edges with LEFT JOINs.
  - `blockers(ids, "archive"|"delete")` implements the new rules: Archived, retired or non-champion models allow archive; delete needs retirement plus the retention period. Every line names a real path.
  - `dataset_state()`.
  - Archive and delete take `only_version` and `state_scope`.
  - Delete takes `freeze_holdout`, and the requester can withdraw.
  - `settle_default()`, `verify_all()`, `integrity_gate()`.
  - Diff by identical sha.
  - Search by the state of any version.
- `catalog/datasets_api.py`:
  - Upload takes `preview`, `reason` and `make_default`.
  - Refusals are audited.
  - `POST /api/data/verify-all`.
  - `since`/`until` are validated.
  - Usage returns `blocks_archive` and `blocks_archive_or_delete`.
- `catalog/governance.py`: the acting-name and reason context (headers), `refused()`, and `read_audit()` with dedupe.
- `catalog/schema.py`:
  - `datasets.state_scope`.
  - `model_versions.retired_at` / `retired_by`.
  - `dataset_requests.options`.
  - A `version_requests` table (four-eyes retire).
- `registry/api.py`:
  - Retire (`POST …/retire`, `POST /api/registry/version-requests/{id}/decide`, `GET …/retire`).
  - No archiving of the champion.
  - A retired version stays Archived.
  - Review and stage refusals are audited.
- `engine/api.py`: run delete (`by`, guard, audit); the integrity gate and the archived note on run create.
- `dataset_versions.py`: B4 and B5.
- `datasets.py`: preview, and `reason` on every derived version.
- `main.py`: the header middleware, the read audit on row lookups, the optional verify timer.
- `config.py`:
  - `ENSEMBLE_RETIRED_DATA_RETENTION_DAYS` (default 2,557 days; **needs model-risk approval**).
  - `ENSEMBLE_VERIFY_EVERY_HOURS` (no default).
- `anomaly/api.py` and `engine/shift_api.py` pass `by`.

**UI** (no new nav items)
- Data: upload goes through a preview card, with a reason field.
- The versions table shows reasons and no ✓ on deleted rows.
- "Archive v1".
- Usage marks retired models and separates archive blockers from delete blockers.
- The archive/delete card offers "v1 only (later versions stay)", "keep a holdout sample" and "Withdraw my request".
- The list fits at 1366.
- Registry → Review & Stage gets a retire box: ask (the comment is the reason), withdraw, approve or reject as someone else, and a "retired" pill.
- Build's version strip notes an archived version.
- The client sends `X-Ensemble-Actor` on every request.

## Screenshots (`docs/progress/DL2/`, 1366 × 900)

- `01-upload-preview-warnings`: the 3-row file previewed, with 6 contract warnings. Only "Create v4" is offered.
- `02-upload-preview-clean`: a clean file, matched 1,350 / changed 100 / +200 / −150, + bureau, and a change ticket. Both buttons are offered.
- `03-created-and-default`: the result line ("It is now the default").
- `04-versions`: reasons, the deleted v2 without ✓, the default.
- `05-default-diff-parent-to-child`: the diff opened as parent → child of the default.
- `06-used-by-with-retired`: v1's usage, with the champion, a retired model, deleted runs, and the blockers with their paths.
- `07-remove-v1-only-and-withdraw`: "v1 only (later versions stay)", "keep a holdout sample", and the requester's "Withdraw my request".
- `08-retire-champion-refused`: Retire… on the champion's version is refused, with the path.
- `09-retire-asked-by-alice`, `10-retire-approve-as-carol`, `11-retired`: the four-eyes retire flow.
- `12-build-archived-note`: Build on an archived version.
- `13-data-list-1366`: the list, with "Last change" fitting.
- `14-search-deleted-versions`: show = deleted.
- `15-audit-refusals-and-reads`: refusals and reads in the dataset's audit.

Notes on the screenshots:
- `01` and `02` were taken before the list-width fix, so their background list still cuts "Last change". `13` is after the fix.
- The retention days in `07` read "0", because the scratch server ran with `ENSEMBLE_RETIRED_DATA_RETENTION_DAYS=0` to exercise the delete.

## Remaining limits

- **Names, not identities.** There is still no sign-in. The acting-name header, four eyes and the admin list are names, so anyone can type "Dana". This is DLA1's last nice-to-have and is unchanged.
- **The retention period is a placeholder.** It defaults to 7 years, labelled "needs model-risk approval". The clock runs from the model's retirement, not from the data's last use.
- **The holdout sample is a copy of rows.** It is opt-in and off by default, so a PII purge must leave it off. Nothing scrubs columns.
- **The integrity gate reads the last verify; it does not re-hash at train time.** A file edited after an ok verify trains until the next verify (on demand, verify-all, or the timer if set).
- **Run delete has no four eyes.** It needs `by` and is refused while a non-retired model or a champion comes from the run. Deleting the run of a retired model or of a rejected one is allowed and audited.
- **The read audit covers row lookups and diff examples (plus exports, which were already audited).** Other row-level views are not logged: Slice rows, the Data Doctor's examples, SQL preview, the holdout predictions download. They would need the same one-line `read_audit` call.
- **A default pinned by a warned upload stays pinned.** When no default was set, a warned upload pins the pointer to the version in use. In-app versions made later (Doctor, Feature Lab) then don't become the default implicitly as "latest" used to. Making one the default is one click.
- **Retired versions still pin their runs against retention.** Retention pins source runs of non-rejected versions, retired included. That is safe, but the folders stay until the retention policy itself is revised.
- **5d was not run live.** The anomaly-review and shift-weights `by` was checked by reading the code (it needs an anomaly or shift run).
