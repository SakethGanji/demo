#!/usr/bin/env bash
# like rerun.sh but runs a given script. Usage: rerun_any.sh <port> <w> <snap> <out> <script.py>
PORT=$1; W=$2; SNAP=$3; OUT=$4; S=$5
[ -f $W/api.pid ] && kill $(cat $W/api.pid) 2>/dev/null; sleep 3
rm -rf $W && cp -r $SNAP $W
python3 -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(\"UPDATE approval_requests SET status='withdrawn' WHERE action='data.purge' AND status='pending'\");c.commit()" $W/data/ensemble.sqlite3
/var/tmp/x4/restart.sh $PORT $W
cd /home/user/demo/docs/progress/F-X4c
X4_DATA=$W/data X4_DB=$W/data/ensemble.sqlite3 /var/tmp/x4/venv/bin/python $S http://localhost:$PORT $W/state.json > $OUT 2>&1
