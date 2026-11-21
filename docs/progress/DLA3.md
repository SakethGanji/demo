# DLA3 · Independent recheck of dataset management, editing and governance, motion-components @ 9a01901

Written into the repo by DL4 (the recheck's findings, as reported to it).

## Confidence

- Dataset management and editing: **80 %** (89 % once M-b and M-c are fixed).
- Governance: **82 %**.

## What works

- Edit ops. A preview writes nothing, and the parent's sha256 is unchanged.
- The diff equals pandas (503 / +2 / −57).
- Root, holdout and fold carry-over are exact.
- A reason is required; dedupe; caps. About 45 formula attacks were refused, and no code was executed.
- The UI edit flow ran with 0 console errors.
- Reset lockdown, including against spoofed admin names. The audit log went 123 → 153 and never shrank.
- Train-time hash; adopt-disk-file; an edit on a mismatched version → 409.
- m1–m6.
- Core flow, Model Doctor, registry four-eyes.

## Issues

| # | Severity | Finding |
|---|---|---|
| M-a | 500 | `round(income, 1e18)` in a formula → 500 (OverflowError). |
| M-b | major | `add_rows` turns int columns into float. The target becomes "0.0, 1.0" with a `new_target_category` warning, which blocks making the version the default. |
| M-c | major | `drop_column` of the target → 201, and the target silently becomes "region". Rename and cast of the target are also accepted. |
| M-d | major | Truncating the holdout in the split files from 396 to 50 rows → verify says "ok". The run trains on 346 rows that other runs hold out, and its metric is computed on 50 rows. |
| m-e | minor | `cast_column` number → text dedupes to the parent and does nothing. Uploads strip leading zeros ("02139" → 2139). |
| m-f | minor | The add_rows cap is per op (2 × 800 accepted). |
| m-g | minor | After rename + replace, the edit summary says "changed 51" while the diff says 503. |
| m-h | minor | A dataset delete removed files that a succeeded run trained on. |
| m-i | minor | The upload preview returns 201. |
| reset | governance | With nothing registered, an admin plus the flag alone wipes all user data, with no four eyes. |
| m7 | — | Not re-tested. |
