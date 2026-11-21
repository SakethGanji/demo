# DLA4: independent recheck of DL4 (motion-components @ 6a75f6a)

**Confidence:** dataset management and editing 78 %; governance 86 %.

## Fixed / passing

- **M-a formulas:** 457 formulas, 0 5xx responses.
- **M-b add_rows:** keeps ints; the target stays {0,1}.
- **M-c target edits:** drop/rename/cast refused, including the add-copy → drop → rename-back trick. `set_target` works, is audited, and keeps the result off the default.
- **M-d split tamper:** caught by verify and at fit time. The override is audited; restore-splits works; `splits_sha256` is recorded on runs.
- **m-f** (cap per edit), **m-g** (summary equals diff), **m-h** (delete guarded, irreproducible acknowledgement needs four eyes; every spoof refused), **m-i**, **reset** (four eyes; every spoof refused).
- **Core flow:** passes. The paired Δ equals an independent recomputation from /holdout-predictions (−0.044444).

## Open

1. **Major, 500s.** `set_cells` of 1e30, 1e308, 2**63 or 2**64 into an int column, and `add_column` with value 1.797e308, return 500. They come from `frame_io.prepare` / `to_csv_bytes` (cannot safely cast float64 to int64) and from inf not being JSON-compliant. 28 distinct cases. `fill_missing` / `replace_values` with these values give a 400 that leaks pandas internals.
2. **Major, silent wrong lineage.** The upload-as-version key guess missed a plain id column (`acct` "A00000"). It fell back to an exact row hash: 15 corrected rows were recorded as deleted + new, lost their root identity and holdout assignment, and 20 % of the holdout was redrawn. Nothing tells the user the guess failed (the profile marks `acct` as role 'ignored').
3. **Major, known limit.** Tampering with the split file **and** its DB hash (split_files) is undetected. Verify passes, a 50-row holdout trains, and restore-splits refuses ("nothing to restore").
   - Signals that exist but are never compared: the run's `splits_sha256` differs from earlier runs on the same version, and `datasets.splits_sha256` is stale.
   - Paired compare says "paired on the same original holdout rows" over a 50-of-180 overlap.
   - Fix: at verify and fit, compare against earlier runs' `splits_sha256` and the version's stored hash, and keep the protecting hash outside the mutable table.
4. **Minor.** About 60 `pd.read_csv(csv_path)` call sites ignore the version schema, so the ZIP shows as a number in Explain, Model Doctor, Slices, fairlending and scoring. The export zip lacks schema.json. Smaller items:
   - a one-class target edit only warns;
   - the reset request is not marked used;
   - `runs_made_irreproducible` is null in the pending response;
   - Build match misses renamed-column files (safe: it creates a new dataset).

All four should be absorbed by the Parquet + DuckDB data-platform plan:
- typed Parquet reads through one `frames.read`;
- `__row_id` identity with an explicit key choice;
- split files as content-addressed Parquet with run-level cross-checks.
