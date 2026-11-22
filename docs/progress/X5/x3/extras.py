"""X5/x3 extras: X3 m4 on the commit body (`by` not text), and m10 with more rows first (no verify before the run).
Usage: extras.py <port> <data dir> [postgres url]"""
import os, sqlite3, sys
import pyarrow as pa, pyarrow.parquet as pq, requests
P, DATA = sys.argv[1], sys.argv[2]; PG = sys.argv[3] if len(sys.argv) > 3 else None
A = f"http://localhost:{P}/api"; H = {"X-Ensemble-Actor": "Ana"}
csv = "id,x,target\n" + "".join(f"{i},{i * 0.5},{i % 2}\n" for i in range(200))
for by in (7, ["x"], {"a": 1}, True):
    u = requests.post(f"{A}/uploads", headers=H, files=[("file", ("m4.csv", csv.encode()))], data={"name": "x5m4", "target": "target"}).json()["upload_id"]
    requests.post(f"{A}/uploads/{u}/check", headers=H)
    r = requests.post(f"{A}/uploads/{u}/commit", headers=H, json={"reason": "x5 m4", "by": by})
    print(f"m4 commit by={by!r}: POST /uploads/{{u}}/commit → {r.status_code} {r.text[:160]}")
u = requests.post(f"{A}/uploads", headers=H, files=[("file", ("m10b.csv", csv.encode()))], data={"name": "x5m10b", "target": "target"}).json()["upload_id"]
requests.post(f"{A}/uploads/{u}/check", headers=H)
V = requests.post(f"{A}/uploads/{u}/commit", headers=H, json={"reason": "x5"}).json()["version_id"]
con = __import__("psycopg").connect(PG) if PG else sqlite3.connect(f"{DATA}/ensemble.sqlite3"); ph = "%s" if PG else "?"
cur = con.cursor(); cur.execute(f"SELECT b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = {ph}", (V,))
path = f"{DATA}/lake/{cur.fetchone()[0]}"; t = pq.read_table(path); os.chmod(path, 0o644)
pq.write_table(pa.concat_tables([t, t.slice(0, 10)]), path)
r = requests.post(f"{A}/runs", headers=H, json={"dataset_id": V, "label": "target", "time_limit": 10})
print(f"m10 more rows ({t.num_rows}+10), no verify first: POST /runs → {r.status_code} {r.text[:220]}")
pq.write_table(t.slice(0, t.num_rows - 10), path)
r = requests.post(f"{A}/runs", headers=H, json={"dataset_id": V, "label": "target", "time_limit": 10})
print(f"m10 fewer rows ({t.num_rows}-10): POST /runs → {r.status_code} {r.text[:220]}")
