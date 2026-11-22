#!/usr/bin/env bash
# X5 lane x4: one engine × mode pass. Usage: run.sh <audit|enforce> <port> <work dir> <out dir> "<steps>"
# A step is <dir>:<script> with dir ∈ X4 F-X4a F-X4b F-X4c X5 (X5 = this dir; it imports F-X4c's x4lib). `tamper` steps
# (F-X4b:tamper) run after the API is stopped. Postgres when ENSEMBLE_DB_URL is set: the DB is dropped and re-created first.
set -u
MODE=$1; PORT=$2; W=$3; mkdir -p $4; OUT=$(cd $4 && pwd); SUB=$5
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd $HERE/../.. && pwd); PY=${X4_PY:-/home/user/demo/backend/.venv/bin/python}
mkdir -p $W
if [ -n "${ENSEMBLE_DB_URL:-}" ]; then
  DBN=${ENSEMBLE_DB_URL##*/}; ADM=${ENSEMBLE_DB_URL%/*}/postgres
  psql "$ADM" -qc "DROP DATABASE IF EXISTS $DBN WITH (FORCE)" -c "CREATE DATABASE $DBN" > $OUT/db_reset.txt 2>&1
fi
date -u +"start %FT%TZ" > $OUT/timing.txt
$HERE/api.sh $MODE $PORT $W > $OUT/api_start.txt
DBX=${ENSEMBLE_DB_URL:-$W/data/ensemble.sqlite3}
for s in $SUB; do
  d=${s%%:*}; n=${s#*:}
  [ $n = tamper ] && continue
  if [ $d = X5 ]; then f=$HERE/$n.py; PP=$P/F-X4c; else f=$P/$d/$n.py; PP=; fi
  echo "## $d/$n $(date -u +%T)" >&2; echo "$d/$n $(date -u +%T)" >> $OUT/timing.txt
  PYTHONPATH=$PP X4_DATA=$W/data X4_DB=$DBX X4_PLATFORM_URL=${ENSEMBLE_DB_URL:-} X4_WORK=$W X5_LOOPBACK=${X5_LOOPBACK:-0} \
    timeout 2400 $PY $f http://localhost:$PORT $W/state.json > $OUT/${d}_$n.txt 2>&1
done
curl -s -H 'X-Ensemble-Actor: Ada' "localhost:$PORT/api/authz/coverage" | $PY -c "import json,sys; c=json.load(sys.stdin); print('coverage: mode', c['mode'], '| routes', c['n_routes'], '| must', c['n_must'], '| covered', c['n_covered'], '| uncovered', c['n_uncovered'], [u['method']+' '+u['path'] for u in c['uncovered']][:10])" > $OUT/coverage.txt 2>&1
kill $(cat $W/api.pid); sleep 6; kill -9 $(cat $W/api.pid) 2>/dev/null
if [[ " $SUB " == *":tamper"* ]]; then
  CR=$(python3 -c "import json;print(json.load(open('$W/state.json'))['cr'])")
  (cd $P/../../backend && X4_DB=$DBX X4_DATA=$W/data $PY $P/F-X4b/tamper.py $CR > $OUT/F-X4b_tamper.txt 2>&1)
fi
date -u +"end %FT%TZ" >> $OUT/timing.txt
cp $W/api.log $OUT/api.log
echo "tracebacks: $(grep -c Traceback $W/api.log) | HTTP 5xx: $(grep -cE '" 5[0-9][0-9]' $W/api.log)" > $OUT/api_log_counts.txt
grep -E '" 5[0-9][0-9]' $W/api.log | sed 's/.*"\(GET\|POST\|PUT\|PATCH\|DELETE\) /\1 /' >> $OUT/api_log_counts.txt
