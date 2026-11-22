"""X3 lane M helper: the UI's upload chain as plain HTTP calls (POST /uploads → PATCH plan → POST check → POST commit)."""
import json, os, sys, requests

A = os.environ.get("A", "http://localhost:8731/api")
H = {"X-Ensemble-Actor": "Ana"}


def j(r):
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:400]


def upload(files, parent=None, plan=None, **form):
    """files: [(name, bytes|path)]. Returns upload_id (and status)."""
    fs = []
    for n, b in files:
        if isinstance(b, str) and os.path.exists(b):
            b = open(b, "rb").read()
        fs.append(("file", (n, b)))
    q = {"parent_version_id": parent} if parent else {}
    r = requests.post(f"{A}/uploads", headers=H, params=q, files=fs, data=form)
    s, d = j(r)
    if s >= 300:
        return None, (s, d)
    up = d["upload_id"]
    if plan is not None:
        s, d = j(requests.patch(f"{A}/uploads/{up}", headers=H, json=plan))
        if s >= 300:
            return up, (s, d)
    return up, None


def check(up):
    return j(requests.post(f"{A}/uploads/{up}/check", headers=H))


def commit(up, **body):
    body.setdefault("reason", "x3")
    return j(requests.post(f"{A}/uploads/{up}/commit", headers=H, json=body))


def chain(files, parent=None, plan=None, commit_body=None, **form):
    up, err = upload(files, parent, plan, **form)
    if err:
        return {"stage": "upload/patch", "status": err[0], "body": err[1]}
    s, c = check(up)
    out = {"check_status": s, "check": c}
    if s >= 300 or (isinstance(c, dict) and c.get("blocks")):
        out["stage"] = "check"
        return out
    s, m = commit(up, **(commit_body or {}))
    out.update(commit_status=s, commit=m, stage="commit")
    return out


def get(path, **q):
    return j(requests.get(f"{A}{path}", headers=H, params=q))


def put(path, body):
    return j(requests.put(f"{A}{path}", headers=H, json=body))


def post(path, body=None, **kw):
    return j(requests.post(f"{A}{path}", headers=H, json=body, **kw))


def rows(vid, table=None, limit=1000):
    q = {"limit": limit}
    if table:
        q["table"] = table
    return get(f"/datasets/{vid}/rows", **q)


def pp(x, n=1500):
    s = json.dumps(x, default=str)
    print(s[:n])


def grid(vid, table=None, limit=200):
    out, after = [], None
    while True:
        q = {"limit": limit, "table": table} if table else {"limit": limit}
        if after is not None:
            q["after"] = after
        s, d = get(f"/datasets/{vid}/grid", **q)
        if s != 200:
            return s, d
        rs = d.get("rows") or []
        out += rs
        if len(rs) < limit:
            return 200, out
        after = rs[-1]["_row_id"]


def version(vid):
    s, d = get(f"/datasets/{vid}")
    root = d.get("root_id") if isinstance(d, dict) else None
    return get(f"/datasets/{root or vid}/versions/{vid}")


def delta(name, csv, parent, spec=None, table="loans"):
    plan = {"table": table, "tables": {table: {"action": "delta", **(spec or {})}}}
    r = chain([(f"{table}.csv", csv.encode())], parent=parent, plan=plan)
    c = r.get("check", {})
    print(f"\n### {name}: stage={r['stage']} check={r.get('check_status')} commit={r.get('commit_status')}")
    if isinstance(c, dict) and c.get("blocks"): print("  blocks", c["blocks"])
    if isinstance(c, dict) and "detail" in c: print("  detail", c["detail"])
    for x in (c.get("tables", []) if isinstance(c, dict) else []):
        if x.get("delta"): print("  delta", {k: x["delta"].get(k) for k in ("mode","key","inserted","updated","updated_unchanged","deleted","missing","update_of_missing_key","insert_of_existing_key","rows_after")})
        print("  schema", [(s["name"], s["type"]) for s in x.get("schema", [])])
    if isinstance(c, dict): print("  warnings", [(w["code"], w.get("message","")[:110]) for w in c.get("warnings", [])])
    if r.get("commit_status") and r["commit_status"] >= 300: print("  commit body", r["commit"])
    return r.get("commit", {}).get("version_id") if r.get("commit_status") in (200, 201) else None
def show(V, accts):
    s, g = grid(V, "loans"); print("  n rows", len(g), [{k: x[k] for k in ("_row_id","acct","amt","seg","target") if k in x} for x in g if str(x.get("acct")) in accts])
