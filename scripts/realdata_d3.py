#!/usr/bin/env python3
"""D3 on real data: the Model Doctor's headline, What's missing and How to improve on the four public datasets, the bank
leakage ablation, and a hidden-feature test (Taiwan without PAY_0).

    backend/.venv/bin/python -I scripts/realdata_d3.py [--api http://127.0.0.1:8000] [--only bank,taiwan,taiwan_cost,german_cost,banking77,taiwan_nopay0,featurelab]

Downloads and converts with scripts/realdata_d1.py (cached in backend/data/realdata/, not committed). Each run: AutoGluon
medium_quality, 5-fold bagging, one stack level, permutation importance on (the Doctor reads it). Bank keeps `duration`
(no decision-time answer), so the leak is in; its ablation is the Doctor's own prescription. Results go to
backend/data/realdata/d3_results.json and are printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realdata_d1 as rd  # noqa: E402

RUNS = {  # name → (source, target, time_limit, extra body)
    "bank": ("bank", 600, {}),
    "taiwan": ("taiwan", 600, {"drop_columns": ["ID"]}),
    "taiwan_cost": ("taiwan", 600, {"drop_columns": ["ID"], "cost": {"c_fp": 1, "c_fn": 5}, "eval_metric": None}),
    "german_cost": ("german", 300, {"cost": {"c_fp": 1, "c_fn": 5}, "eval_metric": None}),
    "banking77": ("banking77", 600, {}),
    "taiwan_nopay0": ("taiwan", 600, {"drop_columns": ["ID", "PAY_0"]}),
}
OUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "realdata" / "d3_results.json"


def wait(c: httpx.Client, rid: str, timeout: float = 5000) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(5)
    raise TimeoutError(rid)


def summary(d: dict) -> dict:
    m = d.get("missing_info") or {}
    p = d.get("primary") or {}
    return {"headline": d["headline"], "primary": p.get("id"), "severity": p.get("severity"),
            "secondary": [f"{c['id']}:{c['severity']}" for c in d["secondary"]],
            "rx": (p.get("prescription") or {}).get("title"),
            "irreducible": m.get("irreducible"), "caveat": m.get("caveat"),
            "gaps": [{"rank": g["rank"], "title": g["title"], "gain": g["gain"], "finding": g["finding"], "request": g["request"]} for g in m.get("gaps") or []],
            "levers": [{"rank": x["rank"], "title": x["title"], "delta": x["delta"], "ci": x["ci"], "metric": x["metric"], "source": x["source"]} for x in d.get("improve") or []],
            "ceiling": next((c for c in d["checks"] if c["id"] == "ceiling"), {}).get("reason"),
            "errors": m.get("errors"), "seconds": m.get("seconds")}


def show(name: str, rid: str, s: dict) -> None:
    print(f"\n=== {name} ({rid}) ===\n{s['headline'][:600]}\n  secondary {s['secondary']} · rx {s['rx']}")
    if s.get("irreducible"):
        ir = s["irreducible"]
        print(f"  irreducible [{ir['lower']:.3f}, {ir['r_nn']:.3f}] vs model error {ir['model_error']:.3f}")
    for g in s["gaps"]:
        gn = g["gain"]
        print(f"  gap {g['rank']}. {g['title']}: " + (f"{gn['delta']:+.4f} {gn.get('metric') or ''} {gn.get('ci')}" if gn else "not measured"))
    for x in s["levers"]:
        print(f"  lever {x['rank']}. {x['title']}: {x['delta']} {x['metric'] or ''} · {x['source'][:90]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="bank,taiwan,taiwan_cost,german_cost,banking77,taiwan_nopay0,featurelab")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=1800)
    files = rd.prepare()
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    dsids: dict[str, str] = {}
    for name in [x.strip() for x in a.only.split(",") if x.strip()]:
        if name == "featurelab":
            ds = dsids.get("taiwan") or (res.get("taiwan") or {}).get("dataset")
            j = c.post(f"/api/datasets/{ds}/feature-lab", json={"max_candidates": 20, "time_limit": 600}).json()
            while (g := c.get(f"/api/datasets/{ds}/feature-lab/{j['id']}").json())["status"] == "running":
                time.sleep(3)
            r = g.get("result") or {}
            res["featurelab"] = {"job": g["id"], "kept": len(r.get("kept") or []), "tried": len(r.get("journal") or [])}
            d = c.get(f"/api/runs/{res['taiwan']['run']}/doctor").json()
            res["taiwan"]["after_featurelab"] = summary(d)
            show("taiwan after Feature Lab", res["taiwan"]["run"], res["taiwan"]["after_featurelab"])
            OUT.write_text(json.dumps(res, indent=1, default=str))
            continue
        src, tl, extra = RUNS[name]
        path, target = files[src], rd.OUT[src][1]
        if src not in dsids:
            dsids[src] = c.post("/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")}, data={"target": target}).json()["id"]
        binary = src != "banking77"
        body = {"dataset_id": dsids[src], "label": target, "presets": "medium_quality", "time_limit": tl,
                "eval_metric": "roc_auc" if binary else None, "problem_type": "binary" if binary else "multiclass",
                "fit_kwargs": {"num_bag_folds": 5, "num_stack_levels": 1, "dynamic_stacking": False},
                "source": "realdata-d3", "name": f"{name} · D3", **extra}
        t0 = time.time()
        rid = c.post("/api/runs", json=body).json()["id"]
        rec = wait(c, rid)
        out: dict = {"dataset": dsids[src], "run": rid, "status": rec["status"], "seconds": round(time.time() - t0)}
        if rec["status"] != "succeeded":
            out["error"] = rec.get("error")
            res[name] = out
            print(name, rec["status"], rec.get("error"))
            continue
        d = c.get(f"/api/runs/{rid}/doctor").json()
        out["doctor"] = summary(d)
        show(name, rid, out["doctor"])
        if name == "bank" and d["primary"] and d["primary"]["id"] == "leakage":
            rx = d["primary"]["prescription"]
            ex = c.post(rx["endpoint"], json=rx["body"]).json()
            child = wait(c, ex["id"])
            out["ablation_run"] = ex["id"]
            d2 = c.get(f"/api/runs/{rid}/doctor").json()
            out["after_ablation"] = summary(d2)
            out["ablation"] = d2["primary"]["evidence"].get("ablation")
            print(f"  ablation {ex['id']} {child['status']}: {out['ablation']}")
            dc = c.get(f"/api/runs/{ex['id']}/doctor").json()
            out["ablated_doctor"] = summary(dc)
            show("bank without duration", ex["id"], out["ablated_doctor"])
        res[name] = out
        OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"\nwritten {OUT}")


if __name__ == "__main__":
    main()
