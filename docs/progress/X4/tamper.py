"""X4: tamper with a team's audit chain in the scratch database (the API stopped), then run verify_audit_chain, the function
behind GET /teams/{t}/audit/verify. The append-only triggers are switched off first (a database owner can do that).

    cd backend && X4_DB=<sqlite file | postgresql://…> python …/tamper.py <team id>"""
import os
import sys

sys.path.insert(0, os.getcwd())
from app.catalog.schema import verify_audit_chain  # noqa: E402
from app.db import Database  # noqa: E402

TEAM = sys.argv[1]
URL = os.environ["X4_DB"]
PG = URL.startswith("postgres")
from pathlib import Path  # noqa: E402

db = Database(Path("/tmp/x4_unused.sqlite3"), url=URL) if PG else Database(Path(URL), url="")


def q(sql, a=(), fetch=False):
    with db.connect() as con:
        cur = con.execute(sql, a)
        return [tuple(r) for r in cur.fetchall()] if fetch else None


def verify(tag):
    with db.connect() as con:
        r = verify_audit_chain(con, TEAM)
    print(f"  {tag}: ok={r['ok']} rows={r['rows']} first_break={r.get('first_break')}")


verify("clean")
ids = [r[0] for r in q("SELECT id FROM audit_log WHERE team_id = ? AND row_hash IS NOT NULL ORDER BY id", (TEAM,), True)]
mid = ids[len(ids) // 2]
try:
    q("UPDATE audit_log SET comment = comment WHERE id = ?", (mid,))
    print("  UPDATE accepted (no trigger)")
except Exception as e:  # noqa: BLE001
    print(f"  UPDATE audit_log refused: {type(e).__name__}: {str(e).splitlines()[0]}")
if PG:
    q("ALTER TABLE audit_log DISABLE TRIGGER USER")
    cols = [r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log' ORDER BY ordinal_position", (), True)]
else:
    trg = q("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'audit_log'", (), True)
    for n in ("audit_ro_u", "audit_ro_d"):
        q(f"DROP TRIGGER IF EXISTS {n}")
    cols = [r[1] for r in q("PRAGMA table_info(audit_log)", (), True)]
print("  (append-only triggers off)")
old = q("SELECT comment FROM audit_log WHERE id = ?", (mid,), True)[0][0]
q("UPDATE audit_log SET comment = ? WHERE id = ?", ((old or "") + " ", mid))
verify(f"edited row {mid} (one space added to comment)")
q("UPDATE audit_log SET comment = ? WHERE id = ?", (old, mid))
row = q("SELECT * FROM audit_log WHERE id = ?", (ids[3],), True)[0]
q("DELETE FROM audit_log WHERE id = ?", (ids[3],))
verify(f"deleted row {ids[3]}")
q(f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", row)
verify("row put back")
tail = q("SELECT * FROM audit_log WHERE team_id = ? ORDER BY id DESC LIMIT 1", (TEAM,), True)[0]
q("DELETE FROM audit_log WHERE id = ?", (tail[0],))
verify("deleted the last row")
q("UPDATE catalog_meta SET value = ? WHERE key = ?", (tail[cols.index("prev_hash")], f"audit_head:{TEAM}"))
verify("… and moved catalog_meta audit_head back one row (tail truncation)")
q(f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tail)
q("UPDATE catalog_meta SET value = ? WHERE key = ?", (tail[cols.index("row_hash")], f"audit_head:{TEAM}"))
verify("restored")
if PG:
    q("ALTER TABLE audit_log ENABLE TRIGGER USER")
else:
    for (s,) in trg:
        q(s)
print("  (triggers back on)")
