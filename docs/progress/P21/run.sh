#!/usr/bin/env bash
# P21: start a fresh API (SQLite, or Postgres with ENSEMBLE_DB_URL set), run chain.py then foureyes.py, stop it.
# Usage: run.sh <mode audit|enforce> <port> <work dir> <python> <out file>
set -u
MODE=$1; PORT=$2; W=$3; PY=$4; OUT=$5
HERE=$(cd "$(dirname "$0")" && pwd)
rm -rf $W/data && mkdir -p $W/data
cd $HERE/../../../backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE $EXTRA \
    $(dirname $PY)/uvicorn app.main:app --port $PORT > $W/api_$MODE.log 2>&1 &
API=$!
for i in $(seq 90); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
{ echo "# DB: ${ENSEMBLE_DB_URL:+postgres}${ENSEMBLE_DB_URL:-sqlite}"; $PY $HERE/chain.py http://localhost:$PORT $W/state.json; \
  $PY $HERE/foureyes.py http://localhost:$PORT $W/state.json; } > $OUT 2>&1
kill $API; wait $API 2>/dev/null
echo "tracebacks: $(grep -c Traceback $W/api_$MODE.log) | HTTP 500: $(grep -c '" 500' $W/api_$MODE.log)" >> $OUT
