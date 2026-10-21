"""The ProjectPlan's pure layer: normalization, validation codes PLAN001-PLAN014, plan diffs and the consumer helpers every
builder reads the plan through."""

from __future__ import annotations

import pytest

from src.services import module_generator as mg
from src.services import project_plan as pp
from tests.unit.plan_fixture import qa_plan

FACTS = {"tables": [{"name": "environments", "columns": [{"name": "name", "type": "string"}], "row_count": 2}],
         "routes": [{"method": "GET", "path": "/health"}]}


def codes(issues, severity=None):
    return {i["code"] for i in issues if severity is None or i["severity"] == severity}


def tbl(plan, name):
    return next(t for t in plan["data"]["tables"] if t["name"] == name)


def route(plan, key):
    return next(r for r in plan["routes"] if r["key"] == key)


# ── normalize ────────────────────────────────────────────────────────────────


def test_fixture_is_valid():
    issues = pp.validate_plan(qa_plan(), FACTS)
    assert not [i for i in issues if i["severity"] == "error"], issues


def test_normalize_fills_every_section_and_is_idempotent():
    n = pp.normalize({"title": " T "})
    assert n["v"] == 1 and n["title"] == "T"
    for k in ("requirements", "environments", "secrets", "integrations", "data", "modules", "routes", "apps", "nfr",
              "build_order", "migrations"):
        assert k in n
    assert set(n["requirements"]) >= {"answers", "assumptions", "limitations", "acceptance"}
    full = pp.normalize(qa_plan())
    assert pp.normalize(full) == full


def test_normalize_route_keys_hosts_system_columns_and_conventions():
    p = qa_plan()
    p["routes"].append({"method": "get", "path": "/x", "module": "runs", "pagination": True})
    p["secrets"][0]["allowed_hosts"] = ["http://LocalHost:4590/path", "bank.example.com"]
    tbl(p, "tests")["columns"].append({"name": "created_at", "type": "datetime"})
    tbl(p, "tests")["audit"] = True
    tbl(p, "tests")["soft_delete"] = True
    n = pp.normalize(p)
    assert route(n, "GET /x")["module"] == "runs"
    assert n["secrets"][0]["allowed_hosts"] == ["localhost", "bank.example.com"]
    cols = [c["name"] for c in tbl(n, "tests")["columns"]]
    assert "created_at" not in cols and {"created_by", "updated_by", "archived_at"} <= set(cols)
    assert "tests" in next(m for m in n["modules"] if m["key"] == "tests_library")["owns"]


# ── validation codes ─────────────────────────────────────────────────────────


def test_plan001_malformed_and_duplicates():
    assert "PLAN001" in codes(pp.validate_plan("nope"))
    p = qa_plan()
    p["data"]["tables"].append({"name": "tests", "owner": "tests_library", "columns": [{"name": "a", "type": "string"}]})
    assert "PLAN001" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    p["routes"] = "x"
    assert "PLAN001" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    tbl(p, "runs")["columns"].append({"name": "job_id", "type": "string"})
    assert "PLAN001" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan002_unknown_ref():
    p = qa_plan()
    tbl(p, "runs")["columns"].append({"name": "env_name", "type": "string", "ref": "environments.name"})
    assert "PLAN002" not in codes(pp.validate_plan(p, FACTS))           # an existing project table counts
    tbl(p, "runs")["columns"].append({"name": "x_id", "type": "integer", "ref": "nowhere.id"})
    tbl(p, "runs")["columns"].append({"name": "y_id", "type": "integer", "ref": "tests.nope"})
    paths = {i["path"] for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN002"}
    assert paths == {"data.tables.runs.columns.x_id", "data.tables.runs.columns.y_id"}


def test_plan003_owner_and_route_module():
    p = qa_plan()
    tbl(p, "tests")["owner"] = None
    assert "PLAN003" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    route(p, "GET /tests")["module"] = "ghost"
    assert "PLAN003" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    route(p, "POST /triage/{id}/retry")["tables"]["writes"].append("tests")   # triage is not a writer of tests
    assert "PLAN003" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan004_state_machine():
    p = qa_plan()
    tbl(p, "runs")["state_machine"]["transitions"].append({"from": "running", "to": "exploded"})
    assert "PLAN004" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    tbl(p, "runs")["state_machine"]["transitions"][0]["route"] = "POST /nowhere"
    assert "PLAN004" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan005_screen_route():
    p = qa_plan()
    p["apps"][0]["screens"][0]["routes"].append("GET /health")      # an existing project route
    assert "PLAN005" not in codes(pp.validate_plan(p, FACTS))
    p["apps"][0]["screens"][0]["routes"].append("DELETE /tests/{id}")
    assert "PLAN005" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan006_integration_secret():
    p = qa_plan()
    p["integrations"][0]["secrets"].append("JIRA_TOKEN")
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN006"]
    assert iss and iss[0]["needs_input"] and iss[0]["severity"] == "error"


def test_plan007_secret_hosts():
    p = qa_plan()
    p["secrets"][0]["allowed_hosts"] = []
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN007"]
    assert iss and iss[0]["severity"] == "error" and iss[0]["needs_input"]
    p = qa_plan()
    p["secrets"][0]["allowed_hosts"] = ["localhost:4590"]
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN007"]
    assert iss and iss[0]["severity"] == "warning" and "localhost" in iss[0]["message"]


def test_plan008_environments():
    p = qa_plan()
    p["environments"][0]["base_url"] = ""
    assert "PLAN008" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    p["environments"][1]["live"] = False
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN008"]
    assert iss and iss[0]["needs_input"]


def test_plan008_a_url_nothing_calls_does_not_block():
    p = qa_plan()
    p["integrations"] = []
    for m in p.get("modules", []):
        m["integrations"] = []
    p["environments"][0]["base_url"] = ""
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN008"]
    assert iss and all(i["severity"] == "warning" and not i["needs_input"] for i in iss)
    # a connected database written up as an integration calls no URL either
    p["integrations"] = [{"key": "shop", "kind": "other", "secrets": ["CONN_SHOP_URL"]}]
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN008"]
    assert iss and all(i["severity"] == "warning" for i in iss)
    p["integrations"] = [{"key": "shop", "kind": "http"}]
    assert "PLAN008" not in codes(pp.validate_plan(p, {**FACTS, "connections": [{"key": "shop", "tables": []}]}), "error")
    assert "PLAN008" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan009_pagination_errors_idempotency():
    p = qa_plan()
    route(p, "GET /runs")["pagination"] = False
    del route(p, "POST /runs")["idempotency"]
    route(p, "POST /runs/{id}/cancel")["errors"] = []
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN009"]
    assert {i["path"] for i in iss} == {"routes.GET /runs", "routes.POST /runs", "routes.POST /runs/{id}/cancel"}
    assert "routes.GET /runs/{id}" not in {i["path"] for i in iss}       # one item is not a list


def test_plan010_enum_and_required_default():
    p = qa_plan()
    tbl(p, "runs")["columns"].append({"name": "kind", "type": "string", "enum": []})
    assert "PLAN010" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    p["data"]["tables"].append({"name": "environments", "owner": "common",
                                "columns": [{"name": "name", "type": "string"},
                                            {"name": "region", "type": "string", "required": True}]})
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN010"]
    assert iss and iss[0]["needs_input"] and iss[0]["path"] == "data.tables.environments.columns.region"
    assert "PLAN010" not in codes(pp.validate_plan(p, {"tables": [], "routes": []}))   # no rows yet: fine


def test_plan010_retention_needs_a_number():
    p = qa_plan()
    tbl(p, "runs")["retention"] = {"policy": "days", "value": None}
    iss = [i for i in pp.validate_plan(p, FACTS) if i["code"] == "PLAN010"]
    assert iss and iss[0]["needs_input"] and iss[0]["path"] == "data.tables.runs.retention"


def test_plan011_build_order():
    p = qa_plan()
    p["build_order"] = ["common", "runs", "tests_library", "triage"]      # runs reads tests (owned by tests_library)
    assert "PLAN011" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    p["build_order"] = ["common", "tests_library", "runs"]                  # triage missing
    assert "PLAN011" in codes(pp.validate_plan(p, FACTS), "error")
    p = qa_plan()
    p["modules"][0]["depends_on"] = ["triage"]                              # a cycle
    assert "PLAN011" in codes(pp.validate_plan(p, FACTS), "error")


def test_plan012_large_module_warns():
    p = qa_plan()
    p["routes"] += [{"key": f"GET /tests/x{i}", "module": "tests_library", "pagination": True} for i in range(12)]
    assert "PLAN012" in codes(pp.validate_plan(p, FACTS), "warning")


def test_plan013_jobs_without_contract_or_smoke():
    p = qa_plan()
    del p["integrations"][0]["smoke"]
    assert "PLAN013" in codes(pp.validate_plan(p, FACTS), "warning")


def test_plan014_helper_outside_shared_module():
    p = qa_plan()
    p["modules"][2]["helpers"] = [{"name": "audit", "signature": "audit(...)"}]
    assert "PLAN014" in codes(pp.validate_plan(p, FACTS), "warning")


def test_has_errors():
    assert not pp.has_errors(pp.validate_plan(qa_plan(), FACTS))
    assert pp.has_errors([{"severity": "error"}])


# ── diffs ────────────────────────────────────────────────────────────────────


def test_diff_additive_is_not_breaking():
    a, b = qa_plan(), qa_plan()
    tbl(b, "runs")["columns"].append({"name": "note", "type": "string"})
    tbl(b, "runs")["columns"][1]["enum"].append("blocked")
    b["routes"].append({"key": "GET /runs/{id}/log", "module": "runs", "purpose": "log"})
    d = pp.diff_plans(a, b)
    assert d["breaking"] is False
    assert "runs" in d["affected"]["modules"] and "runs" in d["affected"]["tables"]
    assert {c["kind"] for c in d["changes"]} >= {"column", "route"}


@pytest.mark.parametrize("mutate", [
    lambda p: p["routes"].pop(),                                            # remove a route
    lambda p: p["data"]["tables"].pop(0),                                    # remove a table
    lambda p: tbl(p, "runs")["columns"].pop(),                               # remove a column
    lambda p: tbl(p, "runs")["columns"][1]["enum"].remove("cancelled"),      # remove an enum value
    lambda p: tbl(p, "tests")["columns"][2].update(type="string"),           # type change
    lambda p: route(p, "GET /runs").update(module="triage"),                 # a route moves module
])
def test_diff_breaking(mutate):
    a, b = qa_plan(), qa_plan()
    mutate(b)
    d = pp.diff_plans(a, b)
    assert d["breaking"] is True and any(c["breaking"] for c in d["changes"])


def test_diff_affected_apps():
    a, b = qa_plan(), qa_plan()
    route(b, "GET /runs")["response"] = {"fields": [{"name": "rows", "type": "json"}]}
    d = pp.diff_plans(a, b)
    assert d["affected"]["apps"] == ["qa_workbench"] and d["affected"]["modules"] == ["runs"]
    assert pp.diff_plans(a, qa_plan())["changes"] == []
    first = pp.diff_plans(None, a)
    assert first["breaking"] is False and set(first["affected"]["modules"]) == {"common", "tests_library", "runs", "triage"}


# ── consumer helpers ─────────────────────────────────────────────────────────


def test_module_spec_is_accepted_by_spec_message():
    spec = pp.module_spec(qa_plan(), "runs")
    assert spec["module"] == "runs"
    ents = {e["name"]: e for e in spec["entities"]}
    assert set(ents) == {"runs", "tests", "audit_log"} and ents["tests"]["read_only"] is True
    assert not ents["runs"].get("read_only") and ents["audit_log"]["writer"] is True     # runs may write the shared audit log
    assert spec["workflows"][0]["entity"] == "runs" and "queued" in spec["workflows"][0]["states"]
    assert {r["key"] for r in spec["routes"]} == {"POST /runs", "GET /runs", "GET /runs/{id}", "POST /runs/{id}/sync",
                                                  "POST /runs/{id}/cancel"}
    assert [h["name"] for h in spec["helpers"]] == ["audit", "page"] and spec["shared_module"] == "common"
    assert spec["external_systems"][0]["name"] == "jobs"
    assert "POST /runs returns a job_id" in spec["acceptance_criteria"]
    assert "The Library lists every test" not in spec["acceptance_criteria"]
    assert any(a["answer"].startswith("Project secrets") for a in spec["answered"])
    msg = mg.spec_message(spec, None)
    assert "Build exactly this" in msg and "POST /runs/{id}/sync" in msg and "BANK_USER" in msg
    assert pp.module_spec(qa_plan(), "ghost") is None


def test_module_spec_for_shared_module():
    spec = pp.module_spec(qa_plan(), "common")
    assert [e["name"] for e in spec["entities"]] == ["audit_log"] and spec["routes"] == []
    assert spec["helpers"][0]["name"] == "audit"


def test_app_brief_shape():
    b = pp.app_brief(qa_plan(), "qa_workbench")
    assert set(b) == {"users", "screens", "journeys", "routes", "states", "acceptance_criteria", "assumptions"}
    assert b["users"] and [s["name"] for s in b["screens"]] == ["Library", "Runs"]
    assert {"method": "POST", "path": "/runs", "purpose": "start a run"} in b["routes"]
    assert len(b["routes"]) == 5 and set(b["states"]) >= {"empty", "loading", "error", "live"}
    assert "The Library lists every test" in b["acceptance_criteria"] and b["journeys"]
    assert pp.app_brief(qa_plan(), "QA Workbench")["screens"]                  # by name too
    assert pp.app_brief(qa_plan(), "nope") is None


def test_answered_topics_route_contract_table_plan():
    assert pp.answered_topics(qa_plan())["credentials"].startswith("Project secrets")
    assert pp.route_contract(qa_plan(), "post /runs/{run_id}/sync")["module"] == "runs"
    assert pp.route_contract(qa_plan(), "DELETE /x") is None
    assert pp.table_plan(qa_plan(), "runs")["owner"] == "runs" and pp.table_plan(qa_plan(), "x") is None


def test_build_order_is_topological():
    p = qa_plan()
    assert pp.build_order(p) == ["common", "tests_library", "runs", "triage", "app:qa_workbench"]
    p["build_order"] = []                       # computed when the plan has none (or an invalid one)
    order = pp.build_order(p)
    assert order.index("common") < order.index("runs") < order.index("triage")
    assert order.index("tests_library") < order.index("runs") and order[-1] == "app:qa_workbench"


def test_module_for():
    p = qa_plan()
    assert pp.module_for(p, "runs") == "runs"
    assert pp.module_for(p, "Build the triage module from the approved plan [plan:triage]") == "triage"
    assert pp.module_for(p, "Tests_Library") == "tests_library"
    assert pp.module_for(p, "something else") is None


def test_with_app_binding():
    p = pp.with_app_bound(qa_plan(), "qa_workbench", "app_123")
    assert p["apps"][0]["app_id"] == "app_123"
    assert pp.diff_plans(qa_plan(), p)["breaking"] is False
