#!/usr/bin/env bash
# P24 extras on a finished chain's data dir (local store, SQLite): a version deleted the pre-P24 way (refs kept) is
# collected by GC in two passes; verify-all re-hashes legacy_json blobs and fails on a tampered one.
# Usage: extra.sh <port> <work dir> <python>
set -u
PORT=$1; W=$2; PY=$3; HERE=$(cd "$(dirname "$0")" && pwd); A=localhost:$PORT/api; H="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
$PY $HERE/predelete.py $W/data
$PY $HERE/legacy_json.py $W/data put
cd $HERE/../../../backend
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_GC_LIVE_GRACE_DAYS=0 $(dirname $PY)/uvicorn app.main:app --port $PORT > $W/api_extra.log 2>&1 &
API=$!
for i in $(seq 120); do curl -s $A/health >/dev/null && break; sleep 2; done
j() { $PY -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
echo "## E1 POST /admin/lake/gc?dry_run=1"
P1=$(curl -s $H -X POST "$A/admin/lake/gc?dry_run=1" | tee $W/e1.json | j "d['plan_id']")
j "'deleted_versions_with_refs', d['deleted_versions_with_refs'], '| candidates', len(d['candidates'])" < $W/e1.json
echo "## E2 POST /admin/lake/gc?dry_run=0 {plan_id}"
curl -s $H $J -X POST "$A/admin/lake/gc?dry_run=0" -d "{\"plan_id\":\"$P1\",\"confirm\":\"gc\",\"by\":\"Ada\"}" | j "{k: d[k] for k in ('deleted', 'deleted_versions_refs_dropped', 'skipped', 'failed')}"
echo "## E3 POST /admin/lake/gc?dry_run=1"
P2=$(curl -s $H -X POST "$A/admin/lake/gc?dry_run=1" | tee $W/e3.json | j "d['plan_id']")
j "'deleted_versions_with_refs', d['deleted_versions_with_refs'], '| candidates', sorted((c['kind'], c['sha256'][:12]) for c in d['candidates'])" < $W/e3.json
echo "## E4 POST /admin/lake/gc?dry_run=0 {plan_id}"
curl -s $H $J -X POST "$A/admin/lake/gc?dry_run=0" -d "{\"plan_id\":\"$P2\",\"confirm\":\"gc\",\"by\":\"Ada\"}" | j "{k: d[k] for k in ('deleted', 'bytes', 'skipped', 'failed')}"
echo "## E5 GET /admin/lake/verify-all (a live legacy_json blob)"
curl -s $H "$A/admin/lake/verify-all?by=Ada" | j "'ok', d['ok'], '| legacy_json', d['legacy_json']"
kill $API; wait $API 2>/dev/null
$PY $HERE/legacy_json.py $W/data tamper
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam $(dirname $PY)/uvicorn app.main:app --port $PORT >> $W/api_extra.log 2>&1 &
API=$!
for i in $(seq 120); do curl -s $A/health >/dev/null && break; sleep 2; done
echo "## E6 GET /admin/lake/verify-all (after the bytes were rewritten)"
curl -s $H "$A/admin/lake/verify-all?by=Ada" | j "'ok', d['ok'], '| legacy_json', {'checked': d['legacy_json']['checked'], 'failed': [(x['sha256'][:12], x['now'][:12]) for x in d['legacy_json']['failed']]}"
kill $API; wait $API 2>/dev/null
echo "tracebacks: $(grep -c Traceback $W/api_extra.log) | HTTP 500: $(grep -c '" 500' $W/api_extra.log)"
