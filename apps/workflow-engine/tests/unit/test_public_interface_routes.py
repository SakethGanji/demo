"""``/api/i/{project}/{slug}/...``: body caps, strict models, idempotency, no-store, 404 hiding, rate-limit hook (DESIGN-STAGE5-UIS section 7.2)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import rate_limit
from src.core.dependencies import get_db_session
from src.routes import public_interfaces as routes
from src.services.chat_service import ChatService

from ._interface_fakes import FakeChatRepo, FakeInterfaceRepo, fixture, make_service

GOOD = {"order_id": "A-12345", "amount": 742, "reason": "damaged", "email": "dana@example.com"}
P = "/api/i/bank/refunds"


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def check(self, bucket, key, cost=1):
        self.calls.append((bucket, key))


@pytest.fixture
def env(monkeypatch):
    form_svc, form_repo, rec = make_service()
    chat_def = fixture("chat_support_ui.json")
    chat_svc, chat_irepo, chat_rec = make_service(FakeInterfaceRepo(chat_def, slug="support-chat"))
    chat_repo = FakeChatRepo()

    def interface_service(session):
        class Dispatch:
            def __getattr__(self, name):
                return getattr(form_svc, name)

            async def resolve_live(self, project, slug, kinds=None):
                return await (chat_svc if slug == "support-chat" else form_svc).resolve_live(project, slug, kinds=kinds)

            def schema_of(self, t):
                return form_svc.schema_of(t)

        return Dispatch()

    monkeypatch.setattr(routes, "InterfaceService", interface_service)
    monkeypatch.setattr(routes, "ChatService", lambda session: ChatService(session, repo=chat_repo, interfaces=chat_svc))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_db_session] = lambda: object()
    limiter = Recorder()
    previous = rate_limit.set_rate_limiter(limiter)
    c = TestClient(app)
    c.rec, c.limiter, c.chat_rec, c.chat_repo, c.form_repo = rec, limiter, chat_rec, chat_repo, form_repo        # type: ignore[attr-defined]
    yield c
    rate_limit.set_rate_limiter(previous)


def submit(c, body=None, key="k1", **kw):
    headers = {"Idempotency-Key": key} if key else {}
    return c.post(f"{P}/submit", content=json.dumps(body if body is not None else {"fields": GOOD}), headers={"Content-Type": "application/json", **headers}, **kw)


def test_schema_is_public_json_and_never_cached(env):
    r = env.get(f"{P}/schema")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["fields"][0]["name"] == "order_id"


@pytest.mark.parametrize("path", ["/api/i/nope/refunds/schema", "/api/i/bank/other/schema", "/api/i/bank/refunds/runs/exec_1"])
def test_misses_are_404_with_a_single_shape_and_no_store(env, path):
    r = env.get(path)
    assert r.status_code == 404 and r.json() == {"detail": {"code": "NOT_FOUND", "message": "not found"}} and r.headers["cache-control"] == "no-store"


def test_submit_returns_202_with_ids_token_and_poll_url(env):
    r = submit(env)
    body = r.json()
    assert r.status_code == 202 and r.headers["cache-control"] == "no-store"
    assert set(body) == {"submission_id", "execution_id", "run_token", "poll_url"}
    assert body["poll_url"] == f"/api/i/bank/refunds/runs/{body['execution_id']}"
    assert env.rec.calls[0]["start_node"] == "Refund request"


def test_same_idempotency_key_same_run(env):
    a, b = submit(env, key="same").json(), submit(env, key="same").json()
    assert (a["submission_id"], a["execution_id"]) == (b["submission_id"], b["execution_id"]) and len(env.rec.calls) == 1


def test_the_key_may_come_in_the_body_when_a_proxy_strips_the_header(env):
    r = submit(env, {"fields": GOOD, "idempotency_key": "from-body"}, key=None)
    assert r.status_code == 202
    assert submit(env, key=None).status_code == 400 and submit(env, key=None).json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_the_header_wins_over_the_body_key(env):
    submit(env, {"fields": GOOD, "idempotency_key": "body"}, key="hdr")
    assert [s["idempotency_key"] for s in env.form_repo.submissions.values()] == ["hdr"]


def test_body_over_64kb_is_413_before_anything_runs(env):
    r = submit(env, {"fields": {**GOOD, "reason": "x" * 70000}})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE" and env.rec.calls == []
    assert submit(env, {"fields": {**GOOD, "reason": "x" * 60000}}).status_code == 422             # under the cap: validated (enum), not refused as too large


def test_a_chunked_body_over_the_cap_is_also_refused(env):
    def gen():
        yield b'{"fields": {"a": "'
        yield b"x" * 70000
        yield b'"}}'

    r = env.post(f"{P}/submit", content=gen(), headers={"Idempotency-Key": "c"})
    assert r.status_code == 413


@pytest.mark.parametrize("raw,status", [("not json", 400), ("", 422), ("[]", 422), ('{"fields": 1}', 422), ('{"fields": {}, "extra": 1}', 422), ('{}', 422)])
def test_bad_bodies(env, raw, status):
    r = env.post(f"{P}/submit", content=raw, headers={"Idempotency-Key": "k", "Content-Type": "application/json"})
    assert r.status_code == status and set(r.json()["detail"]) >= {"code", "message"}


def test_field_errors_use_the_error_envelope(env):
    r = submit(env, {"fields": {**GOOD, "order_id": "bad", "evil": 1}})
    d = r.json()["detail"]
    assert r.status_code == 422 and d["code"] == "UNKNOWN_FIELD" and set(d["fields"]) == {"order_id", "evil"}
    r = submit(env, {"fields": {**GOOD, "order_id": "bad"}})
    assert r.json()["detail"]["code"] == "VALIDATION_FAILED" and "order_id" in r.json()["detail"]["fields"]


def test_polling_needs_the_bearer_token(env):
    out = submit(env).json()
    env.form_repo.add_run(out["execution_id"], "waiting")
    url = f"{P}/runs/{out['execution_id']}"
    assert env.get(url).status_code == 404
    assert env.get(url, headers={"Authorization": "Bearer nope"}).status_code == 404
    r = env.get(url, headers={"Authorization": f"Bearer {out['run_token']}"})
    assert r.status_code == 200 and r.json()["status"] == "waiting" and r.headers["cache-control"] == "no-store"
    assert set(r.json()) == {"execution_id", "status", "progress", "waiting_for", "result", "message", "error"}


def test_the_rate_limit_hook_is_called_on_every_public_route(env):
    out = submit(env).json()
    env.form_repo.add_run(out["execution_id"], "running")
    env.get(f"{P}/schema")
    env.get(f"{P}/runs/{out['execution_id']}", headers={"Authorization": f"Bearer {out['run_token']}"})
    s = env.post("/api/i/bank/support-chat/chat/sessions", json={}).json()
    h = {"Authorization": f"Bearer {s['session_token']}"}
    env.get(f"/api/i/bank/support-chat/chat/sessions/{s['session_id']}/messages", headers=h)
    env.post(f"/api/i/bank/support-chat/chat/sessions/{s['session_id']}/messages", json={"content": "hi", "client_message_id": "m"}, headers=h)
    buckets = [b for b, _ in env.limiter.calls]
    assert buckets == ["iface.submit:bank:refunds", "iface.poll:bank:refunds", "iface.poll:bank:refunds", "chat.send:bank:support-chat",
                       "iface.poll:bank:support-chat", "chat.send:bank:support-chat"]
    assert all(k == "testclient" for _, k in env.limiter.calls)


def test_a_limiter_can_refuse_with_retry_after(env):
    class Deny:
        def check(self, bucket, key, cost=1):
            if bucket.startswith("iface.submit"):
                raise rate_limit.rate_limited(7)

    rate_limit.set_rate_limiter(Deny())
    r = submit(env)
    assert r.status_code == 429 and r.headers["retry-after"] == "7" and r.json()["detail"]["code"] == "RATE_LIMITED" and env.rec.calls == []
    assert env.get(f"{P}/schema").status_code == 200


def test_forwarded_for_is_honoured_only_behind_a_trusted_proxy(env, monkeypatch):
    env.get(f"{P}/schema", headers={"X-Forwarded-For": "9.9.9.9"})
    monkeypatch.setenv("TRUSTED_PROXY", "1")
    env.get(f"{P}/schema", headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert [k for _, k in env.limiter.calls] == ["testclient", "9.9.9.9"]


def test_a_chat_conversation_over_http(env):
    base = "/api/i/bank/support-chat/chat/sessions"
    r = env.post(base, json={"user_label": "Dana"})
    s = r.json()
    assert r.status_code == 201 and set(s) == {"session_id", "session_token"}
    h = {"Authorization": f"Bearer {s['session_token']}"}
    sent = env.post(f"{base}/{s['session_id']}/messages", json={"content": "hello", "client_message_id": "m1"}, headers=h)
    assert sent.status_code == 202 and sent.json() == {"seq": 1, "execution_id": "exec_1"}
    again = env.post(f"{base}/{s['session_id']}/messages", json={"content": "second", "client_message_id": "m2"}, headers=h)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "CHAT_BUSY"
    listed = env.get(f"{base}/{s['session_id']}/messages?after=0&limit=10", headers=h).json()
    assert listed["busy"] is True and [m["content"] for m in listed["messages"]] == ["hello"]
    assert env.get(f"{base}/{s['session_id']}/messages", headers={"Authorization": "Bearer x"}).status_code == 404
    assert env.get(f"{base}/{s['session_id']}/messages?limit=0", headers=h).status_code == 422


def test_chat_routes_are_404_for_a_form_interface_and_forms_for_a_chat(env):
    assert env.post("/api/i/bank/refunds/chat/sessions", json={}).status_code == 404
    assert env.post("/api/i/bank/support-chat/submit", json={"fields": {}}, headers={"Idempotency-Key": "k"}).status_code == 404


def test_strict_chat_bodies(env):
    base = "/api/i/bank/support-chat/chat/sessions"
    assert env.post(base, json={"surprise": 1}).status_code == 422
    s = env.post(base, json={}).json()
    h = {"Authorization": f"Bearer {s['session_token']}"}
    url = f"{base}/{s['session_id']}/messages"
    assert env.post(url, json={"content": "hi"}, headers=h).status_code == 422
    assert env.post(url, json={"content": "hi", "client_message_id": "m", "x": 1}, headers=h).status_code == 422
    assert env.post(url, json={"content": "x" * 40000, "client_message_id": "m"}, headers=h).status_code == 413
