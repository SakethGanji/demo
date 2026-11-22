"""Replay each recorded 5xx/hang (deduplicated) one at a time, capturing the exception the API logged for it.

    python replay.py <base> <sweep.jsonl> <api.log> <out.jsonl>

Signature = (route, exception class, innermost app frame). Writes one line per replayed record and prints a table of
distinct signatures with a count and one minimal curl repro each."""
import json, re, sys, time
import httpx

BASE, INP, LOG, OUT = sys.argv[1:5]
cl = httpx.Client(timeout=90)
recs = [json.loads(line) for line in open(INP)]
recs = [r for r in recs if r["kind"] in ("5xx", "hang") and "sync-s3" not in r["route"]]
import os
from urllib.parse import urlsplit
NEWBASE = os.environ.get("NEWBASE")  # replay against another API; ids remapped via OLDSTATE/NEWSTATE
remap = {}
if NEWBASE:
    o, n = json.load(open(os.environ["OLDSTATE"])), json.load(open(os.environ["NEWSTATE"]))
    remap = {str(o[k]): str(n[k]) for k in o if k in n and o[k] and n[k] and isinstance(o[k], str) and len(str(o[k])) > 4}
for r in recs:
    u = r["url"]
    if NEWBASE:
        sp = urlsplit(u)
        u = NEWBASE + u[len(f"{sp.scheme}://{sp.netloc}"):]
        for a, b in remap.items():
            u = u.replace(a, b)
        if isinstance(r["body"], (dict, str)):
            bs = json.dumps(r["body"]) if isinstance(r["body"], dict) else r["body"]
            for a, b in remap.items():
                bs = bs.replace(a, b)
            r["body"] = json.loads(bs) if isinstance(r["body"], dict) else bs
    r["url"] = u
# dedupe: route + variant family (e.g. body:by=<anything> → body:by)
seen = {}
for r in recs:
    v = r["variant"]
    if v.startswith("rawval:") and not v.endswith("7890"):
        fam = "rawval:NaN/inf"
    elif v.startswith("raw:"):
        fam = v[:60]
    else:
        fam = ":".join(re.split(r"[=:]", v, maxsplit=2)[:2])
    seen.setdefault((r["method"], r["route"], fam, type(r["body"]).__name__), []).append(r)


def logsize():
    with open(LOG, "rb") as f:
        f.seek(0, 2)
        return f.tell()


def logtail(off):
    with open(LOG, "rb") as f:
        f.seek(off)
        return f.read().decode("utf-8", "replace")


def exc_of(txt):
    lines = txt.splitlines()
    exc = next((ln for ln in reversed(lines) if re.match(r"^[A-Za-z_.]+(Error|Exception|Exit|Interrupt|Warning)\b.*", ln)
                or re.match(r"^[a-z_.]+\.[A-Z]\w+:", ln)), "")
    frames = [ln.strip() for ln in lines if ln.strip().startswith("File ") and "/backend/app/" in ln]
    return exc[:300], (frames[-1] if frames else "")[:300]


out = open(OUT, "w")
sigs = {}
for (m, route, fam, _), group in seen.items():
    r = group[0]
    off = logsize()
    body = r["body"]
    hd = {"X-Ensemble-Actor": "Ada"}
    kw = {}
    if isinstance(body, str):
        hd["content-type"] = "application/json"
        kw["content"] = body.encode("utf-8", "surrogatepass")
    elif body is not None:
        hd["content-type"] = "application/json"
        kw["content"] = json.dumps(body, allow_nan=True).encode()
    try:
        t0 = time.time()
        x = cl.request(m, r["url"], headers=hd, **kw)
        st, el = x.status_code, time.time() - t0
    except Exception as e:  # noqa: BLE001
        st, el = f"ERR {type(e).__name__}", time.time() - t0
    time.sleep(0.3)
    exc, frame = exc_of(logtail(off))
    rec = {"method": m, "route": route, "family": fam, "n": len(group), "orig_status": r["status"], "replay_status": st,
           "elapsed": round(el, 1), "exc": exc, "frame": frame, "url": r["url"], "body": body,
           "variants": sorted({g["variant"] for g in group})[:12]}
    out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    out.flush()
    key = (m, route, exc.split(":")[0], frame)
    sigs.setdefault(key, []).append(rec)

print(f"# {len(recs)} 5xx/hang records → {len(seen)} families → {len(sigs)} signatures")
for (m, route, exc, frame), rs in sorted(sigs.items()):
    n = sum(x["n"] for x in rs)
    print(f"\n## {m} {route} | {n} responses | {exc or '(no log exception)'} | {frame}")
    print(f"   families: {[x['family'] for x in rs][:10]}")
    print(f"   replay: {rs[0]['replay_status']} exc: {rs[0]['exc'][:200]}")
    print(f"   url: {rs[0]['url'][:300]}  body: {json.dumps(rs[0]['body'], default=str)[:200]}")
