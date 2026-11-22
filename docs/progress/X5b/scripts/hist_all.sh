#!/usr/bin/env bash
# history of every dataset (and every version id listed) → status counts
A=$1/api; H="-H X-Ensemble-Actor:Ada"
ids=$(curl -s $H "$A/datasets" | python3 -c "import sys,json;d=json.load(sys.stdin);d=d.get('datasets',d) if isinstance(d,dict) else d;print(' '.join(x['id'] for x in d))")
n=0; for i in $ids; do s=$(curl -s -o /dev/null -w '%{http_code}' $H $A/datasets/$i/history); echo "$s"; done | sort | uniq -c | sed 's/^/  history of every dataset: /'
echo "  ($(echo $ids | wc -w) datasets)"
