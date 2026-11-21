#!/bin/bash
S=${S:-.}; cd $S/final
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"; DB=$S/p05final/ensemble.sqlite3
DS=$(head -1 commit_big.json | python -c "import sys,json;print(json.load(sys.stdin)['version_id'])")
echo "## POST /runs on the 10M version $DS (stopped after data_ready: the fit itself is not P05's)"
$A/runs -d "{\"dataset_id\":\"$DS\",\"label\":\"y\",\"time_limit\":60,\"presets\":\"medium_quality\",\"drop_columns\":[\"note\"]}" -o run10.json -w '  HTTP %{http_code} in %{time_total}s (the 10M split is drawn by this first run)\n'
R=$(python -c "import json;print(json.load(open('run10.json'))['id'])"); python -c "import json;c=json.load(open('run10.json'))['config'];print('  config input_table',c['input_table']);print('  data_sha256_expected',c['data_sha256_expected'])"
for i in $(seq 1 150); do n=$(python -c "import sqlite3;print(sqlite3.connect('$DB').execute(\"select count(*) from run_events where run_id='$R' and type='data_ready'\").fetchone()[0])"); [ "$n" = 1 ] && break; sleep 2; done
python -c "
import sqlite3,json;c=sqlite3.connect('$DB')
for t,d in c.execute(\"select type,data from run_events where run_id='$R' and type in ('data_hashed','splits_hashed','data_ready') order by seq\"):
    d=json.loads(d); print('  event',t,{k:d.get(k) for k in ('match','data_sha256','table','storage','rows','train_rows','holdout_rows','read') if k in d})
r=c.execute(\"select table_name, table_sha256=read_sha256, match, rows_total, rows_used from run_inputs where run_id='$R'\").fetchone(); print('  run_inputs',r)"
curl -s -X POST -H X-Ensemble-Actor:Ana localhost:8000/api/runs/$R/cancel; echo
echo "## a second POST /runs (split already drawn)"; $A/runs -d "{\"dataset_id\":\"$DS\",\"label\":\"y\",\"time_limit\":60,\"drop_columns\":[\"note\"]}" -o run10b.json -w '  HTTP %{http_code} in %{time_total}s\n'; sleep 15; curl -s -X POST -H X-Ensemble-Actor:Ana localhost:8000/api/runs/$(python -c "import json;print(json.load(open('run10b.json'))['id'])")/cancel; echo
echo "## export the 10M version as CSV"
$A/datasets/$DS/export?format=csv\&by=Ana -D h10.txt -o exp10.csv -w '  HTTP %{http_code} in %{time_total}s\n'; grep -i "x-ensemble" h10.txt
sha256sum exp10.csv $S/apps10m.csv | sed 's#'$S'/##'; head -3 exp10.csv
