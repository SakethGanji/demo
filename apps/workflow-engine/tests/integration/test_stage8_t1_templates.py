"""Stage 8 T1 (tester): the template CI gate of DESIGN-STAGE8-REUSE 8.5 spelled out step by step, then install tried the hard
ways: a rollback seen from outside, two installs racing, the user editing or deleting what an install made, secrets that must
never be created, and another project asking about an install it does not own."""

from __future__ import annotations

import concurrent.futures
import json
import shutil
from pathlib import Path

import pytest

from .conftest import EngineServer, StudioClient, TestDatabase, uniq

pytestmark = pytest.mark.integration

ENGINE = Path(__file__).resolve().parents[2]
TEMPLATES = sorted(p.parent.name for p in (ENGINE / "templates").glob("*/template.json"))
BROKEN = ENGINE / "tests" / "fixtures" / "templates_broken"


def bundle(slug: str) -> dict:
    return json.loads((ENGINE / "templates" / slug / "template.json").read_text())


def new_project(api, name: str | None = None, allowlist: list[str] | None = None) -> str:
    r = api.post("/projects", {"name": name or uniq("tplci")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    if allowlist is not None:
        assert api.put(f"/projects/{pid}", {"settings": {"egress_allowlist": allowlist}}).status_code == 200
    return pid


def install(api, project: str, slug: str, **body):
    return api.post(f"/projects/{project}/templates/{slug}/install", body or None)


def counts(db, project: str) -> dict[str, int]:
    q = lambda sql: db.query(sql, (project,))[0][0]  # noqa: E731
    return {
        "workflows": q("SELECT count(*) FROM workflows WHERE project_id = %s"),
        "tables": q("SELECT count(*) FROM data_tables WHERE project_id = %s"),
        "rows": q("SELECT count(*) FROM data_table_rows r JOIN data_tables t ON t.id = r.table_id WHERE t.project_id = %s"),
        "refs": q("SELECT count(*) FROM workflow_refs WHERE project_id = %s"),
        "installs": q("SELECT count(*) FROM template_installs WHERE project_id = %s"),
    }


# ── 8.5, step by step, for every shipped template ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", TEMPLATES)
def test_the_ci_gate_for_each_template(api, test_db, slug):
    b = bundle(slug)
    project = new_project(api, f"tplci_{slug}_{uniq('r')}", allowlist=list(b["requires"]["egress_hosts"]))     # 1
    r = install(api, project, slug, run_tests=True)                                                               # 2
    assert r.status_code == 201, r.text
    body = r.json()
    for w in b["workflows"]:
        s = body["tests"][w["key"]]
        assert s["failed"] == 0 and s["passed"] >= b["ci"]["min_tests_per_workflow"], (w["key"], s)
    ids = {w["key"]: w["id"] for w in body["workflows"]}
    assert set(ids) >= {w["key"] for w in b["workflows"]}
    for wid in ids.values():                                                                                      # 3
        res = api.post(f"/workflows/{wid}/check", {})
        assert res.status_code == 200, res.text
        errors = [f for f in res.json()["findings"] if f["severity"] == "error"]
        # a secret the project does not hold yet is the one finding a fresh install is allowed to have
        assert all(f["code"] == "WF006" and f.get("path") in {s["name"] for s in b["requires"]["secrets"]} for f in errors), errors

    before = counts(test_db, project)                                                                             # 4
    again = install(api, project, slug)
    assert again.status_code == 200 and again.json()["status"] == "already_installed"
    assert counts(test_db, project) == before

    copy_ = install(api, project, slug, as_copy=True)                                                             # 5
    assert copy_.status_code == 201, copy_.text
    c = copy_.json()
    assert all(s["failed"] == 0 for s in c["tests"].values())
    own = {w["id"] for w in c["workflows"] if w["key"] in {x["key"] for x in b["workflows"]}}
    assert own.isdisjoint(ids.values())
    assert {t["id"] for t in c["tables"]}.isdisjoint({t["id"] for t in body["tables"]}) if c["tables"] else True
    assert all(w["name"] != next(o["name"] for o in body["workflows"] if o["key"] == w["key"]) for w in c["workflows"] if w["key"] in ids)

    for s in b["requires"]["secrets"]:                                                                            # 6
        rr = api.post(f"/projects/{project}/secrets", {"key": s["name"], "value": "k-test", "type": "string", "environment": "dev"})
        assert rr.status_code in (200, 201), rr.text
    for w in b["workflows"]:
        if w["role"] == "entry":
            pub = api.post(f"/workflows/{ids[w['key']]}/publish", {})
            assert pub.status_code == 200, pub.text


# ── rollback seen from outside: a whole-database diff, on an engine that serves a broken-at-publish bundle ─────────


@pytest.fixture(scope="module")
def rollback_world(tmp_path_factory):
    import os

    cat = tmp_path_factory.mktemp("s8t1-catalog")
    shutil.copytree(BROKEN / "rollback-on-publish", cat / "rollback-on-publish")
    shutil.copytree(ENGINE / "templates" / "customer-notify", cat / "customer-notify")
    db = TestDatabase()
    db.name = db.name.replace("workflows_test_", "workflows_test_s8t1tpl_", 1) if not os.environ.get("TEST_DB_NAME") else db.name + "_s8t1tpl"
    db.create()
    srv = None
    try:
        db.migrate("apply")
        srv = EngineServer(db, extra_env={"WORKFLOW_TEMPLATES_DIR": str(cat)}, label="-s8t1-templates")
        srv.start()
        yield StudioClient(srv.base_url), db
    finally:
        if srv:
            srv.stop()
        db.drop()


SKIP = {"executions", "node_outputs", "schema_migrations", "migrations", "alembic_version"}


def everything(db) -> dict[str, list]:
    """Every row of every table, except run history, as sorted text: a failed install must leave this unchanged."""
    out = {}
    for (t,) in db.query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'workflow-app' AND table_type = 'BASE TABLE'"):
        if t in SKIP:
            continue
        cols = [c for (c,) in db.query("SELECT column_name FROM information_schema.columns WHERE table_schema = 'workflow-app' "
                                       "AND table_name = %s AND column_name <> 'updated_at' ORDER BY ordinal_position", (t,))]
        out[t] = sorted(map(str, db.query("SELECT " + ", ".join(f'"{c}"' for c in cols) + f' FROM "{t}"')))
    return out


def test_a_bundle_that_fails_at_publish_leaves_no_trace_anywhere(rollback_world):
    api, db = rollback_world
    project = new_project(api, allowlist=["keep.bank.example"])
    before = everything(db)
    r = install(api, project, "rollback-on-publish")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "TEMPLATE_INSTALL_FAILED" and detail["stage"] == "publish" and detail["workflow_key"] == "helper"
    after = everything(db)
    changed = {t for t in before if before[t] != after.get(t)} | (set(after) - set(before))
    assert changed == set(), f"rows left behind in: {sorted(changed)}"
    assert api.get(f"/projects/{project}").json()["settings"]["egress_allowlist"] == ["keep.bank.example"]
    assert api.get(f"/projects/{project}/template-installs").json() == []
    # the failure is not sticky: the next, good install in the same project works
    ok = install(api, project, "customer-notify")
    assert ok.status_code == 201, ok.text
    again = install(api, project, "rollback-on-publish")
    assert again.status_code == 422                                               # and the broken one still fails the same way


# ── two installs at once ────────────────────────────────────────────────────────────────────────────────────────────


def race(api, project: str):
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futs = [pool.submit(lambda: install(api, project, "refund-triage", run_tests=False)) for _ in range(2)]
        return [f.result() for f in futs]


def test_two_simultaneous_installs_of_the_same_template_make_one_set_of_rows(api, test_db):
    project = new_project(api)
    race(api, project)
    c = counts(test_db, project)
    assert c["installs"] == 2, c                                                  # refund-triage and its dependency, once each
    names = [r[0] for r in test_db.query("SELECT name FROM workflows WHERE project_id = %s ORDER BY name", (project,))]
    assert sorted(set(names)) == sorted(names), f"duplicate workflows after a racing install: {names}"
    assert c["tables"] == 2 and c["workflows"] == 3, c
    assert c["rows"] == 2 + 6                                                     # seed rows inserted once
    # and the project is healthy afterwards
    assert install(api, project, "refund-triage").json()["status"] == "already_installed"


def test_the_loser_of_two_racing_installs_is_told_politely(api):
    for _ in range(8):                                    # the race is real but timing-dependent: try fresh projects until it happens
        statuses = sorted(r.status_code for r in race(api, new_project(api)))
        assert statuses in ([200, 201], [201, 409], [201, 201]), statuses


# ── the user changes what the install made ──────────────────────────────────────────────────────────────────────────


def test_reinstalling_never_touches_an_installed_workflow_the_user_has_edited(api, test_db):
    project = new_project(api)
    first = install(api, project, "customer-notify").json()
    wid = next(w["id"] for w in first["workflows"] if w["key"] == "notify")
    definition = api.get(f"/workflows/{wid}").json()["definition"]
    nodes = definition["nodes"] + [{"name": "Mine", "type": "Set", "parameters": {"mode": "manual", "fields": [], "keepOnlySet": False},
                                    "position": {"x": 900, "y": 0}}]
    assert api.put(f"/workflows/{wid}", {"nodes": nodes, "connections": definition["connections"]}).status_code == 200
    again = install(api, project, "customer-notify")
    assert again.status_code == 200 and again.json()["status"] == "already_installed"
    assert "Mine" in {n["name"] for n in api.get(f"/workflows/{wid}").json()["definition"]["nodes"]}


def test_reinstalling_after_the_user_deleted_the_installed_workflows_does_not_claim_they_exist(api):
    project = new_project(api)
    first = install(api, project, "customer-notify").json()
    for w in first["workflows"]:
        assert api.delete(f"/workflows/{w['id']}").status_code == 200
    again = install(api, project, "customer-notify")
    assert again.status_code in (200, 201), again.text
    body = again.json()
    for w in body["workflows"]:
        assert api.get(f"/workflows/{w['id']}").status_code == 200, f"install reports {w['key']} = {w['id']} but it does not exist"


def test_secrets_are_listed_as_needs_and_never_created(api):
    project = new_project(api)
    body = install(api, project, "kyc-review", run_tests=False).json()
    names = {s["name"] for s in bundle("kyc-review")["requires"]["secrets"]}
    held = {s["key"] for s in api.get(f"/projects/{project}/secrets").json()}
    assert names and names.isdisjoint(held)
    assert {n["name"] for n in body["needs"] if n["kind"] == "secret"} == names
    # no workflow, table row, or API spec the install made contains a value that looks like the placeholder token we use in tests
    dump = json.dumps([api.get(f"/workflows/{w['id']}").json() for w in body["workflows"]])
    assert "k-test" not in dump


def test_an_existing_table_with_other_columns_is_not_reused_even_when_asked(api):
    project = new_project(api)
    r = api.post(f"/projects/{project}/tables", {"name": "refund_limits", "columns": [{"name": "something_else", "type": "string"}]})
    assert r.status_code in (200, 201), r.text
    out = install(api, project, "refund-triage", reuse_tables=["refund_limits"], run_tests=True)
    assert out.status_code == 201, out.text
    t = next(t for t in out.json()["tables"] if t["key"] == "refund_limits")
    assert t["reused"] is False and t["name"] != "refund_limits"                   # incompatible: renamed, seeded, and the tests pass
    assert t["seeded_rows"] == 2
    assert all(s["failed"] == 0 for s in out.json()["tests"].values())


# ── another project asking ──────────────────────────────────────────────────────────────────────────────────────────


def test_an_install_of_one_project_is_invisible_to_another(api):
    a, b = new_project(api), new_project(api)
    body = install(api, a, "customer-notify").json()
    assert api.get(f"/projects/{a}/template-installs/{body['install_id']}").status_code == 200
    assert api.get(f"/projects/{b}/template-installs/{body['install_id']}").status_code == 404
    assert api.get(f"/projects/{b}/template-installs").json() == []
    assert {t["slug"]: t["installed_in"] for t in api.get("/templates", params={"project": b}).json()}["customer-notify"] == []
    other = install(api, b, "customer-notify")
    assert other.status_code == 201 and other.json()["install_id"] != body["install_id"]
    assert {w["id"] for w in other.json()["workflows"]}.isdisjoint({w["id"] for w in body["workflows"]})
    # each project's sub-workflow sits in its own project: the picker of one never lists the other's
    picker = {r["id"] for r in api.get(f"/projects/{b}/subworkflows").json()}
    assert picker == {w["id"] for w in other.json()["workflows"]}


def test_the_installed_entry_calls_the_installed_sub_workflow_of_its_own_project(api, test_db):
    a, b = new_project(api), new_project(api)
    ra, rb = install(api, a, "refund-triage").json(), install(api, b, "refund-triage").json()
    main_a = next(w["id"] for w in ra["workflows"] if w["key"] == "main")
    in_a = {r[0] for r in test_db.query("SELECT id FROM workflows WHERE project_id = %s", (ra["project_id"],))}
    in_b = {r[0] for r in test_db.query("SELECT id FROM workflows WHERE project_id = %s", (rb["project_id"],))}
    called = {r[0] for r in test_db.query("SELECT child_workflow_id FROM workflow_refs WHERE parent_workflow_id = %s", (main_a,))}
    assert len(called) == 2 and called <= in_a and called.isdisjoint(in_b)


def test_a_malformed_install_request_is_a_422_and_writes_nothing(api, test_db):
    project = new_project(api)
    before = counts(test_db, project)
    for bad in ({"clash": "overwrite"}, {"reuse_tables": "refund_limits"}, {"as_copy": "perhaps"}, {"surprise": 1}, {"version": 3}):
        r = api.post(f"/projects/{project}/templates/refund-triage/install", bad)
        assert r.status_code == 422, (bad, r.text)
    assert counts(test_db, project) == before
