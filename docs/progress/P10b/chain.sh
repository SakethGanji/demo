#!/usr/bin/env bash
# P10b core chain, as the UI calls the API: chain.sh <tag>. Needs the API on :8000 (run_api.sh) and accounts.csv / v2.csv
# from make_data.py in the current directory. Works the same with ENSEMBLE_BLOB_STORE=s3 and =local.
set -u
TAG=${1:-s3}
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
U="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"
AD="curl -s -H X-Ensemble-Actor:Ada -H Content-Type:application/json localhost:8000/api"
py() { python3 -c "import sys,json;d=json.load(sys.stdin);d=d.get('detail',d) if isinstance(d,dict) and 'detail' in d else d
$1"; }
wait_run() { for i in $(seq 1 120); do s=$($A/runs/$1 | py "print(d['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 4; done; }
run() { $A/runs -d "{\"dataset_id\":\"$1\",\"label\":\"y\",\"time_limit\":20,\"presets\":\"medium_quality\",\"drop_columns\":[\"acct\"],\"name\":\"$2\"}" | py "print(d.get('id') or d)"; }
t() { date +%s.%N; }

echo "## 0. GET /admin/lake/status: the store"
$AD/admin/lake/status | py "s=d['blob_store'];print('  store',d['store'],'| kind',s['kind'],'| read',s.get('read'),'| object_lock',s.get('object_lock'),s.get('lock_mode'),s.get('lock_days'),'| versioning',s.get('versioning'))"

echo "## 1. v1: POST /uploads → check → commit (200,000 rows)"
T0=$(t)
UP=$($U/uploads -F file=@accounts.csv -F name="p10b accounts $TAG" -F target=y | py "print(d['upload_id'])")
$U/uploads/$UP/check -X POST >/dev/null
V1=$($A/uploads/$UP/commit -X POST -d '{"reason":"P10b v1"}' | py "print(d['version_id'])"); echo "  v1 $V1 ($(python3 -c "print(round($(t)-$T0,2))") s)"
$A/datasets/$V1/manifest | py "
fs=[f for t in d['manifest']['data']['tables'] for f in t['files']]
print('  manifest',d['manifest_sha256'][:12]+'…','| data',d['data_sha256'][:12]+'…','| parts',len(fs),'| resolved',list(d['resolved'].values())[0])"
$A/datasets/$V1/rows?limit=3 | py "print('  GET …/rows (preview):',d.get('n_rows') or d.get('total'),'rows | first',[r.get('acct') for r in d['rows'][:3]])"

echo "## 2. v2 = next file (POST /uploads parent_version_id=v1 → check → commit {match key acct}) → GET …/diff"
U2=$($U/uploads -F file=@v2.csv -F parent_version_id=$V1 | py "print(d['upload_id'])")
$U/uploads/$U2/check -X POST >/dev/null
V2=$($A/uploads/$U2/commit -X POST -d '{"match":{"method":"key","columns":["acct"]},"reason":"P10b next extract"}' | py "print(d['version_id'])"); echo "  v2 $V2"
$A/datasets/$V1/diff/$V2 | py "r=d.get('rows',d);print('  diff v1 v2',{k:r.get(k) for k in ('a','b','matched','changed','added','removed','matched_by')})"

echo "## 3. v3 = POST …/edit on v2 (set 2 cells, delete 2 rows)"
OPS='[{"op":"set_cells","cells":[{"row_id":10,"column":"age","value":44},{"row_id":11,"column":"segment","value":"corp"}]},{"op":"delete_rows","row_ids":[12,13]}]'
V3=$($A/datasets/$V2/edit -X POST -d "{\"by\":\"Ana\",\"reason\":\"P10b edit\",\"ops\":$OPS}" | py "print(d['id'])"); echo "  v3 $V3"
$A/datasets/$V2/diff/$V3 | py "r=d.get('rows',d);print('  diff v2 v3',{k:r.get(k) for k in ('a','b','matched','changed','added','removed','matched_by')})"

echo "## 4. r1 on v1, r2 on v3 (POST /runs, 20 s) → POST /compare"
R1=$(run $V1 "p10b r1 $TAG"); R2=$(run $V3 "p10b r2 $TAG"); wait_run $R1; wait_run $R2
for R in $R1 $R2; do $A/runs/$R | py "print('  run',d['id'],d['status'],d.get('error') or '','| dataset',d['config']['dataset_id'] if 'config' in d else '','| holdout',{k:round(v,4) for k,v in (d.get('holdout') or {}).get('metrics',{}).items() if k in ('accuracy','roc_auc')})"; done
$A/compare -d "{\"a\":\"$R1\",\"b\":\"$R2\"}" | py "print('  compare r1 r2:',d.get('paired'),'|',d.get('label'),'| overlap',(d.get('overlap') or {}).get('n_common'),'| Δ',round(d['delta'],4) if d.get('delta') is not None else None)"

echo "## 5. verify-all and GC dry run"
$AD/admin/lake/verify-all | py "print('  verify-all ok',d['ok'],'| datasets',d['datasets'],'| checked',d['checked'],'| failed',d['failed'][:5])"
$AD/admin/lake/gc?dry_run=1 -X POST | py "print('  gc dry run: blobs',d['blobs'],'refs',d['blob_refs'],'| candidates',len(d['candidates']),'| orphans',len(d['orphans']),'young',d['orphans_in_grace'],'| integrity',[(i['key'][-20:],i['finding']) for i in d['integrity']],'| stale tmp',len(d['stale_tmp']))"
echo "$V1 $V2 $V3 $R1 $R2" > ids_$TAG.txt
$AD/admin/lake/status | py "s=d['blob_store'];st=s.get('stats') or {};c=(s.get('cache') or {}).get('stats') or {}
print('  store stats',{k:st.get(k) for k in ('puts','put_bytes','put_mb_s','put_existing','gets','get_bytes','get_mb_s')})
print('  cache',{k:c.get(k) for k in ('hits','misses','fills','fill_bytes','fill_mb_s','tamper_refetch','evictions')})"
