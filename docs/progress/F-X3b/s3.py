"""F-X3b S3 checks (MinIO, Object Lock + versioning) of X3 m9 / M8's S3 variant, m7 and m8.
Usage (env: AWS_* , ENSEMBLE_S3_ENDPOINT): s3.py <api host:port> <bucket> <cache dir> <work dir with v1.vid/vkeys.vid> <files dir> <tag>"""
import io, json, os, subprocess, sys
import boto3, pyarrow as pa, pyarrow.compute as pc, pyarrow.parquet as pq, requests

api, bucket, cache, W, F, tag = sys.argv[1:7]
A = f"http://{api}/api"
H = {"X-Ensemble-Actor": "Ana"}
HA = {"X-Ensemble-Actor": "Ada"}
R, V = open(f"{W}/v1.vid").read().strip(), open(f"{W}/vkeys.vid").read().strip()
s3 = boto3.client("s3", endpoint_url=os.environ["ENSEMBLE_S3_ENDPOINT"])


def show(label, r, keys=None):
    try:
        d = r.json()
    except ValueError:
        d = r.text[:160]
    if isinstance(d, dict) and keys:
        d = {k: d.get(k) for k in keys}
    elif isinstance(d, dict) and "detail" in d:
        d = d["detail"] if not isinstance(d["detail"], dict) else {k: d["detail"].get(k) for k in ("code", "message", "sha256", "matching_version")}
    print(f"   {label}: {r.status_code} {str(d)[:260]}")
    return r


def part(table):
    m = requests.get(f"{A}/datasets/{V}/manifest", headers=H).json()["manifest"]
    t = next(t for t in m["data"]["tables"] if t["name"] == table)
    return t["files"][0]["sha256"]


def ymean(rows):
    return sum(rows) / len(rows)


sha, sha_c, sha_p, sha_d = part("apps"), part("cust"), part("pays"), part("daily")
print("## m9 / M8 (S3): rewrite a cached part (y flipped) and its .meta sidecar")
requests.get(f"{A}/datasets/{V}/grid", headers=H, params={"table": "apps", "limit": 5})  # fill the cache
p = f"{cache}/blobs/{sha[:2]}/{sha}.parquet"
t = pq.read_table(io.BytesIO(s3.get_object(Bucket=bucket, Key=f"blobs/{sha[:2]}/{sha}.parquet")["Body"].read()))
y0 = t["y"].to_pylist()
t = t.set_column(t.schema.get_field_index("y"), "y", pc.subtract(pa.scalar(1, pa.int64()), t["y"]))
os.chmod(p, 0o644); pq.write_table(t, p)
st = os.stat(p)
json.dump({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino, "ctime_ns": st.st_ctime_ns, "filled_at": 0}, open(p + ".meta", "w"))
print(f"   tampered {sha[:12]} in the cache; the bucket's y mean is {ymean(y0):.3f}, the cache's {1 - ymean(y0):.3f}")
g = requests.get(f"{A}/datasets/{V}/grid", headers=H, params={"table": "apps", "limit": 200}).json()
cols = g.get("columns") or []
yi = [c["name"] if isinstance(c, dict) else c for c in cols].index("y") if cols else None
rows = g.get("rows") or []
served = [r[yi] if isinstance(r, list) else r["y"] for r in rows] if yi is not None or rows else []
print("   grid serves y = the bucket's bytes:", [int(x) for x in served[:200]] == y0[:len(served)], f"({len(served)} rows)")
show("GET verify", requests.get(f"{A}/datasets/{V}/verify", headers=H), ["ok"])
spec = {"grain": "apps", "joins": [{"type": "n:1", "table": "cust", "on": ["cust"], "prefix": f"s3{tag}__"}]}
j = requests.post(f"{A}/datasets/{R}/joins", headers=H, json={"name": f"s3_{tag}", "version_id": V, "spec": spec}).json()
f = show("POST frames (after the tamper)", requests.post(f"{A}/datasets/{R}/versions/{V}/frames", headers=H, json={"join_id": j["id"]}), ["id", "cached"]).json()
if f.get("id"):
    rows = requests.get(f"{A}/datasets/{R}/frames/{f['id']}/rows", headers=H, params={"limit": 100}).json()
    yi = rows["columns"].index("y")
    print("   frame y = the bucket's bytes:", [r[yi] for r in rows["rows"]] == y0[:100])
    show("frame verify", requests.get(f"{A}/datasets/{R}/frames/{f['id']}", headers=H, params={"verify": 1}),
         ["verify"])
st = requests.get(f"{A}/admin/lake/status", headers=HA).json()["blob_store"]["cache"]["stats"]
print("   cache stats", {k: st.get(k) for k in ("tamper_refetch", "rehash_on_hit", "first_use_rehash", "fills")})

print("## m7: a blob deleted from the bucket (every version) and the cache")
key = f"blobs/{sha_c[:2]}/{sha_c}.parquet"
for v in s3.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", []):
    s3.delete_object(Bucket=bucket, Key=key, VersionId=v["VersionId"], BypassGovernanceRetention=True)
for suf in ("", ".meta"):
    q = f"{cache}/blobs/{sha_c[:2]}/{sha_c}.parquet{suf}"
    if os.path.exists(q):
        os.chmod(q, 0o644); os.remove(q)
print(f"   deleted {sha_c[:12]} (cust)")
show("GET grid?table=cust", requests.get(f"{A}/datasets/{V}/grid", headers=H, params={"table": "cust"}))
show("GET rows/count?table=cust", requests.get(f"{A}/datasets/{V}/rows/count", headers=H, params={"table": "cust"}))
show("GET stats?table=cust", requests.get(f"{A}/datasets/{V}/stats", headers=H, params={"table": "cust"}))
show("GET manifest", requests.get(f"{A}/datasets/{V}/manifest", headers=H))
j2 = requests.post(f"{A}/datasets/{R}/joins", headers=H, json={"name": f"s3m_{tag}", "version_id": V,
                   "spec": {"grain": "apps", "joins": [{"type": "n:1", "table": "cust", "on": ["cust"], "prefix": f"m{tag}__"}]}})
show("POST joins (validate reads cust)", j2)
if j2.ok:
    show("POST frames", requests.post(f"{A}/datasets/{R}/versions/{V}/frames", headers=H, json={"join_id": j2.json()["id"]}))
u = requests.post(f"{A}/uploads", headers=H, params={"parent_version_id": V}, files={"file": ("cust.csv", open(f"{F}/cust.csv", "rb"))}).json()
requests.patch(f"{A}/uploads/{u['upload_id']}", headers=H, json={"table": "cust", "tables": {"cust": {"action": "delta", "key": ["cust"]}}})
show("delta /check on cust", requests.post(f"{A}/uploads/{u['upload_id']}/check", headers=H))
show("GET verify", requests.get(f"{A}/datasets/{V}/verify", headers=H), ["ok"])

print("## m8: a newer object version with other bytes stacked on the pays part")
body = s3.get_object(Bucket=bucket, Key=f"blobs/{sha_d[:2]}/{sha_d}.parquet")["Body"].read()
vid = s3.put_object(Bucket=bucket, Key=f"blobs/{sha_p[:2]}/{sha_p}.parquet", Body=body).get("VersionId")
for suf in ("", ".meta"):
    q = f"{cache}/blobs/{sha_p[:2]}/{sha_p}.parquet{suf}"
    if os.path.exists(q):
        os.chmod(q, 0o644); os.remove(q)
print(f"   stacked version {vid} on {sha_p[:12]} (pays)")


def upload_pays():
    u = requests.post(f"{A}/uploads", headers=H, files={"file": ("pays.csv", open(f"{F}/pays.csv", "rb"))}, data={"name": f"pays_again_{tag}"}).json()
    requests.post(f"{A}/uploads/{u['upload_id']}/check", headers=H)
    return requests.post(f"{A}/uploads/{u['upload_id']}/commit", headers=H, json={"reason": "F-X3b m8"})


r = show("upload pays.csv again (dedupes onto the blob)", upload_pays())
show("GET grid?table=pays", requests.get(f"{A}/datasets/{V}/grid", headers=H, params={"table": "pays", "limit": 2}))
show("POST restore (no confirm)", requests.post(f"{A}/admin/lake/blobs/{sha_p}/restore", headers=HA, json={"by": "Ada"}))
rr = requests.post(f"{A}/admin/lake/blobs/{sha_p}/restore", headers=HA, json={"by": "Ada", "confirm": "restore"})
show("POST restore", rr, ["restored", "matching_version", "removed_versions"])
r = show("upload pays.csv again", upload_pays(), ["version_id"])
show("GET grid?table=pays", requests.get(f"{A}/datasets/{V}/grid", headers=H, params={"table": "pays", "limit": 2}), ["total", "n_rows"])
show("GET verify (cust still deleted)", requests.get(f"{A}/datasets/{V}/verify", headers=H), ["ok", "problems"])
