#!/usr/bin/env bash
cd /var/tmp/x5b/scale; export X5_BASE=http://127.0.0.1:8621 X5_STATE=/var/tmp/x5b/scale/state_u5.json
A=$(cat ../s10m/api.pid); W=$(cat ../s10m/workerd.pid); export X5_PIDS=$A,$W
PY=/home/user/x5b_wt/backend/.venv/bin/python
echo "== register run_trainjoin on 274a07d $(date +%T)"; $PY scale.py $A register run_trainjoin
M=$($PY -c "import json;print(json.load(open('state_u5.json'))['model_run_trainjoin'])")
curl -s -H X-Ensemble-Actor:x5 localhost:8621/api/registry/versions/$M | $PY -c "
import sys,json;d=json.load(sys.stdin);p=d['result']['parity'];print(' status',d['status'],'| parity',p['status'],p['summary'][:160],'| predictions',{k:p['predictions'][k] for k in ('status','rows','max_abs_diff','ids_equal')})"
echo "== batch 20k on $M"; $PY batchpk.py $A $M jb/apps.csv,jb/branches.csv
echo "== end $(date +%T)"
