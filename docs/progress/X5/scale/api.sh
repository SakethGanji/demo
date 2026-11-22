#!/usr/bin/env bash
# X5: start an API. usage: api.sh <port> <data dir> [audit|enforce]   (Postgres when ENSEMBLE_DB_URL is set)
set -u; P=$1; D=$2; MODE=${3:-audit}; mkdir -p $D
cd /home/user/demo/backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
env ENSEMBLE_DATA_DIR=$D ENSEMBLE_ADMINS=Ada,Adam,x5 ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub $EXTRA \
  nohup .venv/bin/python -m uvicorn app.main:app --port $P > $D/../api_$P.log 2>&1 &
echo $! > $D/../api_$P.pid
for i in $(seq 150); do curl -s localhost:$P/api/health >/dev/null && break; sleep 1; done
curl -s localhost:$P/api/health | head -c 150; echo; cat $D/../api_$P.pid
