"""X4 helpers: HTTP calls as a named person, the PII leak counter, and the shared state file.

People (set up by setup.py): Ada, Adam (platform admins, ENSEMBLE_ADMINS=Ada,Adam);
Credit Risk (cr): Ben, Bo (owners), Ana (maintainer), Mo (member), Vic (viewer), Rita (risk reviewer);
Collections (co): Cole (owner), Carl (member); Fraud (fr): Fay (member), Fox (owner).
Data: `loans` (600 rows; PII: applicant_name, ssn, email, dob)."""
import json
import os
import re
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
B = BASE + "/api"
STATE = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("X4_STATE", "state.json")
c = httpx.Client(timeout=900)
N = 600


def row(i):
    inc = 30000 + (i * 37) % 50000
    return {"app_id": f"A{i:05d}", "applicant_name": f"Person{i:04d} Example", "ssn": 123456789 + (i * 2654435761) % 800000000,
            "email": f"user{i:04d}@example.com", "dob": f"{1950 + i % 50}-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "income": inc, "age": 20 + i % 50, "segment": ["retail", "sme", "corporate"][i % 3],
            "y": int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0)}


def csv(rows):
    cols = list(rows[0])
    return (",".join(cols) + "\n" + "".join(",".join(str(r[k]) for k in cols) + "\n" for r in rows)).encode()


R = [row(i) for i in range(N)]
RAW = {str(r["ssn"]) for r in R} | {f"{r['ssn']:,}" for r in R} | {r["applicant_name"] for r in R} | {r["email"] for r in R} \
    | {r["dob"] for r in R}
_rx = re.compile(r"(?<![\d.])(?:" + "|".join(re.escape(v) for v in sorted(RAW, key=len, reverse=True)) + r")(?!\d)")


def leaks(text):
    return _rx.findall(text or "")


def h(who, extra=None):
    d = {"X-Ensemble-Actor": who} if who else {}
    d.update(extra or {})
    return d


def leak(r):
    try:
        t = r.text
    except Exception:  # noqa: BLE001 - binary
        t = r.content.decode("latin-1")
    return len(leaks(t))


def show(n, r, keys=None, cut=400):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:300]
    hdr = f" | {r.headers['x-ensemble-authz']}" if "x-ensemble-authz" in r.headers else ""
    shown = {k: j.get(k) for k in keys} if keys and isinstance(j, dict) else j
    print(f"## {n} → HTTP {r.status_code}{hdr} | pii {leak(r)}")
    if cut:
        print("   ", json.dumps(shown, default=str, ensure_ascii=False)[:cut])
    sys.stdout.flush()
    return j


def post(who, url, js=None, **kw):
    hd = kw.pop("headers", None)
    return c.post(B + url, json=js, headers=h(who, hd), **kw)


def put(who, url, js=None, **kw):
    return c.put(B + url, json=js, headers=h(who), **kw)


def patch(who, url, js=None, **kw):
    return c.patch(B + url, json=js, headers=h(who), **kw)


def get(who, url, **kw):
    hd = kw.pop("headers", None)
    return c.get(B + url, headers=h(who, hd), **kw)


def wait(url, ok=("succeeded", "failed", "cancelled", "ready", "error"), key="status", who="Ana", limit=900):
    t0 = time.time()
    j = {}
    while time.time() - t0 < limit:
        j = get(who, url).json()
        if j.get(key) in ok:
            return j
        time.sleep(3)
    return j


def audit_rows(team, action=None, who="Ada", limit=500):
    p = {"limit": limit}
    if action:
        p["action"] = action
    return get(who, f"/teams/{team}/audit", params=p).json().get("entries") or []


def load():
    with open(STATE) as f:
        return json.load(f)


def save(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)
