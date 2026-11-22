# joins2.sh <api> <files> <work>: fan-out after a rewrite / a delta on a composite key; saved join re-applied
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; F=$2; W=$3; cd $W; . $HERE/lib.sh
R=$(cat v1.vid); V=$(cat vkeys.vid); J4=$(cat j4.id)
fr() { call $1.json $H $J -X POST $A/datasets/$R/versions/$2/frames -d "{\"join_id\":\"$3\"}"; }
dup() { curl -s $H "$A/uploads?parent_version_id=$1" -F file=@$F/$2 > $3.u.json; local U; U=$(val $3.u.json "d['upload_id']")
  curl -s $H -X PATCH $A/uploads/$U $J -d "$4" > $3.p.json; curl -s $H -X POST $A/uploads/$U/check > $3.c.json
  val $3.c.json "'check blocks', [(b['code'], b.get('table')) for b in d.get('blocks', [])], 'warnings', [w['code'] for w in d.get('warnings', [])]" 2>/dev/null || det $3.c.json
  call $3.m.json $H $J -X POST $A/uploads/$U/commit -d '{"reason":"X3 A fan-out"}'; val $3.m.json "d.get('version_id') or d.get('detail')"; }
if [ -z "${ONLY_B:-}" ]; then
echo "## J5a cust rewritten from cust_dup.csv (C0005 twice) as a new version → frame of the saved n:1 join"
dup $V cust_dup.csv r5 '{"table":"cust","tables":{"cust":{"action":"rewrite","match":{"method":"none"}}}}'
V5=$(val r5.m.json "d.get('version_id','')")
if [ -n "$V5" ]; then curl -s $H $A/datasets/$R/versions/$V5 > v5.json; val v5.json "[(t['name'], t['n_rows'], [(k['columns'], k.get('valid')) for k in t['keys']]) for t in d['tables'] if t['name']=='cust']"
  fr f5 $V5 $J4; det f5.json
  call vv.json $H $J -X POST $A/datasets/$R/joins/$J4/validate -d "{\"version_id\":\"$V5\"}"; det vv.json; fi
fi
echo "## J5b composite key [cust, score] on cust; delta inserts (C0005, 1): key valid, cust repeats → frame of n:1 on cust"
call k5.json $H $J -X PUT $A/datasets/$V/tables/cust/key -d '{"columns":["cust","score"],"reason":"X3 composite"}'; VK=$(val k5.json "d.get('version_id') or d"); echo "composite key version $VK"
dup $VK cust_delta_dup.csv d5 '{"table":"cust","tables":{"cust":{"action":"delta","key":["cust","score"]}}}'
VD=$(val d5.m.json "d.get('version_id','')"); echo "delta version $VD"
[ -n "$VD" ] && { fr f5b $VD $J4; det f5b.json; }
echo "## J5c the same join on the version before the delta (cached frame) still works"
fr f5c $VK $J4; val f5c.json "d.get('detail') or (d['id'], d['cached'], d.get('same_frame_as_version'))"
