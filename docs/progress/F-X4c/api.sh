#!/usr/bin/env bash
# X4: start a fresh API in the background. Usage: api.sh <mode audit|enforce> <port> <work dir>
# (Postgres when ENSEMBLE_DB_URL is set.) Writes <work dir>/api.pid and <work dir>/api.log.
set -u
MODE=$1; PORT=$2; W=$3
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${X4_PY:-/var/tmp/x4/venv/bin/python}
rm -rf $W/data && mkdir -p $W/data
cd $HERE/../../../backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub \
    ENSEMBLE_GC_LIVE_GRACE_DAYS=0 $EXTRA $PY -m uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 120); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
curl -s localhost:$PORT/api/health | head -c 200; echo
