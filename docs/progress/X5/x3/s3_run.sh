#!/usr/bin/env bash
# X5/x3: S3 (MinIO 127.0.0.1:9010, Object Lock + versioning) items: X3 A setup.sh, then F-X3b's s3.py (m9 / M8-on-S3, m7, m8)
# and s3_m8.py (M8 with a stacked object in the bucket). Usage: s3_run.sh <port> <bucket> <cache dir> <work dir> <tag>
P=$1; B=$2; C=$3; W=$4; T=$5; PY=/home/user/demo/backend/.venv/bin/python; export PATH=/home/user/demo/backend/.venv/bin:$PATH
export AWS_ACCESS_KEY_ID=x5x3user AWS_SECRET_ACCESS_KEY=$(cat /var/tmp/x5/x3/minio.secret) AWS_DEFAULT_REGION=us-east-1 ENSEMBLE_S3_ENDPOINT=http://127.0.0.1:9010 NO_PROXY=127.0.0.1,localhost
mkdir -p $W; echo "# setup (X3 A setup.sh)"; bash /home/user/demo/docs/progress/X3/A/setup.sh localhost:$P/api /var/tmp/x5/x3/files $W 2>&1 | tail -4
echo "# M8 on S3: a stacked object in the bucket while a frame is built"; $PY /home/user/demo/docs/progress/X5/x3/s3_m8.py $P $B $C $W $T
echo "# F-X3b s3.py"; $PY /home/user/demo/docs/progress/F-X3b/s3.py localhost:$P $B $C $W /var/tmp/x5/x3/files $T
