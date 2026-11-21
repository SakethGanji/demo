#!/usr/bin/env bash
# P10b: the API with ENSEMBLE_S3_READ=httpfs and ENSEMBLE_DUCK_EXTENSION_DIR=<bundle> (~/.duckdb moved away): previews,
# filters and diffs read s3:// through DuckDB httpfs; frames (diff's parts, training) still read the verified cache.
set -u
read V1 V2 V3 R1 R2 < ids_s3.txt
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
AD="curl -s -H X-Ensemble-Actor:Ada -H Content-Type:application/json localhost:8000/api"
py() { python3 -c "import sys,json;d=json.load(sys.stdin);d=d.get('detail',d) if isinstance(d,dict) and 'detail' in d else d
$1"; }
$AD/admin/lake/status | py "s=d['blob_store'];print('  read',s['read'],'| duck extension_directory',d['duck']['settings'].get('extension_directory'),'| autoinstall',d['duck']['settings'].get('autoinstall_known_extensions'))"
$A/datasets/$V1/manifest | py "print('  manifest resolved:',list(d['resolved'].values())[0])"
for i in 1 2; do s=$(date +%s.%N); $A/datasets/$V1/rows?limit=3 | py "print('  GET …/rows',[r['acct'] for r in d['rows']],end='')"; echo " ($(python3 -c "print(round($(date +%s.%N)-$s,3))") s)"; done
$A/datasets/$V2/rows?limit=3\&after=150000 | py "print('  GET …/rows after=150000',[r['__row_id'] if '__row_id' in r else r.get('acct') for r in d['rows']])"
$A/datasets/$V1/diff/$V3 | py "r=d.get('rows',d);print('  diff v1 v3',{k:r.get(k) for k in ('matched','changed','added','removed')})"
$AD/admin/lake/status | py "s=d['blob_store'];c=s['cache']['stats'];print('  store gets',s['stats']['gets'],'| cache',{k:c[k] for k in ('hits','misses','fills')})"
