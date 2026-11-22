#!/bin/bash
# usage: run_hook.sh <data_dir> <port> [VAR=value ...]  — the head API with hooks (wt_head), env vars passed through
DATA=$1; PORT=$2; shift 2
pkill -f "uvicorn app.main:app --port $PORT"; pkill -f "hook_api.py.*$PORT" ; pkill -f "X2PORT=$PORT"; sleep 2
cd $S/wt_head/backend && env "$@" ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada PORT=$PORT nohup $PYBIN/python $S/x2/hook_api.py X2PORT=$PORT >> $DATA.hook.log 2>&1 < /dev/null &
for i in $(seq 120); do curl -s -o /dev/null localhost:$PORT/api/health && break; sleep 1; done
curl -s -o /dev/null -w "%{http_code}" localhost:$PORT/api/health | grep -q 200 && echo "hook api up :$PORT ($*)" || { echo "hook api down"; tail -5 $DATA.hook.log; }
