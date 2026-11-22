#!/usr/bin/env bash
# F-X4a: one engine × mode pass of X4's scripts plus fx4a.py, against the backend of <repo>.
# Usage: run.sh <audit|enforce> <port> <work dir> <out dir> [scripts] [repo]
# Postgres when ENSEMBLE_DB_URL is set. Default scripts: setup sweep decide fx4a teams. The API is X4's api.sh, run from
# <repo>/docs/progress/X4 (so a base worktree runs its own backend).
set -u
MODE=$1; PORT=$2; W=$3; mkdir -p $4; OUT=$(cd $4 && pwd); SUB=${5:-"setup sweep decide fx4a teams"}
HERE=$(cd "$(dirname "$0")" && pwd); REPO=${6:-$(cd $HERE/../../.. && pwd)}
X4=$REPO/docs/progress/X4; PY=${X4_PY:-/var/tmp/x4/venv/bin/python}
mkdir -p $W
$X4/api.sh $MODE $PORT $W > $OUT/api_start.txt
for s in $SUB; do
  echo "## $s" >&2
  if [ $s = fx4a ]; then f=$HERE/fx4a.py; else f=$HERE/../X4/$s.py; fi
  X4_DATA=$W/data X4_PLATFORM_URL=${ENSEMBLE_DB_URL:-} timeout 2400 $PY $f http://localhost:$PORT $W/state.json > $OUT/$s.txt 2>&1
done
curl -s -H 'X-Ensemble-Actor: Ada' "localhost:$PORT/api/authz/coverage" | $PY -c "import json,sys; c=json.load(sys.stdin); print('coverage: mode', c['mode'], '| routes', c['n_routes'], '| must', c['n_must'], '| covered', c['n_covered'], '| uncovered', c['n_uncovered'], [u['method']+' '+u['path'] for u in c['uncovered']][:10])" > $OUT/coverage.txt
kill $(cat $W/api.pid); sleep 5
echo "tracebacks: $(grep -c Traceback $W/api.log) | HTTP 500: $(grep -c '" 500' $W/api.log)" > $OUT/api_log_counts.txt
grep '" 500' $W/api.log | sed 's/.*"\(GET\|POST\|PUT\|PATCH\|DELETE\) /\1 /' >> $OUT/api_log_counts.txt
