"""Every column a join emits must have a name of its own.

``build_join_sql`` disambiguates a right-side name that clashes with the left
by prefixing it with the right sheet's key. The prefix is not itself unique: a
right sheet that happens to contain a column literally called
``{sheet_key}_{something}`` can be renamed onto a name that is already in the
output.

That is not a cosmetic problem. ``preview_join`` zips the cursor description
with each row into a dict, so two identically named columns collapse — one
column's values silently replace the other's while ``output_columns`` still
advertises both. The user sees a full-looking table with wrong values in it and
nothing anywhere says so. (The execute path is safer by accident: DuckDB
rejects ``CREATE TABLE`` with duplicate output names, so it fails loudly.)
"""

from __future__ import annotations

from app.features.relationships.joins import JoinSide, build_join_sql


def side(sheet_key: str, key: str, columns: list[str], view: str) -> JoinSide:
    s = JoinSide({"name": sheet_key}, {},
                 {"sheet_key": sheet_key, "sheet_name": sheet_key.title()},
                 key, view)
    s.columns = columns
    return s


def test_a_right_column_named_like_a_prefixed_alias_still_gets_a_unique_name():
    left = side("orders", "customer_id",
                ["order_id", "customer_id", "tier"], "join_l")
    # `tier` collides, so it becomes `customers_tier` — which this sheet
    # already has a column called.
    right = side("customers", "customer_id",
                 ["customer_id", "tier", "customers_tier"], "join_r")

    sql, names = build_join_sql(left, right, "inner", None)

    assert len(set(names)) == len(names), names
    assert names[:3] == ["order_id", "customer_id", "tier"]
    assert set(names[3:]) == {"customers_tier", "customers_customers_tier"}
    # The SQL must agree with the names the caller is promised.
    for alias in names[3:]:
        assert f'AS "{alias}"' in sql


def test_a_right_column_colliding_with_an_already_prefixed_alias_is_not_lost():
    """The clash can also come from the left side, not just from a sibling."""
    left = side("orders", "customer_id",
                ["customer_id", "tier", "customers_tier"], "join_l")
    right = side("customers", "customer_id", ["customer_id", "tier"], "join_r")

    _sql, names = build_join_sql(left, right, "inner", None)

    assert len(set(names)) == len(names), names
    assert names == ["customer_id", "tier", "customers_tier",
                     "customers_tier_2"]


def test_the_ordinary_join_naming_is_unchanged():
    """The disambiguation must not perturb the common case."""
    left = side("orders", "customer_id",
                ["order_id", "customer_id", "total"], "join_l")
    right = side("customers", "customer_id", ["customer_id", "tier"], "join_r")

    _sql, names = build_join_sql(left, right, "inner", None)

    assert names == ["order_id", "customer_id", "total", "tier"]
