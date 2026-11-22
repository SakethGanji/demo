# pins2.sh <api> <work>: delete the pinned ext v1 accepting irreproducible runs (four eyes), then use the join, frame, run
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; W=$2; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid); E1=$(cat ext_root.id); J3=$(val j3.json "d['id']"); F3=$(val f3.json "d['id']"); RUN=$(val r4.json "d['id']")
call d.json $HA $J -X POST $A/datasets/$E1/delete -d '{"by":"Ada","reason":"X3 delete the pinned first version","only_version":true,"irreproducible":{"reason":"X3 test of a deleted pin"}}'; DR=$(val d.json "d.get('id') or d"); echo "request $DR"
call dd.json -H X-Ensemble-Actor:Ben $J -X POST $A/dataset-requests/$DR/decide -d '{"by":"Ben","approve":true,"comment":"X3"}'; val dd.json "d.get('detail') or d.get('status')"
call vs.json $H $A/datasets/$E1; val vs.json "d.get('detail') or ('state', d.get('state'))"
call f.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$J3\"}"; det f.json
call g.json $H "$A/datasets/$R/frames/$F3?verify=1"; val g.json "d.get('detail') or ('verify', d['verify']['ok'], [(x['table'], x['ok']) for x in d['verify']['inputs']], 'cache', d['verify']['cache']['ok'])"
call g2.json $H "$A/datasets/$R/joins/$J3"; val g2.json "d.get('detail') or 'get join ok'"
call v.json $HA $A/admin/lake/runs/$RUN/verify; val v.json "d.get('detail') or ('run verify ok', d['ok'], d['data'].get('ok'), d['data'].get('ref'))"
call r.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$J3\",\"label\":\"y\",\"time_limit\":10}"; det r.json
call g3.json $H $J -X POST $A/runs/$RUN/register -d '{"created_by":"Ana","model_name":"x3-deleted-pin"}'; det g3.json
