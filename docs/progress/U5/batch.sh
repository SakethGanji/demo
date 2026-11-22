#!/usr/bin/env bash
# batch.sh <port> <model version> <out prefix>
set -u; P=$1; V=$2; O=$3; D=$(dirname $0)/reg/bin
curl -s -H X-Ensemble-Actor:Ada localhost:$P/api/registry/versions/$V/batches -F files=@$D/applications.csv -F files=@$D/bureau.csv -F created_by=Ada > $O.post.json
B=$(python3 -c "import json;print(json.load(open('$O.post.json'))['id'])")
for i in $(seq 120); do curl -s -H X-Ensemble-Actor:Ada localhost:$P/api/registry/batches/$B > $O.json; s=$(python3 -c "import json;print(json.load(open('$O.json')).get('status'))"); case $s in queued|running|scoring|pending) sleep 2;; *) break;; esac; done
echo "batch $B $s"
