#!/usr/bin/env bash
# P12 verification chain: manual API calls in the order the UI makes them. Usage: chain.sh <api base> <files dir> <work dir>
set -u
A=${1:-localhost:8000/api}; F=${2:?files}; W=${3:?work}; H="-H X-Ensemble-Actor:Ana"; J="-H content-type:application/json"
mkdir -p $W; cd $W
py() { python3 -c "$1"; }
val() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)"; }
code() { curl -s -o $1 -w '%{http_code}' "${@:2}"; }

echo "## 1. POST /uploads book.xlsx + bureau.csv"
curl -s $H $A/uploads -F file=@$F/book.xlsx -F file=@$F/bureau.csv -F name="credit book" -F target=defaulted > u1.json
U1=$(val u1.json "d['upload_id']"); val u1.json "d['upload_id'], d['status'], [(f['filename'], f['bytes']) for f in d['files']]"
echo "## 2. POST /check"
curl -s $H -X POST $A/uploads/$U1/check > c1.json
py "
import json; c=json.load(open('c1.json'))
print('kind', c['kind'], '| primary', c['primary_table'], '| blocks', c['blocks'], '| warnings', c['warnings'])
for t in c['tables']: print('  ', t['name'], t['action'], (t['source_file'] + (' / sheet ' + t['sheet'] if t.get('sheet') else '')), t['n_rows'], 'x', t['n_cols'])
print('   applications schema:', [(s['name'], s['type'], s['reason']) for s in c['tables'][0]['schema']])
print('   sheets:', [(s['sheet'], s.get('skipped') or 'included') for s in c['sheets'] if 'sheet' in s])
print('   suggested relations:', [(r['name'], r['from']['columns'], r['n_orphans'], r['coverage']) for r in c['suggested_relations']])"
echo "## 2b. PATCH the plan (keys, relations), check again"
curl -s $H -X PATCH $A/uploads/$U1 $J -d '{"tables":{"applications":{"key":["app_id"]},"payments":{"key":"payment_id"},"products":{"key":["product"]}},"relations":[{"name":"payments_applications","from":{"table":"payments","columns":["app_id"]},"to":{"table":"applications","columns":["app_id"]}},{"from":{"table":"applications","columns":["product"]},"to":{"table":"products","columns":["product"]}}]}' > p1.json
val p1.json "d['status'], sorted(d['plan']['tables']), [r['name'] for r in d['plan']['relations']]"
curl -s $H -X POST $A/uploads/$U1/check > c1b.json
val c1b.json "'blocks', d['blocks'], '| keys', [(k['table'], k['columns'], k['valid']) for k in d['keys']], '| relations', [(r['name'], r['n_orphans'], r['to_unique']) for r in d['relations']]"
echo "## 3. POST /commit"
echo "HTTP $(code m1.json $H -X POST $A/uploads/$U1/commit $J -d '{"reason":"P12 credit book v1"}')"
V1=$(val m1.json "d['version_id']"); val m1.json "d['version_id'], 'v', d['version_no'], 'n_tables', d['n_tables'], 'primary', d['primary_table'], 'target', d['profile_target']"
echo "## 4. GET /datasets/{root}/versions/{v}"
curl -s $H $A/datasets/$V1/versions/$V1 > v1.json
py "
import json; v=json.load(open('v1.json')); print('data_sha256', v['data_sha256'], '| manifest_sha256', v['manifest_sha256'])
for t in v['tables']: print('  ', t['ordinal'], t['name'], t['change'], t['n_rows'], 'rows', t['table_sha256'], 'keys', [k['columns'] for k in t['keys']], 'splits', [s['split_key'] for s in t['splits']])
print('   relations', [(r['name'], r['n_orphans']) for r in v['relations']])"
echo "## 5. POST /uploads?parent_version_id=v1 payments_more.xlsx, PATCH payments: append, check, commit"
curl -s $H "$A/uploads?parent_version_id=$V1" -F file=@$F/payments_more.xlsx > u2.json; U2=$(val u2.json "d['upload_id']")
curl -s $H -X PATCH $A/uploads/$U2 $J -d '{"tables":{"payments":{"action":"append"}}}' > /dev/null
curl -s $H -X POST $A/uploads/$U2/check > c2.json
val c2.json "'blocks', d['blocks'], '| tables', [(t['name'], t['action'], t['append']) for t in d['tables']], '| carried', d['carried']"
echo "HTTP $(code m2.json $H -X POST $A/uploads/$U2/commit $J -d '{"reason":"P12 November payments"}')"
V2=$(val m2.json "d['version_id']"); val m2.json "d['version_id'], 'v', d['version_no'], d.get('default')"
curl -s $H $A/datasets/$V2/manifest > man2.json
py "
import json; m=json.load(open('man2.json'))['manifest']; v1={t['name']: t['table_sha256'] for t in json.load(open('v1.json'))['tables']}
vt={t['table_name']: t['change'] for t in json.load(open('m2.json'))['tables']}
for t in m['data']['tables']: print('  ', t['name'], vt[t['name']], t['table_sha256'][:16], 'same sha as v1' if v1[t['name']] == t['table_sha256'] else 'new sha', t['n_rows'], [(f['role'], f['n_rows'], f['row_id_min'], f['row_id_max'], f['sha256'][:12]) for f in t['files']])
print('   relations', [(r['name'], r['n_orphans']) for r in m['data']['relations']], '| ops', [o['op'] for o in m['provenance']['ops']])"
echo "## 6. PUT /datasets/v2/tables/bureau/key {columns: [customer_id]}"
echo "HTTP $(code k3.json $H -X PUT $A/datasets/$V2/tables/bureau/key $J -d '{"columns":["customer_id"],"reason":"bureau is one row per customer"}')"
V3=$(val k3.json "d['version_id']"); val k3.json "d['version_id'], 'v', d['version_no'], d['op'], 'keys', d['keys'], '| changes', [(t['table_name'], t['change']) for t in d['tables']]"
echo "HTTP $(code k3b.json $H -X PUT $A/datasets/$V3/tables/bureau/key $J -d '{"columns":"customer_id"}') (again)"; val k3b.json "d['version_id'], 'deduped', d['deduped']"
echo "HTTP $(code k3c.json $H -X PUT $A/datasets/$V3/tables/bureau/key $J -d '{"columns":["score"]}') (bureau.score)"; cat k3c.json; echo
echo "## 6b. PUT /datasets/v3/relations/applications_bureau"
echo "HTTP $(code r4.json $H -X PUT $A/datasets/$V3/relations/applications_bureau $J -d '{"from":{"table":"applications","columns":["customer_id"]},"to":{"table":"bureau","columns":["customer_id"]},"reason":"applicants to their bureau record"}')"
V4=$(val r4.json "d['version_id']"); val r4.json "d['version_id'], 'v', d['version_no'], d['relation']"
echo "## 7. GET /datasets/v4/relations"
curl -s $H $A/datasets/$V4/relations > g4.json
py "
import json; r=json.load(open('g4.json'))
for x in r['relations']: print('  ', x['name'], x['from']['table'], x['from']['columns'], '->', x['to']['table'], x['to']['columns'], 'n_orphans', x['n_orphans'], 'of', x.get('n_from_rows'), 'coverage', x.get('coverage'))
print('   suggested', r['suggested'])"
echo "## 8. POST /runs {dataset_id: v4, table: applications}  and  {table: payments, label: days_late}"
echo "HTTP $(code run1.json $H -X POST $A/runs $J -d "{\"dataset_id\":\"$V4\",\"table\":\"applications\",\"label\":\"defaulted\",\"time_limit\":30}")"
R1=$(val run1.json "d['id']"); val run1.json "d['id'], d['config']['input_ref'], d['config']['input_table']['table_sha256'][:16], d['config']['split'].get('table'), d['config']['split'].get('split_id')"
echo "HTTP $(code run2.json $H -X POST $A/runs $J -d "{\"dataset_id\":\"$V4\",\"table\":\"payments\",\"label\":\"days_late\",\"time_limit\":30,\"drop_columns\":[\"paid_on\"]}")"
R2=$(val run2.json "d['id']"); val run2.json "d['id'], d['config']['input_ref'], d['config']['input_table']['n_rows'], d['config']['split'].get('table'), d['config']['split'].get('split_id')"
for R in $R1 $R2; do for i in $(seq 60); do s=$(curl -s $A/runs/$R | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 5; done
  echo "  $R $s"; curl -s --max-time 3 "$A/runs/$R/events" | grep -E '"type": "(data_hashed|run_succeeded|run_failed)"' | python3 -c "
import sys, json
for l in sys.stdin:
    d=json.loads(l.split('data: ',1)[1]); print('    ', d['type'], {k: d.get(k) for k in ('match','table','storage','best_model','score_test','error') if k in d})"; done
echo "HTTP $(code run3.json $H -X POST $A/runs $J -d "{\"dataset_id\":\"$V4\",\"table\":\"nope\"}") (table nope)"; cat run3.json; echo
echo "## also: diff, edit and verify on one table"
echo "HTTP $(code d12.json $H "$A/datasets/$V1/diff/$V2?table=payments")"; val d12.json "d['a_table'], {k: d['rows'][k] for k in ('a','b','matched','changed','added','removed')}, [(t['table'], t['status']) for t in d['tables']]"
echo "HTTP $(code e5.json $H -X POST $A/datasets/$V4/edit $J -d '{"by":"Ana","table":"bureau","reason":"drop bureau rows with a score below 450 (feed error)","ops":[{"op":"delete_rows","where":{"column":"score","cmp":"lt","value":450}}]}')"
V5=$(val e5.json "d['id']"); val e5.json "d['id'], 'table', d['table'], 'target', d['target'], 'rows (primary)', d['rows'], 'removed', d['edit']['removed']"
curl -s $H $A/datasets/$V5/versions/$V5 > v5.json; val v5.json "'primary', d['primary_table'], [(t['name'], t['change'], t['n_rows'], [s['split_key'][:28] for s in t['splits']]) for t in d['tables']], [(r['name'], r['n_orphans']) for r in d['relations']]"
echo "HTTP $(code d45.json $H "$A/datasets/$V4/diff/$V5?table=bureau")"; val d45.json "{k: d['rows'][k] for k in ('a','b','matched','changed','added','removed')}, [(t['table'], t['status']) for t in d['tables']]"
echo "HTTP $(code vf5.json $H $A/datasets/$V5/verify)"; val vf5.json "'ok', d['ok'], [(f['table'], f['part'], f['status']) for f in d['files']]"
echo "## also: bad workbooks"
for f in bad.xlsx fake.xlsx empty.xlsx header_only.xlsx dup_sheets.xlsx; do
  curl -s $H $A/uploads -F file=@$F/$f > ub.json; U=$(val ub.json "d['upload_id']")
  echo "  $f HTTP $(code cb.json $H -X POST $A/uploads/$U/check) $(cat cb.json)"; done
echo "## also: one CSV, unchanged (P05 / P06)"
curl -s $H $A/uploads -F file=@$F/loans.csv -F target=y > us.json; U=$(val us.json "d['upload_id']")
curl -s $H -X POST $A/uploads/$U/check > cs.json; val cs.json "d['kind'], d['typed'], [(t['name'], t['n_rows'], [(s['name'], s['type']) for s in t['schema']]) for t in d['tables']]"
echo "HTTP $(code ms.json $H -X POST $A/uploads/$U/commit $J -d '{"reason":"single csv"}')"; S1=$(val ms.json "d['version_id']"); val ms.json "d['version_id'], d['n_tables'], [(t['table_name'], t['change']) for t in d['tables']]"
curl -s $H "$A/uploads?parent_version_id=$S1" -F file=@$F/loans_v2.csv > us2.json; U=$(val us2.json "d['upload_id']"); curl -s $H -X POST $A/uploads/$U/check > /dev/null
echo "HTTP $(code ms2.json $H -X POST $A/uploads/$U/commit $J -d '{"reason":"v2"}') $(val ms2.json "d['detail']['code']")"
echo "HTTP $(code ms3.json $H -X POST $A/uploads/$U/commit $J -d '{"reason":"v2","match":{"method":"key","columns":["acct"]}}')"; val ms3.json "d['version_id'], 'v', d['version_no'], {k: d['match'][k] for k in ('matched','changed','added','removed')}"
