#!/usr/bin/env bash
# P14 verification chain: manual API calls in the order the UI makes them, on the credit-book sample (P12) plus a
# bureau.csv uploaded as its own dataset. Usage: chain.sh <api base> <files dir> <work dir> <db path|url> <lake dir>
set -u
A=${1:-localhost:8000/api}; F=${2:?files}; W=${3:?work}; DB=${4:?db}; LAKE=${5:?lake}
H="-H X-Ensemble-Actor:Ana"; HA="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
HERE=$(cd "$(dirname "$0")" && pwd); PY=${PY:-python3}
mkdir -p $W; cd $W
val() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)"; }
call() { local out=$1; shift; local c; c=$(curl -s -o $out -w '%{http_code}' "$@"); echo -n "HTTP $c "; }
err() { val $1 "(d.get('detail') or d) if isinstance(d.get('detail') or d, str) else {k: v for k, v in (d.get('detail') or d).items() if k in ('code','message','join','column','repeated')}"; }
wait_run() { for i in $(seq 1 90); do s=$(curl -s $H $A/runs/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in queued|running) sleep 5;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
SPEC='{"grain":"applications","joins":[{"type":"n:1","table":"bureau","on":["customer_id"]},{"type":"aggregate","table":"payments","on":["app_id"],"prefix":"pay__","aggs":[{"name":"n_pay","fn":"count"},{"name":"late_mean","fn":"mean","column":"days_late"},{"name":"late_max","fn":"max","column":"days_late"},{"name":"late_180d","fn":"sum","column":"days_late","window":{"date":"paid_on","relative_to":"applied_on","days":180}}]}]}'

echo "## 0. GET /datasets/credit-book/versions/credit-book"
curl -s $H $A/datasets/credit-book/versions/credit-book > v.json
val v.json "'primary', d['primary_table'], [(t['name'], t['n_rows'], [k['columns'] for k in t['keys']]) for t in d['tables']]"

echo "## 1. POST /datasets/credit-book/joins: 1:n without an aggregate, and other bad specs → 400"
call e1.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"1:n","table":"payments","on":["app_id"]}]}}'; err e1.json
call e2.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"n:1","table":"payments","on":["app_id"]}]}}'; err e2.json
call e3.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"n:1","table":"bureau","on":[["customer_id","cust_no"]]}]}}'; err e3.json
call e4.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"aggregate","table":"payments","on":["app_id"],"aggs":[{"name":"s","fn":"sum","column":"paid_on"}]}]}}'; err e4.json
call e5.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"aggregate","table":"payments","on":["app_id"],"aggs":[{"name":"amount","fn":"sum","column":"amount"}]}]}}'; err e5.json
call e6.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"n:1","table":"nope","on":["customer_id"]}]}}'; err e6.json
call e7.json $H $J -X POST $A/datasets/credit-book/joins -d '{"name":"apps_full","spec":{"grain":"applications","joins":[{"type":"n:1","table":"bureau","on":["amount"]}]}}'; err e7.json

echo "## 2. fixed: n:1 bureau + aggregate payments → 201 with coverage; the same again → 200 (same row)"
call j1.json $H $J -X POST $A/datasets/credit-book/joins -d "{\"name\":\"apps_full\",\"spec\":$SPEC}"
JID=$(val j1.json "d['id']"); val j1.json "d['id'], d['name'], 'v', d['spec_version'], 'spec_sha256', d['spec_sha256'][:16]"
val j1.json "'\n'.join('   %s %s on %s: matched %d of %d, coverage %s, orphans %d, key %s%s' % (c['table'], c['type'], c['on'], c['matched'], c['grain_rows'], c['coverage'], c['orphans'], c['key'], (', rows aggregated %d' % c['rows_aggregated']) if 'rows_aggregated' in c else '') for c in d['validation']['coverage'])"
val j1.json "'   columns', d['validation']['n_columns'], [c['name'] for c in d['validation']['columns'] if c['from'] != 'grain']"
call j1b.json $H $J -X POST $A/datasets/credit-book/joins -d "{\"name\":\"apps_full\",\"spec\":$SPEC}"; val j1b.json "d['id'], 'v', d['spec_version']"
call j1c.json $H $J -X POST $A/datasets/credit-book/joins -d "{\"name\":\"other_name\",\"spec\":$SPEC}"; err j1c.json
call jl.json $H $A/datasets/credit-book/joins; val jl.json "[(j['id'], j['name'], j['spec_version'], j['latest']) for j in d['joins']]"

echo "## 3. POST /datasets/credit-book/versions/credit-book/frames {join_id} → rows = grain rows; again → cached"
call f1.json $H $J -X POST $A/datasets/credit-book/versions/credit-book/frames -d "{\"join_id\":\"$JID\"}"
FID=$(val f1.json "d['id']"); val f1.json "d['id'], 'n_rows', d['n_rows'], 'cached', d['cached'], 'grain', d['grain_table'], 'frame_sha256', d['frame_sha256'][:16], 'cache_blob', d['cache_blob'][:16], d['bytes'], 'bytes', d['seconds'], 's'"
val f1.json "'   schema', [c['name'] + ' ' + c['type'] for c in d['schema'][9:]]"
val f1.json "'   inputs', [(x['role'], x['version_id'], x['table'], x['table_sha256'][:12]) for x in d['inputs']]"
call f2.json $H $J -X POST $A/datasets/credit-book/versions/credit-book/frames -d "{\"join_id\":\"$JID\"}"; val f2.json "d['id'], 'n_rows', d['n_rows'], 'cached', d['cached'], 'cache_blob', d['cache_blob'][:16]"
call f3.json $H "$A/datasets/credit-book/frames/$FID?verify=1"; val f3.json "'verify', d['verify']['ok'], 'cache', d['verify']['cache']['ok'], 'inputs', [(x['table'], x['ok']) for x in d['verify']['inputs']]"
echo "## 3b. independent check (pandas recomputation from the lake)"
$PY $HERE/check.py "$DB" "$LAKE" $FID

echo "## 4. POST /runs: a table run, a join run, a join run under a small frame budget (P11 sample)"
call r1.json $H $J -X POST $A/runs -d '{"dataset_id":"credit-book","table":"applications","label":"defaulted","time_limit":30}'; R1=$(val r1.json "d['id']"); echo $R1
call r2.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"credit-book\",\"join_id\":\"$JID\",\"label\":\"defaulted\",\"time_limit\":30}"; R2=$(val r2.json "d['id']"); val r2.json "d['id'], d['name']"
call r3.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"credit-book\",\"join_id\":\"$JID\",\"label\":\"defaulted\",\"time_limit\":20,\"frame_budget_mb\":0.25}"; R3=$(val r3.json "d['id']"); echo $R3
call x1.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"credit-book\",\"join_id\":\"$JID\",\"label\":\"pay__n_pay\"}"; err x1.json
call x2.json $H $J -X POST $A/runs -d '{"dataset_id":"credit-book","join_id":"jn_nope","label":"defaulted"}'; err x2.json
wait_run $R1; wait_run $R2; wait_run $R3
for R in $R1 $R2 $R3; do curl -s $H $A/runs/$R > $R.json; val $R.json "d['id'], d['status'], 'input_ref', d['config']['input_ref'], '| data_sha256', (d['data'].get('data_sha256') or '')[:16], '| read', d['data']['read']['kind'], '| rows used', d['data']['rows_used'], 'of', d['data']['rows_total'], '| sampled', d['data']['frame']['sampled'], '| split', d['data']['split'].get('split_id')"; done
val $R2.json "'   features', d['data']['features']"
val $R3.json "'   frame', d['data']['frame'].get('text')"
$PY $HERE/check.py "$DB" "$LAKE" $FID $R1 $R2 $R3 | grep run_
echo "## 4b. re-verify (GET /admin/lake/runs/{id}/verify)"
for R in $R1 $R2 $R3; do call vr.json $HA $A/admin/lake/runs/$R/verify; val vr.json "d['run_id'], 'ok', d['ok'], '| data', d['data']['ok'], d['data']['recorded'][:16], d['data']['now'][:16], d['data']['ref'], '| split', d['split']['ok']"; done

echo "## 5. POST /compare {a: table run, b: join run} → paired"
call c.json $H $J -X POST $A/compare -d "{\"a\":\"$R1\",\"b\":\"$R2\"}"; val c.json "'paired', d['paired'], '|', d['overlap']['text'], d['overlap']['n_common'], '| metric', d['metric'], 'a', d['score_a'], 'b', d['score_b'], 'delta', round(d['delta'], 5), '| ci', d.get('ci')"

echo "## 6. bureau.csv as its own dataset; a join on credit-book pins its version; a run; delete the bureau version → blocked"
curl -s $H $A/uploads -F file=@$F/bureau.csv -F name="bureau ext" > u.json; U=$(val u.json "d['upload_id']")
curl -s $H -X POST $A/uploads/$U/check > uc.json; val uc.json "'check', d['kind'], d['blocks']"
call um.json $H $J -X POST $A/uploads/$U/commit -d '{"reason":"P14 bureau as its own dataset"}'; VB=$(val um.json "d['version_id']"); echo "bureau version $VB"
SPEC2="{\"grain\":\"applications\",\"joins\":[{\"type\":\"n:1\",\"table\":{\"root_id\":\"$VB\",\"version_id\":\"$VB\",\"table\":\"bureau\"},\"on\":[\"customer_id\"],\"columns\":[\"score\",\"n_inquiries_12m\"],\"prefix\":\"ext__\"}]}"
call j2.json $H $J -X POST $A/datasets/credit-book/joins -d "{\"name\":\"apps_bureau_ext\",\"spec\":$SPEC2}"; JID2=$(val j2.json "d['id']")
val j2.json "d['id'], [(c['table'], c['version_id'], c['coverage'], c['orphans'], c['key']['how']) for c in d['validation']['coverage']]"
call f4.json $H $J -X POST $A/datasets/credit-book/versions/credit-book/frames -d "{\"join_id\":\"$JID2\"}"; FID2=$(val f4.json "d['id']"); val f4.json "d['id'], d['n_rows'], d['cached'], [(x['version_id'], x['table']) for x in d['inputs']]"
call r4.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"credit-book\",\"join_id\":\"$JID2\",\"label\":\"defaulted\",\"time_limit\":20}"; R4=$(val r4.json "d['id']"); wait_run $R4
call us.json $H $A/datasets/$VB/usage; val us.json "'usage runs', [(r['id'], r['status'], r['dataset_id'], r.get('via_join')) for r in d['runs']], '| join_frames', [(f['id'], f['relation'], f['tables']) for f in d['join_frames']]"
val us.json "'blocks_archive_or_delete', d['blocks_archive_or_delete']"
call del.json $H $J -X POST $A/datasets/$VB/delete -d '{"by":"Ana","reason":"P14: try to delete the bureau version"}'; val del.json "(d.get('detail') or d)"
call del2.json $H $J -X POST $A/datasets/credit-book/delete -d '{"by":"Ana","reason":"P14: try to delete the credit book","only_version":true}'; val del2.json "(d.get('detail') or d).get('blockers') if isinstance(d.get('detail'), dict) else d"
call lin.json $H $A/lineage/dataset/$VB; val lin.json "'lineage of the bureau dataset', d['counts'], [(e['src']['kind'], e['src']['id'], e['relation'], e['dst']['kind'], e['dst']['id']) for e in d['edges'] if 'join_frame' in (e['src']['kind'], e['dst']['kind'])]"
call lin2.json $H $A/lineage/run/$R2; val lin2.json "'lineage of the join run: root', d['root'], d['counts']"
$PY $HERE/check.py "$DB" "$LAKE" $FID2 $R4 | grep -v "^   missing"
