"""Webhook delivery lookup, update validation, and list/detail authorization.

These cover three failure modes that the happy-path webhook tests cannot see,
because each only appears once a subscription has history, is edited after
creation, or is read by someone who is not a team admin.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.features.webhooks import repo, service
from app.infra.db.postgres import async_session_factory
from conftest import DEFAULT_TEAM_ID, auth, create_team_user

SENT: list[dict] = []


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def capture_deliveries(monkeypatch):
    """Intercept the outbound POST; everything else stays real."""
    SENT.clear()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            SENT.append({"url": url, "body": content, "headers": headers or {}})
            return _Response(200)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    yield


async def subscribe(client, user_id, name="hook", url="https://example.test/hook"):
    r = await client.post("/api/v1/webhooks", headers=auth(user_id),
                          json={"name": name, "url": url, "events": []})
    assert r.status_code == 201, r.text
    return r.json()


async def _bury_under_newer_deliveries(subscription_id: str, delivery_id: str,
                                       count: int) -> None:
    """Make *delivery_id* the oldest row, behind *count* newer siblings."""
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE webhook_deliveries "
                 "SET created_at = now() - interval '1 day' WHERE id = :id"),
            {"id": delivery_id})
        await s.execute(
            text("""
                INSERT INTO webhook_deliveries
                    (subscription_id, event_type, payload, created_at)
                SELECT :sid, 'webhook.test', '{}'::jsonb,
                       now() + (g * interval '1 second')
                FROM generate_series(1, :n) AS g
            """),
            {"sid": subscription_id, "n": count})
        await s.commit()


async def _delivery_row(delivery_id: str) -> dict:
    async with async_session_factory() as s:
        return dict((await s.execute(
            text("SELECT status, attempts, error, response_status "
                 "FROM webhook_deliveries WHERE id = :id"),
            {"id": delivery_id})).mappings().one())


# --- delivery lookup ----------------------------------------------------------

async def test_a_delivery_buried_under_newer_ones_is_still_sent(client, admin_id):
    """The worker drains oldest-first, so on any backlogged subscription the
    job it runs is exactly the delivery a bounded newest-first page cannot
    show. Resolving the row by id is what keeps those events from being
    dropped: unsent, unrecorded, and stuck 'pending' while the job that was
    supposed to send them reports success.
    """
    created = await subscribe(client, admin_id)
    delivery = await repo.create_delivery(
        subscription_id=created["id"], event_type="tag.promoted",
        dataset_id=None, payload={"event": "tag.promoted", "data": {}})
    await _bury_under_newer_deliveries(created["id"], delivery["id"], 250)

    result = await service.deliver(delivery["id"], created["id"])

    assert result["delivered"] is True, result
    assert [s["url"] for s in SENT] == ["https://example.test/hook"]
    row = await _delivery_row(delivery["id"])
    assert row["status"] == "delivered"
    assert row["attempts"] == 1


async def test_a_delivery_that_cannot_be_resolved_is_recorded_as_failed(
        client, admin_id):
    """Every other exit from deliver() records an attempt; this one used to
    return quietly, so the job completed while the row stayed 'pending' with
    attempts=0 and no error. Both tables then lied about the same event and
    nothing in the UI could show that the delivery never happened.
    """
    other = await subscribe(client, admin_id, name="other",
                            url="https://other.test/hook")
    created = await subscribe(client, admin_id)
    delivery = await repo.create_delivery(
        subscription_id=created["id"], event_type="tag.promoted",
        dataset_id=None, payload={"event": "tag.promoted", "data": {}})

    # Same id, wrong subscription: signing it with the other subscription's
    # secret and posting it to the other subscription's url would be worse
    # than not sending it.
    result = await service.deliver(delivery["id"], other["id"])

    assert result["delivered"] is False
    assert SENT == []
    row = await _delivery_row(delivery["id"])
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["error"] == "delivery row missing"


# --- update validation --------------------------------------------------------

async def test_patch_rejects_the_same_urls_and_events_that_create_rejects(
        client, admin_id):
    """Nothing downstream re-validates a subscription, so whatever PATCH
    accepts is persisted and used verbatim. An unknown event name is the
    quiet one: the subscription keeps returning 200 and looking healthy while
    matching_subscriptions() can never match it again.
    """
    h = auth(admin_id)
    created = await subscribe(client, admin_id)

    assert (await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                               json={"url": "file:///etc/passwd"})).status_code == 422
    assert (await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                               json={"events": ["not.a.thing"]})).status_code == 422
    assert (await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                               json={"url": ""})).status_code == 422

    # The subscription is untouched, and legitimate edits still work.
    still = (await client.get(f"/api/v1/webhooks/{created['id']}", headers=h)).json()
    assert still["url"] == "https://example.test/hook"
    assert still["events"] == []

    r = await client.patch(f"/api/v1/webhooks/{created['id']}", headers=h,
                           json={"url": "http://example.test/moved",
                                 "events": ["tag.promoted"]})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "http://example.test/moved"
    assert r.json()["events"] == ["tag.promoted"]


# --- list/detail agreement ----------------------------------------------------

async def test_the_webhook_list_shows_only_rows_the_caller_can_open(
        client, admin_id):
    """The list and the detail route have to agree on one gate. While the
    collection was ungated, an editor got a page of rows — delivery target
    urls included — where every drill-down (open, deliveries, test) 403s.
    """
    created = await subscribe(client, admin_id)
    editor, _ = await create_team_user(client, admin_id, "editor",
                                       team_id=DEFAULT_TEAM_ID)

    listed = (await client.get("/api/v1/webhooks", headers=auth(editor))).json()
    assert listed["total"] == 0
    assert listed["items"] == []

    # And the gate is a filter, not a blanket denial: an admin still sees it,
    # and everything an admin sees is openable.
    listed = (await client.get("/api/v1/webhooks", headers=auth(admin_id))).json()
    assert created["id"] in [w["id"] for w in listed["items"]]
    for hook in listed["items"]:
        r = await client.get(f"/api/v1/webhooks/{hook['id']}", headers=auth(admin_id))
        assert r.status_code == 200, r.text
