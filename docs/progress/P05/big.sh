#!/bin/bash
# the 10M chain: /uploads → /check → /commit, timed, with the API's peak RSS sampled every 0.2 s
S=${S:-.}; cd $S
PID=$(pgrep -f "uvicorn app.main:app" | head -1); rm -f stop; (./rss.sh $PID stop > final/rss_big.txt &)
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
T0=$(date +%s.%N)
curl -s -H X-Ensemble-Actor:Ana localhost:8000/api/uploads -F file=@apps10m.csv -F name="applications 10M" -F target=y -w '\nHTTP %{http_code} %{time_total}s\n' > final/up_big.json; T1=$(date +%s.%N)
U=$(head -1 final/up_big.json | python -c "import sys,json;print(json.load(sys.stdin)['upload_id'])")
$A/uploads/$U/check -X POST -w '\nHTTP %{http_code} %{time_total}s\n' > final/check_big.json; T2=$(date +%s.%N)
$A/uploads/$U/commit -X POST -d '{"reason":"P05 10M upload"}' -w '\nHTTP %{http_code} %{time_total}s\n' > final/commit_big.json; T3=$(date +%s.%N)
touch stop; sleep 1; cat final/rss_big.txt
python -c "print('upload %.1fs check %.1fs commit %.1fs total %.1fs'%($T1-$T0,$T2-$T1,$T3-$T2,$T3-$T0))"
tail -1 final/up_big.json; tail -1 final/check_big.json; tail -1 final/commit_big.json
