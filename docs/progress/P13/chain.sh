#!/usr/bin/env bash
# P13 verification chain: manual API calls in the order the UI makes them. Usage: chain.sh <api base> <files dir> <work dir>
set -u
A=${1:-localhost:8000/api}; F=${2:?files}; W=${3:?work}; H="-H X-Ensemble-Actor:Ana"; J="-H content-type:application/json"
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p $W; cd $W
py() { python3 -c "$1"; }
val() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)"; }
code() { curl -s -o $1 -w '%{http_code}' "${@:2}"; }
delta_upload() {  # <parent> <file> <plan json> <out prefix>: upload, PATCH, check (HTTP code printed)
  curl -s $H "$A/uploads?parent_version_id=$1" -F file=@$F/$2 > $4.u.json; local U; U=$(val $4.u.json "d['upload_id']")
  curl -s $H -X PATCH $A/uploads/$U $J -d "$3" > $4.p.json
  echo "check HTTP $(code $4.c.json $H -X POST $A/uploads/$U/check)"; echo $U > $4.id
}
show_check() { py "
import json; c=json.load(open('$1'))
if 'tables' not in c: print('  ', c); raise SystemExit
print('   blocks', [(b['code'], b.get('table')) for b in c['blocks']], '| warnings', [(w['code'], w.get('table')) for w in c['warnings']], '| carried', c.get('carried'))
for t in c['tables']:
    d=t.get('delta')
    if d: print('  ', t['name'], t['action'], 'mode', d.get('mode'), '| key', d['key'], 'from', d['key_from'], '| op column', d['op_column'], '| ops', d.get('ops'))
    if d and 'inserted' in d: print('     inserted', d['inserted'], 'updated', d['updated'], '(unchanged', str(d['updated_unchanged']) + ')', 'deleted', d['deleted'], 'missing', d['missing'], '| update_of_missing_key', d['update_of_missing_key'], 'insert_of_existing_key', d['insert_of_existing_key'], '| rows', d['parent_rows'], '->', d['rows_after'], '| holdout rows deleted', d['holdout_rows_deleted'], '| columns new', d['columns_new'], 'missing', d['columns_missing'])"; }
show_version() { curl -s $H $A/datasets/$V1/versions/$1 > $2; curl -s $H $A/datasets/$1/manifest > $2.man; py "
import json; v=json.load(open('$2')); prev={t['name']: t['table_sha256'] for t in json.load(open('${3:-}'))['tables']} if '${3:-}' else {}
mf={t['name']: t['files'] for t in json.load(open('$2.man'))['manifest']['data']['tables']}
for t in v['tables']: print('  ', t['name'], t['change'], t['n_rows'], 'rows', t['table_sha256'][:16], ('same sha as parent' if prev.get(t['name']) == t['table_sha256'] else 'new sha') if prev else '', '| keys', [k['columns'] for k in t['keys']], '| splits', [s['split_key'][:32] for s in t['splits']], '| parts', [(p['role'], p['n_rows'], p['bytes'], p['sha256'][:10]) for p in mf[t['name']]])"; }

echo "## 1. POST /uploads loans.csv + branches.csv → check → commit (no key declared)"
curl -s $H $A/uploads -F file=@$F/loans.csv -F file=@$F/branches.csv -F name="loan book" -F target=defaulted > u1.json
U1=$(val u1.json "d['upload_id']"); curl -s $H -X POST $A/uploads/$U1/check > c1.json; val c1.json "'blocks', c1 if (c1:=d['blocks']) else [], [(t['name'], t['action'], t['n_rows']) for t in d['tables']]"
echo "commit HTTP $(code m1.json $H -X POST $A/uploads/$U1/commit $J -d '{"reason":"P13 loan book v1"}')"
V1=$(val m1.json "d['version_id']"); val m1.json "d['version_id'], 'v', d['version_no'], [(t['table_name'], t['n_rows']) for t in d['tables']]"
show_version $V1 v1.json ""

echo "## 2. delta1.csv on v1, no key in the plan, none declared → 400"
delta_upload $V1 delta1.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' d0; cat d0.c.json; echo

echo "## 3. PUT /datasets/v1/tables/loans/key {columns: [acct]} → v2 (every table carried)"
echo "HTTP $(code k2.json $H -X PUT $A/datasets/$V1/tables/loans/key $J -d '{"columns":["acct"],"reason":"one row per account"}')"
V2=$(val k2.json "d['version_id']"); val k2.json "d['version_id'], 'v', d['version_no'], d['op'], [(t['table_name'], t['change']) for t in d['tables']]"
show_version $V2 v2.json v1.json

echo "## 4. delta1.csv on v2 (key from the parent) → /check"
delta_upload $V2 delta1.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' d1; show_check d1.c.json
echo "## 5. commit → v3"
echo "commit HTTP $(code m3.json $H -X POST $A/uploads/$(cat d1.id)/commit $J -d '{"reason":"P13 November delta"}')"
V3=$(val m3.json "d['version_id']"); val m3.json "d['version_id'], 'v', d['version_no'], d.get('default'), {t: {k: x[k] for k in ('mode','change','inserted','updated','deleted','missing','first_row_id')} for t, x in d['deltas'].items()}"
show_version $V3 v3.json v2.json
curl -s $H $A/datasets/$V3/manifest > man3.json
val man3.json "[(o['op'], o.get('table'), {k: o[k] for k in ('mode','inserted','updated','deleted','missing') if k in o} or {k: o.get('carried', {}).get(k) for k in ('kept_holdout','new_rows','new_holdout')}) for o in d['manifest']['provenance']['ops'] if o['op'] in ('delta','split_carried')]"
echo "## 6. GET /datasets/v2/diff/v3?table=loans (equals the delta counts)"
echo "HTTP $(code d23.json $H "$A/datasets/$V2/diff/$V3?table=loans")"; val d23.json "{k: d['rows'][k] for k in ('a','b','matched','changed','added','removed')}, [(t['table'], t['status']) for t in d['tables']]"
val d23.json "'changed columns', [(c['column'], c.get('n_changed')) for c in d.get('columns', []) if c.get('n_changed')][:6]" 2>/dev/null || true
py "
import json; c=json.load(open('m3.json'))['deltas']['loans']; r=json.load(open('d23.json'))['rows']
print('   delta inserted/updated/deleted', (c['inserted'], c['updated'] - c['updated_unchanged'], c['deleted']), '== diff added/changed/removed', (r['added'], r['changed'], r['removed']), ':', (c['inserted'], c['updated'] - c['updated_unchanged'], c['deleted']) == (r['added'], r['changed'], r['removed']))"
echo "## 7. holdout identity of the updated rows (v2 → v3 splits)"
PYTHONPATH=$HERE/../../../backend ${PYX:-python3} $HERE/holdout.py $V2 $V3 loans $F/delta1.csv acct 2>&1 | grep -v INFO

echo "## 8. a duplicate-key delta → 400 listing the keys"
delta_upload $V3 delta_dup.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' dd; py "
import json; c=json.load(open('dd.c.json'))['detail']; print('  ', c['code'], '|', c['message']); print('   keys', c.get('keys'), '| n_duplicate_keys', c.get('n_duplicate_keys'))"

echo "## 9. an insert-only delta (300 rows) → change appended, bytes ≈ the delta"
delta_upload $V3 delta_inserts.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' di; show_check di.c.json
echo "commit HTTP $(code m4.json $H -X POST $A/uploads/$(cat di.id)/commit $J -d '{"reason":"P13 new accounts"}')"
V4=$(val m4.json "d['version_id']"); val m4.json "d['version_id'], 'v', d['version_no'], {t: (x['mode'], x['change'], x['inserted'], x.get('first_row_id')) for t, x in d['deltas'].items()}"
show_version $V4 v4.json v3.json
py "
import json, os; P={t['name']: t['files'] for t in json.load(open('v4.json.man'))['manifest']['data']['tables']}['loans']; ap=[p for p in P if p['role']=='append']; base=[p for p in P if p['role']!='append']
print('   bytes written: append part', sum(p['bytes'] for p in ap), 'for', sum(p['n_rows'] for p in ap), 'rows | the delta file', os.path.getsize('$F/delta_inserts.csv'), 'bytes | base parts reused', sum(p['bytes'] for p in base), 'bytes', sum(p['n_rows'] for p in base), 'rows')"
echo "HTTP $(code d34.json $H "$A/datasets/$V3/diff/$V4?table=loans")"; val d34.json "{k: d['rows'][k] for k in ('a','b','matched','changed','added','removed')}"

echo "## 10. a delta on the other table (branches) of v4 → loans carried with the same sha"
delta_upload $V4 branches_delta.csv '{"table":"branches","tables":{"branches":{"action":"delta","key":["branch"]}}}' db; show_check db.c.json
echo "commit HTTP $(code m5.json $H -X POST $A/uploads/$(cat db.id)/commit $J -d '{"reason":"P13 branch 005 moved, branch 021 opened"}')"
V5=$(val m5.json "d['version_id']"); val m5.json "d['version_id'], 'v', d['version_no'], {t: (x['mode'], x['change'], x['inserted'], x['updated'], x['deleted']) for t, x in d['deltas'].items()}"
show_version $V5 v5.json v4.json
echo "HTTP $(code d45.json $H "$A/datasets/$V4/diff/$V5?table=branches")"; val d45.json "{k: d['rows'][k] for k in ('a','b','matched','changed','added','removed')}, [(t['table'], t['status']) for t in d['tables']]"

echo "## 11. refusals and warnings"
delta_upload $V5 delta_badop.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' dx; val dx.c.json "d['detail']['code'], d['detail']['message']"
delta_upload $V5 delta_badop.csv '{"table":"loans","tables":{"loans":{"action":"delta","ops":{"X":"delete"}}}}' dx2; show_check dx2.c.json
delta_upload $V5 delta_newcol.csv '{"table":"loans","tables":{"loans":{"action":"delta"}}}' dn; show_check dn.c.json
delta_upload $V5 delta_newcol.csv '{"table":"loans","tables":{"loans":{"action":"delta","allow_new_columns":true}}}' dn2; show_check dn2.c.json
delta_upload $V5 delta1.csv '{"table":"loans","tables":{"loans":{"action":"delta","key":"segment"}}}' dk; val dk.c.json "d.get('detail')"
delta_upload $V5 delta1.csv '{"table":"nope","tables":{"nope":{"action":"delta","key":"acct"}}}' dt; show_check dt.c.json
curl -s $H "$A/uploads?parent_version_id=$V5" -F file=@$F/delta1.csv > dl.u.json; UL=$(val dl.u.json "d['upload_id']")
curl -s $H -X POST $A/uploads/$UL/check > dl.c.json; val dl.c.json "'no plan →', [(w['code'], w['message'][:90]) for w in d['warnings'] if w['code']=='looks_like_delta']"
echo "## 12. GET /datasets/v5/verify"
echo "HTTP $(code vf5.json $H $A/datasets/$V5/verify)"; val vf5.json "'ok', d['ok'], [(f['table'], f['part'], f['status']) for f in d['files']]"
