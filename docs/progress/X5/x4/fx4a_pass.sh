#!/usr/bin/env bash
# X5 lane x4: F-X4a's own checks (fx4a.py: C2 masking pages, unmask path, fairness purity, M3 batches, M1, M2, M5) on a fresh
# set-up, then c4.py again with ENSEMBLE_SQL_ALLOW_LOOPBACK=1 (address-alias probes of the platform server).
# Usage: fx4a_pass.sh <audit|enforce> <port> <tag>. Postgres when ENSEMBLE_DB_URL is set.
HERE=$(cd "$(dirname "$0")" && pwd)
X5_M4_PART=2 X5_LOOPBACK=1 $HERE/run.sh $1 $2 /var/tmp/x5/x4/w_$3 $HERE/out/$3 "X4:setup X5:extra X5:m4 F-X4a:fx4a X5:c4 F-X4b:audit F-X4b:tamper"
