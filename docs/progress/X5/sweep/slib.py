"""X5 sweep helpers (adapted from X4/x4lib.py)."""
import json, os, sys, time
import httpx
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8640"
B = BASE + "/api"
STATE = sys.argv[2] if len(sys.argv) > 2 else "state.json"
c = httpx.Client(timeout=120)
N = 300

def row(i):
    inc = 30000 + (i * 37) % 50000
    return {"app_id": f"A{i:05d}", "cust": f"C{i % 100:03d}", "applicant_name": f"Person{i:04d} Example",
            "email": f"user{i:04d}@example.com", "dob": f"{1950 + i % 50}-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "applied_on": f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "income": inc, "age": 20 + i % 50, "segment": ["retail", "sme", "corporate"][i % 3],
            "y": int(inc < 52000 and (20 + i % 50) < 50 or i % 7 == 0)}

def csv(rows):
    cols = list(rows[0])
    return (",".join(cols) + "\n" + "".join(",".join(str(r[k]) for k in cols) + "\n" for r in rows)).encode()

R = [row(i) for i in range(N)]
CUST = [{"cust": f"C{i:03d}", "score": 300 + (i * 7) % 500} for i in range(100)]

def h(who):
    return {"X-Ensemble-Actor": who} if who else {}

def post(who, url, js=None, **kw):
    return c.post(B + url, json=js, headers=h(who), **kw)
def put(who, url, js=None, **kw):
    return c.put(B + url, json=js, headers=h(who), **kw)
def patch(who, url, js=None, **kw):
    return c.patch(B + url, json=js, headers=h(who), **kw)
def get(who, url, **kw):
    return c.get(B + url, headers=h(who), **kw)

def show(n, r):
    try:
        j = r.json()
    except ValueError:
        j = r.text[:300]
    print(f"## {n} -> {r.status_code} {json.dumps(j, default=str)[:300]}")
    sys.stdout.flush()
    return j if isinstance(j, dict) else {"_": j}

def wait(url, ok=("succeeded", "failed", "cancelled", "ready", "error", "done", "completed"), key="status", who="Ada", limit=600):
    t0 = time.time(); j = {}
    while time.time() - t0 < limit:
        j = get(who, url).json()
        if j.get(key) in ok:
            return j
        time.sleep(3)
    return j

def load():
    with open(STATE) as f:
        return json.load(f)
def save(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)
