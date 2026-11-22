"""X5 garbage-input sweep over every route of the OpenAPI schema.

    python sweep.py <base> <state.json> <openapi.json> <keys.json> <out prefix> [data dir]

Writes <out>.jsonl (every response >= 500, every hang, every 4xx body that looks like it leaks engine internals) and
<out>.summary.txt (counts). Destructive routes run last."""
import json, random, re, sys, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx

BASE, STATE, OAS, KEYS, OUT = sys.argv[1:6]
DATA = sys.argv[6] if len(sys.argv) > 6 else None
st = json.load(open(STATE))
oas = json.load(open(OAS))
keys = json.load(open(KEYS))
import os
DRY = os.environ.get("DRY")
TIMEOUT = 60
WHO = os.environ.get("WHO", "Ada")
cl = httpx.Client(timeout=TIMEOUT, limits=httpx.Limits(max_connections=8))
lock = threading.Lock()
outf = open(OUT + ".jsonl", "w")
stats = {"sent": 0, "5xx": 0, "hang": 0, "leak": 0, "err": 0, "by_status": {}}
routes_hit = set()

blob = "0" * 64
if DATA:
    b = next(Path(DATA, "lake", "blobs").rglob("*.parquet"), None)
    if b:
        blob = b.stem


def fill(path, name):
    joins = "/joins" in path or "/frames" in path
    m = {"dataset_id": st["v1"], "a": st["v1"], "b": st.get("v2") or st["v1"],
         "root_id": st["jroot"] if joins else st["root"], "version_id": st["jv"] if joins else st["v1"],
         "run_id": st["anomaly"] if path.startswith("/api/anomaly/") else st["run"],
         "vid": st["mv"], "bid": st["batch"], "team": st["cr"], "principal": "Ana", "project_id": st["pd"],
         "kind": "dataset", "node_id": st["v1"], "note_id": str(st.get("note") or 1), "tag": "t1",
         "up": st["up"], "sha256": blob, "join_id": st["join"], "frame_id": st["frame"], "hid": st.get("hold") or "lh_x",
         "key": st.get("shiftkey") or "holdout", "aid": st.get("autopilot") or "ap_x", "review_id": st.get("review") or "rv_x",
         "job_id": st.get("job") or "jb_x", "request_id": st.get("champ") or "cr_x", "pid": "pg_x", "table": "loans"}
    if name == "cid":
        return st["cert"] if "/certifications/" in path else (st.get("change") or "ch_x")
    if name == "sid":
        if path.startswith("/api/shares"):
            return st["share"]
        if path.startswith("/api/agent"):
            return st["agent"]
        return st.get("slice") or "0"
    if name == "rid":
        if "dataset-requests" in path:
            return st.get("dreq") or "dsr_x"
        if "reset-requests" in path:
            return st.get("rreq") or "arq_x"
        return "vr_x"
    if name == "name":
        return "apps_cust" if "/relations/" in path else "pd-x5"
    if name == "table" and "/tables/" in path:
        return "loans"
    return m.get(name, "x")


WRONG_KIND = {"dataset_id": "run", "run_id": "v1", "vid": "run", "bid": "mv", "team": "pd", "project_id": "cr",
              "root_id": "run", "version_id": "mv", "join_id": "frame", "frame_id": "join", "cid": "share", "sid": "cert"}
PATH_GARBAGE = ["nonexistent_zzz", "A" * 10000, "%00", "%C3%A9%F0%9F%98%80%E2%80%AE", "..%2F..%2Fetc%2Fpasswd", "..", "0", "-1",
                "18446744073709551616", "1e400", "NaN", "null", "%20", "%25", "a%2Fb", "%FF%FE", "'%20OR%201=1--", "%7B%7D"]
QUERY_GARBAGE = ["abc", str(2 ** 64), "-1", "-99999999999999999999", "NaN", "inf", "-inf", "", "x" * 10000, "\x00", "1e400",
                 "é😀‮", "[1,2]", "{\"a\":1}", "true", "null", "0x10", "' OR 1=1 --", "../../etc/passwd"]
VALS = [1, 0, -1, 2 ** 64, -(2 ** 64), 1.5, True, False, None, [], ["a"], [1, None], {}, {"a": 1}, "", "x" * 20000,
        "../../etc/passwd", "\x00", "é😀‮", "NaN", "' OR 1=1 --"]
RAWVALS = ["NaN", "Infinity", "-Infinity", "1e400", "-1e400", "123456789012345678901234567890"]
COMMON = ["by", "reason", "version_id", "ids", "dataset_id", "name", "table", "columns", "spec", "approve", "confirm",
          "run_ids", "team_id", "object_id", "limit", "label", "comment", "actor", "created_by", "project_id"]
RAW_BODIES = [("", "application/json"), ("null", "application/json"), ("[]", "application/json"), ("1", "application/json"),
              ('"s"', "application/json"), ("true", "application/json"), ("{", "application/json"),
              ('{"a":', "application/json"), ("[" * 1000 + "]" * 1000, "application/json"),
              ('{"a":' * 1000 + "1" + "}" * 1000, "application/json"), ('{"x": NaN, "y": Infinity, "z": -Infinity}', "application/json"),
              ('{"x": 1e400}', "application/json"), ("\xff\xfe\x00garbage", "application/json"), ('{"by":"Ada"}', "text/plain"),
              ("by=Ada&reason=x", "application/x-www-form-urlencoded"), ('{"by":"Ada"}', "multipart/form-data; boundary=zz"),
              ('{"by":"Ada","by":1}', "application/json"), ("[" * 100000, "application/json"),
              ('{"' + "k" * 100 + '": 1}' * 1, "application/json"), (json.dumps({f"k{i}": i for i in range(5000)}), "application/json")]
LEAK = re.compile(r"Traceback|File \"/|\.py\", line|sqlite3\.|psycopg|duckdb\.|Binder Error|Catalog Error|Parser Error|"
                  r"Conversion Error|pandas\.|pyarrow|numpy\.|KeyError|TypeError|AttributeError|IndexError|ZeroDivisionError|"
                  r"object has no attribute|unsupported operand|not subscriptable|NoneType|UndefinedColumn|syntax error at",
                  re.I)

BASES = {
    "POST /api/shares": {"object_id": st["v1"], "grantee_team": st["co"], "scope": "all", "reason": "x5 sweep", "by": "Ben"},
    "POST /api/datasets/{dataset_id}/certifications": {"level": "certified", "reason": "x5 sweep", "by": "Ana"},
    "POST /api/datasets/{root_id}/joins": {"name": "jz", "version_id": st["jv"], "spec": {"grain": "apps", "joins": [
        {"type": "n:1", "table": "cust", "on": ["cust"], "prefix": "z__"}]}, "by": "Ana"},
    "POST /api/datasets/{root_id}/versions/{version_id}/frames": {"join_id": st["join"], "by": "Ana"},
    "PUT /api/datasets/{dataset_id}/relations/{name}": {"from": {"table": "apps", "columns": ["cust"]},
                                                       "to": {"table": "cust", "columns": ["cust"]}, "cardinality": "n:1", "by": "Ana"},
    "PUT /api/datasets/{dataset_id}/tables/{table}/key": {"columns": ["app_id"], "reason": "x5", "by": "Ana"},
    "PUT /api/datasets/{dataset_id}/pii": {"confirm": "suggested", "by": "Ana"},
    "POST /api/legal-holds": {"scope_kind": "dataset", "scope_id": st["v1"], "case_ref": "C9", "reason": "x5 sweep", "by": "Ada"},
    "POST /api/teams/{team}/members": {"name": "Zed", "role": "member", "by": "Ada"},
    "POST /api/projects": {"name": "PZ", "team_id": st["cr"], "by": "Ana"},
    "POST /api/compare": {"run_ids": [st["run"], st["anomaly"]], "by": "Ada"},
    "POST /api/runs/{run_id}/predict": {"rows": [{"income": 40000, "age": 30, "segment": "sme"}], "by": "Ada"},
    "POST /api/registry/versions/{vid}/predict": {"rows": [{"income": 40000, "age": 30, "segment": "sme"}], "by": "Ada"},
    "POST /api/registry/versions/{vid}/score": {"rows": [{"income": 40000, "age": 30, "segment": "sme"}], "by": "Ada"},
    "POST /api/authz/check": {"actor": "Ana", "action": "dataset.read", "object_id": st["v1"]},
    "POST /api/datasets/{dataset_id}/rows/preview": {"filter": "income > 1", "by": "Ada"},
    "POST /api/datasets/{dataset_id}/edit": {"edits": [{"row": 0, "column": "income", "value": 1}], "reason": "x5", "by": "Ada"},
    "PATCH /api/uploads/{up}": {"target": "y", "by": "Ana"},
    "POST /api/sql/preview": {"url": "sqlite:///nonexistent.db", "sql": "select 1"},
}
HEAVY = ("POST /api/runs", "POST /api/forecast/runs", "POST /api/anomaly/runs", "POST /api/autopilot",
         "POST /api/expected-loss/runs", "POST /api/segmented/runs", "POST /api/survival/runs", "POST /api/multilabel/runs",
         "POST /api/reject-inference/runs", "POST /api/runs/{run_id}/register", "POST /api/runs/{run_id}/learning-curve",
         "POST /api/runs/{run_id}/split-twin", "POST /api/runs/{run_id}/metric-twin", "POST /api/runs/{run_id}/experiments",
         "POST /api/runs/{run_id}/review", "POST /api/runs/import")
DESTRUCTIVE = re.compile(r"^DELETE |/reset|/gc|/purge|/delete|retention/run|/cleanup|/archive|/revoke|/retire|/release|"
                         r"/cancel|/stop|/decide|/migrate|cache/clear|/restore|/sync-s3|/seed")


def record(kind, method, tmpl, variant, status, elapsed, text, url, body=None):
    rec = {"kind": kind, "method": method, "route": tmpl, "variant": variant, "status": status, "elapsed": round(elapsed, 2),
           "url": url[:600], "body": (body if body is None or len(str(body)) < 600 else str(body)[:600] + "…"),
           "resp": (text or "")[:800]}
    with lock:
        outf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        outf.flush()


def send(method, tmpl, variant, url, params=None, json_body=None, raw=None, ctype=None, files=None, data=None):
    hd = {"X-Ensemble-Actor": WHO}
    kw = {}
    if raw is not None:
        hd["content-type"] = ctype or "application/json"
        kw["content"] = raw.encode("utf-8", "surrogatepass") if isinstance(raw, str) else raw
    elif json_body is not None or (method in ("POST", "PUT", "PATCH") and files is None and data is None):
        hd["content-type"] = "application/json"
        kw["content"] = json.dumps(json_body, allow_nan=True).encode() if json_body is not None else b"{}"
    if files is not None:
        kw["files"] = files
    if data is not None:
        kw["data"] = data
    t0 = time.time()
    try:
        try:
            r = cl.request(method, BASE + url, params=params, headers=hd, **kw)
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError):
            t0 = time.time()  # a pooled connection the server closed after an earlier 500: retry once
            r = cl.request(method, BASE + url, params=params, headers=hd, **kw)
        el = time.time() - t0
        with lock:
            stats["sent"] += 1
            stats["by_status"][r.status_code] = stats["by_status"].get(r.status_code, 0) + 1
            routes_hit.add(f"{method} {tmpl}")
        txt = r.text if "application/json" in r.headers.get("content-type", "") or "text" in r.headers.get("content-type", "") else ""
        if r.status_code >= 500:
            with lock:
                stats["5xx"] += 1
            record("5xx", method, tmpl, variant, r.status_code, el, r.text[:800], str(r.request.url), json_body if raw is None else raw)
        elif r.status_code >= 400 and LEAK.search(txt or ""):
            with lock:
                stats["leak"] += 1
            record("leak", method, tmpl, variant, r.status_code, el, txt, str(r.request.url), json_body if raw is None else raw)
        if el > 20:
            record("slow", method, tmpl, variant, r.status_code, el, "", str(r.request.url), json_body if raw is None else raw)
    except httpx.TimeoutException:
        with lock:
            stats["sent"] += 1
            stats["hang"] += 1
            routes_hit.add(f"{method} {tmpl}")
        record("hang", method, tmpl, variant, None, time.time() - t0, "", BASE + url, json_body if raw is None else raw)
    except Exception as e:  # noqa: BLE001
        with lock:
            stats["sent"] += 1
            stats["err"] += 1
        record("client_err", method, tmpl, variant, None, time.time() - t0, f"{type(e).__name__}: {e}"[:300], BASE + url)


def build(path, overrides):
    def rep(m):
        n = m.group(1)
        return overrides[n] if n in overrides else urllib.parse.quote(str(fill(path, n)), safe="")
    return re.sub(r"{([^}]+)}", rep, path)


def tasks_for(method, path, op):
    tk = []
    names = re.findall(r"{([^}]+)}", path)
    qparams = [p["name"] for p in op.get("parameters", []) if p["in"] == "query"]
    rb = op.get("requestBody", {}).get("content", {})
    is_json = "application/json" in rb
    is_mp = "multipart/form-data" in rb
    key = f"{method} {path}"
    heavy = key in HEAVY
    base_q = {"by": WHO} if "by" in qparams else {}
    base_body = dict(BASES.get(key, {"by": WHO, "reason": "x5 sweep garbage probe"}))
    if heavy:
        base_body = {"by": WHO}  # never a valid heavy request: garbage on one field only
    vurl = build(path, {})
    tk.append((method, path, "valid", vurl, dict(base_q), base_body if (is_json or method in ("POST", "PUT", "PATCH")) and not is_mp else None, None, None))
    # path garbage
    for n in names:
        vals = list(PATH_GARBAGE)
        if n in WRONG_KIND and st.get(WRONG_KIND[n]):
            vals.append(st[WRONG_KIND[n]])
        for g in vals:
            tk.append((method, path, f"path:{n}={g[:30]}", build(path, {n: g}), dict(base_q),
                       base_body if is_json else None, None, None))
    # query garbage
    for q in qparams:
        for g in QUERY_GARBAGE:
            tk.append((method, path, f"query:{q}={g[:30]!r}", vurl, {**base_q, q: g}, base_body if is_json else None, None, None))
        tk.append((method, path, f"query:{q}=[array]", vurl, list(base_q.items()) + [(q, "1"), (q, "2")], base_body if is_json else None, None, None))
    if qparams:
        tk.append((method, path, "query:all=2**64", vurl, {q: str(2 ** 64) for q in qparams}, base_body if is_json else None, None, None))
        tk.append((method, path, "query:all=-1", vurl, {q: "-1" for q in qparams}, base_body if is_json else None, None, None))
    # JSON / raw bodies (also for POST routes declaring no body: they may read request.json())
    if is_json or (method in ("POST", "PUT", "PATCH") and not is_mp):
        for raw, ct in RAW_BODIES:
            tk.append((method, path, f"raw:{ct[:20]}:{raw[:30]!r}", vurl, dict(base_q), None, (raw, ct), None))
        ks = list(dict.fromkeys(keys.get(key, []) + COMMON))
        if not is_json:
            ks = []  # no declared body: raw bodies only
        if is_json:
            ref = rb["application/json"]["schema"]
            ref = ref.get("$ref") or next((x.get("$ref") for x in ref.get("anyOf", []) if x.get("$ref")), None)
            if ref:
                ks = list(dict.fromkeys(list(oas["components"]["schemas"][ref.split("/")[-1]].get("properties", {})) + ks))
        for k in ks:
            for v in VALS:
                b = dict(base_body)
                b[k] = v
                tk.append((method, path, f"body:{k}={json.dumps(v)[:30]}", vurl, dict(base_q), b, None, None))
            for rv in RAWVALS:
                bb = dict(base_body)
                bb.pop(k, None)
                raw = json.dumps(bb)[:-1] + (", " if bb else "") + json.dumps(k) + ": " + rv + "}"
                tk.append((method, path, f"rawval:{k}={rv}", vurl, dict(base_q), None, (raw, "application/json"), None))
    if is_mp or path in ("/api/uploads",) or "upload" in path or "/score" in path and method == "POST":
        fields = {}
        if is_mp:
            ref = rb["multipart/form-data"]["schema"].get("$ref")
            if ref:
                fields = oas["components"]["schemas"][ref.split("/")[-1]].get("properties", {})
        filefields = [f for f, s in fields.items() if s.get("format") == "binary" or "contentMediaType" in s
                      or (s.get("type") == "array" and (s.get("items") or {}).get("format") == "binary")] or ["file"]
        textfields = [f for f in fields if f not in filefields]
        ff = filefields[0]
        mps = [("no file", None, {"by": WHO}), ("empty file", {ff: ("x.csv", b"", "text/csv")}, {"by": WHO}),
               ("binary", {ff: ("x.csv", bytes(range(256)) * 40, "text/csv")}, {"by": WHO}),
               ("zip header xlsx", {ff: ("x.xlsx", b"PK\x03\x04" + b"\x00" * 200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}, {"by": WHO}),
               ("parquet garbage", {ff: ("x.parquet", b"PAR1" + b"\x00" * 100 + b"PAR1", "application/octet-stream")}, {"by": WHO}),
               ("latin1 csv", {ff: ("x.csv", "a,b\né,ü\n".encode("latin-1"), "text/csv")}, {"by": WHO}),
               ("header only", {ff: ("x.csv", b"a,b\n", "text/csv")}, {"by": WHO}),
               ("ragged csv", {ff: ("x.csv", b"a,b\n1,2,3,4\n\"unterminated\n", "text/csv")}, {"by": WHO}),
               ("dup header", {ff: ("x.csv", b"a,a,a\n1,2,3\n", "text/csv")}, {"by": WHO}),
               ("bad filename", {ff: ("../../" + "x" * 300 + "\x00.csv", b"a\n1\n", "text/csv")}, {"by": WHO}),
               ("no extension", {ff: ("noext", b"a\n1\n", "application/octet-stream")}, {"by": WHO}),
               ("json file", {ff: ("x.json", b"{\"a\":1}", "application/json")}, {"by": WHO}),
               ("valid csv", {ff: ("x.csv", b"income,age,segment,y\n40000,30,sme,1\n50000,40,retail,0\n", "text/csv")}, {"by": WHO})]
        for tf in textfields:
            for g in ["", "x" * 20000, "-1", str(2 ** 64), "NaN", "{\"a\":", "[1]", "\x00", "null", "true"]:
                mps.append((f"field {tf}={g[:20]!r}", {ff: ("x.csv", b"income,age\n1,2\n", "text/csv")}, {"by": WHO, tf: g}))
        for lab, files, data in mps:
            tk.append((method, path, f"mp:{lab}", vurl, dict(base_q), None, None, (files, data)))
        tk.append((method, path, "mp:raw garbage boundary", vurl, dict(base_q), None, ("--zz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n\r\na\n1\n", "multipart/form-data; boundary=zz"), None))
        tk.append((method, path, "mp:no boundary", vurl, dict(base_q), None, ("garbage", "multipart/form-data"), None))
        tk.append((method, path, "raw octet", vurl, dict(base_q), None, (b"a,b\n1,2\n", "application/octet-stream"), None))
    return tk


def run(t):
    if DRY:
        with lock:
            stats["sent"] += 1
        return
    method, path, variant, url, params, body, raw, mp = t
    if raw is not None:
        send(method, path, variant, url, params=params, raw=raw[0], ctype=raw[1])
    elif mp is not None:
        send(method, path, variant, url, params=params, files=mp[0], data=mp[1])
    else:
        send(method, path, variant, url, params=params, json_body=body)


allr = [(m.upper(), p, o) for p, ops in oas["paths"].items() for m, o in ops.items()]
if os.environ.get("ONLY"):
    allr = [r for r in allr if re.search(os.environ["ONLY"], f"{r[0]} {r[1]}")]
safe = [r for r in allr if not DESTRUCTIVE.search(f"{r[0]} {r[1]}")]
dest = [r for r in allr if DESTRUCTIVE.search(f"{r[0]} {r[1]}")]
# admin reset itself last of all
# sync-s3 without a bucket raises SystemExit and takes the server down (found in pass 1): after everything else
dest.sort(key=lambda r: ("sync-s3" in r[1], "/admin/reset" in r[1] and "requests" not in r[1], "/admin/lake/gc" in r[1]))
if os.environ.get("PHASE") == "destructive":
    safe = []
if os.environ.get("START"):
    i = next(i for i, r in enumerate(safe) if f"{r[0]} {r[1]}" == os.environ["START"])
    safe = safe[i:]
t0 = time.time()
for phase, group in (("safe", safe), ("destructive", dest)):
    if os.environ.get("PHASE") == "safe" and phase == "destructive":
        continue
    for m, p, o in group:
        tk = tasks_for(m, p, o)
        if p.startswith("/api/monitoring/stream") or p.endswith("/events"):
            tk = tk[:40]  # streams: keep few (each may hold for the timeout)
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(run, tk))
        print(f"{phase} {m} {p}: {len(tk)} req, total sent {stats['sent']} 5xx {stats['5xx']} hang {stats['hang']} "
              f"({time.time() - t0:.0f}s)", flush=True)
stats["routes_total"] = len(allr)
stats["routes_hit"] = len(routes_hit)
stats["routes_missed"] = sorted({f"{m} {p}" for m, p, _ in allr} - routes_hit)
stats["seconds"] = round(time.time() - t0)
json.dump(stats, open(OUT + ".summary.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in stats.items() if k != "routes_missed"}, default=str))
