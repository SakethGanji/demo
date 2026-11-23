"""UI-DESIGN §10.1 — the pure parts of the module generator (no DB, no LLM, no sandbox)."""

from __future__ import annotations

import pytest

from src.services import module_generator as mg


def test_scan_usage_literals_lines_and_dynamic_names():
    src = '''import studio
from studio import db, secret

def f():
    a = studio.db.table("orders")
    b = db.table("refunds").list()
    c = studio.db.table(name)
    d = f"Bearer {studio.secret('PAY')}"
    e = secret("OTHER")
    g = studio.secret(key_var)
    h = studio.db.table("orders")
'''
    u = mg.scan_usage(src)
    assert u["tables"] == {"orders": [5, 11], "refunds": [6]}
    assert u["secrets"] == {"PAY": [8], "OTHER": [9]}
    assert u["dynamic_tables"] == [7] and u["dynamic_secrets"] == [10]


def test_scan_usage_on_a_syntax_error_is_empty_not_a_crash():
    assert mg.scan_usage("def (:")["tables"] == {}


@pytest.mark.parametrize("problem,line", [
    ({"code": "syntax_error", "line": 3, "message": "expected ':'"}, "- syntax_error line 3: expected ':'"),
    ({"code": "route_conflict", "line": 7, "message": "POST /orders already exists (workflow 'Create order')"},
     "- route_conflict line 7: POST /orders already exists (workflow 'Create order')"),
    ({"code": "module_name", "line": None, "message": "'orders_api' is taken, choose another"},
     "- module_name: 'orders_api' is taken, choose another"),
    ({"code": "missing_table", "line": None, "name": "refunds", "message": "x"},
     "- missing_table refunds: declare it in tables"),
    ({"code": "no_code", "line": None, "message": "x"}, "- no_code: call emit_module with the full module"),
    ({"code": "invalid_decorator", "line": None, "message": "path must be a string literal"},
     "- invalid_decorator: path must be a string literal"),
])
def test_repair_lines_match_the_spec_wording(problem, line):
    assert mg.problem_line(problem) == line


def test_repair_header_is_the_pinned_line():
    assert mg.REPAIR_HEADER == "Fix these problems in the module you emitted:"


def test_declared_tables_validation():
    ok, errs = mg.declared_tables([{"name": "refunds", "columns": [
        {"name": "reason", "type": "string", "required": True}, {"name": "amount", "type": "number"}]}])
    assert errs == [] and ok == [{"name": "refunds", "columns": [
        {"name": "reason", "type": "string", "required": True}, {"name": "amount", "type": "number", "required": False}]}]
    ok, errs = mg.declared_tables([{"name": "refunds", "columns": [{"name": "x", "type": "float"}]},
                                   {"name": "Bad-Name", "columns": []}, "nope"])
    assert ok == [] and [e["code"] for e in errs] == ["invalid_table"] * 3
    assert mg.declared_tables(None) == ([], [])


def test_fenced_python_takes_the_first_block():
    assert mg._fenced_python("hi\n```python\nA = 1\n```\n```python\nB = 2\n```") == "A = 1\n"
    assert mg._fenced_python("```\nplain\n```") == "plain\n"
    assert mg._fenced_python("no code here") is None and mg._fenced_python(None) is None


def test_scrub_replaces_every_encoding_and_reports_only_when_something_changed():
    import base64
    from src.services.vars_substitution import expand_secret_variants

    v = expand_secret_variants({"s3cr3t-value"})
    b64 = base64.b64encode(b"s3cr3t-value").decode()
    msgs = [{"role": "user", "content": f"a s3cr3t-value b {b64}"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"arguments": '{"k": "s3cr3t-value"}'}}]}]
    out, changed = mg._scrub(msgs, v)
    assert changed and "s3cr3t" not in str(out) and b64 not in str(out) and out[0]["content"] == "a *** b ***"
    clean = [{"role": "user", "content": "nothing here"}]
    assert mg._scrub(clean, v) == (clean, False)
    assert mg._scrub(msgs, set()) == (msgs, False)
    short, changed = mg._scrub([{"role": "user", "content": "abc"}], expand_secret_variants({"abc"}))
    assert not changed and short[0]["content"] == "abc"       # length < 4 is never scrubbed


def test_request_model_rejects_what_the_spec_rejects():
    from pydantic import ValidationError

    ok = mg.GenerateRequest.model_validate({"prompt": "x"})
    assert ok.self_test is True and ok.conversation == []
    for bad in ({"prompt": ""}, {"prompt": "x" * 8001}, {"prompt": "x", "extra": 1},
                {"prompt": "x", "current_source": "é" * 100_001},
                {"prompt": "x", "conversation": [{"role": "tool", "content": "x"}]}):
        with pytest.raises(ValidationError):
            mg.GenerateRequest.model_validate(bad)
    assert mg.GenerateRequest.model_validate({"prompt": "x", "current_source": "a" * 200_000})


def test_lock_is_per_project_and_actor_and_release_is_idempotent():
    k1 = mg.try_acquire("p1", None)
    assert k1 == ("p1", "local") and mg.try_acquire("p1", None) is None and mg.try_acquire("p1", "local") is None
    k2 = mg.try_acquire("p1", "alice")
    k3 = mg.try_acquire("p2", None)
    assert k2 and k3
    for k in (k1, k1, k2, k3, None):
        mg.release(k)
    assert mg.try_acquire("p1", None) == k1
    mg.release(k1)


def test_undefined_sdk_names():
    assert mg.undefined_sdk_names("from studio import api\n@api.get('/x')\ndef f(req):\n    return {}\n") == []
    assert mg.undefined_sdk_names("import studio\nfrom studio import api, Response as R\ndef f():\n    R(); HTTPError(1)\n") \
        == [("HTTPError", 4)]
    assert mg.undefined_sdk_names("@api.get('/x')\ndef f(req):\n    return studio.db.table('t').list()\n") \
        == [("api", 1), ("studio", 3)]
    assert mg.undefined_sdk_names("def api(): pass\n@api\ndef g(): pass\n") == []
    assert mg.undefined_sdk_names("def (:") == []


def test_rules_state_the_single_req_handler_contract():
    """Regression: Gemini wrote `def close(req, id=None)` and the route always 404'd."""
    from src.services.module_generator import RULES

    assert 'exactly one argument, `req`' in RULES
    assert 'req.params["id"]' in RULES
