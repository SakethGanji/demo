#!/bin/bash
# usage: run_api.sh <code_dir> <data_dir>   (an API on :8000 for that code on that data dir; $PYBIN = the venv's bin)
CODE=$1; DATA=$2
pkill -f "uvicorn app.main:app" ; sleep 2
cd $CODE/backend && ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada nohup $PYBIN/uvicorn app.main:app --port 8000 > $DATA.api.log 2>&1 < /dev/null &
for i in $(seq 240); do curl -s -o /dev/null localhost:8000/api/health && break; sleep 1; done
pgrep -f "uvicorn app.main:app" >/dev/null && echo "api up ($CODE)" || { echo "api down"; tail -30 $DATA.api.log; }
