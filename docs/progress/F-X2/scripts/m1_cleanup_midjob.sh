#!/bin/bash
# F-X2: X2's M1 repro. usage: m1_cleanup_midjob.sh <code dir> <out prefix>   (data dir = $S/build/data, port 8712)
# the outside changes are undone first (X2 untamper a/b/d, F-X2 untamper c/e/f), verify-all ×2, then a job slowed to 5 s
# per version, and POST cleanup?dry_run=0 as soon as the first folder of this job exists
CODE=$1; OUT=$2; D=$S/build/data; A=(-H X-Ensemble-Actor:Ada -H Content-Type:application/json)
pkill -f "fx2_hook.py"; sleep 2
$PYBIN/python $S/x2/untamper.py $S/build/state.json $S/build/snap_head $D; [ -z "$ONLY_ABD" ] && $PYBIN/python $FX2/fx2_untamper.py $S/build/state.json $S/build/snap_head $D
bash $FX2/hook.sh $CODE $D 8712 X2_SLOW=5
for i in 1 2; do curl -s "${A[@]}" "localhost:8712/api/admin/lake/verify-all?by=Ada" | python3 -c "import json,sys;d=json.load(sys.stdin);print('verify-all', d.get('ok'), d.get('checked'), [f['id'] for f in d.get('failed') or []])"; done
curl -s "${A[@]}" -X POST "localhost:8712/api/admin/lake/migrate/cleanup?dry_run=1" -d '{"by":"Ada"}' > ${OUT}_cleanup_dry_before_job.json
NEW=$(sqlite3 $D/ensemble.sqlite3 "select id from datasets where coalesce(storage,'csv')='csv' order by created_at" 2>/dev/null || python3 -c "
import sqlite3;print('\n'.join(r[0] for r in sqlite3.connect('$D/ensemble.sqlite3').execute(\"select id from datasets where coalesce(storage,'csv')='csv'\")))")
echo "CSV-era left: $NEW"
curl -s "${A[@]}" -X POST "localhost:8712/api/admin/lake/migrate?dry_run=0" -d '{"by":"Ada"}' | cut -c1-80; echo
for i in $(seq 600); do F=$(for v in $NEW; do [ -d $D/datasets/$v.migrated ] && echo $v; done | head -1); [ -n "$F" ] && break; sleep 0.2; done
echo "first folder of this job: $F.migrated"
curl -s -w "\nHTTP %{http_code}\n" "${A[@]}" -X POST "localhost:8712/api/admin/lake/migrate/cleanup?dry_run=0" -d '{"by":"Ada"}' > ${OUT}_cleanup_midjob.json
tail -1 ${OUT}_cleanup_midjob.json; head -c 600 ${OUT}_cleanup_midjob.json; echo
ls -d $D/datasets/$F.migrated 2>/dev/null && echo "$F.migrated still there" || echo "$F.migrated DELETED"
until curl -s "${A[@]}" localhost:8712/api/admin/lake/migrate | grep -q '"status": *"finished"'; do sleep 2; done
curl -s "${A[@]}" localhost:8712/api/admin/lake/migrate | python3 -c "import json,sys;d=json.load(sys.stdin);print('job', {k: d['progress'][k] for k in ('migrated','skipped','failed')}, d['by_status'])"
