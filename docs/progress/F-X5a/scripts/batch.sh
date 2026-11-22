#!/usr/bin/env bash
# usage: batch.sh <model version> <csv>  → polls the batch, prints status, seconds
V=$1; F=$2; t0=$(date +%s)
B=$(curl -s -H 'X-Ensemble-Actor: x5' -F "file=@$F" -F created_by=x5 localhost:8621/api/registry/versions/$V/batches | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
while true; do
  s=$(curl -s -H 'X-Ensemble-Actor: x5' localhost:8621/api/registry/batches/$B | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'], d.get('n_rows'), (d.get('error') or '')[:300])")
  case "$s" in succeeded*|failed*) echo "batch $B: $s ($(( $(date +%s)-t0 )) s)"; break;; esac; sleep 2
done
