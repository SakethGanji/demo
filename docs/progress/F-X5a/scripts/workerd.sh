#!/usr/bin/env bash
# F-X5a: an external workerd beside an API. usage: workerd.sh <data dir> <slots> [extra env...]
set -u; D=$1; SL=$2; shift 2
cd /home/user/demo/backend
env ENSEMBLE_DATA_DIR=$D ENSEMBLE_ADMINS=Ada,Adam,x5 ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=audit "$@" \
  nohup .venv/bin/python -m app.engine.workerd --slots $SL > $D/../workerd.log 2>&1 &
echo $! > $D/../workerd.pid; cat $D/../workerd.pid
