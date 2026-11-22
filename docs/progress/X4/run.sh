#!/usr/bin/env bash
# X4: one engine × mode pass. Usage: run.sh <audit|enforce> <port> <work dir> <out dir> [subset]
# Postgres when ENSEMBLE_DB_URL is set (scratch initdb cluster). The full list: setup sweep agent decide governance teams
# purge audit; tamper runs with the API stopped.
set -u
MODE=$1; PORT=$2; W=$3; mkdir -p $4; OUT=$(cd $4 && pwd); SUB=${5:-"setup sweep agent decide governance teams purge audit"}
HERE=$(cd "$(dirname "$0")" && pwd); PY=${X4_PY:-/var/tmp/x4/venv/bin/python}
mkdir -p $OUT $W
$HERE/api.sh $MODE $PORT $W > $OUT/api_start.txt
DBX=${ENSEMBLE_DB_URL:-$W/data/ensemble.sqlite3}
for s in $SUB; do
  [ $s = tamper ] && continue
  echo "## $s" >&2
  X4_DATA=$W/data X4_DB=$DBX X4_PLATFORM_URL=${ENSEMBLE_DB_URL:-} timeout 2400 $PY $HERE/$s.py http://localhost:$PORT $W/state.json > $OUT/$s.txt 2>&1
done
kill $(cat $W/api.pid); sleep 5
if [[ " $SUB " == *" tamper "* ]]; then
  CR=$(python3 -c "import json;print(json.load(open('$W/state.json'))['cr'])")
  (cd $HERE/../../../backend && X4_DB=$DBX $PY $HERE/tamper.py $CR > $OUT/tamper.txt 2>&1)
fi
echo "tracebacks: $(grep -c Traceback $W/api.log) | HTTP 500: $(grep -c '" 500' $W/api.log)" > $OUT/api_log_counts.txt
grep '" 500' $W/api.log | sed 's/.*"\(GET\|POST\|PUT\|PATCH\|DELETE\) /\1 /' >> $OUT/api_log_counts.txt
