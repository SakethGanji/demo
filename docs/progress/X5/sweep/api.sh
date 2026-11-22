#!/usr/bin/env bash
# start a fresh API: api.sh <audit|enforce> <port> <work dir>  (ENSEMBLE_DB_URL honoured)
set -u; MODE=$1; PORT=$2; W=$3
rm -rf $W/data && mkdir -p $W/data
cd /home/user/demo/backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
env PYTHONUNBUFFERED=1 ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub $EXTRA \
  .venv/bin/python -m uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 120); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 1; done
curl -s localhost:$PORT/api/health | head -c 200; echo
