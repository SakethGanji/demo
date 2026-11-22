# pins3.sh <api> <files> <work>: ext2 dataset; a join pinned to it with a frame and NO run; delete ext2 (four eyes); then use the join
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; F=$2; W=$3; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid)
upload e9 $A/uploads -F file=@$F/cust.csv -F name=ext2; E9=$(cat e9.vid)
SPEC="{\"grain\":\"apps\",\"joins\":[{\"type\":\"n:1\",\"table\":{\"root_id\":\"$E9\",\"version_id\":\"$E9\",\"table\":\"cust\"},\"on\":[\"cust\"],\"prefix\":\"e9__\"}]}"
call j.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"pin_e9\",\"version_id\":\"$V\",\"spec\":$SPEC}"; JX=$(val j.json "d['id']")
call f.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$JX\"}"; FX=$(val f.json "d['id']"); echo "join $JX frame $FX (cache $(val f.json "d['cache_blob'][:12]"))"
call u.json $H $A/datasets/$E9/usage; val u.json "'usage', [r['id'] for r in d['runs']], [f['id'] for f in d['join_frames']], d.get('blocks_archive_or_delete')"
call d.json $HA $J -X POST $A/datasets/$E9/delete -d '{"by":"Ada","reason":"X3 delete a version a saved join pins"}'; DR=$(val d.json "d.get('id') or d"); echo "request $DR"
call dd.json -H X-Ensemble-Actor:Ben $J -X POST $A/dataset-requests/$DR/decide -d '{"by":"Ben","approve":true,"comment":"X3"}'; val dd.json "d.get('detail') or d.get('status')"
call vs.json $H $A/datasets/$E9; val vs.json "d.get('detail') or ('state', d.get('state'))"
call f2.json $H $J -X POST $A/datasets/$R/versions/$V/frames -d "{\"join_id\":\"$JX\"}"; det f2.json
call g.json $H "$A/datasets/$R/frames/$FX?verify=1"; val g.json "d.get('detail') or ('verify', d['verify']['ok'], [(x['table'], x['ok']) for x in d['verify']['inputs']], 'cache', d['verify']['cache']['ok'])"
call g2.json $H "$A/datasets/$R/joins/$JX"; val g2.json "d.get('detail') or ('get join', d['name'])"
call v.json $H $J -X POST $A/datasets/$R/joins/$JX/validate -d "{\"version_id\":\"$V\"}"; det v.json
call r.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"$V\",\"join_id\":\"$JX\",\"label\":\"y\",\"time_limit\":10}"; det r.json
call l.json $H $A/datasets/$R/versions/$V/frames; val l.json "d.get('detail') or [(f['id'], f.get('cache_blob','')[:12]) for f in (d.get('frames') or d)]"
