"""The rules-list Page envelope, including the state every dataset starts in.

``GET /datasets/{id}/rules`` returns the whole rule set — it takes no
``limit``/``offset`` — but it still speaks the shared ``Page`` envelope, and a
UI reads that envelope the same way whichever endpoint produced it.
"""

from __future__ import annotations

from conftest import auth, make_orders_workbook, upload_file


async def _orders_dataset(client, admin_id, tmp_path):
    wb = tmp_path / "orders.xlsx"
    make_orders_workbook(wb, clean=True)
    body = await upload_file(client, admin_id, wb, name="orders.xlsx")
    return body["dataset_id"]


async def test_the_empty_rules_list_reports_a_page_size_the_envelope_allows(
        client, admin_id, tmp_path):
    """A dataset with no rules must not answer ``limit: 0``.

    ``limit`` is documented as the page size, and the service's own
    ``pagination`` dependency rejects ``limit=0`` as out of range (ge=1). Every
    dataset starts with zero rules, so the empty state is the FIRST thing the
    quality screen ever renders — and it is the one response where a pager
    computing ``ceil(total / limit)`` divides by zero, and where "load more"
    logic driven by ``offset + limit < total`` reads a page size the same API
    would refuse as input.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)

    r = await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == [] and body["total"] == 0
    assert body["offset"] == 0
    assert body["limit"] >= 1, (
        f"limit is the page size and must stay in the documented ge=1 range: {body}")

    # The non-empty case is unchanged: one page holding everything.
    create = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})
    assert create.status_code == 201, create.text
    body = (await client.get(f"/api/v1/datasets/{ds}/rules", headers=h)).json()
    assert body["total"] == 1 and body["limit"] == 1 and body["offset"] == 0
