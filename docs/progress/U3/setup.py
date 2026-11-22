"""U3 walk set-up by API (what earlier walks already drove in the UI): teams and people, five versions of `loans`, the
share to Collections, the runs the purge reaches, an anomaly run for the masked page, and the change-set file the walk
uploads. Usage: python setup.py <api base> <files dir> <state.json>

People: Ada, Adam (platform admins); Credit Risk: Ben, Bo (owners), Ana (maintainer), Vic (viewer);
Collections: Cole (owner), Carl (member)."""
import json
import sys
import time
from pathlib import Path

import httpx

B = sys.argv[1].rstrip("/") + "/api"
FILES = Path(sys.argv[2])
OUT = Path(sys.argv[3])
c = httpx.Client(timeout=900)
h = lambda who: {"X-Ensemble-Actor": who}  # noqa: E731
post = lambda who, url, js=None, **kw: c.post(B + url, json=js, headers=h(who), **kw)  # noqa: E731
get = lambda who, url, **kw: c.get(B + url, headers=h(who), **kw)  # noqa: E731
N = 400


def loans(k):
    rows = ["app_id,applicant_name,ssn,dob,income,age,segment,y"]
    for i in range(N + 10 * k):
        inc = 30000 + (i * 37 + k) % 50000
        rows.append(f"A{i:05d},Person{i:04d} Example,{123456789 + (i * 2654435761) % 800000000},{1950 + i % 50}-{1 + i % 12:02d}-{1 + i % 28:02d},"
                    f"{inc},{20 + i % 50},{['retail', 'sme', 'corporate'][i % 3]},{int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0)}")
    return ("\n".join(rows) + "\n").encode()


def commit(who, data, q, reason):
    u = c.post(B + "/uploads", params=q, files=[("file", ("loans.csv", data, "text/csv"))], headers=h(who)).json()["upload_id"]
    post(who, f"/uploads/{u}/check")
    r = post(who, f"/uploads/{u}/commit", {"reason": reason, "by": who, "match": {"method": "key", "columns": ["app_id"]}})
    assert r.status_code == 201, r.text
    return r.json()["version_id"]


def wait(url, who="Ada"):
    for _ in range(300):
        st = get(who, url).json()
        if st.get("status") in ("succeeded", "failed", "cancelled"):
            return st
        time.sleep(3)
    return st


cr = post("Ada", "/teams", {"name": "Credit Risk", "by": "Ada"}).json()["id"]
for who, role in (("Ben", "owner"), ("Bo", "owner"), ("Ana", "maintainer"), ("Vic", "viewer")):
    post("Ada", f"/teams/{cr}/members", {"name": who, "role": role, "by": "Ada"})
co = post("Ada", "/teams", {"name": "Collections", "by": "Ada"}).json()["id"]
for who, role in (("Cole", "owner"), ("Carl", "member")):
    post("Ada", f"/teams/{co}/members", {"name": who, "role": role, "by": "Ada"})
pd = post("Ana", "/projects", {"name": "PD 2026", "team_id": cr, "by": "Ana"}).json()["id"]
rp = post("Cole", "/projects", {"name": "Recoveries", "team_id": co, "by": "Cole"}).json()["id"]

V = {1: commit("Ana", loans(1), {"project_id": pd}, "loans v1")}
root = V[1]
for k in range(2, 5):
    V[k] = commit("Ana", loans(k), {"parent_version_id": V[k - 1]}, f"loans v{k}")
c.put(f"{B}/datasets/{V[4]}/pii", json={"confirm": "suggested", "by": "Ana"}, headers=h("Ana"))
# the commits matched by app_id, so loans already has the key; declaring it again returns the same version (no v5)
r = c.put(f"{B}/datasets/{V[4]}/tables/loans/key", json={"columns": ["app_id"], "by": "Ana", "reason": "the application id"}, headers=h("Ana"))
assert r.status_code in (200, 201), r.text
print("versions", V, "| key app_id →", r.status_code)

sid = post("Ben", "/shares", {"object_id": root, "grantee_team": co, "scope": "all", "pii": "masked", "reason": "recoveries models", "by": "Ben"}).json()["id"]
print("share", post("Bo", f"/shares/{sid}/decide", {"approve": True, "by": "Bo"}).json()["status"])

rb = {"label": "y", "table": "loans", "time_limit": 20, "presets": "medium_quality"}
r_ana = post("Ana", "/runs", {**rb, "dataset_id": V[3], "by": "Ana"}).json()["id"]  # an old version: re-verifies as purged later
r_carl = post("Carl", "/runs", {**rb, "dataset_id": V[4], "project_id": rp, "by": "Carl"}).json()["id"]
an = post("Ana", "/anomaly/runs", {"dataset_id": V[4], "features": ["income", "age", "ssn", "segment"], "lof": False, "by": "Ana"}).json()
an_id = an.get("id") or an.get("run", {}).get("id")
for rid in (r_ana, r_carl, an_id):
    print("run", rid, wait(f"/runs/{rid}")["status"])

# the change set the walk uploads: its operation column is `change` (new / chg / del), not _op
FILES.mkdir(parents=True, exist_ok=True)
base = loans(4).decode().splitlines()
hdr, rows = base[0], base[1:]
out = [hdr + ",change"]
for i in (3, 8, 15, 22, 40, 41, 77, 90):  # 8 updates: income +1000
    f = rows[i].split(",")
    f[4] = str(int(f[4]) + 1000)
    out.append(",".join(f) + ",chg")
for i in (5, 6, 7):  # 3 deletes
    out.append(rows[i] + ",del")
for j in range(5):  # 5 new applications
    i = 9000 + j
    out.append(f"A{i:05d},Person{i:04d} Example,{200000000 + i},1980-0{1 + j}-1{j},{45000 + 500 * j},{30 + j},retail,0,new")
(FILES / "loans_changes.csv").write_text("\n".join(out) + "\n")
st = {"cr": cr, "co": co, "pd": pd, "rp": rp, "V": V, "root": root, "share": sid, "r_ana": r_ana, "r_carl": r_carl, "anomaly": an_id}
OUT.write_text(json.dumps(st, indent=1))
print(json.dumps(st))
