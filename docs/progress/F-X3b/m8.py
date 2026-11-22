"""M8 repro (local store): build a join frame while an input part holds other bytes, restore the bytes, then ask
frame verify. Usage: m8.py <api> <data dir> <root> <version> <tag>"""
import json, os, shutil, sqlite3, stat, sys, urllib.request
import pyarrow as pa, pyarrow.parquet as pq

api, data, R, V, tag = sys.argv[1:6]
H = {"X-Ensemble-Actor": "Ana", "content-type": "application/json"}


def call(method, path, body=None):
    req = urllib.request.Request(f"http://{api}{path}", method=method, headers=H,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


con = sqlite3.connect(f"{data}/ensemble.sqlite3") if os.path.exists(f"{data}/ensemble.sqlite3") else None
if con is None:
    import psycopg
    con = psycopg.connect(os.environ["ENSEMBLE_DB_URL"])
cur = con.cursor()
ph = "?" if isinstance(con, sqlite3.Connection) else "%s"
cur.execute(f"SELECT f.blob_sha256, b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = {ph} AND f.table_name = 'apps'", (V,))
sha, key = cur.fetchone()
path = f"{data}/lake/{key}"
keep = path + ".orig"
shutil.copyfile(path, keep)
t = pq.read_table(path)
y = pa.array([1 - v for v in t.column("y").to_pylist()])
t = t.set_column(t.schema.get_field_index("y"), "y", y)
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
pq.write_table(t, path)
print("1. flipped y in the apps part", sha[:12])
spec = {"grain": "apps", "joins": [{"type": "n:1", "table": "cust", "on": ["cust"], "prefix": f"{tag}__"}]}
s, j = call("POST", f"/api/datasets/{R}/joins", {"name": f"m8_{tag}", "version_id": V, "spec": spec})
print("2. save join", s, j.get("id") or j.get("detail"))
s, f = call("POST", f"/api/datasets/{R}/versions/{V}/frames", {"join_id": j.get("id")})
print("3. POST frames while the bytes are wrong", s, f.get("id") or f.get("detail"))
shutil.copyfile(keep, path)
os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
os.unlink(keep)
print("4. restored the part")
if s == 201:
    s, g = call("GET", f"/api/datasets/{R}/frames/{f['id']}?verify=1")
    v = g.get("verify") or {}
    print("5. frame verify", s, "ok", v.get("ok"), "| inputs", [(x["table"], x["ok"]) for x in v.get("inputs", [])],
          "| cache", (v.get("cache") or {}).get("ok"), "| derived", (v.get("derived") or {}).get("ok"), (v.get("derived") or {}).get("summary"))
    s, f2 = call("POST", f"/api/datasets/{R}/versions/{V}/frames", {"join_id": j.get("id")})
    print("6. POST frames again (bytes good)", s, "cached", f2.get("cached"), "rematerialised", f2.get("rematerialised"), f2.get("detail"))
s, f3 = call("POST", f"/api/datasets/{R}/versions/{V}/frames", {"join_id": j.get("id")})
print("7. POST frames (bytes good)", s, f3.get("id"), "cached", f3.get("cached"))
