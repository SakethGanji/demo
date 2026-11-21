#!/usr/bin/env bash
# P10b S3 checks 6-9: s3checks.sh <data dir>. API on :8000 with ENSEMBLE_BLOB_STORE=s3; ids_s3.txt from chain.sh;
# $PY = the backend's python; AWS_* / ENSEMBLE_S3_* in the environment (tamper.py talks to the bucket directly).
set -u
DATA=$1; HERE=$(cd "$(dirname "$0")" && pwd)
read V1 V2 V3 R1 R2 < ids_s3.txt
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
AD="curl -s -H X-Ensemble-Actor:Ada -H Content-Type:application/json localhost:8000/api"
py() { python3 -c "import sys,json;d=json.load(sys.stdin);d=d.get('detail',d) if isinstance(d,dict) and 'detail' in d else d
$1"; }
cache() { $AD/admin/lake/status | py "c=d['blob_store']['cache'];s=c['stats'];print('  cache: entries',c['entries'],'bytes',c['bytes'],'|',{k:s[k] for k in ('hits','misses','fills','rehash_on_hit','tamper_refetch','fill_integrity_errors')})"; }
rows() { $A/datasets/$1/rows?limit=2 -w ' HTTP %{http_code}' | python3 -c "import sys,json;t=sys.stdin.read();b,h=t.rsplit(' HTTP',1);d=json.loads(b)
print('  GET …/rows',h.strip(),'first accts',[r['acct'] for r in d['rows']] if 'rows' in d else str(d.get('detail',d))[:300])"; }
KEY=$($A/datasets/$V1/manifest | py "print(d['manifest']['data']['tables'][0]['files'][0]['key'])")
echo "v1 data part: $KEY"

echo "## 6. delete the cache directory → re-read: it refills and verifies"
cache
rm -rf "$DATA/cache"; echo "  rm -rf cache/ → exists: $([ -d $DATA/cache/blobs ] && echo yes || echo no)"
rows $V1
$A/datasets/$V1/diff/$V3 | py "r=d.get('rows',d);print('  diff v1 v3',{k:r.get(k) for k in ('matched','changed','added','removed')})"
cache
echo "  cache files: $(find $DATA/cache/blobs -name '*.parquet' | wc -l) parquet; $KEY cached: $([ -f $DATA/cache/blobs/${KEY#blobs/} ] && echo yes || echo no)"

echo "## 7a. tamper with the cached copy (a byte flipped; mtime changes) → next read re-hashes, drops it, re-fetches"
$PY $HERE/tamper.py cache-flip $DATA $KEY | sed 's/^/  /'
rows $V1
cache
echo "  cached copy now: $(sha256sum $DATA/cache/blobs/${KEY#blobs/} | cut -c1-16)… (name ${KEY:9:16}…)"
echo "## 7b. tamper again and put the old mtime back → the ctime still differs: re-hashed, dropped, re-fetched"
$PY $HERE/tamper.py cache-flip-keep-mtime $DATA $KEY | sed 's/^/  /'
rows $V1
cache
echo "  cached copy now: $(sha256sum $DATA/cache/blobs/${KEY#blobs/} | cut -c1-16)…"
echo "## 7c. POST /admin/lake/cache/verify (re-hash every cached file) after a flip that no read has seen yet"
$PY $HERE/tamper.py cache-flip $DATA $KEY | sed 's/^/  /'
$AD/admin/lake/cache/verify -X POST | py "print('  cache/verify: checked',d['checked'],'dropped',[x[:16]+'…' for x in d['dropped']])"
rows $V1
echo "  cached copy now: $(sha256sum $DATA/cache/blobs/${KEY#blobs/} | cut -c1-16)…"

echo "## 8. tamper with the object in the bucket (PUT without If-None-Match: a new version over the locked one)"
$PY $HERE/tamper.py bucket-put $DATA $KEY | sed 's/^/  /'
$PY $HERE/tamper.py bucket-versions $DATA $KEY
$A/datasets/$V1/verify | py "print('  GET …/verify: ok',d['ok'],'| status',d['status'],'| problems',d['problems'][:3])"
$AD/admin/lake/verify-all | py "print('  verify-all: ok',d['ok'],'| failed',[(f.get('id'),f.get('status') or f.get('problems')) for f in d['failed']][:3])"
rm -rf "$DATA/cache"; echo "  cache emptied → a fill now gets the tampered bytes:"
rows $V1
cache
echo "  the locked original cannot be deleted without the governance bypass:"
$PY $HERE/tamper.py bucket-delete $DATA $KEY | sed 's/^/    /'
echo "  undo: delete the tampering version (it has no retention) → the locked original is current again"
$PY $HERE/tamper.py bucket-undo $DATA $KEY | sed 's/^/    /'
$A/datasets/$V1/verify | py "print('  GET …/verify: ok',d['ok'],'| status',d['status'])"
rows $V1

echo "## 9. concurrent writes of the same sha"
$PY $HERE/tamper.py race $DATA 8 | sed 's/^/  /'
echo "  two uploads of one new file (twin.csv: the first 50,000 accounts, never uploaded) committed at the same time:"
head -50001 accounts.csv > twin.csv
stats() { $AD/admin/lake/status | py "s=d['blob_store']['stats'];print('  store stats: puts',s['puts'],'existing',s['put_existing'],'raced',s['put_raced'])"; }
stats
for i in 1 2; do curl -s -H X-Ensemble-Actor:Ana localhost:8000/api/uploads -F file=@twin.csv -F name="p10b twin $i" -F target=y | py "print(d['upload_id'])"; done > ups.txt
for u in $(cat ups.txt); do curl -s -X POST -H X-Ensemble-Actor:Ana localhost:8000/api/uploads/$u/check >/dev/null; done
for u in $(cat ups.txt); do ($A/uploads/$u/commit -X POST -d '{"reason":"P10b twin"}' -w ' HTTP %{http_code}\n' > commit_$u.txt) & done; wait
VS=""; for u in $(cat ups.txt); do VS="$VS $(python3 -c "import json;b,h=open('commit_$u.txt').read().rsplit(' HTTP',1);d=json.loads(b);print(d.get('version_id'))")"; python3 -c "import json;b,h=open('commit_$u.txt').read().rsplit(' HTTP',1);d=json.loads(b);print('  commit',h.strip(),d.get('version_id'),(d.get('detail') or ''))"; done
stats
TK=""; for v in $VS; do k=$($A/datasets/$v/manifest | py "print(d['manifest']['data']['tables'][0]['files'][0]['key'])"); echo "  $v data part $k"; TK=$k; done
$PY $HERE/tamper.py bucket-versions $DATA $TK
cache
