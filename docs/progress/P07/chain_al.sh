#!/usr/bin/env bash
# P07 chain, part 2: a run on the edited version (data gate on its table_sha256), then an active-learning round on it
# whose answers come from the simulated oracle as text ("0" / "1") → the new version keeps y as BIGINT (P04's handoff).
A="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"; J="-H Content-Type:application/json"
py() { python3 -c "import sys,json;t=sys.stdin.read();b,h=(t.rsplit(' HTTP',1)+[''])[:2];d=json.loads(b);$1"; }
read V1 V2 V3 V4 V5 V7 < ids.txt
echo "## 8. POST /runs on the edited version v2 ($V2)"
R=$($A/runs -X POST $J -d "{\"dataset_id\":\"$V2\",\"label\":\"y\",\"time_limit\":30,\"presets\":\"medium_quality\",\"by\":\"Ana\"}" | py "print(d['id'])"); echo "run $R"
for i in $(seq 1 120); do st=$($A/runs/$R | py "print(d['status'])"); [ "$st" = "succeeded" ] || [ "$st" = "failed" ] && break; sleep 5; done; echo "status $st"
$A/runs/$R/events?format=json | py "ev=d if isinstance(d,list) else d.get('events',[]);[print('  event',e.get('type') or e.get('event'),{k:v for k,v in (e.get('data') or e).items() if k in ('match','table','storage','table_sha256','rows','train_rows','holdout_rows')}) for e in ev if (e.get('type') or e.get('event')) in ('data_hashed','splits_hashed','data_ready')]"
echo "## 9. active learning on that run: start (pool hide) → simulated oracle answers (text labels) → submit → a version"
$A/runs/$R/active-learning -X POST $J -d '{"pool":"hide","hide_share":0.5,"budget":20,"rounds":1,"compare_random":false,"seeds":1}' -w ' HTTP %{http_code}' | py "print('start HTTP',h.strip(),d.get('status'))"
for i in $(seq 1 120); do st=$($A/runs/$R/active-learning | py "print(d.get('status'))"); [ "$st" = "awaiting_labels" ] || [ "$st" = "failed" ] || [ "$st" = "done" ] && break; sleep 5; done; echo "  queue status $st"
$A/runs/$R/active-learning/simulate -X POST -w ' HTTP %{http_code}' | py "print('simulate HTTP',h.strip(),d)"
AL=$($A/runs/$R/active-learning/submit -X POST -w ' HTTP %{http_code}' | py "print('submit HTTP',h.strip(),d.get('dataset_version') or d,file=sys.stderr);print((d.get('dataset_version') or {}).get('id'))")
$A/datasets/$AL/manifest | py "m=d.get('manifest',d);t=m['data']['tables'][0];print('  manifest op',m['provenance']['op'],'| y type',[c['type'] for c in t['schema'] if c['name']=='y'],'| types kept',[o.get('types') for o in m['provenance']['ops'] if o['op']=='active_learning'])"
$A/datasets/$V2/diff/$AL | py "r=d['rows'];print('  diff',{k:r.get(k) for k in ('matched','changed','added','removed')})"
$A/datasets/$AL | py "print('  profile y classes',d['profile'].get('classes'),'| task',d['profile'].get('task'))"
echo "$R $AL" > run_ids.txt
