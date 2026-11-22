"""fuzz.py <api> <work dir>: garbage into the join / frame / batch / inputs.zip routes; prints every 5xx (and a count)."""
import json, sys, io, itertools
from pathlib import Path
import requests
A, W = sys.argv[1], Path(sys.argv[2])
H = {"X-Ensemble-Actor": "Ana"}
R = (W / "v1.vid").read_text().strip(); V = (W / "vkeys.vid").read_text().strip(); JM = (W / "jm.id").read_text().strip()
M1 = (W / "m1.id").read_text().strip(); E1 = (W / "ext_root.id").read_text().strip(); EK = (W / "ext_k.id").read_text().strip()
FID = (W / "f4.id").read_text().strip()
bad = []; n = 0
def hit(method, url, **kw):
    global n; n += 1
    try:
        r = requests.request(method, A + url, headers=H, timeout=120, **kw)
    except Exception as e:
        bad.append((method, url, str(kw)[:200], "EXC " + str(e)[:200])); return
    if r.status_code >= 500:
        bad.append((method, url, str(kw)[:300], r.status_code, r.text[:300]))
G = lambda **j: {"grain": "apps", **j}
nl = {"type": "n:1", "table": "cust", "on": ["cust"]}
specs = [None, 1, "x", [], {}, {"grain": None}, {"grain": 5}, G(), G(joins=None), G(joins={}), G(joins=[None]), G(joins=[1]),
         G(joins=[{}]), G(joins=[{"type": "x"}]), G(joins=[{**nl, "type": None}]), G(joins=[{**nl, "on": None}]), G(joins=[{**nl, "on": []}]),
         G(joins=[{**nl, "on": [[]]}]), G(joins=[{**nl, "on": [["cust"]]}]), G(joins=[{**nl, "on": [["cust", "cust", "x"]]}]),
         G(joins=[{**nl, "on": [[1, 2]]}]), G(joins=[{**nl, "on": [{"a": 1}]}]), G(joins=[{**nl, "table": None}]), G(joins=[{**nl, "table": 5}]),
         G(joins=[{**nl, "table": {}}]), G(joins=[{**nl, "table": {"version_id": None, "table": "cust"}}]),
         G(joins=[{**nl, "table": {"version_id": "ds_nope", "table": "cust"}}]), G(joins=[{**nl, "table": {"version_id": EK}}]),
         G(joins=[{**nl, "table": {"version_id": EK, "table": "nope"}}]), G(joins=[{**nl, "table": {"root_id": R, "version_id": EK, "table": "cust"}}]),
         G(joins=[{**nl, "table": {"version_id": "../../etc", "table": "cust"}}]), G(joins=[{**nl, "columns": "score"}]), G(joins=[{**nl, "columns": [None]}]),
         G(joins=[{**nl, "columns": []}]), G(joins=[{**nl, "prefix": None}]), G(joins=[{**nl, "prefix": 7}]), G(joins=[{**nl, "prefix": "a\"; drop"}]),
         G(joins=[{**nl, "prefix": ""}]), G(joins=[{**nl, "prefix": "x" * 5000}]), G(joins=[nl, nl]), G(grain="cust", joins=[nl]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"]}]), G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": None}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": []}]), G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [None]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x"}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "median", "column": "amount"}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "sum"}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": None, "fn": "count"}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "column": 5}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": 5}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {"date": "paid_on", "relative_to": "applied_on", "days": "x"}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {"date": "paid_on", "relative_to": "applied_on", "days": 1e300}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {"date": "paid_on", "relative_to": "applied_on", "days": 10**12}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {"date": "paid_on", "relative_to": "applied_on", "days": 0}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "count", "window": {"date": "amount", "relative_to": "x1", "days": -5}}]}]),
         G(joins=[{"type": "aggregate", "table": "pays", "on": ["app_id"], "aggs": [{"name": "x", "fn": "min", "column": "paid_dmy"}]}]),
         G(joins=[{"type": "1:1", "table": "cust", "on": ["cust"]}]), G(joins=[{"type": "n:1", "table": "pays", "on": [["app_id", "amount"]]}]),
         G(joins=[{"type": "n:1", "table": "daily", "on": [["x1", "rate"]]}]), G(where={"x": 1}), G(where="1=1"), {"grain": "apps", "joins": [], "extra": 1}]
for i, s in enumerate(specs):
    hit("POST", f"/datasets/{R}/joins", json={"name": f"fz{i}", "version_id": V, "spec": s})
for body in [None, [], "x", {"name": None, "spec": G(joins=[nl])}, {"name": "", "spec": G(joins=[nl])}, {"name": "x" * 10000, "spec": G(joins=[nl])},
             {"name": "fzv", "version_id": 5, "spec": G(joins=[nl])}, {"name": "fzv", "version_id": "ds_nope", "spec": G(joins=[nl])},
             {"name": "fzv", "version_id": EK, "spec": G(joins=[nl])}]:
    hit("POST", f"/datasets/{R}/joins", json=body)
for root in ["ds_nope", "x" * 300, "%00", EK]:
    hit("POST", f"/datasets/{root}/joins", json={"name": "fzr", "spec": G(joins=[nl])}); hit("GET", f"/datasets/{root}/joins")
for jid in ["jn_nope", JM, "'", "x" * 500]:
    hit("GET", f"/datasets/{R}/joins/{jid}")
    for body in [None, {}, {"version_id": None}, {"version_id": 5}, {"version_id": "ds_nope"}, {"version_id": EK}, {"version_id": V}]:
        hit("POST", f"/datasets/{R}/joins/{jid}/validate", json=body)
for vid in [V, "ds_nope", EK, R]:
    for body in [None, {}, {"join_id": None}, {"join_id": 5}, {"join_id": "jn_nope"}, {"join_id": JM}, {"join_id": [JM]}, "x"]:
        hit("POST", f"/datasets/{R}/versions/{vid}/frames", json=body)
    hit("GET", f"/datasets/{R}/versions/{vid}/frames")
for fid in [FID, "jf_nope", "x" * 300]:
    for q in ["", "?verify=1", "?verify=abc", "?verify=-1"]:
        hit("GET", f"/datasets/{R}/frames/{fid}{q}"); hit("GET", f"/datasets/ds_nope/frames/{fid}{q}"); hit("GET", f"/datasets/{EK}/frames/{fid}{q}")
F = Path(sys.argv[3]) / "batch" / "clean"
ap, cu, pa = (F / "apps.csv").read_bytes(), (F / "cust.csv").read_bytes(), (F / "pays.csv").read_bytes()
cases = {
    "empty files": [("files", ("apps.csv", b"")), ("files", ("cust.csv", b"")), ("files", ("pays.csv", b""))],
    "header only grain": [("files", ("apps.csv", ap.split(b"\n")[0] + b"\n")), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "header only pays": [("files", ("apps.csv", ap)), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa.split(b"\n")[0] + b"\n"))],
    "binary": [("files", ("apps.csv", bytes(range(256)) * 10)), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "parquet garbage": [("files", ("apps.parquet", b"PAR1garbagePAR1")), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "xlsx garbage": [("files", ("book.xlsx", b"PK\x03\x04garbage"))],
    "no files": [],
    "one file field 'file'": [("file", ("apps.csv", ap))],
    "dup grain keys": [("files", ("apps.csv", ap + b"\n".join(ap.split(b"\n")[1:3]) + b"\n")), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "dup cust keys": [("files", ("apps.csv", ap)), ("files", ("cust.csv", cu + b"\n".join(cu.split(b"\n")[1:3]) + b"\n")), ("files", ("pays.csv", pa))],
    "names weird": [("files", ("../../apps.csv", ap)), ("files", ("CUST.CSV", cu)), ("files", ("pays", pa))],
    "semicolon": [("files", ("apps.csv", ap.replace(b",", b";"))), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "latin1": [("files", ("apps.csv", ap.replace(b"A0", "Ä0".encode("latin-1")))), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "all-null key": [("files", ("apps.csv", b"app_id,cust,applied_on,applied_day,x1\n,,,,\n,,,,\n")), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
    "huge dates": [("files", ("apps.csv", b"app_id,cust,applied_on,applied_day,x1\nA1,C0001,9999-99-99,0000-00-00 99:99:99,1e400\nA2,C0002,-5,abc,nan\n")), ("files", ("cust.csv", cu)), ("files", ("pays.csv", pa))],
}
for name, files in cases.items():
    for f in ({"created_by": "Ana"}, {"created_by": "Ana", "data_date": "not-a-date"}):
        hit("POST", f"/registry/versions/{M1}/batches", files=files or None, data=f)
for vid in [M1, "mv_nope", "x" * 300]:
    hit("GET", f"/registry/versions/{vid}/inputs.zip"); hit("GET", f"/registry/versions/{vid}/card")
for body in [{"row": None}, {"row": []}, {"row": {"applied_on": "garbage", "x1": "abc"}}, {"rows": [{"applied_on": 5}]}, {"rows": "x"}, {}]:
    hit("POST", f"/registry/versions/{M1}/predict", json=body); hit("POST", f"/registry/versions/{M1}/score", json=body)
print(f"{n} calls, {len(bad)} 5xx / exceptions")
for b in bad:
    print("  ", b)
