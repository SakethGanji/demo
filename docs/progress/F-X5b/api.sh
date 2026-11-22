#!/usr/bin/env bash
# api.sh <audit|enforce> <port> <workdir> [pg dbname]  — fresh API
set -u; MODE=$1; PORT=$2; W=$3; DB=${4:-}
[ -f $W/api.pid ] && kill $(cat $W/api.pid) 2>/dev/null; sleep 1
[ -z "${KEEP:-}" ] && rm -rf $W/data && mkdir -p $W/data
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
[ -n "$DB" ] && EXTRA="$EXTRA ENSEMBLE_DB_URL=postgresql://postgres@127.0.0.1:55432/$DB"
if [ -n "$DB" ] && [ -z "${KEEP:-}" ]; then psql -q -h /tmp -p 55432 -U postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null; fi
SNAP=$W/snap; rm -rf $SNAP; mkdir -p $SNAP; cp -r /home/user/demo/backend/app /home/user/demo/backend/ensemble_models $SNAP/; cd $SNAP
env PYTHONUNBUFFERED=1 ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub $EXTRA \
  /home/user/demo/backend/.venv/bin/python -m uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 180); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 1; done
curl -s localhost:$PORT/api/health | head -c 120; echo
