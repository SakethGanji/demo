#!/usr/bin/env bash
# P10b step 10: copy a local lake (made with ENSEMBLE_BLOB_STORE=local by chain.sh local) into a new bucket, with a crash
# and a resume, then serve that data dir from the bucket and verify-all. sync.sh <data dir> <bucket> [port]
set -u
DATA=$1; BUCKET=$2; PORT=${3:-8001}; HERE=$(cd "$(dirname "$0")" && pwd)
export ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_BLOB_STORE=s3://$BUCKET/
SY="$PY -m app.lake.sync_s3"
sm() { python3 -c "import sys,json;d=json.load(sys.stdin);print('  ',{k:d[k] for k in ('dry_run','local_objects','copied','copied_bytes','already_in_bucket','put_mb_s','verified_in_bucket','ok','seconds')},'| integrity',len(d['integrity_local']),'failed',len(d['failed']),'missing',len(d['missing_in_bucket']),'mismatch',len(d['mismatch_in_bucket']))"; }
q() { $PY -c "import sqlite3,sys;c=sqlite3.connect('$DATA/ensemble.sqlite3');[print('  ',*r) for r in c.execute(sys.argv[1])]" "$1"; }
cd $HERE/../../../backend
echo "## 10.1 dry run (nothing copied)"
$SY --dry-run 2>/dev/null | sm
echo "## 10.2 the sync crashes after 20 puts"
$PY $HERE/sync_crash.py 20 2>/dev/null; echo "  exit $?"
q "select key, value from catalog_meta where key = 'sync_s3:progress'"
echo "## 10.3 re-run: resumes (the 20 already copied are checked, not copied again), then every catalogued sha is re-hashed from the bucket"
$SY --by Ada 2>/dev/null | sm
echo "## 10.4 re-run with --deep-verify: nothing to copy, every object re-hashed"
$SY --deep-verify --by Ada 2>/dev/null | sm
q "select actor, action, json_extract(details,'$.copied'), json_extract(details,'$.ok') from audit_log where action = 'lake_synced_to_s3' order by id"
echo "## 10.5 the same data dir served from the bucket (ENSEMBLE_BLOB_STORE=s3://$BUCKET/), a fresh cache"
rm -rf $DATA/cache
(ENSEMBLE_ADMINS=Ada nohup $PY -m uvicorn app.main:app --port $PORT > $DATA.sync-api.log 2>&1 &)
for i in $(seq 60); do curl -s -o /dev/null localhost:$PORT/api/health && break; sleep 1; done
AD="curl -s -H X-Ensemble-Actor:Ada localhost:$PORT/api"
read V1 V2 V3 R1 R2 < ${IDS:-ids_local.txt}  # chain.sh local wrote them
$AD/admin/lake/status | python3 -c "import sys,json;d=json.load(sys.stdin);s=d['blob_store'];print('  store',d['store'],'| object_lock',s['object_lock'],'| versions',d['parquet_versions'])"
$AD/admin/lake/verify-all | python3 -c "import sys,json;d=json.load(sys.stdin);print('  verify-all ok',d['ok'],'| datasets',d['datasets'],'| checked',d['checked'],'| failed',d['failed'][:3])"
$AD/datasets/$V1/rows?limit=2 | python3 -c "import sys,json;d=json.load(sys.stdin);print('  GET …/rows',[r['acct'] for r in d['rows']])"
$AD/datasets/$V1/diff/$V3 | python3 -c "import sys,json;d=json.load(sys.stdin);r=d.get('rows',d);print('  diff v1 v3',{k:r.get(k) for k in ('matched','changed','added','removed')})"
$AD/admin/lake/gc?dry_run=1 -X POST | python3 -c "import sys,json;d=json.load(sys.stdin);print('  gc dry run: candidates',len(d['candidates']),'orphans',len(d['orphans']),'young',d['orphans_in_grace'],'integrity',len(d['integrity']))"
$AD/admin/lake/status | python3 -c "import sys,json;d=json.load(sys.stdin);c=d['blob_store']['cache'];print('  cache: entries',c['entries'],'bytes',c['bytes'],{k:c['stats'][k] for k in ('hits','misses','fills','fill_mb_s')})"
pkill -f "port $PORT"
echo "  API log: tracebacks $(grep -c Traceback $DATA.sync-api.log), HTTP 500 $(grep -c '\" 500' $DATA.sync-api.log)"
