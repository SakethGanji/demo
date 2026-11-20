# F-P2: fixes for the PA1 platform findings and the A2 governance items (M2, m6)

Every check below was a manual API call or UI step, chained the way the UI makes them. No smoke or integration suites were run.

**Data:**
- A scratch data dir with `credit3k`: the first 3,000 rows of credit-default, `ds_968ef036`.
- A Data Doctor version, a clean_labels version, the SQL demo load plus 2 refreshes, and a second upload.
- 13 small runs: 15–60 s, no bagging, no stacking.

**Timings:** measured on a copy of that database with 3,000 synthetic runs, like PA1:
- 300 parents × 9 branches;
- result JSON of about 81 KB each (PA1's was 61 KB);
- one third of the branches tagged with a review id.

"Before" is the same database served by the code at `26964a9`, from a git worktree.

**Builds:** `npm run build` passes (tsc + vite).

**Commits:** `faa8886`, `4dd4f07`, `f17fadd`, `b46ea96`, `7fe112b`, and this report. F-A2's work was merged in `09edcaa` (merge, no rebase).

## Decisions

- **Four eyes compares letters and digits only.** It is `catalog/governance.identity_key`, and the registry's `identity_key` calls it.
  - The steps: NFKD, then lookalike Cyrillic and Greek letters mapped to Latin, then casefold, then every character that is not a letter or digit dropped (punctuation, spaces, zero-width characters, combining accents).
  - "a.l.i.c.e", "al-ice", "al ice", "Alicé", "Аlice" (Cyrillic А), "Ali​ce" (zero-width), "ＡＬＩＣＥ" and "Αlice" (Greek Α) are all `alice`.
  - A name with no letter or digit ("...") gets 400.
  - Two different people whose names fold together must use other names. This can only make four eyes stricter.
  - The UI's `nameKey` mirrors it.
- **Self-promotion to Production is blocked by default.** Whoever registered a version cannot move it to Production themselves, even after someone else approved it. This gives 409.
  - `ENSEMBLE_ALLOW_SELF_PROMOTE=1` allows it.
  - Every Production audit row records `self_promotion` and `registered_by`.
- **The experiment author is still excluded.** The person in `change_by` cannot review the version, and now cannot decide its champion request either.
- **Leak gate.** Production, a champion request and a champion approval all get 409 while `vela.doctor.open_critical(run)` (F-A2) is non-empty.
  - `{"override_reason": "…"}` (at least 10 characters) lets the action proceed. The audit row stores the open questions and the reason.
  - If the check is missing in a build, the audit says `leak_check: "not available in this build"`, never "clean".
  - If the check raises an error, that counts as a block.
- **Admins (A2 m6).** `ENSEMBLE_ADMINS` now has no default, so typing "admin" no longer works.
  - Names are compared with `identity_key`.
  - The 403 message and `GET /api/retention/plan` (`admins.note`) both say "names, not authenticated identities".

## Before → after

| # | Finding | Before (PA1 / 26964a9) | After |
|---|---|---|---|
| 1 | Four eyes | "a.l.i.c.e" approved alice's champion (200). Registry review by "A.lice" / "Аlice" → 200 | All 10 spoof variants: **403** on champion decide, **409** on registry review. "..." → 400. Registrant → Production: 409. Leak open → 409; with a reason → 200, audited |
| 2 | Archived champion | Request 201; archived after request, decide → 200 | Request **409** "is Archived". Decide re-checks → **409**. Rejecting is still allowed |
| 3 | /oof on an evicted run | 500 | **410**. Removed from STORED_ONLY |
| 4 | Top 3 ignores label and type | Per (project, dataset, metric), ranked on accuracy | Per (project, dataset, label, problem type). Binary is ranked on AUC (or AP), the rest on the run's metric. The multiclass `home_ownership` run is now "#1 … home_ownership (multiclass)" |
| 5 | `keep` case and versions | Only exact `keep`; on a version, nothing | Any case. Tags dedupe case-insensitively. `Keep` on a version pins its source run ("source of a version tagged 'keep'") |
| 6 | Performance | See the timings below | See the timings below |
| 7 | Phantom projects | One per SQL refresh / monitoring window | 2 refreshes add **0** projects. The backfill removes old empty system projects (1 removed in the check). Moving a tree away drops the empty default project |
| 8 | Projects can't be created | No POST; switcher hides 0-run projects | `POST /api/projects`. `POST /api/datasets` takes `project_id`. `POST /api/projects/{id}/datasets` moves a root with its versions and runs (a version gets 409). The switcher lists every project. UI: "New project…" and "Move a dataset into this project…". A project's tree covers all its roots |
| 9 | Compare B | "abc" or 1e400 → 500; 1, −5 or 1e6 accepted | Anything except a whole number 200–10,000 → **400** (abc, inf, 1, −5, 1e6, 199, 10001, 200.5, true). At most 2 compares run at once. B × rows ≤ 20 M (B is lowered and the response says so). B = 10,000 on 596 rows takes 161 ms |
| 10 | Evicted run page | Looked live; actions led to 410s | "Evicted" banner. Explain tab hidden, Register… disabled. "Open the config in the Gym" and "Re-fit from config" (POST /runs with the stored body, `parent_run_id`, `source: refit`). `predictor_state` is on GET /runs/{id} |
| 11 | Tag with "/" | Delete → 404 | `tags/{tag:path}`: delete → 200 |
| 12 | /children on an evicted run | 410 | **200** (stored-only) |
| 13 | Δ across different targets | −0.5067 | `difference: null`, note "not comparable: different targets …" |
| 14 | Refresh reason | "different original datasets" | "b has no root-row mapping (refresh)" |
| 15 | derived_from meta | n_changes 0; clean_labels got op data_doctor | Data Doctor fix: `n_changes: 2`. Relabel: op **clean_labels**, n_changes 1. One line in `data_doctor_api.py` passes op and changes through `derive()`. The backfill fills n_changes and op from lineage.json |
| 16 | Superseded request | "rejected", decided_by = the new requester, no audit | **withdrawn**, decided_by empty, plus a `champion_withdrawn` audit row naming the newer request. The champions table was rebuilt once for the new status |
| 17 | run_ids | A string was used as a substring; dict or [null] → 200 | String, dict, [null] or [] → **400**. Unknown ids → **404** with the list |
| 18 | Project fields, notes | name 123 and a 5,000-character description accepted; notes final; deleting a missing tag → 200 | 400 on type and length (name 1–120, description ≤ 2,000). PATCH and DELETE on notes keep the old row and are audited (`note_edited` / `note_deleted`). Deleting a missing tag or note → **404** |
| 19 | predictor_state | Queued or cancelled runs showed "present"; a failed run with no predictor showed "evicted" | "none" until `finish()` finds a predictor folder. The backfill: "evicted" only with a tombstone, otherwise "none" (checked: failed and cancelled rows → none; a real eviction stays evicted) |
| 20 | Pickers after a slim | Every model offered | A slim marks the deleted leaderboard rows `deleted`, `can_infer: false` (LinearModel deleted; Scorecard and WeightedEnsemble kept). `models_kept` is stored. Results and CustomEnsemble pickers skip them. An evict marks every row |
| 21 | UI minors | — | Gym, Vela and twin dots coloured (the class name was built from "Gym" → `src-ym`). Compare's base is the first run clicked (selection order). Champion shown by name and version (tree head, champion card). The run page's notes strip has its own "acting as" box. Backfilled roots get op `upload` / `sample` |
| 22 | Vela patch card | "same fixed holdout rows" | "each run's own holdout", plus "not the same rows (another split or dataset), see Δ Against below" when the split or dataset differs |
| — | Unclear: partial overlap | Not said | `partial` and "overlap only (596 of 600 rows)" in the API and the card. The rows column reads "596 of 600" |
| — | Unclear: paired vs unpaired metric | Paired used AUC, unpaired eval_metric | Both use the run's eval metric (numpy scorers for the common AutoGluon metrics; a fallback is reported in `metric_note`). AUC is shown alongside for binary, without a CI |

## Timings (3,006 runs, about 81 KB results; best of 3)

**API**

| Call | Before | After |
|---|---|---|
| GET /api/lineage/project/{p} | 3,193 ms | **185 ms** |
| Models page: GET /api/runs?project_id (full list) → `&limit=100` page | 6,759 ms · 40 MB | **155 ms** · 1.3 MB |
| Models poll every 5 s: full list → light, queued and running only | 6,759 ms · 40 MB | **7 ms** · 0 MB |
| Registry: unscoped GET /api/runs → none (Register… reads `status=succeeded&limit=100`) | 7,481 ms · 40 MB | 0 on load; **270 ms** · 1.3 MB on Register… |
| GET /api/retention/plan | 218 ms | 247 ms (top-3 now reads AUC through json_extract) |

**UI** (Playwright at 1366 × 900, cold load)

| Step | Before | After |
|---|---|---|
| Models ?project= to the first row | 36.6 s | **1.2 s** |
| /api/runs bytes polled in the next 12 s | 40 MB every 5 s (PA1) | **0.00 MB** |
| Models → Lineage tab | 33.8 s | **1.1 s** |
| Registry (version selected) to the tree | not rendered after 120 s | **1.6 s** |

**How the lineage time came down:**
- The tree no longer calls `runs.get` or globs `reviews/` per edge.
- review_id and verdict sit on the edge. They are written at creation, filled by the backfill once, and stored back once a review ends.
- Each review's journal is read once.
- `run_dir` comes from `json_extract`.
- A fallback for branches written before their journal checks one directory per parent, not per edge.

## Screenshots (`docs/progress/F-P2/`, 1366 wide)

- `01-models-projects`: the switcher lists every project, including the 0-run ones and "Retail credit". The New project… form is open.
- `02-evicted-run-banner`: an evicted run, with the banner, Re-fit from config, the Explain tab gone, Register… disabled and the run's own "acting as" box.
- `03-run-notes-acting-as`: tags and notes, with edit and delete.
- `04-compare-overlap-auc`: paired, "overlap only (596 of 600 rows)", accuracy with "AUC 0.7268 · 0.7226 (−0.0042, no CI)", rows "596 of 600".
- `05-registry-champion-four-eyes`: the champion card, by name, with the four-eyes and leak-gate wording.
- `06-registry-review-wording`: Review & Stage with "names, not authenticated identities" and the self-promotion rule.
- `07-lineage-3k-gym-dots`: the 3,006-run project tree. Gym and patch dots are coloured; the champion is "credit3k-default v2".
- `08-lineage-scratch-project`: the scratch project tree.

No console errors except the expected 410s.

## Remaining risks

- **Names, not identities.** Four eyes and admins compare typed names, so anyone can type another person's name. identity_key only stops the easy spoofs.
- **Lookalikes.** The confusables table covers Cyrillic and Greek letters and a few Latin ones, plus 0→o and 1→l. It is not the full Unicode confusables list: Armenian or Cherokee lookalikes still pass.
- **A self-promotion edge case.** A version's registrant can still request it as champion. A different person must approve, so four eyes holds.
- **The leak gate depends on F-A2's `open_critical`.** It reads the Doctor's last stored diagnosis, so a run whose Doctor never ran reads as clean. A check that raises an error blocks.
- **The full Models table still reads the list shape per run** (about 13 KB each). Paging keeps that to 100 runs per page. The filters, counts and parallel-coordinates chart cover the loaded pages only, which the header says ("newest 100 loaded").
- **Home and Vela still call the unscoped `api.runs.list`.** They were outside this list of findings and are the next to move.
- **Retention plan** is about 250 ms at 3k runs because of json_extract for AUC. Fine for a dry run, but not a hot path.
- **A worker died on the scratch box.** Starting a second API on a copied database reaped a worker of the first one (run `scratch2` failed). That is an artefact of running two APIs on one machine, not of these changes. It is worth knowing for anyone repeating the timing setup.
