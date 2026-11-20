#!/usr/bin/env python3
"""D4 on real data: the review loop on the four public datasets, called the way the UI calls it.

    backend/.venv/bin/python -I scripts/realdata_d4.py [--api http://127.0.0.1:8000] [--phase train,review] [--only bank,taiwan,german_cost,banking77]
        [--offline]

train   uploads each dataset (scripts/realdata_d1.py downloads and converts it) and trains the base run as D3 did
        (medium_quality, 5-fold bagging, one stack level, permutation importance); bank keeps `duration`.
review  POST /api/runs/{id}/review, then polls GET /api/runs/{id}/review/{rid} until it is done (the journal fills in
        as each experiment finishes), then GET /api/runs/{id}/doctor to read the domain gaps on What's missing.
        --offline turns the language model off for the review (PUT /api/llm/settings {enabled: false}) and back on.

Results go to backend/data/realdata/d4_results.json and are printed.
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

RUNS = {  # name → (source, time_limit, extra body)
    "bank": ("bank", 600, {}),
    "taiwan": ("taiwan", 600, {"drop_columns": ["ID"]}),
    "german_cost": ("german", 300, {"cost": {"c_fp": 1, "c_fn": 5}, "eval_metric": None}),
    "banking77": ("banking77", 600, {}),
}
OUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "realdata" / "d4_results.json"


def wait(c: httpx.Client, rid: str, timeout: float = 5000) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(5)
    raise TimeoutError(rid)


def train(c: httpx.Client, res: dict, names: list[str]) -> None:
    files = rd.prepare()
    for name in names:
        if (res.get(name) or {}).get("status") == "succeeded":
            continue
        src, tl, extra = RUNS[name]
        path, target = files[src], rd.OUT[src][1]
        ds = c.post("/api/datasets", files={"file": (path.name, path.read_bytes(), "text/csv")}, data={"target": target}).json()["id"]
        binary = src != "banking77"
        body = {"dataset_id": ds, "label": target, "presets": "medium_quality", "time_limit": tl,
                "eval_metric": "roc_auc" if binary else None, "problem_type": "binary" if binary else "multiclass",
                "fit_kwargs": {"num_bag_folds": 5, "num_stack_levels": 1, "dynamic_stacking": False},
                "source": "realdata-d4", "name": f"{name} · D4", **extra}
        t0 = time.time()
        rid = c.post("/api/runs", json=body).json()["id"]
        print(f"{name}: training {rid}", flush=True)
        rec = wait(c, rid)
        res[name] = {"dataset": ds, "run": rid, "status": rec["status"], "seconds": round(time.time() - t0), "error": rec.get("error")}
        print(f"{name}: {rec['status']} in {res[name]['seconds']} s", flush=True)
        OUT.write_text(json.dumps(res, indent=1, default=str))


def show_review(name: str, r: dict) -> None:
    print(f"\n=== review {name} ({r['id']}, {r['status']}, decider {r.get('decider')}) ===")
    for e in r.get("journal") or []:
        res = e.get("result") or {}
        d = f"Δ {res['delta']:+.4f} [{res['ci'][0]:+.4f}, {res['ci'][1]:+.4f}] {res['metric']}" if res.get("delta") is not None else (e.get("error") or e.get("status"))
        print(f"  {e['step']}. [{e['verdict'] or e['status']}] {e['hypothesis']['title']} → {e['experiment']['kind']} {d}")
    p = r.get("plan") or {}
    print("  plan:", p.get("summary"))
    for a in p.get("next") or []:
        print(f"   - [{a['kind']}] {a['title']}: {a['why'][:160]}")
    for g in p.get("domain_gaps") or []:
        print(f"   ? {g['title']}: {g['why'][:140]}")
    if r.get("guard"):
        print("  guard:", r["guard"])


def review(c: httpx.Client, res: dict, names: list[str], offline: bool, max_experiments: int) -> None:
    if offline:
        c.put("/api/llm/settings", json={"enabled": False})
    try:
        for name in names:
            rid = res[name]["run"]
            t0 = time.time()
            r = c.post(f"/api/runs/{rid}/review", json={"by": "realdata-d4", "max_experiments": max_experiments}).json()
            if "id" not in r:
                print(name, r)
                continue
            while (g := c.get(f"/api/runs/{rid}/review/{r['id']}").json())["status"] in ("queued", "running"):
                time.sleep(10)
            key = "review_offline" if offline else "review"
            res[name][key] = {**g, "wall_seconds": round(time.time() - t0)}
            show_review(name, g)
            d = c.get(f"/api/runs/{rid}/doctor").json()
            res[name][key + "_domain_gaps"] = ((d.get("missing_info") or {}).get("domain_gaps"))
            OUT.write_text(json.dumps(res, indent=1, default=str))
    finally:
        if offline:
            c.put("/api/llm/settings", json={"enabled": None})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--phase", default="train,review")
    ap.add_argument("--only", default="bank,taiwan,german_cost,banking77")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--max-experiments", type=int, default=5)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=1800)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    names = [x.strip() for x in a.only.split(",") if x.strip()]
    phases = a.phase.split(",")
    if "train" in phases:
        train(c, res, names)
    if "review" in phases:
        review(c, res, names, a.offline, a.max_experiments)
    print(f"\nwritten {OUT}")


if __name__ == "__main__":
    main()
