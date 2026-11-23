"""The eval binder renames hidden-test form inputs to the field ids the built form declares (case ``input_aliases``)."""

from tests.support import ai_eval

ALIASES = {"account_id": ["account_number", "account_no"], "customer_email": ["email"]}


def _defn(props):
    return {"nodes": [{"name": "Form", "type": "FormTrigger", "parameters": {"input_schema": {"type": "object", "properties": {p: {} for p in props}}}}], "connections": []}


def _bound(props, item):
    r = ai_eval.Resolver(_defn(props), {})
    t = {"name": "n", "input": {"items": [item]}, "expect": [{"node": "$trigger", "op": "status", "value": "success", "subject": "node"}]}
    return ai_eval.bind_test(t, r, "t_00000001", aliases=ALIASES)["test"]["input"]["items"][0]


def test_a_key_is_renamed_to_the_alias_the_form_declares():
    assert _bound(["account_number", "customer_email"], {"account_id": "A1", "customer_email": "x@y.z", "reason": "r"}) == {"account_number": "A1", "customer_email": "x@y.z", "reason": "r"}


def test_a_declared_key_is_never_renamed_and_unknown_names_are_not_guessed():
    assert _bound(["account_id", "email"], {"account_id": "A1", "customer_email": "x"}) == {"account_id": "A1", "email": "x"}
    assert _bound(["acct"], {"account_id": "A1"}) == {"account_id": "A1"}


def test_every_case_alias_names_a_hidden_input_key():
    for case in ai_eval.load_cases():
        keys = {k for t in case["hidden_tests"] for it in (t.get("input") or {}).get("items", []) for k in it}
        assert set(case.get("input_aliases") or {}) <= keys, case["id"]
