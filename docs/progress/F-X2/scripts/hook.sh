#!/bin/bash
# usage: hook.sh <code dir> <data_dir> <port> [VAR=value ...]  — that code's API with X2's hooks (+ FX2_NO_LOCK)
CODE=$1; DATA=$2; PORT=$3; shift 3
pkill -f "fx2_hook.py X2PORT=$PORT"; pkill -f "uvicorn app.main:app --port $PORT"; sleep 2
cd $CODE/backend && env "$@" X2_SCRIPTS=$S/x2 ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada PORT=$PORT nohup $PYBIN/python $FX2/fx2_hook.py X2PORT=$PORT >> $DATA.hook.log 2>&1 < /dev/null &
for i in $(seq 120); do curl -s -o /dev/null localhost:$PORT/api/health && break; sleep 1; done
curl -s -o /dev/null -w "%{http_code}" localhost:$PORT/api/health | grep -q 200 && echo "hook api up $CODE :$PORT ($*)" || { echo "hook api down"; tail -5 $DATA.hook.log; }
