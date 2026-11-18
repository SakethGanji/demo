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
