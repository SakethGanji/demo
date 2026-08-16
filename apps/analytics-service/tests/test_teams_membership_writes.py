"""Every membership *write* path — not just PATCH/DELETE — protects a team.

``POST /teams/{id}/members`` is backed by an ``ON CONFLICT DO UPDATE`` upsert,
so it is a role-change route wearing an "add" name. The guards were originally
written only into PATCH and DELETE, which left two production holes this file
pins shut:

1. Orphaning. Any team admin could POST the sole owner back with
   ``role=viewer`` and get a cheerful 201. The team then has nobody holding
   team:delete, and nobody can grant ``owner`` back either — you cannot grant
   above your own rank — so the team is unrecoverable without a platform
   superuser. It is also silent: the caller sees a success.

2. Unreachability. The route accepted an opaque ``user_id`` only, and no
   endpoint turns a person into an id unless they already share a team with
   the caller (``GET /teams/{id}/members`` 404s otherwise, and
   ``POST /auth/users`` dead-ends on a 409 that carries no id). The everyday
   "add a colleague from another team" flow was therefore unbuildable by an
   admin console, which is exactly why the gap survived: the test suite only
   ever minted brand-new users.
"""

from __future__ import annotations

from conftest import auth, create_team_user


async def test_add_member_cannot_demote_the_last_owner(client, admin_id):
    """A POST that rewrites the sole owner's role must 409, like the PATCH does."""
    team_admin, tid = await create_team_user(client, admin_id, "admin")

    # admin_id (the creating superuser) is the team's only owner.
    members = (await client.get(f"/api/v1/teams/{tid}/members",
                                headers=auth(admin_id))).json()["items"]
    owners = [m["user_id"] for m in members if m["role"] == "owner"]
    assert owners == [admin_id]

    # A plain team admin holds team:manage and may grant 'viewer' (below their
    # own rank), so nothing but the last-owner guard stands in the way.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(team_admin),
                          json={"user_id": admin_id, "role": "viewer"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "last-owner"

    # ...and the superuser can't do it to themselves either.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(admin_id),
                          json={"user_id": admin_id, "role": "viewer"})
    assert r.status_code == 409, r.text

    # The role on disk is untouched — a rejected write must not half-apply.
    members = (await client.get(f"/api/v1/teams/{tid}/members",
                                headers=auth(admin_id))).json()["items"]
    assert {m["user_id"]: m["role"] for m in members}[admin_id] == "owner"


async def test_add_member_still_allows_writes_that_keep_an_owner(client, admin_id):
    """The guard must not over-fire: re-POSTing an owner as owner, or demoting
    an owner while another owner remains, are both legitimate."""
    h = auth(admin_id)
    second, tid = await create_team_user(client, admin_id, "viewer")

    # Re-asserting the sole owner's own role is a no-op, not a demotion.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=h,
                          json={"user_id": admin_id, "role": "owner"})
    assert r.status_code == 201, r.text

    # With a second owner in place, demoting the first via POST is allowed.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=h,
                          json={"user_id": second, "role": "owner"})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=h,
                          json={"user_id": admin_id, "role": "viewer"})
    assert r.status_code == 201, r.text
    members = (await client.get(f"/api/v1/teams/{tid}/members", headers=h)).json()["items"]
    assert {m["user_id"]: m["role"] for m in members}[admin_id] == "viewer"


async def test_add_member_accepts_email_for_a_user_outside_the_callers_teams(
    client, admin_id,
):
    """An owner can add a colleague they can only name by email, not by id."""
    colleague, other_team = await create_team_user(client, admin_id, "editor")
    colleague_email = (await client.get("/api/v1/auth/me",
                                        headers=auth(colleague))).json()["user"]["email"]

    owner, tid = await create_team_user(client, admin_id, "owner")

    # The premise: this owner genuinely cannot discover the colleague's id.
    # Their team is invisible, so there is no id to put in `user_id`.
    assert (await client.get(f"/api/v1/teams/{other_team}/members",
                             headers=auth(owner))).status_code == 404

    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(owner),
                          json={"email": colleague_email, "role": "editor"})
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == colleague
    assert r.json()["role"] == "editor"

    members = (await client.get(f"/api/v1/teams/{tid}/members",
                                headers=auth(owner))).json()["items"]
    assert {m["user_id"]: m["role"] for m in members}[colleague] == "editor"


async def test_add_member_by_email_keeps_the_existing_404_and_rbac_shape(
    client, admin_id,
):
    """The email path must not become a softer door than the id path: an
    unknown address 404s like an unknown id, and it is still gated on
    team:manage — otherwise it would be an account-probe for any viewer.

    Which refusal you get follows ARCHITECTURE.md §3 and not the presence of
    an email: inside the team but under-roled is a truthful 403, while a team
    you are not in is a 404 that hides its existence.
    """
    viewer, tid = await create_team_user(client, admin_id, "viewer")

    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(admin_id),
                          json={"email": "nobody-here@bank.com", "role": "viewer"})
    assert r.status_code == 404, r.text

    outsider_email = (await client.get("/api/v1/auth/me",
                                       headers=auth(viewer))).json()["user"]["email"]

    # In their own team, the viewer is refused for the honest reason: no
    # team:manage. The email is never resolved, so this is not a probe.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(viewer),
                          json={"email": outsider_email, "role": "viewer"})
    assert r.status_code == 403, r.text

    # Against a team they are not in, the same request must not confirm that
    # the team exists — 403 here would make the route a team-enumeration
    # oracle that GET /teams/{id}/members already refuses to be.
    _, other_tid = await create_team_user(client, admin_id, "viewer")
    r = await client.post(f"/api/v1/teams/{other_tid}/members", headers=auth(viewer),
                          json={"email": outsider_email, "role": "viewer"})
    assert r.status_code == 404, r.text

    # Naming a user both ways at once is a client bug, not a silent preference.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(admin_id),
                          json={"user_id": viewer, "email": outsider_email,
                                "role": "viewer"})
    assert r.status_code == 422, r.text
