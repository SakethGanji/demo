# shared helpers: A=api base, H=actor header
H="-H X-Ensemble-Actor:Ana"; HA="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
val() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)"; }
call() { local out=$1; shift; local c; c=$(curl -s -o $out -w '%{http_code}' "$@"); echo -n "HTTP $c "; }
det() { python3 -c "import json,sys
try: d=json.load(open('$1'))
except Exception: print(open('$1').read()[:300]); raise SystemExit
d=d.get('detail', d) if isinstance(d, dict) else d
print({k: v for k, v in d.items() if k in ('code','message','join','column','repeated','table','columns')} if isinstance(d, dict) else d)"; }
wait_run() { for i in $(seq 1 180); do s=$(curl -s $H $A/runs/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in queued|running) sleep 4;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
wait_ver() { for i in $(seq 1 180); do s=$(curl -s $H $A/registry/versions/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in building) sleep 4;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
wait_batch() { for i in $(seq 1 120); do s=$(curl -s $H $A/registry/batches/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in queued|running|pending) sleep 3;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
upload() {  # upload <out prefix> <extra curl args...>: upload, check, commit; prints version id into <prefix>.vid
  local p=$1; shift
  curl -s $H "$@" > $p.u.json; local U; U=$(val $p.u.json "d['upload_id']")
  curl -s $H -X POST $A/uploads/$U/check > $p.c.json
  call $p.m.json $H $J -X POST $A/uploads/$U/commit -d '{"reason":"X3 A"}'; val $p.m.json "d.get('version_id') or d"; val $p.m.json "d.get('version_id','')" > $p.vid
}
