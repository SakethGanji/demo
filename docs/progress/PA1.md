# PA1: platform layer end-to-end check (independent, read-only), tested at 10df09f (P1 f6bd69e + A1 merge)

**Confidence that the platform layer works end to end:** about 65 %.
- Solid: the data model, edges, backfill and compare maths.
- Holes: governance (champion four-eyes, archived champions) and retention pins (label grouping, `keep` case).
- Scale holds on the API's light paths, but not for lineage or any UI page.

## Setup

"Old" data was seeded with the pre-P1 code on a scratch data dir:
- upload credit3k;
- a Doctor apply;
- runs: raw, fixed, a Gym child and an ablation;
- a registration;
- an SQL dataset, then a refresh.

P1 then ran on top of that dir, adding:
- Feature Lab and a clean_labels relabel;
- a version of a version, and another SQL refresh;
- a second upload;
- an offline review;
- 4 registered versions;
- 3,000 synthetic runs.

## Wrong, ranked

### 1. CRITICAL: champion four-eyes bypass
- identity_key maps inner punctuation to spaces.
- "a.l.i.c.e", "al-ice", "al ice", "Alicé" and Cyrillic "Аlice" all count as someone other than "alice". "a.l.i.c.e" approved alice's own champion request.
- **Repro:**
  1. POST /api/projects/{p}/champion `{"version_id": v, "by": "alice"}`
  2. POST /api/champions/{id}/decide `{"approve": true, "by": "a.l.i.c.e"}` → 200
- **Expected:** 403. Drop punctuation and fold accents and confusables.
- Registry review uses the same key (A2 M2 confirms the bypass there).

### 2. MAJOR: an Archived version can become champion
- Requesting an Archived version → 201.
- decide doesn't re-check: archive the version after the request, approve it → 200. The tree then shows "Archived · ★ champion".
- **Expected:** 409 on both request and decide unless the version is ready, approved and not archived.

### 3. MAJOR: GET /runs/{evicted}/oof → 500
- It is listed in STORED_ONLY, but calls load_predictor, which raises FileNotFoundError.
- **Expected:** 410. Remove it from STORED_ONLY.

### 4. MAJOR: retention's top 3 ignores label and problem type
- Ranking is per (project, dataset, metric) only.
- A multiclass home_ownership run competed on accuracy with the binary runs, was left unpinned, and was evicted. It was the only model for that target.
- The ranking metric is accuracy on a 5.5 % default rate, so it's noise.
- **Expected:**
  - group by label and problem type;
  - rank by the metric compare uses (AUC/AP for binary).

### 5. MAJOR: the `keep` tag pins only in exact lowercase
- "Keep" and "KEEP" are accepted and shown, but don't pin.
- A `keep` tag on a version does nothing.
- **Expected:** case-insensitive, or tags normalised to lowercase.

### 6. MAJOR: performance at 3k runs (300 parents × 9 branches, 61 KB results)
- **API:**
  - GET /api/lineage/project/{p}: 3.4–3.9 s (P1 claimed 151 ms). lineage.tree calls runs.get(parent) and globs reviews/ for every branched_from edge without review_id.
  - GET /api/runs?project_id: 5.5 s, 35 MB.
  - The light list (0.13–0.18 s), children (20 ms) and plan (0.2 s) are fine.
- **UI (1366 wide):**
  - Models ?project=: 26.7 s to the first row, then it re-polls the 35 MB list every 5 s.
  - The Lineage tab takes 13.6 s.
  - Registry fetches the unscoped /api/runs (35 MB) 3 times; the tree hadn't rendered after 120 s.

### 7. MAJOR: every SQL refresh creates an empty phantom project
- record_root_dataset runs before record_version, so each refresh leaves a project with 0 datasets.
- GET lineage on that project returns another project's tree.
- A nightly refresh adds one such project per night. The monitoring window likely does the same.

### 8. MAJOR (gap): projects can't be created
- There is no POST /api/projects; every upload becomes its own project.
- The switcher hides projects with n_runs = 0, so a new upload's project can't be selected.
- Isolation itself works.

### 9. MAJOR: compare's "B" isn't validated
- "abc" or 1e400 → 500.
- 1 → a "95 % CI" of [x, x].
- −5 → paired with CI [null, null].
- 1,000,000 → over 60 s, and it kept burning CPU after the client gave up. A training run alongside it failed for lack of time.
- **Expected:** 400 outside 200–10,000.

### 10. MAJOR (UX): no "evicted" banner on the run page
- Register…, Serve… and Explain all lead to 410.
- The stored ladder renders as if the run were live.

## Minors

11. A tag containing "/" can be added but not deleted (404).
12. GET /runs/{evicted}/children → 410. It should be stored-only.
13. An unpaired compare across different targets still shows a Δ (−0.5067). It should be null.
14. The SQL-refresh compare reason reads "different original datasets", though both share a root. It should say "b has no root-row mapping (refresh)".
15. derived_from n_changes is always 0 for the Data Doctor (data_doctor_api passes `changes=[]`). clean_labels versions get op "data_doctor".
16. A newer champion request marks the older pending one "rejected", with decided_by set to the new requester, and writes no audit row. It should be "withdrawn" plus an audit entry.
17. Retention run_ids aren't validated. A string is used as a substring match ("run_ids":"run_x" slimmed run_x). A dict, [null] or unknown ids → 200 doing nothing.
18. PATCH /projects accepts a name of 123 and a 5,000-character description. Notes can't be edited or deleted. DELETE of a missing tag → 200.
19. predictor_state is "present" for queued and cancelled runs. The backfill marks a failed old run with no predictor as "evicted".
20. After a slim, the leaderboard still lists every model, and the UI pickers may offer deleted ones (predict → 400 "No model").
21. UI:
    - Gym children have no colour dot.
    - Compare's base is the first run in table order, not the first one clicked.
    - The champion is shown by id, not name.
    - Tagging on the run page is disabled until "acting as" is set in Registry (a dead end).
22. (Pre-existing) The Vela patch card says "same fixed holdout rows" for a group-split twin, though /api/compare finds 122 shared rows of 600.

## Unclear

- A paired compare on a partial overlap shows scores on the overlap only, without saying so.
- Unpaired compare uses eval_metric; paired uses AUC.
- The clean_labels version node looks unused in the tree.
- A review with max_experiments 2 shows 3 steps (serve_rung is free).
- Backfilled roots have op null; new uploads have op "upload".

## Claims that held

**Backfill**
- Projects, parent, root, op, sha256 and n_rows are correct.
- Gym and ablation runs are branched_from, with source and patch.
- built_from edges exist, and the SQL refresh edge is marked inferred.
- Idempotent across a restart: 5,735 edges, 0 duplicates.
- 127 forced backfills during live training: no errors.

**Versions**
- The original's sha256 is unchanged.
- A version of a version has the right parent, root and root_rows (2,970).

**Compare**
- Δ matches sklearn exactly (−0.003552 on 596 shared rows).
- The CI matches an independent bootstrap: [−0.0462, +0.0401] vs the API's [−0.0443, +0.0416].
- Versions resolve to their source run; derived versions pair through root rows; a different dataset is unpaired with no CI.

**Champion**
- Rejected, other-project and already-champion requests are refused.
- Case, padding, full-width and zero-width variants are refused.
- A third party can approve, and the new champion supersedes the old one.
- Requests, approvals and rejections are all audited.

**Retention**
- The plan's bytes equal `du -sb`.
- Pinned: champion, Staging, source runs, top 3, keep, running.
- Non-admin → 403; "Admin." → 403; pinned → 409.
- Slim: 157 MB → 1.1 MB, and it still predicts. Evict frees the disk, writes a tombstone, and is audited.
- 34 routes return 410.
- Compare, lineage, the stored ladder, reviews and Gym children still work after eviction.

**UI**
- The tree matches the edges.
- Review branches collapse with their verdicts.
- No console errors except the expected 410s.
