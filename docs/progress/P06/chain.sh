#!/usr/bin/env bash
# P06 verification chain (DLA4 #2), as the UI will call it. Needs the API on :8000 with a fresh ENSEMBLE_DATA_DIR,
# and v1.csv / v2.csv / fixed.txt from make_data.py in the current directory.
A="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"; J="-H Content-Type:application/json"
code() { python3 -c "import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);d=d.get('detail',d);print('HTTP',h.strip(),d.get('code'),'|',(d.get('message') or '')[:420]);$1"; }
echo "## 1. v1: POST /uploads → check → commit"
U1=$($A/uploads -F file=@v1.csv -F name="dla4 accounts" -F target=y | python3 -c "import sys,json;print(json.load(sys.stdin)['upload_id'])")
$A/uploads/$U1/check -X POST >/dev/null
V1=$($A/uploads/$U1/commit -X POST $J -d '{"reason":"P06 v1"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['version_id'])")
$A/datasets/$V1 | python3 -c "import sys,json;d=json.load(sys.stdin);print('v1',d['id'],d['rows'],'rows; profile role of acct:',[c.get('role') for c in d['profile']['columns'] if c['name']=='acct'])"
echo "## 2. v2 (15 corrected incomes): POST /uploads parent_version_id=v1 → check"
U2=$($A/uploads -F file=@v2.csv -F parent_version_id=$V1 | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['upload_id'])")
$A/uploads/$U2/check -X POST | python3 -c "
import sys,json;nv=json.load(sys.stdin)['next_version']
print('parent',nv['parent_version_id'],'table',nv['table'],'parent_rows',nv['parent_rows'],'parent_holdout',nv['parent_holdout'])
for c in nv['candidates']: print('  candidate',c['columns'],'score',c['score'],'overlap',c['overlap'],'valid',c['valid'],'id_like',c['id_like'])
for m in nv['methods']: print('  simulate',m['label'],m['roles'],{k:m.get(k) for k in ('matched','changed','added','removed','holdout_identity_lost')},'warnings',[w['code'] for w in m['warnings']],'needs',m['needs_acknowledge'])
print('preselect',nv['preselect'])"
echo "## 3. commit without match"
$A/uploads/$U2/commit -X POST $J -d '{}' -w ' HTTP %{http_code}' | code "print('candidates',[c['columns'] for c in d['candidates']])"
echo "## 4. commit {match: row_hash}, no acknowledge"
$A/uploads/$U2/commit -X POST $J -d '{"match":{"method":"row_hash"}}' -w ' HTTP %{http_code}' | code "print('warnings',[w['code'] for w in d['warnings']])"
echo "## 5. commit {match: key acct}"
V2=$($A/uploads/$U2/commit -X POST $J -d '{"match":{"method":"key","columns":["acct"]},"reason":"15 corrected incomes"}' -w ' HTTP %{http_code}' | python3 -c "
import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b)
print('HTTP',h.strip(),d['version_id'],'v',d['version_no'],[(t['table_name'],t['change']) for t in d['tables']],'default',d.get('default'),file=sys.stderr)
print('match',d['match'],file=sys.stderr);print(d['version_id'])")
echo "## 6. GET /datasets/v1/diff/v2"
$A/datasets/$V1/diff/$V2 | python3 -c "import sys,json;r=json.load(sys.stdin)['rows'];print({k:r[k] for k in ('a','b','matched','changed','added','removed','matched_by')})"
echo "## 7. GET …/splits (v1, v2)"
for v in $V1 $V2; do $A/datasets/$v/splits | python3 -c "import sys,json;[print('$v',s['key'],'holdout_rows',s['holdout_rows'],'sha256',s['sha256'][:16],'carried',s.get('carried')) for s in json.load(sys.stdin)['splits']]"; done
echo "## 8. manifest of v2"
$A/datasets/$V2/manifest | python3 -c "import sys,json;d=json.load(sys.stdin);m=d.get('manifest',d);p=m['provenance'];print('op',p['op'],'ops',[o['op'] for o in p['ops']],'checks',p['checks']);print('match',[o['match'] for o in p['ops'] if o['op']=='upload_version'][0]);print('keys',m['data']['tables'][0]['keys'])"
echo "## 9. repeat commit; the same file again (dedupe by data_sha256)"
$A/uploads/$U2/commit -X POST $J -d '{"match":{"method":"key","columns":["acct"]}}' -w ' HTTP %{http_code}\n' | python3 -c "import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);print('HTTP',h.strip(),d['version_id'],'repeat',d['repeat'])"
U3=$($A/uploads -F file=@v2.csv -F root_id=$V1 | python3 -c "import sys,json;print(json.load(sys.stdin)['upload_id'])"); $A/uploads/$U3/check -X POST >/dev/null
$A/uploads/$U3/commit -X POST $J -d '{"match":{"method":"declared"}}' -w ' HTTP %{http_code}\n' | python3 -c "import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);print('HTTP',h.strip(),d['version_id'],'deduped',d['deduped'],'match',d['match']['method'],d['match']['columns'])"
echo "## 10. the old route POST /datasets/{id}/versions (today's UI)"
curl -s -w ' HTTP %{http_code}' localhost:8000/api/datasets/$V1/versions -F file=@v2.csv -F by=Ana | code ""
curl -s -w ' HTTP %{http_code}\n' localhost:8000/api/datasets/$V1/versions -F file=@v2.csv -F by=Ana -F preview=true | python3 -c "import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);print('preview HTTP',h.strip(),{k:d[k] for k in ('matched_by','matched','changed','added','removed','holdout_identity_lost','needs_match')})"
curl -s -w ' HTTP %{http_code}' localhost:8000/api/datasets/$V1/versions -F file=@v2.csv -F by=Ana -F row_key=hash | code ""
curl -s -w ' HTTP %{http_code}\n' localhost:8000/api/datasets/$V1/versions -F file=@v2.csv -F by=Ana -F row_key=hash -F acknowledge=holdout_identity_loss -F make_default=no | python3 -c "import sys;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);import json;d=json.loads(b);u=d['upload'];print('HTTP',h.strip(),d['id'],{k:u[k] for k in ('matched_by','matched','changed','added','removed','holdout_identity_lost','acknowledged')},u['default'])"
echo "$V1 $V2" > ids.txt
