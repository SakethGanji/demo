#!/bin/bash
# P05 chain after the 10M upload (big.sh): repeat commit, record, manifest, verify, runs, tamper → 409, export, verify-all, wrapper, stale check
S=${S:-.}; cd $S/final
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
U=$(head -1 up_big.json | python -c "import sys,json;print(json.load(sys.stdin)['upload_id'])"); DS=$(head -1 commit_big.json | python -c "import sys,json;print(json.load(sys.stdin)['version_id'])")
DB=$S/p05final/ensemble.sqlite3
echo "upload $U  dataset $DS"
python - <<'PY'
import json
c=json.loads(open('check_big.json').readline()); t=c['tables'][0]
print('## /check:', t['name'], t['n_rows'], 'rows', t['n_cols'], 'cols; encoding', t['encoding'], 'sep', t['separator'], '; check_sha256', c['check_sha256'][:16], '; blocks', c['blocks'], '; warnings', c['warnings'], '; seconds', c['seconds'])
for col in t['schema']: print('  ', col['name'], col['type'], 'nullable' if col['nullable'] else '', '|', col['reason'])
v=json.loads(open('commit_big.json').readline())
print('## /commit:', {k:v[k] for k in ('version_id','version_no','storage','n_rows','n_cols','primary_table','data_sha256','manifest_sha256','repeat')})
print('   table', v['tables'][0]['table_name'], v['tables'][0]['table_sha256'], v['tables'][0]['bytes'], 'bytes', v['tables'][0]['change'])
PY
echo "## repeat /commit"; $A/uploads/$U/commit -X POST -d '{}' -w ' HTTP %{http_code} %{time_total}s\n' | python -c "import sys,json;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);d=json.loads(b);print({k:d[k] for k in ('version_id','data_sha256','repeat')},'HTTP'+h.strip())"
echo "## GET /uploads/$U"; $A/uploads/$U | python -c "import sys,json;d=json.load(sys.stdin);print({k:d[k] for k in ('status','committed_version_id','check_sha256')})"
echo "## GET /datasets/$DS"; $A/datasets/$DS | python -c "
import sys,json;d=json.load(sys.stdin);p=d['profile'];print({k:d[k] for k in ('id','name','filename','rows','n_cols','kind','target','n_classes','op')})
print('  task',p['task'],'classes',p['classes'],'sampled',p.get('sampled',{}).get('rows'),'of',p.get('sampled',{}).get('of'))
print('  account_no:',[(c['type'],c['dtype'],c['example'],c['unique']) for c in p['columns'] if c['name']=='account_no'])
print('  warnings',p['warnings'])"
echo "## GET /datasets/$DS/manifest"; $A/datasets/$DS/manifest | python -c "
import sys,json;d=json.load(sys.stdin);m=d['manifest'];t=m['data']['tables'][0]
print('manifest_sha256',d['manifest_sha256'][:16],'bytes',d['bytes']);print('table',t['name'],t['table_sha256'][:16],t['n_rows'],'row_id',t['row_id'])
print('files',[(f['sha256'][:12],f['n_rows'],f['row_id_min'],f['row_id_max'],f['bytes']) for f in t['files']])
print('schema',[(c['name'],c['type'],c.get('reason')) for c in t['schema']][:3],'…')
print('provenance op',m['provenance']['op'],'sources',m['provenance']['sources'],'ops',m['provenance']['ops'])"
echo "## GET /datasets/$DS/verify"; $A/datasets/$DS/verify | python -c "import sys,json;d=json.load(sys.stdin);print(d['ok'],d['status'],d['manifest'],d['problems'],[f['status'] for f in d['files']])"

echo "## POST /runs on the 10M version (stopped after data_hashed: the fit itself is not P05's)"
/usr/bin/time -f "  POST /runs took %es" $A/runs -d "{\"dataset_id\":\"$DS\",\"label\":\"y\",\"time_limit\":60,\"presets\":\"medium_quality\",\"drop_columns\":[\"note\"]}" -o run10.json -w '  HTTP %{http_code}\n'
R=$(python -c "import json;print(json.load(open('run10.json'))['id'])"); python -c "import json;c=json.load(open('run10.json'))['config'];print('  config input_table',c['input_table']);print('  data_sha256_expected',c['data_sha256_expected'])"
for i in $(seq 1 120); do n=$(sqlite3 $DB "select count(*) from run_events where run_id='$R' and type='data_ready'" 2>/dev/null || python -c "import sqlite3;print(sqlite3.connect('$DB').execute(\"select count(*) from run_events where run_id='$R' and type='data_ready'\").fetchone()[0])"); [ "$n" = 1 ] && break; sleep 2; done
python -c "
import sqlite3,json;c=sqlite3.connect('$DB')
for t,d in c.execute(\"select type,data from run_events where run_id='$R' and type in ('data_hashed','splits_hashed','data_ready') order by seq\"):
    d=json.loads(d); print('  event',t,{k:d.get(k) for k in ('match','data_sha256','table','storage','rows','train_rows','holdout_rows','read') if k in d})"
curl -s -X POST -H X-Ensemble-Actor:Ana localhost:8000/api/runs/$R/cancel; echo

echo "## small upload (3 000 rows) → full run to the end"
U2=$(curl -s -H X-Ensemble-Actor:Ana localhost:8000/api/uploads -F file=@$S/small.csv -F target=y | python -c "import sys,json;print(json.load(sys.stdin)['upload_id'])")
C1=$($A/uploads/$U2/check -X POST | python -c "import sys,json;print(json.load(sys.stdin)['check_sha256'])")
echo "  check $C1"
echo "  PATCH types {dmy: DATE %d/%m/%Y}:"; $A/uploads/$U2 -X PATCH -d '{"types":{"dmy":"DATE"},"formats":{"dmy":"%d/%m/%Y"},"name":"small p05"}' | python -c "import sys,json;d=json.load(sys.stdin);print('  ',d['status'],d['plan'])"
echo "  commit before a new check:"; $A/uploads/$U2/commit -X POST -d '{}' -w '  HTTP %{http_code}\n'
C2=$($A/uploads/$U2/check -X POST | python -c "import sys,json;d=json.load(sys.stdin);print(d['check_sha256'])"); echo "  re-check $C2"
echo "  commit naming the old check:"; $A/uploads/$U2/commit -X POST -d "{\"check_sha256\":\"$C1\"}" -w '  HTTP %{http_code}\n'
echo "  two commits at once:"; ($A/uploads/$U2/commit -X POST -d "{\"check_sha256\":\"$C2\"}" -w ' HTTP %{http_code}\n' > c_a.txt &) ; $A/uploads/$U2/commit -X POST -d "{\"check_sha256\":\"$C2\"}" -w ' HTTP %{http_code}\n' > c_b.txt; sleep 2
for f in c_a.txt c_b.txt; do python -c "import sys;t=open('$f').read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);print('  ',{k:d[k] for k in ('version_id','repeat')},'HTTP'+h.strip(), [ (c['name'],c['type']) for c in d['tables'][0]['schema'] if c['name']=='dmy'])"; done
DS2=$(python -c "import json;print(json.loads(open('c_b.txt').read().rsplit(' HTTP',1)[0])['version_id'])")
$A/runs -d "{\"dataset_id\":\"$DS2\",\"label\":\"y\",\"time_limit\":30,\"presets\":\"medium_quality\"}" -o run2.json -w '  POST /runs HTTP %{http_code}\n'
R2=$(python -c "import json;print(json.load(open('run2.json'))['id'])")
for i in $(seq 1 100); do s=$($A/runs/$R2 | python -c "import sys,json;print(json.load(sys.stdin)['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 4; done
$A/runs/$R2 | python -c "import sys,json;d=json.load(sys.stdin);print('  run',d['id'],d['status'],'data_integrity',{k:d['data_integrity'][k] for k in ('match','storage','table','table_sha256')},'holdout auc',(d.get('holdout') or {}).get('metrics',{}).get('roc_auc'))"
echo "## run_inputs"; python -c "
import sqlite3;c=sqlite3.connect('$DB');c.row_factory=sqlite3.Row
for r in c.execute('select run_id, role, version_id, table_name, table_sha256, read_sha256, match, legacy_csv_sha256, split_legacy_sha256, read_mode, rows_total, rows_used, columns_dropped from run_inputs order by created_at'): print(' ',{k:(r[k][:16] if isinstance(r[k],str) and len(r[k])==64 else r[k]) for k in r.keys()})"

echo "## flip one byte of the 10M table's first part → POST /runs"
K=$(python -c "import sqlite3;print(sqlite3.connect('$DB').execute(\"select b.key from table_files f join blobs b on b.sha256=f.blob_sha256 where f.version_id='$DS' order by f.part\").fetchone()[0])"); F=$S/p05final/lake/$K
chmod u+w $F; python -c "f=open('$F','r+b');f.seek(1822593);b=f.read(1);f.seek(1822593);f.write(bytes([b[0]^1]));f.close()"; chmod 0444 $F
$A/runs -d "{\"dataset_id\":\"$DS\",\"label\":\"y\",\"time_limit\":60}" -w '\n  HTTP %{http_code}\n'
echo "  the next one (version now flagged by the failed check):"; $A/runs -d "{\"dataset_id\":\"$DS\",\"label\":\"y\",\"time_limit\":60}" -w '\n  HTTP %{http_code}\n' | cut -c1-200
echo "  GET verify:"; $A/datasets/$DS/verify | python -c "import sys,json;d=json.load(sys.stdin);print('  ',d['ok'],d['status'],d['problems'])"
chmod u+w $F; python -c "f=open('$F','r+b');f.seek(1822593);b=f.read(1);f.seek(1822593);f.write(bytes([b[0]^1]));f.close()"; chmod 0444 $F
echo "  byte restored; GET verify:"; $A/datasets/$DS/verify | python -c "import sys,json;d=json.load(sys.stdin);print('  ',d['ok'],d['status'])"

echo "## export"
/usr/bin/time -f "  export csv took %es" $A/datasets/$DS/export?format=csv\&by=Ana -D h10.txt -o exp10.csv; grep -i "x-ensemble" h10.txt
sha256sum exp10.csv $S/apps10m.csv | sed 's#'$S'/##'; head -3 exp10.csv
$A/datasets/$DS2/export?by=Ana -o exp2.zip; unzip -l exp2.zip | tail -6; unzip -p exp2.zip schema.json | python -c "import sys,json;d=json.load(sys.stdin);print('  schema.json',d['format'],[(c['name'],c['kind'],c['type'],c.get('reason')) for c in d['tables'][0]['columns']][:3],'…')"
unzip -p exp2.zip data.csv | head -2

echo "## POST /api/data/verify-all (admin)"; curl -s -H Content-Type:application/json localhost:8000/api/data/verify-all -d '{"by":"Ada"}' | python -c "import sys,json;d=json.load(sys.stdin);print('  datasets',d['datasets'],'checked',d['checked'],'failed',d['failed'],'ok',d['ok'])"
python -c "import sqlite3;c=sqlite3.connect('$DB');print('  storage of the checked versions:',c.execute('select storage, count(*) from datasets group by 1').fetchall())"

echo "## POST /api/datasets (the old route, now a wrapper)"
curl -s localhost:8000/api/datasets -F file=@$S/small.csv -F by=Ben -F target=y -w ' HTTP %{http_code}' -o wrap.json; echo
python -c "
import json,urllib.request
d=json.load(open('wrap.json')); old=json.load(urllib.request.urlopen('http://localhost:8000/api/datasets/credit-default'))
print('  keys same as a CSV-era record:', sorted(d)==sorted(old), '| profile keys missing vs CSV-era:', sorted(set(old['profile'])-set(d['profile'])))
print('  ',{k:d[k] for k in ('id','name','filename','source','rows','n_cols','kind','target','n_classes','op')},'storage',d['profile']['storage'],'split',d['profile']['splits']['key'])"
for f in tiny.csv dup.csv empty.csv ragged.csv; do curl -s localhost:8000/api/datasets -F file=@$S/$f -F by=Ana -w ' HTTP %{http_code}\n' | sed "s/^/  $f: /"; done
echo "## tracebacks in the API log: $(grep -c Traceback $S/api.log)"
