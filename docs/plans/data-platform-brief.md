# Data platform re-plan: brief for the planning sessions (2026-11-21)

## User decisions (fixed)
- **Storage:** every upload is converted to **Parquet**; Parquet is the stored format. CSV is kept only for export. **DuckDB** is the query engine for joins, diffs, previews and profiling, over local disk or S3.
- **Hierarchy:** **team → project → dataset → version → tables**. A dataset may have **multiple tables/sheets**, for example an Excel file with several sheets, or related tables such as applications, bureau and payments.
- **No production database connection.** Data arrives as **file exports** uploaded by people:
  - full extracts;
  - delta files, possibly;
  - an optional watched drop folder later.
- **Every change creates a new version; nothing is modified in place.** This covers uploads, in-app edits, Data Doctor fixes, Feature Lab and relabels. Changes made outside the app must be detected (hash checks).
- **Scale:** many teams, each with many projects, datasets and versions. Thousands of runs. Tables from thousands to tens of millions of rows. Must "scale very high".
- **Build/buy:** no heavy new platforms unless the scale demands it. Parquet + DuckDB + our metadata database (SQLite now, Postgres for multi-user) is the core. lakeFS / Iceberg / Delta are allowed only as an evaluated escalation path. See docs/platform-eval.md.
- **Identity:** names are not authenticated today (no SSO in the POC). Team permissions must be designed in and checked in code, with enforcement switched on once sign-in exists.
- **Verification rule for later build sessions:** no smoke/integration suites. Verify with manual API calls chained the way the UI calls them, plus `npm run build`.

## What exists today (read the code, don't trust this summary)
- **Storage:** backend/app/datasets.py holds `create_version`, `derive` and `root_rows`. Each version is a folder `data/datasets/<id>/` containing data.csv, splits.json, splits/, lineage.json, and possibly TOMBSTONE.json.
- **Metadata:** backend/app/db.py holds the tables datasets, runs, run_events, registered_models, model_versions, audit_log, batch_jobs, projects, lineage_edges, annotations, champions, dataset_cards and dataset_requests. backend/app/catalog/ holds dsets.py, datasets_api.py, dataset_versions.py and schema.py.
- **Edits and diffs:**
  - Edit-as-version: POST /api/datasets/{id}/edit with typed ops.
  - Upload-as-version, with preview, contract warnings and a row key / row hash for matching.
  - Diff by root_row.
- **Integrity:**
  - sha256 per version;
  - train-time data_sha256 on each run;
  - split hashing in progress in session DL4.
- **Governance:** retire / archive / four-eyes delete; tombstones; audit; read audit.
- **Consumers of a dataset version:**
  - training: AutoGluon reads a pandas DataFrame;
  - holdout and folds;
  - Data Doctor, Model Doctor, What's missing, Feature Lab, ladder, review experiments;
  - registry batches and monitoring, SQL refresh;
  - the anomaly, forecast, survival, EL, segmented, reject-inference and multilabel kinds;
  - export and lineage tree;
  - paired compare by root rows.
- **Known pain:**
  - CSV loses types (leading zeros, int→float);
  - full CSV copy per version;
  - single table per version;
  - no teams;
  - SQLite single-writer.

Reports:
- docs/progress/DL1.md, DL2.md, DL3.md, DLA1.md, DLA2.md, DLA3.md (dataset layer);
- docs/progress/P1.md, F-P2.md, PA1.md, A4.md (platform);
- docs/platform-eval.md (build vs adopt).

## Output format
Each planning session writes its plan to **docs/plans/<lens>.md** and commits **only that file** to motion-components. Pull with merge first, never rebase or force, and open no PR.

The plan must contain:
1. the concrete schema: DB tables, columns and keys; Parquet file layout and naming; manifest format;
2. the key algorithms;
3. scale numbers, measured where possible;
4. a migration path from today's code, with each affected file and module named;
5. risks;
6. a phased build plan in sessions of about 2 h each, every phase with its manual API verification.

Commit messages end with:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018upNdRigdGd9A2nGzFSQXq
