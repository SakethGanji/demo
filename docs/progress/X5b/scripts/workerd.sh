#!/usr/bin/env bash
# usage: workerd.sh <workdir> <slots> [extra env...]
set -u; W=$1; SL=$2; shift 2
cd ${BK:-/home/user/x5b_wt/backend}
env PYTHONUNBUFFERED=1 ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam,x5 ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=audit "$@" \
  nohup .venv/bin/python -m app.engine.workerd --slots $SL >> $W/workerd.log 2>&1 &
echo $! > $W/workerd.pid; cat $W/workerd.pid
