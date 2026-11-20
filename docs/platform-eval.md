# Platform layer: build or adopt tools? (independent read-only review, motion-components @ 93a40d3, 2026-11-20)

## Bottom line

**Don't replace the core.** MLflow 3.18 (already in the repo) is the only tool that earns a bigger role, and only as a
**derived index and a UI people can browse**. It should not be the system of record for runs, dataset versions,
lineage, approval or retention.

What makes ENSEMBLE trustworthy is not something these tools do:
- paired Δ on the same original rows;
- the four-eyes rule;
- a hash-stamped audit trail;
- holdouts that stay fixed across dataset versions.

What breaks at thousands of runs is not tooling either:
- 13 call sites read every run and parse every result JSON;
- lineage is written by 7 hand-rolled `lineage.json` writers;
- nothing deletes 1–6 GB predictors.

So: fix those in our own schema, then mirror more into MLflow (parent runs, `log_input`, the `champion` alias).

## Inventory of custom pieces (lines from `wc -l`)

| Piece | Where | Lines | Overlaps with |
|---|---|---|---|
| SQLite schema (6 tables + batch_jobs; JSON blobs in `runs.config` / `runs.result`; no projects, tags, notes, lineage edges or dataset-version table) | db.py | 161 | MLflow tracking store |
| Dataset store + versions (CSV per id, fixed splits, `create_version` + `lineage.json` {parent, root, root_rows, changes}) | datasets.py | 332 | DVC, lakeFS, Delta/Iceberg, mlflow.data |
| Lineage writers (7 copies of the same logic) | data_doctor_api, featurelab_api, shift_api, annotations_api, active_learning_api, anomaly/api, autopilot/engine | 10–20 each | none: should be one helper |
| Root-row mapping + paired Δ | vela/experiments.py `_roots`, `delta()` | ~90 | no tool does this |
| Run store + events | engine/store.py | 120 | MLflow runs, partly |
| Job runner (process per fit, process-group kill, restart recovery, reload adoption) | engine/runner.py, jobs.py | 290 | Prefect, Dagster, Ray Jobs |
| SSE with replay | engine/api.py | ~50 | none |
| Parent + patch (children found by scanning all runs) | engine/api.py, experiments.py | ~10 + callers | MLflow nested runs |
| MLflow mirror (uses the deprecated stage API) | registry/tracking.py | 204 | MLflow |
| Registry, four-eyes, stages | registry/store.py, api.py | ~300 relevant | MLflow registry (different semantics) |
| Version build (clone → refit_full → infer_limit → clone_for_deployment → measure → SHA-256) | registry/build.py | 794 | MLflow LoggedModel, partly |
| Audit log (append-only) | registry/store.py + ~27 call sites | ~40 | MLflow has none |
| Typed experiments | vela/experiments.py | 489 | none |
| Review journal | vela/review.py | 852 | none |
| Agent | agent/tools.py, loop.py | 1,365 | none |
| Compare UI (unpaired Δ) | CompareRuns.tsx | 129 | MLflow compare UI |
| Lineage UI (first 10 versions; no run→run or ds→ds edges) | Lineage.tsx | 39 | MLflow UI, partly |
| Ladder | engine/ladder.py | 314 | none |
| Stats (Wilson, DeLong; paired bootstrap re-implemented in ~15 modules) | stats.py | 49 | scipy.stats.bootstrap (the loop only) |
| Storage; no retention anywhere | config.py | — | MLflow artifact store, `gc` |

## Tool by tool (licences and hosting checked against Nov 2026 docs)

### MLflow 3.18: wrap it, mirror more
- **What fits:**
  - experiments as projects; parent runs;
  - `log_input` with dataset digest and tags; `search_runs` on dataset name, digest and tags;
  - LoggedModel; aliases (`champion`); `search_model_versions`;
  - the compare UI; basic-auth/RBAC; workspaces (logical, not hard isolation).
- **Verified gaps (prototyped):**
  - dataset digests sample only the first 10,000 rows (a change at row 30,000 of 50k gave the same digest), so it is not an integrity hash;
  - no dataset→dataset lineage (`create_dataset` in 3.18 is for GenAI eval sets);
  - no "same original rows";
  - `mlflow gc` hard-deleted a run that a `champion`-aliased version still pointed at, leaving a dangling run_id;
  - no four-eyes, no append-only audit;
  - stages are deprecated;
  - runs can't move between experiments.

### DVC: reject
Git- and CLI-centred, with commits made by people. Our versions are made at runtime by the app. It deletes nothing.
(lakeFS took DVC over in Nov 2025.)

### lakeFS: defer
A storage backend (object store + server + KV; RBAC/SSO are enterprise-only). Use it only if the bank already runs it; then a
version = a commit ref on our `dataset_versions` row.

### Delta / Iceberg time travel: reject for now
These snapshot one table over time; our versions are new derived tables. Row tracking needs Spark. Revisit if the data source
becomes a lakehouse (then store the snapshot id).

### OpenLineage + Marquez: optional export later
A ~100-line emitter so bank governance tools (Marquez, DataHub, Collibra) see our jobs. It can't be our lineage store.

### Feast: not relevant

### W&B / Neptune / Comet: reject
- Neptune's SaaS shut down on 5 Mar 2026 and its self-hosted images were removed.
- W&B self-managed is enterprise-only.
- Comet self-hosted is enterprise.
- None has four-eyes approval or paired Δ.

### ZenML / Kedro / Metaflow: reject
Pipeline frameworks that would restructure the worker; RBAC is paid. They are not a run-tree, comparison or approval layer.

### Prefect / Dagster / Ray: keep the custom runner
Our runner handles process-group kill (AutoGluon's Ray children), reload adoption and restart recovery in 290 lines.
Revisit only for multi-host or GPU scheduling; then Ray Jobs (already a transitive dependency).

## Recommended architecture

- **System of record:** our SQLite (Postgres before multi-user).
- **Keep custom:**
  - run store, events and SSE; job runner;
  - datasets and splits; paired Δ and root rows;
  - typed experiments and the review journal;
  - four-eyes and the audit log;
  - build.py; the Doctor and the ladder.
- **Build (small):**
  - `projects` (datasets and runs get `project_id`).
  - Explicit dataset versions: parent_id, root_id, sha256, n_rows and op on `datasets`; root_rows in Parquet/npy.
  - `lineage_edges` (src, dst, relation ∈ derived_from / trained_on / branched_from / built_from / compared_in, actor, meta),
    so the tree is one recursive CTE.
  - Indexed run columns: project_id, parent_run_id, source, primary_metric, primary_score, predictor_state, predictor_bytes.
    This gives a light list path that fixes the 13 full scans.
  - `annotations`: tags and notes on anything.
  - `champions`: approval required, audited, mirrored to the MLflow alias.
  - `POST /api/compare {a, b}`: paired Δ + CI when a and b share a root and holdout rows; otherwise a labelled unpaired table.
  - One `stats.paired_bootstrap`.
- **Wrap MLflow:**
  - one experiment per project; `parentRunId`;
  - tags `ensemble.source` / `ensemble.review_id`; `log_input` per run with our SHA-256 as an input tag;
  - aliases only (drop the stage calls);
  - MLflow strictly derived and resyncable; never run `mlflow gc` on a timer.

### AI agent branches
- A hypothesis is a run with an edge `branched_from` → parent. The edge's meta holds source (vela-agent / doctor-experiment),
  patch, review_id, hypothesis_id and verdict.
- Data-deriving experiments also create a dataset-version node.
- UI tree: dataset root → versions → runs → branches, coloured by source and verdict; agent branches collapse under
  their review.
- In MLflow they are nested child runs tagged by source.

### Retention (1–6 GB per predictor)
- **Three tiers:**
  - **present:** the full predictor;
  - **slimmed:** `delete_models(models_to_keep='best')` + `save_space()`;
  - **evicted:** the predictor is deleted. Kept: config, result JSON, holdout_predictions.csv, leaderboard, journal and hashes.
- Paired comparisons, the ladder, journals and audit still work after eviction. Predict, explain, SHAP and what-if on an
  evicted run return 410 "re-fit from config".
- **Pinned (never evicted):**
  - the source runs of non-rejected versions;
  - the champion, Production and Staging;
  - runs under a pending or running review;
  - the top 3 per project and dataset;
  - runs tagged `keep`.
- Registered version folders follow the bank's model-risk retention period. Deleting one needs an audited four-eyes step.
- **Defaults (need model-risk approval):**
  - slim after 7 days, evict after 30;
  - agent branches not kept: evicted 7 days after their review ends;
  - failed runs: evicted after 3 days.
- Every eviction is audited, and there is a dry-run endpoint.

## Migration (the UI keeps working at every step)

| Step | Change | Lines (rough) |
|---|---|---|
| 1 | Schema additions + backfill from config, lineage.json and model_versions (a default project per dataset root) | +250 / 0 |
| 2 | One `datasets.derive()` helper writes versions, root_rows and edges; the 7 writers point at it | +120 / −110 |
| 3 | Light list queries + `children()` from edges; move the 13 `list()` callers; scale test with 5k synthetic runs | +60 / −40 |
| 4 | Projects, tags, notes and champion: APIs and UI | +350 backend / +500 UI |
| 5 | Lineage tree (recursive CTE) + `/api/compare` with paired Δ; Lineage.tsx becomes the full tree | +250 / +400 UI / −30 |
| 6 | MLflow mirror upgrade (experiment per project, parentRunId, log_input, champion alias, drop stages) + `resync` | +80 / −30 |
| 7 | Retention job (dry-run → slim → evict) + a `predictor_state` guard on every predictor-reading route | +300 |
| 8 | (optional) Stats consolidation, checked by before/after numbers on a fixed seed | +80 / −250 to −350 |

Total ≈ +2,000 to 2,300 lines added (half UI) and ≈ 450 to 550 removed. A heavier tool would remove only ~500 lines (the
mirror and the runner), and the runner would come back as glue.

## Risks
- **SQLite with concurrent writers:** plan Postgres before multi-user (plain SQL and CTEs port).
- **Backfill:** old runs lack lineage, so mark their edges `inferred` and never claim a paired Δ across them.
- **Eviction:** it breaks silent predictor readers (ladder re-score, shift, slices, learning curve, explain, recourse,
  card). Audit every `predictor_path` reader first.
- **MLflow drift:** keep MLflow derived, make the registry read-only for people, and add a resync. Never `mlflow gc` by hand.
- **Retention periods** are bank policy; model risk must approve them.
- **Agent branches:** the journal and holdout predictions are kept forever as evidence.

## Prototypes (scratch venv, mlflow 3.18, SQLite; no AutoGluon)
- **P1 (run, dataset and lineage):** the parent/child/agent-tag/dataset/LoggedModel/alias/approval-tag queries all
  worked. Missing: any link from ds_v2 back to ds_root.
- **P2 (digest):** only the first 10k rows count toward the digest.
- **P3/P4 (scale):** 3,000 runs.
  - Full paged read: 2.7 s; filtered: ~150 ms; children by tag: 4 ms; 100 × get_run: 281 ms.
  - A whole-project tree from MLflow is a scan of a few seconds; our edge table makes it one query.
- **P5 (gc):** gc orphaned a `champion`-aliased version, so gc must never be the retention mechanism.
- **Not prototyped:** DVC, lakeFS, Marquez, ZenML, Prefect and Dagster (judged on their docs); the AutoGluon slimming ratio.
