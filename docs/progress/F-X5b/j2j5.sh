#!/usr/bin/env bash
A=$1/api; H="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
for b in '{"time_limit":1e400}' '{"dataset_id": NaN}' '{"time_limit": Infinity}'; do echo "$(curl -s -o /dev/null -w '%{http_code}' $H $J -X POST $A/runs -d "$b") POST /runs $b"; done
python3 -c 'print("{\"a\":"*1000+"1"+"}"*1000)' > /tmp/deep.json
echo "$(curl -s -o /dev/null -w '%{http_code}' $H $J -X POST $A/runs --data-binary @/tmp/deep.json) POST /runs 1000-deep"
echo "$(curl -s -o /tmp/j2o -w '%{http_code}' --max-time 30 $H $J -X POST "$A/admin/lake/sync-s3?dry_run=1" -d '{"by":"Ada"}') POST /admin/lake/sync-s3?dry_run=1 (no bucket)"; head -c 300 /tmp/j2o; echo
echo "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 $A/health) GET /health after"
