#!/usr/bin/env bash
# X5/x3: admin lake routes with a non-text body `by` (lake/api.py _admin strips it). Usage: admin_by.sh <port>
A=localhost:$1/api
for by in 123 '["x"]' '{"a":1}' true; do
  for r in "admin/lake/gc?dry_run=1" "admin/lake/sync-s3?dry_run=1" "admin/lake/migrate?dry_run=1" "admin/lake/migrate/cleanup?dry_run=1" \
           "admin/lake/blobs/0000000000000000000000000000000000000000000000000000000000000000/restore" "admin/lake/seed"; do
    printf 'POST /api/%s {"by": %s} [actor Ada] → ' "$r" "$by"
    curl -s -H X-Ensemble-Actor:Ada -H content-type:application/json -X POST "$A/$r" -d "{\"by\": $by, \"rows\": 10, \"confirm\": \"restore\"}" -w ' %{http_code}\n' | tail -c 140
  done
done
