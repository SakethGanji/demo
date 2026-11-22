#!/usr/bin/env bash
# More minimal repros (path/body-type 5xx). Usage: repro2.sh <base> <state.json>
A=$1/api; S=$2
st() { python3 -c "import json;print(json.load(open('$S'))['$1'])"; }
V1=$(st v1); RUN=$(st run); MV=$(st mv); CR=$(st cr)
H="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"; L=$(python3 -c 'print("A"*10000)'); T=$(mktemp)
c() { local lab=$1; shift; printf '%s | %s\n' "$(curl -s -o $T -w '%{http_code}' --max-time 90 "$@")" "$lab"; head -c 160 $T | tr '\n' ' '; echo; }
echo "## R11 10 KB path id -> OSError(ENAMETOOLONG) on file-backed stores"
c "GET /agent/sessions/{10KB}" $H "$A/agent/sessions/$L"
c "GET /autopilot/{10KB}" $H "$A/autopilot/$L"
c "GET /autopilot/{10KB}/inbox" $H "$A/autopilot/$L/inbox"
c "POST /autopilot/{10KB}/resume" $H -X POST "$A/autopilot/$L/resume"
c "GET /datasets/{v1}/feature-lab/{10KB}" $H "$A/datasets/$V1/feature-lab/$L"
c "GET /runs/{run}/review/{10KB}" $H "$A/runs/$RUN/review/$L"
c "POST /vela/ask {autopilot_id: 20KB}" $H $J -X POST $A/vela/ask -d "{\"question\":\"hi\",\"autopilot_id\":\"$L$L\"}"
c "control GET /autopilot/nonexistent" $H "$A/autopilot/ap_nonexistent"
echo "## R12 non-text action / kind"
c "POST /authz/check {action: []}" $H $J -X POST $A/authz/check -d "{\"actor\":\"Ana\",\"action\":[],\"object_id\":\"$V1\"}"
c "POST /registry/versions/{mv}/changes {kind: []}" $H $J -X POST $A/registry/versions/$MV/changes -d '{"by":"Ada","kind":[]}'
c "POST /datasets/{v1}/decision-time {run_ids: {a:1}}" $H $J -X POST $A/datasets/$V1/decision-time -d '{"by":"Ada","run_ids":{"a":1}}'
c "POST /datasets/{v1}/decision-time {object_id: false}" $H $J -X POST $A/datasets/$V1/decision-time -d '{"by":"Ada","object_id":false}'
c "POST /admin/lake/blobs/{sha}/restore {by: 1}" $H $J -X POST $A/admin/lake/blobs/$(printf 'a%.0s' {1..64})/restore -d '{"by":1}'
echo "## R13 big ints into SQLite"
c "GET /teams/{cr}/audit?cursor=2**64" $H "$A/teams/$CR/audit?cursor=18446744073709551616"
c "POST /runs/{run}/review {max_experiments: 1e30 int}" $H $J -X POST $A/runs/$RUN/review -d '{"by":"Ada","max_experiments":123456789012345678901234567890}'
c "POST /runs/{run}/review {max_minutes: 1e30 int}" $H $J -X POST $A/runs/$RUN/review -d '{"by":"Ada","max_minutes":123456789012345678901234567890}'
rm -f $T
