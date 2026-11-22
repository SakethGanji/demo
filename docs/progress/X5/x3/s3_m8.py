"""X5/x3 M8 on S3: other bytes (y flipped) put as a newer object version on the apps part in the bucket (cache copy
dropped), with the join saved first. POST frames while stacked (new build), unstack, build, stack again, POST frames
(the cached path) and frame verify, unstack. Usage: s3_m8.py <port> <bucket> <cache dir> <work dir> <tag>"""
import io, os, sys
import boto3, pyarrow as pa, pyarrow.compute as pc, pyarrow.parquet as pq, requests
P, bucket, cache, W, tag = sys.argv[1:6]
A = f"http://localhost:{P}/api"; H = {"X-Ensemble-Actor": "Ana"}
R, V = open(f"{W}/v1.vid").read().strip(), open(f"{W}/vkeys.vid").read().strip()
s3 = boto3.client("s3", endpoint_url=os.environ["ENSEMBLE_S3_ENDPOINT"])
vf = requests.get(f"{A}/datasets/{V}/verify", headers=H).json()  # (the manifest route answers 409 once s3.py deleted cust)
sha = next(f for f in vf["files"] if f["table"] == "apps")["stored"]
key = f"blobs/{sha[:2]}/{sha}.parquet"
t = pq.read_table(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))
t2 = t.set_column(t.schema.get_field_index("y"), "y", pc.subtract(pa.scalar(1, pa.int64()), t["y"]))
buf = io.BytesIO(); pq.write_table(t2, buf)


def drop_cache():
    for suf in ("", ".meta"):
        q = f"{cache}/blobs/{sha[:2]}/{sha}.parquet{suf}"
        if os.path.exists(q):
            os.chmod(q, 0o644); os.remove(q)


def stack():
    vid = s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())["VersionId"]; drop_cache(); return vid


def unstack(vid):
    s3.delete_object(Bucket=bucket, Key=key, VersionId=vid, BypassGovernanceRetention=True); drop_cache()


def frames(label, jid):
    r = requests.post(f"{A}/datasets/{R}/versions/{V}/frames", headers=H, json={"join_id": jid})
    print(f"{label}: POST /datasets/{{R}}/versions/{{V}}/frames → {r.status_code} {r.text[:240]}")
    return r


for name, spec in (("daily", {"grain": "apps", "joins": [{"type": "n:1", "table": "daily", "on": [["applied_day", "day"]], "prefix": f"d{tag}__"}]}),):
    j = requests.post(f"{A}/datasets/{R}/joins", headers=H, json={"name": f"m8s3_{tag}", "version_id": V, "spec": spec})
    print("1. POST joins (bytes good) →", j.status_code, j.json().get("id")); jid = j.json()["id"]
    vid = stack(); print(f"2. stacked version {vid} (y flipped) on apps part {sha[:12]}; cache copy removed")
    frames("3. new build while stacked", jid)
    unstack(vid); print("4. stacked version removed")
    r = frames("5. build, bytes good", jid); fid = r.json().get("id")
    rows = requests.get(f"{A}/datasets/{R}/frames/{fid}/rows", headers=H, params={"limit": 200}).json()
    yi = rows["columns"].index("y")
    print("   frame y = the bucket's original bytes:", [x[yi] for x in rows["rows"]] == t["y"].to_pylist()[:len(rows["rows"])])
    vid = stack(); print(f"6. stacked again {vid}")
    frames("7. cached path while stacked", jid)
    v = requests.get(f"{A}/datasets/{R}/frames/{fid}", headers=H, params={"verify": 1})
    vv = v.json().get("verify", v.json())
    print("8. frame verify while stacked →", v.status_code, {k: vv.get(k) for k in ("ok", "derived", "inputs_deleted")} if isinstance(vv, dict) else str(vv)[:200],
          [(x.get("table"), x.get("ok")) for x in vv.get("inputs", [])] if isinstance(vv, dict) else "")
    unstack(vid); print("9. unstacked")
    v = requests.get(f"{A}/datasets/{R}/frames/{fid}", headers=H, params={"verify": 1}).json()["verify"]
    print("10. frame verify after →", v.get("ok"), "derived", str(v.get("derived"))[:120])
