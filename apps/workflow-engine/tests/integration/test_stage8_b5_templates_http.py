"""Stage 8 B5 over HTTP on real Postgres: the template catalog, preview, install of all six starter templates into fresh
projects with their own tests run through the existing test runner, then re-install, copy, collision and export
(DESIGN-STAGE8-REUSE 8, 11)."""

from __future__ import annotations

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

SLUGS = ["aml-alert-triage", "card-dispute-intake", "customer-notify", "kyc-review", "loan-precheck", "refund-triage"]


def new_project(api, allowlist=None) -> str:
    r = api.post("/projects", {"name": uniq("b5-proj")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    if allowlist is not None:
        r = api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": allowlist}})
        assert r.status_code == 200, r.text
    return pid


def install(api, project: str, slug: str, **body):
    return api.post(f"/projects/{project}/templates/{slug}/install", body or None)


def run_tests(api, workflow_id: str) -> dict:
    r = api.post(f"/workflows/{workflow_id}/tests/run", {})
    assert r.status_code == 200, r.text
    return r.json()


# ── the catalog ──────────────────────────────────────────────────────────────────────────────


def test_the_catalog_lists_the_six_starter_templates(api):
    r = api.get("/templates")
    assert r.status_code == 200, r.text
    rows = {t["slug"]: t for t in r.json()}
    assert sorted(rows) == SLUGS
    refund = rows["refund-triage"]
    assert refund["version"] == "1.0.0" and refund["category"] == "payments"
    assert [(w["key"], w["role"]) for w in refund["workflows"]] == [("main", "entry"), ("eligibility", "subworkflow")]
    assert refund["tables"] == ["refund_limits"] and refund["apis"] == ["refunds"]
    assert refund["requires"]["templates"] == [{"slug": "customer-notify", "version": "^1"}]
    assert refund["requires"]["secrets"][0]["name"] == "REFUNDS_API_KEY"
    assert "installed_in" not in refund

    assert {t["slug"] for t in api.get("/templates", params={"category": "compliance"}).json()} == {"aml-alert-triage", "kyc-review"}
    assert [t["slug"] for t in api.get("/templates", params={"q": "dispute"}).json()] == ["card-dispute-intake"]
    assert api.get("/templates", params={"q": "zzzz"}).json() == []


def test_a_template_has_its_bundle_readme_and_preview(api):
    r = api.get("/templates/refund-triage")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["format"] == "wf-template/1" and t["slug"] == "refund-triage" and len(t["bundle_hash"]) == 64
    assert t["readme"].startswith("# Refund triage")
    main = t["preview"]["main"]
    assert main["role"] == "entry" and main["interface"] == "form"
    assert {n["name"] for n in main["nodes"]} >= {"Refund request", "Check eligibility", "Manager approval", "Issue refund"}
    assert any(c["source_node"] == "Manager approval" and c["source_output"] == "approved" for c in main["connections"])
    assert t["api_summaries"][0]["base_url"] == "http://refunds.bank.example"
    assert t["api_summaries"][0]["operations"][0]["operation_id"] == "issueRefund"
    assert [w["key"] for w in t["workflows"]] == ["main", "eligibility"]


def test_an_unknown_template_is_a_plain_404(api):
    r = api.get("/templates/no-such-thing")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "TEMPLATE_NOT_FOUND"
    project = new_project(api)
    r = install(api, project, "no-such-thing")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "TEMPLATE_NOT_FOUND"
    assert api.post("/projects/proj_nope/templates/refund-triage/install", {}).status_code == 404
    assert api.post("/projects/proj_nope/templates/refund-triage/install-preview", {}).status_code == 404
    assert api.get("/projects/proj_nope/template-installs").status_code == 404
    assert api.get(f"/projects/{project}/template-installs/tpi_nope").status_code == 404
    assert api.get("/workflows/wf_nope/export-template").status_code == 404
    r = install(api, project, "customer-notify", version="9.9.9")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "TEMPLATE_VERSION_NOT_FOUND"
    r = api.post(f"/projects/{project}/templates/customer-notify/install", {"clash": "overwrite"})
    assert r.status_code == 422


# ── install every template ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", SLUGS)
def test_each_template_installs_into_a_fresh_project_and_its_tests_pass(api, slug):
    project = new_project(api)
    r = install(api, project, slug)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "installed" and body["template"] == {"slug": slug, "version": "1.0.0"} and body["install_id"].startswith("tpi_")
    for key, summary in body["tests"].items():
        assert summary["failed"] == 0 and summary["passed"] >= 3, (key, summary)
    assert set(body["tests"]) == {w["key"] for w in body["workflows"]}

    # the existing test runner agrees, workflow by workflow, after the install
    for w in body["workflows"]:
        report = run_tests(api, w["id"])
        assert report["summary"]["failed"] == 0 and report["summary"]["errored"] == 0, (w["key"], report["summary"])
        assert report["summary"]["total"] >= 3 and report["summary"]["approved_total"] == report["summary"]["total"]

    # sub-workflows are published (v1), entry workflows stay drafts with their triggers off
    for w in body["workflows"]:
        detail = api.get(f"/workflows/{w['id']}").json()
        assert detail["project_id"] == project
        if w["role"] == "subworkflow":
            assert w["published_version"] == 1 and detail["active"] is True
        else:
            assert w["published_version"] is None and detail["active"] is False
    # the install shows up in the project, with its mapping
    listed = api.get(f"/projects/{project}/template-installs").json()
    assert slug in {i["slug"] for i in listed}
    detail = api.get(f"/projects/{project}/template-installs/{body['install_id']}").json()
    assert detail["mapping"]["workflows"] == {w["key"]: w["id"] for w in body["workflows"]}
    assert detail["install_name"] == slug and detail["status"] == "installed"


def test_installed_entry_workflows_publish_once_their_secrets_exist(api):
    project = new_project(api)
    body = install(api, project, "refund-triage").json()
    main = next(w for w in body["workflows"] if w["key"] == "main")
    r = api.post(f"/workflows/{main['id']}/publish", {})
    assert r.status_code == 422, "REFUNDS_API_KEY is not defined yet"
    assert any(f["code"] == "WF006" and f.get("path") == "REFUNDS_API_KEY" for f in r.json()["detail"]["findings"])
    need = next(n for n in body["needs"] if n["name"] == "REFUNDS_API_KEY")
    assert need["kind"] == "secret" and need["environments_missing"] == ["dev", "prod"]
    r = api.post(f"/projects/{project}/secrets", {"key": "REFUNDS_API_KEY", "value": "k-test", "type": "string", "environment": "dev"})
    assert r.status_code in (200, 201), r.text
    r = api.post(f"/workflows/{main['id']}/publish", {})
    assert r.status_code == 200, r.text
    again = api.get(f"/projects/{project}/template-installs/{body['install_id']}").json()
    assert {n["name"]: n["environments_missing"] for n in again["needs"]}["REFUNDS_API_KEY"] == ["prod"]


# ── preview, re-install, copies, collisions ──────────────────────────────────────────────────


def test_preview_then_install_then_reinstall_then_copy(api):
    project = new_project(api)
    p1 = api.post(f"/projects/{project}/templates/refund-triage/install-preview", {})
    assert p1.status_code == 200, p1.text
    plan = p1.json()
    assert plan["status"] == "new" and plan["dependencies"] == [{"slug": "customer-notify", "version": "1.0.0", "status": "will_install"}]
    assert [w["name"] for w in plan["plan"]["workflows"]] == ["Refund eligibility", "Refund triage"]
    assert [n["name"] for n in plan["needs"]] == ["REFUNDS_API_KEY"]
    assert api.get(f"/projects/{project}/workflows").json() == [], "a preview writes nothing"
    assert api.get(f"/projects/{project}/template-installs").json() == []
    assert api.get("/templates", params={"project": project}).json() and \
        all(t["installed_in"] == [] for t in api.get("/templates", params={"project": project}).json())

    first = install(api, project, "refund-triage", run_tests=False)
    assert first.status_code == 201 and "tests" not in first.json()
    workflows = api.get(f"/projects/{project}/workflows").json()
    assert {w["name"] for w in workflows} == {"Refund triage", "Refund eligibility", "Notify customer"}
    tables = api.get(f"/projects/{project}/tables").json()
    assert {t["name"]: t["row_count"] for t in tables} == {"refund_limits": 2, "notify_templates": 6}
    installed = {t["slug"]: t["installed_in"] for t in api.get("/templates", params={"project": project}).json()}
    assert installed["refund-triage"] == [project] and installed["customer-notify"] == [project] and installed["kyc-review"] == []

    again = install(api, project, "refund-triage")
    assert again.status_code == 200 and again.json()["status"] == "already_installed"
    assert again.json()["install_id"] == first.json()["install_id"]
    assert {w["id"] for w in again.json()["workflows"]} == {w["id"] for w in first.json()["workflows"]}
    assert len(api.get(f"/projects/{project}/workflows").json()) == 3
    assert {t["name"]: t["row_count"] for t in api.get(f"/projects/{project}/tables").json()} == {"refund_limits": 2, "notify_templates": 6}
    assert len(api.get(f"/projects/{project}/template-installs").json()) == 2

    plan2 = api.post(f"/projects/{project}/templates/refund-triage/install-preview", {}).json()
    assert plan2["status"] == "already_installed" and plan2["installed"]["install_id"] == first.json()["install_id"]

    copy_ = install(api, project, "refund-triage", as_copy=True)
    assert copy_.status_code == 201, copy_.text
    c = copy_.json()
    assert c["install_name"] == "refund-triage-2" and c["slugs"] == {"main": "refund-triage-2"}
    names = {w["name"] for w in c["workflows"]}
    assert names == {"Refund triage (2)", "Refund eligibility (2)"}
    assert all(f["failed"] == 0 for f in c["tests"].values())
    assert {w["id"] for w in c["workflows"]}.isdisjoint({w["id"] for w in first.json()["workflows"]})
    assert {(r["kind"], r["from"], r["to"]) for r in c["renamed"]} == {
        ("workflow", "Refund triage", "Refund triage (2)"), ("workflow", "Refund eligibility", "Refund eligibility (2)"),
        ("table", "refund_limits", "refund_limits_2"), ("interface_slug", "refund-triage", "refund-triage-2")}
    assert c["apis"][0]["reused"] is True, "an identical API is registered once"
    assert len(api.get(f"/projects/{project}/workflows").json()) == 5, "the dependency was reused, not copied"


def test_a_name_clash_is_renamed_by_default_and_refused_on_request(api):
    project = new_project(api)
    r = api.post(f"/projects/{project}/workflows", {"name": "Notify customer", "nodes": [
        {"name": "Go", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}}], "connections": [], "settings": {}})
    assert r.status_code == 201, r.text
    r = install(api, project, "customer-notify", clash="fail")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "TEMPLATE_NAME_CLASH"
    assert r.json()["detail"]["clashes"] == [{"kind": "workflow", "name": "Notify customer"}]
    assert api.get(f"/projects/{project}/tables").json() == []
    r = install(api, project, "customer-notify")
    assert r.status_code == 201, r.text
    assert r.json()["renamed"] == [{"kind": "workflow", "from": "Notify customer", "to": "Notify customer (2)"}]
    assert {w["name"] for w in api.get(f"/projects/{project}/workflows").json()} == {"Notify customer", "Notify customer (2)"}


def test_installing_into_a_project_that_restricts_egress_adds_the_hosts_and_the_tests_still_pass(api):
    closed = new_project(api, allowlist=["unrelated.bank.example"])
    r = install(api, closed, "loan-precheck")
    assert r.status_code == 201, r.text
    assert set(r.json()["egress_added"]) == {"risk.bank.example", "fees.bank.example"}
    assert all(f["failed"] == 0 for f in r.json()["tests"].values())
    allow = api.get(f"/projects/{closed}").json()["settings"]["egress_allowlist"]
    assert allow == ["unrelated.bank.example", "risk.bank.example", "fees.bank.example"]
    open_ = new_project(api)
    assert install(api, open_, "loan-precheck").json()["egress_added"] == []
    assert api.get(f"/projects/{open_}").json()["settings"]["egress_allowlist"] == []


def test_the_aml_template_creates_its_variable_with_the_default(api):
    project = new_project(api)
    body = install(api, project, "aml-alert-triage").json()
    assert all(f["failed"] == 0 for f in body["tests"].values()), body["tests"]
    needs = {n["name"]: n for n in body["needs"]}
    assert needs["AML_HIGH_SCORE"]["kind"] == "variable" and needs["AML_HIGH_SCORE"]["environments_missing"] == ["prod"]
    assert needs["AML_API_KEY"]["kind"] == "secret"
    secrets = api.get(f"/projects/{project}/secrets").json()
    row = next(s for s in secrets if s["key"] == "AML_HIGH_SCORE")
    assert row["environment"] == "dev" and row["value"] == "70" and row["type"] == "number"


# ── export ───────────────────────────────────────────────────────────────────────────────────


def test_export_returns_a_bundle_that_validates(api):
    from src.services.template_bundle import parse_bundle

    project = new_project(api)
    body = install(api, project, "kyc-review", run_tests=False).json()
    main = next(w["id"] for w in body["workflows"] if w["key"] == "main")
    r = api.get(f"/workflows/{main}/export-template", params={"slug": "kyc-exported", "version": "3.1.0"})
    assert r.status_code == 200, r.text
    out = r.json()
    bundle = out["bundle"]
    assert out["warnings"] == ["$vars.KYC_API_KEY is not defined in this project; it is listed as a variable without a default"]
    assert bundle["slug"] == "kyc-exported" and bundle["version"] == "3.1.0"
    assert bundle["requires"]["variables"] == [{"name": "KYC_API_KEY", "default": None, "type": "string"}]
    assert {w["role"] for w in bundle["workflows"]} == {"entry", "subworkflow"}
    parse_bundle(bundle)
    assert {t["name"] for t in bundle["tables"]} >= {"kyc_risk_thresholds"}
    assert all(t["seed_rows"] == [] for t in bundle["tables"])
    default_slug = api.get(f"/workflows/{main}/export-template").json()["bundle"]["slug"]
    assert default_slug == "kyc-review"


# ── the parts the mocked tests cannot reach: real data tables, code and expressions on the installed workflows ─────


def pinned_run_put(api, workflow_id: str, pins: dict) -> None:
    detail = api.get(f"/workflows/{workflow_id}").json()["definition"]
    keep = ("name", "type", "label", "parameters", "position", "retry_on_fail", "retry_delay", "disabled", "on_error")
    nodes = []
    for n in detail["nodes"]:
        node = {k: n[k] for k in keep if k in n and n[k] is not None}
        if n["name"] in pins:
            node["pinned_data"] = [{"json": j} for j in pins[n["name"]]]
        nodes.append(node)
    r = api.put(f"/workflows/{workflow_id}", {"nodes": nodes, "connections": detail["connections"]})
    assert r.status_code == 200, r.text


def pinned_run(api, workflow_id: str, pins: dict, input_data: dict):
    """Pin the outside steps (HTTP, AI, sub-workflow calls) of the installed draft, leave tables / code / If / Set real, run it."""
    pinned_run_put(api, workflow_id, pins)
    r = api.post(f"/workflows/{workflow_id}/run", {"input_data": input_data})
    assert r.status_code == 200, r.text
    run = r.json()
    return run["data"], run


def parent_of(api, project: str, sub_id: str, pins: dict, item: dict) -> str:
    """A throwaway workflow that calls the draft of ``sub_id`` (whose outside steps are pinned) with ``item``."""
    pinned_run_put(api, sub_id, pins)
    parent = api.post(f"/projects/{project}/workflows", {"name": uniq("b5-parent"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "call", "type": "ExecuteWorkflow", "parameters": {"workflowId": sub_id, "version": "draft", "inputMode": "custom",
                                                                     "customInput": item}, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "call", "source_output": "main", "target_input": "main"}], "settings": {}})
    assert parent.status_code == 201, parent.text
    return parent.json()["id"]


def run_sub(api, project: str, sub_id: str, pins: dict, item: dict):
    """Run a sub-workflow's draft for real through a throwaway parent; returns its node outputs ``{node: [json]}``."""
    run = api.post(f"/workflows/{parent_of(api, project, sub_id, pins, item)}/run", {}).json()
    assert run["status"] == "success", run
    detail = api.get(f"/executions/{run['execution_id']}").json()
    assert len(detail["children"]) == 1 and detail["children"][0]["status"] == "success", detail["children"]
    child = api.get(f"/executions/{detail['children'][0]['execution_id']}").json()
    return {n: [i["json"] for i in items] for n, items in child["node_data"].items()}


def first(data, node):
    assert node in data and data[node], f"{node} produced nothing: {sorted(data)}"
    item = data[node][0]
    return item["json"] if isinstance(item, dict) and set(item) == {"json"} else item


def http_reply(code, body):
    return [{"statusCode": code, "headers": {}, "body": body}]


def installed(api, slug):
    project = new_project(api)
    body = install(api, project, slug, run_tests=False).json()
    return project, {w["key"]: w["id"] for w in body["workflows"]}


def test_notify_looks_its_message_up_in_the_real_table_and_fills_the_placeholders(api):
    project, ids = installed(api, "customer-notify")
    data = run_sub(api, project, ids["notify"], {"Send message": http_reply(200, {"delivered": True})},
                   {"to": "dana@example.com", "channel": "email", "template": "kyc_approved", "vars": {"name": "Dana"}})
    assert first(data, "Find message")["name"] == "kyc_approved"
    assert first(data, "Render message")["text"] == "Thank you, Dana. Your identity check is complete."
    assert first(data, "Delivered") == {"delivered": True, "channel": "email"}
    data = run_sub(api, project, ids["notify"], {"Send message": http_reply(400, {"error": "bad recipient"})},
                   {"to": "x@example.com", "channel": "sms", "template": "refund_declined", "vars": {"reason": "No receipt."}})
    assert first(data, "Render message")["text"] == "We could not approve your refund request. No receipt."
    assert first(data, "Not delivered") == {"delivered": False, "error": "bad recipient"}


def test_refund_eligibility_compares_with_the_real_limit_of_the_segment(api):
    project, ids = installed(api, "refund-triage")
    classify = {"Classify reason": [{"response": "damaged", "structured": {"category": "damaged"}, "toolCalls": [], "iterations": 1}]}
    base = {"customer_email": "dana@example.com", "reason": "damaged parcel"}
    for amount, segment, eligible, limit in [(500, "retail", True, 500), (501, "retail", False, 500), (2000, "premier", True, 2000),
                                            (2001, "premier", False, 2000)]:
        data = run_sub(api, project, ids["eligibility"], classify, {**base, "amount": amount, "segment": segment})
        decided = first(data, "Decide")
        assert (decided["eligible"], decided["limit"], decided["amount"]) == (eligible, limit, amount), (amount, segment, decided)
    # a missing required input is refused by the sub-workflow's own contract, before anything runs
    parent = parent_of(api, project, ids["eligibility"], classify, {"customer_email": "dana@example.com"})
    run = api.post(f"/workflows/{parent}/run", {}).json()
    assert run["status"] == "failed" and run["errors"][0]["error_code"] == "SUBWORKFLOW_FAILED"
    assert "required property" in run["errors"][0]["error"]


def test_kyc_screening_maps_scores_to_the_rating_bands_of_the_real_table(api):
    project, ids = installed(api, "kyc-review")
    clean = {"Sanctions check": http_reply(200, {"match": False, "list_name": ""}),
             "Identity check": http_reply(200, {"verified": True, "reason": ""})}
    who = {"customer_id": "c-1", "full_name": "Dana Example", "country": "GB"}
    for score, risk in [(0, "low"), (39, "low"), (40, "medium"), (69, "medium"), (70, "high"), (100, "high")]:
        data = run_sub(api, project, ids["screen"], {**clean, "Risk score": http_reply(200, {"score": score, "band": "A"})}, who)
        assert first(data, "Assess")["risk"] == risk, (score, first(data, "Assess"))
    data = run_sub(api, project, ids["screen"], {**clean, "Sanctions check": http_reply(200, {"match": True, "list_name": "TEST-LIST"}),
                                                 "Risk score": http_reply(200, {"score": 10, "band": "A"})}, who)
    assert first(data, "Assess")["risk"] == "high" and first(data, "Assess")["hits"] == ["sanctions match: TEST-LIST"]


def test_dispute_reason_codes_decide_fraud_from_the_real_table(api):
    _p, ids = installed(api, "card-dispute-intake")
    pins = {"Create dispute case": http_reply(201, {"case_id": "D-7", "status": "open"}),
            "Block card": http_reply(200, {"card_last4": "4242", "status": "blocked"}),
            "Provisional credit": http_reply(200, {"credit_id": "PC-1", "amount": 50}),
            "Notify customer": [{"delivered": True, "channel": "email"}]}
    for code, fraud in [("R-FRAUD", True), ("R-QUALITY", False), ("R-DUPLICATE", False), ("R-UNKNOWN", False)]:
        body = {"customer_id": "c-1", "card_last4": "4242", "merchant": "Test Merchant", "amount": 50, "description": "x",
                "reason_code": code, "email": "dana@example.com"}
        data, _ = pinned_run(api, ids["main"], pins, body)   # a manual run wraps the payload as the webhook's body
        assert first(data, "Classify reason")["fraud"] is fraud and first(data, "Classify reason")["case_id"] == "D-7", code
        assert ("Block card" in data and bool(data["Block card"])) is fraud, code


def test_aml_score_adds_the_weights_of_the_real_rules(api):
    project, ids = installed(api, "aml-alert-triage")
    pins = {"Customer risk": http_reply(200, {"score": 50, "band": "B"}),
            "Summarise": [{"response": "x", "structured": {"reason": "Why."}, "toolCalls": [], "iterations": 1}]}
    for text, score in [("routine payroll", 50), ("Several cash deposits", 75), ("cash deposits and a shell company", 100),
                        ("rapid movement of funds", 70)]:
        data = run_sub(api, project, ids["score"], pins, {"alert_id": "AL-1", "customer_id": "c-1", "description": text})
        assert first(data, "Result")["score"] == score, (text, first(data, "Score"))
    # the entry workflow splits the listing into one item per alert and routes on the real AML_HIGH_SCORE variable (70)
    alerts = [{"alert_id": "AL-1", "customer_id": "c-1", "description": "a"}, {"alert_id": "AL-2", "customer_id": "c-2", "description": "b"}]
    for score, high in [(90, True), (70, True), (69, False)]:
        data, _ = pinned_run(api, ids["main"], {"List open alerts": http_reply(200, {"alerts": alerts}),
                                                "Score alert": [{"alert_id": "AL-1", "customer_id": "c-1", "score": score, "reason": "r"}],
                                                "Assign to analyst": http_reply(201, {"ticket_id": "T", "queue": "compliance"}),
                                                "Close low risk": http_reply(200, {"alert_id": "AL-1", "status": "closed"})}, {})
        assert [d["json"]["alert_id"] for d in data["Split alerts"]] == ["AL-1", "AL-2"]
        assert ("Assign to analyst" in data) is high and ("Close low risk" in data) is not high, score


def test_loan_precheck_applies_the_real_policy(api):
    _p, ids = installed(api, "loan-precheck")
    quote = http_reply(200, {"origination_fee": 100, "apr": 8.1})
    form = lambda amount, term, income: {"customer_id": "c-1", "requested_amount": amount, "term_months": term, "monthly_income": income}  # noqa: E731
    cases = [("A", form(12000, 24, 3000), True), ("A", form(60000, 60, 90000), False), ("B", form(20000, 40, 2000), True),
             ("B", form(20000, 10, 2000), False), ("C", form(100, 10, 5000), False)]
    for band, f, approved in cases:
        data, _ = pinned_run(api, ids["main"], {"Credit score": http_reply(200, {"score": 700, "band": band}), "Quote fees": quote}, f)
        got = first(data, "Affordability")
        assert got["approved"] is approved, (band, f, got)
        assert ("Quote fees" in data and bool(data["Quote fees"])) is approved
