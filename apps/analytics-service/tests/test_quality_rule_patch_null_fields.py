"""PATCHing a rule field to an explicit ``null`` — the non-nullable columns.

``RuleUpdate`` uses ``None`` as "field not sent", so every field is typed
``X | None``. That makes an explicitly-sent ``null`` indistinguishable from an
omission *at the type level* but not at the wire level: ``exclude_unset``
keeps the key, the repo emits ``name = NULL``, and Postgres rejects it because
the column is ``NOT NULL``. What the caller then sees is decided by whichever
``except`` clause happens to be in the way.
"""

from __future__ import annotations

from conftest import auth, make_orders_workbook, upload_file

PROBLEM = "application/problem+json"


async def _orders_dataset(client, admin_id, tmp_path):
    wb = tmp_path / "orders.xlsx"
    make_orders_workbook(wb, clean=True)
    body = await upload_file(client, admin_id, wb, name="orders.xlsx")
    return body["dataset_id"]


async def _create_rule(client, h, ds, spec):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
    assert r.status_code == 201, r.text
    return r.json()


async def test_patching_a_rule_field_to_an_explicit_null_is_a_422_not_a_phantom_name_collision(
        client, admin_id, tmp_path):
    """An explicit null on a NOT NULL column must name the field the caller sent.

    Without the guard the null reaches Postgres, the NotNullViolation arrives as
    the same ``IntegrityError`` class the unique index raises, and the blanket
    handler answers ``409 A rule named 'None' already exists on this dataset``.
    That is wrong three times over: the status says "retry with another name",
    the detail names a rule that does not exist, and it blames the ``name``
    field on a request that only sent ``severity``. A form clearing a control
    to null gets told its name is taken and highlights a box the user never
    touched, and the real cause — a non-nullable field — is never reported.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)
    rule = await _create_rule(client, h, ds, {
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})
    url = f"/api/v1/datasets/{ds}/rules/{rule['id']}"

    for field in ("name", "severity", "enabled"):
        r = await client.patch(url, headers=h, json={field: None})
        assert r.status_code == 422, f"{field} -> {r.status_code}: {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
        body = r.json()
        assert "A rule named" not in body["detail"], (
            f"{field}=null was reported as a name collision: {body['detail']}")
        assert "None" not in body["detail"]
        locs = [".".join(str(p) for p in e["loc"]) for e in body["errors"]]
        assert any(loc.endswith(field) for loc in locs), (
            f"the form needs to know {field} was the offending field, got {locs}")

    # Nothing was written: the rule still reads exactly as created.
    stored = (await client.get(url, headers=h)).json()
    assert stored["name"] == "rows-present"
    assert stored["severity"] == "error" and stored["enabled"] is True
    assert stored["updated_at"] == rule["updated_at"]


async def test_patching_a_nullable_rule_field_to_null_still_clears_it(
        client, admin_id, tmp_path):
    """The null guard must cover only the columns that are actually NOT NULL.

    ``description``, ``column_selector`` and ``sheet_selector`` are nullable, and
    clearing them is the only way a UI can un-set an optional field. A guard
    written as "reject every explicit null" would take that away and turn an
    ordinary "clear this box" into a 422.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)
    rule = await _create_rule(client, h, ds, {
        "name": "rows-present", "description": "seeded",
        "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})
    url = f"/api/v1/datasets/{ds}/rules/{rule['id']}"

    r = await client.patch(url, headers=h, json={"description": None})
    assert r.status_code == 200, r.text
    assert r.json()["description"] is None
    assert r.json()["name"] == "rows-present"
