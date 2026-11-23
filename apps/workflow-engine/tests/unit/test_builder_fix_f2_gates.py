"""F2: Jobs-contract gate, placeholder gate, plan-vs-delivery ledger, explicit lookup truncation."""

from __future__ import annotations

from src.services import module_generator as mg
from src.services.backend_templates import checks, coverage, quality

HEAD = "import studio\nfrom studio import api\n\n@api.post('/runs')\ndef start(req):\n"


def codes(src):
    return [p["code"] for p in checks.job_contract_problems(src)]


def test_missing_totp_credential_and_policy_are_named():
    src = HEAD + '''    steps = [{"action": "otp", "value": "{{totp:totp}}"}, {"action": "fill", "value": "{{username}}"}]
    return studio.jobs.start({"type": "test-run", "target": {"base_url": "http://x"},
                              "credentials": {"username": studio.secret("U")},
                              "cases": [{"id": "A", "kind": "ui", "steps": steps}]})
'''
    probs = checks.job_contract_problems(src)
    by = {p["code"]: p["message"] for p in probs}
    assert "totp" in by["job_credential_missing"] and "{{username}}" not in by["job_credential_missing"]
    assert "job_no_policy" in by
    assert mg._extra_problems(src)       # reaches the repair loop


def test_complete_job_body_is_clean():
    src = HEAD + '''    return studio.jobs.start({"type": "test-run", "target": {"base_url": "http://x"}, "policy": {"forbidden_names": []},
                              "credentials": {"username": studio.secret("U"), "totp": studio.secret("T")},
                              "cases": [{"id": "A", "kind": "ui", "steps": [{"value": "{{totp:totp}}"}]}]})
'''
    assert codes(src) == []


def test_required_fields_and_ignored_fields():
    src = HEAD + '''    return studio.jobs.start({"type": "test-run", "policy": {"x": 1}, "target": {"allowed_hosts": []},
        "cases": [{"id": "a", "kind": "api", "requests": [{"method": "GET", "path": "/", "expect": {"status": 200, "max_ms": 5}}]}]})
'''
    c = codes(src)
    assert c.count("job_missing_field") == 1 and "job_policy_shape" in c and "job_ignored_field" in c
    assert "job_missing_field" in codes(HEAD + '    return studio.jobs.start({"type": "triage", "job_id": "j"})\n')


def test_unresolvable_bodies_are_not_guessed_at():
    src = HEAD + '    body = build(req)\n    return studio.jobs.start(body)\n'
    assert codes(src) == []
    src = HEAD + '    b = {"type": "triage", "job_id": "j"}\n    b.update(extra(req))\n    return studio.jobs.start(b)\n'
    assert codes(src) == []


def test_placeholder_bodies_markers_and_sample_data():
    src = '''from studio import api
@api.get("/a")
def a(req):
    raise NotImplementedError

@api.get("/b")
def b(req):
    # TODO: query the table
    return {"rows": [{"id": 1, "name": "Sample"}, {"id": 2, "name": "Other"}]}

@api.get("/c")
def c(req):
    """Placeholder until Jobs is wired."""
    return {}
'''
    got = {(p["code"]) for p in quality.placeholder_problems(src)}
    assert got == {"placeholder_body", "placeholder_data", "placeholder_marker"}
    real = '''import studio
from studio import api
@api.get("/ok")
def ok(req):
    """List. Returns {rows}"""
    return {"rows": studio.db.table("t").all()}
@api.get("/kinds")
def kinds(req):
    return {"kinds": ["ui", "api"]}
'''
    assert quality.placeholder_problems(real) == []


def test_route_ledger_compares_plan_to_source():
    ledger = coverage.planned(["POST /runs", "GET /runs/{id}", "GET /coverage"])
    ledger.update(coverage.routes_in_text("Needs `GET /runs/<run_id>` and DELETE /x/*"))
    decls = [{"method": "POST", "path": "/runs"}, {"method": "GET", "path": "/runs/{run_id}"}]
    assert coverage.missing(ledger, decls) == ["GET /coverage"]
    assert coverage.missing(ledger, decls, {"GET /coverage"}) == []


def test_lookup_truncation_is_explicit_and_resumable():
    text = "x" * (mg.MAX_LOOKUP_CHARS + 500)
    first = mg.cut_lookup(text)
    assert f"truncated: 500 more chars; call read_function with offset={mg.MAX_LOOKUP_CHARS}" in first
    assert mg.cut_lookup(text, mg.MAX_LOOKUP_CHARS) == "x" * 500
