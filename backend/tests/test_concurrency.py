"""S5 (robustness): heavy predictor work runs in worker processes, so the API keeps answering.

Kernel SHAP, permutation importance and batch scoring used to run in the API process's thread pool; their
CPU-bound Python held the GIL and stalled other requests (S4 saw a flaky end-to-end failure from it). Here SHAP for
several uncached rows, a 30-shuffle permutation importance and a batch scoring job run at once, and while they do,
/api/health and /api/runs/{id} must keep answering quickly."""

from __future__ import annotations

import io
import statistics
import threading
import time

import httpx
import numpy as np
import pandas as pd
import pytest

from .conftest import start_run, wait_for_run
from .test_understand import _imbalanced, _upload

HP = {"GBM": {"num_boost_round": 40}, "LR": {}, "RF": {"n_estimators": 300}}


@pytest.fixture(scope="module")
def heavy_run(client):
    df = _imbalanced(n=1500, seed=21)
    rng = np.random.default_rng(0)
    for i in range(8):  # 12 features: Kernel SHAP samples 2048 of 4096 coalitions per row, real work
        df[f"n{i}"] = rng.normal(size=len(df)).round(3)
    ds = _upload(client, "concurrency.csv", df, target="default")
    run = start_run(client, dataset_id=ds["id"], time_limit=60, feature_importance=False, fit_kwargs={"hyperparameters": HP})
    out = wait_for_run(client, run["id"], timeout=300)
    assert out["status"] == "succeeded", out["error"]
    return ds, out


def test_api_answers_while_shap_runs(api_server, heavy_run):
    ds, run = heavy_run
    rid = run["id"]
    holdout = httpx.get(f"{api_server.url}/api/runs/{rid}/holdout-predictions", timeout=60).json()["rows"]
    rows = holdout[:6]
    results: dict[str, tuple[int, float]] = {}
    in_flight = threading.Event()

    def call(name: str, method: str, url: str, **kw) -> None:
        t0 = time.time()
        r = httpx.request(method, f"{api_server.url}{url}", timeout=300, **kw)
        results[name] = (r.status_code, time.time() - t0, r)

    heavy = [threading.Thread(target=call, args=(f"shap{r}", "GET", f"/api/runs/{rid}/shap"), kwargs={"params": {"row": r}})
             for r in rows]
    heavy.append(threading.Thread(target=call, args=("importance", "GET", f"/api/runs/{rid}/feature-importance"),
                                  kwargs={"params": {"shuffles": 30}}))
    t_start = time.time()
    for t in heavy:
        t.start()
    in_flight.set()

    probes: list[tuple[str, float, float]] = []  # (endpoint, latency, time since start)
    while any(t.is_alive() for t in heavy):
        for url in ("/api/health", f"/api/runs/{rid}", "/api/status"):
            t0 = time.time()
            r = httpx.get(f"{api_server.url}{url}", timeout=30)
            assert r.status_code == 200, (url, r.text)
            probes.append((url, time.time() - t0, t0 - t_start))
        time.sleep(0.05)
    for t in heavy:
        t.join()
    heavy_s = max(v[1] for v in results.values())

    for name, (code, secs, r) in results.items():
        assert code == 200, (name, r.text)
    for r in rows:
        sh = results[f"shap{r}"][2].json()
        assert not sh["cached"] and abs(sh["additivity_error"]) < 1e-6
    assert results["importance"][2].json()["meta"]["num_shuffle_sets"] == 30

    lat = [p[1] for p in probes]
    health = [p[1] for p in probes if p[0] == "/api/health"]
    runget = [p[1] for p in probes if p[0].startswith("/api/runs/")]
    # The heavy work took a while, and the probes kept coming back fast the whole time.
    assert heavy_s > 1.0, f"the heavy requests finished in {heavy_s:.2f}s: too short to test anything"
    assert len(health) >= 5, probes
    assert max(health) < 0.5, f"/api/health took {max(health):.3f}s while SHAP ran"
    assert max(runget) < 1.0, f"/api/runs/{{id}} took {max(runget):.3f}s while SHAP ran"
    assert statistics.median(lat) < 0.15, f"median probe {statistics.median(lat):.3f}s"
    print(f"\nheavy work {heavy_s:.2f}s; {len(probes)} probes, median {statistics.median(lat) * 1e3:.1f} ms, "
          f"max health {max(health) * 1e3:.1f} ms, max run {max(runget) * 1e3:.1f} ms")


def test_api_answers_while_a_batch_scores(api_server, client, heavy_run):
    ds, run = heavy_run
    v = client.post(f"/api/runs/{run['id']}/register", json={"created_by": "Load Tester"}).json()
    deadline = time.time() + 240
    while (v := client.get(f"/api/registry/versions/{v['id']}").json())["status"] == "building":
        assert time.time() < deadline
        time.sleep(0.3)
    assert v["status"] == "ready", v["error"]
    df = _imbalanced(n=60_000, seed=4).drop(columns=["default"])
    rng = np.random.default_rng(0)
    for i in range(8):
        df[f"n{i}"] = rng.normal(size=len(df)).round(3)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    b = client.post(f"/api/registry/versions/{v['id']}/batches", files={"file": ("big.csv", buf.getvalue().encode(), "text/csv")},
                    data={"created_by": "Load Tester"}).json()
    lat, progress = [], []
    while True:
        t0 = time.time()
        r = httpx.get(f"{api_server.url}/api/health", timeout=30)
        lat.append(time.time() - t0)
        assert r.status_code == 200
        bb = client.get(f"/api/registry/batches/{b['id']}").json()
        progress.append(bb["progress"])
        if bb["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert bb["status"] == "succeeded", bb["error"]
    assert bb["n_rows"] == 60_000
    assert max(lat) < 0.5, f"/api/health took {max(lat):.3f}s during batch scoring"
    # progress moved through intermediate values on the way to 1
    # (rounded: the stage fractions are sums of floats, so 0.85 can arrive as 0.8500000000000001 and then 0.85)
    progress = [round(p, 9) for p in progress]
    assert progress == sorted(progress) and progress[-1] == 1.0 and any(0 < p < 1 for p in progress)
    assert np.isfinite(bb["result"]["drift"]["max_psi"]) and bb["result"]["drift"]["max_psi"] < 0.1  # same distribution


def test_pool_survives_a_crashed_worker(api_server, client, heavy_run):
    """If a worker process dies, the next heavy request gets a fresh pool instead of failing forever."""
    import os
    import signal

    import psutil

    ds, run = heavy_run
    me = psutil.Process(api_server.proc.pid)
    kids = [p for p in me.children(recursive=False) if "multiprocessing" in " ".join(p.cmdline())]
    assert kids, "no worker processes found under the API"
    os.kill(kids[0].pid, signal.SIGKILL)
    time.sleep(0.5)
    ok = False
    for _ in range(3):  # the request that meets the broken pool may fail; the next one must work
        r = client.post(f"/api/runs/{run['id']}/predict", json={"rows": [{"x1": 0.3, "x2": 0.1, "region": "north", "age": 30}]})
        if r.status_code == 200:
            ok = True
            break
    assert ok, r.text
    assert client.get("/api/health").status_code == 200
