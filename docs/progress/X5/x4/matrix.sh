#!/usr/bin/env bash
# X5 lane x4: the main matrix pass list (one call = one engine × mode). Usage: matrix.sh <audit|enforce> <port> <tag>
# Postgres when ENSEMBLE_DB_URL is set.
STEPS="X4:setup X4:sweep X4:agent F-X4b:agent X4:decide X4:governance F-X4c:setup2 F-X4c:purge X5:m4 X4:teams X5:c4 X4:audit F-X4b:audit F-X4b:tamper"
HERE=$(cd "$(dirname "$0")" && pwd)
$HERE/run.sh $1 $2 /var/tmp/x5/x4/w_$3 $HERE/out/$3 "$STEPS"
