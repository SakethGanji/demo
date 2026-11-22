#!/usr/bin/env bash
# F-X4c: X4's purge scenario (+ setup2). Usage: run.sh <port> <work dir> <out dir> [mode=enforce]
# Postgres when ENSEMBLE_DB_URL is set; S3 when ENSEMBLE_BLOB_STORE=s3://… (+ ENSEMBLE_S3_ENDPOINT, AWS_* creds).
set -u
PORT=$1; W=$2; mkdir -p $3; OUT=$(cd $3 && pwd); MODE=${4:-enforce}
HERE=$(cd "$(dirname "$0")" && pwd); PY=${X4_PY:-/var/tmp/x4/venv/bin/python}
mkdir -p $W
$HERE/api.sh $MODE $PORT $W > $OUT/api_start.txt
DBX=${ENSEMBLE_DB_URL:-$W/data/ensemble.sqlite3}
for s in setup setup2 purge; do
  [ -f $HERE/$s.py ] || continue
  echo "## $s" >&2
  X4_DATA=$W/data X4_DB=$DBX timeout 2400 $PY $HERE/$s.py http://localhost:$PORT $W/state.json > $OUT/$s.txt 2>&1
done
kill $(cat $W/api.pid); sleep 5
echo "tracebacks: $(grep -c Traceback $W/api.log) | HTTP 500: $(grep -c '" 500' $W/api.log)" > $OUT/api_log_counts.txt
grep '" 500' $W/api.log | sed 's/.*"\(GET\|POST\|PUT\|PATCH\|DELETE\) /\1 /' >> $OUT/api_log_counts.txt
