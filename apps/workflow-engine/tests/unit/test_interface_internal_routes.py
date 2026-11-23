"""``/api/workflows/{id}/interface*`` (studio): request / response mapping and error envelopes (DESIGN-STAGE5-UIS section 7.1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.dependencies import get_db_session
from src.core.exceptions import WorkflowNotFoundError
from src.routes import interfaces as routes
from src.services.interface_service import InterfaceError

W = "/api/workflows/wf_1/interface"


class Stub:
    """Records what the routes hand to the service and plays back a scripted outcome."""

    calls: list[tuple] = []
    outcome: object = None

    def __init__(self, session):
        pass

    async def _do(self, name, *args):
        Stub.calls.append((name, *args))
        if isinstance(Stub.outcome, Exception):
            raise Stub.outcome
        return Stub.outcome if Stub.outcome is not None else {"ok": name}

    async def get_interface(self, wid): return await self._do("get", wid)
    async def put_interface(self, wid, raw): return await self._do("put", wid, raw)
    async def generate(self, wid, kind, slug): return await self._do("generate", wid, kind, slug)
    async def preview_submit(self, wid, fields, key): return await self._do("preview_submit", wid, fields, key)
    async def preview_data(self, wid, eid): return await self._do("preview_data", wid, eid)
    async def ai_edit(self, wid, sid, instruction, spec): return await self._do("ai_edit", wid, sid, instruction, spec)
    async def client_ts(self, wid): return "export const x = 1\n" if Stub.outcome is None else await self._do("client_ts", wid)
    async def promote(self, wid): return (201, {"app_id": "app_1", "app_url": "/a/x"}) if Stub.outcome is None else await self._do("promote", wid)


@pytest.fixture
def client(monkeypatch):
    Stub.calls, Stub.outcome = [], None
    monkeypatch.setattr(routes, "InterfaceService", Stub)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_db_session] = lambda: object()
    return TestClient(app)


def test_get_returns_the_service_payload(client):
    assert client.get(W).json() == {"ok": "get"} and Stub.calls == [("get", "wf_1")]


def test_put_passes_the_raw_body_and_null_removes(client):
    client.put(W, json={"kind": "form", "slug": "ab"})
    client.put(W, content="null", headers={"Content-Type": "application/json"})
    assert Stub.calls == [("put", "wf_1", {"kind": "form", "slug": "ab"}), ("put", "wf_1", None)]


def test_put_too_large_or_invalid_json(client):
    assert client.put(W, content="{", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.put(W, content="x" * (600 * 1024), headers={"Content-Type": "application/json"}).status_code == 413


def test_interface_invalid_is_422_with_fields(client):
    Stub.outcome = InterfaceError(422, "INTERFACE_INVALID", "The interface isn't valid.", fields={"slug": "bad"})
    r = client.put(W, json={})
    assert r.status_code == 422 and r.json()["detail"] == {"code": "INTERFACE_INVALID", "message": "The interface isn't valid.", "fields": {"slug": "bad"}}


def test_unknown_workflow_is_404(client):
    Stub.outcome = WorkflowNotFoundError("wf_1")
    assert client.get(W).status_code == 404 and client.post(f"{W}/promote").status_code == 404


def test_generate_validates_the_kind(client):
    assert client.post(f"{W}/generate", json={"kind": "wizard"}).status_code == 422
    assert client.post(f"{W}/generate", json={"kind": "custom", "slug": "refunds", "x": 1}).status_code == 422
    client.post(f"{W}/generate", json={"kind": "custom", "slug": "refunds"})
    assert Stub.calls == [("generate", "wf_1", "custom", "refunds")]


def test_preview_submit_needs_a_key_and_a_strict_body(client):
    assert client.post(f"{W}/preview/submit", json={"fields": {}}).status_code == 400
    assert client.post(f"{W}/preview/submit", json={"nope": 1}, headers={"Idempotency-Key": "k"}).status_code == 422
    r = client.post(f"{W}/preview/submit", json={"fields": {"a": 1}}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 202 and Stub.calls == [("preview_submit", "wf_1", {"a": 1}, "k")]


def test_preview_data_takes_an_optional_execution_id(client):
    client.get(f"{W}/preview/data")
    client.get(f"{W}/preview/data?execution_id=exe_1")
    assert Stub.calls == [("preview_data", "wf_1", None), ("preview_data", "wf_1", "exe_1")]


def test_ai_edit_screen_ids_may_contain_colons_and_spaces(client):
    r = client.post(f"{W}/screens/task:Manager%20review/ai-edit", json={"instruction": "add a link", "spec": {"v": 1}})
    assert r.status_code == 200 and Stub.calls == [("ai_edit", "wf_1", "task:Manager review", "add a link", {"v": 1})]
    assert client.post(f"{W}/screens/start/ai-edit", json={"instruction": "", "spec": {}}).status_code == 422
    assert client.post(f"{W}/screens/start/ai-edit", json={"instruction": "x" * 2001, "spec": {}}).status_code == 422


@pytest.mark.parametrize("status,code", [(503, "LLM_NOT_CONFIGURED"), (422, "AI_SPEC_INVALID"), (502, "AI_FAILED")])
def test_ai_errors_keep_their_status_and_code(client, status, code):
    Stub.outcome = InterfaceError(status, code, "m")
    r = client.post(f"{W}/screens/start/ai-edit", json={"instruction": "x", "spec": {}})
    assert r.status_code == status and r.json()["detail"]["code"] == code


def test_client_ts_is_plain_text(client):
    r = client.get(f"{W}/client.ts")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and r.text == "export const x = 1\n"


def test_promote_reports_201_then_200(client):
    r = client.post(f"{W}/promote")
    assert r.status_code == 201 and r.json() == {"app_id": "app_1", "app_url": "/a/x"}
