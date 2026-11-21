#!/usr/bin/env bash
# P09 verification chain (DLA4 #3), as the UI calls the API. Needs the API on :8000 with a fresh ENSEMBLE_DATA_DIR=$DATA
# and ENSEMBLE_ADMINS=Ada, accounts.csv from make_data.py in the current directory, and $PY = a python with pyarrow
# (for tamper.py, which opens the DB and the lake directly, as someone with file access would).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
U="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"
py() { python3 -c "import sys,json;t=sys.stdin.read()
try: b,h=t.rsplit(' HTTP',1)
except ValueError: b,h=t,''
d=json.loads(b);d=d.get('detail',d) if isinstance(d,dict) else d;h=h.strip()
$1"; }
wait_run() { for i in $(seq 1 90); do s=$($A/runs/$1 | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 4; done; }
run() {  # run <name> <dataset> [actor] [extra json] → prints the run id
  local who=${3:-Ana} extra=${4:-}
  curl -s -H X-Ensemble-Actor:$who -H Content-Type:application/json localhost:8000/api/runs \
    -d "{\"dataset_id\":\"$2\",\"label\":\"y\",\"time_limit\":20,\"presets\":\"medium_quality\",\"drop_columns\":[\"acct\"],\"name\":\"$1\"$extra}" \
    -w ' HTTP %{http_code}' | py "print(d if isinstance(d,str) and h.startswith('2') else d.get('id') if isinstance(d,dict) and d.get('id') else 'HTTP '+h+' | '+(d if isinstance(d,str) else str(d.get('code') or '')+' | '+str(d.get('message') or d))[:420])"; }
show_run() { $A/runs/$1 | py "si=d.get('splits_integrity') or {};print(' ',d['id'],d['name'],d['status'],'| split',si.get('split_id'),'match',si.get('match'),'failed',si.get('failed'),'override',si.get('override'),'| holdout rows',(d.get('data') or {}).get('holdout_rows'))"; }
checks() { py "[print(' ',s['id'],s.get('split_key'),s['status']) or [print('    check',c['n'],c['check'],'ok    ' if c['ok'] else 'FAILED','-',c['detail']) for c in s['checks']] for s in d['splits']]"; }

echo "## 1. v1: POST /uploads → check → commit (900 rows); its split is a Parquet row + blob + audit row"
UP=$($U/uploads -F file=@accounts.csv -F name="dla4 accounts" -F target=y | py "print(d['upload_id'])")
$U/uploads/$UP/check -X POST >/dev/null
V1=$($A/uploads/$UP/commit -X POST -d '{"reason":"P09 v1"}' | py "print(d['version_id'])")
$A/datasets/$V1/splits | py "[print('  v1',V,s['key'],'holdout_rows',s['holdout_rows'],'split_id',s['split_id'],'split_sha256',s['split_sha256'][:12]+'…','blob',s['blob_sha256'][:12]+'…') for V in ['$V1'] for s in d['splits']]"
$PY "$HERE/q.py" "$DATA/ensemble.sqlite3" "select '  splits rows', count(*) from splits" "select '  split_created audit rows', count(*) from audit_log where action='split_created'" \
  "select '  split_files rows for Parquet versions', count(*) from split_files f join datasets d on d.id=f.dataset_id where d.storage='parquet'" \
  "select '  datasets.splits_sha256 of v1', splits_sha256 from datasets where id='$V1'"

echo "## 2. r1, r2 on v1 (POST /runs): each pins the split blob and records split_sha256 in run_inputs"
R1=$(run r1 $V1); R2=$(run r2 $V1); wait_run $R1; wait_run $R2; show_run $R1; show_run $R2
$PY "$HERE/q.py" "$DATA/ensemble.sqlite3" "select '  run_inputs', run_id, split_id, substr(split_sha256,1,12) from run_inputs order by created_at" \
  "select '  blob_refs', owner_kind, owner_id, role, substr(blob_sha256,1,12) from blob_refs where role='split' order by owner_kind, owner_id"

echo "## 3. replace the split blob / update the splits row with the DB opened directly → the trigger refuses"
$PY "$HERE/tamper.py" "$DATA" $V1 update | sed 's/^/  /'

echo "## 4. simulated tamper: a new blob (50-row holdout) + a new splits row, DB opened directly (delete trigger dropped)"
$PY "$HERE/tamper.py" "$DATA" $V1 replace | sed 's/^/  /'
echo "## 5. POST /runs → 409"
run r3 $V1 | sed 's/^/  /'
echo "## 6. GET …/verify names the check"
$A/datasets/$V1/verify | py "print('  ok',d['ok'],'data_ok',d['data_ok'],'splits_ok',d['splits_ok'])"
$A/datasets/$V1/verify | checks
echo "## 7. the tamperer also forges a split_created audit row → check 3 passes, check 4 still fails"
$PY "$HERE/tamper.py" "$DATA" $V1 forge | sed 's/^/  /'
$A/datasets/$V1/verify | checks
$A/datasets/$V1/verify -X POST -d '{"by":"Ana"}' | py "r=d['results'][0];print('  POST …/verify ok',d['ok'],'status',r['status'],'| remedy:',r.get('remedy','')[:330])"

echo "## 8. DLA4 #3's run: Ana is refused; Ada (admin) trains on the 50-row holdout with an audited override"
run r3 $V1 | sed 's/^/  /'
R3=$(run "r3 (override)" $V1 Ada ',"by":"Ada","integrity_override_reason":"reproduce DLA4 #3: train on the tampered 50-row holdout"')
wait_run $R3; show_run $R3

echo "## 9. POST /compare r1 vs r3 (50 of 180 holdout rows in common)"
$A/compare -d "{\"a\":\"$R1\",\"b\":\"$R3\"}" > cmp13.json
py "print('  paired',d['paired'],'|',d['label']);print('  overlap',d['overlap']);print('  table',[(t['side'],t['id'],t['holdout_rows'],round(t['holdout_score'],4)) for t in d['table']],'delta',d.get('delta'));s=json.dumps(d).replace('\"same_holdout\"','');print('  the text \"same holdout\" appears:','same holdout' in s)" < cmp13.json

echo "## 10. POST …/restore-splits {} → the offer (r1's pinned blob); then apply it"
$A/datasets/$V1/restore-splits -X POST -d '{}' > offer.json
py "o=d['offers'][0];print('  applied',d['applied'],'| now',{k:o['now'][k] for k in ('split_id','n_holdout')},'failed',o['failed']);print('  offer',{k:o['offer'][k] for k in ('run_id','split_id','split_sha256','blob_sha256','n_holdout','runs_agreeing')})" < offer.json
B=$(py "print(d['offers'][0]['offer']['blob_sha256'])" < offer.json); K=$(py "print(d['offers'][0]['split_key'])" < offer.json)
$A/datasets/$V1/restore-splits -X POST -d "{\"split_key\":\"$K\",\"blob_sha256\":\"$B\",\"by\":\"Ana\",\"reason\":\"DLA4 #3: put back the split r1 and r2 trained on\"}" \
  | py "print('  applied',d['applied'],'restored',{k:d['restored'][k] for k in ('id','n_holdout')},'from',d['from_run'],'replaced',{k:d['replaced'][k] for k in ('id','n_holdout')});print('  verify after',d['verify']['ok'],[s['status'] for r in d['verify']['results'] for s in r['splits']])"
$A/datasets/$V1/restore-splits -X POST -d '{}' -w ' HTTP %{http_code}' | py "print('  again: HTTP',h,d['code'])"
$PY "$HERE/q.py" "$DATA/ensemble.sqlite3" "select '  triggers', group_concat(name) from sqlite_master where type='trigger' and name like 'splits_ro%'" \
  "select '  audit', id, action, actor, substr(comment,1,70) from audit_log where action in ('split_created','training_refused','training_integrity_override','dataset_splits_restored') order by id"

echo "## 11. r4 on the restored split: all five checks pass (r3's overridden run is not evidence); paired with r1"
R4=$(run r4 $V1); wait_run $R4; show_run $R4
$A/runs/$R4 | py "print('  check 4:',[c['detail'] for c in d['splits_integrity']['checks'] if c['n']==4][0])"
$A/compare -d "{\"a\":\"$R1\",\"b\":\"$R4\"}" | py "print('  compare r1 r4:',d['paired'],'|',d['label'],'| overlap',d['overlap']['text'],d['overlap']['share'])"

echo "## 12. v2 = POST …/edit (delete 40 holdout rows, fix 1 cell, add 2 rows): the split is carried by join in the commit"
OPS=$(python3 -c "
import json;sp=json.load(open('$DATA/datasets/$V1/splits/random-stratified__y-95cb0bfd.json'))
print(json.dumps([{'op':'delete_rows','row_ids':sp['holdout'][:40]},{'op':'set_cells','cells':[{'row_id':3,'column':'income','value':51000}]},
 {'op':'add_rows','rows':[{'acct':'A90000','income':30000,'age':40,'segment':'sme','tenure':2,'y':1},{'acct':'A90001','income':90000,'age':50,'segment':'corp','tenure':9,'y':0}]}]))")
V2=$($A/datasets/$V1/edit -X POST -d "{\"by\":\"Ana\",\"reason\":\"drop 40 rows, fix 1, add 2\",\"ops\":$OPS}" | py "print(d['id'])")
$A/datasets/$V2/splits | py "[print('  v2',s['split_id'],'holdout_rows',s['holdout_rows'],'carried_from',s['carried_from'],s.get('carried')) for s in d['splits']]"
$A/datasets/$V2/verify | py "[print('  verify v2',s['status'],[c['detail'] for c in s['checks'] if c['n']==5]) for s in d['splits']]"
$A/datasets/$V1/diff/$V2 | py "r=d.get('rows',d);print('  diff v1 v2',{k:r.get(k) for k in ('a','b','matched','changed','added','removed','matched_by')})"
python3 -c "import json;d=json.load(open('$DATA/datasets/$V2/lineage.json'));print('  v2 lineage.json has a row map:', any(k in d for k in ('root_'+'rows','parent_rows')),'| files:', sorted(__import__('os').listdir('$DATA/datasets/$V2')))"
R5=$(run r5 $V2); wait_run $R5; show_run $R5
$A/compare -d "{\"a\":\"$R1\",\"b\":\"$R5\"}" | py "print('  compare r1 (v1) r5 (v2):',d['paired'],'|',d['label'],'| overlap',d['overlap']['n_common'],'of',max(d['overlap']['n_a'],d['overlap']['n_b']),d['overlap']['share'],'| Δ',round(d['delta'],4),'CI',[round(x,4) for x in d['ci']])"

echo "## 13. v3 = POST /uploads parent=v2 → commit {match: key acct}: carried from v2's split; then tamper v3's carried split"
UP=$($U/uploads -F file=@accounts.csv -F parent_version_id=$V2 | py "print(d['upload_id'])"); $U/uploads/$UP/check -X POST >/dev/null
V3=$($A/uploads/$UP/commit -X POST -d '{"match":{"method":"key","columns":["acct"]},"reason":"the full file again","acknowledge":["holdout_identity_loss"]}' | py "print(d['version_id'])")
$A/datasets/$V3/splits | py "[print('  v3',s['split_id'],'holdout_rows',s['holdout_rows'],'carried_from',s['carried_from'],s.get('carried')) for s in d['splits']]"
$PY "$HERE/tamper.py" "$DATA" $V3 replace | sed 's/^/  /'
$A/datasets/$V3/verify | checks
$A/datasets/$V3/restore-splits -X POST -d '{}' | py "o=d['offers'][0];print('  restore offer',o['offer'],'|',o.get('why_none'))"
echo "$V1 $V2 $V3 $R1 $R2 $R3 $R4 $R5" > ids.txt
