#!/bin/bash
# The reset four-eyes chain, twice: with live data, then with nothing live (the DLA4 "reset request not marked used" case).
H="-H Content-Type:application/json"
req() { curl -s $H -H "X-Ensemble-Actor:$1" -X POST "localhost:8000/api/admin/$2" -d "$3" -w "\n%{http_code}"; }
show() { python3 -c "import sys,json;t=sys.stdin.read().rsplit('\n',1);j=json.loads(t[0]);print(t[1], json.dumps({k:j.get(k) for k in $1})[:300])"; }
status() { curl -s localhost:8000/api/admin/reset-requests | python3 -c "import sys,json;j=json.load(sys.stdin);rs=j.get('requests',j);print([(r['id'],r['status'],r.get('used_by')) for r in rs])"; }
for round in 1 2; do
  echo "== round $round"
  rid=$(req Ana reset-requests '{"by":"Ana","reason":"P04 reset check round '$round'"}' | tee /dev/stderr | head -1 | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
  req Ben "reset-requests/$rid/decide" '{"by":"Ben","approve":true,"comment":"ok"}' | show '["id","status","decided_by"]'
  req Ana reset "{\"confirm\":\"reset\",\"by\":\"Ana\",\"request_id\":\"$rid\",\"reason\":\"P04 check\"}" | show '["four_eyes","by","detail"]'
  status
done
echo "== a third request (refused with 409 while an approved request stayed unused)"
req Ana reset-requests '{"by":"Ana","reason":"P04 reset check round 3"}' | show '["id","status","detail"]'
