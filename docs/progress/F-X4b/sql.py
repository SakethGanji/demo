"""F-X4b: X4 C4 (the SQL source reaching the platform's databases) after the fix, plus the legitimate paths.

The API runs with credentials for the external database in its own environment (PGPASSWORD, ~/.pgpass: api.sh) and an
import dir (ENSEMBLE_SQL_IMPORT_DIRS = <work>/import). External database: a separate scratch Postgres cluster on
192.0.2.2:55433, database bankext, user extuser / extpw (granted pg_read_server_files, so only the statement guard stops
pg_read_file). Fay (Fraud member) works in her own project `fp`.

    X4_DATA=<data dir> X4_WORK=<work dir> [X4_PLATFORM_URL=postgresql://…] python sql.py <base> <state.json>"""
import os
import shutil
import sqlite3

from x4lib import audit_rows, get, load, post, show

st = load()
fp, pd, fr = st["fp"], st["pd"], st["fr"]
DATA, WORK = os.environ["X4_DATA"], os.environ["X4_WORK"]
IMP = os.path.join(WORK, "import")
PLATFORM = os.environ.get("X4_PLATFORM_URL")
EXT = "postgresql://extuser:extpw@192.0.2.2:55433/bankext"


def pv(n, who, url, q="SELECT 1 AS x", project=fp, password=None, keys=("rows", "n_rows", "detail"), cut=260):
    body = {"url": url, "query": q}
    if password:
        body["password"] = password
    return show(n, post(who, "/sql/preview" + (f"?project_id={project}" if project else ""), body), list(keys), cut=cut)


print(f"# mode {st['mode']}")
print("\n# 1. X4's repro: files under the data dir")
pv("1a Fay sqlite:///mlflow/mlflow.db (registered_models)", "Fay", "sqlite:///mlflow/mlflow.db", "SELECT name, description FROM registered_models")
pv("1b Fay sqlite:///ensemble.sqlite3", "Fay", "sqlite:///ensemble.sqlite3")
pv("1c Fay sqlite:////<abs data dir>/ensemble.sqlite3", "Fay", f"sqlite:///{DATA}/ensemble.sqlite3", "SELECT actor FROM audit_log LIMIT 2")
pv("1d Fay sqlite:////<abs data dir>/mlflow/mlflow.db", "Fay", f"sqlite:///{DATA}/mlflow/mlflow.db", "SELECT name FROM registered_models")
os.makedirs(IMP, exist_ok=True)
link = os.path.join(IMP, "innocent.sqlite")
if os.path.lexists(link):
    os.unlink(link)
os.symlink(os.path.join(DATA, "ensemble.sqlite3"), link)
pv("1e Fay sqlite:///<import>/innocent.sqlite (a symlink to the platform DB)", "Fay", f"sqlite:///{link}", "SELECT actor FROM audit_log LIMIT 2")
pv("1f Fay sqlite:////etc/hosts (outside the import dir)", "Fay", "sqlite:////etc/hosts")

if PLATFORM:
    print("\n# 2. X4's repro: the platform's own Postgres (the server's connection settings: socket + trust)")
    for qy in ("SELECT actor, action, team_id FROM audit_log ORDER BY id DESC LIMIT 3",
               "SELECT display_name, is_admin FROM principals WHERE is_admin = 1",
               "SELECT pg_read_file('/etc/hostname') AS f"):
        pv(f"2 Fay {PLATFORM.split('@')[-1]} {qy[:45]}…", "Fay", PLATFORM, qy)
    db = PLATFORM.split("/")[-1].split("?")[0]
    pv("2d Fay postgresql://postgres:x@localhost:55432/<platform db> (same server over TCP)", "Fay",
       f"postgresql://postgres:x@localhost:55432/{db}", "SELECT count(*) FROM audit_log")
    pv("2e Fay postgresql://postgres:x@192.0.2.2:55432/<platform db> (a local address of the platform server)", "Fay",
       f"postgresql://postgres:x@192.0.2.2:55432/{db}", "SELECT count(*) FROM audit_log")

print("\n# 3. no inherited identity: the API process holds PGPASSWORD=extpw and a ~/.pgpass for the external DB")
pv("3a no host (socket default)", "Fay", "postgresql://extuser@/bankext")
pv("3b socket in the query", "Fay", "postgresql://extuser:extpw@/bankext?host=/var/tmp/fx4b/sock&port=55433")
pv("3c no password (PGPASSWORD / .pgpass would supply it)", "Fay", "postgresql://extuser@192.0.2.2:55433/bankext")
pv("3d empty password", "Fay", "postgresql://extuser:@192.0.2.2:55433/bankext")
pv("3e wrong password (no fallback to .pgpass)", "Fay", "postgresql://extuser:wrong@192.0.2.2:55433/bankext")
pv("3f loopback address of the external server", "Fay", "postgresql://extuser:extpw@localhost:55433/bankext")
pv("3g passfile option in the URL", "Fay", "postgresql://extuser:x@192.0.2.2:55433/bankext?passfile=/tmp/p")

print("\n# 4. statement guard on an allowed external DB (extuser may pg_read_file there)")
for n, q in (("4a pg_read_file", "SELECT pg_read_file('/etc/hostname') AS f"),
             ("4b quoted pg_read_file", "SELECT \"pg_read_file\"('/etc/hostname') AS f"),
             ("4c pg_catalog.pg_read_binary_file", "SELECT pg_catalog.pg_read_binary_file('/etc/hostname') AS f"),
             ("4d lo_import", "SELECT lo_import('/etc/hostname') AS f"),
             ("4e query_to_xml (SQL in a string)", "SELECT query_to_xml('select pg_read_file(''/etc/hostname'')', true, true, '') AS f"),
             ("4f COPY … FROM PROGRAM", "COPY applicants FROM PROGRAM 'id'"),
             ("4g two statements", "SELECT 1; SELECT pg_read_file('/etc/hostname')"),
             ("4h U& identifier", "SELECT U&\"\\0070g_read_file\"('/etc/hostname')"),
             ("4i dblink", "SELECT * FROM dblink('dbname=postgres', 'select 1') AS t(x int)")):
    pv(n, "Fay", EXT, q)

print("\n# 5. project and right")
pv("5a no project_id", "Fay", EXT, "SELECT id FROM applicants LIMIT 2", project=None)
pv("5b Vic (Credit Risk viewer) into Credit Risk's project", "Vic", EXT, "SELECT id FROM applicants LIMIT 2", project=pd)
pv("5c Fay into Credit Risk's project", "Fay", EXT, "SELECT id FROM applicants LIMIT 2", project=pd)
show("5d Fay POST /sql/tables without project_id", post("Fay", "/sql/tables", {"url": EXT}), cut=200)

print("\n# 6. the legitimate paths")
show("6a Fay POST /sql/tables?project_id=fp (external Postgres)", post("Fay", f"/sql/tables?project_id={fp}", {"url": EXT}), ["tables"], cut=300)
pv("6b Fay preview (external Postgres)", "Fay", EXT, "SELECT id, income, segment FROM applicants WHERE income > 36000 ORDER BY id",
   keys=("n_rows", "dialect", "url", "rows"), cut=400)
pv("6c Fay preview with the password in `password`", "Fay", "postgresql://extuser@192.0.2.2:55433/bankext", "SELECT count(*) AS n FROM applicants",
   password="extpw", keys=("rows", "url"))
r = show("6d Fay POST /sql/datasets {project_id: fp} (load)", post("Fay", "/sql/datasets", {
    "url": EXT, "query": "SELECT id, income, segment FROM applicants", "project_id": fp, "name": "ext applicants", "created_by": "Fay"}),
    ["id", "name", "rows", "project_id", "team_id"], cut=300)
if isinstance(r, dict) and r.get("id"):
    show("6e GET /datasets/{id} (as Fay)", get("Fay", f"/datasets/{r['id']}"), ["id", "rows"], cut=200)
    DB = os.environ["X4_DB"]
    if DB.startswith("postgres"):
        import psycopg
        with psycopg.connect(DB) as con_:
            got = con_.execute("SELECT project_id, team_id FROM datasets WHERE id = %s", (r["id"],)).fetchone()
    else:
        with sqlite3.connect(DB) as con_:
            got = con_.execute("SELECT project_id, team_id FROM datasets WHERE id = ?", (r["id"],)).fetchone()
    print(f"    datasets row: project_id {got[0]} (fp = {fp}), team_id {got[1]} (Fraud = {fr})")
shutil.copy(os.path.join(DATA, "demo", "bank.sqlite"), os.path.join(IMP, "bank_copy.sqlite"))
pv("6f Fay sqlite file in the import dir", "Fay", f"sqlite:///{IMP}/bank_copy.sqlite", "SELECT count(*) AS n FROM applications", keys=("rows", "dialect"))
pv("6g Fay the bundled demo database", "Fay", "sqlite:///demo/bank.sqlite", "SELECT count(*) AS n FROM applications", keys=("rows", "dialect"))
con = sqlite3.connect(os.path.join(IMP, "plain.sqlite"))
con.execute("CREATE TABLE IF NOT EXISTS t (a int, b text)")
con.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')")
con.commit()
con.close()
pv("6h Fay a relative path → the import dir", "Fay", "sqlite:///plain.sqlite", "SELECT * FROM t", keys=("rows", "dialect"))

print("\n# 7. audit rows in Fraud's chain")
for r in audit_rows(fr, limit=500):
    if r["action"].startswith("sql_") or r["action"] == "dataset_loaded":
        d = r.get("details") or {}
        print(f"  {r['action']:<18} {r['actor']:<5} principal={r.get('principal_id')} project={r.get('project_id')} code={d.get('code')} "
              f"url={d.get('url')} q={str(d.get('query'))[:50]!r} rows={d.get('rows')}")
print("  any password in the audit rows:", any("extpw" in str(r) for r in audit_rows(fr, limit=500)))
print("  verify fraud:", get("Ada", f"/teams/{fr}/audit/verify").json().get("ok"))
