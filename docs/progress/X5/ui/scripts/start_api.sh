#!/usr/bin/env bash
# X5 UI lane: start a fresh API on :8660 from the worktree. usage: start_api.sh <name> <authz mode> <admins> [extra env...]
set -e
N=$1; MODE=$2; ADM=$3; shift 3
D=/var/tmp/x5/ui/$N; rm -rf $D; mkdir -p $D/data
cd /var/tmp/x5/head/backend
env ENSEMBLE_DATA_DIR=$D/data ENSEMBLE_ADMINS=$ADM ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AUTHZ_ALLOW_TYPED=1 "$@" \
  nohup .venv/bin/python -m uvicorn app.main:app --port 8660 > $D/api.log 2>&1 &
echo $! > $D/api.pid
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8660/api/health >/dev/null && { echo "up $N pid $(cat $D/api.pid)"; exit 0; }; sleep 1; done
echo "API did not start"; tail -30 $D/api.log; exit 1
