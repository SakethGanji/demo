#!/usr/bin/env bash
# api.sh <port> <workdir> <code dir (has app/ ensemble_models/)> [env...]: API from a snapshot of the code
set -u; P=$1; W=$2; CODE=$3; shift 3; mkdir -p $W/data
[ -f $W/api_$P.pid ] && kill $(cat $W/api_$P.pid) 2>/dev/null && sleep 2
SNAP=$W/snap_$P; rm -rf $SNAP; mkdir -p $SNAP; cp -r $CODE/app $CODE/ensemble_models $SNAP/; cd $SNAP
env PYTHONUNBUFFERED=1 ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=${MODE:-audit} ENSEMBLE_AGENT_DECIDER=stub ENSEMBLE_AUTHZ_ALLOW_TYPED=1 ENSEMBLE_GC_LIVE_GRACE_DAYS=0 "$@" \
  nohup /home/user/demo/backend/.venv/bin/python -m uvicorn app.main:app --port $P > $W/api_$P.log 2>&1 &
echo $! > $W/api_$P.pid
for i in $(seq 240); do curl -s localhost:$P/api/health >/dev/null && break; sleep 1; done
curl -s localhost:$P/api/health | head -c 100; echo
