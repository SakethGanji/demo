#!/usr/bin/env bash
# X5/x3: start a fresh API in the background. Usage: api.sh <audit|enforce> <port> <data dir> [pg db name] [extra env k=v ...]
# Log: <data dir>/api.log, pid: <data dir>.pid. With a db name the API uses Postgres 16 at 127.0.0.1:5433 (db re-created).
set -u
MODE=$1; PORT=$2; D=$3; DB=${4:-}; shift 4 2>/dev/null || shift $#
PY=/home/user/demo/backend/.venv/bin/python
rm -rf $D && mkdir -p $D
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
if [ -n "$DB" ] && [ "$DB" != - ]; then
  psql -q -h 127.0.0.1 -p 5433 -U postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB" >/dev/null
  EXTRA="$EXTRA ENSEMBLE_DB_URL=postgresql://postgres@127.0.0.1:5433/$DB"
fi
cd /home/user/demo/backend
env ENSEMBLE_DATA_DIR=$D ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_GC_LIVE_GRACE_DAYS=0 \
    $EXTRA "$@" $PY -m uvicorn app.main:app --port $PORT > $D/api.log 2>&1 &
echo $! > $D.pid
for i in $(seq 150); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
curl -s localhost:$PORT/api/health | head -c 120; echo
