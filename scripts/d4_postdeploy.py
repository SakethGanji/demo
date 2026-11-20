#!/usr/bin/env python3
"""D4: crafted cases for the post-deployment changes, one per kind, through the API the Registry page calls.

    backend/.venv/bin/python -I scripts/d4_postdeploy.py [--api http://127.0.0.1:8000] [--only threshold,recalibrate,retrain_window,drop_feature]

One crafted credit table (8,000 applicants, 5 numbers + region, default rate ≈ 16 %, cost 5:1) trains one run; the run is
registered once per scenario (a model name each), then each version scores a later batch built to need one change:

  threshold       the default rate rises (logit + 0.9): the cost-optimal threshold moves
  recalibrate     the scores are too flat (true logit = 1.8 × the model's): the probabilities need recalibrating
  retrain_window  the effect of x2 flips sign from month 1; three labelled months, then the newest
  drop_feature    x3's feed breaks (it carries noise on another scale): drop it

Each batch gets its labels (POST /registry/batches/{id}/labels), then GET …/changes, POST …/changes {kind}, and, when
the result is better beyond noise, POST …/changes/{id}/request; the new version is pending approval (four eyes).
Results go to backend/data/realdata/d4_postdeploy.json and are printed.
"""

from __future__ import annotations

import argparse
import functools
import builtins
print = functools.partial(builtins.print, flush=True)  # noqa: A001
import io
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "realdata" / "d4_postdeploy.json"
B = np.array([-2.3, 0.9, 0.7, 0.8, -0.5, 0.3])  # intercept, x1..x5


def make(n: int, seed: int, *, shift: float = 0.0, steep: float = 1.0, flip_x2: bool = False, broken_x3: bool = False,
         start: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5))
    region = rng.choice(["north", "south", "east", "west"], n)
    b = B.copy()
    if flip_x2:
        b[2] = -0.9
    z = b[0] + X @ b[1:] + np.where(region == "south", 0.3, 0.0)
    z = steep * z + shift
    y = rng.random(n) < 1 / (1 + np.exp(-z))
    df = pd.DataFrame(X, columns=[f"x{i}" for i in range(1, 6)])
    if broken_x3:
        df["x3"] = rng.normal(5.0, 3.0, n)  # the feed now carries another field on another scale
    df.insert(0, "app_id", [f"A{start + i:06d}" for i in range(n)])
    df["region"] = region
    df["default"] = np.where(y, "yes", "no")
    return df


def csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def wait(get, ok=("succeeded", "ready", "failed", "cancelled"), key="status", timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = get()
        if r.get(key) in ok:
            return r
        time.sleep(3)
    raise TimeoutError


def batch(c: httpx.Client, vid: str, df: pd.DataFrame, name: str, date: str) -> dict:
    feats = df.drop(columns=["default"])
    for _ in range(3):  # a batch killed by an API restart is scored again
        b = c.post(f"/api/registry/versions/{vid}/batches", files={"file": (name, csv(feats), "text/csv")},
                   data={"created_by": "d4-ops", "data_date": date}).json()
        b = wait(lambda: c.get(f"/api/registry/batches/{b['id']}").json())
        if b["status"] == "succeeded":
            break
    lab = df[["app_id", "default"]].assign(label_date=date)
    r = c.post(f"/api/registry/batches/{b['id']}/labels", files={"file": (f"labels_{name}", csv(lab), "text/csv")},
               data={"created_by": "d4-ops", "id_column": "app_id", "label_column": "default"})
    r.raise_for_status()
    return b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="threshold,recalibrate,retrain_window,drop_feature")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=600)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    if not (res.get("run") and c.get(f"/api/runs/{res['run']}").status_code == 200):
        train = make(8000, 1)
        ds = c.post("/api/datasets", files={"file": ("d4_crafted_credit.csv", csv(train), "text/csv")}, data={"target": "default"}).json()["id"]
        body = {"dataset_id": ds, "label": "default", "problem_type": "binary", "presets": "medium_quality", "time_limit": 90,
                "cost": {"c_fp": 1, "c_fn": 5}, "drop_columns": ["app_id"], "name": "d4 crafted credit · cost 5:1", "source": "d4-postdeploy",
                "fit_kwargs": {"num_bag_folds": 3}}
        rid = c.post("/api/runs", json=body).json()["id"]
        rec = wait(lambda: c.get(f"/api/runs/{rid}").json())
        print("run", rid, rec["status"], flush=True)
        res = {"dataset": ds, "run": rid}
    rid = res["run"]
    scen = {
        "threshold": [("m4", make(3000, 14, shift=0.9, start=400000), "2026-04-30")],
        "recalibrate": [("m4", make(1500, 24, steep=1.8, start=500000), "2026-04-30")],
        "retrain_window": [(f"m{m}", make(600, 30 + m, flip_x2=True, start=600000 + 10000 * m), f"2026-0{m}-28") for m in (1, 2, 3)]
                          + [("m4", make(1500, 34, flip_x2=True, start=640000), "2026-04-30")],
        "drop_feature": [("m4", make(1500, 44, broken_x3=True, start=700000), "2026-04-30")],
    }
    for kind in [x.strip() for x in a.only.split(",") if x.strip()]:
        out = res.get(kind) or {}
        if not out.get("version"):
            v = c.post(f"/api/runs/{rid}/register", json={"created_by": "d4-owner", "model_name": f"d4-crafted-{kind.replace('_', '-')}"}).json()
            v = wait(lambda: c.get(f"/api/registry/versions/{v['id']}").json())
            out["version"] = v["id"]
            for name, df, date in scen[kind]:
                b = batch(c, v["id"], df, f"{kind}_{name}.csv", date)
                out.setdefault("batches", []).append(b["id"])
        vid = out["version"]
        st = c.get(f"/api/registry/versions/{vid}/changes").json()
        print(f"\n=== {kind} ({vid}) eligible {st['eligible']} · newest {st['newest_batch']} · cost {st['cost']['source']} · drifted {st['drifted'][:2]}")
        body = {"kind": kind, "actor": "d4-analyst"}
        it = c.post(f"/api/registry/versions/{vid}/changes", json=body).json()
        if it.get("run_id"):
            wait(lambda: c.get(f"/api/runs/{it['run_id']}").json())
            it = next(x for x in c.get(f"/api/registry/versions/{vid}/changes").json()["experiments"] if x["id"] == it["id"])
        r = it.get("result") or {}
        print(f"  {it.get('title')}: {r.get('metric')} {r.get('current')} → {r.get('proposed')}, Δ {r.get('delta')} {r.get('ci')} · {it.get('verdict')}")
        for k in ("operating", "threshold", "auc", "cost", "calibration"):
            if r.get(k):
                print(f"    {k}: {json.dumps(r[k])[:300]}")
        out["change"] = it
        if it.get("verdict") == "better":
            rq = c.post(f"/api/registry/versions/{vid}/changes/{it['id']}/request", json={"actor": "d4-analyst"})
            print("  request →", rq.status_code, rq.json().get("version", {}).get("id") if rq.status_code < 300 else rq.text[:200])
            if rq.status_code < 300:
                nv = rq.json()["version"]
                nv = wait(lambda: c.get(f"/api/registry/versions/{nv['id']}").json())
                out["requested_version"] = {k: nv.get(k) for k in ("id", "version", "status", "stage", "approval", "created_by")}
                out["requested_threshold"] = ((nv.get("result") or {}).get("version") or {}).get("decision_threshold")
                same = c.post(f"/api/registry/versions/{nv['id']}/review", json={"reviewer": "d4-analyst", "decision": "approve"})
                out["self_approval"] = {"status": same.status_code, "detail": same.json().get("detail")}
                print(f"  new version {nv['id']} {nv['status']} approval {nv['approval']} threshold {out['requested_threshold']}; self-approval → {same.status_code} {same.json().get('detail')}")
        live = c.get(f"/api/registry/versions/{vid}").json()
        out["live_unchanged"] = {"stage": live.get("stage"), "threshold": ((live.get("result") or {}).get("version") or {}).get("decision_threshold")}
        res[kind] = out
        OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"\nwritten {OUT}")


if __name__ == "__main__":
    main()
