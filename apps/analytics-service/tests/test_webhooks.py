"""Outbound notifications on lifecycle events.

Deliveries are exercised end to end with the HTTP client patched, so the
subscription model, the event fan-out, the worker hand-off, the signature, and
the delivery record are all real — only the network hop is faked.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from conftest import DEFAULT_TEAM_ID, auth, create_team_user, upload_inline

ROWS = [{"id": 1, "amount": 10.0}, {"id": None, "amount": 20.0}]

SENT: list[dict] = []


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def capture_deliveries(monkeypatch):
    """Intercept the outbound POST; everything else stays real."""
    SENT.clear()
    status = {"code": 200}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            SENT.append({"url": url, "body": content, "headers": headers or {}})
            if status["code"] == 0:
                raise ConnectionError("receiver unreachable")
            return _Response(status["code"])

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    yield status


async def subscribe(client, admin_id, events=None, name="hook"):
    r = await client.post("/api/v1/webhooks", headers=auth(admin_id), json={
        "name": name, "url": "https://example.test/hook", "events": events or []})
    assert r.status_code == 201, r.text
    return r.json()


# --- subscriptions ------------------------------------------------------------

async def test_the_secret_is_returned_once_and_never_again(client, admin_id):
    created = await subscribe(client, admin_id)
    assert created["secret"].startswith("whsec_")

    fetched = await client.get(f"/api/v1/webhooks/{created['id']}",
                               headers=auth(admin_id))
    assert fetched.status_code == 200
    assert "secret" not in fetched.json()

    listed = await client.get("/api/v1/webhooks", headers=auth(admin_id))
    assert all("secret" not in w for w in listed.json()["items"])


async def test_subscription_crud(client, admin_id):
    h = auth(admin_id)
    created = await subscribe(client, admin_id)

    assert (await client.post("/api/v1/webhooks", headers=h, json={
        "name": "hook", "url": "https://example.test/x"})).status_code == 409

    r = await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                           json={"enabled": False, "events": ["tag.promoted"]})
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["events"] == ["tag.promoted"]

    assert (await client.delete(f"/api/v1/webhooks/{created['id']}",
                                headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/webhooks/{created['id']}",
                             headers=h)).status_code == 404


async def test_unknown_event_types_are_rejected(client, admin_id):
    r = await client.post("/api/v1/webhooks", headers=auth(admin_id), json={
        "name": "bad", "url": "https://example.test/x", "events": ["not.a.thing"]})
    assert r.status_code == 422


async def test_non_http_urls_are_rejected(client, admin_id):
    r = await client.post("/api/v1/webhooks", headers=auth(admin_id), json={
        "name": "bad", "url": "file:///etc/passwd"})
    assert r.status_code == 422


# --- delivery -----------------------------------------------------------------

async def test_a_test_ping_is_signed_and_verifiable(client, admin_id):
    created = await subscribe(client, admin_id)
    r = await client.post(f"/api/v1/webhooks/{created['id']}/test",
                          headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delivered"
    assert r.json()["response_status"] == 200

    [sent] = SENT
    assert sent["url"] == "https://example.test/hook"
    timestamp = sent["headers"]["X-Webhook-Timestamp"]
    expected = hmac.new(created["secret"].encode(),
                        f"{timestamp}.".encode() + sent["body"],
                        hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Webhook-Signature"] == f"sha256={expected}"
    assert sent["headers"]["X-Webhook-Event"] == "webhook.test"


async def test_a_failing_receiver_is_recorded_not_raised(client, admin_id,
                                                         capture_deliveries):
    created = await subscribe(client, admin_id)
    capture_deliveries["code"] = 500

    r = await client.post(f"/api/v1/webhooks/{created['id']}/test",
                          headers=auth(admin_id))
    assert r.status_code == 200          # the API call still succeeds
    assert r.json()["status"] == "failed"
    assert r.json()["response_status"] == 500
    assert "500" in r.json()["error"]


async def test_an_unreachable_receiver_is_recorded(client, admin_id,
                                                   capture_deliveries):
    created = await subscribe(client, admin_id)
    capture_deliveries["code"] = 0       # raise on send

    r = await client.post(f"/api/v1/webhooks/{created['id']}/test",
                          headers=auth(admin_id))
    assert r.json()["status"] == "failed"
    assert "ConnectionError" in r.json()["error"]


# --- lifecycle events ---------------------------------------------------------

async def test_a_failing_validation_fires_an_event(client, admin_id):
    from app.shared import worker

    created = await subscribe(client, admin_id)
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "id-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "id"})

    await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert await worker.run_pending_jobs_once() >= 1

    deliveries = (await client.get(
        f"/api/v1/webhooks/{created['id']}/deliveries", headers=h)).json()
    assert deliveries["total"] == 1
    delivery = deliveries["items"][0]
    assert delivery["event_type"] == "validation.failed"
    assert delivery["status"] == "delivered"
    assert delivery["payload"]["data"]["error_failures"] == 1
    assert delivery["dataset_id"] == ds


async def test_the_event_body_carries_no_failing_rows(client, admin_id):
    """The failing rows are an artifact; the webhook gets counts only."""
    from app.shared import worker

    await subscribe(client, admin_id)
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "id-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "id"})
    await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    await worker.run_pending_jobs_once()

    body = SENT[-1]["body"].decode()
    assert "error_failures" in body
    for leak in ("sample_failures", "amount", "20.0"):
        assert leak not in body


async def test_promotion_fires_an_event(client, admin_id):
    from app.shared import worker

    created = await subscribe(client, admin_id, events=["tag.promoted"])
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                      headers=h, json={"version_number": 1, "reason": "go live"})
    await worker.run_pending_jobs_once()

    deliveries = (await client.get(
        f"/api/v1/webhooks/{created['id']}/deliveries", headers=h)).json()
    assert deliveries["total"] == 1
    payload = deliveries["items"][0]["payload"]
    assert payload["event"] == "tag.promoted"
    assert payload["data"]["tag"] == "production"
    assert payload["data"]["to_version_number"] == 1


async def test_the_event_filter_is_respected(client, admin_id):
    from app.shared import worker

    created = await subscribe(client, admin_id, events=["tag.promoted"])
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "r", "rule_type": "not_null", "sheet_selector": "data",
        "column_selector": "id"})

    await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    await worker.run_pending_jobs_once()

    deliveries = (await client.get(
        f"/api/v1/webhooks/{created['id']}/deliveries", headers=h)).json()
    assert deliveries["total"] == 0      # subscribed to promotions only


async def test_a_disabled_subscription_receives_nothing(client, admin_id):
    from app.shared import worker

    created = await subscribe(client, admin_id)
    h = auth(admin_id)
    await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                       json={"enabled": False})
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                      headers=h, json={"version_number": 1})
    await worker.run_pending_jobs_once()

    assert (await client.get(f"/api/v1/webhooks/{created['id']}/deliveries",
                             headers=h)).json()["total"] == 0


async def test_a_broken_webhook_does_not_break_the_operation(
        client, admin_id, capture_deliveries):
    """A dead receiver must never fail the promotion that triggered it."""
    from app.shared import worker

    await subscribe(client, admin_id)
    capture_deliveries["code"] = 0
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1})
    assert r.status_code == 200          # the promotion succeeded
    await worker.run_pending_jobs_once()


# --- scoping ------------------------------------------------------------------

async def test_events_only_reach_their_own_team(client, admin_id):
    from app.shared import worker

    created = await subscribe(client, admin_id)     # Default team
    outsider, other_team = await create_team_user(client, admin_id, "admin")

    from conftest import upload_file, SAMPLE_CSV

    other_ds = (await upload_file(client, admin_id, SAMPLE_CSV, name="o.csv",
                                  team_id=other_team))["dataset_id"]
    await client.post(f"/api/v1/datasets/{other_ds}/tags/production/promote",
                      headers=auth(admin_id), json={"version_number": 1})
    await worker.run_pending_jobs_once()

    assert (await client.get(f"/api/v1/webhooks/{created['id']}/deliveries",
                             headers=auth(admin_id))).json()["total"] == 0


async def test_managing_webhooks_needs_team_manage(client, admin_id):
    created = await subscribe(client, admin_id)
    editor, _ = await create_team_user(client, admin_id, "editor",
                                       team_id=DEFAULT_TEAM_ID)

    assert (await client.post("/api/v1/webhooks", headers=auth(editor), json={
        "name": "nope", "url": "https://example.test/x"})).status_code == 403
    assert (await client.delete(f"/api/v1/webhooks/{created['id']}",
                                headers=auth(editor))).status_code == 403


async def test_another_teams_webhook_is_hidden(client, admin_id):
    created = await subscribe(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "admin")
    assert (await client.get(f"/api/v1/webhooks/{created['id']}",
                             headers=auth(outsider))).status_code == 404
