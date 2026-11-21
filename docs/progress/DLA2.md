# DLA2: independent recheck of DL2 (dataset governance), motion-components @ fba1ed1

The check re-ran DLA1's repros and probed the paths around them, on a scratch data dir with self-generated CSVs.

## Confidence

- **Dataset management end to end: 88 %.**
- **Governance: 65 %**, about 80 % once C1 and M1 are fixed.

## Confirmed fixed

- B1: the run-delete guard.
- The upload preview.
- The retire path.
- The v1-only purge.
- The reason field.
- The audit of refused actions.
- B4–B8.
- Search.
- The full core flow.

## C1, CRITICAL: reset bypasses all governance

`POST /api/admin/reset` needs only `{"confirm": "reset"}`. It asks for no `by`, makes no admin check, has no four eyes and writes no audit row.

One call:
- wiped `audit_log` from 108 rows to 14;
- runs 8 → 0;
- model_versions 2 → 0;
- datasets 27 → 14.

It also left:
- a dangling approved champion;
- 20 dangling lineage edges;
- zero audit rows about the reset itself.

## M1, MAJOR: no train-time hash

A file tampered with after the last OK verify still trains, and the run records integrity "ok".

If the file is then restored and re-verified, the tampering can't be detected, because the run stores no sha256 of the bytes it read.

## Minors

- **m1:** an admin retires a version alone, and anyone can type an admin's name.
- **m2:** reads are allowed without a name. The dedupe uses the raw name. Three reads aren't logged at all: anomaly top, shift rows and holdout predictions.
- **m3:** derived versions are created as "system" with no name. Identical shift-weights versions aren't deduped.
- **m4:** a tiny upload gives a raw sklearn `n_splits` error.
- **m5:** a 5-row version is accepted for training, then fails.
- **m6:** anyone can withdraw anyone's delete request.
- **m7:** sample datasets are regenerated in place when their generator changes.
