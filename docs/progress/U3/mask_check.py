"""U3 (F-X4a hand-off) masking check on the four surfaces F-X4a left out: the forecast CSVs (future + a new-history
prediction) and the expected-loss / survival / segmented Try It and score answers. Demo samples, one column of each
tagged as PII; Ada (admin) reads masked by default, then with unmasked=1&reason=. Usage: python mask_check.py <api>"""
import io, json, sys, time
import httpx

B = sys.argv[1].rstrip("/") + "/api"; c = httpx.Client(timeout=900); H = {"X-Ensemble-Actor": "Ada"}
get = lambda u, **k: c.get(B + u, headers=H, **k)  # noqa: E731
post = lambda u, js=None, **k: c.post(B + u, json=js, headers=H, **k)  # noqa: E731


def wait(rid):
    for _ in range(400):
        r = get(f"/runs/{rid}").json()
        if r.get("status") in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(3)
    return r


def ds(name):
    return next(d for d in get("/datasets").json()["datasets"] if d["id"] == name or d.get("root_id") == name)


def tag(vid, col, mask="redact", kind="sensitive"):
    r = c.put(f"{B}/datasets/{vid}/pii", json={"columns": [{"column": col, "tag": kind, "mask": mask}], "by": "Ada"}, headers=H)
    print(f"  tag {vid}.{col} {kind}/{mask} → {r.status_code} masked {r.json().get('masked_columns')}")


def cols(vid):
    return [x["name"] for x in get(f"/datasets/{vid}").json()["profile"]["columns"]]


def register(rid, name):
    r = post(f"/runs/{rid}/register", {"created_by": "Ada", "model_name": name})
    vid = r.json().get("id") or r.json().get("version_id")
    for _ in range(200):
        v = get(f"/registry/versions/{vid}").json()
        if v.get("status") in ("ready", "failed") or v.get("ready"):
            break
        time.sleep(3)
    return vid, v.get("status")


def leak(text, raw):
    return sum(text.count(x) for x in raw)


import os
# -- 1. forecast: the item column hashed ------------------------------------------------------------------------------
if not os.environ.get("ONLY_ROWS"):  # ONLY_ROWS=1 reruns the row-model part alone
    d = ds("transaction-volume"); C = cols(d["id"]); print("## forecast", d["id"], C)
    item, ts, tgt = "item_id", "timestamp", "volume"  # the sample's own columns
    print("  mapping", item, ts, tgt)
    tag(d["id"], item, mask="hash", kind="pii_quasi")
    r = post("/forecast/runs", {"dataset_id": d["id"], "mapping": {"item_id": item, "timestamp": ts, "target": tgt}, "prediction_length": 7,
                                "time_limit": 30, "presets": "fast_training"}).json()
    fr = r.get("id") or r.get("run", {}).get("id"); print("  run", fr, wait(fr)["status"])
    raw_items = sorted({line.split(",")[0] for line in get(f"/forecast/runs/{fr}/forecast.csv").text.splitlines()[1:]})
    m = get(f"/forecast/runs/{fr}/forecast.csv")
    u = get(f"/forecast/runs/{fr}/forecast.csv", params={"unmasked": 1, "reason": "QA of the series ids", "by": "Ada"})
    print(f"  forecast.csv masked: {m.status_code} X-Ensemble-Masked-Columns={m.headers.get('x-ensemble-masked-columns')} first item {m.text.splitlines()[1].split(',')[0]!r}")
    print(f"  forecast.csv unmasked=1&reason: {u.status_code} header={u.headers.get('x-ensemble-masked-columns')} first item {u.text.splitlines()[1].split(',')[0]!r}")
    print(f"  no reason: {get(f'/forecast/runs/{fr}/forecast.csv', params={'unmasked': 1}).status_code}")
    # a new history through the saved predictor → its predictions CSV
    hist = get(f"/datasets/{d['id']}/rows", params={"limit": 100000}).json()
    rows = hist.get("rows") or []
    if rows:
        keys = [k for k in rows[0] if not k.startswith("_")]
        csv = ",".join(keys) + "\n" + "".join(",".join(str(r.get(k, "")) for k in keys) + "\n" for r in rows)
        p = c.post(f"{B}/forecast/runs/{fr}/predict", files={"file": ("hist.csv", csv.encode(), "text/csv")}, headers=H).json()
        if p.get("download"):
            pm = c.get(B.removesuffix("/api") + p["download"], headers=H)
            print(f"  predictions/{p['id']}.csv masked: {pm.status_code} header={pm.headers.get('x-ensemble-masked-columns')} first item {pm.text.splitlines()[1].split(',')[0]!r}")
        else:
            print("  predict:", str(p)[:200])

# -- 2. expected loss, survival, segmented: Try It and score answers -------------------------------------------------
cases = [("loan-losses", "/expected-loss/runs", {"time_limit": 15, "presets": "medium_quality"}, "el"),
         ("account-attrition", "/survival/runs", {"n_estimators": 50}, "survival"),
         ("segmented-loans", "/segmented/runs", {"time_limit": 10}, "segmented")]
for name, route, extra, kind in cases:
    d = ds(name); C = cols(d["id"]); print(f"## {kind}", d["id"], C)
    det = get(route.replace("/runs", "/detect"), params={"dataset_id": d["id"]}).json()
    used = set(json.dumps(det))
    r = post(route, {"dataset_id": d["id"], **extra}).json(); rid = r.get("id") or r.get("run", {}).get("id")
    run = wait(rid); print("  run", rid, run["status"])
    feats = run.get("features") or (run.get("config") or {}).get("features") or []
    col = next((x for x in feats if x in C and x not in ("segment",)), feats[0] if feats else C[1])
    tag(run.get("dataset_id") or d["id"], col)
    vid, st = register(rid, f"u3-{kind}"); print("  version", vid, st)
    # the row as stored (an audited unmasked read by the admin), so the leak count looks for the real value
    row = {k: v for k, v in (get(f"/datasets/{d['id']}/rows", params={"limit": 1, "unmasked": 1, "reason": "the check's sample row", "by": "Ada"}).json()["rows"][0]).items() if not k.startswith("_")}
    raw = [str(row[col])] if row.get(col) not in (None, "") else []
    for path, body in ((f"/registry/versions/{vid}/predict", {"row": row}), (f"/registry/versions/{vid}/score", {"rows": [row]})):
        a = c.post(B + path, json=body, headers=H)
        b = c.post(B + path, json=body, headers=H, params={"unmasked": 1, "reason": "checking the answer", "by": "Ada"})
        print(f"  {path.rsplit('/', 1)[1]:7s} masked: {a.status_code} masked_columns {a.json().get('masked_columns')} raw {col}={raw} in body: {leak(a.text, raw)}"
              f" | unmasked: {b.status_code} masked_columns {b.json().get('masked_columns')} unmasked {b.json().get('unmasked')} raw in body: {leak(b.text, raw)}")
