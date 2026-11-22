# reg.sh <api> <files> <work>: cross-dataset pin, a join model, parity after new input versions, batches, archive/delete
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; F=$2; W=$3; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid)
fr() { call $1.json $H $J -X POST $A/datasets/$R/versions/$2/frames -d "{\"join_id\":\"$3\"}"; }
echo "## R0 ext = cust.csv as its own dataset, key cust"
upload e1 $A/uploads -F file=@$F/cust.csv -F name=ext; E1=$(cat e1.vid)
call ek.json $H $J -X PUT $A/datasets/$E1/tables/cust/key -d '{"columns":["cust"],"reason":"X3"}'; EK=$(val ek.json "d['version_id']"); echo "ext keyed $EK"; echo $E1 > ext_root.id; echo $EK > ext_k.id
SPEC="{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":{\"root_id\":\"$E1\",\"version_id\":\"$EK\",\"table\":\"cust\"},\"on\":[\"cust\"],\"prefix\":\"x__\"},{\"type\":\"aggregate\",\"table\":\"pays\",\"on\":[\"app_id\"],\"prefix\":\"p__\",\"aggs\":[{\"name\":\"n\",\"fn\":\"count\"},{\"name\":\"amt90\",\"fn\":\"sum\",\"column\":\"amount\",\"window\":{\"date\":\"paid_on\",\"relative_to\":\"applied_on\",\"days\":-90}}]}]}"
call jm.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"model_join\",\"version_id\":\"$V\",\"spec\":$SPEC}"; JM=$(val jm.json "d['id']"); echo "join $JM"; echo $JM > jm.id
SPEC2="{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":\"cust\",\"on\":[\"cust\"],\"prefix\":\"l__\"},{\"type\":\"n:1\",\"table\":{\"root_id\":\"$E1\",\"version_id\":\"$EK\",\"table\":\"cust\"},\"on\":[\"cust\"],\"prefix\":\"x__\"}]}"
call ja.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"two_custs\",\"version_id\":\"$V\",\"spec\":$SPEC2}"; JA=$(val ja.json "d.get('id') or d"); echo "join (two tables named cust) $JA"
echo "## R1 runs on both joins (time_limit 20)"
call r1.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$JM\",\"label\":\"y\",\"time_limit\":20}"; R1=$(val r1.json "d.get('id') or d"); echo $R1 > r1.id
call r2.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$JA\",\"label\":\"y\",\"time_limit\":20}"; R2=$(val r2.json "d.get('id') or d"); echo $R2 > r2.id
wait_run $R1; wait_run $R2
echo "## R2 register the join model"
call g1.json $H $J -X POST $A/runs/$R1/register -d '{"created_by":"Ana","model_name":"x3-join"}'; M1=$(val g1.json "d.get('id') or d"); wait_ver $M1; echo $M1 > m1.id
call c1.json $H $A/registry/versions/$M1/card; val c1.json "'parity', d['data']['parity']['status'], '|', d['data']['parity'].get('summary'), '| tables', [(t['name'], t['dv']) for t in d['data']['inputs']['tables']]"
call g2.json $H $J -X POST $A/runs/$R2/register -d '{"created_by":"Ana","model_name":"x3-two-custs"}'; M2=$(val g2.json "d.get('id') or d"); wait_ver $M2; echo $M2 > m2.id
call c2.json $H $A/registry/versions/$M2/card; val c2.json "'parity', d['data']['parity']['status'], '|', d['data']['parity'].get('summary')"
echo "## R3 batch on the two-cust model (tables share a name)"
call b0.json $H -X POST $A/registry/versions/$M2/batches -F files=@$F/batch/clean/apps.csv -F files=@$F/batch/clean/cust.csv -F created_by=Ana; det b0.json
