#!/usr/bin/env bash
# X5b K1: X5's scale.py steps, peaks over the API tree + the workerd tree.
cd /var/tmp/x5b/scale
export X5_BASE=http://127.0.0.1:8621 X5_STATE=/var/tmp/x5b/scale/state.json
A=$(cat /var/tmp/x5b/s10m/api.pid); W=$(cat /var/tmp/x5b/s10m/workerd.pid); export X5_PIDS=$A,$W
PY=/home/user/x5b_wt/backend/.venv/bin/python
for s in upload multi join train trainjoin; do echo "== $s $(date +%T)"; $PY scale.py $A $s; done
echo "== register run_train $(date +%T)"; $PY scale.py $A register run_train
echo "== register run_trainjoin $(date +%T)"; $PY scale.py $A register run_trainjoin
echo "== end $(date +%T)"
