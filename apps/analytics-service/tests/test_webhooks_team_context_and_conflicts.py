"""Which team a webhook lands in, who can see it afterwards, and renames.

Every case here is about a subscription ending up somewhere the caller did not
ask for, or becoming invisible to the person who owns it. None of them is
visible from the single-team happy path in ``test_webhooks.py``, because there
the caller's home team, active team and only membership are all the same team.
"""

from __future__ import annotations

from conftest import DEFAULT_TEAM_ID, auth, create_team_user, rid


async def _new_team(client, admin_id, name=None) -> str:
    r = await client.post("/api/v1/teams", headers=auth(admin_id),
                          json={"name": name or f"t-{rid()}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _subscribe(client, user_id, name, *, team_id=None, headers=None,
                     url="https://example.test/hook"):
    h = dict(headers or {})
    h.update(auth(user_id))
    params = {"team_id": team_id} if team_id else None
    r = await client.post("/api/v1/webhooks", headers=h, params=params,
                          json={"name": name, "url": url, "events": []})
    return r


# --- create: which team does the subscription land in? ------------------------

async def test_creating_a_webhook_honours_the_x_team_id_header(client, admin_id):
    """POST /webhooks must create in the team named by X-Team-Id.

    Every other collection-scoped write in the service (upload, saved objects,
    user creation) resolves its team through ``pick_active_team(principal,
    x_team_id)``. This route ignored the header entirely and always fell back
    to the caller's home team, so an admin of two teams who switched teams in
    the UI silently pointed the other team's lifecycle events — dataset names,
    row counts, publisher identities — at a receiver they had configured for
    this one. The subscription then never appeared in the team they thought
    they were configuring.
    """
    home = await _new_team(client, admin_id)
    other = await _new_team(client, admin_id)
    user, _ = await create_team_user(client, admin_id, "admin", team_id=home)
    r = await client.post(f"/api/v1/teams/{other}/members", headers=auth(admin_id),
                          json={"user_id": user, "role": "admin"})
    assert r.status_code == 201, r.text

    r = await _subscribe(client, user, "cross-team-hook",
                         headers={"X-Team-Id": other})
    assert r.status_code == 201, r.text
    assert r.json()["team_id"] == other

    # And with no header at all it still falls back to the home team.
    r = await _subscribe(client, user, "home-hook")
    assert r.status_code == 201, r.text
    assert r.json()["team_id"] == home


async def test_an_explicit_team_id_query_parameter_still_wins_over_the_header(
        client, admin_id):
    """The query parameter is the explicit request; the header is ambient context.

    Adding X-Team-Id support must not silently retarget callers who already
    name the team in the query string.
    """
    a = await _new_team(client, admin_id)
    b = await _new_team(client, admin_id)

    r = await _subscribe(client, admin_id, "explicit-wins", team_id=a,
                         headers={"X-Team-Id": b})
    assert r.status_code == 201, r.text
    assert r.json()["team_id"] == a


# --- list: superusers -------------------------------------------------------

async def test_a_superuser_lists_webhooks_from_teams_they_do_not_belong_to(
        client, admin_id):
    """The list and the detail route must agree for superusers too.

    A superuser may create a subscription in any team and may open any
    subscription, but the collection was scoped to their own memberships. A
    platform admin who configured a webhook on behalf of another team got 201,
    then an empty list — the subscription existed, fired, and shipped that
    team's data outward with no route that would show it back to them.
    """
    foreign = await _new_team(client, admin_id)
    # The superuser is an owner of any team they create, so drop the membership
    # to model a team they genuinely do not belong to.
    r = await client.delete(f"/api/v1/teams/{foreign}/members/{admin_id}",
                            headers=auth(admin_id))
    assert r.status_code in (204, 400, 409), r.text
    if r.status_code != 204:
        # Last-owner guard: hand ownership over first, then leave.
        other, _ = await create_team_user(client, admin_id, "owner", team_id=foreign)
        r = await client.delete(f"/api/v1/teams/{foreign}/members/{admin_id}",
                                headers=auth(admin_id))
        assert r.status_code == 204, r.text

    created = await _subscribe(client, admin_id, f"foreign-{rid()}", team_id=foreign)
    assert created.status_code == 201, created.text
    created = created.json()
    assert created["team_id"] == foreign

    # The detail route lets the superuser open it...
    r = await client.get(f"/api/v1/webhooks/{created['id']}", headers=auth(admin_id))
    assert r.status_code == 200, r.text

    # ...so the collection has to list it.
    listed = await client.get("/api/v1/webhooks", headers=auth(admin_id),
                              params={"limit": 200})
    assert listed.status_code == 200, listed.text
    assert created["id"] in [w["id"] for w in listed.json()["items"]]


async def test_a_non_superuser_still_only_lists_their_own_manageable_teams(
        client, admin_id):
    """Widening the superuser view must not widen anyone else's.

    The list is the one place a team's outbound delivery urls are enumerable;
    an ordinary admin must still see only the teams they administer.
    """
    mine = await _new_team(client, admin_id)
    theirs = await _new_team(client, admin_id)
    user, _ = await create_team_user(client, admin_id, "admin", team_id=mine)

    ours = (await _subscribe(client, admin_id, "ours", team_id=mine)).json()
    hidden = (await _subscribe(client, admin_id, "hidden", team_id=theirs)).json()

    listed = (await client.get("/api/v1/webhooks", headers=auth(user),
                               params={"limit": 200})).json()
    ids = [w["id"] for w in listed["items"]]
    assert ours["id"] in ids
    assert hidden["id"] not in ids
    assert listed["total"] == len(ids)


# --- rename conflicts ---------------------------------------------------------

async def test_renaming_a_webhook_onto_a_siblings_name_returns_409_not_500(
        client, admin_id):
    """(team_id, name) is unique, and the UPDATE has no ON CONFLICT to lean on.

    POST answers a collision with a typed 409, but PATCH let the unique
    violation escape as an unhandled IntegrityError: the caller got an opaque
    500 that a UI cannot turn into "that name is taken", and — because the
    exception aborted the request mid-transaction — no indication of whether
    the rename had been applied.
    """
    first = (await _subscribe(client, admin_id, f"first-{rid()}",
                              team_id=DEFAULT_TEAM_ID)).json()
    second = (await _subscribe(client, admin_id, f"second-{rid()}",
                               team_id=DEFAULT_TEAM_ID)).json()

    r = await client.patch(f"/api/v1/webhooks/{second['id']}",
                           headers=auth(admin_id), json={"name": first["name"]})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "webhook-name-taken"
    assert first["name"] in body["detail"]

    # The rejected rename left both rows exactly as they were.
    still = await client.get(f"/api/v1/webhooks/{second['id']}",
                             headers=auth(admin_id))
    assert still.json()["name"] == second["name"]


async def test_the_same_name_in_a_different_team_is_not_a_conflict(client, admin_id):
    """Uniqueness is per team, so the 409 must not become a global name lock."""
    other = await _new_team(client, admin_id)
    shared = f"shared-{rid()}"
    a = (await _subscribe(client, admin_id, shared, team_id=DEFAULT_TEAM_ID)).json()
    b = (await _subscribe(client, admin_id, f"tmp-{rid()}", team_id=other)).json()

    r = await client.patch(f"/api/v1/webhooks/{b['id']}", headers=auth(admin_id),
                           json={"name": shared})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == shared == a["name"]


async def test_creating_a_duplicate_name_reports_the_same_machine_code_as_a_rename(
        client, admin_id):
    """One collision, one code — a UI branching on it must not care which verb hit it."""
    name = f"dupe-{rid()}"
    assert (await _subscribe(client, admin_id, name,
                             team_id=DEFAULT_TEAM_ID)).status_code == 201

    r = await _subscribe(client, admin_id, name, team_id=DEFAULT_TEAM_ID)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "webhook-name-taken"
