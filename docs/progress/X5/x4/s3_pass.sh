#!/usr/bin/env bash
# X5 lane x4: the purge on S3 (MinIO built from source, 127.0.0.1:9020, versioned bucket with Object Lock, default 30-day
# GOVERNANCE retention per blob). Usage: s3_pass.sh <audit|enforce> <port> <tag> <bucket>. Postgres when ENSEMBLE_DB_URL is set.
HERE=$(cd "$(dirname "$0")" && pwd)
export ENSEMBLE_BLOB_STORE=s3://$4/ ENSEMBLE_S3_ENDPOINT=http://127.0.0.1:9020 ENSEMBLE_S3_CREATE_BUCKET=1 ENSEMBLE_S3_URL_STYLE=path \
       AWS_ACCESS_KEY_ID=x5x4admin AWS_SECRET_ACCESS_KEY=x5x4secret-throwaway AWS_DEFAULT_REGION=us-east-1
$HERE/run.sh $1 $2 /var/tmp/x5/x4/w_$3 $HERE/out/$3 "X4:setup F-X4c:setup2 F-X4c:purge X5:m4"
