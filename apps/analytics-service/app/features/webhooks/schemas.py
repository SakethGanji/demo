"""Request/response models for webhook subscriptions and deliveries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# The lifecycle moments worth telling someone about. An empty subscription
# filter means "all of these".
EVENT_TYPES = (
    "validation.passed",
    "validation.failed",
    "tag.promoted",
    "tag.rolled_back",
    "version.ready",
    "transformation.completed",
    "dataset.published",
)


def _check_http_url(v: str) -> str:
    if not v.startswith(("http://", "https://")):
        raise ValueError("url must be http:// or https://")
    return v


def _check_known_events(v: list[str]) -> list[str]:
    unknown = [e for e in v if e not in EVENT_TYPES]
    if unknown:
        raise ValueError(f"unknown event type(s): {unknown}")
    return v


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2000)
    events: list[str] = Field(
        default_factory=list,
        description=f"Event types to receive; empty means all. One of: "
                    f"{', '.join(EVENT_TYPES)}")
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        return _check_http_url(v)

    @field_validator("events")
    @classmethod
    def _known_events(cls, v: list[str]) -> list[str]:
        return _check_known_events(v)


class WebhookUpdate(BaseModel):
    """A partial update.

    It repeats create's checks rather than relaxing them: nothing downstream
    re-validates, so anything accepted here is persisted verbatim. A retarget
    to a non-http url, or a filter naming an event that does not exist, would
    otherwise return 200 and leave a subscription that looks healthy but can
    never fire again.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    events: list[str] | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        return v if v is None else _check_http_url(v)

    @field_validator("events")
    @classmethod
    def _known_events(cls, v: list[str] | None) -> list[str] | None:
        return v if v is None else _check_known_events(v)


class WebhookOut(BaseModel):
    """A subscription. The secret is never read back after creation."""

    id: str
    team_id: str
    name: str
    url: str
    events: list[str] = Field(default_factory=list)
    enabled: bool
    created_by: str | None = None
    created_at: str
    updated_at: str


class WebhookCreated(WebhookOut):
    """Creation echoes the signing secret — the only time it is ever returned."""

    secret: str = Field(description="Shown once. Store it; it cannot be retrieved.")


class DeliveryOut(BaseModel):
    id: str
    subscription_id: str
    event_type: str
    dataset_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    attempts: int
    response_status: int | None = None
    error: str | None = None
    created_at: str
    delivered_at: str | None = None
