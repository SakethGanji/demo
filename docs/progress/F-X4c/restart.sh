#!/usr/bin/env bash
# restart the API on an existing work dir (no wipe). Usage: restart.sh <port> <work dir> [mode]
PORT=$1; W=$2; MODE=${3:-enforce}
[ -f $W/api.pid ] && kill $(cat $W/api.pid) 2>/dev/null; sleep 3
cd /home/user/demo/backend
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub \
    ENSEMBLE_GC_LIVE_GRACE_DAYS=0 ENSEMBLE_AUTHZ_ALLOW_TYPED=1 /var/tmp/x4/venv/bin/python -m uvicorn app.main:app --port $PORT >> $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 120); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
