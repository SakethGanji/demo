"""Restart the API on the same data folder: nothing is lost and the samples are not regenerated."""

from __future__ import annotations

import hashlib
import shutil

import pytest

from .conftest import ApiServer
from .test_upload import make_csv

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def api_server(tmp_path_factory):
    """A server of its own, so restarting it can't disturb the other tests."""
    server = ApiServer(tmp_path_factory.mktemp("restart") / "data")
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def client(api_server):
    import httpx

    with httpx.Client(base_url=api_server.url, timeout=60) as c:
        yield c


@pytest.fixture(scope="module")
def data_dir(api_server):
    return api_server.data_dir


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_restart_keeps_datasets(api_server, client, data_dir):
    r = client.post("/api/datasets", files={"file": ("keep_me.csv", make_csv()[0], "text/csv")})
    assert r.status_code == 201
    before = client.get("/api/datasets").json()["datasets"]
    tickets_csv = data_dir / "datasets" / "support-tickets" / "data.csv"
    mtime = tickets_csv.stat().st_mtime_ns

    api_server.restart()

    after = client.get("/api/datasets").json()["datasets"]
    assert after == before  # same records, same created_at: samples were not regenerated
    assert tickets_csv.stat().st_mtime_ns == mtime
    assert client.get(f"/api/datasets/{r.json()['id']}").json() == r.json()


def test_deleted_sample_is_regenerated_identically(api_server, client, data_dir):
    d = data_dir / "datasets" / "support-tickets"
    hashes = {p.name: _sha(p) for p in d.iterdir()}
    profile = client.get("/api/datasets/support-tickets").json()["profile"]
    shutil.rmtree(d)

    api_server.restart()

    assert {p.name: _sha(p) for p in d.iterdir()} == hashes  # fixed seeds: byte-identical files
    assert client.get("/api/datasets/support-tickets").json()["profile"] == profile


# -- runs in flight when the API stops --------------------------------------------------

def _long_run(client):
    from .conftest import start_run, wait_for_event

    run = start_run(client, dataset_id="support-tickets", time_limit=900, feature_importance=False,
                    fit_kwargs={"hyperparameters": {"GBM": {"num_boost_round": 5000}}, "num_bag_folds": 5})
    pid = wait_for_event(client, run["id"], "run_started")["pid"]
    wait_for_event(client, run["id"], "model_started")
    return run, pid


def _gone(pid, timeout=15):
    import os
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # A zombie (exited, not yet reaped by its dead parent) counts as gone.
        try:
            if open(f"/proc/{pid}/stat").read().split(")")[-1].split()[0] == "Z":
                return True
        except OSError:
            return True
        time.sleep(0.2)
    return False


@pytest.mark.parametrize("how", ["killed", "stopped"])
def test_run_in_flight_is_marked_failed_on_next_start(api_server, client, how):
    from app.engine.runner import STOPPED_MSG

    from .conftest import read_sse, start_run, wait_for_run

    run, pid = _long_run(client)
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "running"

    if how == "killed":  # the API dies without cleaning up: the next start must recover
        api_server.proc.kill()
        api_server.proc.wait()
        api_server.proc = None
        api_server.start()
    else:  # a normal stop: shutdown stops the worker; the record must read failed too
        api_server.restart()

    assert _gone(pid), "the worker of the interrupted run is still alive"
    r = client.get(f"/api/runs/{run['id']}").json()
    assert r["status"] == "failed" and r["error"] == STOPPED_MSG and r["finished_at"]
    events = read_sse(client, f"/api/runs/{run['id']}/events", timeout=30)  # the stream ends, it doesn't hang
    assert events[-1]["event"] == "run_failed" and events[-1]["data"]["error"] == STOPPED_MSG
    assert sum(e["event"] in ("run_succeeded", "run_failed", "run_cancelled") for e in events) == 1
    assert [int(e["id"]) for e in events] == list(range(len(events)))

    # The runner works after recovery.
    tiny = start_run(client, dataset_id="account-churn", time_limit=60, feature_importance=False,
                     fit_kwargs={"hyperparameters": {"LR": {}}})
    assert wait_for_run(client, tiny["id"], timeout=120)["status"] == "succeeded"
