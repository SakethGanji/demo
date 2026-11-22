#!/usr/bin/env bash
# F-X5a: start an API. usage: api.sh <port> <data dir> [extra env...]   (Postgres when ENSEMBLE_DB_URL is set)
set -u; P=$1; D=$2; shift 2; mkdir -p $D
cd /var/tmp/fx5/base/backend
env ENSEMBLE_DATA_DIR=$D ENSEMBLE_ADMINS=Ada,Adam,x5 ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=audit ENSEMBLE_AGENT_DECIDER=stub "$@" \
  nohup .venv/bin/python -m uvicorn app.main:app --port $P > $D/../api_$P.log 2>&1 &
echo $! > $D/../api_$P.pid
for i in $(seq 200); do curl -s localhost:$P/api/health >/dev/null && break; sleep 1; done
curl -s localhost:$P/api/health | head -c 150; echo; cat $D/../api_$P.pid
