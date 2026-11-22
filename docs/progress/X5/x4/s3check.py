"""X5 (lane x4): the S3 bucket after the purge: Object Lock / versioning settings, and every version / delete marker of the
purged blobs (keys given on the command line, as hex sha prefixes). python s3check.py <bucket> <sha> …"""
import sys

import boto3

cl = boto3.client("s3", endpoint_url="http://127.0.0.1:9020", aws_access_key_id="x5x4admin",
                  aws_secret_access_key="x5x4secret-throwaway", region_name="us-east-1")
b = sys.argv[1]
print("versioning:", cl.get_bucket_versioning(Bucket=b).get("Status"))
print("object lock:", cl.get_object_lock_configuration(Bucket=b).get("ObjectLockConfiguration"))
vs = cl.list_object_versions(Bucket=b)
allv = vs.get("Versions", [])
print("object versions:", len(allv), "| delete markers:", len(vs.get("DeleteMarkers", [])))
one = allv[0]
r = cl.get_object_retention(Bucket=b, Key=one["Key"], VersionId=one["VersionId"])
print("retention on a kept blob:", one["Key"], r.get("Retention"))
for sha in sys.argv[2:]:
    hits = [(v["Key"], v["VersionId"]) for v in allv if sha in v["Key"]] + [(m["Key"], "DM") for m in vs.get("DeleteMarkers", []) if sha in m["Key"]]
    print(f"purged blob {sha[:12]}: versions/markers left {hits}")
