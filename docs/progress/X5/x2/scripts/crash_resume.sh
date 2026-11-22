#!/bin/bash
# X5/x2: job 1 with two injected kills (X2's hooks via F-X2's fx2_hook.py), then a clean resume; job 2 for idempotency.
# usage: crash_resume.sh <data dir> <port> <out dir> <L2 id> <A1 id> <rL2 id>
D=$1; P=$2; O=$3; L2=$4; A1=$5; RL2=$6
API=/home/user/demo/docs/progress/X5/x2/scripts/api.sh; PY=/home/user/demo/backend/.venv/bin/python
ST=/home/user/demo/docs/progress/X2/scripts/state.py
A=(-H X-Ensemble-Actor:Ada -H Content-Type:application/json)
alive() { pgrep -f "X2PORT=$P\b" >/dev/null || pgrep -f "port $P\b" >/dev/null; }
echo "== start 1: X2_CRASH_AFTER_COMMIT=$L2"
$API /home/user/demo $D $P hook X2_CRASH_AFTER_COMMIT=$L2
curl -s "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate?dry_run=0" -d '{"by":"Ada"}' | cut -c1-200; echo
for i in $(seq 300); do alive || break; sleep 0.5; done; echo "process gone after ~$((i/2)) s"
grep "X2 HOOK" $D.api.log | tail -2
(cd /home/user/demo/backend && ENSEMBLE_DATA_DIR=$D $PY $ST $D) > $O/state_after_kill1.txt; head -4 $O/state_after_kill1.txt
echo "== window: API without the worker? (start plain API, check L2 reads) — the janitor re-queues, so read fast"
echo "== start 2: X2_CRASH_BEFORE_COMMIT=$A1"
$API /home/user/demo $D $P hook X2_CRASH_BEFORE_COMMIT=$A1
mkdir -p $O/window
for u in "datasets/$L2" "datasets/$L2/rows?limit=3" "datasets/$L2/splits" "runs/$RL2/explain" "admin/lake/runs/$RL2/verify"; do
  curl -s -o $O/window/$(echo $u | tr '/?=&' '____').json -w "GET $u %{http_code}\n" "${A[@]}" "localhost:$P/api/$u"; done
for i in $(seq 300); do alive || break; sleep 0.5; done; echo "process gone after ~$((i/2)) s"
grep "X2 HOOK" $D.api.log | tail -2
(cd /home/user/demo/backend && ENSEMBLE_DATA_DIR=$D $PY $ST $D) > $O/state_after_kill2.txt; head -4 $O/state_after_kill2.txt
echo "== start 3: no hooks"
$API /home/user/demo $D $P none
until curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate | grep -q '"status": *"finished"'; do sleep 2; done
curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate > $O/status_after_job1.json
(cd /home/user/demo/backend && ENSEMBLE_DATA_DIR=$D $PY $ST $D --fp-out $O/fp_after_job1.json) > $O/state_after_job1.txt; cat $O/state_after_job1.txt | head -12
echo "== job 2 (idempotency)"
J=$(curl -s "${A[@]}" -X POST "localhost:$P/api/admin/lake/migrate?dry_run=0" -d '{"by":"Ada"}'); echo "$J" | cut -c1-200
sleep 3; until curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate | grep -q '"status": *"finished"'; do sleep 2; done
curl -s "${A[@]}" localhost:$P/api/admin/lake/migrate > $O/status_after_job2.json
(cd /home/user/demo/backend && ENSEMBLE_DATA_DIR=$D $PY $ST $D --fp-out $O/fp_after_job2.json) > $O/state_after_job2.txt
cmp $O/fp_after_job1.json $O/fp_after_job2.json && echo "FINGERPRINT IDENTICAL after job 2" || diff $O/fp_after_job1.json $O/fp_after_job2.json
