"""``AddMemberRequest`` must name exactly one user, at the schema boundary.

The route now resolves either an internal ``user_id`` or an exact ``email``.
Both fields are optional at the type level, so without a model validator the
two degenerate requests reach the handler and behave badly rather than loudly:
``{}`` would fall through to a ``get_user_by_id(None)`` lookup, and sending
both would silently pick whichever branch the handler happens to test first —
so a console that filled in a stale id alongside a fresh email would add the
wrong person to a team and report success. A 422 at parse time is the only
answer a client can act on.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.auth.permissions import Role
from app.features.auth.schemas import AddMemberRequest


def test_add_member_request_accepts_a_user_id_alone():
    body = AddMemberRequest(user_id="11111111-1111-1111-1111-111111111111")
    assert body.user_id == "11111111-1111-1111-1111-111111111111"
    assert body.email is None
    assert body.role is Role.VIEWER  # unchanged default: adds are least-privilege


def test_add_member_request_accepts_an_email_alone():
    body = AddMemberRequest(email="Colleague@Bank.com", role=Role.EDITOR)
    # EmailStr normalizes the domain but not the local part; the repo lookup is
    # `lower(email) = lower(:e)`, so either way case never decides a match.
    assert body.email == "Colleague@bank.com"
    assert body.user_id is None
    assert body.role is Role.EDITOR


@pytest.mark.parametrize("payload", [
    pytest.param({}, id="neither"),
    pytest.param({"user_id": "11111111-1111-1111-1111-111111111111",
                  "email": "colleague@bank.com"}, id="both"),
])
def test_add_member_request_rejects_ambiguous_identifiers(payload):
    with pytest.raises(ValidationError) as exc:
        AddMemberRequest(**payload)
    assert "exactly one" in str(exc.value)
