# Second UI-flow audit — verified findings (2026-08-09)

Eight parallel domain agents drove every UI flow over HTTP on isolated worker
DBs, self-refuted each candidate against source, and reported only verified
defects with reproduction. Fixed centrally, each with a failing-first test.
Severity: silent-wrong-answer (confident wrong output a UI cannot detect) is the
worst class and is called out per item.

## Outcome

- **14 defects fixed** across files, explorer, data_accelerator, library,
  discovery, relationships, transform, and platform/MCP — 3 HIGH (1 security),
  8 MEDIUM, 3 LOW. Each has a failing-first test.
- **2 leads refuted** as deliberate, pinned behaviour (library multi-target
  publish; aggregate group_count == returned page). Kept, not changed.
- **1 lead deferred** as a defensible UX enhancement (health status doesn't flag
  a superseded profiled version; the version is already disclosed in evidence).
- Full suite green afterwards: **2680 tests, 0 failures** (Postgres + MinIO).
- Every fix is a small commit on `feat/analytics-mcp-consolidation`; the
  throwaway per-domain probe files the agents wrote were removed.

Per-domain detail follows.

## files (w1) — DONE
- [MED, silent-wrong] version `size_bytes` counted only the canonical/default
  sheet on upload, but summed all sheets on sheet-replace — multi-sheet uploads
  under-reported size everywhere the catalog/version-list/search show it.
  Fixed: upload path sums per-sheet sizes (processing.py). Test: test_files_size_accounting.py
- [MED, silent-wrong] `GET /upload/status` `file_size_bytes` = raw bytes from the
  warm cache but parquet bytes from the DB fallback → changed after a restart.
  Fixed: persist raw `source_size_bytes`, both fallbacks read it (processing.py, files/api.py).

## explorer (w2)
- [HIGH, silent-wrong] cursor pagination skips+duplicates rows when the sort key
  has ties (offset paging over a non-total order). root: shared/query/compile.py.
- [HIGH] POST /profile-runs 500s when a numeric-pair correlation is undefined
  (CORR() NULL vs non-nullable float schema). root: data_accelerator/schemas.py + profiling.py.

## data_accelerator (w3)
- [MED] per-sheet diff & row-diff 404 across a confirmed rename (match side B by
  sheet_key only). root: data_accelerator/services/diffs.py, rowdiff.py.
- [REFUTED] "group_count reports the page, not true cardinality, under a limit"
  — this is the documented contract: group_count == the returned page's group
  count, `truncated` reflects the server cap only, and the stored artifact holds
  exactly the page (test_datasets_journeys asserts group_count==2 under limit:2
  with an explicit comment). Not changed.

## library (w4)
- [REFUTED] "a completed analytics run can be published unlimited times" — this
  is deliberate: a run may seed a new dataset AND be published as a new version
  of the source (test_library_journeys step 9 pins the self-derivation, unlike
  the transform path which is single-target). Not a bug; no guard added.
- [MED, silent-wrong] masked category axis collapses distinct groups and drops
  their measures in chart render. root: library/service.py + charts.py.
- [LOW] publish name-collision returns generic code:"conflict". root: library/service.py.
- [LOW] `new_version` publish silently ignores a supplied `name`. root: library/service.py.

## discovery (w5)
- [MED, silent-wrong] empty/blanked column dictionary entries count as
  "documented" (asymmetry: sheet side is content-guarded, column side is not) —
  inflates health doc dimension, catalog facet, and the ?documentation= filter.
  root: discovery/repo.py.
- [LOW] health missing_data/duplicates top-line status describes the latest
  *profiled* version, which can be a superseded one. (optional stale flag)

## relationships (w6)
- [HIGH, silent-wrong] manual re-declare of a CONFIRMED edge corrupts provenance:
  method badge frozen but its evidence/confidence overwritten (fk_rule loses
  rule_id; statistical gets a fabricated 1.0 confidence). root: relationships/repo.py.
- [LOW] SeedResponse.created counts rules projected, not rows inserted (re-seed
  reports created>0 with no new edges). root: relationships/repo.py + service.py.

## transform (w7)
- [MED] structurally-invalid step configs (unparseable regex, invalid strptime
  format) pass save-time validation and only fail at run — violates the
  "reject at save, not run" invariant. root: transform/service.py + compile.py.
- [LOW, self-refuted, by-design] compute/split/merge `into` overwriting a
  normalized-name column; dedupe without order_by arbitrary survivor. (skip)

## platform / governance / MCP (w8)
- [HIGH, security, silent-wrong] masking re-leaked through derived `/samples`
  artifacts: an elevated caller's aggregate/sql/pivot output holds raw sensitive
  values, and a DATASET_READ teammate can fetch it (residual flagged in DEFECTS.md).
  root: files/api.py `_authorize_sample_access` (needs ensure_raw_access).
- [MED] derived-artifact `/samples` downloads are not audited (egress with no
  trail). root: app/api/middleware.py is_download predicate.
- [LOW] GET /teams (and members) report limit==len(items), giving limit:0 on the
  empty state. root: auth/api.py.

---

# Hands-on UI test sweep (2026-08-09, later)

Ten agents drove every UI flow in real browsers with **computed** assertions
(fixtures whose correct answers were known in advance; writes verified by
re-reading, never by trusting a toast). ~700 checks. Everything below was
reproduced before it was fixed, and re-verified in the browser after.

## Fixed — client

- **Every 204 DELETE reported as a failure.** `parse()` checked content-type
  before status, and the server stamps `content-type: application/json` on its
  204s, so `res.json()` threw on the empty body. All 10 delete call sites showed
  "Delete failed" while the delete had succeeded, leaving a ghost row and (in
  modals) a stuck backdrop. Three agents hit this independently.
- **Validation errors were unreadable.** FastAPI puts the real reason in
  `errors[]`; the client showed only the constant "Request validation failed".
  `ApiError` now folds field errors into the message.

## Fixed — UI

- **Phantom dataset page**: an unknown or cross-team id synthesized a fake
  dataset — raw UUID as the title, live Edit/Delete, and tabs blaming a missing
  version. Now probes existence first and surfaces a real error; also pages the
  catalog so deep links past the first 200 datasets resolve.
- **Unmatched-join % rendered 100× too high** for any rate ≤ 1% (0.5% → "50%"):
  one helper was scaling both fractions and already-percent values.
- **Editing a saved view silently rewrote it** — dropped `OR` logic, extra sort
  keys, `search`, and nested filter groups; a 4-row view became 0 rows with no
  warning. Edits now merge into the stored spec, and an un-editable grouped
  filter is preserved with a notice.
- Analytics: chart series now come from the server's `columns` (a duplicate
  alias plotted one measure twice); a non-additive-only run explains the missing
  grand total; the pivot Total row fills its grand-total cell; the pivot chart
  no longer plots the row-total column as a peer series; duplicate React keys.
- Explore: removing the last filter condition left the grid filtered with no way
  to clear it; added a "Clear all" and an active-filter count.
- "Act as" kept the previous seat's label, so the top bar claimed
  "System (admin)" while acting as a viewer.
- Catalog: row click did a full page reload (now client-side routing);
  pagination added, and the per-page tiles say so instead of mixing denominators.
- Versions: multi-sheet versions can now be downloaded (sheet picker) instead of
  being told to set a query parameter; removed an inert "clickable" row.
- Favorite: a fast second click re-sent the first verb and 404'd.
- Dataset header shows a read-only badge instead of buttons a viewer can't use
  (the server remains the enforcement point).
- Charts can now be created and deleted in the UI (previously read-only).

## Fixed — server

- **Duplicate aggregation alias produced a wrong grand total**: the totals query
  is a bare SELECT, so the duplicate key collapsed and the last measure won —
  the footer showed the count total under the sum's column. Now a typed 400
  (`duplicate-alias`).
- Parse errors leaked the absolute staging path (`/tmp/accelerator/...`), both
  quoted and inside a suggested `read_csv_auto('...')` fix. Scrubbed to basenames.
- A rejected upload left a permanent zombie dataset (created before parsing,
  never rolled back). A dataset created by a failed upload is now removed; a
  pre-existing dataset keeps its failed version as real history.
- Transform run LIST omitted the publish stamp, so the UI re-offered Publish and
  the second attempt 409'd. `published_version_*` moved onto the list model.
- Tag names containing `/`, `\`, or whitespace are rejected at creation: every
  other tag route takes the name as a path segment, so such a tag could be
  created and then never read, promoted, or deleted.

## Refuted — pinned, deliberate behaviour (reverted my change)

- **"An empty upload should be rejected."** No: a zero-row upload is a
  legitimate empty export, pinned by
  `test_upload_failure_contracts.py::test_uploading_an_empty_file_still_succeeds`.
  The UI now warns that 0 rows landed instead of the server refusing the file.
- **"Discovery over-reports `suggested`."** `suggested` counts pairs *derived*,
  including ones that only refresh `evidence.statistical` — real work, pinned by
  `test_relationship_guards.py`. The UI toast was reworded to report new edges
  separately rather than changing the API's semantics.

## Known-open (low severity, deliberately not changed)

- A saved view can still be created with a type-mismatched filter value; it
  fails readably at run time rather than at save.
- Admin: no search across ~400 teams; re-adding an existing member silently
  changes their role; selected team isn't in the URL.
- Transform preview samples at the head of the chain, so dedupe/filter previews
  aren't representative on large datasets (labelled "sampled dry run").
- Quality: a warning-only failure shows a green `completed` badge.

---

# Deep audits: a11y, masking depth, uploads, concurrency (2026-08-09, final)

Four more agents on the areas nobody had touched.

## SECURITY — six paths leaked raw sensitive values (all fixed, all pinned)

The masking policy masks a **viewer and an editor**; only elevated access sees
raw. Six routes bypassed it by never passing the principal to the service. Two
were reachable straight from the UI by a viewer whose own page header said
"read-only".

| Path | Reach | Fix |
|---|---|---|
| Transform **preview** | UI, viewer | `ensure_raw_access` |
| Transform **compile with `rows`** | API, viewer — could author *any* pipeline over *any* readable dataset and dump PII | gated; schema-only compile stays open |
| **Join preview** | UI, viewer — exposes BOTH datasets' columns | gated on both sides |
| **Profile-run detail** | API, viewer — `top_values` are verbatim cell values | values masked, extremes dropped |
| **Profile drift** in schema diff (`?include=profile`) | API, viewer — categories built from `top_values` | gated |
| Saved **analytics definition run** | API, editor — the computation `/aggregate` refuses, via a second door | gated |

Masking is otherwise real and selective: the same seat sees `***` in the grid,
view runs and row diffs, gets 403 on SQL/download/aggregate/pivot/`/samples`,
and still sees real values for non-sensitive columns. Verified after the fix:
8/8 leak probes plus join preview, with controls proving schema-only compile
still works, admins still see raw, and ordinary reads are masked, not refused.

## Upload paths

- **A finished TUS upload could be replayed to overwrite an immutable ready
  version** — ingest deletes the staging file, so the resume offset read 0 and
  a client that lost its final PATCH response would re-send over a ready
  version (content and checksum changed; status stayed `ready`). Fixed.
- Concurrent uploads to one dataset **500'd ~50% of the time** (SELECT MAX then
  INSERT). Number now allocated in the INSERT with retry.
- TUS accepted `include_sheets` and ignored it; 413 stranded a dataset;
  status pollers dropped `error_kind`. All fixed.

## Accessibility (measured, not estimated)

The app was **unusable below ~900px**: a fixed 232px sidebar left an 87px
content column at 375px with every action off-screen and +332px of overflow.
Now 0 overflow and full-width content at 375 and 320. Also fixed: 80 inputs
with no accessible name; modals with no focus management at all; sort and the
data-dictionary editor being mouse-only; 13 glyph-only buttons; missing
landmarks/skip link/title/live regions; 19 sub-AA contrast pairs (via separate
text tokens — the validated chart palette and reserved status hues untouched);
`prefers-reduced-motion`. 14/14 verified in-browser.

## Volume

API is never the bottleneck (50k-row upload 161ms; 20 concurrent aggregates
0 errors, p95 775ms). The **browser** was: a 5,200-group chart drew 0.14px-wide
bars, and 50,000 groups froze the main thread for 10s building 400k DOM nodes.
Charts now cap at 60 categories with a message and a full table fallback.

## Refuted — pinned, deliberate behaviour (my change reverted)

- A **cancelled TUS upload keeps its dataset**: the documented retry flow is to
  re-upload onto the same id, and the failed version must keep its number
  (`test_files_journeys`). The UI now explains a dataset with no readable
  version and offers Delete, instead of the server deleting data mid-retry.
- An **empty upload is a legitimate empty export** (`test_upload_failure_contracts`).

## Known-open (deliberate, listed rather than hidden)

- ~10 write controls are still offered to seats that can't use them (they refuse
  correctly and change nothing; the dataset header's `read-only` badge is the
  pattern to extend). `Execute join` even becomes enabled after Preview.
- Publish is not idempotent under two *parallel* requests (two datasets). The
  sequential multi-target publish is deliberate, so this needs a dedupe key
  rather than a blanket guard.
- Double-submit guards are `disabled={busy}` state, so two clicks dispatched in
  a single JS task can both get through; a real human double-click is de-duped.
- No optimistic concurrency on dataset metadata (last write wins, no signal).
- Explore's column drawer renders `top values` as `[object Object]`.
- Upload's destination picker only lists the first 200 datasets.
