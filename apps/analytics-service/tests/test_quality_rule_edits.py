"""Editing a quality rule after it exists — the PATCH contract.

Rules are the dataset's written-down contract, and the promotion gate reads
their results. An edit that is accepted but stored wrong is therefore not a
cosmetic bug: it changes what "validated" means for every later release, and it
does so with a 200 that gives the steward no reason to look.
"""

from __future__ import annotations

from conftest import auth, make_orders_workbook, upload_file

PROBLEM = "application/problem+json"


async def _orders_dataset(client, admin_id, tmp_path, *, clean=True, filename="orders.xlsx"):
    wb = tmp_path / filename
    make_orders_workbook(wb, clean=clean)
    body = await upload_file(client, admin_id, wb, name="orders.xlsx")
    return body["dataset_id"]


async def _create_rule(client, h, ds, spec):
    r = await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json=spec)
    assert r.status_code == 201, r.text
    return r.json()


async def test_patching_a_rule_to_an_unknown_sheet_clears_its_logical_pin(
        client, admin_id, tmp_path):
    """Re-targeting a rule must re-target what actually runs, not just the label.

    A rule carries two references to its sheet: the ``sheet_selector`` text and
    the ``logical_sheet_id`` pin, and the engine resolves the pin FIRST so a
    rule survives a confirmed sheet rename. If a PATCH that changes the selector
    leaves the old pin in place, the rule keeps evaluating the PREVIOUS sheet
    while the API — and the persisted result row — report the new selector. The
    steward sees "orders row count >= 1 on refunds: passed" for a dataset that
    has no refunds sheet at all, and promotes on the strength of it.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)

    rule = await _create_rule(client, h, ds, {
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})
    assert rule["logical_sheet_id"], "a live sheet should have been pinned on create"

    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}", headers=h,
                           json={"sheet_selector": "refunds"})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_selector"] == "refunds"
    assert r.json()["logical_sheet_id"] is None

    # The stored row agrees with the response — no field contradicts another.
    stored = (await client.get(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                               headers=h)).json()
    assert stored["logical_sheet_id"] is None

    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=h)).json()
    result = run["results"][0]
    assert result["sheet_selector"] == "refunds"
    assert result["status"] == "error", (
        "the rule now targets a sheet this version does not have; a pass here "
        "would be an answer about the old sheet")
    assert "refunds" in result["message"]


async def test_renaming_a_rule_onto_a_sibling_name_is_a_409_not_a_500(
        client, admin_id, tmp_path):
    """The same collision POST reports as 409 must not escape PATCH as a 500.

    Rule names are unique per dataset. A UI renaming a rule inline needs to tell
    the user "that name is taken" and keep their text in the box; a 500 means it
    can only say "something went wrong", and an operator gets paged for a
    user typo.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)

    await _create_rule(client, h, ds, {
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders"})
    second = await _create_rule(client, h, ds, {
        "name": "customers-present", "rule_type": "sheet_exists",
        "sheet_selector": "customers"})

    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{second['id']}", headers=h,
                           json={"name": "rows-present"})
    assert r.status_code == 409, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert "rows-present" in r.json()["detail"]

    # The rule is untouched, so the caller can retry with another name.
    stored = (await client.get(f"/api/v1/datasets/{ds}/rules/{second['id']}",
                               headers=h)).json()
    assert stored["name"] == "customers-present"


async def test_patch_rejects_edits_that_create_would_reject(client, admin_id, tmp_path):
    """PATCH re-checks the merged rule against the create-time invariants.

    Without this, a rule can be edited into a shape POST refuses — parameters
    emptied, column unset — and the request still returns 200. Nothing surfaces
    until the next validation run turns it into a per-rule ``error``, which
    reads like a data problem rather than the edit that caused it. Worse for
    ``accepted_values``: an empty value list accepts everything, so the rule
    goes green instead of erroring.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)

    tiers = await _create_rule(client, h, ds, {
        "name": "tier-accepted", "rule_type": "accepted_values",
        "sheet_selector": "customers", "column_selector": "tier",
        "parameters": {"values": ["gold", "silver"]}})
    fk = await _create_rule(client, h, ds, {
        "name": "orders-customer-fk", "rule_type": "foreign_key",
        "sheet_selector": "orders", "column_selector": "customer_id",
        "parameters": {"ref_sheet": "customers", "ref_column": "customer_id"}})

    for rule_id, body in (
        (tiers["id"], {"parameters": {}}),
        (tiers["id"], {"column_selector": None}),
        (tiers["id"], {"sheet_selector": None}),
        (fk["id"], {"parameters": {}}),
        (fk["id"], {"parameters": {"ref_sheet": "customers"}}),
    ):
        r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule_id}",
                               headers=h, json=body)
        assert r.status_code == 422, f"{body} -> {r.status_code} {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert r.json()["code"] == "invalid-rule-shape"

    # Nothing was written: both rules still evaluate as they were defined.
    assert (await client.get(f"/api/v1/datasets/{ds}/rules/{tiers['id']}",
                             headers=h)).json()["parameters"] == {
        "values": ["gold", "silver"]}
    run = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                             headers=h)).json()
    assert run["rules_total"] == 2 and run["error_failures"] == 0
    assert {res["status"] for res in run["results"]} == {"passed"}


async def test_patch_still_accepts_a_legal_edit_and_leaves_untouched_fields_alone(
        client, admin_id, tmp_path):
    """The new merge-and-check must not turn partial updates into full replacements."""
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)
    rule = await _create_rule(client, h, ds, {
        "name": "tier-accepted", "rule_type": "accepted_values",
        "sheet_selector": "customers", "column_selector": "tier",
        "parameters": {"values": ["gold", "silver"]}, "description": "why"})

    r = await client.patch(f"/api/v1/datasets/{ds}/rules/{rule['id']}", headers=h,
                           json={"severity": "warning", "enabled": False})
    assert r.status_code == 200, r.text
    patched = r.json()
    assert patched["severity"] == "warning" and patched["enabled"] is False
    assert patched["parameters"] == {"values": ["gold", "silver"]}
    assert patched["column_selector"] == "tier"
    assert patched["description"] == "why"
    assert patched["logical_sheet_id"] == rule["logical_sheet_id"]


async def test_a_single_rule_can_be_fetched_by_id(client, admin_id, tmp_path):
    """GET one rule, so a UI can deep-link to it without downloading every rule.

    It also has to hide rules that are not this dataset's: rule ids are opaque,
    and answering for a foreign one would confirm it exists.
    """
    h = auth(admin_id)
    ds = await _orders_dataset(client, admin_id, tmp_path)
    other = await _orders_dataset(client, admin_id, tmp_path, filename="other.xlsx")

    rule = await _create_rule(client, h, ds, {
        "name": "rows-present", "rule_type": "row_count_min",
        "sheet_selector": "orders", "parameters": {"min": 1}})

    r = await client.get(f"/api/v1/datasets/{ds}/rules/{rule['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == rule

    # Same rule id, wrong dataset → 404, not somebody else's rule.
    r = await client.get(f"/api/v1/datasets/{other}/rules/{rule['id']}", headers=h)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith(PROBLEM)

    # Malformed and deleted ids are both plain 404s.
    assert (await client.get(f"/api/v1/datasets/{ds}/rules/not-a-uuid",
                             headers=h)).status_code == 404
    assert (await client.delete(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                                headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/datasets/{ds}/rules/{rule['id']}",
                             headers=h)).status_code == 404
