#!/bin/bash
# X5/x2: start an API for <code dir> on <data dir> at <port>, optionally through a hook script (X2 hook_api / F-X2 fx2_hook)
# usage: api.sh <code dir> <data dir> <port> [hook] [VAR=value ...]   hook = none|hook
CODE=$1; DATA=$2; PORT=$3; HOOK=${4:-none}; shift 4 2>/dev/null || shift $#
PY=/home/user/demo/backend/.venv/bin/python
pkill -f "port $PORT\b" ; pkill -f "X2PORT=$PORT\b"; sleep 2
cd $CODE/backend
if [ "$HOOK" = hook ]; then
  env "$@" X2_SCRIPTS=/home/user/demo/docs/progress/X2/scripts ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 PORT=$PORT \
    nohup $PY /home/user/demo/docs/progress/F-X2/scripts/fx2_hook.py X2PORT=$PORT >> $DATA.api.log 2>&1 < /dev/null &
else
  env "$@" ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 nohup $PY -m uvicorn app.main:app --port $PORT >> $DATA.api.log 2>&1 < /dev/null &
fi
for i in $(seq 240); do curl -s -o /dev/null localhost:$PORT/api/health && break; sleep 1; done
curl -s -o /dev/null -w "%{http_code}" localhost:$PORT/api/health | grep -q 200 && echo "api up ($CODE :$PORT $HOOK $*)" || { echo "api down"; tail -30 $DATA.api.log; }
