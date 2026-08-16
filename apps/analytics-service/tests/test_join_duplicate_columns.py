"""A previewed join must not hand back two columns with the same name.

The right side's colliding columns are renamed ``{sheet_key}_{column}``. If the
right sheet already contains a column with that exact name, the rename lands on
a name that is already in the output. ``preview_join`` builds each row by
zipping the cursor description into a dict, so the two columns collapse: the
body still advertises the name twice in ``output_columns`` while one column's
values have silently replaced the other's. Nothing errors, and the user has no
way to tell which column they are looking at.

This is the end-to-end version of tests/unit/test_join_output_column_names.py:
it goes through a real workbook, a real relationship and the real preview
route, because the collapse happens in the response assembly, not in the SQL
builder.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import XLSX_MIME, auth, upload_file


def make_colliding_workbook(path):
    """Orders.tier collides with Customers.tier — and Customers ALSO has
    a column literally called ``customers_tier``, which is the name the
    collision rule generates."""
    wb = Workbook()
    cust = wb.active
    cust.title = "Customers"
    cust.append(["customer_id", "tier", "customers_tier"])
    for row in ([1, "gold", "c1"], [2, "silver", "c2"], [3, "gold", "c3"]):
        cust.append(row)
    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "total", "tier"])
    for row in ([10, 1, 100.0, "L10"], [11, 2, 40.0, "L11"], [12, 1, 60.0, "L12"]):
        orders.append(row)
    wb.save(path)


async def _dataset_and_edge(client, admin_id, tmp_path):
    path = tmp_path / "collide.xlsx"
    make_colliding_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="collide.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/relationships",
                          headers=auth(admin_id),
                          json={"from_sheet": "Orders", "from_column": "customer_id",
                                "to_sheet": "Customers", "to_column": "customer_id"})
    assert r.status_code == 201, r.text
    return ds, r.json()


async def test_a_preview_never_returns_two_columns_under_one_name(
        client, admin_id, tmp_path):
    _ds, rel = await _dataset_and_edge(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post("/api/v1/joins/preview", headers=h,
                          json={"relationship_id": rel["id"], "how": "inner"})
    assert r.status_code == 200, r.text
    body = r.json()
    cols = body["output_columns"]

    assert len(set(cols)) == len(cols), cols
    assert len(body["preview"]) == 3
    for row in body["preview"]:
        # Every advertised column is actually present and distinct in the row.
        assert set(row) == set(cols)

    # And each name still means what it says: the prefixed alias is the RIGHT
    # side's `tier`, and the right's own `customers_tier` keeps its values.
    assert {row["tier"] for row in body["preview"]} == {"L10", "L11", "L12"}
    assert {row["customers_tier"] for row in body["preview"]} == {"gold", "silver"}
    assert {row["customers_customers_tier"]
            for row in body["preview"]} == {"c1", "c2"}


async def test_an_executed_join_records_column_names_its_stored_table_can_honour(
        client, admin_id, tmp_path):
    """``output_columns`` is persisted on the run summary and inherited by every
    consumer of the artifact — the samples reader, publishing, lineage. A name
    listed twice there is a lie about the stored table that outlives the
    request that produced it."""
    _ds, rel = await _dataset_and_edge(client, admin_id, tmp_path)
    h = auth(admin_id)

    r = await client.post("/api/v1/joins/execute", headers=h,
                          json={"relationship_id": rel["id"], "how": "inner"})
    assert r.status_code == 200, r.text
    assert r.json()["row_count"] == 3
    cols = r.json()["output_columns"]
    assert len(set(cols)) == len(cols), cols

    sample = await client.get(
        f"/api/v1/samples/{r.json()['sample_file']}/data", headers=h)
    assert sample.status_code == 200, sample.text
    payload = sample.json()
    rows = payload["data"] if isinstance(payload, dict) else payload
    # Every advertised column is really in the stored data, under that name.
    assert set(rows[0]) == set(cols)
