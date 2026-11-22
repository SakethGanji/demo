#!/usr/bin/env bash
# X5 lane x4: extra.py (on a fresh set-up) + governance + the routes the sweep leaves unfilled. Usage: probe_pass.sh <mode> <port> <tag>
HERE=$(cd "$(dirname "$0")" && pwd)
$HERE/run.sh $1 $2 /var/tmp/x5/x4/w_$3 $HERE/out/$3 "X4:setup X5:extra X4:governance X5:unfilled"
