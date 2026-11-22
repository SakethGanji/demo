"""X5 (lane x4): the GET routes X4's sweep leaves `unfilled` (no id to put in the path), called as Fay (Fraud, no share
left), Carl (Collections, share revoked) and Vic (Credit Risk viewer). Run after X4 setup.py + governance.py (Fay's
alerts dataset with a frame joined from loans while her share was live; the share has expired).

    PYTHONPATH=…/F-X4c python unfilled.py <base> <state.json>"""
import httpx

from x4lib import B, c, csv, get, h, leak, load, post, R

st = load()
cr, run, v1, fds = st["cr"], st["run"], st["v1"], st.get("fay_ds")
print(f"# mode {st['mode']}")


def row(n, url, params=None, who=("Fay", "Carl", "Vic", "Ana")):
    out = []
    for w in who:
        r = httpx.get(B + url, params=params or {}, headers=h(w), timeout=120)
        wd = "W" if "x-ensemble-authz" in r.headers else ""
        out.append(f"{w} {r.status_code}{wd}/pii {leak(r)}")
    print(f"## {n}: " + " · ".join(out))


# Fay's frame (built from Credit Risk's loans while the share was live)
frs = get("Fay", f"/datasets/{fds}/versions/{fds}/frames").json().get("frames") or [] if fds else []
fr = frs[0]["id"] if frs else None
jn = frs[0].get("join_id") if frs else None
print("Fay's frames:", [(f["id"], f.get("join_id")) for f in frs])
if fr:
    row("1a GET /datasets/{fay ds}/frames/{fr} (after the share expired)", f"/datasets/{fds}/frames/{fr}")
    row("1b GET /datasets/{fay ds}/frames/{fr}/rows (CR columns income/segment in Fay's frame)", f"/datasets/{fds}/frames/{fr}/rows")
    row("1c GET /datasets/{CR v1}/frames/{fay fr}/rows (path root ≠ frame's root)", f"/datasets/{v1}/frames/{fr}/rows")
    row("1d GET /datasets/{fay ds}/joins/{jn}", f"/datasets/{fds}/joins/{jn}")
    row("1e GET /datasets/{CR v1}/joins/{fay jn} (mismatched root)", f"/datasets/{v1}/joins/{jn}")
# a purge request on CR (pending; no hold now? governance placed one on PD: so it may be refused)
p = post("Ana", f"/datasets/{v1}/purge", {"table": "loans", "columns": ["email"], "reason": "X5 probe of GET /purges/{id}", "by": "Ana"})
pid = (p.json() or {}).get("id") if p.status_code < 300 else None
print(f"purge request → HTTP {p.status_code} {p.text[:160]}")
if pid:
    row("2a GET /purges/{pid}", f"/purges/{pid}")
row("2b GET /purges?team=cr", "/purges", {"team": cr})
row("2c GET /legal-holds?team=cr", "/legal-holds", {"team": cr})
# shift check key
s = post("Ana", f"/runs/{run}/shift", {"by": "Ana"})
key = (s.json() or {}).get("key") if s.status_code < 300 else None
print(f"shift POST → HTTP {s.status_code} {s.text[:160]}")
if key:
    row("3a GET /runs/{run}/shift/{key}", f"/runs/{run}/shift/{key}")
    row("3b GET /runs/{run}/shift/{key}/rows", f"/runs/{run}/shift/{key}/rows")
# an upload staged by Ana in PD
u = c.post(B + "/uploads", params={"filename": "x.csv", "project_id": st["pd"]}, files={"file": ("x.csv", csv(R[:30]), "text/csv")},
           headers=h("Ana")).json().get("upload_id")
row("4a GET /uploads/{Ana's staged upload of 30 loans rows}", f"/uploads/{u}")
row("4b GET /uploads/{up}/preview?", f"/uploads/{u}/preview")
