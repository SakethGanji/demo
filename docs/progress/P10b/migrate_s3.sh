#!/usr/bin/env bash
# P10b: P10's migration and restore-splits against S3. migrate_s3.sh <CSV-era data dir> <bucket> <P10 state.json>
# The data dir is the one P10's gen.py makes (three code states, then tamper.py). Its local lake (samples, the P06 child)
# is synced to the bucket first; the API then runs with ENSEMBLE_BLOB_STORE=s3://<bucket>/.
set -u
DATA=$1; BUCKET=$2; ST=$3; HERE=$(cd "$(dirname "$0")" && pwd); P10=$HERE/../P10
export ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_BLOB_STORE=s3://$BUCKET/
g() { python3 -c "import json;print(json.load(open('$ST'))['$1'])"; }
A="curl -s -H X-Ensemble-Actor:Ada -H Content-Type:application/json localhost:8000/api"
poll() { for i in $(seq 150); do s=$($A/admin/lake/migrate | python3 -c "import sys,json;d=json.load(sys.stdin);p=d['progress'] or {};j=d.get('job') or {};print(p.get('job_id'),p.get('status'),j.get('status'))"); case "$s" in "$1 finished succeeded"|"$1 "*" failed") break;; esac; sleep 2; done; }
status() { $A/admin/lake/migrate | python3 -c "
import sys,json;d=json.load(sys.stdin);p=d['progress'];print('  job',p['job_id'],'|',{k:p[k] for k in ('total','done','migrated','skipped','failed')},'| by status',d['by_status'],'| CSV-era left',d['csv_versions_left'])"; }
q() { $PY -c "import sqlite3,sys;c=sqlite3.connect('$DATA/ensemble.sqlite3');[print('  ',*r) for r in c.execute(sys.argv[1])]" "$1"; }
cd $HERE/../../../backend
q "select 'versions', count(*), sum(storage='csv'), sum(storage='parquet') from datasets"
echo "## M1. sync the local lake (the Parquet versions made before the switch) into s3://$BUCKET"
$PY -m app.lake.sync_s3 --by Ada 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print('  ',{k:d[k] for k in ('local_objects','copied','verified_in_bucket','ok')})"
pkill -f "uvicorn app.main:app"; sleep 2
(ENSEMBLE_ADMINS=Ada nohup $PY -m uvicorn app.main:app --port 8000 > $DATA.s3-api.log 2>&1 < /dev/null &)
for i in $(seq 120); do curl -s -o /dev/null localhost:8000/api/health && break; sleep 1; done
$A/admin/lake/status | python3 -c "import sys,json;d=json.load(sys.stdin);print('  API on',d['store'],'| object_lock',d['blob_store']['object_lock'])"
echo "## M2. POST /admin/lake/migrate?dry_run=1"
$A/admin/lake/migrate?dry_run=1 -X POST | python3 -c "import sys,json;d=json.load(sys.stdin);print('  versions',d['versions'],'by action',d['by_action'])"
echo "## M3. the job (S3: every new blob goes to the bucket through put_file; tmp work on local scratch)"
J=$($A/admin/lake/migrate?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job']['id'])"); echo "  202 job $J"; poll $J; status
$A/admin/lake/migrate | python3 -c "
import sys,json;d=json.load(sys.stdin)
for r in d['versions']:
  if r['status']!='migrated': print('  ',r['version_id'],r['status'],r['reason'] or '','|',(r['message'] or '')[:120])"
echo "## M4. undo the two outside changes (DL3 / DL4 flows): restore data.csv, POST …/restore-splits, migrate again"
$PY $P10/untamper.py $ST $DATA | sed 's/^/  /'
$A/datasets/$(g T1)/restore-splits -X POST -d '{"by":"Ada","reason":"put back the split as it was drawn"}' | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  restore-splits',d.get('restored'),'verify ok',d.get('verify',{}).get('ok'))"
J=$($A/admin/lake/migrate?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job']['id'])"); echo "  202 job $J"; poll $J; status
q "select 'versions', count(*), sum(storage='csv'), sum(storage='parquet') from datasets"
q "select 'blobs by kind', kind, count(*), sum(bytes) from blobs group by kind"
echo "## M5. POST …/verify of migrated versions (their CSV-era terms; legacy blobs re-hashed from the bucket)"
for k in L1 A2 C1 T1; do $A/datasets/$(g $k)/verify -X POST -d '{"by":"Ada"}' | python3 -c "
import sys,json;d=json.load(sys.stdin);r=d['results'][0];s=r.get('storage') or {};l=s.get('legacy') or {};print('  $k',r['id'],'ok',d['ok'],'| status',r['status'],'| stored',r['stored'][:12]+'…','now',(r['now'] or '')[:12]+'…','| storage',s.get('kind'),'data_ok',s.get('data_ok'),'| legacy blob',l.get('blob','')[:16]+'…','stored == now',l.get('stored')==l.get('now'))"; done
echo "## M6. a run re-verify through its legacy blobs, and a new run on a migrated version"
$A/admin/lake/runs/$(g rL2)/verify | python3 -c "import sys,json;d=json.load(sys.stdin);x=d['data'];print('  run',d['run_id'],'ok',d['ok'],'| data recorded == now',x.get('recorded')==x.get('now'),'| split',d['split'].get('basis'))"
R=$($A/runs -d "{\"dataset_id\":\"$(g A2)\",\"label\":\"y\",\"time_limit\":20,\"presets\":\"medium_quality\",\"name\":\"p10b on migrated A2\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
for i in $(seq 60); do s=$($A/runs/$R | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 4; done
$A/runs/$R | python3 -c "import sys,json;d=json.load(sys.stdin);print('  run',d['id'],d['status'],d.get('error') or '','| read',(d.get('data') or {}).get('read'))"
echo "## M7. verify-all twice, then the cleanup of the .migrated folders"
for i in 1 2; do $A/admin/lake/verify-all?by=Ada | python3 -c "import sys,json;d=json.load(sys.stdin);print('  verify-all',d['ok'],'datasets',d['datasets'],'checked',d['checked'],'failed',d['failed'][:3])"; done
$A/admin/lake/migrate/cleanup?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print('  cleanup deleted',sum(f['delete'] for f in d['folders']),'folders')"
$A/admin/lake/verify-all?by=Ada | python3 -c "import sys,json;d=json.load(sys.stdin);print('  verify-all after cleanup',d['ok'],'checked',d['checked'])"
$A/admin/lake/gc?dry_run=1 -X POST | python3 -c "import sys,json;d=json.load(sys.stdin);print('  gc dry run: candidates',len(d['candidates']),'| unreferenced in grace',len(d['unreferenced_in_grace']),'| orphans',len(d['orphans']),'young',d['orphans_in_grace'],'| integrity',len(d['integrity']))"
echo "  data.csv files left: $(find $DATA/datasets -name data.csv | wc -l)"
echo "## API log: tracebacks $(grep -c Traceback $DATA.s3-api.log), HTTP 500 $(grep -c '\" 500' $DATA.s3-api.log)"
