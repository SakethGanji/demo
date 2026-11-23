"""Phase 0 stale-base race: a save (PUT /apps/{id}) or an AI chat turn based on a version that is no longer the app's head is
refused with 409 ``stale_base``, so an AI reply can't overwrite visual edits saved after its base. Real Postgres; the model
is never called (stream_chat is replaced)."""

from __future__ import annotations

import uuid

import pytest

from src.schemas.app import AppCreateRequest, AppFilePayload, AppUpdateRequest
from src.services import project_files as pf
from src.services.app_builder_ai_service import AppBuilderAIService
from src.services.app_service import StaleBaseError
from src.services.project_service import ProjectService
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio
APP = "export default function App() { return <div>v</div> }\n"


async def make_app(pg) -> tuple[str, int]:
    async with pg() as s:
        pid = (await ProjectService(s).create(f"sb-{uuid.uuid4().hex[:8]}")).id
        svc = pf._app_service(s)
        app = await svc.create_app(AppCreateRequest(name="Stale", definition={"sourceCode": None}, project_id=pid))
        d = await svc.update_app(app.id, AppUpdateRequest(
            source_code=APP, files=[AppFilePayload(path="App.tsx", content=APP)], create_version=True, version_trigger="ai"))
        return app.id, d.current_version.id


async def client(pg):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.dependencies import get_db_session
    from src.routes import app_builder_ai, apps

    app = FastAPI()
    app.include_router(apps.router, prefix="/api")
    app.include_router(app_builder_ai.router, prefix="/api")

    async def sess():
        async with pg() as s:
            yield s

    app.dependency_overrides[get_db_session] = sess
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def save_body(content: str, base: int | None) -> dict:
    body = {"source_code": content, "files": [{"path": "App.tsx", "content": content}],
            "create_version": True, "version_trigger": "visual"}
    if base is not None:
        body["base_version_id"] = base
    return body


async def test_service_refuses_a_save_on_a_stale_base(pg):
    aid, v1 = await make_app(pg)
    async with pg() as s:
        svc = pf._app_service(s)
        d = await svc.update_app(aid, AppUpdateRequest(source_code="a", files=[AppFilePayload(path="App.tsx", content="a")],
                                                       create_version=True, base_version_id=v1))
        v2 = d.current_version.id
        assert v2 != v1
    async with pg() as s:
        with pytest.raises(StaleBaseError) as ei:
            await pf._app_service(s).update_app(aid, AppUpdateRequest(
                source_code="b", files=[AppFilePayload(path="App.tsx", content="b")], create_version=True, base_version_id=v1))
        assert ei.value.detail()["current_version_id"] == v2


async def test_put_returns_409_on_a_stale_base_and_keeps_the_newer_version(pg):
    aid, v1 = await make_app(pg)
    c = await client(pg)
    r = await c.put(f"/api/apps/{aid}", json=save_body("visual edit", v1))
    assert r.status_code == 200, r.text
    v2 = r.json()["current_version"]["id"]
    # a second writer still on v1 (the AI reply that started before the visual save) is refused
    r = await c.put(f"/api/apps/{aid}", json=save_body("ai reply", v1))
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "stale_base" and d["base_version_id"] == v1 and d["current_version_id"] == v2
    got = (await c.get(f"/api/apps/{aid}")).json()
    assert got["current_version"]["id"] == v2
    assert any(f["content"] == "visual edit" for f in got["files"])
    # no base: legacy writers are unaffected
    r = await c.put(f"/api/apps/{aid}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text


async def test_chat_refuses_a_stale_base_before_streaming(pg, monkeypatch):
    aid, v1 = await make_app(pg)
    called: list[int | None] = []

    async def fake_stream(self, request):
        called.append(request.base_version_id)
        yield {"event": "message", "data": '{"type": "done"}'}

    monkeypatch.setattr(AppBuilderAIService, "stream_chat", fake_stream)
    c = await client(pg)
    r = await c.put(f"/api/apps/{aid}", json=save_body("visual edit", v1))
    v2 = r.json()["current_version"]["id"]

    r = await c.post("/api/ai/app-builder/chat", json={"message": "x", "app_id": aid, "current_version_id": v1, "base_version_id": v1})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "stale_base"
    assert called == []  # the model never ran

    r = await c.post("/api/ai/app-builder/chat", json={"message": "x", "app_id": aid, "current_version_id": v2, "base_version_id": v2})
    assert r.status_code == 200, r.text
    assert called == [v2]
