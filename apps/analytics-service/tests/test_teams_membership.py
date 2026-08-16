"""Team membership management — /api/v1/teams/* (list, members, add, remove).

RBAC: only team:manage can mutate membership, nobody grants above their own
rank, the last owner can never be removed or demoted, and non-members can't
even see that a team exists (404).
"""

from __future__ import annotations

from conftest import auth, create_team_user


async def test_list_my_teams_reflects_memberships(client, admin_id):
    uid, tid_a = await create_team_user(client, admin_id, "editor")
    _, tid_b = await create_team_user(client, admin_id, "viewer")

    # Add the first user to the second team too.
    r = await client.post(f"/api/v1/teams/{tid_b}/members", headers=auth(admin_id),
                          json={"user_id": uid, "role": "viewer"})
    assert r.status_code == 201, r.text

    teams = (await client.get("/api/v1/teams", headers=auth(uid))).json()
    roles = {m["team_id"]: m["role"] for m in teams["items"]}
    assert roles[tid_a] == "editor" and roles[tid_b] == "viewer"


async def test_member_listing_visible_to_members_hidden_from_outsiders(client, admin_id):
    uid, tid = await create_team_user(client, admin_id, "viewer")
    outsider, _ = await create_team_user(client, admin_id, "editor")

    members = (await client.get(f"/api/v1/teams/{tid}/members",
                                headers=auth(uid))).json()
    by_id = {m["user_id"]: m["role"] for m in members["items"]}
    assert by_id[uid] == "viewer"
    assert "owner" in by_id.values()  # the creating superuser owns the team

    r = await client.get(f"/api/v1/teams/{tid}/members", headers=auth(outsider))
    assert r.status_code == 404  # existence hidden
    r = await client.get(
        "/api/v1/teams/11111111-1111-1111-1111-111111111111/members",
        headers=auth(outsider))
    assert r.status_code == 404


async def test_add_member_role_and_rank_rules(client, admin_id):
    h = auth(admin_id)
    team_admin, tid = await create_team_user(client, admin_id, "admin")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=tid)
    stranger, _ = await create_team_user(client, admin_id, "viewer")

    # A team admin can add an existing user at or below their own rank.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(team_admin),
                          json={"user_id": stranger, "role": "editor"})
    assert r.status_code == 201 and r.json()["role"] == "editor"

    # ...but never above it (no self-escalation path).
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(team_admin),
                          json={"user_id": stranger, "role": "owner"})
    assert r.status_code == 403

    # Viewers can't manage membership at all.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=auth(viewer),
                          json={"user_id": stranger, "role": "viewer"})
    assert r.status_code == 403

    # Adding a nonexistent user 404s.
    r = await client.post(f"/api/v1/teams/{tid}/members", headers=h,
                          json={"user_id": "22222222-2222-2222-2222-222222222222",
                                "role": "viewer"})
    assert r.status_code == 404


async def test_remove_member_and_last_owner_guard(client, admin_id):
    h = auth(admin_id)
    editor, tid = await create_team_user(client, admin_id, "editor")

    # Members can be removed; a removed member loses visibility.
    r = await client.request("DELETE", f"/api/v1/teams/{tid}/members/{editor}",
                             headers=h)
    assert r.status_code == 204
    assert (await client.get(f"/api/v1/teams/{tid}/members",
                             headers=auth(editor))).status_code == 404
    # Removing them again → 404 (no longer a member).
    r = await client.request("DELETE", f"/api/v1/teams/{tid}/members/{editor}",
                             headers=h)
    assert r.status_code == 404

    # The creating superuser is the sole owner — irremovable and undemotable.
    r = await client.request("DELETE", f"/api/v1/teams/{tid}/members/{admin_id}",
                             headers=h)
    assert r.status_code == 409
    r = await client.patch(f"/api/v1/teams/{tid}/members/{admin_id}",
                           headers=h, json={"role": "viewer"})
    assert r.status_code == 409

    # With a second owner in place, the first can leave.
    second, _ = await create_team_user(client, admin_id, "viewer", team_id=tid)
    r = await client.patch(f"/api/v1/teams/{tid}/members/{second}", headers=h,
                           json={"role": "owner"})
    assert r.status_code == 200
    r = await client.request("DELETE", f"/api/v1/teams/{tid}/members/{admin_id}",
                             headers=h)
    assert r.status_code == 204


async def test_patch_role_of_non_member_404s(client, admin_id):
    _, tid = await create_team_user(client, admin_id, "viewer")
    stranger, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.patch(f"/api/v1/teams/{tid}/members/{stranger}",
                           headers=auth(admin_id), json={"role": "editor"})
    assert r.status_code == 404
