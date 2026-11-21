#!/usr/bin/env bash
# P07 verification chain, as the UI calls it. Needs the API on :8000 with a fresh ENSEMBLE_DATA_DIR (= $DATA) and
# accounts.csv from make_data.py in the current directory.
A="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"; J="-H Content-Type:application/json"
py() { python3 -c "import sys,json;t=sys.stdin.read();b,h=(t.rsplit(' HTTP',1)+[''])[:2];d=json.loads(b);d=d.get('detail',d) if isinstance(d.get('detail'),dict) else d;$1"; }
err='print("HTTP",h.strip(),d.get("code"),"|",str(d.get("message") or d.get("detail"))[:300])'
mani() { $A/datasets/$1/manifest | py "m=d.get('manifest',d);p=m['provenance'];print('  manifest op',p['op'],'| tables',[(t['name'],t['table_sha256'][:12],t['n_rows']) for t in m['data']['tables']]);print('  ops',[(o['op'],o.get('table'),o.get('change'),o.get('from_version')) for o in p['ops']]);print('  schema',[(c['name'],c['type']) for c in m['data']['tables'][0]['schema']])"; }
diff() { $A/datasets/$1/diff/$2 | py "r=d['rows'];print('  diff',{k:r.get(k) for k in ('a','b','matched','changed','added','removed','matched_by')})"; }
splits() { $A/datasets/$1/splits | py "[print('  split',s['key'],'holdout',s['holdout_rows'],'sha',s['sha256'][:16],'carried',s.get('carried')) for s in d['splits']]"; }

echo "## 1. v1: POST /uploads → check → commit"
U1=$($A/uploads -F file=@accounts.csv -F name="p07 accounts" -F target=y | py "print(d['upload_id'])")
$A/uploads/$U1/check -X POST >/dev/null
V1=$($A/uploads/$U1/commit -X POST $J -d '{"reason":"P07 v1"}' | py "print(d['version_id'])"); echo "v1 $V1"; mani $V1

echo "## 2. POST …/edit set_cells + drop_column: preview, then commit"
OPS='[{"op":"set_cells","cells":[{"row":3,"column":"income","value":"51000"},{"row_id":10,"column":"age","value":44},{"row":5,"column":"zip","value":"00042"}]},{"op":"drop_column","column":"note"}]'
$A/datasets/$V1/edit -X POST $J -d "{\"by\":\"Ana\",\"ops\":$OPS,\"preview\":true}" -w ' HTTP %{http_code}' | py "print('preview HTTP',h.strip(),{k:d[k] for k in ('rows','n_cols','matched','changed','added','removed','ops_summary','would_be_default')});print('  examples',d['edit_log'][0]['examples']);print('  sample row 5',d['sample'][3])"
V2=$($A/datasets/$V1/edit -X POST $J -d "{\"by\":\"Ana\",\"reason\":\"fix 3 cells, drop note\",\"ops\":$OPS}" -w ' HTTP %{http_code}' | py "print('commit HTTP',h.strip(),d['id'],'created',d['created'],'default',d['default'].get('is_default'),d['default'].get('why'),file=sys.stderr);print(d['id'])")
mani $V2; diff $V1 $V2; splits $V1; splits $V2

echo "## 3. the same edit again → the existing version (dedupe by data_sha256)"
$A/datasets/$V1/edit -X POST $J -d "{\"by\":\"Ana\",\"reason\":\"the same edit again\",\"ops\":$OPS}" -w ' HTTP %{http_code}' | py "print('HTTP',h.strip(),{k:d.get(k) for k in ('id','created','duplicate_of')})"

echo "## 4. DL3 / DL4 guards"
for o in '{"op":"drop_column","column":"y"}' '{"op":"rename_column","column":"y","to":"label"}' '{"op":"cast_column","column":"y","to":"text"}'; do
  $A/datasets/$V2/edit -X POST $J -d "{\"by\":\"Ana\",\"reason\":\"guard\",\"ops\":[$o]}" -w ' HTTP %{http_code}' | py "$err"; done
$A/datasets/$V2/edit -X POST $J -d '{"by":"Ana","reason":"guard","ops":[{"op":"rename_column","column":"y","to":"label"}],"set_target":{"column":"label","reason":"short"}}' -w ' HTTP %{http_code}' | py "$err"
$A/datasets/$V2/edit -X POST $J -d '{"by":"Ana","reason":"one class","ops":[{"op":"filter_rows","where":{"column":"y","cmp":"eq","value":0}}]}' -w ' HTTP %{http_code}' | py "$err"
python3 -c "import json;print(json.dumps({'by':'Ana','reason':'cap','ops':[{'op':'set_cells','cells':[{'row':i%900,'column':'age','value':30} for i in range(5001)]}]}))" > cap.json
$A/datasets/$V2/edit -X POST $J -d @cap.json -w ' HTTP %{http_code}' | py "$err"
for o in '{"op":"set_cells","cells":[{"row":0,"column":"age","value":1e30}]}' '{"op":"set_cells","cells":[{"row":0,"column":"age","value":9223372036854775808}]}' \
         '{"op":"set_cells","cells":[{"row":0,"column":"income","value":1.797e308},{"row":1,"column":"income","value":1.797e308}]}' \
         '{"op":"add_column","name":"big","formula":"income * 1e308"}' '{"op":"fill_missing","column":"age","strategy":"constant","value":18446744073709551616}' \
         '{"op":"replace_values","column":"age","find":44,"replace":1e308}' '{"op":"set_cells","cells":[{"row":0,"column":"age","value":"forty"}]}'; do
  $A/datasets/$V2/edit -X POST $J -d "{\"by\":\"Ana\",\"reason\":\"values\",\"ops\":[$o]}" -w ' HTTP %{http_code}' | py "$err"; done
$A/datasets/$V2/edit -X POST $J -d '{"by":"Ana","reason":"rename with a stated target","ops":[{"op":"rename_column","column":"y","to":"label"}],"set_target":{"column":"label","reason":"the label column is renamed for the bureau"},"preview":true}' -w ' HTTP %{http_code}' | py "print('set_target preview HTTP',h.strip(),d['target'],[w['code'] for w in d['checks']['warnings']])"

echo "## 5. Data Doctor apply (relabel, labels sent as text) → new version, __row_id kept, kinds kept"
V3=$($A/datasets/$V2/data-doctor/apply -X POST $J -d '{"by":"Ana","transforms":[{"op":"relabel","rows":[1,2,4],"labels":["1","1","0"],"reason":"reviewed"}]}' -w ' HTTP %{http_code}' | py "print('HTTP',h.strip(),d['effects'],file=sys.stderr);print(d['dataset']['id'])")
mani $V3; diff $V2 $V3; splits $V3

echo "## 6. a two-table version (seed), then Feature Lab adds a column → only that table is rewritten"
V4=$(ENSEMBLE_DATA_DIR=$DATA PYTHONPATH=$BACKEND $PYBIN two_tables.py $V3 2>/dev/null | tail -1 | cut -d' ' -f1); echo "v4 $V4"; mani $V4
JOB=$($A/datasets/$V4/feature-lab -X POST $J -d '{"by":"Ana","apply":true,"time_limit":60,"candidates":[{"op":"date_part","col":"opened","part":"month"},{"op":"log","col":"income"}]}' | py "print(d['id'])")
for i in $(seq 1 100); do st=$($A/datasets/$V4/feature-lab/$JOB | py "print(d.get('status'))"); [ "$st" = "succeeded" ] || [ "$st" = "failed" ] && break; sleep 3; done
V5=$($A/datasets/$V4/feature-lab/$JOB | py "r=d['result'];print(d['status'],'kept',[t['name'] for t in r['kept']],'oof',round(r['base_score_oof'],4),'→',round(r['final_score_oof'],4),'version',d.get('version'),file=sys.stderr);print((d.get('version') or {}).get('id'))")
mani $V5; diff $V4 $V5
$A/datasets/$V4/feature-lab -X POST $J -d '{"by":"Ana","apply":true,"time_limit":60,"candidates":[{"op":"date_part","col":"opened","part":"month"},{"op":"log","col":"income"}]}' >/dev/null

echo "## 7. an edit of a CSV-era version (the credit-default sample) → a Parquet version of the same root"
$A/datasets/credit-default/edit -X POST $J -d '{"by":"Ana","reason":"P07 on a CSV-era parent","ops":[{"op":"set_cells","cells":[{"row":0,"column":"income_usd","value":12345}]},{"op":"add_rows","rows":[{"applicant_id":"NEW-1","income_usd":50000,"age":30,"defaulted":"No"}]},{"op":"delete_rows","rows":[1]}]}' -w ' HTTP %{http_code}' | py "print('HTTP',h.strip(),d['id'],d['rows'],d['edit']['ops_summary']);open('v7.txt','w').write(d['id'])"
V7=$(cat v7.txt); mani $V7; diff credit-default $V7; splits $V7
echo "$V1 $V2 $V3 $V4 $V5 $V7" > ids.txt
