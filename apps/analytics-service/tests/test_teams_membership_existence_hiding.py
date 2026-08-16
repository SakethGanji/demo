"""Membership *mutations* hide team existence exactly as the listing does.

``GET /teams/{id}/members`` has always answered 404 to an outsider, on the
ARCHITECTURE.md §3 rule that a 403 confirms the resource exists and so leaks
another tenant's inventory. The three mutation routes on the same collection
did not: they went straight to ``can(team_id, TEAM_MANAGE)`` and raised 403,
so an attacker who could not *read* a team could still enumerate teams by
POSTing to them and sorting 403 (real) from 404 (not real). One HTTP verb
undid the property the other verb enforced.

The distinction that must survive is the one the doc actually draws: outside
the team → 404; inside the team but under-roled → 403.
"""

from __future__ import annotations

from conftest import auth, create_team_user

# A syntactically valid uuid that names no team.
GHOST_TEAM = "3f3f3f3f-3f3f-4f3f-8f3f-3f3f3f3f3f3f"


async def test_adding_a_member_to_a_team_you_do_not_belong_to_answers_404_not_403(
    client, admin_id,
):
    """Without this, POST /teams/{id}/members is a team-existence oracle: an
    outsider gets 403 for a team that exists and 404 for one that does not, so
    they can enumerate every tenant on the platform by probing uuids — the
    precise leak the 404-on-read rule was written to close."""
    outsider, _ = await create_team_user(client, admin_id, "owner")
    victim, target_team = await create_team_user(client, admin_id, "owner")

    r = await client.post(f"/api/v1/teams/{target_team}/members",
                          headers=auth(outsider),
                          json={"user_id": outsider, "role": "owner"})
    assert r.status_code == 404, r.text

    # Indistinguishable from a team that does not exist at all.
    ghost = await client.post(f"/api/v1/teams/{GHOST_TEAM}/members",
                              headers=auth(outsider),
                              json={"user_id": outsider, "role": "owner"})
    assert ghost.status_code == 404, ghost.text

    # ...and the rejected write really did not land.
    members = (await client.get(f"/api/v1/teams/{target_team}/members",
                                headers=auth(victim))).json()["items"]
    assert outsider not in {m["user_id"] for m in members}


async def test_patching_a_role_in_a_team_you_do_not_belong_to_answers_404_not_403(
    client, admin_id,
):
    """PATCH leaked the same fact as POST, and additionally leaked membership:
    a 403 on /teams/{tid}/members/{uid} confirmed both that the team exists and
    that the caller had guessed a real target id."""
    outsider, _ = await create_team_user(client, admin_id, "owner")
    member, target_team = await create_team_user(client, admin_id, "editor")

    r = await client.patch(f"/api/v1/teams/{target_team}/members/{member}",
                           headers=auth(outsider), json={"role": "viewer"})
    assert r.status_code == 404, r.text

    ghost = await client.patch(f"/api/v1/teams/{GHOST_TEAM}/members/{member}",
                               headers=auth(outsider), json={"role": "viewer"})
    assert ghost.status_code == 404, ghost.text


async def test_removing_a_member_of_a_team_you_do_not_belong_to_answers_404_not_403(
    client, admin_id,
):
    """DELETE is the highest-value probe of the three — a 403 told an outsider
    the team exists *and* invited them to keep hunting for a role that would
    let the removal through."""
    outsider, _ = await create_team_user(client, admin_id, "owner")
    member, target_team = await create_team_user(client, admin_id, "editor")

    r = await client.request("DELETE",
                             f"/api/v1/teams/{target_team}/members/{member}",
                             headers=auth(outsider))
    assert r.status_code == 404, r.text

    ghost = await client.request("DELETE",
                                 f"/api/v1/teams/{GHOST_TEAM}/members/{member}",
                                 headers=auth(outsider))
    assert ghost.status_code == 404, ghost.text

    # The member is still there: a 404 must mean "refused", not "done".
    members = (await client.get(f"/api/v1/teams/{target_team}/members",
                                headers=auth(member))).json()["items"]
    assert member in {m["user_id"] for m in members}


async def test_a_member_who_lacks_team_manage_still_gets_a_truthful_403(
    client, admin_id,
):
    """The existence-hiding guard must not swallow the in-team case. A viewer
    already knows their own team exists, so downgrading their 403 to 404 would
    be a pure UX regression and would make 'wrong role' indistinguishable from
    'wrong team' for a legitimate admin console."""
    viewer, tid = await create_team_user(client, admin_id, "viewer")
    editor, _ = await create_team_user(client, admin_id, "editor", team_id=tid)

    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(viewer),
                          json={"user_id": editor, "role": "viewer"})
    assert r.status_code == 403, r.text

    r = await client.patch(f"/api/v1/teams/{tid}/members/{editor}",
                           headers=auth(viewer), json={"role": "viewer"})
    assert r.status_code == 403, r.text

    r = await client.request("DELETE", f"/api/v1/teams/{tid}/members/{editor}",
                             headers=auth(viewer))
    assert r.status_code == 403, r.text


async def test_membership_routes_404_on_a_nonexistent_team_even_for_a_superuser(
    client, admin_id,
):
    """A superuser's ``can()`` is unconditionally true, so nothing stopped a
    typo'd team id from reaching the team_members INSERT and dying on a
    foreign-key violation — a 500 with a database error where a 404 belongs.
    The GET was worse than wrong: it answered 200 with an empty page, which
    reads as 'that team exists and is empty'."""
    h = auth(admin_id)

    r = await client.get(f"/api/v1/teams/{GHOST_TEAM}/members", headers=h)
    assert r.status_code == 404, r.text

    r = await client.post(f"/api/v1/teams/{GHOST_TEAM}/members", headers=h,
                          json={"user_id": admin_id, "role": "viewer"})
    assert r.status_code == 404, r.text

    r = await client.patch(f"/api/v1/teams/{GHOST_TEAM}/members/{admin_id}",
                           headers=h, json={"role": "viewer"})
    assert r.status_code == 404, r.text

    r = await client.request("DELETE",
                             f"/api/v1/teams/{GHOST_TEAM}/members/{admin_id}",
                             headers=h)
    assert r.status_code == 404, r.text
