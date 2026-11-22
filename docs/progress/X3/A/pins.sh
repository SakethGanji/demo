# pins.sh <api> <work>: a join pinned to an ext version with no runs; archive it, then delete it; re-use the join
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; W=$2; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid); E1=$(cat ext_root.id); E2=$(val ed.m.json "d['version_id']")
SPEC="{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":{\"root_id\":\"$E1\",\"version_id\":\"$E2\",\"table\":\"cust\"},\"on\":[\"cust\"],\"prefix\":\"e2__\"}]}"
call jp.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"pin_e2\",\"version_id\":\"$V\",\"spec\":$SPEC}"; JP=$(val jp.json "d['id']"); echo "join $JP pinned to $E2"
call f1.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$JP\"}"; FP=$(val f1.json "d['id']"); echo "frame $FP"
echo "## P1 archive $E2 (only_version)"
call a.json $H $J -X POST $A/datasets/$E2/archive -d '{"by":"Ana","reason":"X3 archive the pinned version","only_version":true}'; val a.json "d.get('detail') or d.get('state') or list(d)[:5]"
call f2.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$JP\"}"; val f2.json "d.get('detail') or ('frame', d['id'], 'cached', d['cached'])"
call v2.json $H $J -X POST $A/datasets/$R/joins/$JP/validate -d "{\"version_id\":\"$V\"}"; val v2.json "d.get('detail') or 'validate ok'"
call r.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$JP\",\"label\":\"y\",\"time_limit\":10}"; val r.json "d.get('detail') or ('run created on an archived pin', d['id'])"
RR=$(val r.json "d.get('id','')"); [ -n "$RR" ] && wait_run $RR
echo "## P2 delete $E2 (admin) — blocked by that run?"
call d.json $HA $J -X POST $A/datasets/$E2/delete -d '{"by":"Ada","reason":"X3 delete the pinned version","only_version":true}'; det d.json
echo "## P3 a second join pinned to the ext root's first version $E1 (no frame, no run) → delete $E1? then use the join"
SPEC3="{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":{\"root_id\":\"$E1\",\"version_id\":\"$E1\",\"table\":\"cust\"},\"on\":[\"cust\"],\"prefix\":\"e1__\"}]}"
call j3.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"pin_e1\",\"version_id\":\"$V\",\"spec\":$SPEC3}"; J3=$(val j3.json "d['id']")
call f3.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$J3\"}"; F3=$(val f3.json "d['id']"); echo "frame $F3 (no run)"
call d3.json $HA $J -X POST $A/datasets/$E1/delete -d '{"by":"Ada","reason":"X3 delete the pinned first version","only_version":true}'; det d3.json
call g3.json $H "$A/datasets/$R/joins/$J3"; val g3.json "d.get('detail') or ('get join ok', len(d.get('frames') or []), 'frames')"
call f4.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$J3\"}"; det f4.json
call g4.json $H "$A/datasets/$R/frames/$F3?verify=1"; val g4.json "d.get('detail') or ('verify', d['verify']['ok'], [(x['table'], x['ok'], x.get('why') or x.get('error')) for x in d['verify']['inputs']])"
call r4.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$J3\",\"label\":\"y\",\"time_limit\":10}"; det r4.json
