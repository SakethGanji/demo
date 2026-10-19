"""Project datasets on a real Postgres, the analytics service faked with ``httpx.MockTransport``: import (forwarding,
naming, refusals, version capture, compensation), list scoping, preview, rename, delete order, drift (``missing``,
409 profile), and the order in which the data nodes resolve a ``datasetId``."""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from src.core.config import settings
from src.engine.types import ExecutionContext, NodeDefinition, Workflow
from src.routes.datasets import router as datasets_router
from src.services import project_dataset_service as pds
from src.services.project_service import ProjectService

pytestmark = pytest.mark.anyio

ANALYTICS = "http://analytics.test"
REAL_ASYNC_CLIENT = httpx.AsyncClient


class FakeAnalytics:
    """Just enough of the analytics service: upload, versions, delete, sample and profile, every request recorded."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.datasets: dict[str, dict[str, Any]] = {}
        self.down = False
        self.fail: tuple[int, Any] | None = None  # (status, body) for the next call
        self.versions_broken = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if self.fail:
            status, body = self.fail
            self.fail = None
            return httpx.Response(status, json=body)
        path, method = request.url.path, request.method
        if method == "POST" and path == "/files/upload":
            body = request.read()
            did = str(uuid.uuid4())
            self.datasets[did] = {"body": body, "version": str(uuid.uuid4())}
            return httpx.Response(200, json={
                "dataset_id": did, "version_id": None, "status": "complete", "file_path": f"/data/{did}.parquet",
                "file_size_bytes": len(body), "row_count": 3, "column_count": 2,
                "columns": [{"name": "region", "dtype": "VARCHAR"}, {"name": "revenue", "dtype": "DOUBLE"}],
                "preview": [], "error": None, "message": "Upload and processing complete"})
        if method == "DELETE" and path.startswith("/datasets/"):
            did = path.rsplit("/", 1)[1]
            if self.datasets.pop(did, None) is None:
                return httpx.Response(404, json={"detail": f"Dataset not found: {did}"})
            return httpx.Response(200, json={"deleted": True})
        if method == "GET" and path.startswith("/datasets/") and path.endswith("/versions"):
            ds = self.datasets.get(path.split("/")[2])
            if self.versions_broken:
                return httpx.Response(500, json={"detail": "boom"})
            if ds is None:
                return httpx.Response(404, json={"detail": "Dataset not found"})
            return httpx.Response(200, json=[{"id": str(uuid.uuid4()), "version_number": 1, "status": "complete"},
                                             {"id": ds["version"], "version_number": 2, "status": "complete"}])
        if method == "POST" and path in ("/sample", "/profile") and json.loads(request.content)["dataset_id"] not in self.datasets:
            return httpx.Response(404, json={"detail": "Dataset not found"})
        if method == "POST" and path == "/sample":
            req = json.loads(request.content)
            rows = [{"region": f"r{i}", "revenue": float(i)} for i in range(req["sampling_steps"][0]["sample_size"])]
            return httpx.Response(200, json={"success": True, "original_count": 100, "sampled_count": len(rows),
                                             "columns": [], "preview": rows[:5], "data": rows})
        if method == "POST" and path == "/profile":
            req = json.loads(request.content)
            return httpx.Response(200, json={"success": True, "row_count": 3, "column_count": 2, "columns": [],
                                             "memory_usage_bytes": 10, "duplicate_row_count": 0, "echo": req})
        return httpx.Response(404, json={"detail": "Not Found"})

    def calls(self, method: str, prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.startswith(prefix)]


@pytest.fixture
def fake(monkeypatch):
    f = FakeAnalytics()
    monkeypatch.setattr(pds, "ANALYTICS_TRANSPORT", httpx.MockTransport(f))
    monkeypatch.setattr(settings, "analytics_service_url", ANALYTICS)
    return f


@pytest.fixture
async def api(pg, fake):
    app = FastAPI()
    app.include_router(datasets_router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def new_project(sf) -> str:
    async with sf() as s:
        return (await ProjectService(s).create(f"ds-{uuid.uuid4().hex[:8]}")).id


async def upload(api, project: str, file_name: str = "sales.csv", content: bytes = b"region,revenue\nnorth,1\n",
                 name: str | None = None) -> httpx.Response:
    data = {"name": name} if name is not None else None
    return await api.post(f"/api/projects/{project}/datasets", files={"file": (file_name, content, "text/csv")}, data=data)


# ── import ───────────────────────────────────────────────────────────────────────────────────


async def test_import_forwards_the_file_and_stores_the_link(pg, api, fake):
    project = await new_project(pg)
    r = await upload(api, project, content=b"region,revenue\nnorth,1\nsouth,2\n")
    assert r.status_code == 201, r.text
    item = r.json()
    assert set(item) == {"id", "dataset_id", "version_id", "name", "file_name", "format", "row_count", "column_count",
                         "size_bytes", "columns", "missing", "created_at", "updated_at"}
    assert uuid.UUID(item["id"]).version == 7 and item["dataset_id"] in fake.datasets and item["missing"] is False
    # the sync upload names no version, so the latest one is asked for and stored
    assert item["version_id"] == fake.datasets[item["dataset_id"]]["version"]
    assert (item["name"], item["file_name"], item["format"]) == ("sales", "sales.csv", "csv")
    assert (item["row_count"], item["column_count"], item["size_bytes"]) == (3, 2, len(fake.datasets[item["dataset_id"]]["body"]))
    assert item["columns"] == [{"name": "region", "type": "VARCHAR"}, {"name": "revenue", "type": "DOUBLE"}]
    sent = fake.calls("POST", "/files/upload")[0]
    assert sent.url.params["sync"] == "true"
    assert b'filename="sales.csv"' in fake.datasets[item["dataset_id"]]["body"]
    assert b"north,1\nsouth,2" in fake.datasets[item["dataset_id"]]["body"]


async def test_names_default_to_the_stem_and_clashes_get_a_suffix(pg, api, fake):
    project = await new_project(pg)
    names = [(await upload(api, project)).json()["name"] for _ in range(3)]
    assert names == ["sales", "sales (2)", "sales (3)"]
    assert (await upload(api, project, "q3.parquet", name="  Q3   revenue ")).json()["name"] == "Q3 revenue"
    assert (await upload(api, project, "other.xlsx", name="sales")).json()["name"] == "sales (4)"
    assert (await upload(api, project, "SALES.XLS")).json()["format"] == "xls"


@pytest.mark.parametrize("file_name", ["notes.txt", "data.json", "noextension"])
async def test_an_unsupported_file_is_a_400_and_never_reaches_analytics(pg, api, fake, file_name):
    project = await new_project(pg)
    r = await upload(api, project, file_name)
    assert r.status_code == 400
    assert "CSV, Parquet or Excel" in r.json()["detail"]["message"]
    assert fake.requests == []
    assert (await api.get(f"/api/projects/{project}/datasets")).json() == []


async def test_analytics_down_is_a_503_naming_the_url(pg, api, fake):
    project = await new_project(pg)
    fake.down = True
    r = await upload(api, project)
    assert r.status_code == 503
    assert r.json() == {"detail": f"The analytics service isn't reachable at {ANALYTICS}. Start it to import datasets."}
    fake.down = False
    assert (await api.get(f"/api/projects/{project}/datasets")).json() == []


async def test_an_analytics_refusal_is_a_502_with_its_message(pg, api, fake):
    project = await new_project(pg)
    fake.fail = (500, {"detail": "Processing failed: bad CSV on line 7"})
    r = await upload(api, project)
    assert r.status_code == 502
    assert "Processing failed: bad CSV on line 7" in r.json()["detail"]["message"]
    assert (await api.get(f"/api/projects/{project}/datasets")).json() == []


async def test_a_version_lookup_failure_still_imports_without_a_version(pg, api, fake):
    project = await new_project(pg)
    fake.versions_broken = True
    r = await upload(api, project)
    assert r.status_code == 201 and r.json()["version_id"] is None and r.json()["dataset_id"] in fake.datasets
    assert len(fake.calls("GET", "/datasets/")) == 1


async def test_a_failed_link_insert_deletes_the_analytics_dataset_again(pg, api, fake, monkeypatch):
    project = await new_project(pg)
    first = (await upload(api, project)).json()
    monkeypatch.setattr(pds, "project_dataset_id", lambda: first["id"])  # every insert now collides on the primary key
    r = await upload(api, project, name="second")
    assert r.status_code == 409
    orphan = [d for d in fake.datasets if d != first["dataset_id"]]
    assert orphan == []  # analytics accepted the file, then the engine took it back
    deletes = [c.url.path for c in fake.calls("DELETE", "/datasets/")]
    assert len(deletes) == 1 and deletes[0] != f"/datasets/{first['dataset_id']}"
    assert [d["name"] for d in (await api.get(f"/api/projects/{project}/datasets")).json()] == ["sales"]


async def test_any_error_after_analytics_accepted_the_file_compensates_and_re_raises(pg, fake, monkeypatch):
    project_id = await new_project(pg)

    async def broken(*a, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(pds.ProjectDatasetService, "_insert_link", broken)
    async with pg() as s:
        p = await ProjectService(s).require(project_id)
        with pytest.raises(RuntimeError, match="database went away"):
            await pds.ProjectDatasetService(s).upload(p, "sales.csv", io.BytesIO(b"a\n1\n"))
    assert fake.datasets == {} and len(fake.calls("DELETE", "/datasets/")) == 1


# ── list / get ───────────────────────────────────────────────────────────────────────────────


async def test_the_list_is_scoped_to_the_project_and_newest_first(pg, api, fake):
    a, b = await new_project(pg), await new_project(pg)
    for n in ("first", "second", "third"):
        assert (await upload(api, a, name=n)).status_code == 201
    await upload(api, b, name="elsewhere")
    assert [d["name"] for d in (await api.get(f"/api/projects/{a}/datasets")).json()] == ["third", "second", "first"]
    assert [d["name"] for d in (await api.get(f"/api/projects/{b}/datasets")).json()] == ["elsewhere"]
    assert (await api.get("/api/projects/no-such-project/datasets")).status_code == 404


async def test_get_adds_a_20_row_preview_from_analytics(pg, api, fake):
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    r = await api.get(f"/api/projects/{project}/datasets/{item['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: v for k, v in body.items() if k != "preview"} == item and body["missing"] is False
    assert len(body["preview"]) == 20 and list(body["preview"][0]) == ["region", "revenue"]
    sent = json.loads(fake.calls("POST", "/sample")[0].content)
    assert sent["dataset_id"] == item["dataset_id"] and sent["return_data"] is True and sent["target_total_volume"] == 20


async def test_a_link_whose_analytics_dataset_is_gone_reads_as_missing(pg, api, fake):
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    fake.datasets.pop(item["dataset_id"])  # deleted behind the engine's back
    r = await api.get(f"/api/projects/{project}/datasets/{item['id']}")
    assert r.status_code == 200 and r.json()["missing"] is True and r.json()["preview"] == []
    n = len(fake.requests)
    listed = (await api.get(f"/api/projects/{project}/datasets")).json()
    assert [d["missing"] for d in listed] == [False] and len(fake.requests) == n  # the list never asks analytics


async def test_other_analytics_failures_on_get_are_not_reported_as_missing(pg, api, fake):
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    fake.fail = (500, {"detail": "duckdb exploded"})
    r = await api.get(f"/api/projects/{project}/datasets/{item['id']}")
    assert r.status_code == 502 and "duckdb exploded" in r.json()["detail"]["message"]
    fake.down = True
    assert (await api.get(f"/api/projects/{project}/datasets/{item['id']}")).status_code == 503


async def test_an_id_from_another_project_is_a_404_everywhere(pg, api, fake):
    a, b = await new_project(pg), await new_project(pg)
    item = (await upload(api, a)).json()
    base = f"/api/projects/{b}/datasets/{item['id']}"
    for r in [await api.get(base), await api.patch(base, json={"name": "x"}), await api.delete(base)]:
        assert r.status_code == 404 and r.json()["detail"]["code"] == "dataset_not_found"
    assert fake.calls("DELETE", "/datasets/") == [] and item["dataset_id"] in fake.datasets
    assert (await api.get(f"/api/projects/{a}/datasets/{item['id']}")).status_code == 200
    assert (await api.get(f"/api/projects/{a}/datasets/not-a-uuid")).status_code == 404


# ── rename / delete ────────────────────────────────────────────────────────────────


async def test_rename_and_its_clash(pg, api, fake):
    project = await new_project(pg)
    one = (await upload(api, project, name="one")).json()
    await upload(api, project, name="two")
    base = f"/api/projects/{project}/datasets/{one['id']}"
    r = await api.patch(base, json={"name": "two"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
    assert (await api.patch(base, json={"name": "  "})).status_code == 422
    r = await api.patch(base, json={"name": "renamed"})
    assert r.status_code == 200 and r.json()["name"] == "renamed" and r.json()["dataset_id"] == one["dataset_id"]
    assert (await api.patch(base, json={"name": "renamed"})).status_code == 200  # same name: a no-op, not a clash
    assert sorted(d["name"] for d in (await api.get(f"/api/projects/{project}/datasets")).json()) == ["renamed", "two"]


async def test_delete_removes_the_link_and_the_analytics_dataset(pg, api, fake):
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    r = await api.delete(f"/api/projects/{project}/datasets/{item['id']}")
    assert r.status_code == 204 and r.content == b""
    assert [c.url.path for c in fake.calls("DELETE", "/datasets/")] == [f"/datasets/{item['dataset_id']}"]
    assert item["dataset_id"] not in fake.datasets
    assert (await api.get(f"/api/projects/{project}/datasets")).json() == []
    assert (await api.get(f"/api/projects/{project}/datasets/{item['id']}")).status_code == 404


async def test_a_project_with_datasets_cannot_be_deleted_until_they_are(pg, api, fake):
    """No FK spans the two databases, so a project delete must never strand a file in analytics."""
    from src.services.project_service import ProjectError
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    async with pg() as s:
        with pytest.raises(ProjectError) as e:
            await ProjectService(s).delete(project)
    assert (e.value.status, e.value.code) == (409, "project_not_empty") and "datasets" in str(e.value)
    assert fake.calls("DELETE", "/datasets/") == [] and item["dataset_id"] in fake.datasets
    assert (await api.delete(f"/api/projects/{project}/datasets/{item['id']}")).status_code == 204
    async with pg() as s:
        await ProjectService(s).delete(project)


async def test_delete_ignores_a_404_from_analytics_but_keeps_the_link_when_it_fails(pg, api, fake):
    project = await new_project(pg)
    gone = (await upload(api, project, name="gone")).json()
    kept = (await upload(api, project, name="kept")).json()
    fake.datasets.pop(gone["dataset_id"])  # already deleted on the analytics side
    assert (await api.delete(f"/api/projects/{project}/datasets/{gone['id']}")).status_code == 204
    fake.down = True
    assert (await api.delete(f"/api/projects/{project}/datasets/{kept['id']}")).status_code == 503
    fake.down = False
    fake.fail = (500, {"detail": "storage offline"})
    r = await api.delete(f"/api/projects/{project}/datasets/{kept['id']}")
    assert r.status_code == 502 and "storage offline" in r.json()["detail"]["message"]
    assert kept["dataset_id"] in fake.datasets
    assert [d["name"] for d in (await api.get(f"/api/projects/{project}/datasets")).json()] == ["kept"]
    assert (await api.delete(f"/api/projects/{project}/datasets/{kept['id']}")).status_code == 204  # a retry finishes


# ── name resolution in the data nodes ────────────────────────────────────────────────────────


def _context(project_id: str | None) -> ExecutionContext:
    return ExecutionContext(workflow=Workflow(name="wf", nodes=[], connections=[]), execution_id="exec_t",
                            start_time=datetime.now(), mode="manual", project_id=project_id)


async def _run_profile(monkeypatch, fake, project_id: str | None, dataset: str):
    from src.nodes.data import profile as profile_mod

    monkeypatch.setattr(profile_mod.httpx, "AsyncClient",
                        lambda *a, **kw: REAL_ASYNC_CLIENT(*a, transport=httpx.MockTransport(fake), **kw))
    node = profile_mod.ProfileNode()
    nd = NodeDefinition(name="Profile", type="Profile", parameters={"sourceType": "dataset", "datasetId": dataset})
    return await node.execute(_context(project_id), nd, [])


def _profiled(fake) -> str:
    return json.loads(fake.calls("POST", "/profile")[-1].content)["dataset_id"]


async def test_a_node_resolves_its_dataset_in_order_within_its_project(pg, api, fake, monkeypatch):
    a, b = await new_project(pg), await new_project(pg)
    mine = (await upload(api, a, name="sales")).json()
    theirs = (await upload(api, b, name="sales")).json()  # same name, other project: must not be picked
    # 1. the link id (what the picker saves) -> its analytics dataset
    result = await _run_profile(monkeypatch, fake, a, mine["id"])
    assert result.outputs["main"][0].json["success"] is True and _profiled(fake) == mine["dataset_id"]
    # 2. the analytics dataset_id of one of the project's links (legacy values)
    await _run_profile(monkeypatch, fake, a, mine["dataset_id"])
    assert _profiled(fake) == mine["dataset_id"]
    # 3. any other UUID passes through unchanged (workflows saved before project datasets)
    fake.datasets[raw := str(uuid.uuid4())] = {"body": b"", "version": str(uuid.uuid4())}
    await _run_profile(monkeypatch, fake, a, raw)
    assert _profiled(fake) == raw
    # 4. otherwise a name in the project
    await _run_profile(monkeypatch, fake, a, "sales")
    assert _profiled(fake) == mine["dataset_id"]
    await _run_profile(monkeypatch, fake, b, "sales")
    assert _profiled(fake) == theirs["dataset_id"]


async def test_another_projects_link_id_is_not_resolved(pg, api, fake, monkeypatch):
    a, b = await new_project(pg), await new_project(pg)
    theirs = (await upload(api, b, name="sales")).json()
    n = len(fake.calls("POST", "/profile"))
    with pytest.raises(ValueError, match="This dataset was deleted from the project"):
        await _run_profile(monkeypatch, fake, a, theirs["id"])
    assert len(fake.calls("POST", "/profile")) == n


async def test_a_node_whose_link_was_deleted_says_so(pg, api, fake, monkeypatch):
    project = await new_project(pg)
    item = (await upload(api, project)).json()
    assert (await api.delete(f"/api/projects/{project}/datasets/{item['id']}")).status_code == 204
    with pytest.raises(ValueError, match="This dataset was deleted from the project"):
        await _run_profile(monkeypatch, fake, project, item["id"])
    assert fake.calls("POST", "/profile") == []


async def test_a_node_naming_a_missing_dataset_fails_clearly(pg, api, fake, monkeypatch):
    a, b = await new_project(pg), await new_project(pg)
    await upload(api, b, name="sales")
    with pytest.raises(ValueError, match=r"No dataset named 'sales' in this project"):
        await _run_profile(monkeypatch, fake, a, "sales")
    assert fake.calls("POST", "/profile") == []


async def test_the_ai_tools_send_the_dataset_id_not_a_file_path(pg, api, fake):
    from src.nodes.ai.tools._analytics_helpers import resolve_dataset_payload, resolve_dataset_ref

    raw = str(uuid.uuid4())
    assert resolve_dataset_ref({"dataset_id": raw, "data": [{"a": 1}], "top_n": 3}) == {"dataset_id": raw, "top_n": 3}
    assert resolve_dataset_ref({"data": [{"a": 1}]}) == {"data": [{"a": 1}]}
    project = await new_project(pg)
    item = (await upload(api, project, name="sales")).json()
    assert await resolve_dataset_payload({"dataset_id": "sales"}, _context(project)) == {"dataset_id": item["dataset_id"]}
    with pytest.raises(pds.DatasetNotFound):
        await resolve_dataset_payload({"dataset_id": "nope"}, _context(project))
