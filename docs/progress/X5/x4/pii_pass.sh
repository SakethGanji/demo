#!/usr/bin/env bash
# X5 lane x4: F-PII's chain (F-PII/run.sh + chain.py, unchanged) on one engine × mode. Usage: pii_pass.sh <audit|enforce> <port> <tag>
# Postgres when ENSEMBLE_DB_URL is set (the DB is dropped and re-created first).
HERE=$(cd "$(dirname "$0")" && pwd); W=/var/tmp/x5/x4/w_$3; OUT=$HERE/out/pii; mkdir -p $OUT $W
if [ -n "${ENSEMBLE_DB_URL:-}" ]; then
  DBN=${ENSEMBLE_DB_URL##*/}; ADM=${ENSEMBLE_DB_URL%/*}/postgres
  psql "$ADM" -qc "DROP DATABASE IF EXISTS $DBN WITH (FORCE)" -c "CREATE DATABASE $DBN"
fi
bash $HERE/../../F-PII/run.sh $1 $2 $W /home/user/demo/backend/.venv/bin/python $OUT/chain_$3.txt
cp $W/api_$1.log $OUT/api_$3.log
{ echo "HTTP 5xx: $(grep -cE '" 5[0-9][0-9]' $W/api_$1.log)"; grep -E '" 5[0-9][0-9]' $W/api_$1.log; } > $OUT/api_log_counts_$3.txt
