#!/usr/bin/env bash
# X5 lane x4: start a fresh API (X4's api.sh + F-X4b's SQL-source env: import dir, PGPASSWORD/.pgpass, checkpoints).
# Usage: api.sh <audit|enforce> <port> <work dir>. Postgres when ENSEMBLE_DB_URL is set (the caller drops/creates the DB);
# S3 when ENSEMBLE_BLOB_STORE=s3://… (+ENSEMBLE_S3_*, AWS_*). X5_LOOPBACK=1 → ENSEMBLE_SQL_ALLOW_LOOPBACK=1.
set -u
MODE=$1; PORT=$2; W=$3
PY=${X4_PY:-/home/user/demo/backend/.venv/bin/python}
rm -rf $W/data $W/import $W/home && mkdir -p $W/data $W/import $W/home
printf '*:*:*:*:extpw\n' > $W/home/.pgpass && chmod 600 $W/home/.pgpass
cd /home/user/demo/backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
[ "${X5_LOOPBACK:-0}" = 1 ] && EXTRA="$EXTRA ENSEMBLE_SQL_ALLOW_LOOPBACK=1"
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub \
    ENSEMBLE_GC_LIVE_GRACE_DAYS=0 ENSEMBLE_SQL_IMPORT_DIRS=$W/import ENSEMBLE_AUDIT_CHECKPOINT_SECONDS=20 \
    HOME=$W/home PGPASSWORD=extpw $EXTRA $PY -m uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 150); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
curl -s localhost:$PORT/api/health | head -c 200; echo
