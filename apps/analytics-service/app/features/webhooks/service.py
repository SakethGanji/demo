"""Emitting and delivering webhook events.

Emission is fire-and-forget from the caller's point of view: a lifecycle event
fans out to one `webhook_delivery` job per matching subscription, and the
existing job worker delivers them. A slow or dead receiver therefore cannot
stall the request that triggered it.

Signing follows the usual shape — `X-Webhook-Signature: sha256=<hmac>` over the
exact request body, plus a timestamp header so receivers can reject replays.
Delivery failures are recorded on the delivery row rather than raised, because
a broken receiver is not the emitting request's problem.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone

from app.shared import worker

from . import repo

logger = logging.getLogger("analytics.webhooks")

DELIVERY_TIMEOUT_S = 10.0
SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
EVENT_HEADER = "X-Webhook-Event"


def generate_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def sign(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 over `timestamp.body`, so a signature can't be replayed
    against a different moment."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                   hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def build_payload(event_type: str, *, dataset_id: str | None,
                  team_id: str, data: dict) -> dict:
    """The wire body. Thin by construction — ids and counts only.

    Nothing here should ever carry dataset cell values: a webhook body lands in
    logs, proxies, and third-party systems well outside this service's
    authorization.
    """
    return {
        "event": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "team_id": team_id,
        "dataset_id": dataset_id,
        "data": data,
    }


async def emit(event_type: str, *, team_id: str, dataset_id: str | None = None,
               data: dict | None = None, inline: bool = False) -> int:
    """Queue *event_type* to every subscription that wants it.

    Returns how many deliveries were queued. Never raises: a webhook problem
    must not fail the operation that triggered the event.
    """
    try:
        subscriptions = await repo.matching_subscriptions(team_id, event_type)
    except Exception:  # noqa: BLE001
        logger.exception("could not resolve webhook subscriptions")
        return 0

    payload = build_payload(event_type, dataset_id=dataset_id,
                            team_id=team_id, data=data or {})
    queued = 0
    for subscription in subscriptions:
        try:
            delivery = await repo.create_delivery(
                subscription_id=subscription["id"], event_type=event_type,
                dataset_id=dataset_id, payload=payload)
            await worker.dispatch(
                "webhook_delivery",
                params={"delivery_id": delivery["id"],
                        "subscription_id": subscription["id"]},
                dataset_id=dataset_id, team_id=team_id, inline=inline)
            queued += 1
        except Exception:  # noqa: BLE001
            logger.exception("could not queue webhook delivery")
    return queued


async def deliver(delivery_id: str, subscription_id: str) -> dict:
    """POST one delivery to its subscription's URL and record the outcome."""
    import httpx

    subscription = await repo.get_subscription(subscription_id)
    secret = await repo.get_secret(subscription_id)
    if not subscription or not secret:
        await repo.record_attempt(delivery_id, status="failed",
                                  response_status=None,
                                  error="subscription no longer exists")
        return {"delivered": False, "reason": "gone"}

    delivery = await repo.get_delivery(delivery_id)
    if delivery is None or delivery["subscription_id"] != subscription_id:
        # Terminal, like every other exit below: a row we cannot resolve (or
        # one that belongs to a different subscription, whose secret would be
        # the wrong one to sign with) must be marked failed, never left
        # 'pending' forever while its job reports success.
        await repo.record_attempt(delivery_id, status="failed",
                                  response_status=None,
                                  error="delivery row missing")
        return {"delivered": False, "reason": "delivery row missing"}

    body = json.dumps(delivery["payload"], separators=(",", ":")).encode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: delivery["event_type"],
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(secret, body, timestamp),
    }

    try:
        async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_S) as client:
            response = await client.post(subscription["url"], content=body,
                                         headers=headers)
        ok = 200 <= response.status_code < 300
        await repo.record_attempt(
            delivery_id, status="delivered" if ok else "failed",
            response_status=response.status_code,
            error=None if ok else f"receiver returned {response.status_code}")
        return {"delivered": ok, "response_status": response.status_code}
    except Exception as exc:  # noqa: BLE001 — recorded, never raised
        await repo.record_attempt(delivery_id, status="failed",
                                  response_status=None,
                                  error=f"{type(exc).__name__}: {exc}")
        return {"delivered": False, "reason": str(exc)}


async def _handle_delivery(job: dict) -> dict:
    """Registered `webhook_delivery` handler (worker loop AND inline)."""
    params = job.get("parameters") or {}
    return await deliver(params["delivery_id"], params["subscription_id"])


worker.register_handler("webhook_delivery", _handle_delivery)
