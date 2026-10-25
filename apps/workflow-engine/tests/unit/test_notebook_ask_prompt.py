"""Notebook Ask upgrades, the pure parts: notes-only replies, the compact spec echo, result lines with failed checks,
the hidden connection secrets, the new optional spec fields (absent = byte-identical to before), and ``has_item``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.schemas.notebook import validate_spec
from src.services.notebook import apitest
from src.services.notebook.executors import ask as A
from src.services.notebook.service import NOTES_MAX, clean_notes


def _plan(cells, **kw):
    return A.validate_plan(cells, ["c1"], 2, {"bank_rw"}, {"bank"}, set(), **kw)


def test_empty_cells_need_notes():
    assert _plan([]) == ([], ["`cells` must be a non-empty list (or give `notes` to answer without cells)"])
    assert _plan(None)[1]
    assert _plan([], allow_empty=True) == ([], [])
    assert _plan(None, allow_empty=True) == ([], [])
    # allow_empty never lets a bad cell through
    cells, errors = _plan([{"kind": "query", "spec": {"connection": "nope", "sql": "SELECT 1"}}], allow_empty=True)
    assert cells == [] and "unknown connection 'nope'" in errors[0]


def test_has_notes():
    assert A.has_notes({"notes": "x"}) and not A.has_notes({"notes": "  "}) and not A.has_notes({})
    assert not A.has_notes({"notes": 3}) and not A.has_notes(None)


def test_tool_schema_has_notes_and_allows_empty_cells():
    props = A.TOOL["parameters"]["properties"]
    assert "Omit to keep current notes" in props["notes"]["description"]
    assert "notes" not in A.TOOL["parameters"]["required"]
    assert "May be empty" in props["cells"]["description"]


def test_clean_notes():
    assert clean_notes("  hi  ") == "hi"
    assert clean_notes("") is None and clean_notes("   ") is None and clean_notes(None) is None
    assert len(clean_notes("x" * 5000)) == NOTES_MAX


def test_compact_drops_null_fields_only():
    spec = {"method": "GET", "url": "u", "body": None, "auth": None, "params": {}, "assertions": [{"kind": "status",
            "op": "eq", "path": None, "value": 200}]}
    assert A.compact(spec) == {"method": "GET", "url": "u", "params": {}, "assertions": [{"kind": "status", "op": "eq",
                                                                                           "value": 200}]}


def test_result_line_shows_passed_and_failed_checks():
    out = {"row_count": 1, "columns": ["id"], "rows": [{"id": 1}], "passed": False, "assertions": [
        {"label": "Status is 200", "passed": True, "actual": 200},
        {"label": "$.status equals open", "passed": False, "actual": "closed"}]}
    line = A._result_line(SimpleNamespace(status="error", output=out, error={"code": "http_failed", "message": "m"}))
    assert "did not pass" in line and "failed checks: $.status equals open (got \"closed\")" in line
    ok = A._result_line(SimpleNamespace(status="success", output={**out, "passed": True, "assertions": []}, error=None))
    assert ok.startswith("success; passed")
    # outputs without `passed` read as before
    assert A._result_line(SimpleNamespace(status="success", output={"row_count": 0, "columns": []}, error=None)) == \
        "success; 0 rows, columns []"


def test_connection_url_secrets_are_hidden():
    assert A._CONN_SECRET.fullmatch("CONN_BANK_RW_URL") and A._CONN_SECRET.fullmatch("CONN_BANK_URL")
    assert not A._CONN_SECRET.fullmatch("LOGIN_PASSWORD") and not A._CONN_SECRET.fullmatch("CONN_X_URL_OLD")


def test_system_prompt_states_the_rules():
    s = A.SYSTEM
    for phrase in ("`assert` passes when its input has no rows", "No wildcards, filters or `..`", "has_item",
                   "goto {url}", "expectCount {target, count}", "`credential_slot` is the name of a listed secret",
                   "table `bank.loans`", "rerun:\"always\"", "continue_on_fail:true", "refer to the change cell's own",
                   "plan one `note` cell saying so, or no cells with just `notes`", "`notes` is your memory"):
        assert phrase in s, phrase


@pytest.mark.parametrize("kind,spec", [
    ("http", {"method": "POST", "url": "http://x/login"}),
    ("rule", {"op": "count", "left": "{{cells.c1.rows}}"}),
])
def test_new_optional_fields_are_absent_unless_set(kind, spec):
    out = validate_spec(kind, spec)
    assert "rerun" not in out and "continue_on_fail" not in out  # an old spec validates exactly as before
    if kind == "http":
        assert validate_spec(kind, {**spec, "rerun": "always"})["rerun"] == "always"
        with pytest.raises(Exception):
            validate_spec(kind, {**spec, "rerun": "sometimes"})
    assert validate_spec(kind, {**spec, "continue_on_fail": True})["continue_on_fail"] is True


def test_has_item_assertion():
    body = {"items": [{"id": "L1", "status": "open"}, {"id": "L7", "status": "closed"}], "tags": ["a", "b"]}

    def chk(path, value):
        return apitest.check({"kind": "json_path", "path": path, "op": "has_item", "value": value}, 200, {}, body, 5)

    assert chk("$.items", {"id": "L7", "status": "closed"})["passed"]
    assert not chk("$.items", {"id": "L7", "status": "open"})["passed"]
    assert not chk("$.items", {"nope": 1})["passed"]
    assert chk("$.tags", "b")["passed"] and not chk("$.tags", "c")["passed"]
    assert not chk("$.items[0]", {"id": "L1"})["passed"]  # not a list
    assert "has an item with" in chk("$.items", {"id": "L1"})["label"]
    validate_spec("http", {"url": "u", "assertions": [{"kind": "json_path", "path": "$.items", "op": "has_item",
                                                       "value": {"id": 1}}]})
    with pytest.raises(Exception):
        validate_spec("http", {"url": "u", "assertions": [{"kind": "status", "op": "has_item", "value": 1}]})


def test_compare_fails_on_mismatch_only_when_asked():
    from src.services.notebook import rule as R

    spec = {"op": "compare", "left": [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}], "right": [{"id": 1, "n": "a"}],
            "key": "id", "fields": [{"left": "n", "right": "n", "op": "eq"}]}
    assert R.evaluate(spec)["passed"] is True and R.evaluate(spec)["mismatches"] == 1  # unchanged default
    assert R.evaluate({**spec, "fail_on_mismatch": True})["passed"] is False
    assert R.evaluate({**spec, "right": spec["left"], "fail_on_mismatch": True})["passed"] is True
    out = validate_spec("rule", {"op": "compare", "left": "{{cells.c1.rows}}", "key": "id"})
    assert "fail_on_mismatch" not in out
    assert validate_spec("data", {"rows": [{"a": 1}]}).get("from_table", "absent") == "absent"
    assert validate_spec("data", {"from_table": "t"})["from_table"] == "t"


def test_store_keeps_reserved_fields_as_src():
    from src.services.notebook.executors import store as S

    assert S._rows([{"id": "U1", "name": "Ana", "created_at": "x"}]) == [
        {"src_id": "U1", "name": "Ana", "src_created_at": "x"}]
    assert S._rows([{"name": "Ana"}]) == [{"name": "Ana"}]  # rows without them are untouched
    assert S._rows([{"id": 1, "src_id": 2}]) == [{"id": 1, "src_id": 2}]  # no clobbering (still refused, as before)
    assert S.stored_name("id") == "src_id" and S.stored_name("wire_id") == "wire_id"


def test_failed_rows_names_the_inputs():
    out = {"rows": [{"i": 0, "status": "success", "row": {"id": "T1"}},
                    {"i": 1, "status": "error", "error": {"message": "Status is 200 (got 500)"}, "row": {"id": "T2"}}]}
    assert A.failed_rows(out) == 'rows 1 (id "T2") failed; row 1: Status is 200 (got 500)'
    assert A.failed_rows({"rows": [{"i": 3, "status": "error"}]}) == "rows 3 failed"
