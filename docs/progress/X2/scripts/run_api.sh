#!/bin/bash
# usage: run_api.sh <code_dir> <data_dir> [port]  — an API for that code on that data dir
CODE=$1; DATA=$2; PORT=${3:-8710}
pkill -f "uvicorn app.main:app --port $PORT" ; sleep 2
cd $CODE/backend && ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada nohup $PYBIN/uvicorn app.main:app --port $PORT > $DATA.api.log 2>&1 < /dev/null &
for i in $(seq 240); do curl -s -o /dev/null localhost:$PORT/api/health && break; sleep 1; done
curl -s -o /dev/null -w "%{http_code}" localhost:$PORT/api/health | grep -q 200 && echo "api up ($CODE :$PORT)" || { echo "api down"; tail -30 $DATA.api.log; }
