"""F-X4b: X4 M6 / m1 (tail truncation) after the fix. The API is stopped; the database is changed directly, as its owner
could: the append-only triggers are dropped first. verify_audit_chain is the function behind GET /teams/{t}/audit/verify
(with the data dir, as the route passes it, so the signed checkpoints count).

Steps: X4's tamper sequence (edit a middle row, delete a middle row, delete the tail + rewind catalog_meta); then the
stronger attacker who also drops the audit_heads triggers and deletes the heads of the removed rows (caught by the
signed checkpoint); then an edited checkpoint line. On Postgres, the app role's grants on audit_heads are shown.

    cd backend && X4_DB=<sqlite file | postgresql://…> X4_DATA=<data dir> python …/tamper.py <team id>"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from pathlib import Path  # noqa: E402

from app.catalog import audit_anchor  # noqa: E402
from app.catalog.schema import verify_audit_chain  # noqa: E402
from app.db import Database  # noqa: E402

TEAM = sys.argv[1]
URL = os.environ["X4_DB"]
DATA = Path(os.environ["X4_DATA"])
PG = URL.startswith("postgres")
db = Database(Path("/tmp/fx4b_unused.sqlite3"), url=URL) if PG else Database(Path(URL), url="")


def q(sql, a=(), fetch=False):
    with db.connect() as con:
        cur = con.execute(sql, a)
        return [tuple(r) for r in cur.fetchall()] if fetch else None


def verify(tag, data=True):
    with db.connect() as con:
        r = verify_audit_chain(con, TEAM, DATA if data else None)
    print(f"  {tag}: ok={r['ok']} rows={r['rows']} anchored={r.get('anchored')} first_break={json.dumps(r.get('first_break'))}")
    return r


print("  checkpoint before tampering:", audit_anchor.checkpoint(db, DATA))
verify("clean")
ids = [r[0] for r in q("SELECT id FROM audit_log WHERE team_id = ? AND row_hash IS NOT NULL ORDER BY id", (TEAM,), True)]
mid = ids[len(ids) // 2]
for sql in ("UPDATE audit_log SET comment = comment WHERE id = ?", "UPDATE audit_heads SET head = head WHERE audit_id = ?",
            "DELETE FROM audit_heads WHERE audit_id = ?"):
    try:
        q(sql, (mid,))
        print(f"  {sql.split(' SET')[0].split(' WHERE')[0]} accepted (no trigger)")
    except Exception as e:  # noqa: BLE001
        print(f"  {sql.split(' SET')[0].split(' WHERE')[0]} refused: {type(e).__name__}: {str(e).splitlines()[0]}")
if PG:
    role = os.environ.get("X4_APP_ROLE")
    print("  grants on audit_heads:", q("SELECT grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants"
                                       " WHERE table_name = 'audit_heads' GROUP BY grantee ORDER BY grantee", (), True))
    q("ALTER TABLE audit_log DISABLE TRIGGER USER")
    cols = [r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_log' ORDER BY ordinal_position", (), True)]
else:
    trg = q("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'audit_log'", (), True)
    for n in ("audit_ro_u", "audit_ro_d"):
        q(f"DROP TRIGGER IF EXISTS {n}")
    cols = [r[1] for r in q("PRAGMA table_info(audit_log)", (), True)]
print("  (audit_log append-only triggers off)")
old = q("SELECT comment FROM audit_log WHERE id = ?", (mid,), True)[0][0]
q("UPDATE audit_log SET comment = ? WHERE id = ?", ((old or "") + " ", mid))
verify(f"edited row {mid} (one space added to comment)")
q("UPDATE audit_log SET comment = ? WHERE id = ?", (old, mid))
row = q("SELECT * FROM audit_log WHERE id = ?", (ids[3],), True)[0]
q("DELETE FROM audit_log WHERE id = ?", (ids[3],))
verify(f"deleted row {ids[3]}")
q(f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", row)
verify("row put back")

N = 3
tail = q(f"SELECT * FROM audit_log WHERE team_id = ? ORDER BY id DESC LIMIT {N}", (TEAM,), True)
for t in tail:
    q("DELETE FROM audit_log WHERE id = ?", (t[0],))
q("UPDATE catalog_meta SET value = ? WHERE key = ?", (tail[-1][cols.index("prev_hash")], f"audit_head:{TEAM}"))
verify(f"deleted the last {N} rows and moved catalog_meta audit_head back {N} rows (X4's tail truncation)", data=False)

print("  (a stronger attacker: the audit_heads triggers off too, and the heads of the removed rows deleted)")
if PG:
    q("ALTER TABLE audit_heads DISABLE TRIGGER USER")
else:
    htrg = q("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'audit_heads'", (), True)
    for n in ("ahead_ro_u", "ahead_ro_d"):
        q(f"DROP TRIGGER IF EXISTS {n}")
heads = q("SELECT team_id, audit_id, head, ts FROM audit_heads WHERE team_id = ? AND audit_id >= ? ORDER BY audit_id", (TEAM, tail[-1][0]), True)
q("DELETE FROM audit_heads WHERE team_id = ? AND audit_id >= ?", (TEAM, tail[-1][0]))
verify("… and deleted those rows' audit_heads (without the checkpoints)", data=False)
verify("… the same, verified with the signed checkpoints (as GET …/audit/verify does)")

for t in reversed(tail):
    q(f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", t)
for hrow in heads:
    q("INSERT INTO audit_heads (team_id, audit_id, head, ts) VALUES (?, ?, ?, ?)", hrow)
q("UPDATE catalog_meta SET value = ? WHERE key = ?", (tail[0][cols.index("row_hash")], f"audit_head:{TEAM}"))
verify("restored")

cpf = audit_anchor.directory(DATA) / audit_anchor.FILE
lines = cpf.read_text().splitlines()
i = max(k for k, line in enumerate(lines) if json.loads(line)["team_id"] == TEAM)
c = json.loads(lines[i])
orig = lines[i]
c["audit_id"] = int(c["audit_id"]) - N
lines[i] = json.dumps(c, sort_keys=True)
cpf.write_text("\n".join(lines) + "\n")
verify(f"checkpoint line {i + 1} edited (audit_id moved back {N}, signature kept)")
lines[i] = orig
cpf.write_text("\n".join(lines) + "\n")
verify("checkpoint restored")

if PG:
    q("ALTER TABLE audit_log ENABLE TRIGGER USER")
    q("ALTER TABLE audit_heads ENABLE TRIGGER USER")
else:
    for (s,) in trg + htrg:
        q(s)
print("  (triggers back on)")
