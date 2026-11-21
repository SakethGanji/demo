"""P19: tamper with the audit log as someone with file access would (drops the append-only trigger first).
Usage: python tamper.py <db> edit <audit id> | delete-last <team id>"""
import sqlite3, sys

con = sqlite3.connect(sys.argv[1])
con.execute("DROP TRIGGER IF EXISTS audit_ro_u")
con.execute("DROP TRIGGER IF EXISTS audit_ro_d")
if sys.argv[2] == "edit":
    con.execute("UPDATE audit_log SET actor = 'Someone else' WHERE id = ?", (int(sys.argv[3]),))
    print("edited audit row", sys.argv[3], "actor → 'Someone else'")
else:
    i = con.execute("SELECT MAX(id) FROM audit_log WHERE team_id = ?", (sys.argv[3],)).fetchone()[0]
    con.execute("DELETE FROM audit_log WHERE id = ?", (i,))
    print("deleted audit row", i, "(the team's last)")
con.execute("CREATE TRIGGER audit_ro_u BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
con.execute("CREATE TRIGGER audit_ro_d BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END")
con.commit()
