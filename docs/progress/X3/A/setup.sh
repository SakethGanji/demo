# setup.sh <api> <files> <work>: dataset jx (apps, cust, pays, daily) + keys
HERE=$(cd "$(dirname "$0")" && pwd); A=$1; F=$2; W=$3; mkdir -p $W; cd $W; . $HERE/lib.sh
upload v1 $A/uploads -F file=@$F/apps.csv -F file=@$F/cust.csv -F file=@$F/pays.csv -F file=@$F/daily.csv -F name=jx -F target=y
val v1.c.json "[(t['name'], t['action'], t['n_rows'], [c['name']+':'+c['type'] for c in t['schema']]) for t in d['tables']], d['blocks']"
V=$(cat v1.vid)
for tk in apps:app_id cust:cust pays:pay_id daily:day; do t=${tk%%:*}; k=${tk#*:}
  call k.json $H $J -X PUT $A/datasets/$V/tables/$t/key -d "{\"columns\":[\"$k\"],\"reason\":\"X3\"}"; V=$(val k.json "d.get('version_id') or d"); echo "$t key -> $V"; done
echo $V > vkeys.vid
