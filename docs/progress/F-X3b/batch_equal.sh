#!/usr/bin/env bash
# Batches before/after: the same model and files scored by the head code and by the fixed code, on one data dir.
# Usage: batch_equal.sh <port> <model version> <files dir> <out file>
P=$1; M=$2; F=$3; O=$4; A=localhost:$P/api; H="-H X-Ensemble-Actor:Ana"
for set in clean coerce reorder; do
  B=$(curl -s $H -X POST $A/registry/versions/$M/batches -F files=@$F/batch/$set/apps.csv -F files=@$F/batch/$set/cust.csv -F files=@$F/batch/$set/pays.csv -F created_by=Ana | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
  until curl -s $H $A/registry/batches/$B | python3 -c "import json,sys;d=json.load(sys.stdin);sys.exit(1 if d['status'] in ('queued','running','pending') else 0)"; do sleep 2; done
  curl -s $H $A/registry/batches/$B/download -o $O.$set.csv; echo "$set $B $(sha256sum < $O.$set.csv | cut -c1-16) rows $(($(wc -l < $O.$set.csv)-1))"
done
