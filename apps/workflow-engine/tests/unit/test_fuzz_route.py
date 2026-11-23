"""The fuzz route maps AI failures like CONTRACTS-STAGE3B section 1 (the AI half is monkeypatched)."""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.dependencies import get_workflow_service
from src.engine.test_ai import TestAIError
from src.routes import test_fuzz as route


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[get_workflow_service] = lambda: types.SimpleNamespace(_workflow_repo=object())
    seen = {}

    async def fake_fuzz(repo, wid, **kw):
        seen.update(kw)
        if kw.get("propose"):
            await kw["propose"]({}, {}, [])
        return {"runs": 0, "seed": kw["seed"], "findings": [], "inputs_tried": 0, "ai_inputs": 0}

    monkeypatch.setattr(route, "fuzz_workflow", fake_fuzz)
    c = TestClient(app)
    c.seen = seen  # type: ignore[attr-defined]
    return c


def _service(monkeypatch, fn):
    mod = types.ModuleType("src.services.test_ai_service")
    mod.propose_variations = fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.services.test_ai_service", mod)
    import src.services as pkg
    monkeypatch.setattr(pkg, "test_ai_service", mod, raising=False)


def test_plain_request_passes_options_through(client):
    r = client.post("/api/workflows/wf_1/tests/fuzz", json={"count": 5, "seed": 9})
    assert r.status_code == 200 and client.seen["count"] == 5 and client.seen["seed"] == 9 and client.seen["propose"] is None
    assert client.post("/api/workflows/wf_1/tests/fuzz", json={"count": 0}).status_code == 422
    assert client.post("/api/workflows/wf_1/tests/fuzz", json={"count": 201}).status_code == 422


@pytest.mark.parametrize("code,status,detail", [("not_configured", 503, "ai_not_configured"), ("bad_json", 502, "ai_failed")])
def test_ai_errors_map_to_503_and_502(client, monkeypatch, code, status, detail):
    async def boom(*a, **k):
        raise TestAIError(code, "nope")

    _service(monkeypatch, boom)
    r = client.post("/api/workflows/wf_1/tests/fuzz", json={"ai": True})
    assert r.status_code == status and r.json()["detail"]["code"] == detail


def test_ai_true_passes_the_proposer(client, monkeypatch):
    async def ok(*a, **k):
        return []

    _service(monkeypatch, ok)
    assert client.post("/api/workflows/wf_1/tests/fuzz", json={"ai": True}).status_code == 200
    assert client.seen["propose"] is ok
