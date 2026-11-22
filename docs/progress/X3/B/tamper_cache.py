"""Replace a cached blob with other bytes (the real part with `defaulted` flipped), then rewrite its .meta sidecar
with the new stat so the `stat` hit check passes. Usage: tamper_cache.py <cache dir> <sha> <bucket>"""
import json, os, sys, boto3, io
import pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc
cache, sha, bucket = sys.argv[1:4]
p = f"{cache}/blobs/{sha[:2]}/{sha}.parquet"
s3 = boto3.client("s3", endpoint_url=os.environ["ENSEMBLE_S3_ENDPOINT"])
t = pq.read_table(io.BytesIO(s3.get_object(Bucket=bucket, Key=f"blobs/{sha[:2]}/{sha}.parquet")["Body"].read()))
i = t.schema.get_field_index("defaulted")
t = t.set_column(i, "defaulted", pc.subtract(pa.scalar(1, pa.int64()), t["defaulted"]))
os.chmod(p, 0o644); pq.write_table(t, p)
st = os.stat(p)
json.dump({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino, "ctime_ns": st.st_ctime_ns, "filled_at": 0}, open(p + ".meta", "w"))
print("tampered", p, st.st_size, "rows", t.num_rows)
