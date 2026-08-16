#!/usr/bin/env bash
# Create (or reset) N isolated test-worker databases: accelerator_w1..accelerator_wN.
# Each is dropped, recreated, and migrated, so a worker always starts clean.
#
#   scripts/provision_test_workers.sh 8
#
# See scripts/test_worker.sh for how a worker is then used.
set -euo pipefail

N="${1:-8}"
PG_CONTAINER="${PG_CONTAINER:-analytics-pg}"

cd "$(dirname "$0")/.."

for i in $(seq 1 "$N"); do
    db="accelerator_w${i}"
    echo "==> ${db}"
    # FORCE terminates any connection left behind by an interrupted run.
    docker exec "$PG_CONTAINER" psql -U accelerator -d postgres -q \
        -c "DROP DATABASE IF EXISTS ${db} WITH (FORCE);" \
        -c "CREATE DATABASE ${db} OWNER accelerator;"
    ACCELERATOR_DB_NAME="${db}" venv/bin/python -m app.infra.db.postgres.migrate apply \
        | tail -1
    rm -rf "/tmp/accelerator_w${i}"
done

echo "provisioned ${N} worker(s)"
