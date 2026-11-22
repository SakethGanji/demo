#!/usr/bin/env bash
# sweep.sh <mode> <port> <name> [pgdb]  — fresh API, X5 fixture, X5 sweep; output in /var/tmp/fx5b/sw/<name>.*
set -u; MODE=$1; PORT=$2; N=$3; DB=${4:-}
S=/home/user/demo/docs/progress/X5/sweep; PY=/home/user/demo/backend/.venv/bin/python; O=/var/tmp/fx5b/sw; W=/var/tmp/fx5b/w_$N
mkdir -p $O $W
/var/tmp/fx5b/api.sh $MODE $PORT $W $DB > $O/$N.api_start.txt 2>&1
B=http://localhost:$PORT
cd $S && $PY fixture.py $B $W/state.json > $O/$N.fixture.txt 2>&1
curl -s $B/openapi.json > $W/openapi.json
cd /home/user/demo/backend && [ -f $O/keys.json ] || ENSEMBLE_DATA_DIR=/var/tmp/fx5b/kd PYTHONPATH=. $PY $S/keys.py $O/keys.json > /dev/null 2>&1
cd $S && $PY sweep.py $B $W/state.json $W/openapi.json $O/keys.json $O/$N $W/data > $O/$N.progress.txt 2>&1
kill $(cat $W/api.pid)
echo done >> $O/$N.progress.txt
