"""Usage counts writes, not POSTs.

``GET /datasets/{id}/usage`` derived ``writes`` from the HTTP method alone:
anything in ``POST/PUT/PATCH/DELETE`` touching a ``/datasets/{id}`` path was a
write. But the studio's ordinary row read is
``POST /datasets/{id}/versions/{v}/sheets/{s}/query`` — a POST only because a
QuerySpec (projection, filters, multi-sort, cursor) is far too big for a query
string. Merely OPENING a dataset in the UI therefore reported ``writes: 1``.

That is the silent-wrong-answer class, not a mislabel: the Library panel
publishes this number, and "this dataset is being written to constantly" is a
conclusion a human acts on — freezing it, chasing an owner, blocking a
migration. A viewer with read-only access could run the counter up forever.

The counters must partition the same audit rows they are derived from:
``downloads + writes + reads == total_events``. A fix that merely stopped
counting the query would have moved the error into ``total_events``, which
would then exceed its own parts with nothing to explain the difference.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

ROWS = [{"id": 1, "region": "emea", "amount": 10},
        {"id": 2, "region": "amer", "amount": 20}]


async def _usage(client, headers, ds) -> dict:
    r = await client.get(f"/api/v1/datasets/{ds}/usage", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_reading_rows_is_not_a_write(client, admin_id):
    """Open a dataset, then mutate it: only the mutation moves ``writes``."""
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    before = await _usage(client, h, ds)
    assert (before["downloads"], before["writes"], before["total_events"]) == (0, 0, 0)
    assert before["last_activity_at"] is None

    # ---- 1. The read the studio issues to render the table ----
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/sheets/data/query", headers=h,
        json={"columns": ["id", "region"],
              "sort": [{"column": "id"}], "limit": 2})
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()["items"]] == [1, 2]

    after_read = await _usage(client, h, ds)
    assert after_read["writes"] == 0, (
        "a POST that mutates nothing was counted as a write")
    # The read is not discarded — it happened, it is in the audit trail, and it
    # is the most meaningful "is anyone using this?" signal there is. It is
    # counted as what it was.
    assert after_read["reads"] == 1
    assert after_read["downloads"] == 0
    assert after_read["total_events"] == 1
    assert after_read["last_activity_at"]

    # ---- 2. A genuine mutation ----
    r = await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                           json={"description": "quarterly regional totals"})
    assert r.status_code == 200, r.text

    after_write = await _usage(client, h, ds)
    assert after_write["writes"] == 1
    assert after_write["reads"] == 1
    assert after_write["total_events"] == 2

    # ---- 3. The parts still add up to the whole ----
    assert (after_write["downloads"] + after_write["writes"]
            + after_write["reads"]) == after_write["total_events"]


async def test_every_read_shaped_post_stays_out_of_the_write_count(client, admin_id):
    """The whole read-shaped family, not just ``/query``.

    One endpoint at a time is how this stayed wrong: ``/query`` is the loud one,
    but a chart render, a transformation preview and a compile are the same
    shape — a POST because the request body is a spec, returning a computed
    answer and persisting nothing.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    reads = [
        ("/versions/1/query", {"limit": 1}),
        ("/versions/1/sheets/data/query", {"limit": 1}),
        ("/transformations/compile",
         {"sheet": "data", "steps": [{"type": "drop", "columns": ["region"]}]}),
    ]
    for suffix, body in reads:
        r = await client.post(f"/api/v1/datasets/{ds}{suffix}", headers=h, json=body)
        assert r.status_code == 200, f"{suffix}: {r.text}"

    usage = await _usage(client, h, ds)
    assert usage["writes"] == 0, "a read-shaped POST is still counted as a write"
    assert usage["reads"] == len(reads)
    assert usage["total_events"] == len(reads)


async def test_a_read_is_not_dataset_history(client, admin_id):
    """The timeline's own contract is "audited write requests".

    ``dataset_timeline`` classified the same way (``method <> 'GET'``), so a
    query also appeared in the Activity feed as an ``audit`` event — a history
    entry for something that changed nothing. Both surfaces read the one
    classification, so neither can drift from the other.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    assert (await client.post(
        f"/api/v1/datasets/{ds}/versions/1/sheets/data/query",
        headers=h, json={"limit": 1})).status_code == 200

    events = (await client.get(f"/api/v1/datasets/{ds}/timeline",
                               headers=h, params={"limit": 200})).json()["items"]
    audits = [e for e in events if e["event_type"] == "audit"]
    assert audits == [], f"a read was recorded as history: {audits}"

    # ...and a real mutation still is history, so the assertion above is not
    # passing because the audit source went dark.
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={"description": "d"})).status_code == 200
    events = (await client.get(f"/api/v1/datasets/{ds}/timeline",
                               headers=h, params={"limit": 200})).json()["items"]
    audits = [e for e in events if e["event_type"] == "audit"]
    assert [e["details"]["method"] for e in audits] == ["PATCH"]


async def test_a_denied_read_is_not_counted_either(client, admin_id):
    """Classification runs after the success filter, not instead of it.

    ``reads`` is a new counter over the same rows ``writes`` and ``downloads``
    already filter, so the "usage cannot be manufactured by being refused"
    contract has to hold for it too — otherwise an outsider bouncing off a
    dataset it cannot see makes it look busy.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "editor")

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sheets/data/query",
                          headers=auth(outsider), json={"limit": 1})
    assert r.status_code == 404, r.text

    usage = await _usage(client, h, ds)
    assert (usage["downloads"], usage["writes"],
            usage["total_events"]) == (0, 0, 0)
    assert usage["reads"] == 0
    assert usage["last_activity_at"] is None
