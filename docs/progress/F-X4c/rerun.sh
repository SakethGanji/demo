#!/usr/bin/env bash
# restore the pre-purge snapshot of a work dir, restart, withdraw the pending purge, run purge.py. Usage: rerun.sh <port> <w> <snap> <out>
PORT=$1; W=$2; SNAP=$3; OUT=$4
[ -f $W/api.pid ] && kill $(cat $W/api.pid) 2>/dev/null; sleep 3
rm -rf $W && cp -r $SNAP $W
if [ -z "${ENSEMBLE_DB_URL:-}" ]; then python3 -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(\"UPDATE approval_requests SET status='withdrawn' WHERE action='data.purge' AND status='pending'\");c.commit()" $W/data/ensemble.sqlite3; else psql "$ENSEMBLE_DB_URL" -c "UPDATE approval_requests SET status='withdrawn' WHERE action='data.purge' AND status='pending'"; fi
/var/tmp/x4/restart.sh $PORT $W
cd /home/user/demo/docs/progress/F-X4c
X4_DATA=$W/data X4_DB=${ENSEMBLE_DB_URL:-$W/data/ensemble.sqlite3} /var/tmp/x4/venv/bin/python purge.py http://localhost:$PORT $W/state.json > $OUT 2>&1
