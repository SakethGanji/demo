"""Platform journeys — the admin/settings/ops screens, driven as a UI drives them.

House model: tests/test_e2e_journeys.py. Every step is a real HTTP call in the
order a screen makes it, and step N asserts something that is only true BECAUSE
of step N-1. State (team ids, user ids, job ids, subscription ids, signing
secrets, request ids) flows forward exactly as it would through a browser
session — nothing is read out of the database or the service layer.

The screens covered here are the ones that hang off the platform surface rather
than off a dataset:

1. Admin console      — provision a team, promote a deputy, hand over ownership.
2. First-run / empty  — a user with no team must not be led into a dead end.
3. Webhook settings   — configure, watch it fail, repair it, verify, delete.
4. Jobs console       — poll async work from pending to completed, filter, page.
5. Audit console      — page the trail and correlate a request id from a write.
6. Cross-origin shell — preflight, read, download, and errors from a browser.
7. Error interceptor  — one envelope for every platform route.
8. Revocation         — removal collapses every platform surface at once.

Per-endpoint behaviour is already pinned in test_teams_membership*.py,
test_webhooks*.py, test_ops.py and friends; the value here is the ordering
constraints, the ids that must flow between steps, and the RBAC boundaries a
screen renders differently for viewer / editor / admin / owner / outsider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

import pytest

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    rid,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"
ORIGIN = "https://console.example.com"

ROWS = [{"order_id": 1, "region": "EU", "amount": 100.0},
        {"order_id": 2, "region": "US", "amount": 50.0}]

DEAD_URL = "https://dead.example.test/hook"
LIVE_URL = "https://live.example.test/hook"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def problem(response) -> dict:
    """Assert the RFC7807 envelope and hand back the body a UI would branch on."""
    assert response.headers["content-type"].startswith(PROBLEM), response.text
    body = response.json()
    assert set(body) >= {"type", "title", "status", "detail", "instance", "code"}
    assert body["status"] == response.status_code
    return body


async def _new_user(client, actor_id, team_id, name) -> str:
    """POST /auth/users the way an admin console's 'invite' dialog does."""
    r = await client.post("/api/v1/auth/users",
                          headers={**auth(actor_id), "X-Team-Id": team_id},
                          json={"email": f"{name}-{rid()}@bank.com", "name": name.title()})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def receiver(monkeypatch):
    """A fake HTTP receiver: LIVE_URL answers 200, anything else is unreachable.

    The outbound POST is the only faked hop — subscription storage, the event
    fan-out, the worker hand-off, the signature and the delivery record are all
    real, so "repair the url and it starts working" is a genuine state change
    and not a flipped flag.
    """
    sent: list[dict] = []

    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            sent.append({"url": url, "body": content, "headers": headers or {}})
            if url != LIVE_URL:
                raise ConnectionError("receiver unreachable")
            return _Response(200)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return sent


# ---------------------------------------------------------------------------
# 1. Admin console
# ---------------------------------------------------------------------------


async def test_journey_an_admin_console_provisions_a_team_promotes_a_deputy_and_hands_over_ownership(
        client, admin_id):
    """SCREEN: Settings → Team → Members, driven by the team's founder.

    This is the only safe path to hand a team over, and it is order-dependent:
    the successor must be promoted to owner BEFORE the founder leaves. If the
    ordering constraint regresses, an admin who clicks "Leave team" first gets
    a 409 they cannot resolve through the API — there is no route that lets a
    non-owner grant `owner`. The tail of the journey is what the founder's own
    navigation must do afterwards: the team disappears from the switcher and
    its member list stops existing, not merely stops being permitted.
    """
    h_admin = auth(admin_id)

    # ---- 1. A platform admin seats a founder in a starter team ----
    t0 = (await client.post("/api/v1/teams", headers=h_admin,
                            json={"name": f"starter-{rid()}"})).json()["id"]
    founder = await _new_user(client, admin_id, t0, "founder")
    colleague_email = f"colleague-{rid()}@bank.com"
    r = await client.post("/api/v1/auth/users", headers={**h_admin, "X-Team-Id": t0},
                          json={"email": colleague_email, "name": "Colleague"})
    assert r.status_code == 201, r.text
    colleague = r.json()["id"]
    assert r.json()["is_superuser"] is False  # the console must not mint superusers

    # ---- 2. The founder opens their own team — they own what they create ----
    h_founder = auth(founder)
    r = await client.post("/api/v1/teams", headers=h_founder,
                          json={"name": f"payments-{rid()}"})
    assert r.status_code == 201, r.text
    payments = r.json()["id"]

    me = (await client.get("/api/v1/auth/me", headers=h_founder)).json()
    roles = {m["team_id"]: m["role"] for m in me["memberships"]}
    assert roles[payments] == "owner" and roles[t0] == "viewer"
    assert me["user"]["id"] == founder  # the avatar menu renders from here

    # ---- 3. The founder seats two people IN THE NEW TEAM (X-Team-Id context) ----
    deputy = await _new_user(client, founder, payments, "deputy")
    analyst = await _new_user(client, founder, payments, "analyst")

    members = (await client.get(f"/api/v1/teams/{payments}/members",
                                headers=h_founder)).json()
    assert members["total"] == 3
    by_id = {m["user_id"]: m for m in members["items"]}
    assert by_id[founder]["role"] == "owner"
    assert by_id[deputy]["role"] == by_id[analyst]["role"] == "viewer"
    assert by_id[deputy]["email"] and by_id[deputy]["name"] == "Deputy"

    # ---- 4. "Leave team" BEFORE handover is refused with a code, not prose ----
    r = await client.delete(f"/api/v1/teams/{payments}/members/{founder}",
                            headers=h_founder)
    assert r.status_code == 409
    assert problem(r)["code"] == "last-owner"
    # ...and the refusal really is a refusal: the roster is untouched.
    assert (await client.get(f"/api/v1/teams/{payments}/members",
                             headers=h_founder)).json()["total"] == 3

    # ---- 5. Promote the deputy to admin; their own /auth/me reflects it ----
    r = await client.patch(f"/api/v1/teams/{payments}/members/{deputy}",
                           headers=h_founder, json={"role": "admin"})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": deputy, "email": by_id[deputy]["email"],
                        "name": "Deputy", "role": "admin"}

    h_deputy = auth(deputy)
    me_deputy = (await client.get("/api/v1/auth/me", headers=h_deputy)).json()
    assert {m["team_id"]: m["role"] for m in me_deputy["memberships"]}[payments] == "admin"

    # ---- 6. The deputy can now run the roster, but not exceed their own rank ----
    r = await client.post(f"/api/v1/teams/{payments}/members", headers=h_deputy,
                          json={"email": colleague_email, "role": "editor"})
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == colleague and r.json()["role"] == "editor"

    r = await client.patch(f"/api/v1/teams/{payments}/members/{analyst}",
                           headers=h_deputy, json={"role": "owner"})
    assert r.status_code == 403
    assert "own" in problem(r)["detail"]  # "Cannot grant role 'owner' above your own"
    # The rejected grant left the analyst where they were.
    roster = (await client.get(f"/api/v1/teams/{payments}/members",
                               headers=h_deputy)).json()["items"]
    assert {m["user_id"]: m["role"] for m in roster}[analyst] == "viewer"
    assert len(roster) == 4  # the colleague really did join

    # ---- 7. Handover: the founder grants owner, and only then can leave ----
    r = await client.patch(f"/api/v1/teams/{payments}/members/{deputy}",
                           headers=h_founder, json={"role": "owner"})
    assert r.status_code == 200 and r.json()["role"] == "owner"

    r = await client.delete(f"/api/v1/teams/{payments}/members/{founder}",
                            headers=h_founder)
    assert r.status_code == 204, r.text

    # ---- 8. The founder's navigation collapses; the deputy's does not ----
    switcher = (await client.get("/api/v1/teams", headers=h_founder)).json()
    assert [m["team_id"] for m in switcher["items"]] == [t0]
    assert switcher["total"] == 1

    r = await client.get(f"/api/v1/teams/{payments}/members", headers=h_founder)
    assert r.status_code == 404          # existence hidden, not 403
    assert problem(r)["code"] == "not_found"

    after = (await client.get(f"/api/v1/teams/{payments}/members",
                              headers=h_deputy)).json()
    assert after["total"] == 3 and founder not in {m["user_id"] for m in after["items"]}
    assert {m["user_id"]: m["role"] for m in after["items"]}[deputy] == "owner"


# ---------------------------------------------------------------------------
# 2. First-run / empty state
# ---------------------------------------------------------------------------


async def test_journey_a_new_user_with_no_team_is_never_led_into_a_dead_end(
        client, admin_id):
    """SCREEN: the landing shell for a user whose last membership was revoked.

    Reachable in production the moment DELETE /teams/{id}/members/{me} runs.
    Every list the shell mounts must answer an empty page rather than fail, and
    the one escape hatch (create your own team) must actually restore the
    ability to work — otherwise the account is bricked with no in-app remedy.
    """
    h_admin = auth(admin_id)
    lonely, team = await create_team_user(client, admin_id, "viewer")

    # A membership exists first, so the empty state below is a *transition*.
    before = (await client.get("/api/v1/teams", headers=auth(lonely))).json()
    assert [m["team_id"] for m in before["items"]] == [team]

    assert (await client.delete(f"/api/v1/teams/{team}/members/{lonely}",
                                headers=h_admin)).status_code == 204

    # ---- 1. Everything the shell mounts still answers, and answers empty ----
    h = auth(lonely)
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    assert me["memberships"] == []
    assert me["user"]["status"] == "active"   # revoked from a team, not disabled

    for path in ("/api/v1/teams", "/api/v1/jobs", "/api/v1/webhooks",
                 "/api/v1/datasets"):
        r = await client.get(path, headers=h)
        assert r.status_code == 200, f"{path}: {r.text}"
        body = r.json()
        assert body["items"] == [] and body["total"] == 0, path
        assert body["offset"] == 0 and "limit" in body, path

    # The audit console is not theirs, and says so with a code (403, not 404 —
    # they are authenticated, they simply are not a platform admin).
    r = await client.get("/api/v1/audit", headers=h)
    assert r.status_code == 403 and problem(r)["code"] == "forbidden"

    # ---- 2. The dead end: no team context means no write is possible ----
    r = await client.post("/api/v1/upload", headers=h,
                          data={"data": json.dumps(ROWS)})
    assert r.status_code == 400
    assert "team" in problem(r)["detail"].lower()

    # ---- 3. The escape hatch, and the proof it worked ----
    r = await client.post("/api/v1/teams", headers=h, json={"name": f"solo-{rid()}"})
    assert r.status_code == 201, r.text
    solo = r.json()["id"]

    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    assert [(m["team_id"], m["role"]) for m in me["memberships"]] == [(solo, "owner")]

    switcher = (await client.get("/api/v1/teams", headers=h)).json()
    assert switcher["total"] == 1 and switcher["items"][0]["team_name"].startswith("solo-")

    # The same upload that dead-ended in step 2 now succeeds, with no header
    # change: the single new membership resolves the team context.
    body = await upload_inline(client, lonely, json.dumps(ROWS), team_id=None)
    ds = body["dataset_id"]
    listing = (await client.get("/api/v1/datasets", headers=h)).json()
    assert [d["id"] for d in listing["items"]] == [ds] and listing["total"] == 1

    # ...and the webhook settings screen, which was empty above, now accepts a
    # subscription: owning a team is what unlocked every collection-scoped write.
    r = await client.post("/api/v1/webhooks", headers=h,
                          json={"name": "first-hook", "url": LIVE_URL})
    assert r.status_code == 201, r.text
    assert r.json()["team_id"] == solo
    assert (await client.get("/api/v1/webhooks", headers=h)).json()["total"] == 1

    # Whatever the jobs screen shows, every row on it must belong to the one
    # team this user is in — the screen has no other scope to fall back on.
    jobs_page = (await client.get("/api/v1/jobs", headers=h)).json()
    assert all(j["team_id"] == solo for j in jobs_page["items"])


# ---------------------------------------------------------------------------
# 3. Webhook settings screen
# ---------------------------------------------------------------------------


async def test_journey_an_operator_configures_a_webhook_watches_it_fail_and_repairs_it(
        client, admin_id, receiver):
    """SCREEN: Settings → Integrations → Webhooks, the whole configure→repair arc.

    The UI shows the signing secret once, a status column driven by the last
    delivery, and a diagnostics drawer of attempts. What breaks if this
    regresses: an operator retargets a dead receiver and has no way to confirm
    the repair, or the secret shown at creation stops being the one the
    deliveries are actually signed with — in which case every receiver that
    verifies signatures rejects the traffic and nothing in the UI explains it.
    """
    ops, team = await create_team_user(client, admin_id, "admin", team_id=None)
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    h = auth(ops)

    # ---- 1. Create: the secret is shown exactly once ----
    r = await client.post("/api/v1/webhooks", headers={**h, "X-Team-Id": team},
                          json={"name": "prod-receiver", "url": DEAD_URL,
                                "events": [], "enabled": True})
    assert r.status_code == 201, r.text
    created = r.json()
    hook, secret = created["id"], created["secret"]
    assert secret.startswith("whsec_") and created["team_id"] == team

    # ---- 2. The settings table, then the detail pane it drills into ----
    table = (await client.get("/api/v1/webhooks", headers=h)).json()
    assert [w["id"] for w in table["items"]] == [hook] and table["total"] == 1
    assert "secret" not in table["items"][0]

    detail = await client.get(f"/api/v1/webhooks/{hook}", headers=h)
    assert detail.status_code == 200
    assert detail.json()["url"] == DEAD_URL and detail.json()["enabled"] is True
    assert "secret" not in detail.json()

    # A viewer cannot open this screen at all — and the list they get back is
    # empty rather than showing a row that 403s when clicked.
    assert (await client.get("/api/v1/webhooks", headers=auth(viewer))).json()["items"] == []
    r = await client.get(f"/api/v1/webhooks/{hook}", headers=auth(viewer))
    assert r.status_code == 403 and problem(r)["code"] == "forbidden"

    # ---- 3. "Send test" against the dead receiver: recorded, never raised ----
    r = await client.post(f"/api/v1/webhooks/{hook}/test", headers=h)
    assert r.status_code == 200, r.text          # the API call itself succeeds
    ping = r.json()
    assert ping["status"] == "failed" and ping["event_type"] == "webhook.test"
    assert "ConnectionError" in ping["error"] and ping["response_status"] is None

    # ---- 4. The diagnostics drawer explains it without reproducing anything --
    drawer = (await client.get(f"/api/v1/webhooks/{hook}/deliveries",
                               headers=h)).json()
    assert drawer["total"] == 1
    assert drawer["items"][0]["id"] == ping["id"]
    assert drawer["items"][0]["status"] == "failed"
    assert drawer["items"][0]["attempts"] >= 1
    assert receiver[-1]["url"] == DEAD_URL       # it really did try the bad url

    # ---- 5. Repair: retarget. A bad retarget is refused first ----
    r = await client.patch(f"/api/v1/webhooks/{hook}", headers=h,
                           json={"url": "file:///etc/passwd"})
    assert r.status_code == 422 and problem(r)["errors"]
    assert (await client.get(f"/api/v1/webhooks/{hook}",
                             headers=h)).json()["url"] == DEAD_URL  # unchanged

    r = await client.patch(f"/api/v1/webhooks/{hook}", headers=h,
                           json={"url": LIVE_URL})
    assert r.status_code == 200 and r.json()["url"] == LIVE_URL

    # ---- 6. Re-test: the SAME subscription now succeeds ----
    r = await client.post(f"/api/v1/webhooks/{hook}/test", headers=h)
    assert r.json()["status"] == "delivered" and r.json()["response_status"] == 200

    drawer = (await client.get(f"/api/v1/webhooks/{hook}/deliveries",
                               headers=h)).json()
    assert drawer["total"] == 2
    assert [d["status"] for d in drawer["items"]] == ["delivered", "failed"]  # newest first

    # ---- 7. Narrow to one event, then cause that event for real ----
    r = await client.patch(f"/api/v1/webhooks/{hook}", headers=h,
                           json={"events": ["tag.promoted"], "enabled": True})
    assert r.status_code == 200 and r.json()["events"] == ["tag.promoted"]

    ds = (await upload_inline(client, ops, json.dumps(ROWS),
                              team_id=team))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                          headers=h, json={"version_number": 1, "reason": "go-live"})
    assert r.status_code == 200, r.text

    from app.shared import worker
    assert await worker.run_pending_jobs_once() >= 1

    # ---- 8. The real event landed, carrying ids only, signed with the secret
    #         handed back in step 1 ----
    drawer = (await client.get(f"/api/v1/webhooks/{hook}/deliveries",
                               headers=h)).json()
    assert drawer["total"] == 3
    event = drawer["items"][0]
    assert event["event_type"] == "tag.promoted" and event["status"] == "delivered"
    assert event["dataset_id"] == ds
    assert event["payload"]["data"] == {"tag": "production", "from_version_number": None,
                                        "to_version_number": 1, "reason": "go-live"}

    sent = receiver[-1]
    assert sent["url"] == LIVE_URL
    timestamp = sent["headers"]["X-Webhook-Timestamp"]
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + sent["body"],
                        hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Webhook-Signature"] == f"sha256={expected}"
    assert sent["headers"]["X-Webhook-Event"] == "tag.promoted"

    # ---- 9. Delete takes the history with it ----
    assert (await client.delete(f"/api/v1/webhooks/{hook}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/webhooks/{hook}", headers=h)).status_code == 404
    r = await client.get(f"/api/v1/webhooks/{hook}/deliveries", headers=h)
    assert r.status_code == 404 and problem(r)["code"] == "not_found"
    assert (await client.get("/api/v1/webhooks", headers=h)).json()["total"] == 0

    # A second promote after the delete must not resurrect anything.
    await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                      headers=h, json={"version_number": 1})
    await worker.run_pending_jobs_once()
    assert (await client.get("/api/v1/webhooks", headers=h)).json()["items"] == []


# ---------------------------------------------------------------------------
# 4. Jobs console
# ---------------------------------------------------------------------------


async def test_journey_a_progress_modal_polls_an_async_job_from_pending_to_completed(
        client, admin_id, tmp_path):
    """SCREEN: the jobs console + the progress modal an async action opens.

    The modal's only handle on enqueued work is the job_id in the action's
    response; it then polls GET /jobs/{id} until a terminal status. What breaks
    if this regresses: the modal spins forever (no id to poll), or the list and
    the detail disagree about the same job so the console shows one status and
    the modal another, or the timestamps stop parsing and the "started 3s ago"
    label goes blank.
    """
    from app.shared import worker

    ops, team = await create_team_user(client, admin_id, "admin")
    h = auth(ops)

    # ---- 1. A synchronous import leaves a completed row on the console ----
    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)
    ds = (await upload_file(client, ops, path, name="crm.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]

    console = (await client.get("/api/v1/jobs", headers=auth(admin_id),
                                params={"job_type": "import"})).json()
    assert console["limit"] == 50 and console["offset"] == 0
    imported = next(j for j in console["items"] if j["dataset_id"] == ds)
    assert imported["job_type"] == "import" and imported["status"] == "completed"
    assert imported["progress"] == 100 and imported["result"]["row_count"] > 0
    assert imported["dataset_version_id"]     # the modal links back to the version

    # ---- 2. An async action hands back the only handle that exists ----
    r = await client.post(f"/api/v1/datasets/{ds}/relationships/suggest?sync=false",
                          headers=h)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert job_id, "an async action with no job_id leaves the modal nothing to poll"

    # ---- 3. First poll: pending, nothing has run ----
    pending = (await client.get(f"/api/v1/jobs/{job_id}", headers=h)).json()
    assert pending["status"] == "pending"
    assert pending["job_type"] == "relationship_discovery"
    assert pending["dataset_id"] == ds and pending["team_id"] == team
    assert pending["started_at"] is None and pending["completed_at"] is None
    assert pending["result"] is None and pending["error"] is None
    assert datetime.fromisoformat(pending["created_at"])  # strict ISO-8601

    # The console's "in progress" tab finds the same row by filter...
    inflight = (await client.get("/api/v1/jobs", headers=h, params={
        "status": "pending", "job_type": "relationship_discovery"})).json()
    assert [j["id"] for j in inflight["items"]] == [job_id]
    # ...and the list and the detail agree field for field, so switching
    # between them cannot change what the user sees.
    assert inflight["items"][0] == pending

    # ---- 4. The worker runs; the SAME id is how the modal learns the outcome --
    assert await worker.run_pending_jobs_once() >= 1

    done = (await client.get(f"/api/v1/jobs/{job_id}", headers=h)).json()
    assert done["status"] == "completed" and done["progress"] == 100
    assert done["result"]["suggested"] >= 1
    assert done["created_at"] == pending["created_at"]      # same row, not a new one
    started = datetime.fromisoformat(done["started_at"])
    completed = datetime.fromisoformat(done["completed_at"])
    assert datetime.fromisoformat(done["created_at"]) <= started <= completed

    # ---- 5. The tabs move: it left "pending" and joined "completed" ----
    assert (await client.get("/api/v1/jobs", headers=h,
                             params={"status": "pending"})).json()["total"] == 0
    finished = (await client.get("/api/v1/jobs", headers=h, params={
        "status": "completed", "job_type": "relationship_discovery"})).json()
    assert [j["id"] for j in finished["items"]] == [job_id]
    assert finished["items"][0] == done

    # ---- 6. Paging the console: a second run, then one job per page ----
    r = await client.post(f"/api/v1/datasets/{ds}/relationships/suggest?sync=false",
                          headers=h)
    second = r.json()["job_id"]
    assert second != job_id
    assert await worker.run_pending_jobs_once() >= 1

    scope = {"job_type": "relationship_discovery"}
    page1 = (await client.get("/api/v1/jobs", headers=h,
                              params={**scope, "limit": 1, "offset": 0})).json()
    page2 = (await client.get("/api/v1/jobs", headers=h,
                              params={**scope, "limit": 1, "offset": 1})).json()
    assert page1["total"] == page2["total"] == 2
    assert page1["limit"] == 1 and page2["offset"] == 1
    assert len(page1["items"]) == len(page2["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert {page1["items"][0]["id"], page2["items"][0]["id"]} == {job_id, second}
    assert page1["items"][0]["id"] == second      # newest first

    # ---- 7. Error branches the console must render ----
    r = await client.get("/api/v1/jobs/not-a-uuid", headers=h)
    assert r.status_code == 404 and problem(r)["code"] == "not_found"
    r = await client.get("/api/v1/jobs/00000000-0000-0000-0000-0000000000ff", headers=h)
    assert r.status_code == 404

    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(outsider))
    assert r.status_code == 404          # cross-team existence stays hidden
    assert (await client.get("/api/v1/jobs", headers=auth(outsider),
                             params=scope)).json()["total"] == 0

    # An unknown status filter is an empty page, not a 422 — the console must
    # not treat "no such state" and "nothing in this state" as the same thing.
    unknown = await client.get("/api/v1/jobs", headers=h, params={"status": "bogus"})
    assert unknown.status_code == 200 and unknown.json()["items"] == []

    # ---- 8. The upload-status view of the same version, by version id ----
    up = await upload_file(client, ops, path, name="crm.xlsx",
                           content_type=XLSX_MIME, dataset_id=ds, team_id=team)
    status = (await client.get(f"/api/v1/upload/status/{up['version_id']}",
                               headers=h)).json()
    assert status["status"] == "complete" and status["dataset_id"] == ds
    assert status["row_count"] > 0
    r = await client.get("/api/v1/upload/status/00000000-0000-0000-0000-0000000000ff",
                         headers=h)
    assert r.status_code == 404 and problem(r)["code"] == "not_found"


# ---------------------------------------------------------------------------
# 5. Audit console
# ---------------------------------------------------------------------------


async def test_journey_a_compliance_officer_pages_through_the_audit_trail(
        client, admin_id):
    """SCREEN: the platform audit console (superuser only) + support correlation.

    Its only navigation is limit/offset, so consecutive pages must neither
    overlap nor drop rows; and the row a support ticket quotes is found by the
    X-Request-Id the UI read off the failing response. If the trail stops
    carrying request_id, or the attribution columns go back to NULL, the
    console ships columns that can never populate and tickets stop being
    traceable.
    """
    h = auth(admin_id)

    # ---- 1. A burst of governance actions, all in one moment ----
    team = (await client.post("/api/v1/teams", headers=h,
                              json={"name": f"gov-{rid()}"})).json()["id"]
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS),
                              team_id=team))["dataset_id"]

    promote = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                                headers=h, json={"version_number": 1,
                                                 "reason": "quarter close"})
    assert promote.status_code == 200, promote.text
    promote_rid = promote.headers["X-Request-Id"]      # what a UI shows in a toast

    deleted = await client.delete(f"/api/v1/datasets/{ds}", headers=h)
    assert deleted.status_code == 200, deleted.text

    # ---- 2. Page the trail: two pages, disjoint, strictly newest-first ----
    p1 = (await client.get("/api/v1/audit", headers=h,
                           params={"limit": 2, "offset": 0})).json()
    p2 = (await client.get("/api/v1/audit", headers=h,
                           params={"limit": 2, "offset": 2})).json()
    assert p1["limit"] == 2 and p1["offset"] == 0 and p2["offset"] == 2
    assert p1["total"] == p2["total"] >= 4
    ids = [e["id"] for e in p1["items"]] + [e["id"] for e in p2["items"]]
    assert len(ids) == 4
    assert len(set(ids)) == 4, "consecutive pages overlapped"
    assert ids == sorted(ids, reverse=True), "the tiebreak dropped/reordered rows"

    # The four newest rows are the four writes just made, newest first.
    assert [e["method"] for e in p1["items"]] == ["DELETE", "POST"]
    assert p1["items"][0]["action"] == "DELETE /api/v1/datasets/{dataset_id}"
    assert p1["items"][0]["status_code"] == deleted.status_code

    # ---- 3. Correlate the ticket: find the promote by its request id ----
    recent = (await client.get("/api/v1/audit", headers=h,
                               params={"limit": 20})).json()["items"]
    entry = next(e for e in recent if e["request_id"] == promote_rid)
    assert entry["action"] == "POST /api/v1/datasets/{dataset_id}/tags/{tag_name}/promote"
    assert entry["path"] == f"/api/v1/datasets/{ds}/tags/production/promote"
    assert entry["status_code"] == 200 and entry["method"] == "POST"
    assert entry["resource_type"] == "dataset" and entry["resource_id"] == ds
    assert entry["team_id"] == team          # attributed to the OWNING team
    assert entry["actor_user_id"] == admin_id and entry["actor_email"]
    assert entry["metadata"]["path_params"]["tag_name"] == "production"
    assert entry["duration_ms"] is not None and datetime.fromisoformat(entry["occurred_at"])

    # ---- 4. The console's own error branches ----
    r = await client.get("/api/v1/audit", headers=h, params={"limit": 201})
    assert r.status_code == 422
    body = problem(r)
    assert any(e["loc"] == ["query", "limit"] for e in body["errors"])
    r = await client.get("/api/v1/audit", headers=h, params={"offset": -1})
    assert r.status_code == 422

    # A team admin — the most privileged non-platform role — still cannot read it.
    team_admin, _ = await create_team_user(client, admin_id, "admin")
    r = await client.get("/api/v1/audit", headers=auth(team_admin))
    assert r.status_code == 403 and problem(r)["code"] == "forbidden"

    r = await client.get("/api/v1/audit")
    assert r.status_code == 401 and problem(r)["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# 6. Cross-origin browser shell
# ---------------------------------------------------------------------------


async def test_journey_a_browser_ui_on_another_origin_can_preflight_read_and_download(
        client, admin_id):
    """FLOW: what a browser actually does before and during every fetch.

    A cross-origin SPA is the stated consumer. Four things break it and none of
    them are visible from a server-side test client: the preflight must accept
    the X-User-Id request header, Access-Control-Allow-Origin must ride on
    ERROR responses too (or the browser reports a network failure and the
    problem+json body is unreadable), X-Request-Id must be exposed so a bug
    report can quote it, and Content-Disposition must be exposed or a blob
    download saves as the URL's last path segment.
    """
    def exposed(response) -> set[str]:
        raw = response.headers.get("access-control-expose-headers", "")
        return {h.strip().lower() for h in raw.split(",") if h.strip()}

    # ---- 1. The shell's liveness ping ----
    health = await client.get("/health", headers={"Origin": ORIGIN})
    assert health.status_code == 200 and health.json()["status"] == "healthy"

    # ---- 2. The preflight the browser sends before the first authenticated GET
    pre = await client.request("OPTIONS", "/api/v1/datasets", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-user-id",
    })
    assert pre.status_code == 200, pre.text
    assert pre.headers["access-control-allow-origin"] in (ORIGIN, "*")
    assert "x-user-id" in pre.headers["access-control-allow-headers"].lower()
    assert "GET" in pre.headers["access-control-allow-methods"]

    # A preflight for the write the settings screen makes must pass too.
    pre_post = await client.request("OPTIONS", "/api/v1/webhooks", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-user-id,x-team-id,content-type",
    })
    assert pre_post.status_code == 200
    allowed = pre_post.headers["access-control-allow-headers"].lower()
    assert "x-team-id" in allowed and "content-type" in allowed

    # ---- 3. The real GET the preflight authorised ----
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    listing = await client.get("/api/v1/datasets",
                               headers={**auth(admin_id), "Origin": ORIGIN})
    assert listing.status_code == 200
    assert listing.headers["access-control-allow-origin"] in (ORIGIN, "*")
    assert "x-request-id" in exposed(listing)
    assert listing.headers["x-request-id"]
    assert listing.headers["x-content-type-options"] == "nosniff"
    assert any(d["id"] == ds for d in listing.json()["items"])

    # ---- 4. The download the row's menu triggers, with a readable filename ----
    dl = await client.get(f"/api/v1/datasets/{ds}/download",
                          params={"format": "csv"},
                          headers={**auth(admin_id), "Origin": ORIGIN})
    assert dl.status_code == 200, dl.text
    assert "filename=" in dl.headers["content-disposition"]
    assert "content-disposition" in exposed(dl)
    assert "region" in dl.text.splitlines()[0]

    # ---- 5. The error path — the one CORS is added outermost to protect ----
    err = await client.get("/api/v1/datasets/00000000-0000-0000-0000-0000000000ff",
                           headers={**auth(admin_id), "Origin": ORIGIN})
    assert err.status_code == 404
    assert err.headers["access-control-allow-origin"] in (ORIGIN, "*"), \
        "a problem+json without ACAO is a bare network error in the browser"
    assert "x-request-id" in exposed(err) and err.headers["x-request-id"]
    assert problem(err)["code"] == "not_found"

    # Same for the unauthenticated 401 the shell shows a login prompt on.
    unauth = await client.get("/api/v1/datasets", headers={"Origin": ORIGIN})
    assert unauth.status_code == 401
    assert unauth.headers["access-control-allow-origin"] in (ORIGIN, "*")


# ---------------------------------------------------------------------------
# 7. One error interceptor for the whole platform surface
# ---------------------------------------------------------------------------


async def test_journey_the_error_envelope_is_identical_across_every_platform_endpoint(
        client, admin_id):
    """FLOW: the single HTTP interceptor a UI writes once and reuses everywhere.

    Uniformity IS the contract: the interceptor branches on `code`, renders
    `detail`, and attaches `X-Request-Id` to the bug report. One route that
    answers text/plain, omits `code`, or 500s on a malformed id forces a
    special case into every screen that calls it.
    """
    protected = ("/api/v1/auth/me", "/api/v1/teams", "/api/v1/jobs",
                 "/api/v1/webhooks", "/api/v1/audit")

    # ---- 1. Signed out: every platform read answers the same 401 ----
    for path in protected:
        r = await client.get(path)
        assert r.status_code == 401, f"{path}: {r.status_code}"
        body = problem(r)
        assert body["code"] == "unauthorized" and body["instance"] == path
        assert body["title"] == "Unauthorized" and body["detail"]
        assert r.headers["x-request-id"]
        assert r.headers["x-frame-options"] == "DENY"   # hardening rides on errors

    # A header naming a user that does not exist is the same 401, not a 404.
    r = await client.get("/api/v1/auth/me",
                         headers=auth("00000000-0000-0000-0000-0000000000ff"))
    assert r.status_code == 401 and problem(r)["code"] == "unauthorized"

    # ---- 2. Signed in: a malformed id is 404 on every id-taking route ----
    h = auth(admin_id)
    for path in ("/api/v1/jobs/not-a-uuid", "/api/v1/webhooks/not-a-uuid",
                 "/api/v1/webhooks/not-a-uuid/deliveries",
                 "/api/v1/teams/not-a-uuid/members"):
        r = await client.get(path, headers=h)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"
        body = problem(r)
        assert body["code"] == "not_found" and body["instance"] == path
        assert "not-a-uuid" in body["detail"]

    # Writes on malformed ids answer 404 too, not a 500 from the DB cast.
    r = await client.patch("/api/v1/teams/not-a-uuid/members/not-a-uuid",
                           headers=h, json={"role": "viewer"})
    assert r.status_code == 404 and problem(r)["code"] == "not_found"
    r = await client.delete("/api/v1/webhooks/not-a-uuid", headers=h)
    assert r.status_code == 404 and problem(r)["code"] == "not_found"

    # ---- 3. Body validation: 422 with a field-addressable errors array ----
    r = await client.post("/api/v1/webhooks", headers=h, json={})
    assert r.status_code == 422
    body = problem(r)
    assert body["code"] == "unprocessable_entity"
    assert {tuple(e["loc"]) for e in body["errors"]} >= {("body", "name"), ("body", "url")}

    r = await client.post("/api/v1/teams", headers=h, json={"name": ""})
    assert r.status_code == 422 and problem(r)["errors"]

    r = await client.post("/api/v1/auth/users", headers=h,
                          json={"email": "not-an-email", "name": "X"})
    assert r.status_code == 422
    assert any(e["loc"] == ["body", "email"] for e in problem(r)["errors"])

    # ---- 4. The support-ticket loop: a client-supplied id is echoed back ----
    correlation = f"ui-{rid()}"
    r = await client.get("/api/v1/jobs/not-a-uuid",
                         headers={**h, "X-Request-Id": correlation})
    assert r.headers["x-request-id"] == correlation


# ---------------------------------------------------------------------------
# 8. Revocation
# ---------------------------------------------------------------------------


async def test_journey_a_member_removed_from_a_team_immediately_loses_every_platform_surface(
        client, admin_id, tmp_path):
    """FLOW: an admin revokes access while the removed user's tab is still open.

    Jobs, webhooks and datasets each derive their scope differently, so
    "removed" meaning the same thing on all three is an assumption until it is
    driven. Revocation must be effective on the very next request — the
    principal is rebuilt per request — and it must hide the resources, not
    merely forbid them, or a stale tab becomes a cross-tenant oracle.
    """
    h_admin = auth(admin_id)
    ops, team = await create_team_user(client, admin_id, "admin")
    h_ops = auth(ops)

    # ---- 1. The member works normally: a dataset, a job, a subscription ----
    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)
    ds = (await upload_file(client, ops, path, name="crm.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]

    r = await client.post(f"/api/v1/datasets/{ds}/relationships/suggest?sync=false",
                          headers=h_ops)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    jobs_page = (await client.get("/api/v1/jobs", headers=h_ops,
                                  params={"job_type": "relationship_discovery"})).json()
    assert [j["id"] for j in jobs_page["items"]] == [job_id]
    assert jobs_page["items"][0]["dataset_id"] == ds

    r = await client.post(f"/api/v1/webhooks?team_id={team}", headers=h_ops,
                          json={"name": "ops-hook", "url": LIVE_URL})
    assert r.status_code == 201, r.text
    hook = r.json()["id"]

    assert [w["id"] for w in (await client.get("/api/v1/webhooks",
                                               headers=h_ops)).json()["items"]] == [hook]
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=h_ops)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h_ops)).status_code == 200

    # ---- 2. The admin console revokes the membership ----
    r = await client.delete(f"/api/v1/teams/{team}/members/{ops}", headers=h_admin)
    assert r.status_code == 204, r.text

    # ---- 3. The very next request from the open tab sees nothing ----
    me = (await client.get("/api/v1/auth/me", headers=h_ops)).json()
    assert all(m["team_id"] != team for m in me["memberships"])

    assert (await client.get("/api/v1/jobs", headers=h_ops, params={
        "job_type": "relationship_discovery"})).json()["total"] == 0
    assert (await client.get("/api/v1/webhooks", headers=h_ops)).json()["total"] == 0
    assert (await client.get("/api/v1/datasets", headers=h_ops)).json()["total"] == 0

    # ---- 4. Deep links 404 — hidden, not merely forbidden ----
    for path_ in (f"/api/v1/jobs/{job_id}", f"/api/v1/webhooks/{hook}",
                  f"/api/v1/webhooks/{hook}/deliveries",
                  f"/api/v1/datasets/{ds}", f"/api/v1/teams/{team}/members"):
        r = await client.get(path_, headers=h_ops)
        assert r.status_code == 404, f"{path_}: {r.status_code} {r.text}"
        assert problem(r)["code"] == "not_found"

    # Writes are hidden the same way, so a queued form submission cannot land.
    r = await client.post(f"/api/v1/webhooks?team_id={team}", headers=h_ops,
                          json={"name": "sneaky", "url": LIVE_URL})
    assert r.status_code == 404 and problem(r)["code"] == "not_found"
    r = await client.delete(f"/api/v1/webhooks/{hook}", headers=h_ops)
    assert r.status_code == 404

    # ---- 5. Nothing was destroyed — the team still owns all of it ----
    assert (await client.get(f"/api/v1/webhooks/{hook}",
                             headers=h_admin)).status_code == 200
    assert (await client.get(f"/api/v1/jobs/{job_id}",
                             headers=h_admin)).status_code == 200

    # ---- 6. Re-admitting the same user restores every surface at once ----
    r = await client.post(f"/api/v1/teams/{team}/members", headers=h_admin,
                          json={"user_id": ops, "role": "admin"})
    assert r.status_code == 201 and r.json()["role"] == "admin"

    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=h_ops)).status_code == 200
    assert [w["id"] for w in (await client.get("/api/v1/webhooks",
                                               headers=h_ops)).json()["items"]] == [hook]
    assert (await client.get("/api/v1/datasets", headers=h_ops)).json()["total"] == 1
