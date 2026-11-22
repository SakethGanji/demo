# joins1.sh <api> <work>: join edge cases on jx
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; W=$2; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid)
sj() { call $1.json $H $J -X POST $A/datasets/$R/joins -d "{\"name\":\"$2\",\"version_id\":\"$V\",\"spec\":$3}"; }
fr() { call $1.json $H $J -X POST $A/datasets/$R/versions/$2/frames -d "{\"join_id\":\"$3\"}"; }
echo "## J1 n:1 on DATE (apps.applied_on) = TIMESTAMP (daily.day): validation passes?, coverage"
sj j1 j_daily '{"grain":"apps","joins":[{"type":"n:1","table":"daily","on":[["applied_on","day"]],"prefix":"d__"}]}'
val j1.json "d.get('detail') or [(c['table'], c['matched'], c['coverage'], c['orphans'], c['missing_key']) for c in d['validation']['coverage']]"
echo "   control: TIMESTAMP applied_day = TIMESTAMP day"
sj j1b j_daily2 '{"grain":"apps","joins":[{"type":"n:1","table":"daily","on":[["applied_day","day"]],"prefix":"d__"}]}'
val j1b.json "d.get('detail') or [(c['table'], c['matched'], c['coverage'], c['orphans']) for c in d['validation']['coverage']]"
echo "## J2 window on a BIGINT date column (Excel serial paid_serial)"
sj j2 j_serial '{"grain":"apps","joins":[{"type":"aggregate","table":"pays","on":["app_id"],"prefix":"s__","aggs":[{"name":"amt90","fn":"sum","column":"amount","window":{"date":"paid_serial","relative_to":"applied_on","days":-90}}]}]}'
det j2.json
J2=$(val j2.json "d.get('id','')"); [ -n "$J2" ] && { fr f2 $V $J2; det f2.json; }
echo "## J3 window on a text date column (dd/mm/yyyy)"
sj j3 j_dmy '{"grain":"apps","joins":[{"type":"aggregate","table":"pays","on":["app_id"],"prefix":"m__","aggs":[{"name":"amt90","fn":"sum","column":"amount","window":{"date":"paid_dmy","relative_to":"applied_on","days":-90}},{"name":"n90","fn":"count","window":{"date":"paid_dmy","relative_to":"applied_on","days":-90}}]}]}'
det j3.json; J3=$(val j3.json "d.get('id','')"); [ -n "$J3" ] && { fr f3 $V $J3; val f3.json "d.get('detail') or (d['id'], d['n_rows'])"; }
echo "## J4 window on DATE paid_on (boundaries, NULL dates) + skew (A00001 has 30k pays) + NULL keys"
sj j4 j_win '{"grain":"apps","joins":[{"type":"n:1","table":"cust","on":["cust"],"prefix":"c__"},{"type":"aggregate","table":"pays","on":["app_id"],"prefix":"p__","aggs":[{"name":"n","fn":"count"},{"name":"amt","fn":"sum","column":"amount"},{"name":"amt90","fn":"sum","column":"amount","window":{"date":"paid_on","relative_to":"applied_on","days":-90}},{"name":"n90","fn":"count","window":{"date":"paid_on","relative_to":"applied_on","days":-90}},{"name":"n30f","fn":"count","window":{"date":"paid_on","relative_to":"applied_on","days":30}},{"name":"last","fn":"max","column":"paid_on"}]}]}'
val j4.json "d.get('detail') or [(c['table'], c['matched'], c['coverage'], c['orphans'], c['missing_key'], c.get('rows_aggregated')) for c in d['validation']['coverage']]"
J4=$(val j4.json "d['id']"); echo $J4 > j4.id
fr f4 $V $J4; val f4.json "d.get('detail') or (d['id'], d['n_rows'], d['seconds'], [c['name']+':'+c['type'] for c in d['schema']][-8:])"; val f4.json "d['id']" > f4.id
echo "## J5 n:1 to a non-unique side via a version whose cust has a repeated key (fan-out)"
