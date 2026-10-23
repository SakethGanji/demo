"""Step 1 test: drive a generated app over real HTTP, the way the studio would.

    python api_test.py fixture/vendor-payments

Starts server.py as its own process, talks to it only through HTTP, restarts
it mid-way to prove data is kept. Part A works for any app; part B is the
vendor-payments user journey.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "fixture/vendor-payments")
PORT = 8111
BASE = f"http://127.0.0.1:{PORT}"
results = []


def call(method, path, body=None, user="r.alvarez@bank.example", role="member"):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "x-user": user, "x-role": role})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f'  {"✓" if ok else "✗"} {name}' + ("" if ok else f"\n      {detail}"))


def start():
    p = subprocess.Popen(["uv", "run", "--no-project", "--with", "fastapi", "--with", "uvicorn",
                          "python", "server.py", str(APP), "--port", str(PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/api/app")
            return p
        except Exception:  # noqa: BLE001
            if p.poll() is not None:
                sys.exit("server failed to start:\n" + p.stderr.read())
            time.sleep(0.2)
    sys.exit("server didn't start")


def stop(p):
    p.terminate()
    p.wait(10)


(APP / "app.db").unlink(missing_ok=True)
server = start()
try:
    # ---- A. generic: works for any generated app --------------------------
    print("A. Platform endpoints (same for every app)")
    s, app = call("GET", "/api/app")
    check("describe app", s == 200 and app["tables"] and app["actions"], app)
    for t in app["tables"]:
        s, body = call("GET", f'/api/tables/{t["name"]}')
        check(f'list {t["name"]} ({body.get("total")} sample rows)', s == 200 and body["total"] > 0, body)
        s, row = call("GET", f'/api/tables/{t["name"]}/{body["rows"][0]["id"]}')
        check(f'open a {t["name"]} row with its history', s == 200 and "_history" in row, row)
    for q in app["queries"]:
        s, body = call("GET", f"/api/queries/{q['name']}")
        check(f'saved query "{q["name"]}"', s == 200 and isinstance(body, list), body)
    s, body = call("GET", "/api/tables/nope")
    check("unknown table -> 404", s == 404, body)
    s, body = call("POST", "/api/actions/nope", {})
    check("unknown action -> 404", s == 404, body)
    s, body = call("GET", "/api/tables/invoices?q=Iron")
    check("search invoices", s == 200, body)
    s, body = call("GET", "/api/tables/invoices?sort=-amount&limit=2")
    check("sort + page", s == 200 and len(body["rows"]) <= 2
          and body["rows"][0]["amount"] >= body["rows"][-1]["amount"], body)

    # ---- B. the vendor-payments journey ------------------------------------
    print("B. User journey: vendor payments")
    acts = {a["name"]: a for a in app["actions"]}
    print("   actions:", ", ".join(f'{n}({", ".join(i["name"] for i in a["inputs"])})' for n, a in acts.items()))
    MGR = dict(user="m.chen@bank.example", role="manager")
    AP = dict(user="r.alvarez@bank.example", role="member")

    def act(name, kw, who):
        s, body = call("POST", f"/api/actions/{name}", kw, **who)
        return s, body.get("result", body)

    # generic CRUD on a reference table, by a role allowed to edit it
    vcols = {c["name"]: c for c in next(t for t in app["tables"] if t["name"] == "vendors")["columns"]}
    new_vendor = {k: ("Test Vendor Co" if k == "name" else "ap@testvendor.example" if c["type"] == "email"
                      else c["choices"][0] if c["type"] == "choice" else "x")
                  for k, c in vcols.items() if c.get("required") and not c.get("locked")}
    s, body = call("POST", "/api/tables/vendors", new_vendor, **AP)
    check("member can't add a vendor directly (403)", s == 403, (s, body))
    s, vendor = call("POST", "/api/tables/vendors", new_vendor, **MGR)
    check("manager adds a vendor directly", s == 200 and vendor.get("id"), (s, vendor))
    vid = vendor["id"]
    s, body = call("PATCH", f"/api/tables/vendors/{vid}", {"status": "blocked"}, **MGR)
    check("locked column (vendor status) can't be edited directly", s == 400 and "action" in body["detail"], (s, body))
    s, body = call("POST", "/api/tables/invoices", {"vendor_id": vid, "amount": 1}, **MGR)
    check("invoices can't be created directly (only via the action)", s == 403, (s, body))

    # the business flow through actions
    add = next(n for n in acts if "invoice" in n and n.startswith(("add", "create", "submit", "enter")))
    pay = next(n for n in acts if "pay" in n and n.startswith(("record", "add", "make", "pay")))
    approve = next(n for n in acts if n.startswith("approve"))
    inv_in = {i["name"] for i in acts[add]["inputs"]}
    kw = {"vendor_id": vid, "amount": 12400}
    kw.update({k: "TV-1001" for k in inv_in if "number" in k})
    s, inv = act(add, kw, AP)
    check(f"{add}: $12,400 -> needs approval", s == 200 and "approv" in str(inv.get("status")), (s, inv))
    iid = inv["id"]
    pay_in = [i["name"] for i in acts[pay]["inputs"]]
    pid = next(k for k in pay_in if "invoice" in k)
    how = {k: "ach" for k in pay_in if "method" in k}   # the edited app also asks how it was paid
    s, body = act(pay, {pid: iid, "amount": 5000, **how}, AP)
    check("paying before approval is refused (400)", s == 400, (s, body))
    s, body = act(approve, {next(i["name"] for i in acts[approve]["inputs"] if "id" in i["name"]): iid}, AP)
    check("member can't approve (403)", s == 403, (s, body))
    s, body = act(approve, {next(i["name"] for i in acts[approve]["inputs"] if "id" in i["name"]): iid},
                  dict(user="r.alvarez@bank.example", role="manager"))
    check("manager can't approve their own invoice (400)", s == 400, (s, body))
    s, body = act(approve, {next(i["name"] for i in acts[approve]["inputs"] if "id" in i["name"]): iid}, MGR)
    check("manager approves", s == 200, (s, body))
    s, body = act(pay, {pid: iid, "amount": 5000, **how}, AP)
    s2, row = call("GET", f"/api/tables/invoices/{iid}")
    check("partial payment of $5,000 -> partially paid", s == 200 and "partial" in str(row.get("status")), (s, body, row))
    n_before = call("GET", "/api/tables/payments")[1]["total"]
    s, body = act(pay, {pid: iid, "amount": 9000, **how}, AP)
    check("overpaying the $7,400 balance is refused (400)", s == 400, (s, body))
    s, body = call("GET", "/api/tables/payments")
    check("a refused action wrote nothing", body["total"] == n_before, body)

    # restart: data must still be there
    stop(server)
    server = start()
    print("   (server restarted)")
    s, row = call("GET", f"/api/tables/invoices/{iid}")
    check("after restart: the invoice and its state are still there",
          s == 200 and "partial" in str(row.get("status")), (s, row))
    check("after restart: its change history is kept", len(row.get("_history", [])) >= 3, row.get("_history"))
    s, body = act(pay, {pid: iid, "amount": 7400, **how}, AP)
    s2, row = call("GET", f"/api/tables/invoices/{iid}")
    check("paying the rest -> paid", s == 200 and row.get("status") == "paid", (s, body, row))
    s, mails = call("GET", "/api/outbox")
    check("submitter was emailed on approval", any(m["recipient"] == "r.alvarez@bank.example" for m in mails), mails)
    s, body = call("DELETE", f"/api/tables/vendors/{vid}", **MGR)
    check("can't delete a vendor that has invoices (400)", s == 400, (s, body))
finally:
    stop(server)

print(f"\n== {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
