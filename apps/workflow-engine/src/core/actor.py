"""Who is acting (Stage 5, DESIGN-STAGE5-UIS section 8.6).

Every place that records a person (``decided_by``, chat ``user_label``) goes through :func:`current_actor`, so adding
real identity later changes this one function, not the endpoints. Stage 5 has no login: the actor is the name the person
typed (``kind="typed"``, not verified). ``X-User-Id`` (the optional header of ``core.identity``) is used only when nothing
was typed, and is likewise never used for authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Request

MAX_LABEL = 200


@dataclass(frozen=True)
class Actor:
    id: str | None
    label: str
    kind: Literal["typed", "user", "system"]


def current_actor(request: Request | None = None, typed_name: str | None = None) -> Actor:
    """The actor of a request. ``typed_name`` is the name in the request body; its label is trimmed and capped."""
    typed = (typed_name or "").strip()
    if typed:
        return Actor(id=None, label=typed[:MAX_LABEL], kind="typed")
    header = ((request.headers.get("x-user-id") or "").strip() if request is not None else "")[:255]
    if header:
        return Actor(id=header, label=header[:MAX_LABEL], kind="user")
    return Actor(id=None, label="", kind="typed")


SYSTEM_TIMEOUT = Actor(id=None, label="system:timeout", kind="system")
