#!/usr/bin/env bash
# U2 walk set-up by API (what Settings › Teams would do; the walk shows that page too): two teams and their people.
#   Credit Risk: Ben (owner), Ana (maintainer), Rita (risk reviewer) · Collections: Cole (owner), Carl (member)
A=${A:-http://localhost:8000/api}
j() { curl -s -H "X-Ensemble-Actor: $1" -H 'Content-Type: application/json' -X "$2" "$A$3" ${4:+-d "$4"}; }
id() { python3 -c "import json,sys;print(json.load(sys.stdin)['id'])"; }
CR=$(j Ada POST /teams '{"name":"Credit Risk","by":"Ada"}' | id)
CO=$(j Ada POST /teams '{"name":"Collections","by":"Ada"}' | id)
for m in Ben:owner Ana:maintainer Rita:risk_reviewer; do j Ada POST /teams/$CR/members "{\"name\":\"${m%%:*}\",\"role\":\"${m##*:}\",\"by\":\"Ada\"}" >/dev/null; done
for m in Cole:owner Carl:member; do j Ada POST /teams/$CO/members "{\"name\":\"${m%%:*}\",\"role\":\"${m##*:}\",\"by\":\"Ada\"}" >/dev/null; done
j Cole POST /projects "{\"name\":\"Recoveries\",\"by\":\"Cole\",\"team_id\":\"$CO\"}" >/dev/null
echo "$CR $CO"
