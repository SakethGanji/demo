"""X5/x3 C1 extras: the original X3 short-form repro (a grain row, no tables), the caller sending the joined feature
columns themselves, and a row that tries to override a joined feature next to the pinned tables. Usage: c1_extra.py <port> <model version>"""
import json, sys, requests
A = f"http://localhost:{sys.argv[1]}/api"; MV = sys.argv[2]; H = {"X-Ensemble-Actor": "Ana"}
def p(label, path, body):
    r = requests.post(A + path, headers=H, json=body, timeout=600)
    d = r.json(); d = d.get("detail", d) if isinstance(d, dict) else d
    keep = {k: d.get(k) for k in ("code", "tables", "missing_columns", "prediction", "probabilities", "n_rows", "predictions", "frame_row")} if isinstance(d, dict) else d
    print(f"{label}: POST {path} {json.dumps(body)[:200]} → {r.status_code} {json.dumps(keep, default=str)[:400]}")
row = {"app_id": "A00001", "cust": "C0638", "applied_on": "2024-12-10", "x1": 0.3}
p("X3 C1 /predict (grain row only)", f"/registry/versions/{MV}/predict", {"row": row})
p("X3 C1 /score (grain row only)", f"/registry/versions/{MV}/score", {"rows": [row]})
fake = {**row, "x__score": 999, "p__n": 0, "p__amt90": 0.0, "applied_day": "2024-12-10 00:00:00"}
p("caller sends the joined features, no tables", f"/registry/versions/{MV}/predict", {"row": fake})
p("caller sends the joined features, no tables (/score)", f"/registry/versions/{MV}/score", {"rows": [fake]})
p("pinned tables + caller override of x__score/p__n", f"/registry/versions/{MV}/predict", {"row": fake, "tables": {"cust": "pinned", "pays": "pinned"}, "explain": False})
p("pinned tables, no override", f"/registry/versions/{MV}/predict", {"row": row, "tables": {"cust": "pinned", "pays": "pinned"}, "explain": False})
p("tables not a dict", f"/registry/versions/{MV}/predict", {"row": row, "tables": "pinned"})
p("table rows not a list", f"/registry/versions/{MV}/score", {"rows": [row], "tables": {"cust": 5, "pays": []}})
p("unknown alias", f"/registry/versions/{MV}/score", {"rows": [row], "tables": {"cust": "pinned", "pays": [], "nope": []}})
p("rows empty", f"/registry/versions/{MV}/score", {"rows": [], "tables": {"cust": "pinned", "pays": []}})
p("rows of garbage", f"/registry/versions/{MV}/score", {"rows": [1, "x", None], "tables": {"cust": "pinned", "pays": []}})
p("row with a nested value", f"/registry/versions/{MV}/score", {"rows": [{**row, "x1": {"a": 1}}], "tables": {"cust": "pinned", "pays": []}})
