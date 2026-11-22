# LB-A: labels core (label layers, carry-over, snapshots, overlay, materialise)

**Slice of docs/plans/labeling.md:** L01a, L02, L03, L04a, minimal L04b (run overlay verify), L05 (materialise), minimal
L06a/L06b (require() on every route, purge redaction, label kinds in policy, four-eyes keys). SQLite only; the PG DDL
(`labels/schema.pg_ddl`, trigger twins, column grants) is written but not run.

## What is built
- `backend/app/labels/`: `schema.py` (all §3.1 tables incl. LB-B's task/queue/LLM tables, seeded registries,
  `le_ro_u`/`le_ro_d` append-only triggers with the purge-redaction exception, `ri_label_ro_u`, blobs/blob_refs label kinds),
  `store.py` (layers, schemas, `append_events` ≤ 500 with server-side HMAC hashes, `events`), `resolve.py` (votes,
  adjudication, majority/single plug-ins, machine precedence, class_map), `status.py` (ok / absorbed / stale / base_changed /
  conflict / orphaned + unlabelled), `snapshot.py` (Parquet + manifest blobs, values/events/policy hashes, verify),
  `overlay.py` (pin at POST /runs, applied set in the worker, training rows only, overlay blob, run verify),
  `materialise.py` (`derive()` op `apply_labels`, holdout cells never written), `purge.py` (redaction + snapshot/overlay
  blobs into the purge's byte deletion), `api_layers.py`, `api_snapshots.py`, empty `api_tasks.py` / `api_llm.py` for LB-B.
- Hooks: `catalog/schema.migrate`, `pg_schema`, `authz/policy.py` (all `label.*` actions, FOUR_EYES keys, `Rule.task_role`),
  `authz/scope.py` (label_layer / label_task / label_snapshot), `engine/api.py` (`RunCreate.labels`), `engine/worker.py`
  (overlay after the holdout mask, again on the sampled path; pins written in the run_inputs INSERT), `lake/upload.py`
  (`label_identity_lost` → 400 unless `acknowledge: label_identity_loss`), `catalog/purge.py` + `artifacts.SKIP_TABLES`.
- Review (labeling-review.md) applied: C2 (votes resolved per epoch, `epoch_hash` + `item_key` in the snapshot), C3
  (`Rule.task_role`: label_task members, guests included, pass `label.annotate` / `label.review` on scope `label_task`),
  M2 (`absorbed`), M12 (direct `POST …/events` needs `label.task_manage`).

## Chain (fresh API, SQLite, ENSEMBLE_POC_MODE=1): `docs/progress/LB-A-chain.py`
```
1 upload v1 ds_876287d8
2 batch append 42 seqs 1 42
   bad class 400 unknown_class
   bad source 400 unknown_source
   missing row 400 row_not_found
  status v1 {'ok': 42, 'disputed': 1, 'unlabelled': 157}
3 edit v2 ds_066d1eef {'ok': 37, 'stale': 2, 'conflict': 1, 'orphaned': 2, 'disputed': 1, 'live': 37, 'unlabelled': 157}
   non-ok: [(3, 'amount', 'conflict'), (5, 'category', 'stale'), (10, 'category', 'stale'), (20, 'category', 'orphaned'), (25, 'category', 'orphaned')]
   C2 re-review of stale row 5 on v2: [(5, 'Other', 'ok')]
4 lossy re-upload (row hash) without ack: 400 acknowledge_required label_identity_lost 38
   acked ds_bdcf4a4c match.label_identity_lost n/a status orphaned 40
   key re-upload v3 ds_3bce23ae base_changed: [(35, 'base_changed')]
5 snapshot lsn_55d81187cad29479 {'applied': 41, 'disputed': 1} verify ok
   rebuild: same values/events sha True True
6 run run_1a4b6da6 succeeded None
   run overlay verify ok rows_applied 29
7 materialise ds_6d18b57f rows_changed 29 {'applied': 29, 'holdout_skipped': 8, 'orphaned': 2, 'stale': 1, 'base_changed': 1, 'conflict': 0, 'absorbed': 0}
   diff changed rows 29
   status on materialised version: absorbed 29 conflict 1 base_changed 1
8 purge pur_bad48648fc labels: {'layers': ['ll_7dabb2a571bca326'], 'events_redacted': 44, 'snapshots_purged': ['lsn_55d81187cad29479', 'lsn_5a4e31a14f07c962'], 'overlay_blobs': 1, 'llm_cache_deleted': 0} db scrub error: None
   sweep purge_incomplete ['datasets', 'projects', 'dataset_roots']
   run verify after purge purged | snapshot verify purged
   events with value left on category: 0 | amount events kept: ['42']
```
API log: 0 tracebacks. Run holdout checked by hand on an earlier run: the one labelled holdout row (snapshot Fraud, base
Billing) was scored with y_true = Billing.

## Not built / known gaps
- Snapshot build is synchronous in the request (no `label_snapshot` job); rows/status read via pyarrow `is_in` (not the
  DuckDB semi-join): fine for the POC, not 10M.
- Epochs: the snapshot keeps the latest epoch only (review C2 asks for the latest K); `values_sha256` does not yet cover
  `item_key`/`epoch_hash` (M5).
- Post-fit readers (review C1: `serving.read_ids`, RunRows, vela/rows, gym/export) still read base labels: correct for
  the holdout (`holdout: original`), wrong for training-row diagnostics on overlay runs. No `labels_fitted_sha256` yet.
- `holdout: corrected` refused (409); join runs with labels refused (400 labels_unsupported_kind); crowdlab not registered.
- Upload `/check` does not list `label_identity_lost` yet (only the commit refuses). No remap route.
- Purge sweep still reports `purge_incomplete` on datasets/projects/dataset_roots (platform tables, not label tables).
- No snapshot approve route, no proposals, no inputs_gate overlay check (L06b / M7).

## Grep gate 3
`grep -rn "frames.lake_table\|read_part_columns" app | grep -v "^app/(frames.py|lake/|labels/)"`: the one label-column
read is `engine/serving.py:180` (`read_ids`, holdout / RunRows); the rest read schemas, n_rows or SQL relations.
