"""Seed rows point at parents seeded in the same load by position; the loader turns positions into the real ids."""
from __future__ import annotations

from src.services import oneshot_builder as ob

TABLES = [
    {"name": "cases", "columns": [{"name": "suite_id", "type": "string", "ref": "suites.id"}, {"name": "title", "type": "string"}]},
    {"name": "suites", "columns": [{"name": "name", "type": "string"}]},
    {"name": "runs", "columns": [{"name": "case_id", "type": "number", "ref": "cases.id"}]},
]


def test_parents_are_seeded_first():
    seed = {"runs": [{}], "cases": [{}], "suites": [{}], "notes": [{}]}
    order = ob.seed_order(seed, TABLES)
    assert order.index("suites") < order.index("cases") < order.index("runs")
    assert sorted(order) == sorted(seed)


def test_cycles_and_self_refs_do_not_loop():
    tables = [{"name": "a", "columns": [{"name": "b_id", "ref": "b.id"}, {"name": "parent", "ref": "a.id"}]},
              {"name": "b", "columns": [{"name": "a_id", "ref": "a.id"}]}]
    assert sorted(ob.seed_order({"a": [], "b": []}, tables)) == ["a", "b"]


def test_positions_become_real_ids_typed_like_the_column():
    rows = [{"suite_id": "2", "title": "x"}, {"suite_id": 1, "title": "y"}, {"suite_id": "9", "title": "out of range"},
            {"suite_id": "abc"}, {"title": "no link"}]
    out = ob.link_seed_rows(rows, TABLES[0]["columns"], {"suites": [1107, 1108]})
    assert [r.get("suite_id") for r in out] == ["1108", "1107", "9", "abc", None]
    assert rows[0]["suite_id"] == "2"   # input untouched
    num = ob.link_seed_rows([{"case_id": 1}], TABLES[2]["columns"], {"cases": [55]})
    assert num == [{"case_id": 55}]


def test_parent_not_seeded_now_leaves_values_alone():
    rows = [{"suite_id": "1"}]
    assert ob.link_seed_rows(rows, TABLES[0]["columns"], {}) == rows
