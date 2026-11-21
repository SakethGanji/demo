# DLA1: independent check of DL1 (dataset management + lineage), motion-components @ bf25809

The check was read-only and ran on a scratch data dir with self-generated CSVs:

- **loans:** 3,000 rows with an id-like `loan_ref` column.
- **nokey:** 2,600 rows, no id column, 102 duplicate rows.
- **money:** values like "$1,234" and "n/a".

Loans v2 removed 300 rows, changed 200 (income × 1.1), added 400 and added a column (`bureau`), and was shuffled.

## Confidence

- **About 75 %** that the normal UI paths work end to end.
- **About 55 %** for the governance guarantees, because of B1, B2 and the retire dead end.

## Grades

| # | Claim | Grade | Evidence |
|---|---|---|---|
| 1 | Upload as next version | WORKS | **Guessed key:** 2,700 matched, 200 changed, +400, −300 (exact). **Row hash on nokey:** 2,373 / +107 / −227 (exact multiset). **Holdout/fold:** kept for all matched rows. **Warnings:** all fire. **Bad inputs:** 400/404/422, no 500s. |
| 2 | History, default, rollback | WORKS | Data and splits sha256 unchanged by rollback. |
| 3 | Diff | WORKS (API) / PARTLY (edges) | Matches the pandas diff. PSI matches by hand (0.014548). See B4, B5. |
| 4 | Usage (reverse lineage) | PARTLY | Fine through retention eviction, but see B1. |
| 5 | Export | WORKS | Manifest sha = sha256sum = header. |
| 6 | Archive/delete | PARTLY | **Guards:** hold. **Four-eyes:** 10 spoof variants → 403. **Admin path:** works. **Tombstone and 410s:** correct. **But:** B1, B2, retire dead end. |
| 7 | Integrity | WORKS | Training on a mismatched version is still accepted (B3). |
| 8 | Search | WORKS | `since=garbage` → 200 empty (should be 400). `state=deleted` finds only whole deleted datasets, not deleted versions. |
| 9 | Audit | WORKS | **Anomaly-review and shift-weights versions:** don't pass `by` (audited as "system"). **Refused actions:** not audited. |
| 10 | UI | PARTLY | **Main flow:** 0 console errors. See B6–B8. |

## Bugs

### B1, CRITICAL: a run delete takes away the guard on its training data
- `DELETE /api/runs/{id}` needs no `by`, writes no audit row, and has no four-eyes or guard.
- `dsets.usage()` joins lineage_edges to `runs`. Once the run row is gone, its approved model and champion vanish from usage and from `blockers()`.

Repro:
1. Train on v2, register, approve (carol), make champion (approved).
2. Archive v2 → 409. Good.
3. `DELETE /api/runs/<run>` → 204.
4. `GET usage` → runs [], versions [], champions [].
5. Delete request (bob) + approve (carol) → deleted.

The champion's training data is gone.

Fix:
- Drive usage from lineage_edges with a LEFT JOIN.
- Refuse a run delete while the run is the source of a registered version or a champion.
- Make run delete require `by` and write an audit row.

### B2, MAJOR: a careless upload becomes the default version at once
- With no default set, the default is the newest active version, and upload is never blocked.
- A 3-row upload (rows_dropped 99.9 %, 4 × column_removed) became "default v8 (latest)" immediately.
- In the UI, picking the file commits it: there is no preview and no "commit anyway?".

Fix: a new version becomes the default only on an explicit action, or when it has no warnings. Add a preview/confirm step in the UI.

### B3, MAJOR: training on a version whose verify says MISMATCH is accepted (201)
Refuse training, or at least warn and record the mismatch on the run.

### Minor bugs

- **B4:** diffing unrelated datasets with no shared same-type columns reports false matches ("matched 2,600 over 0 shared columns"), because row_hash is all zeros. Refuse, or say "no rows matched".
- **B5:** two siblings uploaded from the same file (same sha) diff as +400/−400, because their new rows got different negative root ids. Fall back to the row hash when the shas are equal, or when new rows don't pair.
- **B6 (UI):** the diff card heading switches to the new A/B pair while showing the previous pair's numbers. `useFetch` keeps stale data with no loading state, and on error the old numbers stay up.
- **B7:** unarchiving a whole dataset also unarchives versions that were archived on their own.
- **B8:**
  - The default diff is the last two active versions; parent → child would be better.
  - A deleted row still shows ✓ next to its hash.
  - The requester can't withdraw their own delete request (403).
  - At 1366 px the "Last change" column is cut off.
  - Training on an archived version is allowed, and nothing says so.

## Dead end: retiring approved models

- A model moved to stage Archived still blocks **archiving** its data, so hiding is treated like deleting.
- The 409 says "Reject or retire these first", but neither is possible:
  - an approved model can't be rejected;
  - there is no retire state.
- The registry also allowed archiving the current approved champion, and the champion stayed approved.

Proposal:
1. Allow archiving data used only by Archived, non-champion models.
2. Add a four-eyes, audited **retire** state for registered versions, not allowed while the version is champion.
3. Allow deleting data only after retirement plus the retention period, with four-eyes. Keep the hash, the tombstone and an optional frozen holdout sample for re-validation.
4. Block archiving the current champion.

## Missing basics

- Protection on run delete (B1).
- Explicit promotion or a release gate for the default version (B2).
- An integrity gate on training, plus a scheduled verify.
- The retire path (above).
- v1 can't be deleted or archived alone, because its id is the root and means the whole dataset. That blocks a PII purge of the first extract while later versions are kept.
- No free-text reason or change ticket per uploaded version.
- No read or access audit: row lookups and previews are not logged.

## Nice to have

- A per-dataset contract.
- A change data feed / cell-level change export.
- Column-level lineage and a schema registry.
- Retention or legal hold per dataset.
- OpenLineage/catalog sync.
- Branches or tags such as "approved-for-model-X".
- "As of date" queries.
- PII tags per column.
- Real sign-in. Names are not identities; the UI's "Acting as" box defeats four-eyes trivially.
