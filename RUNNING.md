# Running ENSEMBLE: setup, test and hand-off guide

- **Repository:** `https://github.com/SakethGanji/demo`
- **Branch:** `motion-components`, which holds all current work (data platform, labeling, UI polish).

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **3.12** recommended | 3.13 can fail to install scipy/numpy wheels. Use `PYTHON=python3.12` if so. |
| Node.js | 22.x | Provides `npm` / `npx` for the Vite UI. |
| git | any | |
| uv | optional | Makes `make setup` much faster (`pip install uv`). |
| Postgres | 16, optional | Only for `make dev-pg`. macOS: `brew install postgresql@16`. Ubuntu: `apt install postgresql-16`. |

Disk: about 6 GB for the Python environment (AutoGluon). RAM: 8 GB minimum, 16 GB comfortable.

## 2. Get the code

```bash
git clone https://github.com/SakethGanji/demo.git
cd demo
git checkout motion-components
git pull
```

## 3. Install (once, about 5–10 minutes)

```bash
make setup
```

If Python 3.13 fails, use 3.12 instead:

```bash
make setup PYTHON=python3.12
```

This creates `backend/.venv`, installs `backend/requirements.lock` and the Python client, and runs `npm install`.

## 4. Run

### SQLite (simplest)

```bash
ENSEMBLE_POC_MODE=1 make dev
```

- UI: http://localhost:5190
- API: http://localhost:8000 (health: `/api/health`)
- Ctrl+C stops both.

### Postgres

```bash
ENSEMBLE_POC_MODE=1 make dev-pg
```

The first time, this creates a local cluster in `~/.ensemble/pg` on port 55432, with a generated password kept outside git. Stop it with:

```bash
make dev-pg-stop
```

In the UI, type any name in **Acting as** (top bar), for example `me`.

### What `ENSEMBLE_POC_MODE=1` does

It lets one person test everything:
- four-eyes rules are off, so you can approve your own requests;
- every name counts as admin and owner, and every permission check passes;
- the status bar shows **▲ POC mode**.

Masking, integrity checks, legal holds, acknowledgements and the audit log stay on. **Never set it on a shared deployment.**

A fresh install starts empty: there are no built-in demo datasets. Upload your own data, or use the files in `try-data/`.

## 5. Try it

Use the test data in `try-data/`. `try-data/TRY.md` has the 15-minute walkthrough, including the LLM SQL query and prompt template.

| File | What it holds |
|---|---|
| `try-data/loans_book.xlsx` | Two sheets. **applications**: 1,500 rows, target `defaulted` yes/no, 250 left blank to label. **payments**: 6,101 instalments. |
| `try-data/bureau.csv` | 1,200 customers |
| `try-data/applications_changes.csv` | A change file: 20 updates, 5 deletes, 10 inserts (`_op` column) |

Quick path:
1. Upload `loans_book.xlsx` and `bureau.csv` together, set the keys and the PII columns, and create the version.
2. Apply `applications_changes.csv` as a delta, then compare the versions.
3. Join the three tables, train, and register the model.
4. On the Labels tab, set up a task and label rows by keyboard.
5. LLM labeling from a SQL query: test on 5 rows, save, calibrate, run, accept.
6. Take a snapshot and retrain on it. Edit a row and watch its label turn stale.
7. Legal hold, then delete (refused), then purge `ssn`.

**LLM:**
- The **fake** destination works offline, for free.
- For a real model, set `ANTHROPIC_API_KEY` (or point `ENSEMBLE_ANTHROPIC_BASE_URL` at an approved gateway) before `make dev`.
- `ENSEMBLE_VELA_LLM=off` disables every LLM call.

## 6. Useful settings

| Variable | Default | Purpose |
|---|---|---|
| `ENSEMBLE_POC_MODE` | off | Single-person testing (see above). |
| `ENSEMBLE_DB_URL` | SQLite | e.g. `postgresql://user:pw@127.0.0.1:55432/ensemble`; `make dev-pg` sets it. |
| `ENSEMBLE_DATA_DIR` | `backend/data` | Where the database and lake live. |
| `ENSEMBLE_AUTHZ` | `audit` | `audit` logs would-deny; `enforce` blocks (needs real users). |
| `ENSEMBLE_ADMINS` | none | Comma-separated admin names, when POC mode is off. |
| `ENSEMBLE_BLOB_STORE` | local | `s3://bucket/prefix` with `ENSEMBLE_S3_ENDPOINT`, for MinIO/S3. |
| `ENSEMBLE_SQL_IMPORT_DIRS` | none | Folders the SQL import may read. |
| `ANTHROPIC_API_KEY` | none | A real LLM, for labeling and narration. |
| `ENSEMBLE_SHUTDOWN_GRACE_S` | 20 | Seconds before shutdown stops running work. |

Ports: `make dev API_PORT=8001 UI_PORT=5191` if 8000 or 5190 is taken.

## 7. Troubleshooting

- **The UI says "API offline".** The API isn't up yet or crashed. Look at the terminal, or open `curl localhost:8000/api/health`.
- **`make setup` fails on scipy/numpy.** Use `PYTHON=python3.12`.
- **Postgres not found by `make dev-pg`.** Install Postgres 16 and make sure `pg_ctl` is on PATH.
- **Start fresh.** Stop the app, then `rm -rf backend/data` (SQLite), or `make dev-pg-stop && rm -rf ~/.ensemble/pg`.

## 8. Hand-off for a Claude Code session on another machine

Start Claude Code in the cloned repository, on branch `motion-components`, and point it at:

- `RUNNING.md`: this file.
- `docs/plans/build-tracker.md`: the phase status table, rules for build sessions, and every carried note and known gap.
- `docs/plans/data-platform-final.md`: the data platform design (Parquet + DuckDB lake, versions, teams, governance).
- `docs/plans/labeling.md` and `docs/plans/labeling-review.md`: the labeling design, its independent review, and the remaining phase list.
- `docs/progress/*.md`: one report per build or check session, including what was verified and what wasn't. Final checks: `X5.md`, `X5b.md`, `LB-X.md`, `LB-PG.md`.

Working rules used so far:
- Commit on `motion-components`; pull with merge, never rebase or force; no PRs unless asked.
- Verify by running the real flow through the API, plus `npm run build` for UI changes.
- Never commit credentials.

### Known gaps, as of this commit

**Labeling:**
- per-class calibration bounds;
- a post-run audit sample;
- LLM batch mode;
- a snapshot approval route;
- the rules UI, label import, snapshot diff;
- credit outcome windows and label drift (phase L10).

**Platform:**
- `POST /runs` and the registration parity check hold about 3 GB in the request at 10M rows;
- the 10M Model Doctor takes about 6 minutes;
- names are not authenticated identities (SSO later);
- upstream MinIO OSS is archived, so pick a maintained S3 store.

**Untested:** a real LLM endpoint (all tests used the fake destination).
