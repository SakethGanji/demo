#!/usr/bin/env bash
# P11 verification chain (§9.1 row P11), as the UI calls the API. Needs the API on :8000 with a fresh ENSEMBLE_DATA_DIR=$DATA,
# ENSEMBLE_HOLDOUT_BATCH_ROWS=100000 (so the 396k-row holdout is several batches), accounts2m.csv from make_data.py in the
# current directory and $PY = the backend's python (check.py reads the DB, the lake and the run folder directly).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
U="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"
py() { python3 -c "import sys,json;d=json.load(sys.stdin)
$1"; }
wait_run() { for i in $(seq 1 120); do s=$($A/runs/$1 | py "print(d['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 5; done; }
run() { $A/runs -d "{\"dataset_id\":\"$1\",\"label\":\"y\",\"time_limit\":60,\"presets\":\"medium_quality\",\"name\":\"$2\"$3}" | py "print(d.get('id') or d)"; }
show() { $A/runs/$1 | py "
f=d['data']['frame'];sp=f.get('sample_spec') or {};h=d['holdout'];b=f['plan']['budget'];e=f['plan'].get('estimate') or {}
print('  ',d['id'],d['status'],d.get('error') or '')
print('   GET /runs/{id}: rows_total',d['data']['rows_total'],'| rows_used',d['data']['rows_used'],'| sample_sha256',d['data']['sample_sha256'])
print('   frame: sampled',f['sampled'],'| estimate',e.get('mb'),'MB',e.get('bytes_per_row'),'B/row | budget',b['mb'],'MB:',b['rule'])
print('   card text:',f['text'])
if sp: print('   sample_spec: rate',sp['rate'],'rates',sp['rates'],'| per stratum',{k:(v['rows'],v['kept']) for k,v in sp['per_stratum'].items()})
print('   holdout: n_rows',h['n_rows'],'| scored',h.get('scored'),'| in memory (diagnostics)',f['holdout']['in_memory'])
print('   holdout metrics',{k:round(v,6) for k,v in h['metrics'].items()})"; }

echo "## 1. v1: POST /uploads → check → commit (2,000,000 rows × 27 columns)"
UP=$($U/uploads -F file=@accounts2m.csv -F name="p11 accounts" -F target=y | py "print(d['upload_id'])")
$U/uploads/$UP/check -X POST >/dev/null
V1=$($A/uploads/$UP/commit -X POST -d '{"reason":"P11 v1"}' | py "print(d['version_id'])"); echo "  v1 $V1"
echo "## 2. v2 = POST …/edit delete_rows (every 100th __row_id): 1,980,000 rows, __row_id with 20,000 gaps"
python3 -c "import json;print(json.dumps({'by':'Ana','reason':'P11: gaps in __row_id','ops':[{'op':'delete_rows','row_ids':list(range(0,2000000,100))}]}))" > edit.json
V2=$($A/datasets/$V1/edit -X POST -d @edit.json | py "print(d['id'])"); echo "  v2 $V2"
echo "## 3. r1 = POST /runs {dataset_id: v2, label: y, frame_budget_mb: 300} → GET /runs/{id}"
R1=$(run $V2 "p11 r1 (300 MB)" ',"frame_budget_mb":300'); wait_run $R1; show $R1
echo "## 4. r2 = the same request again → the same sample_sha256"
R2=$(run $V2 "p11 r2 (300 MB, repeat)" ',"frame_budget_mb":300'); wait_run $R2; show $R2
$A/runs/$R1 | py "print('   r1',d['data']['sample_sha256'])"; $A/runs/$R2 | py "print('   r2',d['data']['sample_sha256'])"
echo "## 5. r3 = no frame_budget_mb (the default: 10 % of the worker's RAM) → the whole table, no sample"
R3=$(run $V2 "p11 r3 (default budget)" ''); wait_run $R3; show $R3
echo "## 6. independent checks (check.py: DB + lake + run folder, splitmix64 written out again)"
for R in $R1 $R2; do echo "  $R"; $PY "$HERE/check.py" "$DATA" $R; done
echo "  $R1 --evaluate"; $PY "$HERE/check.py" "$DATA" $R1 --evaluate | grep -v "^  [12]\.\|blob_refs\|run_inputs\|version"
echo "  $R3 --evaluate"; $PY "$HERE/check.py" "$DATA" $R3 --evaluate
echo "$V1 $V2 $R1 $R2 $R3" > ids.txt
