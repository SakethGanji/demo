"""Schema evolution diff: additive ops are planned, destructive ones are refused, and a rename is seen as drop + add."""

from __future__ import annotations

from src.services import schema_migrations as sm

CUR = [{"name": "title", "type": "string", "required": True},
       {"name": "status", "type": "string", "enum": ["queued", "running"]},
       {"name": "test_id", "type": "integer"}]


def table(*cols, **extra):
    return {"name": "runs", "columns": list(cols), **extra}


def ops_of(res, op):
    return [o for o in res["ops"] if o["op"] == op]


def refused_of(res, op):
    return [o for o in res["refused"] if o["op"] == op]


def test_unchanged_table_has_no_ops():
    res = sm.diff_table(CUR, table(*CUR), 10)
    assert res == {"ops": [], "refused": []}


def test_system_columns_are_ignored():
    res = sm.diff_table(CUR + [{"name": "id", "type": "integer"}], table(*CUR, {"name": "created_at", "type": "datetime"}), 3)
    assert res == {"ops": [], "refused": []}


def test_add_nullable_column():
    res = sm.diff_table(CUR, table(*CUR, {"name": "note", "type": "string"}), 5)
    assert ops_of(res, "add_column") == [{"op": "add_column", "column": {"name": "note", "type": "string", "required": False}}]
    assert res["refused"] == []


def test_add_required_column_with_default_on_nonempty():
    col = {"name": "env", "type": "string", "required": True, "default": "dev"}
    res = sm.diff_table(CUR, table(*CUR, col), 5)
    assert ops_of(res, "add_column")[0]["column"]["default"] == "dev" and res["refused"] == []


def test_required_without_default_refused_only_on_nonempty():
    col = {"name": "env", "type": "string", "required": True}
    assert refused_of(sm.diff_table(CUR, table(*CUR, col), 5), "required_without_default_on_nonempty")
    empty = sm.diff_table(CUR, table(*CUR, col), 0)
    assert empty["refused"] == [] and ops_of(empty, "add_column")


def test_tightening_required_without_default_refused_on_nonempty():
    cols = [dict(c) for c in CUR]
    cols[2]["required"] = True
    assert refused_of(sm.diff_table(CUR, table(*cols), 2), "required_without_default_on_nonempty")
    assert ops_of(sm.diff_table(CUR, table(*cols), 0), "set_required") == [{"op": "set_required", "column": "test_id", "value": True}]
    cols[2]["default"] = 0
    res = sm.diff_table(CUR, table(*cols), 2)
    assert res["refused"] == [] and ops_of(res, "set_required") and ops_of(res, "set_default")


def test_add_enum_values():
    cols = [dict(c) for c in CUR]
    cols[1]["enum"] = ["queued", "running", "passed"]
    res = sm.diff_table(CUR, table(*cols), 9)
    assert ops_of(res, "add_enum_values") == [{"op": "add_enum_values", "column": "status", "values": ["passed"]}]


def test_remove_enum_values_refused():
    cols = [dict(c) for c in CUR]
    cols[1]["enum"] = ["queued"]
    res = sm.diff_table(CUR, table(*cols), 9)
    assert refused_of(res, "remove_enum_values")[0]["values"] == ["running"] and not res["ops"]


def test_unique_index_ref_description_deprecate():
    cols = [dict(c) for c in CUR]
    cols[0].update(unique=True, description="the title")
    cols[2].update(ref="tests.id", index=True)
    cols[1]["deprecated"] = True
    res = sm.diff_table(CUR, table(*cols, indexes=[["status", "test_id"]], description="runs of tests"), 4,
                        current_meta={"indexes": [], "description": None})
    kinds = {o["op"] for o in res["ops"]}
    assert kinds == {"set_unique", "set_description", "set_ref", "set_index", "deprecate_column"}
    assert {"op": "set_unique", "column": "title", "value": True} in res["ops"]
    assert {"op": "set_ref", "column": "test_id", "ref": "tests.id"} in res["ops"]
    assert {"op": "set_index", "columns": ["test_id"]} in res["ops"]
    assert {"op": "set_index", "columns": ["status", "test_id"]} in res["ops"]
    assert {"op": "deprecate_column", "column": "status"} in res["ops"]
    assert {"op": "set_description", "column": None, "description": "runs of tests"} in res["ops"]
    assert res["refused"] == []


def test_table_metadata_only_compared_when_known():
    res = sm.diff_table(CUR, table(*CUR, indexes=[["status"]], description="d"), 4)
    assert res == {"ops": [], "refused": []}
    same = sm.diff_table(CUR, table(*CUR, indexes=[["status"]], description="d"), 4,
                         current_meta={"indexes": [["status"]], "description": "d"})
    assert same == {"ops": [], "refused": []}


def test_drop_column_refused():
    res = sm.diff_table(CUR, table(CUR[0], CUR[1]), 1)
    assert refused_of(res, "drop_column") == [{"op": "drop_column", "column": "test_id",
                                               "reason": refused_of(res, "drop_column")[0]["reason"]}]


def test_rename_detected_as_drop_plus_add_of_same_type():
    res = sm.diff_table(CUR, table(CUR[0], CUR[1], {"name": "case_id", "type": "integer"}), 1)
    ren = refused_of(res, "rename_column")
    assert ren and ren[0]["from"] == "test_id" and ren[0]["to"] == "case_id"
    assert not ops_of(res, "add_column") and not refused_of(res, "drop_column")


def test_type_change_refused():
    cols = [dict(c) for c in CUR]
    cols[2]["type"] = "string"
    res = sm.diff_table(CUR, table(*cols), 0)
    assert refused_of(res, "type_change")[0] == {"op": "type_change", "column": "test_id", "from": "integer", "to": "string",
                                                 "reason": refused_of(res, "type_change")[0]["reason"]}


def test_new_table_is_create_table():
    res = sm.diff_table(None, table(*CUR), 0)
    assert res["ops"][0]["op"] == "create_table" and [c["name"] for c in res["ops"][0]["columns"]] == ["title", "status", "test_id"]


def test_is_additive():
    assert sm.is_additive([{"op": "add_column"}, {"op": "set_index"}])
    assert not sm.is_additive([{"op": "drop_column"}])


def test_plan_migrations_between_plans():
    prev = {"data": {"tables": [table(*CUR), {"name": "old", "columns": [{"name": "x", "type": "string"}]}]}}
    new = {"data": {"tables": [table(*CUR, {"name": "note", "type": "string"}),
                               {"name": "tests", "columns": [{"name": "key", "type": "string", "unique": True}]}]}}
    out = {m["table"]: m for m in sm.plan_migrations(prev, new)}
    assert out["runs"]["ops"] == [{"op": "add_column", "column": {"name": "note", "type": "string", "required": False}}]
    assert out["tests"]["ops"][0]["op"] == "create_table"
    assert out["old"]["refused"][0]["op"] == "drop_table" and out["old"]["ops"] == []
    assert sm.plan_migrations(new, new) == []
    assert sm.plan_migrations(None, new)[0]["ops"][0]["op"] == "create_table"


def test_plan_migrations_assume_rows_unless_told():
    prev = {"data": {"tables": [table(*CUR)]}}
    new = {"data": {"tables": [table(*CUR, {"name": "env", "type": "string", "required": True})]}}
    assert sm.plan_migrations(prev, new)[0]["refused"][0]["op"] == "required_without_default_on_nonempty"
    assert sm.plan_migrations(prev, new, row_counts={"runs": 0})[0]["refused"] == []
