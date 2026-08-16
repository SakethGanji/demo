#!/usr/bin/env bash
# Run pytest in an isolated "worker" environment, so several suite runs can
# proceed concurrently against the one dev Postgres and the one MinIO.
#
# Why this exists: the default test environment is shared and singular. The
# autouse `_db_cleanup` fixture DELETEs every mutable domain table before each
# test, and the session-scoped `_fresh_environment` fixture purges both storage
# backends. Two concurrent default runs therefore delete each other's rows and
# blobs, and fail in ways that look exactly like real product bugs.
#
# A worker gets its own Postgres database, its own local storage dir, its own
# tus staging dir, and its own MinIO prefix. `ACCELERATOR_KEEP_TEST_STATE=1`
# suppresses the session purge, which is safe because the database is already
# private to the worker and is reset by `provision_test_workers.sh`.
#
# Usage:
#   scripts/test_worker.sh w3 tests/unit/test_masking.py
#   scripts/test_worker.sh w3 tests/ -k saved_views
#
# Provision the databases first, once:
#   scripts/provision_test_workers.sh 8
#
# NOTE: this does not make the DEFAULT environment safe to share. Never run a
# plain `pytest` at the same time as anything else.
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "usage: $0 <worker-id> <pytest-args...>" >&2
    echo "example: $0 w3 tests/unit/" >&2
    exit 2
fi

WORKER="$1"
shift

cd "$(dirname "$0")/.."

export ACCELERATOR_DB_NAME="accelerator_${WORKER}"
export ACCELERATOR_STORAGE_DIR="/tmp/accelerator_${WORKER}"
export ACCELERATOR_TUS_UPLOAD_DIR="/tmp/accelerator_${WORKER}/tus_uploads"
export TEST_S3_PREFIX="tests_${WORKER}"
export ACCELERATOR_KEEP_TEST_STATE=1

# `venv/bin/python -m pytest`, never `venv/bin/pytest` — some venv shebangs in
# this repo point at a stale pre-move path.
exec venv/bin/python -m pytest "$@" -p no:randomly
