"""C2: the SQL apply (doctor_sql) gives the same table as the pandas apply. usage: c2eq.py setup | apply <tag> | compare"""
import json, sys
import numpy as np
from c import *
F = "c2eq.json"
T = [
    ("to_missing", [{"op": "to_missing", "column": "amt", "values": [-999], "flag": True}]),
    ("split_codes", [{"op": "split_codes", "column": "code", "codes": [97, 98], "fill": 0}]),
    ("add_missing_flag", [{"op": "add_missing_flag", "column": "note"}]),
    ("drop_duplicates", [{"op": "drop_duplicates"}]),
    ("drop_rows", [{"op": "drop_rows", "rows": [0, 1, 2, 2999]}]),
    ("drop_columns", [{"op": "drop_columns", "columns": ["junk"]}]),
    ("merge_classes", [{"op": "merge_classes", "column": "cat", "classes": ["a", "b"], "into": "ab"}]),
    ("rescale", [{"op": "rescale", "column": "amt", "date_column": "dt", "from": "2021-01-01", "factor": 0.01}]),
    ("relabel", [{"op": "relabel", "rows": [10, 11, 12], "labels": [1, 0, 1]}]),
    ("all", [{"op": "drop_duplicates"}, {"op": "to_missing", "column": "amt", "values": [-999]},
             {"op": "drop_rows", "rows": [5, 6]}, {"op": "merge_classes", "column": "cat", "classes": ["c"], "into": "cc"},
             {"op": "relabel", "rows": [20], "labels": [1]}, {"op": "drop_columns", "columns": ["junk"]}]),
]
if sys.argv[1] == "setup":
    rng = np.random.default_rng(3); N = 3000
    amt = rng.integers(1, 5000, N).astype(float); amt[rng.random(N) < .05] = -999
    code = rng.integers(1, 20, N); code[rng.random(N) < .05] = 97; code[rng.random(N) < .03] = 98
    cat = rng.choice(["a", "b", "c", "d"], N); dt = [f"2020-{m:02d}-15" if rng.random() < .5 else f"2021-{m:02d}-15" for m in rng.integers(1, 13, N)]
    note = np.where(rng.random(N) < .2, "", rng.choice(["x", "yy", "z"], N)); junk = rng.integers(0, 3, N)
    y = (rng.random(N) < .3).astype(int)
    rows = [f"A{i:05d},{amt[i]:.0f},{code[i]},{cat[i]},{dt[i]},{note[i]},{junk[i]},{y[i]}" for i in range(N)]
    for i in range(0, 60, 3): rows[i + 1] = rows[i].replace(f"A{i:05d}", f"A{i + 1:05d}")  # 20 duplicate pairs (content)
    body = ("acct,amt,code,cat,dt,note,junk,y\n" + "\n".join(rows) + "\n").encode()
    u = upload(body, "c2eq.csv"); c("PATCH", f"/api/uploads/{u}", {"target": "y"}, quiet=True)
    st, ck = c("POST", f"/api/uploads/{u}/check", quiet=True)
    st, v = c("POST", f"/api/uploads/{u}/commit", {"by": "fx1", "check_sha256": ck["check_sha256"]}, quiet=True)
    json.dump({"v": v["version_id"]}, open(F, "w")); print("v", v["version_id"])
elif sys.argv[1] == "apply":
    d = json.load(open(F)); tag = sys.argv[2]
    for name, tr in T:
        st, r = c("POST", f"/api/datasets/{d['v']}/data-doctor/apply", {"transforms": tr, "by": "fx1"}, quiet=True)
        if st >= 300: print(tag, name, st, r); continue
        d.setdefault(tag, {})[name] = r["dataset"]["id"]
        print(f"{tag:6s} {name:18s} {st} {r['dataset']['id']} how={r.get('how', 'pandas')} rows {r['rows_before']}->{r['rows_after']}"
              f" effects={[{k: v for k, v in e.items() if k in ('cells', 'rows', 'changed', 'columns', 'added')} for e in r['effects']]}")
    json.dump(d, open(F, "w"))
elif sys.argv[1] == "compare":
    d = json.load(open(F))
    for name, _ in T:
        a, b = d["pandas"][name], d["sql"][name]
        st, x = c("GET", f"/api/datasets/{a}/diff/{b}", quiet=True)
        r = x["rows"]; sc = x["schema"]
        print(f"{name:18s} pandas {a} vs sql {b}: matched {r['matched']} changed {r['changed']} added {r['added']} removed {r['removed']}"
              f" changed_by_column {r['changed_by_column']} schema {({k: v for k, v in sc.items() if v})}")
