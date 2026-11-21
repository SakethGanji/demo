#!/usr/bin/env bash
# Start the API for the P10b chain: run_api.sh <data dir> <local|s3> [port]. The S3 settings and the MinIO credentials
# come from the environment (s3env.sh in the session scratchpad, not committed): AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY,
# ENSEMBLE_S3_ENDPOINT, ENSEMBLE_BLOB_STORE=s3://lake/.
set -eu
DATA=$1; KIND=$2; PORT=${3:-8000}
cd "$(dirname "$0")/../../../backend"
if [ "$KIND" = local ]; then export ENSEMBLE_BLOB_STORE=local; fi
ENSEMBLE_DATA_DIR=$DATA ENSEMBLE_ADMINS=Ada exec "$PY" -m uvicorn app.main:app --port "$PORT"
