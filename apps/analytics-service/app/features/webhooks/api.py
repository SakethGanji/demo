"""Webhook subscription API — team-scoped, not per-dataset.

Subscriptions belong to a team and fire for any dataset in it, so these routes
hang off /webhooks rather than the dataset tree. Managing them is an
administrative act (team:manage), because a subscription sends data outside the
service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from app.api.errors import ProblemException
from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, get_principal, pick_active_team
from app.features.auth.permissions import Permission, role_has

from . import repo, service
from .schemas import (
    DeliveryOut,
    WebhookCreate,
    WebhookCreated,
    WebhookOut,
    WebhookUpdate,
)

router = APIRouter()


def _manageable_teams(principal: Principal) -> list[str] | None:
    """Teams whose webhooks the caller may administer; ``None`` means every team.

    Every single-subscription route below requires team:manage, so the
    collection must agree with them: listing a row that 403s when the UI opens
    it is both a broken drill-down and a needless disclosure of where this
    team ships its data.

    The agreement has to hold in both directions. A superuser may create a
    subscription in a team they do not belong to and may open any subscription
    at all, so scoping their *list* to their own memberships hid exactly the
    rows only they could have made — the subscription existed and fired, with
    no route that would show it back. Hence ``None`` (no team filter) rather
    than the superuser's membership keys.
    """
    if principal.is_superuser:
        return None
    return [team for team, role in (principal.memberships or {}).items()
            if role_has(role, Permission.TEAM_MANAGE)]


def _name_taken(name: str | None) -> ProblemException:
    """The (team_id, name) uniqueness conflict, spelled the same way twice.

    POST reaches it via ``ON CONFLICT DO NOTHING`` and PATCH via a caught
    ``IntegrityError``; a UI branching on ``code`` must not have to care which.
    """
    return ProblemException(
        409, f"A webhook named '{name}' already exists in this team",
        code="webhook-name-taken", name=name)


async def _manageable(principal: Principal, subscription_id: str) -> dict:
    """Fetch a subscription the caller may administer, else 404.

    Cross-team requests 404 rather than 403, matching the existence-hiding
    contract used everywhere else.
    """
    subscription = await repo.get_subscription(subscription_id)
    if not subscription:
        raise HTTPException(404, f"Webhook not found: {subscription_id}")
    if principal.is_superuser:
        return subscription
    role = (principal.memberships or {}).get(subscription["team_id"])
    if role is None:
        raise HTTPException(404, f"Webhook not found: {subscription_id}")
    if not role_has(role, Permission.TEAM_MANAGE):
        raise HTTPException(403, "Managing webhooks requires team:manage")
    return subscription


def _ensure_can_create(principal: Principal, team_id: str) -> None:
    if principal.is_superuser:
        return
    role = (principal.memberships or {}).get(team_id)
    if role is None:
        raise HTTPException(404, f"Team not found: {team_id}")
    if not role_has(role, Permission.TEAM_MANAGE):
        raise HTTPException(403, "Creating webhooks requires team:manage")


@router.post("/webhooks", response_model=WebhookCreated, status_code=201,
             tags=["webhooks"])
async def create_webhook(
    body: WebhookCreate,
    team_id: str | None = Query(default=None, description="Defaults to your active team"),
    x_team_id: str | None = Header(default=None, alias="X-Team-Id",
                                   description="Team to operate in; defaults to your home team"),
    principal: Principal = Depends(get_principal),
) -> WebhookCreated:
    """Subscribe to lifecycle events for a team.

    The response includes the signing secret — **the only time it is ever
    returned**. Store it; every delivery is signed with it.

    The team is the explicit ``team_id`` query parameter when given, else the
    ambient ``X-Team-Id`` context every other collection-scoped write here
    honours. Passing ``None`` for the header — as this route used to — pinned
    creation to the caller's home team, so an admin of two teams who had
    switched context aimed one team's events at the other team's receiver and
    then could not find the subscription where they had made it.
    """
    team = team_id or pick_active_team(principal, x_team_id)
    _ensure_can_create(principal, team)
    secret = service.generate_secret()
    row = await repo.create_subscription(
        team_id=team, name=body.name, url=body.url, secret=secret,
        events=body.events, enabled=body.enabled, created_by=principal.user_id)
    if not row:
        raise _name_taken(body.name)
    return WebhookCreated(**row, secret=secret)


@router.get("/webhooks", response_model=Page[WebhookOut], tags=["webhooks"])
async def list_webhooks(
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[WebhookOut]:
    """Webhook subscriptions across the teams you administer.

    Scoped to team:manage, the same gate the detail route uses, so every row
    listed here can actually be opened.
    """
    rows, total = await repo.list_subscriptions(
        _manageable_teams(principal), limit=page.limit, offset=page.offset)
    return Page.of([WebhookOut(**r) for r in rows], total, page)


@router.get("/webhooks/{subscription_id}", response_model=WebhookOut,
            tags=["webhooks"])
async def get_webhook(
    subscription_id: str, principal: Principal = Depends(get_principal),
) -> WebhookOut:
    """One subscription. The signing secret is never returned."""
    return WebhookOut(**await _manageable(principal, subscription_id))


@router.patch("/webhooks/{subscription_id}", response_model=WebhookOut,
              tags=["webhooks"])
async def update_webhook(
    subscription_id: str, body: WebhookUpdate,
    principal: Principal = Depends(get_principal),
) -> WebhookOut:
    """Update a subscription — retarget it, filter events, or disable it."""
    await _manageable(principal, subscription_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        row = await repo.update_subscription(subscription_id, fields)
    except IntegrityError as exc:
        # (team_id, name) is unique and Postgres has no ON CONFLICT for UPDATE,
        # so the rename collision has to be caught here. Uncaught it surfaced
        # as an opaque 500 that no UI could turn into "that name is taken".
        raise _name_taken(fields.get("name")) from exc
    if not row:
        raise HTTPException(404, f"Webhook not found: {subscription_id}")
    return WebhookOut(**row)


@router.delete("/webhooks/{subscription_id}", status_code=204, tags=["webhooks"])
async def delete_webhook(
    subscription_id: str, principal: Principal = Depends(get_principal),
) -> None:
    """Delete a subscription and its delivery history."""
    await _manageable(principal, subscription_id)
    if not await repo.delete_subscription(subscription_id):
        raise HTTPException(404, f"Webhook not found: {subscription_id}")


@router.get("/webhooks/{subscription_id}/deliveries",
            response_model=Page[DeliveryOut], tags=["webhooks"])
async def list_deliveries(
    subscription_id: str,
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[DeliveryOut]:
    """Delivery attempts for a subscription, newest first.

    This is the debugging surface: each row records the response status or the
    transport error, so a misconfigured receiver is diagnosable without
    reproducing the event.
    """
    await _manageable(principal, subscription_id)
    rows, total = await repo.list_deliveries(
        subscription_id, limit=page.limit, offset=page.offset)
    return Page.of([DeliveryOut(**r) for r in rows], total, page)


@router.post("/webhooks/{subscription_id}/test", response_model=DeliveryOut,
             tags=["webhooks"])
async def test_webhook(
    subscription_id: str, principal: Principal = Depends(get_principal),
) -> DeliveryOut:
    """Send a signed ping, so a receiver can be verified before it matters."""
    subscription = await _manageable(principal, subscription_id)
    payload = service.build_payload(
        "webhook.test", dataset_id=None, team_id=subscription["team_id"],
        data={"message": "This is a test delivery."})
    delivery = await repo.create_delivery(
        subscription_id=subscription_id, event_type="webhook.test",
        dataset_id=None, payload=payload)
    await service.deliver(delivery["id"], subscription_id)
    rows, _ = await repo.list_deliveries(subscription_id, limit=1, offset=0)
    return DeliveryOut(**rows[0])
