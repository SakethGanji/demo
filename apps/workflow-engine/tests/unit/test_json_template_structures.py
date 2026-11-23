"""resolve_json_template on parameters stored as real JSON (a list or object), not a JSON string.

Postgres queryParameters / statements are `json` properties: the UI stores a string, but a workflow written as
JSON (the builder, an import, the API) holds a real list. Both must resolve {{ $json... }} per item; a list used to
come back untouched, so the query ran with the literal "{{ ... }}" text as its parameter.
"""
from src.engine.expression_engine import ExpressionEngine, expression_engine
from src.engine.types import NodeData


def ctx():
    items = [NodeData(json={"body": {"loan_id": "L1", "months": 3}, "plan": "holiday"})]
    return ExpressionEngine.create_context(items, {}, "exec_t", 0)


def test_list_params_resolve_typed():
    out = expression_engine.resolve_json_template(["{{ $json.body.loan_id }}", "{{ $json.body.months }}", "fixed"], ctx())
    assert out == ["L1", 3, "fixed"]


def test_statements_list_resolves_nested_params():
    stmts = [{"query": "UPDATE t SET plan = %s WHERE id = %s", "params": ["{{ $json.plan }}", "{{ $json.body.loan_id }}"]},
             {"query": "INSERT INTO a VALUES (%s)", "params": ["loan {{ $json.body.loan_id }}"]}]
    out = expression_engine.resolve_json_template(stmts, ctx())
    assert out[0]["params"] == ["holiday", "L1"]
    assert out[1]["params"] == ["loan L1"]
    assert out[0]["query"] == stmts[0]["query"]


def test_dict_named_params_resolve():
    assert expression_engine.resolve_json_template({"id": "{{ $json.body.loan_id }}"}, ctx()) == {"id": "L1"}


def test_string_form_unchanged():
    assert expression_engine.resolve_json_template('["{{ $json.body.loan_id }}"]', ctx()) == ["L1"]
