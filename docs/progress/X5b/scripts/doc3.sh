#!/usr/bin/env bash
# X5b K2: a third 10M fit, then GET /doctor at once (it meets the automatic warm_doctor), peaks over both trees.
cd /var/tmp/x5b/scale; export X5_BASE=http://127.0.0.1:8621 X5_STATE=/var/tmp/x5b/scale/state3.json
A=$(cat ../s10m/api.pid); W=$(cat ../s10m/workerd.pid); export X5_PIDS=$A,$W APIPID=$A
PY=/home/user/x5b_wt/backend/.venv/bin/python
$PY scale.py $A train
R=$($PY -c "import json;print(json.load(open('state3.json'))['run_train'])")
sed 's#/home/user/demo/docs/progress/X5/scale#/var/tmp/x5b/scale#' /home/user/demo/docs/progress/X5/scale/doctor10m.py > doctor10m.py
echo "== GET doctor right after the fit $(date +%T)"; $PY doctor10m.py $R
echo "== GET doctor again (cache) $(date +%T)"; $PY doctor10m.py $R
