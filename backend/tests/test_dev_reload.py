"""`make dev` reloads the API whenever backend code changes. With ENSEMBLE_DEV_RELOAD=1 a reload must not cost a
running fit: the worker keeps fitting, the new API process adopts it, queued runs stay queued, and the event stream
is complete (replay from 0 has no gaps and one terminal event).

Here the test process plays uvicorn's reloader: it is the API process's parent, which is what workers watch."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from .conftest import TERMINAL, ApiServer, read_sse, start_run, wait_for_event, wait_for_run
from .seed_data import generate_churn, upload_frame

pytestmark = pytest.mark.slow

# Enough work on the seeded churn data to still be fitting when the API restarts (several seconds per model).
SLOW_HP = {"GBM": [{"num_boost_round": 400, "learning_rate": 0.02, "ag_args": {"name_suffix": f"_{i}"}} for i in range(4)],
           "RF": {"n_estimators": 150}, "XT": {"n_estimators": 150}}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = ApiServer(tmp_path_factory.mktemp("devreload") / "data", env={"ENSEMBLE_DEV_RELOAD": "1"})
    s.start()
    with httpx.Client(base_url=s.url, timeout=60) as c:
        s.churn_id = upload_frame(c, generate_churn(), "account_churn.csv", target="churned")["id"]
    yield s
    s.stop()


def test_reload_keeps_running_and_queued_fits(server):
    with httpx.Client(base_url=server.url, timeout=60) as c:
        run = start_run(c, dataset_id=server.churn_id, time_limit=300, feature_importance=False,
                        fit_kwargs={"hyperparameters": SLOW_HP})
        queued = start_run(c, dataset_id=server.churn_id, time_limit=60, feature_importance=False,
                           fit_kwargs={"hyperparameters": {"LR": {}}})
        wait_for_event(c, run["id"], "model_finished", timeout=240)
        rec = c.get(f"/api/runs/{run['id']}").json()
        assert rec["status"] == "running", "the fit finished before the reload; make SLOW_HP slower"
        assert c.get(f"/api/runs/{queued['id']}").json()["status"] == "queued"
        pid = int(next(e for e in c.get(f"/api/runs/{run['id']}/events", params={"format": "json"}).json()["events"]
                       if e["type"] == "run_started")["pid"])
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert b"anchor" in cmd and str(os.getpid()).encode() in cmd  # it watches the reloader, not the API

    server.restart()  # what uvicorn --reload does on a code change

    with httpx.Client(base_url=server.url, timeout=60) as c:
        assert Path(f"/proc/{pid}").exists(), "the worker died with the old API process"
        done = wait_for_run(c, run["id"], timeout=600)
        assert done["status"] == "succeeded", done["error"]
        assert done["leaderboard"] and done["holdout"]
        events = read_sse(c, f"/api/runs/{run['id']}/events")
        seqs = [int(e["id"]) for e in events]
        assert seqs == list(range(len(seqs)))
        types = [e["event"] for e in events]
        assert sum(t in TERMINAL for t in types) == 1 and types[-1] == "run_succeeded"
        assert "run_failed" not in types
        assert sum(t == "run_started" for t in types) == 1  # the same worker, not a restart of the fit

        q = wait_for_run(c, queued["id"], timeout=300)
        assert q["status"] == "succeeded", q["error"]


def test_cancel_an_adopted_worker(server):
    with httpx.Client(base_url=server.url, timeout=60) as c:
        run = start_run(c, dataset_id=server.churn_id, time_limit=300, feature_importance=False,
                        fit_kwargs={"hyperparameters": SLOW_HP})
        wait_for_event(c, run["id"], "model_started", timeout=240)
        pid = int(next(e for e in c.get(f"/api/runs/{run['id']}/events", params={"format": "json"}).json()["events"]
                       if e["type"] == "run_started")["pid"])
    server.restart()
    with httpx.Client(base_url=server.url, timeout=60) as c:
        assert c.get(f"/api/runs/{run['id']}").json()["status"] == "running"
        assert c.post(f"/api/runs/{run['id']}/cancel").status_code == 200
        done = wait_for_run(c, run["id"], timeout=60)
        assert done["status"] == "cancelled"
        types = [e["type"] for e in c.get(f"/api/runs/{run['id']}/events", params={"format": "json"}).json()["events"]]
        assert types[-1] == "run_cancelled"
    assert not Path(f"/proc/{pid}").exists() or b"app.engine.worker" not in Path(f"/proc/{pid}/cmdline").read_bytes()
