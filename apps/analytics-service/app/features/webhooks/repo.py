"""Persistence for webhook subscriptions and their delivery attempts."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

# `secret` is intentionally absent: it is returned once at creation and never
# read back through the API.
_SUB_COLS = """id::text, team_id::text, name, url, events, enabled,
               created_by::text, created_at::text AS created_at,
               updated_at::text AS updated_at"""
_DELIVERY_COLS = """id::text, subscription_id::text, event_type,
                    dataset_id::text, payload, status, attempts,
                    response_status, error, created_at::text AS created_at,
                    delivered_at::text AS delivered_at"""


async def create_subscription(*, team_id: str, name: str, url: str, secret: str,
                              events: list[str], enabled: bool,
                              created_by: str | None) -> dict | None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO webhook_subscriptions
                    (team_id, name, url, secret, events, enabled, created_by)
                VALUES (:tid, :name, :url, :secret, CAST(:events AS jsonb),
                        :enabled, :uid)
                ON CONFLICT (team_id, name) DO NOTHING
                RETURNING {_SUB_COLS}
            """),
            {"tid": team_id, "name": name, "url": url, "secret": secret,
             "events": json.dumps(events), "enabled": enabled, "uid": created_by},
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def list_subscriptions(team_ids: list[str] | None, *, limit: int,
                             offset: int) -> tuple[list[dict], int]:
    """A page of subscriptions. ``team_ids=None`` means every team.

    ``None`` and ``[]`` are deliberately different: ``[]`` is "administers no
    team" and must answer an empty page, while ``None`` is the superuser's
    unscoped view. Collapsing them would either hide every row from the one
    caller allowed to see them all, or send an empty (untypeable) array into
    the ``= ANY`` parameter.
    """
    if team_ids is not None and not team_ids:
        return [], 0
    where = "" if team_ids is None else "WHERE team_id = ANY(:tids)"
    params: dict = {} if team_ids is None else {"tids": team_ids}
    async with async_session_factory() as s:
        total = (await s.execute(
            text(f"SELECT COUNT(*) FROM webhook_subscriptions {where}"),
            params)).scalar()
        rows = (await s.execute(
            # `id` breaks ties: names are unique per team, not globally, so an
            # unscoped page ordered by name alone can repeat or skip a row
            # across LIMIT/OFFSET windows.
            text(f"SELECT {_SUB_COLS} FROM webhook_subscriptions {where} "
                 f"ORDER BY name, id LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_subscription(subscription_id: str) -> dict | None:
    if not is_uuid(subscription_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_SUB_COLS} FROM webhook_subscriptions WHERE id = :id"),
            {"id": subscription_id})).mappings().first()
        return dict(row) if row else None


async def get_secret(subscription_id: str) -> str | None:
    """Read the signing secret — for the delivery path only, never the API."""
    async with async_session_factory() as s:
        return (await s.execute(
            text("SELECT secret FROM webhook_subscriptions WHERE id = :id"),
            {"id": subscription_id})).scalar()


async def update_subscription(subscription_id: str, fields: dict) -> dict | None:
    if not is_uuid(subscription_id):
        return None
    sets = ["updated_at = now()"]
    params: dict = {"id": subscription_id}
    for col in ("name", "url", "enabled"):
        if col in fields:
            sets.append(f"{col} = :{col}")
            params[col] = fields[col]
    if "events" in fields:
        sets.append("events = CAST(:events AS jsonb)")
        params["events"] = json.dumps(fields["events"] or [])
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"UPDATE webhook_subscriptions SET {', '.join(sets)} "
                 f"WHERE id = :id RETURNING {_SUB_COLS}"), params)).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def delete_subscription(subscription_id: str) -> bool:
    if not is_uuid(subscription_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM webhook_subscriptions WHERE id = :id"),
            {"id": subscription_id})
        await s.commit()
        return result.rowcount > 0


async def matching_subscriptions(team_id: str, event_type: str) -> list[dict]:
    """Enabled subscriptions in *team_id* that want *event_type*.

    An empty `events` array means "everything", so the filter is
    "no filter set, or this event is in it".
    """
    async with async_session_factory() as s:
        rows = (await s.execute(
            text(f"""
                SELECT {_SUB_COLS} FROM webhook_subscriptions
                WHERE team_id = :tid AND enabled
                  AND (jsonb_array_length(events) = 0
                       OR events @> to_jsonb(ARRAY[:event]::text[]))
            """),
            {"tid": team_id, "event": event_type})).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------

async def create_delivery(*, subscription_id: str, event_type: str,
                          dataset_id: str | None, payload: dict) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO webhook_deliveries
                    (subscription_id, event_type, dataset_id, payload)
                VALUES (:sid, :event, :did, CAST(:payload AS jsonb))
                RETURNING {_DELIVERY_COLS}
            """),
            {"sid": subscription_id, "event": event_type, "did": dataset_id,
             "payload": json.dumps(payload)})).mappings().one()
        await s.commit()
        return dict(row)


async def record_attempt(delivery_id: str, *, status: str,
                         response_status: int | None,
                         error: str | None) -> dict | None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                UPDATE webhook_deliveries
                SET status = :status, attempts = attempts + 1,
                    response_status = :code, error = :error,
                    delivered_at = CASE WHEN :status = 'delivered'
                                        THEN now() ELSE delivered_at END
                WHERE id = :id RETURNING {_DELIVERY_COLS}
            """),
            {"id": delivery_id, "status": status, "code": response_status,
             "error": error})).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def get_delivery(delivery_id: str) -> dict | None:
    """One delivery row, by id.

    The delivery path resolves its own row directly rather than looking for it
    inside a page of the subscription's history: a backlog can push a queued
    delivery arbitrarily far outside any window, and a row the worker cannot
    find is a row that is never sent and never marked failed.
    """
    if not is_uuid(delivery_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_DELIVERY_COLS} FROM webhook_deliveries WHERE id = :id"),
            {"id": delivery_id})).mappings().first()
        return dict(row) if row else None


async def list_deliveries(subscription_id: str, *, limit: int,
                          offset: int) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        total = (await s.execute(
            text("SELECT COUNT(*) FROM webhook_deliveries "
                 "WHERE subscription_id = :sid"),
            {"sid": subscription_id})).scalar()
        rows = (await s.execute(
            text(f"SELECT {_DELIVERY_COLS} FROM webhook_deliveries "
                 f"WHERE subscription_id = :sid ORDER BY created_at DESC "
                 f"LIMIT :limit OFFSET :offset"),
            {"sid": subscription_id, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total
