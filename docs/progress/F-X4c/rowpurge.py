"""F-X4c: a row purge. Purge 10 rows of loans by __row_id (on the pre-purge snapshot), approve, then search the data
dir and the DB for those rows' ssn / email / name, and for the other 590 rows' ssn (which must still be there).

    X4_DATA=<data dir> X4_DB=<sqlite file | postgresql://…> python rowpurge.py <base> <state.json>"""
import json
import os
import re

from x4lib import R, get, load, post, show

st = load()
v1 = st["v1"]
DATA, DB = os.environ["X4_DATA"], os.environ["X4_DB"]
rows = get("Ana", f"/datasets/{v1}/rows", params={"by": "Ana", "unmasked": 1, "reason": "erasure check", "limit": 10}).json()
rr = rows.get("rows") or []
ids = [r["_row_id"] for r in rr]
gone = {str(r["ssn"]) for r in rr} | {r["email"] for r in rr} | {r["applicant_name"] for r in rr}
kept = {str(r["ssn"]) for r in R} - {str(r["ssn"]) for r in rr}
print("row ids:", ids, "| values to erase:", len(gone))
rx = re.compile("|".join(re.escape(v) for v in sorted(gone, key=len, reverse=True)).encode())


def scan(tag):
    files = {}
    for root, _, fs in os.walk(DATA):
        for f in fs:
            p = os.path.join(root, f)
            try:
                b = open(p, "rb").read()
            except OSError:
                continue
            m = set(rx.findall(b))
            if m:
                files[os.path.relpath(p, DATA)] = len(m)
    if DB.startswith("postgres"):
        import psycopg
        con = psycopg.connect(DB)
        tabs = [r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    else:
        import sqlite3
        con = sqlite3.connect(DB)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    dbh = {}
    for t in tabs:
        m = set()
        for row in con.execute(f'SELECT * FROM "{t}"').fetchall():
            m |= set(rx.findall(json.dumps(row, default=str).encode()))
        if m:
            dbh[t] = len(m)
    print(f"    {tag}: files holding erased values: {files}\n    {tag}: DB tables: {dbh}")


scan("before")
p = show("purge rows", post("Ana", f"/datasets/{v1}/purge", {"table": "loans", "row_ids": ids, "reason": "erasure requests batch 10 (art. 17)",
                                                               "by": "Ana"}), ["id", "status", "teams_needed"])
inv = (p.get("impact") or {}).get("derived") or {}
print("    impact derived:", [(x["kind"], x["rel"], x["action"]) for x in inv.get("files") or []], "| purged models:", inv.get("purged_models"))
owners = {st["cr"]: "Ben", st["co"]: "Cole", st["fr"]: "Fox"}
j = p
for t in p.get("teams_needed") or []:
    j = show(f"approve {t}", post(owners[t], f"/purges/{p['id']}/decide", {"team": t, "approve": True, "by": owners[t]}), ["status"], cut=100)
d = (j.get("executed") or {}).get("derived") or {}
print("    derived:", json.dumps({k: d.get(k) for k in ("n_deleted", "n_redacted", "purged_models", "model_versions_purged", "error")}))
print("    deleted:", d.get("deleted"))
print("    db:", json.dumps(d.get("db")))
sw = j.get("sweep") or {}
print("    SWEEP:", json.dumps({k: sw.get(k) for k in ("status", "values", "values_total", "files_searched", "n_hits", "hits")})[:1500])
scan("after")
nv = p.get("new_version_id")
r2 = get("Ana", f"/datasets/{nv}/rows", params={"by": "Ana", "unmasked": 1, "reason": "erasure check", "limit": 1000}).json()
left = {str(r["ssn"]) for r in r2.get("rows") or []}
print(f"    new version {nv}: {len(r2.get('rows') or [])} rows; kept ssns present: {len(left & kept)}/{len(kept)}; erased present: {len(left & gone)}")
