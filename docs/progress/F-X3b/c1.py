"""C1 check: online /predict and /score on a join model go through the join, and agree with the batch path and with
training, on rows. Usage (env ENSEMBLE_DATA_DIR [+ ENSEMBLE_DB_URL] of the API): c1.py <api> <model version> <batch dir>
<registry dir of the version>"""
import json, sys, time, urllib.request
from pathlib import Path

import numpy as np, pandas as pd

api, mv, bdir, vdir = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
H = {"X-Ensemble-Actor": "Ana", "content-type": "application/json"}


def call(method, path, body=None, files=None):
    req = urllib.request.Request(f"http://{api}{path}", method=method, headers=H,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def recs(df):
    return json.loads(df.to_json(orient="records"))  # NaN → null, numbers stay numbers


apps = pd.read_csv(bdir / "apps.csv", dtype=str, keep_default_na=False)
cust = pd.read_csv(bdir / "cust.csv")
pays = pd.read_csv(bdir / "pays.csv")
apps_json = [{k: (v if v != "" else None) for k, v in r.items()} for r in apps.to_dict(orient="records")]

print("## 1. /predict on a join model without the other tables → 400 naming them")
s, o = call("POST", f"/api/registry/versions/{mv}/predict", {"row": apps_json[0], "explain": False})
d = o.get("detail", o)
print(s, d.get("code"), d.get("tables"), "| inputs", [(x["alias"], x["role"]) for x in d.get("inputs", [])])
print("## 2. /score with a table lacking a column → 400 batch_column_missing")
s, o = call("POST", f"/api/registry/versions/{mv}/score", {"rows": apps_json[:3], "tables": {"cust": recs(cust.drop(columns=["score"])), "pays": []}})
d = o.get("detail", o); print(s, d.get("code"), d.get("table"), d.get("columns"))
print("## 3. /score, grain rows + cust rows + pays rows (all 300 batch rows) vs the batch of the same files")
t0 = time.time()
s, sc = call("POST", f"/api/registry/versions/{mv}/score", {"rows": apps_json, "tables": {"cust": recs(cust), "pays": recs(pays)}})
print(s, "n_rows", sc.get("n_rows"), "missing_columns", sc.get("missing_columns"), "| join", {k: sc.get("join", {}).get(k) for k in ("grain_rows", "frame_rows", "spec_frozen")},
      "| tables", {a: t["rows"] for a, t in sc.get("join", {}).get("tables", {}).items()}, f"| {time.time() - t0:.1f}s")
import subprocess
out = subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor:Ana", "-X", "POST", f"http://{api}/api/registry/versions/{mv}/batches",
                      "-F", f"files=@{bdir}/apps.csv", "-F", f"files=@{bdir}/cust.csv", "-F", f"files=@{bdir}/pays.csv", "-F", "created_by=Ana"],
                     capture_output=True, text=True).stdout
bid = json.loads(out)["id"]
for _ in range(200):
    s, b = call("GET", f"/api/registry/batches/{bid}")
    if b["status"] not in ("queued", "running", "pending"):
        break
    time.sleep(2)
import io
scored = pd.read_csv(io.StringIO(subprocess.run(["curl", "-s", "-H", "X-Ensemble-Actor:Ana", f"http://{api}/api/registry/batches/{bid}/download"],
                                                capture_output=True, text=True).stdout))
p_on = np.array([r["1"] for r in sc["probabilities"]])
print("   batch", bid, b["status"], "rows", len(scored), "| online vs batch: predictions equal",
      bool((np.array(sc["predictions"]) == scored["prediction"].to_numpy()).all()),
      "| max |Δp| %.2e (batch rounds to 6 dp)" % float(np.max(np.abs(p_on - scored["p_1"].to_numpy()))))
print("## 4. the same with cust \"pinned\" (read from the model's pinned version, re-hashed)")
s, sp = call("POST", f"/api/registry/versions/{mv}/score", {"rows": apps_json, "tables": {"cust": "pinned", "pays": recs(pays)}})
p_pin = np.array([r["1"] for r in sp["probabilities"]])
print(s, "source", sp.get("join", {}).get("tables", {}).get("cust"), "| max |Δp| vs rows-sent %.2e" % float(np.max(np.abs(p_pin - p_on))))
print("## 5. /predict, 5 rows one at a time (their pays only) = /score")
for i in (0, 1, 2, 50, 299):
    a = apps_json[i]
    s, pr = call("POST", f"/api/registry/versions/{mv}/predict",
                 {"row": a, "explain": i == 0, "tables": {"cust": "pinned", "pays": recs(pays[pays.app_id == a["app_id"]])}})
    print("  ", s, a["app_id"], "pays", int((pays.app_id == a["app_id"]).sum()), "p1 %.9f" % pr["probabilities"]["1"], "| /score %.9f" % p_pin[i],
          "| Δ %.1e" % abs(pr["probabilities"]["1"] - p_pin[i]), "| frame", {k: pr["frame_row"].get(k) for k in ("x__score", "p__n", "p__amt90")},
          ("| shap reasons %d, explained p %s" % (len(pr.get("shap", {}).get("reasons", [])), pr.get("explained_prediction"))) if i == 0 else "")
print("## 6. training/serving parity on rows: the version's holdout rows scored online from the pinned tables")
import os
sys.path.insert(0, "/home/user/demo/backend")
from app import frames
from app.db import Database  # noqa: F401
s, v = call("GET", f"/api/registry/versions/{mv}")
s, card = call("GET", f"/api/registry/versions/{mv}/card")
tabs = {t["name"]: t["dv"] for t in card["data"]["inputs"]["tables"]}
hp = pd.read_csv(vdir / "holdout_predictions.csv")
g = frames.read(frames.Table(tabs["apps"], "apps"))
ids = hp["row"].to_numpy()
rows = g.loc[ids]
rows_json = recs(rows.drop(columns=["y"]).astype(object).where(rows.notna(), None).assign(applied_on=rows["applied_on"].astype(str), applied_day=rows["applied_day"].astype(str)))
pt = frames.read(frames.Table(tabs["pays"], "pays"))
pt = pt[pt["app_id"].isin(set(rows["app_id"]))]
s, so = call("POST", f"/api/registry/versions/{mv}/score", {"rows": rows_json, "tables": {"cust": "pinned", "pays": recs(pt.astype(object).where(pt.notna(), None).assign(paid_on=pt["paid_on"].astype(str).where(pt["paid_on"].notna(), None)))}})
po = np.array([r["1"] for r in so["probabilities"]])
print(s, "holdout rows", len(ids), "| online predictions = holdout predictions:", bool((np.array(so["predictions"]) == hp["y_pred"].to_numpy()).all()),
      "| max |Δp| %.2e" % float(np.max(np.abs(po - hp["p_1"].to_numpy()))), "| missing_columns", so.get("missing_columns"))
