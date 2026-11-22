# reg2.sh <api> <files> <work>: new versions of the pinned inputs, then parity / batches / archive / delete
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; F=$2; W=$3; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid); E1=$(cat ext_root.id); EK=$(cat ext_k.id); R1=$(cat r1.id); M1=$(cat m1.id); JM=$(cat jm.id)
delta() { curl -s $H "$A/uploads?parent_version_id=$1" -F file=@$F/$2 > $3.u.json; local U; U=$(val $3.u.json "d['upload_id']")
  curl -s $H -X PATCH $A/uploads/$U $J -d "$4" > /dev/null; curl -s $H -X POST $A/uploads/$U/check > $3.c.json
  call $3.m.json $H $J -X POST $A/uploads/$U/commit -d '{"reason":"X3 A new input version"}'; val $3.m.json "d.get('version_id') or d.get('detail')"; }
echo "## R4 batch (clean) on the join model before any new version"
call b1.json $H -X POST $A/registry/versions/$M1/batches -F files=@$F/batch/clean/apps.csv -F files=@$F/batch/clean/cust.csv -F files=@$F/batch/clean/pays.csv -F created_by=Ana; B1=$(val b1.json "d['id']"); wait_batch $B1
curl -s $H $A/registry/batches/$B1/download -o b1.csv; echo $B1 > b1.id
echo "## R5 new versions: ext (delta: 60 scores +100) and jx (pays delta) and make them default"
delta $EK ext_delta.csv ed '{"table":"cust","tables":{"cust":{"action":"delta"}}}'; E2=$(val ed.m.json "d.get('version_id','')")
delta $V pays_delta.csv pd '{"table":"pays","tables":{"pays":{"action":"delta"}}}'; V2=$(val pd.m.json "d.get('version_id','')")
echo "## R6 re-register the same run (parity from the pinned inputs) → ok?"
call g3.json $H $J -X POST $A/runs/$R1/register -d '{"created_by":"Ana","model_name":"x3-join"}'; M3=$(val g3.json "d.get('id') or d"); wait_ver $M3
call c3.json $H $A/registry/versions/$M3/card; val c3.json "'parity', d['data']['parity']['status'], '| tables', [(t['name'], t['dv']) for t in d['data']['inputs']['tables']]"
echo "## R7 batch (clean) again → predictions identical to R4?"
call b2.json $H -X POST $A/registry/versions/$M1/batches -F files=@$F/batch/clean/apps.csv -F files=@$F/batch/clean/cust.csv -F files=@$F/batch/clean/pays.csv -F created_by=Ana; B2=$(val b2.json "d['id']"); wait_batch $B2
curl -s $H $A/registry/batches/$B2/download -o b2.csv; cmp b1.csv b2.csv && echo "   b1.csv == b2.csv (byte-equal)"
echo "## R8 the join on the new jx version: still pinned to ext $EK (not $E2)?"
call f8.json $H $J -X POST $A/datasets/$R/versions/$V2/frames -d "{\"join_id\":\"$JM\"}"; val f8.json "d.get('detail') or [(x['role'], x['version_id'], x['table']) for x in d['inputs']]"
echo "## R9 batches: reordered + extra columns; coercion failures"
call b3.json $H -X POST $A/registry/versions/$M1/batches -F files=@$F/batch/reorder/apps.csv -F files=@$F/batch/reorder/cust.csv -F files=@$F/batch/reorder/pays.csv -F created_by=Ana; B3=$(val b3.json "d.get('id') or d"); wait_batch $B3
curl -s $H $A/registry/batches/$B3 > b3r.json; val b3r.json "d['status'], d.get('error'), {t: (x['rows'], x['cast_failures'], x['extra_columns']) for t, x in d['result']['summary']['join']['tables'].items()}, '| extra', d['result']['summary'].get('extra_columns')"
curl -s $H $A/registry/batches/$B3/download -o b3.csv
call b4.json $H -X POST $A/registry/versions/$M1/batches -F files=@$F/batch/coerce/apps.csv -F files=@$F/batch/coerce/cust.csv -F files=@$F/batch/coerce/pays.csv -F created_by=Ana; B4=$(val b4.json "d.get('id') or d"); wait_batch $B4
curl -s $H $A/registry/batches/$B4 > b4r.json; val b4r.json "d['status'], d.get('error'), {t: (x['rows'], x['cast_failures']) for t, x in d['result']['summary']['join']['tables'].items()}"
val b4r.json "'summary keys', sorted(d['result']['summary']), '| warnings', d['result']['summary'].get('warnings') or d['result'].get('warnings')"
curl -s $H $A/registry/batches/$B4/download -o b4.csv
python3 - <<'PY'
import csv
def load(p):
    r = list(csv.DictReader(open(p))); return {x["app_id"]: x for x in r}, list(r[0].keys())
a, ca = load("b1.csv"); b, cb = load("b3.csv"); c, cc = load("b4.csv")
pc = [k for k in ca if k.startswith("pred") or k.startswith("prob") or k.startswith("score")]
print("   prediction columns", pc, "| b3 columns", cb[-4:])
for name, o in (("reorder", b), ("coerce", c)):
    diff = [k for k in a if any(a[k][p] != o[k].get(p) for p in pc)]
    print(f"   {name}: rows {len(o)}, rows whose prediction differs from the clean batch: {len(diff)}", diff[:25])
PY
echo "## R10 archive / delete the pinned ext version $EK while a model is registered on it"
call ar.json $H $J -X POST $A/datasets/$EK/archive -d '{"by":"Ana","reason":"X3 archive the pinned ext version","only_version":true}'; det ar.json
call dl.json $HA $J -X POST $A/datasets/$EK/delete -d '{"by":"Ada","reason":"X3 delete the pinned ext version","only_version":true}'; det dl.json
call us.json $H $A/datasets/$EK/usage; val us.json "'usage runs', [(r['id'], r.get('via_join', {}).get('relation')) for r in d['runs']], '| blocks', d.get('blocks_archive_or_delete')"
