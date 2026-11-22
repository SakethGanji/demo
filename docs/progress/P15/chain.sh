#!/usr/bin/env bash
# P15 verification chain: manual API calls in the order the UI makes them. A join and a table run on credit-book (P14),
# register both, card / parity, batches (3 files, a dropped column, a missing file), monitoring, inputs.zip, four eyes,
# then a tampered join cache refusing registration.
# Usage: chain.sh <api base> <batch files dir> <work dir> <db path|url> <lake dir>
set -u
A=${1:-localhost:8000/api}; F=${2:?files}; W=${3:?work}; DB=${4:?db}; LAKE=${5:?lake}
H="-H X-Ensemble-Actor:Ana"; HB="-H X-Ensemble-Actor:Ben"; HA="-H X-Ensemble-Actor:Ada"; J="-H content-type:application/json"
HERE=$(cd "$(dirname "$0")" && pwd); PY=${PY:-python3}
mkdir -p $W; cd $W
val() { python3 -c "import json,sys; d=json.load(open('$1')); print($2)"; }
call() { local out=$1; shift; local c; c=$(curl -s -o $out -w '%{http_code}' "$@"); echo -n "HTTP $c "; }
det() { val $1 "d.get('detail', d)"; }
wait_run() { for i in $(seq 1 120); do s=$(curl -s $H $A/runs/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in queued|running) sleep 5;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
wait_ver() { for i in $(seq 1 120); do s=$(curl -s $H $A/registry/versions/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in building) sleep 5;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
wait_batch() { for i in $(seq 1 120); do s=$(curl -s $H $A/registry/batches/$1 | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])"); case $s in queued|running|pending) sleep 3;; *) echo "$1 $s"; return;; esac; done; echo "$1 timeout"; }
SPEC='{"grain":"applications","joins":[{"type":"n:1","table":"bureau","on":["customer_id"]},{"type":"aggregate","table":"payments","on":["app_id"],"prefix":"pay__","aggs":[{"name":"n_pay","fn":"count"},{"name":"late_mean","fn":"mean","column":"days_late"},{"name":"late_max","fn":"max","column":"days_late"},{"name":"late_180d","fn":"sum","column":"days_late","window":{"date":"paid_on","relative_to":"applied_on","days":180}}]}]}'

echo "## 0. P14 setup: save the join, a table run and a join run on credit-book"
call j1.json $H $J -X POST $A/datasets/credit-book/joins -d "{\"name\":\"apps_full\",\"spec\":$SPEC}"; JID=$(val j1.json "d['id']"); val j1.json "d['id'], d['spec_sha256'][:16]"
call r1.json $H $J -X POST $A/runs -d '{"dataset_id":"credit-book","table":"applications","label":"defaulted","time_limit":30}'; R1=$(val r1.json "d['id']"); echo "table run $R1"
call r2.json $H $J -X POST $A/runs -d "{\"dataset_id\":\"credit-book\",\"join_id\":\"$JID\",\"label\":\"defaulted\",\"time_limit\":30}"; R2=$(val r2.json "d['id']"); echo "join run $R2"
wait_run $R1; wait_run $R2

echo "## 1. POST /runs/{join run}/register → 202; the build's steps"
call g2.json $H $J -X POST $A/runs/$R2/register -d '{"created_by":"Ana","model_name":"credit-join","clone_for_deployment":true}'; V2=$(val g2.json "d['id']"); val g2.json "d['id'], d['status']"
wait_ver $V2
curl -s $H $A/registry/versions/$V2 > v2.json; val v2.json "[(s['name'], s['status'], s.get('summary', '')[:110]) for s in d['progress']['steps']]"
val v2.json "'hashes', {k: (v[:16] if isinstance(v, str) else v) for k, v in d['result']['hashes'].items() if k != 'inputs'}"
val v2.json "'hashes.inputs', [(x['table'], x['dv'], x['sha256'][:12]) for x in d['result']['hashes']['inputs']]"

echo "## 2. GET /registry/versions/{v}/card → tables, join sha, parity"
call c2.json $H $A/registry/versions/$V2/card
val c2.json "'inputs.frame', d['data']['inputs']['frame']"
val c2.json "'inputs.dataset', d['data']['inputs']['dataset'], '| version', {k: d['data']['inputs']['version'][k] for k in ('dv', 'v', 'created_by', 'contract', 'certification')}"
val c2.json "'\n'.join('   table %-12s %-7s %s rows %5s sha %s… source %s uploaded_by %s' % (t['name'], t['role'], t['dv'], t['rows'], t['sha256'][:12], t.get('source_file'), t.get('uploaded_by')) for t in d['data']['inputs']['tables'])"
val c2.json "'join', {k: d['data']['inputs']['join'][k] for k in ('id', 'name', 'spec_version', 'spec_sha256', 'spec_frozen', 'coverage')}"
val c2.json "'split', d['data']['inputs']['split'], '| pii_features', d['data']['inputs']['pii_features'], '| recorded', d['data']['inputs']['recorded']"
val c2.json "'parity', d['data']['parity']['status'], '|', d['data']['parity']['summary']"
val c2.json "'parity detail: cache', d['data']['parity']['cache']['ok'], '| inputs', [(x['table'], x['ok']) for x in d['data']['parity']['inputs']], '| rows', d['data']['parity']['rows'], '| columns', d['data']['parity']['columns_compared'], '| types_equal', d['data']['parity']['types_equal'], '| byte_equal', d['data']['parity']['byte_equal'], '| predictions', d['data']['parity']['predictions']"
curl -s $H "$A/registry/versions/$V2/card?format=md" | sed -n '/Data used in development/,/Serving parity/p'

echo "## 3. POST /registry/versions/{v}/batches with 3 files → scored"
call b1.json $H -X POST $A/registry/versions/$V2/batches -F files=@$F/applications.csv -F files=@$F/bureau.csv -F files=@$F/payments.csv -F created_by=Ana
B1=$(val b1.json "d['id']"); val b1.json "d['id'], d['filename'], d['status']"; wait_batch $B1
curl -s $H $A/registry/batches/$B1 > b1r.json
val b1r.json "'n_rows', d['n_rows'], '| join', {k: d['result']['summary']['join'][k] for k in ('join_name', 'spec_sha256', 'spec_frozen', 'grain', 'grain_rows', 'frame_rows', 'rows_equal_grain')}"
val b1r.json "'tables', {t: (x['file'], x['rows'], x['cast_failures']) for t, x in d['result']['summary']['join']['tables'].items()}"
val b1r.json "'missing', d['result']['summary']['missing_columns'], '| extra', d['result']['summary']['extra_columns'], '| predicted', d['result']['summary'].get('predicted'), '| max_psi', d['result']['drift']['max_psi']"
curl -s $H $A/registry/batches/$B1/download -o scored.csv; python3 -c "import csv; r=list(csv.reader(open('scored.csv'))); print('scored.csv rows', len(r)-1, 'columns', r[0][:3], '…', r[0][-3:])"

echo "## 4. a batch file without a column, a missing file, an unknown file, a single CSV → 400"
call e1.json $H -X POST $A/registry/versions/$V2/batches -F files=@$F/applications.csv -F files=@$F/bad/bureau.csv -F files=@$F/payments.csv -F created_by=Ana; det e1.json
call e2.json $H -X POST $A/registry/versions/$V2/batches -F files=@$F/applications.csv -F files=@$F/payments.csv -F created_by=Ana; det e2.json
call e3.json $H -X POST $A/registry/versions/$V2/batches -F files=@$F/applications.csv -F files=@$F/bureau.csv -F files=@$F/payments.csv -F files=@$F/single.csv -F created_by=Ana; det e3.json

echo "## 5. GET /registry/versions/{v}/monitoring (join version); retrain proposal"
call m2.json $H $A/registry/versions/$V2/monitoring; val m2.json "'monitoring', {k: d[k] for k in list(d)[:6] if not isinstance(d[k], (list, dict))}, '| points', len(d.get('points') or []), '| alerts', list((d.get('alerts') or {}).keys())"
call rt.json $H "$A/registry/versions/$V2/monitoring/retrain"; val rt.json "'retrain ready', d.get('ready'), '|', d.get('blocked')"

echo "## 6. GET /registry/versions/{v}/inputs.zip → manifest + spec + hashes, no rows"
curl -s $H $A/registry/versions/$V2/inputs.zip -o inputs.zip -w 'HTTP %{http_code} %{content_type} %{size_download} bytes\n'
python3 - <<'PY'
import json, zipfile
z = zipfile.ZipFile("inputs.zip")
print("   files", [(i.filename, i.file_size) for i in z.infolist()])
m = json.loads(z.read("manifest.json")); h = json.loads(z.read("hashes.json")); s = json.loads(z.read("join_spec.json"))
print("   manifest", m["format"], m["model"], "v", m["version"], "rows_included", m["rows_included"], "| parity", m["parity"]["status"])
print("   hashes", {k: (v[:16] if isinstance(v, str) else v) for k, v in h.items() if k not in ("tables", "frame", "split")})
print("   hashes.tables", [(t["name"], t["dv"], t["sha256"][:12]) for t in h["tables"]])
print("   join_spec", s["format"], s["grain"], [j["table"] for j in s["joins"]])
blob = b"".join(z.read(n) for n in z.namelist())
print("   no rows: an app_id of the data in the zip?", any(x in blob for x in (b"A0003001", b"\"app_id\": \"A")), "| a customer id?", b"C0" in blob)
PY

echo "## 7. single-table model: register, card, batch, monitoring (unchanged)"
call g1.json $H $J -X POST $A/runs/$R1/register -d '{"created_by":"Ana","model_name":"credit-table"}'; V1=$(val g1.json "d['id']"); wait_ver $V1
call c1.json $H $A/registry/versions/$V1/card; val c1.json "'parity', d['data']['parity'], '| frame', d['data']['inputs']['frame'], '| tables', [(t['name'], t['dv'], t['sha256'][:12]) for t in d['data']['inputs']['tables']], '| join', d['data']['inputs']['join']"
call b2.json $H -X POST $A/registry/versions/$V1/batches -F file=@$F/single.csv -F created_by=Ana; B2=$(val b2.json "d['id']"); wait_batch $B2
curl -s $H $A/registry/batches/$B2 > b2r.json; val b2r.json "'n_rows', d['n_rows'], '| join' , d['result']['summary'].get('join'), '| missing', d['result']['summary']['missing_columns'], '| predicted', d['result']['summary'].get('predicted'), '| max_psi', d['result']['drift']['max_psi']"
call e4.json $H -X POST $A/registry/versions/$V1/batches -F files=@$F/applications.csv -F files=@$F/bureau.csv -F created_by=Ana; det e4.json
call m1.json $H $A/registry/versions/$V1/monitoring; val m1.json "'monitoring keys', sorted(d)[:8], '| points', len(d.get('points') or [])"
call ip1.json $H $A/registry/versions/$V1/inputs.zip; echo "inputs.zip (table model)"; python3 -c "import zipfile; print('  ', zipfile.ZipFile('ip1.json').namelist())"

echo "## 8. approve and promote stay human, four eyes"
call a1.json $H $J -X POST $A/registry/versions/$V2/review -d '{"decision":"approve","reviewer":"Ana","comment":"self"}'; det a1.json
call a2.json $HB $J -X POST $A/registry/versions/$V2/review -d '{"decision":"approve","reviewer":"Ben","comment":"P15 review"}'; val a2.json "(d.get('version') or {}).get('approval') or d.get('detail')"
call p1.json $HB $J -X POST $A/registry/versions/$V2/transition -d '{"stage":"Staging","actor":"Ben"}'; val p1.json "(d.get('version') or {}).get('stage') or d.get('detail')"
call p2.json $H $J -X POST $A/registry/versions/$V2/transition -d '{"stage":"Production","actor":"Ana"}'; val p2.json "(d.get('version') or {}).get('stage') or d.get('detail')"
call p3.json $HB $J -X POST $A/registry/versions/$V2/transition -d '{"stage":"Production","actor":"Ben"}'; val p3.json "(d.get('version') or {}).get('stage') or d.get('detail')"
call p4.json $HB $J -X POST $A/registry/versions/$V2/transition -d '{"stage":"Production","actor":"Ben","override_reason":"P15 test: bureau score is known at decision time"}'; val p4.json "(d.get('version') or {}).get('stage') or d.get('detail')"
echo "## 8b. a batch on the Production join version (monitoring point 2)"
call b3.json $H -X POST $A/registry/versions/$V2/batches -F files=@$F/applications.csv -F files=@$F/bureau.csv -F files=@$F/payments.csv -F created_by=Ana -F data_date=2026-10-31; B3=$(val b3.json "d['id']"); wait_batch $B3
call m3.json $H $A/registry/versions/$V2/monitoring; val m3.json "'monitoring points', [(p.get('batch_id'), p.get('data_date') or p.get('period'), p.get('n_rows')) for p in d.get('points') or []]"
call au.json $H "$A/registry/audit?version_id=$V2"; val au.json "[(a['action'], a['actor'], a.get('to_stage')) for a in (d.get('audit') or d.get('entries') or d)][:8] if isinstance(d, (dict, list)) else d"

echo "## 9. independent check: the batch frame = the training frame's rows 3000–3499; scored = predictor on them"
$PY $HERE/check.py "$DB" "$LAKE" $R2 $V2 $B1 "$(dirname $LAKE)"

echo "## 10. tamper the join cache blob (one byte) → registration refused 409; restored → 202"
CB=$(curl -s $H $A/runs/$R2 | python3 -c "import json,sys; print(json.load(sys.stdin)['config']['join']['cache_blob'])")
P=$(find $LAKE -name "$CB.parquet" -type f | head -1); echo "cache blob ${CB:0:16}… at ${P#$LAKE/}"
cp $P tampered.bak; chmod u+w $P; python3 -c "
import sys; p=sys.argv[1]; b=bytearray(open(p,'rb').read()); b[len(b)//2] ^= 1; open(p,'wb').write(bytes(b))" $P
call t1.json $H $J -X POST $A/runs/$R2/register -d '{"created_by":"Ana","model_name":"credit-join"}'
val t1.json "d['detail']['code'], '|', d['detail']['message'][:300]"
val t1.json "'parity', d['detail']['parity']['status'], 'cache', d['detail']['parity']['cache']"
cp tampered.bak $P
call t2.json $H $J -X POST $A/runs/$R2/register -d '{"created_by":"Ana","model_name":"credit-join"}'; V3=$(val t2.json "d['id']"); wait_ver $V3
curl -s $H $A/registry/versions/$V3 > v3.json; val v3.json "'restored: parity', d['result']['parity']['status'], d['result']['parity']['predictions']['status']"
call au2.json $HA "$A/teams/tm_default/audit?action=register_refused"; val au2.json "[(a['action'], a['actor'], a.get('object_kind'), a.get('object_id'), (a.get('comment') or '')[:120]) for a in d['entries']][:3]"
echo "## 10b. tamper the bureau table's blob (a pinned input) → 409 naming it; restored"
TB=$($PY -c "
import sys
db = sys.argv[1]
sql = \"SELECT blob_sha256 FROM table_files WHERE version_id = 'credit-book' AND table_name = 'bureau'\"
if db.startswith('postgres'):
    import psycopg; print(psycopg.connect(db).execute(sql).fetchone()[0])
else:
    import sqlite3; print(sqlite3.connect(db).execute(sql).fetchone()[0])" "$DB")
TP=$(find $LAKE -name "$TB.parquet" -type f | head -1); echo "bureau blob ${TB:0:16}…"
cp $TP bureau.bak; chmod u+w $TP; python3 -c "
import sys; p=sys.argv[1]; b=bytearray(open(p,'rb').read()); b[len(b)//2] ^= 1; open(p,'wb').write(bytes(b))" $TP
call t3.json $H $J -X POST $A/runs/$R2/register -d '{"created_by":"Ana","model_name":"credit-join"}'
val t3.json "d['detail']['code'], '|', d['detail']['message'][:400]"
cp bureau.bak $TP
echo "R1=$R1 R2=$R2 V1=$V1 V2=$V2 V3=$V3 B1=$B1 B2=$B2" > ids.txt
