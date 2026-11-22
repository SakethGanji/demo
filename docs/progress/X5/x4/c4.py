"""X5 (lane x4): X4 C4 re-check, adversarial. The SQL source must never reach the platform's databases (the SQLite file,
mlflow.db, anything under the data dir; the platform's Postgres server under any address alias) nor use the API's own
identity, nor run server-side file functions. Fay (Fraud member) in her own project `fp`.

External scratch cluster (X5): 192.0.2.2:8629, SCRAM, bankext / extuser / extpw (granted pg_read_server_files).
The API runs with PGPASSWORD=extpw and a ~/.pgpass '*:*:*:*:extpw' (api.sh). Platform Postgres (when X4_PLATFORM_URL):
127.0.0.1:5433, trust auth.

    X4_DATA=<data dir> X4_WORK=<work dir> [X4_PLATFORM_URL=…] PYTHONPATH=…/F-X4c python c4.py <base> <state.json>"""
import os
import sqlite3

from x4lib import audit_rows, get, load, post, show

st = load()
fp, pd, fr = st["fp"], st["pd"], st["fr"]
DATA, WORK = os.environ["X4_DATA"], os.environ["X4_WORK"]
IMP = os.path.join(WORK, "import")
os.makedirs(IMP, exist_ok=True)
PLATFORM = os.environ.get("X4_PLATFORM_URL")
EXT = "postgresql://extuser:extpw@192.0.2.2:8629/bankext"


def pv(n, url, q="SELECT 1 AS x", who="Fay", project=None, password=None, cut=220):
    body = {"url": url, "query": q}
    if password:
        body["password"] = password
    project = fp if project is None else project
    r = post(who, "/sql/preview" + (f"?project_id={project}" if project else ""), body)
    try:
        j = r.json()
    except ValueError:
        j = {"text": r.text[:200]}
    d = j.get("detail") if isinstance(j, dict) else None
    code = (d or {}).get("code") if isinstance(d, dict) else None
    msg = (d or {}).get("message") if isinstance(d, dict) else d
    rows = j.get("rows") if isinstance(j, dict) else None
    print(f"## {n} → HTTP {r.status_code} | code {code} | {str(msg)[:140] if msg else ''} | rows {str(rows)[:cut] if rows is not None else '-'}")
    return r


print(f"# mode {st['mode']} | ENSEMBLE_SQL_ALLOW_LOOPBACK={os.environ.get('X5_LOOPBACK', '0')} (as the API was started)")
print("\n# 1. files under the data dir and path tricks")
pv("1a sqlite:///mlflow/mlflow.db", "sqlite:///mlflow/mlflow.db", "SELECT name FROM registered_models")
pv("1b sqlite:///ensemble.sqlite3", "sqlite:///ensemble.sqlite3", "SELECT actor FROM audit_log LIMIT 2")
pv("1c abs <data>/ensemble.sqlite3", f"sqlite:///{DATA}/ensemble.sqlite3", "SELECT actor FROM audit_log LIMIT 2")
pv("1d abs <data>/mlflow/mlflow.db", f"sqlite:///{DATA}/mlflow/mlflow.db", "SELECT name FROM registered_models")
pv("1e <import>/../data/ensemble.sqlite3 (dot-dot)", f"sqlite:///{IMP}/../data/ensemble.sqlite3", "SELECT actor FROM audit_log LIMIT 2")
pv("1f relative ../data/ensemble.sqlite3", "sqlite:///../data/ensemble.sqlite3", "SELECT actor FROM audit_log LIMIT 2")
pv("1g <data>//./ensemble.sqlite3", f"sqlite:///{DATA}//./ensemble.sqlite3", "SELECT 1")
pv("1h duckdb:/// abs <data>/ensemble.sqlite3", f"duckdb:///{DATA}/ensemble.sqlite3", "SELECT 1")
pv("1i sqlite file: URI with ?mode=ro", f"sqlite:///file:{DATA}/ensemble.sqlite3?mode=ro&uri=true", "SELECT 1")
link = os.path.join(IMP, "innocent.sqlite")
if os.path.lexists(link):
    os.unlink(link)
os.symlink(os.path.join(DATA, "ensemble.sqlite3"), link)
pv("1j symlink in import dir → platform DB", f"sqlite:///{link}", "SELECT actor FROM audit_log LIMIT 2")
dl = os.path.join(IMP, "datadir")
if not os.path.lexists(dl):
    os.symlink(DATA, dl)
pv("1k symlinked dir in import dir → data dir / mlflow.db", f"sqlite:///{dl}/mlflow/mlflow.db", "SELECT name FROM registered_models")
pv("1l /etc/hosts", "sqlite:////etc/hosts")
# DuckDB file in the import dir: can a query reach out of it?
import duckdb  # noqa: E402

dpath = os.path.join(IMP, "x5.duckdb")
if not os.path.exists(dpath):
    dc = duckdb.connect(dpath)
    dc.execute("CREATE TABLE t AS SELECT 1 AS a")
    dc.close()
plat = os.path.join(DATA, "ensemble.sqlite3")
pv("1m duckdb sqlite_scan(<platform db>)", f"duckdb:///{dpath}", f"SELECT * FROM sqlite_scan('{plat}', 'principals')")
pv("1n duckdb read_csv_auto('/etc/passwd')", f"duckdb:///{dpath}", "SELECT * FROM read_csv_auto('/etc/passwd')")
pv("1o duckdb FROM '<platform db>' (replacement scan)", f"duckdb:///{dpath}", f"SELECT * FROM '{plat}'")
pv("1p duckdb glob('/etc/*')", f"duckdb:///{dpath}", "SELECT * FROM glob('/etc/*')")
pv("1q sqlite ATTACH inside a WITH", "sqlite:///demo/bank.sqlite", f"WITH x AS (SELECT 1) SELECT * FROM x; ATTACH '{plat}' AS p")
pv("1r legit: duckdb file in the import dir", f"duckdb:///{dpath}", "SELECT * FROM t")
pv("1s legit: bundled demo", "sqlite:///demo/bank.sqlite", "SELECT count(*) AS n FROM applications")

print("\n# 2. the platform Postgres server under every alias (127.0.0.1:5433, trust)")
dbs = ["x5x4", "x5main", "postgres"]
if PLATFORM:
    dbs.insert(0, PLATFORM.split("/")[-1].split("?")[0])
pv("2a the platform URL itself (no password)", PLATFORM or "postgresql://postgres@127.0.0.1:5433/x5x4", "SELECT count(*) FROM audit_log")
for host in ("127.0.0.1", "localhost", "127.0.0.2", "127.1", "2130706433", "0x7f000001", "0.0.0.0", "0", "[::1]", "[::ffff:127.0.0.1]",
             "[::]", "192.0.2.2", "vm", "localhost."):
    pv(f"2b postgres:x@{host}:5433/{dbs[0]}", f"postgresql://postgres:x@{host}:5433/{dbs[0]}", "SELECT count(*) FROM audit_log")
pv("2c another DB on the platform server (x5main)", "postgresql://postgres:x@127.0.0.1:5433/x5main", "SELECT 1")
pv("2d socket via ?host=", f"postgresql://postgres:x@/{dbs[0]}?host=/var/run/postgresql&port=5433", "SELECT 1")
pv("2e multi-host authority", f"postgresql://postgres:x@192.0.2.2:8629,127.0.0.1:5433/{dbs[0]}", "SELECT 1")
pv("2f options= in query", f"postgresql://postgres:x@192.0.2.2:8629/bankext?options=-csearch_path%3Dpublic", "SELECT 1")
pv("2g no password (PGPASSWORD / .pgpass in the API env)", "postgresql://extuser@192.0.2.2:8629/bankext")
pv("2h empty password", "postgresql://extuser:@192.0.2.2:8629/bankext")
pv("2i wrong password", "postgresql://extuser:wrong@192.0.2.2:8629/bankext")
pv("2j sslmode=disable (allowed option)", "postgresql://extuser:wrong@192.0.2.2:8629/bankext?sslmode=disable")

print("\n# 3. statement guard on the allowed external DB (extuser holds pg_read_server_files)")
for n, q in (("3a pg_read_file", "SELECT pg_read_file('/etc/hostname') AS f"),
             ("3b quoted", "SELECT \"pg_read_file\"('/etc/hostname') AS f"),
             ("3c schema-qualified binary", "SELECT pg_catalog.pg_read_binary_file('/etc/hostname') AS f"),
             ("3d lo_import", "SELECT lo_import('/etc/hostname') AS f"),
             ("3e query_to_xml", "SELECT query_to_xml('select 1', true, true, '') AS f"),
             ("3f COPY FROM PROGRAM", "COPY applicants FROM PROGRAM 'id'"),
             ("3g two statements", "SELECT 1; SELECT 2"),
             ("3h U& ident", "SELECT U&\"\\0070g_read_file\"('/etc/hostname')"),
             ("3i comment-split call", "SELECT pg_read_file/**/('/etc/hostname') AS f"),
             ("3j newline-split call", "SELECT pg_read_file\n('/etc/hostname') AS f"),
             ("3k pg_ls_dir via LATERAL", "SELECT * FROM pg_ls_dir('/') AS d"),
             ("3l current_setting", "SELECT current_setting('data_directory') AS d"),
             ("3m pg_settings view (no call)", "SELECT name, setting FROM pg_settings WHERE name = 'data_directory'"),
             ("3n pg_authid / pg_shadow (no call)", "SELECT usename, passwd FROM pg_shadow"),
             ("3o pg_stat_activity (other sessions' queries)", "SELECT usename, left(query, 60) AS q FROM pg_stat_activity"),
             ("3p xpath/ts_stat (SQL in string)", "SELECT * FROM ts_stat('select to_tsvector(pg_read_file(''/etc/hostname''))')"),
             ("3q write via CTE", "WITH d AS (DELETE FROM applicants RETURNING *) SELECT count(*) FROM d"),
             ("3r SELECT INTO", "SELECT * INTO newt FROM applicants"),
             ("3s nextval side effect", "SELECT nextval('applicants_id_seq')"),
             ("3t pg_sleep (DoS)", "SELECT pg_sleep(30)")):
    pv(n, EXT, q)

print("\n# 4. project and right")
pv("4a no project_id", EXT, "SELECT id FROM applicants LIMIT 2", project="")
pv("4b Vic (CR viewer) into CR's project", EXT, "SELECT id FROM applicants LIMIT 2", who="Vic", project=pd)
pv("4c Fay into CR's project", EXT, "SELECT id FROM applicants LIMIT 2", project=pd)
show("4d Fay POST /sql/tables (no project)", post("Fay", "/sql/tables", {"url": EXT}), cut=200)

print("\n# 5. legitimate")
show("5a Fay /sql/tables?project_id=fp (external)", post("Fay", f"/sql/tables?project_id={fp}", {"url": EXT}), ["tables"], cut=300)
pv("5b Fay preview external", EXT, "SELECT id, income, segment FROM applicants ORDER BY id LIMIT 3")
pv("5c Fay preview with password field", "postgresql://extuser@192.0.2.2:8629/bankext", "SELECT count(*) AS n FROM applicants",
   password="extpw")
r = show("5d Fay POST /sql/datasets {project fp}", post("Fay", "/sql/datasets", {"url": EXT, "query": "SELECT id, income, segment FROM applicants",
                                                                                "project_id": fp, "name": "ext applicants", "created_by": "Fay"}),
         ["id", "name", "rows", "project_id", "team_id"], cut=300)
con = sqlite3.connect(os.path.join(IMP, "plain.sqlite"))
con.execute("CREATE TABLE IF NOT EXISTS t (a int, b text)")
con.execute("DELETE FROM t")
con.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')")
con.commit()
con.close()
pv("5e relative path → import dir", "sqlite:///plain.sqlite", "SELECT * FROM t")

print("\n# 6. audit rows (Fraud chain)")
rows = [x for x in audit_rows(fr, limit=2000) if x["action"].startswith("sql_") or x["action"] == "dataset_loaded"]
import collections  # noqa: E402

print("  counts:", dict(collections.Counter((x["action"], (x.get("details") or {}).get("code")) for x in rows)))
print("  any password in audit rows:", any("extpw" in str(x) for x in audit_rows(fr, limit=2000)))
print("  verify fraud:", get("Ada", f"/teams/{fr}/audit/verify").json().get("ok"))
