# F-X4b: agent principal (X4 C1), SQL source (C4), reset vs holds (M4), audit completeness and tail anchor (M6) · lane G

Base: `motion-components` @ 9c91e7e (X4 done). It was merged with upstream (F-X3a, U3) before the final passes.
Scripts and raw output are in `docs/progress/F-X4b/`:
- `out/base_*`: X4's own scripts run on the base commit (the repros);
- `out/{sqlite,postgres}_{enforce,audit}`: this lane's scripts run on the merged head.

**Verdict: all four findings are reproduced on the base and fixed on the head, on SQLite and Postgres 16, in enforce
and in audit mode. The legitimate paths still work.** All six API logs have 0 tracebacks and 0 HTTP 500s.

## What changed

| Finding | Where | Fix |
|---|---|---|
| **C1** | `agent/api.py`, `agent/loop.py`, `agent/tools.py`, `authz/identity.py`, `authz/deps.py` | **Session start.** `POST /agent/sessions` takes the user from the request principal: 401 with no principal, 403 if the caller is itself the agent. A body `user` that names someone else gets 409. The session stores `owner_id`, which is the caller's principal id.<br>**Signed header.** The agent's signed header now carries `(caller principal id, session id)` instead of a name. `AgentProvider` returns a principal with the caller's roles and `is_admin=False` (at most the caller's rights), `auth="agent"`, `on_behalf_of` = the caller and `agent_id` = the agent's own service principal ("Vela agent", kind `service`; a typed name never resolves to it).<br>**Session routes.** List, get, messages, confirm and stop all check ownership, in every authz mode. The owner gets everything. An admin may list and read, but acting gets 403 `not_session_owner`. Each refusal is audited as `agent_session_refused`.<br>**`by` check.** In enforce mode the body-`by` check no longer skips the agent. The agent's `by` must be "Vela agent on behalf of <caller>" or the caller's name (`deps.by_ok`).<br>Old sessions with no `owner_id` can be read by admins only, and nobody can act in them. |
| **C4** | `sqlsource/core.py`, `sqlsource/api.py`, `authz/policy.py` (+`data.import`), `datasets.py` (+`project_id` passthrough) | **Files.** SQLite and DuckDB files are allowed only under `ENSEMBLE_SQL_IMPORT_DIRS` (alias `ENSEMBLE_SQL_ROOTS`), plus the bundled demo file. A dir that is the data dir, inside it, or holding it is ignored. Paths are resolved, so symlinks are followed. The platform DB, `mlflow.db`, the `ENSEMBLE_DB_URL` / `MLFLOW_TRACKING_URI` SQLite files and anything under the data dir are refused (`sql_platform_db`).<br>**Postgres target.** It needs an explicit TCP host, a user, a non-empty password and a database. Any URL query option except `sslmode` is refused, so no `host=` socket and no `passfile`. The platform's own servers are refused (`ENSEMBLE_DB_URL`, `_OWNER_URL`, `MLFLOW_TRACKING_URI`: the same address and port; a socket or local server means every local address). Loopback, link-local and unspecified addresses are refused unless `ENSEMBLE_SQL_ALLOW_LOOPBACK=1`. `ENSEMBLE_SQL_ALLOWED_HOSTS` is an optional allow-list.<br>**Connection.** It uses psycopg on the checked address (`hostaddr`, no second DNS lookup) with `passfile=/dev/null` (per connection, so the app's own DB connection is untouched), no client certificate, `gssencmode=disable`, `require_auth=scram-sha-256,md5,password` (libpq ≥ 16; trust, peer and GSS are refused), and a READ ONLY transaction with a statement timeout. DuckDB opens with `enable_external_access=false`.<br>**Statement guard.** One SELECT / WITH only. It refuses any `pg_*` / `lo_*` / `dblink*` / `read_*` call, `query_to_xml` and the other SQL-in-a-string functions, `set_config` / `current_setting`, quoted-identifier calls, `U&` escapes, and `FROM '<file>'`.<br>**Routes.** `/sql/tables`, `/sql/preview` and `/sql/datasets` require `project_id` (400 `project_required`; no tm_default) and `data.import` (member) on that project. A load puts the dataset in that project. Every preview, table listing and refusal is audited (`sql_previewed`, `sql_tables_listed`, `sql_refused` with its code; the URL is redacted, and the query and its sha are recorded). |
| **M4** | `reset.py` | `check()` and the delete transaction both refuse with 409 `legal_hold` (`cases`, `holds`) while any hold is active. Legal holds are not in `TABLES`, so the rows are kept, and a released hold stays as history. The route's `admin_reset_refused` audit row is unchanged. |
| **M6** | `catalog/schema.py`, `catalog/audit_anchor.py` (new), `catalog/pg_schema.py`, `authz/api.py`, `main.py`, `lake/upload.py`, `engine/api.py`, `agent/*` | **Agent rows.** They carry `principal_id` = the agent's service principal, `auth` = `agent` and a new `on_behalf_of` column (the caller). This covers rows written in agent requests, `agent:<tool>` rows and background-job rows written as "Vela agent on behalf of X" (e.g. `batch_scored`). `on_behalf_of` is part of the row hash when set; older rows hash as before. `GET /teams/{t}/audit` returns it.<br>**New audit rows:** `agent_session_started` / `_message` / `agent_step_confirmed` / `_declined` / `agent_session_stopped` / `agent_session_refused` (the real caller's principal); `upload_discarded` (in `lake/upload.discard`, so the API and the wrapper's failure paths both write it); `run_cancelled`; and the SQL rows above.<br>**Tail truncation.** A new table, `audit_heads (team_id, audit_id, head, ts)`, gets one row per audit row in the same transaction. It is write-once: triggers `ahead_ro_u` / `ahead_ro_d`, Postgres twins, and `APPEND_ONLY` (the app role has SELECT and INSERT only). Existing databases are seeded with each team's current head.<br>**Signed checkpoints.** Every `ENSEMBLE_AUDIT_CHECKPOINT_SECONDS` (default 300; 0 = off), at start, and on `POST /api/admin/audit/checkpoint` (`admin.config`), each team's latest head is appended as an HMAC-signed line to `ENSEMBLE_AUDIT_CHECKPOINT_DIR/checkpoints.jsonl` (default `<data>/audit`). The key is `ENSEMBLE_AUDIT_CHECKPOINT_KEY`, else a 0600 key file created once.<br>**Verify.** `verify_audit_chain(con, team, data_dir)` now also fails when:<br>• the latest `audit_heads` row is not the chain's last row;<br>• the latest signed checkpoint's row is no longer in the chain with that hash;<br>• any checkpoint line fails its signature.<br>It reports `anchored {audit_heads, checkpoint}`. |

## Verification

**Set-up.**
- Two scratch Postgres 16 clusters, both from initdb:
  - platform: socket plus localhost on port 55432, trust auth. This is X4's set-up: `ENSEMBLE_DB_URL=postgresql://postgres@/<db>?host=…&port=55432`.
  - external: 192.0.2.2 and localhost on port 55433, SCRAM. Database `bankext`, user `extuser` / `extpw`, which was granted `pg_read_server_files` so that only the guard stops `pg_read_file`.
- The API runs with `PGPASSWORD=extpw` and `HOME/.pgpass` holding extuser's password, so that an inherited identity would show.
- Pinned `requirements.lock`, Python 3.13. People and data come from X4's `setup.py`.

### Reproduced on the base (X4's own scripts: `out/base_sqlite_enforce`, `out/base_postgres_enforce`)

```
C1  Fay POST /agent/sessions {user: Ben} → 201 (user Ben); Fay confirms → batch on Credit Risk's model,
    created_by "Vela agent on behalf of Ben"; Fay GET /agent/sessions lists [Ana, Ben]; Fay confirms Ana's step → 200, runs
C4  Fay sqlite:///mlflow/mlflow.db → 200 [pd-x4]
    (Postgres) Fay → platform DB: audit_log, principals (admins), shares → 200; batch_jobs.result → pii 200;
    pg_read_file('/etc/hostname') → 200 "vm"
M4  hold CASE-RESET active → POST /admin/reset → 200, everything deleted
M6  tail truncation: delete the last row and move catalog_meta audit_head back → verify ok=True
```

### Fixed on the head (`out/{sqlite,postgres}_{enforce,audit}/`)

**C1 (`agent.txt`).** Same in all four passes, except 3b and 1a in audit mode (see below).

```
2a Fay POST /agent/sessions {user: Ben}           → 409 "You are signed in as Fay; 'user' says Ben…"
2b anonymous                                      → 401
3  Fay's own session, batch on Credit Risk's model, Fay confirms
     enforce: step error 403 model.use (the agent has Fay's rights); no batch made
     audit:   done, with authz_would_deny auth=agent on_behalf_of=Fay (as Fay herself would get in audit mode)
5a Fay GET /agent/sessions → only Fay's | 5b–5e Fay GET / confirm / messages / stop on Ana's → 403 not_session_owner
6  Ada (admin) lists both owners, reads Ana's (200); confirm → 403 "an admin may read it but not act in it"
7  Ana confirms her own step → done; batch "Vela agent on behalf of Ana" succeeded        ← the agent as yourself
8  agent:batch_score / batch_scored / authz_denied: principal pr_0ae394f2b650 (Vela agent, service), auth agent,
   on_behalf_of = Ana's / Fay's id; agent_session_started / agent_step_confirmed / agent_session_refused: the real
   caller's principal; chains verify ok (anchored)
```

**C4 (`sql.txt`).** Enforce passes. Every "refused" below is a 400 with its code. The no-credentials cases ran with
`PGPASSWORD` and `.pgpass` set in the API's environment.

| Case | Result |
|---|---|
| `mlflow.db` / `ensemble.sqlite3` given as relative paths | Resolved into the import dir, where no such file exists. |
| Absolute data-dir paths: platform DB, `mlflow.db` | Refused, `sql_platform_db`. |
| A symlink in the import dir pointing at the platform DB | Refused, `sql_platform_db`. |
| `/etc/hosts` | Refused, `sql_path_refused`. |
| The platform's Postgres (Postgres passes): the socket URL | Refused, `sql_pg_option`. |
| The platform's Postgres: `localhost:55432` | Refused, `sql_host_refused`. |
| No host | Refused, `sql_pg_no_host`. |
| `?host=` socket, `?passfile=` | Refused, `sql_pg_option`. |
| No password, or an empty one | Refused, `sql_pg_no_credentials`. |
| Wrong password | The connection fails with "password authentication failed". There is no fallback to `.pgpass`. |
| `localhost:55433` | Refused, `sql_host_refused`. |
| `pg_read_file`, `"pg_read_file"`, `pg_catalog.pg_read_binary_file`, `lo_import`, `query_to_xml(…)`, `U&"…"`, `dblink` | Refused, `sql_function`. |
| `COPY … FROM PROGRAM` | Refused, `sql_error` (one SELECT only). |
| Two statements | Refused, `sql_error`. |
| No `project_id` | Refused, `project_required`. |
| Vic (viewer) or Fay into Credit Risk's project | 403 `data.import` in enforce; would-deny in audit. |

A separate probe on the fixed port parsing (`out/out_platform_port.txt`):
- `192.0.2.2:55432` and `localhost:55432` are refused as `sql_platform_db`, even with `ENSEMBLE_SQL_ALLOW_LOOPBACK=1`.
- The external server on `localhost:55433` is allowed with that flag.

An earlier version read the port only from the URL authority, not from `?port=`. In the passes, case 2e failed only
because the platform server doesn't listen on that address. This is fixed after the passes, and the probe shows it.

**Legitimate paths (`sql.txt` §6, all four passes).**
- External Postgres:
  - `/sql/tables` → 200;
  - preview → 200 (`postgresql://extuser:***@192.0.2.2:55433/bankext`);
  - with the password in `password` → 200 (`n: 500`);
  - `POST /sql/datasets {project_id: fp}` → 201, 500 rows, `datasets.project_id` = fp and team = Fraud.
- SQLite: a file in the import dir, the bundled demo and a relative path → 200.
- Audit rows: no password appears in any of them, and Fraud's chain verifies.

**M4 (`audit.txt` §2).** Same in all four passes:

```
2d Ada POST /admin/reset (hold CASE-RESET active, four-eyes approved) → 409 {code legal_hold, cases [CASE-RESET], holds […]}
2e/2f run and v1 still there (200)   | admin_reset_refused audited
2g Adam releases → 2h reset → 200    | 2i the hold row is kept (released_by Adam, active false)
```

**M6 (`audit.txt` §1, `tamper.txt`).**

Actions:
- `upload_discarded` and `run_cancelled` are now in Credit Risk's chain, with Ana's principal.
- The agent session, SQL preview and SQL refusal rows are present.
- Rows with `auth=agent` and no `on_behalf_of`: 0.

Tamper, run with the API stopped (same on all four passes):

```
UPDATE audit_log / UPDATE audit_heads / DELETE audit_heads           → refused by trigger
edit a middle row / delete a middle row                              → caught (as before)
delete the last 3 rows + rewind catalog_meta audit_head (X4's attack) → ok=False "the chain ends before the latest head
                                                                         recorded in audit_heads"
+ drop the audit_heads triggers and delete those heads               → ok=True without checkpoints;
                                                                       ok=False with them (GET …/verify): "a signed checkpoint
                                                                       names a row that is no longer in the chain"
edit a checkpoint line                                                → ok=False "1 checkpoint line(s) fail their signature"
restored                                                              → ok=True
```

Postgres with an app role (`out/out_pg_app_role.txt`, `pg_schema.grant(con, 'fx_app')`): `fx_app` has INSERT and
SELECT on `audit_heads`. DELETE and UPDATE get "permission denied". DISABLE TRIGGER and DROP TRIGGER get "must be
owner". DELETE on `audit_log` gets "permission denied".

No UI change, so no `npm run build`. The UI's `api.agent.start(m, user)` sends the acting name in both the header and
`user`, which matches the principal.

## Deviations and notes

- **The agent is never admin.** Its principal has the caller's roles with `is_admin=False`. This is within "at most
  the caller's rights".
- **Session ownership is enforced in every authz mode,** including audit. It is an identity binding, not a policy
  decision. Starting a session needs a principal (401 otherwise, even in audit).
- **Audit mode still allows cross-team agent actions,** with would-deny. That is the same as the caller doing them
  directly, and the would-deny row now carries `auth=agent` and `on_behalf_of`.
- **C4 refuses loopback by default.** Local development against a Postgres on localhost needs
  `ENSEMBLE_SQL_ALLOW_LOOPBACK=1`. The platform's own port stays refused even then.
- **`PGPASSFILE`** is set per connection (`passfile=/dev/null`), not in `os.environ`. Setting it there would also
  change the app's own catalog connection.
- **The tm_default team chain gets the agent session rows** when the step names no object. A confirmed step that names
  a run, dataset or version lands in that object's team.
- **Files of other lanes, minimal edits:**
  - `authz/deps.py`: `by_ok`, the agent no longer exempt (F-X4a);
  - `authz/policy.py`: `data.import`;
  - `authz/api.py`: `on_behalf_of` in the audit list, the checkpoint route, verify passes the data dir;
  - `engine/api.py`: the cancel audit row (3 lines);
  - `lake/upload.py`: `discard` audits (F-X3a; merged cleanly);
  - `datasets.py`: the `project_id` passthrough;
  - `main.py`: the checkpoint timer.
- **For F-X4a:** `GET /agent/sessions` and `GET /agent/sessions/{sid}` now carry `require("agent.use")` plus the
  ownership check.
- **Deployment:**
  - Put `ENSEMBLE_AUDIT_CHECKPOINT_DIR` on write-once storage, or ship it to a log the DB owner can't write.
  - Put `ENSEMBLE_AUDIT_CHECKPOINT_KEY` in a secret store. A key file next to the checkpoints only protects against
    someone who can't read that directory.
  - Set `ENSEMBLE_SQL_IMPORT_DIRS`; with none set, only the demo file is readable.
- **P23 / migrate_pg:** `audit_heads` is copied like any table, with no sequence. `on_behalf_of` is a TEXT column on
  `audit_log` and must be copied verbatim.
- **Scratch:** the venv, the clusters, the work dirs and the base worktree were under `/var/tmp/fx4b`. They are
  removed after the push.
