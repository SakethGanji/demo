"""Outside changes to the bucket. stack <sha>: put a newer object version with other bytes (the bytes of another
blob); delete <sha>: remove every version (governance bypass); and drop the cache file either way.
Usage: s3_mutate.py stack|delete <sha> <bucket> <cache dir> [other sha]"""
import os, sys, boto3
op, sha, bucket, cache = sys.argv[1:5]
s3 = boto3.client("s3", endpoint_url=os.environ["ENSEMBLE_S3_ENDPOINT"])
key = f"blobs/{sha[:2]}/{sha}.parquet"
if op == "stack":
    other = sys.argv[5]; body = s3.get_object(Bucket=bucket, Key=f"blobs/{other[:2]}/{other}.parquet")["Body"].read()
    r = s3.put_object(Bucket=bucket, Key=key, Body=body); print("stacked version", r.get("VersionId"))
else:
    vs = s3.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", [])
    for v in vs:
        s3.delete_object(Bucket=bucket, Key=key, VersionId=v["VersionId"], BypassGovernanceRetention=True)
    print("deleted versions", len(vs))
for suf in ("", ".meta"):
    p = f"{cache}/blobs/{sha[:2]}/{sha}.parquet{suf}"
    if os.path.exists(p): os.chmod(p, 0o644); os.remove(p)
