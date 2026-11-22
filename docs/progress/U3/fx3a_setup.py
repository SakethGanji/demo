"""U3 (F-X3a hand-off) walk set-up by API: Credit Risk (Ana maintainer, Ben owner), a dataset `book` with two tables
(apps 200 rows, customers 20 rows with a DATE column `since`), the key customers(cust_id) and the relation
apps.cust_id → customers.cust_id; and the three change sets the walk uploads. Usage: python fx3a_setup.py <api> <files> <state>"""
import json
import sys
from pathlib import Path

import httpx

B = sys.argv[1].rstrip("/") + "/api"
F = Path(sys.argv[2]); F.mkdir(parents=True, exist_ok=True)
c = httpx.Client(timeout=600)
h = lambda w: {"X-Ensemble-Actor": w}  # noqa: E731
cr = c.post(B + "/teams", json={"name": "Credit Risk", "by": "Ada"}, headers=h("Ada")).json()["id"]
for who, role in (("Ben", "owner"), ("Ana", "maintainer")):
    c.post(f"{B}/teams/{cr}/members", json={"name": who, "role": role, "by": "Ada"}, headers=h("Ada"))
pd = c.post(B + "/projects", json={"name": "PD 2026", "team_id": cr, "by": "Ana"}, headers=h("Ana")).json()["id"]
apps = "app_id,cust_id,amount,target\n" + "".join(f"A{i:04d},C{i % 20:03d},{1000 + i},{i % 2}\n" for i in range(200))
cust = "cust_id,region,score,since\n" + "".join(f"C{i:03d},{'NS'[i % 2]},{500 + i},2020-{1 + i % 12:02d}-{13 + i % 15}\n" for i in range(20))
u = c.post(B + "/uploads", params={"project_id": pd}, files=[("file", ("apps.csv", apps.encode(), "text/csv")), ("file", ("customers.csv", cust.encode(), "text/csv"))], headers=h("Ana")).json()["upload_id"]
c.post(f"{B}/uploads/{u}/check", headers=h("Ana"))
r = c.post(f"{B}/uploads/{u}/commit", json={"by": "Ana", "reason": "book v1"}, headers=h("Ana")).json()
root = v = r["version_id"]
k = c.put(f"{B}/datasets/{v}/tables/customers/key", json={"columns": ["cust_id"], "by": "Ana"}, headers=h("Ana")).json(); v = k["version_id"]
rel = c.put(f"{B}/datasets/{v}/relations/app_cust", json={"from": {"table": "apps", "columns": ["cust_id"]}, "to": {"table": "customers", "columns": ["cust_id"]},
                                                         "cardinality": "n:1", "by": "Ana"}, headers=h("Ana")).json(); v = rel["version_id"]
print("book", root, "→ key", k["version_id"], "→ relation", v)
(F / "customers_lossy.csv").write_text("cust_id,region,score,since\nC005,S,505.5,2020-06-18\nC006,N,506.25,2020-07-19\n")
(F / "customers_dates.csv").write_text("cust_id,region,score,since\nC001,S,900,03/02/2021\nC002,N,901,04/05/2021\nC003,S,902,11/12/2021\n")
(F / "customers_widerkey.csv").write_text("cust_id,region,score,since\nC001,N,999,2021-01-01\nC002,S,998,2021-01-02\n")
st = {"cr": cr, "pd": pd, "root": root, "head": v}
Path(sys.argv[3]).write_text(json.dumps(st)); print(json.dumps(st))
