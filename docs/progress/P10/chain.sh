#!/usr/bin/env bash
# P10 verification chain. Needs: $S (scratch) with venv/, files/, state.json, before/ (recorded with the pre-P10 head
# acfe988 on the same data), data_before_copy/ (the CSV-era data dir as it was when before/ was recorded).
set -u
HERE=$(cd "$(dirname "$0")" && pwd); export PYBIN=$S/venv/bin; PY=$PYBIN/python
ST=$S/state.json; DATA=$S/data; DB=$DATA/ensemble.sqlite3
g() { python3 -c "import json;print(json.load(open('$ST'))['$1'])"; }
A="curl -s -H X-Ensemble-Actor:Ada -H Content-Type:application/json localhost:8000/api"
poll() { for i in $(seq 120); do s=$($A/admin/lake/migrate | python3 -c "import sys,json;d=json.load(sys.stdin);p=d['progress'] or {};j=d.get('job') or {};print(p.get('job_id'),p.get('status'),j.get('status'))"); case "$s" in "$1 finished succeeded"|"$1 "*" failed") break;; esac; sleep 2; done; }
status() { $A/admin/lake/migrate | python3 -c "
import sys,json;d=json.load(sys.stdin);p=d['progress'];print('  job',p['job_id'],'|',{k:p[k] for k in ('total','done','migrated','skipped','failed')},'| by status',d['by_status'],'| CSV-era left',d['csv_versions_left'])"; }

echo "## 0. the CSV-era data dir as it was when before/ was recorded (pre-P10 head acfe988), started with this code"
pkill -f "uvicorn app.main:app"; sleep 2; rm -rf $DATA; cp -r $S/data_before_copy $DATA
$HERE/run_api.sh /home/user/demo $DATA
$PY $HERE/q.py $DB "select '  versions', count(*), sum(storage='csv'), sum(storage='parquet') from datasets" \
  "select '  triggers', group_concat(name) from sqlite_master where type='trigger' and name='ds_hash_ro'" \
  "select '  split_files rows', count(*) from split_files"

echo "## 1. POST /api/admin/lake/migrate?dry_run=1 → the plan (nothing written)"
$A/admin/lake/migrate?dry_run=1 -X POST > $S/plan.json
python3 -c "
import json;d=json.load(open('$S/plan.json'));print('  versions',d['versions'],'by action',d['by_action'])
for r in d['plan']: print('  ',r['version_id'],'v'+str(r.get('v')),r.get('state'),'→',r['action'],'|',r.get('reason'),'|',r.get('read') or '','|',(r.get('message') or '')[:170])"
$PY $HERE/q.py $DB "select '  after the dry run: lake_migrations rows', count(*) from lake_migrations" "select '  parquet versions', count(*) from datasets where storage='parquet'"

echo "## 2. a first job on the loans root only (v1, v2), then a crash simulated between v1's commit and its folder rename"
J=$($A/admin/lake/migrate?dry_run=0 -X POST -d "{\"by\":\"Ada\",\"only\":[\"$(g L1)\",\"$(g L2)\"]}" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job']['id'])")
echo "  202 job $J"; poll $J; status
L1=$(g L1); mv $DATA/datasets/$L1 $DATA/datasets/$L1.fresh && mv $DATA/datasets/$L1.migrated $DATA/datasets/$L1 && rm -rf $DATA/datasets/$L1.fresh
$PY -c "import sqlite3;c=sqlite3.connect('$DB');c.execute(\"UPDATE lake_migrations SET folder_state='pending' WHERE version_id='$L1'\");c.commit()"
echo "  simulated: $L1 committed, its folder not renamed yet (folder_state pending, data.csv still in $L1/)"

echo "## 3. the full job (resumes: finishes $L1's folder step, migrates the rest, re-checks the skipped)"
J=$($A/admin/lake/migrate?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job']['id'])")
echo "  202 job $J"; poll $J; status
$A/admin/lake/migrate | python3 -c "
import sys,json;d=json.load(sys.stdin)
for r in d['versions']: print('  ',r['version_id'],r['status'],r['reason'] or '','| folder',r['folder_state'],'| attempts',r['attempts'],'|',(r['message'] or '')[:150])"
ls -d $DATA/datasets/$L1* | sed "s#$DATA/##;s#^#  #"; ls $DATA/datasets/$L1 | sed 's/^/    kept: /'
$PY $HERE/q.py $DB "select '  audit', action, count(*) from audit_log where action like '%migrat%' group by action"

echo "## 4. after: the same calls; JSON diff with before/ (everything outside 'storage' must be equal)"
$PY $HERE/record.py $ST $S/after > /dev/null
$PY $HERE/jdiff.py $S/before $S/after | grep -v "^     body/"
echo "  GET …/verify: 409 not_parquet before (CSV-era); after:"
for v in L1 L2 L3 A2 L4 T2; do python3 -c "
import json;d=json.load(open('$S/after/verify_get_$v.json'));b=d['body'];print('   $v',d['status'],'ok',b.get('ok'),'manifest',b.get('manifest'),'files',[f['status'] for f in b.get('files',[])],'splits',[s['status'] for s in b.get('splits',[])])"; done

echo "## 5. re-verify old runs' data and split hashes (GET /api/admin/lake/runs/{id}/verify): via the legacy blobs"
for k in rL1 rL2 rL4 rA1 rA2 rA4 rS1 rT2 rA5; do $A/admin/lake/runs/$(g $k)/verify | python3 -c "
import sys,json;d=json.load(sys.stdin);x=d['data'];s=d['split'] or {}
print('  $k',d['run_id'],'ok',d['ok'],'| data',x['ok'],(x['recorded'] or '')[:12],'=',(x['now'] or '')[:12],'(',x['basis'][:62],') | split',s.get('ok'),(s.get('recorded') or '')[:12],'(',s.get('basis','')[:52],')')"; done

echo "## 6. train on migrated versions: read_mode engine_v1, holdout ids identical"
$PY $HERE/train_check.py $ST L2 rL2 $DATA
$PY $HERE/train_check.py $ST L3 - $DATA

echo "## 7. ds_hash_ro: a migrated version's hashes cannot be rewritten"
$PY - <<PYEOF
import sqlite3
c = sqlite3.connect("$DB")
for q in ("UPDATE datasets SET data_sha256 = 'x' WHERE id = '$(g L2)'", "UPDATE datasets SET legacy_sha256 = 'x' WHERE id = '$(g L2)'",
          "UPDATE datasets SET manifest_sha256 = NULL WHERE id = '$(g L2)'", "UPDATE datasets SET legacy_sha256 = 'x' WHERE id = '$(g C1)'"):
    try:
        c.execute(q); print("  ALLOWED", q)
    except sqlite3.DatabaseError as e:
        print("  refused:", q.split(" WHERE")[0], "→", e)
PYEOF

echo "## 8. GC dry run: no live blob of a migrated version is a candidate"
$A/admin/lake/gc?dry_run=1 -X POST | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  blobs',d['blobs'],'refs',d['blob_refs'],'| candidates',len(d['candidates']),'| unreferenced in grace',len(d['unreferenced_in_grace']),[(b['kind'],b['state']) for b in d['unreferenced_in_grace']][:6],'| orphans',len(d['orphans']),'| integrity',len(d['integrity']))"

echo "## 9. the Parquet child made from a CSV-era parent before P10 (A3 → A4): diff by __row_id after A3's migration"
$A/datasets/$(g A3)/diff/$(g A4) | python3 -c "
import sys,json;d=json.load(sys.stdin);s=d['rows'];print('  ',{k:s.get(k) for k in ('a','b','matched','changed','added','removed','changed_by_column','matched_by')})"

echo "## 10. the two outside changes are resolved through the DL3/DL4 flows, then the job migrates them"
$PY $HERE/untamper.py $ST $DATA
$A/datasets/$(g T1)/restore-splits -X POST -d '{"by":"Ada","reason":"put back the split as it was drawn"}' | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  restore-splits',d.get('restored'),'verify ok',d.get('verify',{}).get('ok'))"
J=$($A/admin/lake/migrate?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job']['id'])")
echo "  202 job $J"; poll $J; status

echo "## 11. cleanup waits for two verify-all passes since the migration"
$A/admin/lake/migrate/cleanup?dry_run=0 -X POST -d '{"by":"Ada"}' -w ' HTTP %{http_code}' | python3 -c "
import sys;t=sys.stdin.read();print('  cleanup now:',t[-9:],t[:200])"
for i in 1 2; do $A/admin/lake/verify-all?by=Ada | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  verify-all',d['ok'],'datasets',d['datasets'],'checked',d['checked'],'failed',len(d['failed']))"; done
$A/admin/lake/migrate/cleanup?dry_run=1 -X POST -d '{"by":"Ada"}' | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  dry run: allowed',d['allowed'],'passes',len(d['verify_all_passes_since_migration']),'folders',len(d['folders']),'to delete',sum(f['delete'] for f in d['folders']),'bytes',d['bytes'])"
$A/admin/lake/migrate/cleanup?dry_run=0 -X POST -d '{"by":"Ada"}' | python3 -c "
import sys,json;d=json.load(sys.stdin);print('  deleted',sum(f['delete'] for f in d['folders']),'folders')"
echo "  .migrated folders left: $(ls -d $DATA/datasets/*.migrated 2>/dev/null | wc -l); data.csv files left: $(find $DATA/datasets -name data.csv | wc -l)"

echo "## 12. after the cleanup: the same calls again (the data now lives only in the lake)"
$PY $HERE/record.py $ST $S/after2 > /dev/null
$PY $HERE/jdiff.py $S/before $S/after2 | awk '/^verify_get/{skip=1;next} /^[a-z]/{skip=0} !skip' 
$A/admin/lake/verify-all?by=Ada | python3 -c "import sys,json;d=json.load(sys.stdin);print('  verify-all',d['ok'],'checked',d['checked'])"
$PY $HERE/q.py $DB "select '  versions', count(*), sum(storage='csv'), sum(storage='parquet') from datasets"
echo "## API log: tracebacks $(grep -c Traceback $DATA.api.log), HTTP 500 $(grep -c '" 500' $DATA.api.log)"
