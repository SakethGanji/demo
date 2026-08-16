"""Data-accelerator contract gaps: tag resolution, catalog facets, rollback events.

Four unrelated defects that share one shape — the route promises something its
handler does not deliver, and answers 200 anyway:

* ``GET /datasets/{id}/tags/{tag}`` declares ``VersionInfo`` but built it from
  a hand-written subset, so ``sheet_count``, ``source_checksum`` and
  ``manifest_checksum`` came back null for a row that had all three.
* ``GET /datasets`` typed ``validation_status``/``documentation`` as bare
  ``str``, so a mistyped facet matched no rows and returned an empty page.
* ``tag.rolled_back`` was a subscribable event type that nothing ever emitted.
* There was no way to list the sheets of a *given* version — only of the
  current one — so every consumer describing "the tables in this version"
  was reading the wrong version's schema whenever one was pinned.
"""

from __future__ import annotations

import json

import pytest

from conftest import auth, upload_inline

ROWS = [{"id": 1, "amount": 10.0}, {"id": 2, "amount": 20.0}]


# --- tag resolution -----------------------------------------------------------

async def test_resolving_a_tag_returns_the_same_version_fields_as_the_list(
        client, admin_id):
    """A consumer that resolves 'production' compares its content identity.

    ``manifest_checksum`` IS the version's content identity; nulling it here
    made every such comparison report a mismatch against the same version.
    """
    h = auth(admin_id)
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "production", "version_number": 1})
    assert r.status_code == 200, r.text

    listed = (await client.get(f"/api/v1/datasets/{ds}/versions",
                               headers=h)).json()["items"][0]
    resolved = (await client.get(f"/api/v1/datasets/{ds}/tags/production",
                                 headers=h)).json()

    assert listed["manifest_checksum"], "fixture precondition: the row has one"
    for field in ("sheet_count", "source_checksum", "manifest_checksum",
                  "checksum", "row_count", "size_bytes", "version_number"):
        assert resolved[field] == listed[field], field


# --- catalog facet vocabularies ----------------------------------------------

@pytest.mark.parametrize("param,value", [
    ("validation_status", "pass"),      # near-miss for 'passed'
    ("validation_status", "unknown"),
    ("documentation", "complete"),      # near-miss for 'full'
    ("documentation", "PARTIAL"),       # the vocabulary is lower-case
])
async def test_a_mistyped_catalog_facet_is_a_422_not_an_empty_page(
        client, admin_id, param, value):
    """An empty 200 is indistinguishable from "you can see no datasets".

    Callers (the MCP catalog tool included) blamed that on tenancy and told
    the user they had no access, when they had simply misspelled a facet.
    """
    await upload_inline(client, admin_id, json.dumps(ROWS))
    r = await client.get(f"/api/v1/datasets?{param}={value}", headers=auth(admin_id))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "unprocessable_entity"
    assert any(e["loc"][-1] == param for e in r.json()["errors"]), r.text


@pytest.mark.parametrize("param,value", [
    ("validation_status", "passed"),
    ("validation_status", "failed"),
    ("validation_status", "none"),
    ("documentation", "full"),
    ("documentation", "partial"),
    ("documentation", "none"),
])
async def test_every_value_the_facet_counts_emit_is_still_accepted(
        client, admin_id, param, value):
    """The closed vocabulary must be exactly what `/datasets/facets` reports,
    or the counts would link to a 422."""
    await upload_inline(client, admin_id, json.dumps(ROWS))
    r = await client.get(f"/api/v1/datasets?{param}={value}", headers=auth(admin_id))
    assert r.status_code == 200, r.text


# --- rollback event -----------------------------------------------------------

async def test_rolling_a_tag_back_fires_the_declared_event(client, admin_id,
                                                           monkeypatch):
    """`tag.rolled_back` was accepted on subscribe and never emitted.

    A consumer subscribing to it to un-publish downstream artifacts simply
    never fired — and a rollback is the moment that matters most.
    """
    sent: list[dict] = []

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            sent.append({"url": url, "body": content})
            return _Response()

    import httpx

    from app.shared import worker

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    h = auth(admin_id)
    created = (await client.post("/api/v1/webhooks", headers=h, json={
        "name": "rollback-hook", "url": "https://example.test/hook",
        "events": ["tag.rolled_back"]})).json()

    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(ROWS), dataset_id=ds)
    for n in (1, 2):
        r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote",
                              headers=h, json={"version_number": n})
        assert r.status_code == 200, r.text

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/rollback",
                          headers=h, json={"reason": "bad numbers"})
    assert r.status_code == 200, r.text
    await worker.run_pending_jobs_once()

    deliveries = (await client.get(
        f"/api/v1/webhooks/{created['id']}/deliveries", headers=h)).json()
    assert deliveries["total"] == 1, deliveries
    assert deliveries["items"][0]["event_type"] == "tag.rolled_back"

    payload = json.loads(sent[-1]["body"].decode())
    assert payload["event"] == "tag.rolled_back"
    assert payload["data"]["tag"] == "production"
    assert payload["data"]["from_version_number"] == 2
    assert payload["data"]["to_version_number"] == 1
    assert payload["data"]["reason"] == "bad numbers"


# --- version-scoped sheet listing ---------------------------------------------

async def test_listing_sheets_of_a_pinned_version_answers_for_that_version(
        client, admin_id, tmp_path):
    """`/datasets/{id}/sheets` resolves the CURRENT version, always.

    A caller running SQL against version 1 that asked for "the tables" got
    version 2's sheet keys back — which is how a consumer concludes a table
    does not exist, or spells one from a schema its query will not see.
    """
    from conftest import XLSX_MIME, make_workbook, upload_file

    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)                             # Revenue / Expenses / Secrets
    make_workbook(v2, second_sheet="Spending")    # Expenses renamed
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    h = auth(admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets", headers=h)
    assert r.status_code == 200, r.text
    keys_v1 = {s["sheet_key"] for s in r.json()["items"]}
    assert "expenses" in keys_v1 and "spending" not in keys_v1

    r = await client.get(f"/api/v1/datasets/{ds}/versions/2/sheets", headers=h)
    assert r.status_code == 200, r.text
    keys_v2 = {s["sheet_key"] for s in r.json()["items"]}
    assert "spending" in keys_v2 and "expenses" not in keys_v2

    # The current-version route agrees with version 2, which is the current one.
    current = {s["sheet_key"] for s in
               (await client.get(f"/api/v1/datasets/{ds}/sheets",
                                 headers=h)).json()["items"]}
    assert current == keys_v2


async def test_an_unknown_version_number_is_a_404(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.get(f"/api/v1/datasets/{ds}/versions/99/sheets",
                         headers=auth(admin_id))
    assert r.status_code == 404, r.text


async def test_another_teams_version_sheets_are_a_404_not_a_403(client, admin_id):
    """ARCHITECTURE §3: a cross-team read must not confirm existence."""
    from conftest import create_team_user

    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets",
                         headers=auth(outsider))
    assert r.status_code == 404, r.text
