#!/bin/bash
# usage: run_api.sh [code_dir] [data_dir]   (defaults: the repo's backend, $S/data)
S=$SCRATCH
CODE=${1:-/home/user/demo/backend}; DATA=${2:-$S/data}
pkill -f "uvicorn app.main:app" ; sleep 1
cd $CODE && ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=fx1 nohup uvicorn app.main:app --port 8000 > $S/api.log 2>&1 &
for i in $(seq 90); do curl -s -o /dev/null localhost:8000/api/health && break; sleep 1; done
pgrep -f "uvicorn app.main:app"
