#!/usr/bin/env bash
# P24: start a fresh API (SQLite, or Postgres with ENSEMBLE_DB_URL set; the local store, or S3 with ENSEMBLE_BLOB_STORE
# and the MinIO credentials in the environment), run chain.py, stop it.
# Usage: run.sh <port> <work dir> <python> <out file>
set -u
PORT=$1; W=$2; PY=$3; OUT=$4
HERE=$(cd "$(dirname "$0")" && pwd)
rm -rf $W/data && mkdir -p $W/data
cd $HERE/../../../backend
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_GC_LIVE_GRACE_DAYS=0 \
    $(dirname $PY)/uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
API=$!
for i in $(seq 120); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
{ echo "# DB: ${ENSEMBLE_DB_URL:+postgres}${ENSEMBLE_DB_URL:-sqlite} | ENSEMBLE_BLOB_STORE=${ENSEMBLE_BLOB_STORE:-local}"; \
  $PY $HERE/chain.py http://localhost:$PORT $W/state.json; } > $OUT 2>&1
kill $API; wait $API 2>/dev/null
echo "tracebacks: $(grep -c Traceback $W/api.log) | HTTP 500: $(grep -c '" 500' $W/api.log)" >> $OUT
