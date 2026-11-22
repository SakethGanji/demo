#!/usr/bin/env bash
# F-X4b: start a fresh API in the background (X4's api.sh, plus the external-DB settings for the SQL source).
# Usage: api.sh <audit|enforce> <port> <work dir>. Postgres when ENSEMBLE_DB_URL is set.
set -u
MODE=$1; PORT=$2; W=$3
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${X4_PY:-/var/tmp/fx4b/venv/bin/python}
rm -rf $W/data && mkdir -p $W/data $W/import $W/home
# the API's own environment holds credentials for the external DB (PGPASSWORD, ~/.pgpass): the SQL source must not use them
printf '192.0.2.2:55433:bankext:extuser:extpw\n*:*:*:*:extpw\n' > $W/home/.pgpass && chmod 600 $W/home/.pgpass
cd $HERE/../../../backend
EXTRA=""; [ "$MODE" = enforce ] && EXTRA="ENSEMBLE_AUTHZ_ALLOW_TYPED=1"
env ENSEMBLE_DATA_DIR=$W/data ENSEMBLE_ADMINS=Ada,Adam ENSEMBLE_DEMO_MODE=1 ENSEMBLE_AUTHZ=$MODE ENSEMBLE_AGENT_DECIDER=stub \
    ENSEMBLE_GC_LIVE_GRACE_DAYS=0 ENSEMBLE_SQL_IMPORT_DIRS=$W/import ENSEMBLE_AUDIT_CHECKPOINT_SECONDS=20 \
    HOME=$W/home PGPASSWORD=extpw $EXTRA $PY -m uvicorn app.main:app --port $PORT > $W/api.log 2>&1 &
echo $! > $W/api.pid
for i in $(seq 120); do curl -s localhost:$PORT/api/health >/dev/null && break; sleep 2; done
curl -s localhost:$PORT/api/health | head -c 200; echo
