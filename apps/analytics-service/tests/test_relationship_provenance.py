"""Provenance integrity when a relationship is re-declared, and seed counts.

A relationship's method, evidence, and confidence must never contradict each
other. The method badge freezes once an edge is reviewed; before this fix a
person re-declaring an already-confirmed edge froze the badge but overwrote its
evidence and confidence — leaving an fk_rule edge whose rule link was destroyed,
or a statistical edge presenting a fabricated 1.0 confidence.
"""

from __future__ import annotations

from conftest import auth, make_crm_workbook, upload_file, XLSX_MIME


async def _crm(client, admin_id, tmp_path, name="crm.xlsx"):
    path = tmp_path / name
    make_crm_workbook(path)
    body = await upload_file(client, admin_id, path, name=name, content_type=XLSX_MIME)
    return body["dataset_id"]


async def _add_fk_rule(client, admin_id, ds):
    r = await client.post(
        f"/api/v1/datasets/{ds}/rules", headers=auth(admin_id),
        json={"name": "orders-customer-fk", "rule_type": "foreign_key",
              "sheet_selector": "Orders", "column_selector": "customer_id",
              "parameters": {"ref_sheet": "Customers", "ref_column": "customer_id"}})
    assert r.status_code == 201, r.text
    return r.json()


async def _declare(client, admin_id, ds, **over):
    payload = {"from_sheet": "Orders", "from_column": "customer_id",
               "to_sheet": "Customers", "to_column": "customer_id", "confirmed": True}
    payload.update(over)
    return await client.post(f"/api/v1/datasets/{ds}/relationships",
                             headers=auth(admin_id), json=payload)


async def test_manual_redeclare_keeps_fk_rule_provenance_on_confirmed_edge(client, admin_id, tmp_path):
    h = auth(admin_id)
    ds = await _crm(client, admin_id, tmp_path)
    rule = await _add_fk_rule(client, admin_id, ds)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)).json()["relationships"][0]
    assert edge["method"] == "fk_rule"
    await client.post(f"/api/v1/datasets/{ds}/relationships/{edge['id']}/confirm", headers=h)

    await _declare(client, admin_id, ds)  # person re-declares the same pair

    g = (await client.get(f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)).json()
    # Method and evidence must agree: badge stays fk_rule, so the rule link stays.
    assert g["method"] == "fk_rule"
    assert g["evidence"].get("rule_id") == rule["id"]
    assert g["confidence"] == 1.0


async def test_manual_redeclare_keeps_statistical_provenance_on_confirmed_edge(client, admin_id, tmp_path):
    h = auth(admin_id)
    ds = await _crm(client, admin_id, tmp_path)
    await client.post(f"/api/v1/datasets/{ds}/relationships/suggest", headers=h)
    items = (await client.get(f"/api/v1/datasets/{ds}/relationships", headers=h)).json()["items"]
    stat = next(e for e in items if e["method"] == "statistical")
    original_conf = stat["confidence"]
    await client.post(f"/api/v1/datasets/{ds}/relationships/{stat['id']}/confirm", headers=h)

    await _declare(client, admin_id, ds,
                   from_sheet=stat["from_sheet"], from_column=stat["from_column"],
                   to_sheet=stat["to_sheet"], to_column=stat["to_column"])

    g = (await client.get(f"/api/v1/datasets/{ds}/relationships/{stat['id']}", headers=h)).json()
    # Badge stays statistical, so its measured evidence and score must not be
    # replaced by a fabricated manual 1.0.
    assert g["method"] == "statistical"
    assert g["confidence"] == original_conf
    assert "coverage" in g["evidence"]


async def test_manual_declare_still_wins_on_a_suggested_edge(client, admin_id, tmp_path):
    h = auth(admin_id)
    ds = await _crm(client, admin_id, tmp_path)
    await _add_fk_rule(client, admin_id, ds)
    edge = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)).json()["relationships"][0]
    # Not confirmed — still 'suggested'. A manual declaration wins outright here.
    await _declare(client, admin_id, ds)
    g = (await client.get(f"/api/v1/datasets/{ds}/relationships/{edge['id']}", headers=h)).json()
    assert g["method"] == "manual"


async def test_reseed_reports_zero_created_when_nothing_is_new(client, admin_id, tmp_path):
    h = auth(admin_id)
    ds = await _crm(client, admin_id, tmp_path)
    await _add_fk_rule(client, admin_id, ds)
    first = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)).json()
    second = (await client.post(f"/api/v1/datasets/{ds}/relationships/seed", headers=h)).json()
    assert first["created"] == 1
    assert second["created"] == 0
    total = (await client.get(f"/api/v1/datasets/{ds}/relationships", headers=h)).json()["total"]
    assert total == 1
