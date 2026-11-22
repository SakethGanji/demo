#!/usr/bin/env bash
# sweep.sh <mode> <port> <name> [pgdb] — fresh API, X5 fixture, X5 sweep; output in /var/tmp/x5b/sw/<name>.*
set -u; MODE=$1; PORT=$2; N=$3; DB=${4:-}
S=/var/tmp/x5b/sw/src; PY=/home/user/x5b_wt/backend/.venv/bin/python; O=/var/tmp/x5b/sw; W=/var/tmp/x5b/w_$N
mkdir -p $O $W
/var/tmp/x5b/bin/api.sh $MODE $PORT $W "$DB" > $O/$N.api_start.txt 2>&1
B=http://localhost:$PORT
cd $S && $PY fixture.py $B $W/state.json > $O/$N.fixture.txt 2>&1
curl -s $B/openapi.json > $W/openapi.json
cd /home/user/x5b_wt/backend && { [ -f $O/keys.json ] || ENSEMBLE_DATA_DIR=/var/tmp/x5b/kd_$N PYTHONPATH=. $PY $S/keys.py $O/keys.json > /dev/null 2>&1; }
cd $S && $PY sweep.py $B $W/state.json $W/openapi.json $O/keys.json $O/$N $W/data > $O/$N.progress.txt 2>&1
kill $(cat $W/api.pid)
echo done >> $O/$N.progress.txt
