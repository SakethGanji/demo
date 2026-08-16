"""Masking exemption is evaluated in the DATASET's team, not across all of them.

The defect these pin: ``may_see_raw`` used to ask "does this caller hold
DATASET_READ_SENSITIVE in *any* team?", OR-ing over every membership::

    def _has_permission(principal, permission):
        return any(role_has(role, permission)
                   for role in (principal.memberships or {}).values())

So a user who was an admin of their own small team — a role a team admin can
hand out — read unmasked PII in *every other team they belonged to*, and could
download those teams' raw files too. Nothing in the response said so; the
values simply arrived unmasked, which is the worst shape for a data-protection
control to fail in.

That contradicted the model the rest of the service enforces, stated in
``app/features/auth/permissions.py``: "A user's authority is evaluated *within
a team*." The team-scoped primitive ``Principal.can(team_id, permission)``
already existed and already handled the superuser bypass; masking just never
used it.

Every test here therefore needs the caller to hold DIFFERENT roles in two
teams. A single-team fixture cannot distinguish the broken behaviour from the
correct one, which is exactly why the original suite passed throughout.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, rid, upload_inline

ROWS = [
    {"id": 1, "email": "ana@example.com", "amount": 100.0},
    {"id": 2, "email": "bob@example.com", "amount": 250.0},
]

MASKED = {"a***@***.com", "b***@***.com"}
RAW = {"ana@example.com", "bob@example.com"}


async def _team(client, admin_id, name):
    r = await client.post("/api/v1/teams", headers=auth(admin_id),
                          json={"name": f"{name}-{rid()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _dataset_with_pii(client, owner_id, team_id):
    """A dataset in *team_id* whose `email` column is declared confidential."""
    ds = (await upload_inline(client, owner_id, json.dumps(ROWS),
                              team_id=team_id))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(owner_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    return ds


async def _admin_elsewhere_viewer_here(client, admin_id, host_team):
    """A user who is ADMIN of their own team and only a VIEWER in *host_team*.

    Returns their user id. The two memberships are the whole point: the old
    check would find DATASET_READ_SENSITIVE via the other team and exempt them
    here.
    """
    uid, other_team = await create_team_user(client, admin_id, "admin")
    r = await client.post(f"/api/v1/teams/{host_team}/members",
                          headers=auth(admin_id),
                          json={"user_id": uid, "role": "viewer"})
    assert r.status_code in (200, 201), r.text

    # Guard the fixture itself: if these two roles are not what we think, the
    # test could pass for the wrong reason.
    me = (await client.get("/api/v1/auth/me", headers=auth(uid))).json()
    roles = {m["team_id"]: m["role"] for m in me["memberships"]}
    assert roles[other_team] == "admin", roles
    assert roles[host_team] == "viewer", roles
    return uid


async def test_being_admin_of_another_team_does_not_unmask_this_teams_pii(
        client, admin_id):
    """The core cross-tenant leak: admin *there* must not mean unmasked *here*."""
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)
    uid = await _admin_elsewhere_viewer_here(client, admin_id, host)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert {row["email"] for row in body["items"]} == MASKED
    assert {row["email"] for row in body["items"]} & RAW == set()


async def test_being_admin_of_another_team_does_not_unlock_this_teams_raw_download(
        client, admin_id):
    """The download gate reads the same predicate, so it leaked the same way.

    Masking rows while leaving /download open would be theatre — the module
    docstring says so — which is why both surfaces have to be team-scoped or
    neither is.
    """
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)
    uid = await _admin_elsewhere_viewer_here(client, admin_id, host)

    r = await client.get(f"/api/v1/datasets/{ds}/download", headers=auth(uid))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sensitive-data-restricted"


async def test_a_structured_query_masks_for_an_admin_of_a_different_team(
        client, admin_id):
    """Every row-returning surface resolves masking separately; pin more than one.

    preview and query reach ``resolve_masking`` by different call paths
    (``explorer/service.py`` lines 101 and 220), so one being fixed does not
    prove the other is.
    """
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)
    uid = await _admin_elsewhere_viewer_here(client, admin_id, host)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(uid),
                          json={"columns": ["email", "amount"]})
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert {row["email"] for row in r.json()["items"]} == MASKED


async def test_an_admin_of_the_owning_team_still_sees_raw_values(client, admin_id):
    """The counterweight: scoping the check must not break the real exemption.

    Without this, "mask everything always" would pass the tests above, and the
    DATASET_READ_SENSITIVE permission would be dead.
    """
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)
    uid, _ = await create_team_user(client, admin_id, "admin", team_id=host)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == []
    assert {row["email"] for row in r.json()["items"]} == RAW

    raw = await client.get(f"/api/v1/datasets/{ds}/download", headers=auth(uid))
    assert raw.status_code == 200, raw.text


async def test_an_editor_of_the_owning_team_is_still_masked(client, admin_id):
    """Editors are deliberately excluded from DATASET_READ_SENSITIVE.

    permissions.py: "the point is that people who work with a dataset every day
    don't routinely see its PII." Pinned here because the fix moved the code
    that decides it.
    """
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)
    uid, _ = await create_team_user(client, admin_id, "editor", team_id=host)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]


async def test_a_platform_superuser_still_sees_raw_values(client, admin_id):
    """Superusers bypass team checks entirely; the fix must preserve that."""
    host = await _team(client, admin_id, "host")
    ds = await _dataset_with_pii(client, admin_id, host)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == []
    assert {row["email"] for row in r.json()["items"]} == RAW
