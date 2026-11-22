#!/bin/bash
# X5/x2: F-X2's m1_cleanup_midjob.sh, re-pointed: undo outside changes (X2 untamper a/b/d, F-X2 untamper c/e/f), verify-all x2,
# a job slowed to 5 s per version (hook), cleanup?dry_run=0 as soon as this job's first folder exists, then after the job.
# usage: m1_midjob.sh <data dir> <snapshot> <state.json> <port> <out prefix>
D=$1; SNAP=$2; ST=$3; P=$4; OUT=$5; A=(-H X-Ensemble-Actor:Ada -H Content-Type:application/json)
PY=/home/user/demo/backend/.venv/bin/python; X2=/home/user/demo/docs/progress/X2/scripts; FX2=/home/user/demo/docs/progress/F-X2/scripts
pkill -f "port $P\b"; pkill -f "X2PORT=$P\b"; sleep 2
$PY $X2/untamper.py $ST $SNAP $D; $PY $FX2/fx2_untamper.py $ST $SNAP $D
/home/user/demo/docs/progress/X5/x2/scripts/api.sh /home/user/demo $D $P hook X2_SLOW=5
for i in 1 2; do curl -s "${A[@]}" "localhost:$P/api/admin/lake/verify-all?by=Ada" | python3 -c "import json,sys;d=json.load(sys.stdin);print('verify-all', d.get('ok'), d.get('checked'), [f['id'] for f in d.get('failed') or []])"; done
curl -s "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate/cleanup?dry_run=1" -d '{"by":"Ada"}' > ${OUT}_cleanup_dry_before_job.json
python3 -c "import json;d=json.load(open('${OUT}_cleanup_dry_before_job.json'));print('dry run before job3: allowed',d.get('allowed'),'deletable',sum(f['delete'] for f in d['folders']),'of',len(d['folders']))"
NEW=$($PY -c "import sqlite3;print(' '.join(r[0] for r in sqlite3.connect('$D/ensemble.sqlite3').execute(\"select id from datasets where coalesce(storage,'csv')='csv' and coalesce(state,'')<>'deleted'\")))")
echo "CSV-era left: $NEW"
curl -s "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate?dry_run=0" -d '{"by":"Ada"}' | cut -c1-80; echo
for i in $(seq 600); do F=$(for v in $NEW; do [ -d $D/datasets/$v.migrated ] && echo $v; done | head -1); [ -n "$F" ] && break; sleep 0.2; done
echo "first folder of this job: $F.migrated"
curl -s -w "\nHTTP %{http_code}\n" "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate/cleanup?dry_run=0" -d '{"by":"Ada"}' > ${OUT}_cleanup_midjob.json
tail -1 ${OUT}_cleanup_midjob.json; head -c 400 ${OUT}_cleanup_midjob.json; echo
ls -d $D/datasets/$F.migrated >/dev/null 2>&1 && echo "$F.migrated still there" || echo "$F.migrated DELETED"
until curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate | grep -q '"status": *"finished"'; do sleep 2; done
curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate > ${OUT}_status_job3.json
python3 -c "import json;d=json.load(open('${OUT}_status_job3.json'));print('job3', {k: d['progress'][k] for k in ('migrated','skipped','failed')}, d['by_status'])"
curl -s -w "\nHTTP %{http_code}\n" "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate/cleanup?dry_run=0" -d '{"by":"Ada"}' > ${OUT}_cleanup_after_job3.json
tail -1 ${OUT}_cleanup_after_job3.json
python3 - "${OUT}_cleanup_after_job3.json" <<'PY'
import json,sys; t=open(sys.argv[1]).read().rsplit("\n",2)[0]; d=json.loads(t); d=d.get("detail",d)
fs=d.get("folders") or []; print("after job3 cleanup: deleted", sum(1 for f in fs if f.get("deleted") or f.get("delete")), "kept", [(f["folder"], (f.get("why_not") or "")[:90]) for f in fs if not (f.get("deleted") or f.get("delete"))])
PY
