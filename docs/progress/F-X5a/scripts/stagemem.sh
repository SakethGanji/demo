#!/usr/bin/env bash
# print the workerd tree's largest process RSS whenever the batch stage changes; usage: stagemem.sh <batch id>
B=$1; W=$(cat /var/tmp/fx5/big/workerd.pid); last=""
while true; do
  st=$(curl -s -H 'X-Ensemble-Actor: x5' localhost:8621/api/registry/batches/$B | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'],'|',(d.get('stage') or '')[:40])")
  m=0; for p in $W $(pgrep -P $W); do r=$(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null); [ -n "$r" ] && [ $r -gt $m ] && m=$r; done
  key=$(echo "$st" | sed 's/[0-9,]* \/ [0-9,]* adverse.*/…/'); echo "$(date +%T) $st max_rss_gb=$(echo "scale=2;$m/1000000"|bc)" >> /var/tmp/fx5/scale/stagemem_$B.log
  case "$st" in succeeded*|failed*) break;; esac; sleep 1
done
