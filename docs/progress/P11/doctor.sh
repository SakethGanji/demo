#!/usr/bin/env bash
# P11 / P09's open bug: vela/doctor.py's fit gap took the holdout with df.loc[holdout positions]. On a Parquet version
# whose __row_id has gaps that reads the wrong rows (or raises KeyError). A 20k-row version with every 10th row deleted.
set -u
A="curl -s -H X-Ensemble-Actor:Ana -H Content-Type:application/json localhost:8000/api"
U="curl -s -H X-Ensemble-Actor:Ana localhost:8000/api"
py() { python3 -c "import sys,json;d=json.load(sys.stdin)
$1"; }
UP=$($U/uploads -F file=@accounts20k.csv -F name="p11 doctor" -F target=y | py "print(d['upload_id'])"); $U/uploads/$UP/check -X POST >/dev/null
W1=$($A/uploads/$UP/commit -X POST -d '{"reason":"P11 doctor v1"}' | py "print(d['version_id'])")
python3 -c "import json;print(json.dumps({'by':'Ana','reason':'gaps','ops':[{'op':'delete_rows','row_ids':list(range(0,20000,10))}]}))" > edit20k.json
W2=$($A/datasets/$W1/edit -X POST -d @edit20k.json | py "print(d['id'])"); echo "  v1 $W1 → v2 $W2 (18,000 rows, every 10th __row_id gone)"
R=$($A/runs -d "{\"dataset_id\":\"$W2\",\"label\":\"y\",\"time_limit\":30,\"name\":\"p11 doctor\"}" | py "print(d['id'])")
for i in $(seq 1 60); do s=$($A/runs/$R | py "print(d['status'])"); [ "$s" = succeeded -o "$s" = failed ] && break; sleep 3; done
$A/runs/$R | py "print('  run',d['id'],d['status'],'| holdout rows',d['holdout']['n_rows'])"
echo "  the old line, in-process on the same run (df.loc[positions]):"
$PY - "$DATA" $R <<'PY'
import json, os, sqlite3, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, os.environ["BACKEND"]); os.environ["ENSEMBLE_DATA_DIR"] = sys.argv[1]
from app import frames
con = sqlite3.connect(Path(sys.argv[1]) / "ensemble.sqlite3")
cfg = json.loads(con.execute("SELECT config FROM runs WHERE id = ?", (sys.argv[2],)).fetchone()[0])
sp = json.loads(Path(cfg["splits_path"]).read_text()); df = frames.read(frames.resolve(cfg)); pos = np.asarray(sp["holdout"], dtype=int)
try:
    old = df.loc[pos]; print("    old: .loc read", len(old), "rows; the holdout's rows?", old.index.equals(df.index[pos]))
except KeyError as e:
    print("    old: .loc →", "KeyError:", str(e)[:90])
new = df.iloc[pos]; print("    new: .iloc read", len(new), "rows = the holdout's __row_ids:", new.index.equals(df.index[pos]))
PY
$A/runs/$R/doctor | py "f=(d.get('measured') or d).get('fit') if isinstance(d,dict) else None
import json as J;s=J.dumps(d);i=s.find('\"n_holdout\"');print('  GET /runs/{id}/doctor:', s[max(0,i-300):i+40] if i>=0 else s[:600]);print('  errors mentioning the fit gap:', [e for e in (d.get('errors') or []) if 'fit' in e] if isinstance(d,dict) else None)"
