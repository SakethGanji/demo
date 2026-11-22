#!/usr/bin/env bash
# F-X3b verification on one API: X3 lane A's repros (setup, joins1, reg, pins3), then M8, C1, M7, M9 GC, minors.
# Usage: chain.sh <api host:port> <data dir of the API> <work dir> <files dir (X3 A make_data + make_batch)> [Postgres URL]
set -u
P=$1; D=$2; W=$3; F=$4; [ -n "${5:-}" ] && export ENSEMBLE_DB_URL=$5; A=localhost:$P/api; X=/home/user/demo/docs/progress/X3/A; HERE=$(cd "$(dirname "$0")" && pwd)
PY=/var/tmp/fx3b-venv/bin/python; export PATH=/var/tmp/fx3b-venv/bin:$PATH
H="-H X-Ensemble-Actor:Ana"; HA="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
if [ "${MINORS_ONLY:-}" != 1 ]; then
echo "# setup (X3 A setup.sh)"; bash $X/setup.sh $A $F $W 2>&1 | tail -4
echo "# M5, M6 (X3 A joins1.sh J1–J4)"; bash $X/joins1.sh $A $W 2>&1 | sed -n 1,12p
fi
cd $W; R=$(cat v1.vid); V=$(cat vkeys.vid)
if [ "${MINORS_ONLY:-}" != 1 ]; then
echo "# M6 more: DATE = VARCHAR key, and the DATE = TIMESTAMP frame's cells"
curl -s $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"j_dt_text\",\"version_id\":\"$V\",\"spec\":{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":\"pays\",\"on\":[[\"applied_on\",\"paid_dmy\"]],\"prefix\":\"q__\"}]}}" -w ' %{http_code}\n' | tail -c 330; echo
J1=$(python3 -c "import json;print(json.load(open('j1.json')).get('id',''))")
curl -s $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$J1\"}" > fj1.json
FJ1=$(python3 -c "import json;print(json.load(open('fj1.json'))['id'])")
curl -s $H "$A/datasets/$R/frames/$FJ1/rows?limit=3" | python3 -c "import json,sys;d=json.load(sys.stdin);print('frame rows', d['columns'][-3:], [r[-3:] for r in d['rows']])"
echo "# C1, M7 (X3 A reg.sh: a join model, the two-cust model and its batch)"; timeout 1200 bash $X/reg.sh $A $F $W 2>&1
echo "# C1 on rows (c1.py)"; ENSEMBLE_DATA_DIR=$D $PY $HERE/c1.py localhost:$P $(cat m1.id) $F/batch/clean $D/registry/x3-join/v1 2>&1 | grep -v Warn
echo "# M7: role-named files"; mkdir -p m7; cp $F/batch/clean/apps.csv m7/; cp $F/batch/clean/cust.csv m7/cust.join0.csv; cp $F/batch/clean/cust.csv m7/cust.join1.csv
B=$(curl -s $H -X POST $A/registry/versions/$(cat m2.id)/batches -F files=@m7/apps.csv -F files=@m7/cust.join0.csv -F files=@m7/cust.join1.csv -F created_by=Ana | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
until curl -s $H $A/registry/batches/$B | python3 -c "
import json,sys;d=json.load(sys.stdin)
if d['status'] in ('queued','running','pending'): sys.exit(1)
print('batch', d['status'], d['n_rows'], list(d['result']['summary']['join']['tables']))"; do sleep 3; done
curl -s $H $A/registry/batches/$B/download | sed -n 2p | cut -d, -f1-13
curl -s $H $J -X POST $A/registry/versions/$(cat m2.id)/predict -d '{"row":{"app_id":"A00001","cust":"C0638","applied_on":"2024-12-10","applied_day":"2024-12-10 00:00:00","x1":0.3481},"explain":false,"tables":{"cust.join0":"pinned","cust.join1":"pinned"}}' | python3 -c "import json,sys;d=json.load(sys.stdin);print('predict', d.get('prediction'), d.get('probabilities'))"
echo "# M9 (X3 A pins3.sh), then GC"; bash $X/pins3.sh $A $F $W 2>&1 | grep -v "^HTTP 200 \[('jf"
FX=$(curl -s $H "$A/datasets/$R/joins" | python3 -c "import json,sys;print([j['id'] for j in json.load(sys.stdin)['joins'] if j['name']=='pin_e9'][0])")
FX=$(curl -s $H "$A/datasets/$R/joins/$FX" | python3 -c "import json,sys;print(json.load(sys.stdin)['frames'][0]['id'])")
curl -s $H "$A/datasets/$R/frames/$FX?verify=1" | python3 -c "import json,sys;d=json.load(sys.stdin);v=d['verify'];print('frame verify ok',v['ok'],'inputs',[(x['table'],x['ok'],x.get('state')) for x in v['inputs']],'inputs_deleted',v.get('inputs_deleted'))"
curl -s $HA "$A/admin/lake/verify-all?by=Ada" > /dev/null
GP=$(curl -s $HA $J -X POST "$A/admin/lake/gc?dry_run=1" -d '{"by":"Ada"}' | tee gc1.json | python3 -c "import json,sys;print(json.load(sys.stdin)['plan_id'])")
python3 -c "import json;d=json.load(open('gc1.json'));print('gc dry run join_cache candidates',[(x['sha256'][:12],x['kind']) for x in d['candidates'] if x['kind']=='join_cache'])"
curl -s $HA $J -X POST "$A/admin/lake/gc?dry_run=0" -d "{\"by\":\"Ada\",\"plan_id\":\"$GP\",\"confirm\":\"gc\"}" | python3 -c "import json,sys;d=json.load(sys.stdin);print('gc run', {k:v for k,v in d.items() if k in ('deleted','skipped')})"
curl -s $HA $J -X POST $A/datasets/$(cat ext_k.id)/delete -d '{"by":"Ada","reason":"F-X3b"}' | python3 -c "import json,sys;d=json.load(sys.stdin);print('delete a pinned input a run trained on:', str(d.get('detail',d))[:160])"
echo "# M8 (m8.py)"; $PY $HERE/m8.py localhost:$P $D $R $V t$P
fi
minors() {
echo "# minors: m5 unreadable batch files, m16 repeated grain key, m15 cast failures, m6 numeric version_id"
M=$(cat m1.id); head -c 2560 /dev/urandom > bin.csv; printf 'PAR1garbagePAR1' > g.parquet; printf 'PK\x03\x04garbage' > book.xlsx; (cat $F/batch/clean/apps.csv; sed -n 2,3p $F/batch/clean/apps.csv) > dup_apps.csv
curl -s $H -X POST $A/registry/versions/$M/batches -F "files=@bin.csv;filename=apps.csv" -F files=@$F/batch/clean/cust.csv -F files=@$F/batch/clean/pays.csv -F created_by=Ana -w ' %{http_code}\n' | tail -c 200
curl -s $H -X POST $A/registry/versions/$M/batches -F "files=@g.parquet;filename=apps.parquet" -F files=@$F/batch/clean/cust.csv -F files=@$F/batch/clean/pays.csv -F created_by=Ana -w ' %{http_code}\n' | tail -c 200
curl -s $H -X POST $A/registry/versions/$M/batches -F files=@book.xlsx -F created_by=Ana -w ' %{http_code}\n' | tail -c 200
curl -s $H -X POST $A/registry/versions/$M/batches -F "files=@dup_apps.csv;filename=apps.csv" -F files=@$F/batch/clean/cust.csv -F files=@$F/batch/clean/pays.csv -F created_by=Ana -w ' %{http_code}\n' | tail -c 200
B=$(curl -s $H -X POST $A/registry/versions/$M/batches -F files=@$F/batch/coerce/apps.csv -F files=@$F/batch/coerce/cust.csv -F files=@$F/batch/coerce/pays.csv -F created_by=Ana | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
until curl -s $H $A/registry/batches/$B | python3 -c "
import json,sys;d=json.load(sys.stdin)
if d['status'] in ('queued','running','pending'): sys.exit(1)
j=d['result']['summary']['join'];print('coerce batch', d['status'], {t:x['cast_failures'] for t,x in j['tables'].items()}, j.get('warnings'))"; do sleep 3; done
curl -s $H $J -X POST $A/datasets/$R/joins -d '{"name":"fzv","version_id":5,"spec":{"grain":"apps","joins":[{"type":"n:1","table":"cust","on":["cust"]}]}}' -w ' %{http_code}\n'
curl -s $H $J -X POST $A/datasets/$R/joins/$(cat j4.id)/validate -d '{"version_id":5}' -w ' %{http_code}\n'
}
minors
grep -c "Traceback" $D/api.log | sed 's/^/tracebacks in api.log: /'
