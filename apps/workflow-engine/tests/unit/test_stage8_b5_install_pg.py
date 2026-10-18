"""Stage 8 B5: the installer on a real Postgres, in process (no HTTP): every shipped template installs into a fresh
project and its own tests pass; clashes, re-installs, copies, table reuse, egress and rollback behave as section 8.3 says."""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from src.schemas.template import InstallRequest
from src.services.project_service import ProjectService
from src.services.template_bundle import get_catalog, load_catalog
from src.services.template_installer import TemplateError, TemplateInstaller

pytestmark = pytest.mark.anyio

BROKEN = Path(__file__).resolve().parents[1] / "fixtures" / "templates_broken"
SLUGS = sorted(get_catalog())


async def new_project(sf, allowlist: list[str] | None = None):
    async with sf() as s:
        p = await ProjectService(s).create(f"b5-{uuid.uuid4().hex[:8]}")
        if allowlist is not None:
            p.settings = {**(p.settings or {}), "egress_allowlist": allowlist}
            await s.commit()
        return p


async def install(sf, project, slug, catalog=None, **kw):
    async with sf() as s:
        proj = await ProjectService(s).require(project.id)
        return await TemplateInstaller(s, catalog).install(proj, slug, InstallRequest(**kw), actor="tester")


def failed_tests(body) -> dict:
    return {k: v for k, v in (body.get("tests") or {}).items() if v["failed"] or v["passed"] < 3}


@pytest.mark.parametrize("slug", SLUGS)
async def test_every_template_installs_and_its_own_tests_pass(pg, slug):
    project = await new_project(pg)
    try:
        status, body = await install(pg, project, slug, run_tests=True)
    except TemplateError as e:
        pytest.fail(json.dumps(e.detail(), indent=1, default=str))
    assert status == 201, body
    assert body["status"] == "installed"
    assert not failed_tests(body), json.dumps(body["tests"], indent=1)
    entries = [w for w in body["workflows"] if w["role"] == "entry"]
    subs = [w for w in body["workflows"] if w["role"] == "subworkflow"]
    assert all(w["published_version"] is None for w in entries)
    assert all(w["published_version"] == 1 for w in subs)


async def _service(s):
    return TemplateInstaller(s)._workflow_service()


async def _add_secrets(sf, project, names):
    from src.services.secret_service import SecretService

    async with sf() as s:
        p = await ProjectService(s).require(project.id)
        for n in names:
            await SecretService(s).create(p, {"key": n, "value": "test-" + n.lower(), "type": "string", "environment": p.default_environment})


@pytest.mark.parametrize("slug", SLUGS)
async def test_installed_workflows_check_clean_and_the_entry_publishes_once_its_secrets_exist(pg, slug):
    project = await new_project(pg)
    status, body = await install(pg, project, slug, run_tests=False)
    assert status == 201
    bundle = get_catalog()[slug].bundle
    async with pg() as s:
        svc = await _service(s)
        for w in body["workflows"]:
            findings = (await svc.check(w["id"]))["findings"]
            errors = [f for f in findings if f["severity"] == "error" and f["code"] != "WF006"]
            assert not errors, (w["key"], errors)
            undefined = {f.get("path") for f in findings if f["code"] == "WF006" and f["severity"] == "error"}
            assert undefined <= {x.name for x in bundle.requires.secrets}, (w["key"], undefined)
    await _add_secrets(pg, project, [x.name for x in bundle.requires.secrets])
    async with pg() as s:
        svc = await _service(s)
        for w in body["workflows"]:
            if w["role"] == "entry":
                res = await svc.publish(w["id"])
                assert res.active and res.version_id


# ── section 8.3 behaviours ───────────────────────────────────────────────────────────────────


async def count(sf, sql: str, **params) -> int:
    async with sf() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def counts(sf, project) -> dict[str, int]:
    pid = project.id
    return {
        "workflows": await count(sf, "SELECT count(*) FROM workflows WHERE project_id = :p", p=pid),
        "tables": await count(sf, "SELECT count(*) FROM data_tables WHERE project_id = :p", p=pid),
        "rows": await count(sf, "SELECT count(*) FROM data_table_rows WHERE table_id IN (SELECT id FROM data_tables WHERE project_id = :p)", p=pid),
        "apis": await count(sf, "SELECT count(*) FROM api_specs WHERE project_id = :p", p=pid),
        "installs": await count(sf, "SELECT count(*) FROM template_installs WHERE project_id = :p", p=pid),
        "variables": await count(sf, "SELECT count(*) FROM variables WHERE project_id = :p", p=pid),
    }


def ids_of(body) -> set[str]:
    return {w["id"] for w in body["workflows"]} | {t["id"] for t in body["tables"]} | {a["id"] for a in body["apis"]}


async def test_installing_twice_is_idempotent_and_writes_nothing(pg):
    project = await new_project(pg)
    s1, first = await install(pg, project, "refund-triage")
    before = await counts(pg, project)
    s2, again = await install(pg, project, "refund-triage")
    assert (s1, s2) == (201, 200) and again["status"] == "already_installed"
    assert again["install_id"] == first["install_id"] and ids_of(again) == ids_of(first)
    assert await counts(pg, project) == before
    assert [d["slug"] for d in again["dependencies"]] == ["customer-notify"]


async def test_an_install_records_what_it_made(pg):
    project = await new_project(pg)
    _s, body = await install(pg, project, "refund-triage")
    row = (await _rows(pg, "SELECT template_slug, template_version, install_name, status, installed_by, depends_on, mapping, bundle_hash "
                           "FROM template_installs WHERE project_id = :p AND template_slug = 'refund-triage'", p=project.id))[0]
    slug, version, name, status, by, deps, mapping, digest = row
    assert (slug, version, name, status, by) == ("refund-triage", "1.0.0", "refund-triage", "installed", "tester")
    assert digest == get_catalog()["refund-triage"].hash and len(deps) == 1
    assert set(mapping["workflows"]) == {"main", "eligibility"} and mapping["tables"].keys() == {"refund_limits"}
    assert mapping["slugs"] == {"main": "refund-triage"} and mapping["roles"]["eligibility"] == "subworkflow"
    # the bundle's tests arrive approved and locked, stamped with the template
    [(tests,)] = await _rows(pg, "SELECT draft_definition->'tests' FROM workflows WHERE id = :w", w=mapping["workflows"]["main"])
    assert len(tests) == 4 and all(t["status"] == "approved" and t["locked"] and t["approved_by"] == "template:refund-triage@1.0.0" for t in tests)
    # the reference index was refreshed: the entry draft calls the sub-workflow and customer-notify
    refs = await _rows(pg, "SELECT node_name, child_workflow_id, version_spec FROM workflow_refs WHERE parent_workflow_id = :w "
                           "AND parent_version_id IS NULL ORDER BY node_name", w=mapping["workflows"]["main"])
    notify = next(w["id"] for w in (await _dependency_body(pg, project))["workflows"])
    assert refs == [("Check eligibility", mapping["workflows"]["eligibility"], "latest"), ("Notify declined", notify, "latest"),
                    ("Notify failed", notify, "latest"), ("Notify issued", notify, "latest")]


async def _rows(sf, sql: str, **params):
    async with sf() as s:
        return [tuple(r) for r in (await s.execute(text(sql), params)).all()]


async def _dependency_body(sf, project):
    async with sf() as s:
        p = await ProjectService(s).require(project.id)
        row = (await _rows(sf, "SELECT id FROM template_installs WHERE project_id = :p AND template_slug = 'customer-notify'", p=project.id))[0][0]
        return await TemplateInstaller(s).install_detail(p, row)


async def test_a_second_install_reuses_the_dependency(pg):
    project = await new_project(pg)
    _s, refund = await install(pg, project, "refund-triage")
    _s, kyc = await install(pg, project, "kyc-review")
    dep1, dep2 = refund["dependencies"][0], kyc["dependencies"][0]
    assert dep1["slug"] == dep2["slug"] == "customer-notify" and dep1["install_id"] == dep2["install_id"]
    assert dep1["status"] == "installed" and dep2["status"] == "already_installed"
    assert await count(pg, "SELECT count(*) FROM workflows WHERE project_id = :p AND name = 'Notify customer'", p=project.id) == 1
    # the shared APIs are not registered twice either
    assert await count(pg, "SELECT count(*) FROM api_specs WHERE project_id = :p AND name = 'risk'", p=project.id) <= 1


async def test_as_copy_installs_a_second_set_with_numbered_names(pg):
    project = await new_project(pg)
    _s, first = await install(pg, project, "refund-triage")
    status, copy_ = await install(pg, project, "refund-triage", as_copy=True, run_tests=True)
    assert status == 201 and copy_["install_name"] == "refund-triage-2"
    assert {w["name"] for w in copy_["workflows"]} == {"Refund triage (2)", "Refund eligibility (2)"}
    assert {t["name"] for t in copy_["tables"]} == {"refund_limits_2"} and copy_["slugs"] == {"main": "refund-triage-2"}
    assert ids_of(first).isdisjoint(ids_of(copy_)) or ids_of(first) & ids_of(copy_) <= {a["id"] for a in first["apis"]}
    assert not failed_tests(copy_), copy_["tests"]
    # the copy's entry calls the copy's sub-workflow, not the original
    main2 = next(w["id"] for w in copy_["workflows"] if w["key"] == "main")
    sub2 = next(w["id"] for w in copy_["workflows"] if w["key"] == "eligibility")
    assert (await _rows(pg, "SELECT child_workflow_id FROM workflow_refs WHERE parent_workflow_id = :w AND node_name = 'Check eligibility' "
                             "AND parent_version_id IS NULL", w=main2)) == [(sub2,)]
    assert (await install(pg, project, "refund-triage", as_copy=True))[1]["install_name"] == "refund-triage-3"


async def test_an_existing_name_is_renamed_or_refused(pg):
    from src.repositories.workflow_repository import WorkflowRepository

    project = await new_project(pg)
    async with pg() as s:
        from tests.unit._pg_world import build, nd
        await WorkflowRepository(s).create(build([nd("Go", "Start")], [], name="Refund triage"), project_id=project.id)
    status, body = await install(pg, project, "refund-triage")
    assert status == 201
    assert {"kind": "workflow", "from": "Refund triage", "to": "Refund triage (2)"} in body["renamed"]
    assert next(w for w in body["workflows"] if w["key"] == "main")["name"] == "Refund triage (2)"

    other = await new_project(pg)
    async with pg() as s:
        await WorkflowRepository(s).create(build([nd("Go", "Start")], [], name="Refund eligibility"), project_id=other.id)
    before = await counts(pg, other)
    with pytest.raises(TemplateError) as e:
        await install(pg, other, "refund-triage", clash="fail")
    assert e.value.status == 409 and e.value.code == "TEMPLATE_NAME_CLASH"
    assert e.value.extra["clashes"] == [{"kind": "workflow", "name": "Refund eligibility"}]
    assert await counts(pg, other) == before, "a refused install writes nothing"


async def test_a_table_is_reused_only_when_asked_and_compatible(pg):
    from src.services.data_table_service import DataTableService

    project = await new_project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(project.id)
        svc = DataTableService(s)
        t = await svc.create_table(p, {"name": "refund_limits", "columns": [
            {"name": "segment", "type": "string", "required": True}, {"name": "auto_limit", "type": "number", "required": True}]})
        await svc.insert_rows(await svc.get_table(p, t["id"]), [{"segment": "retail", "auto_limit": 250}, {"segment": "premier", "auto_limit": 999}])
    _s, body = await install(pg, project, "refund-triage", reuse_tables=["refund_limits"], run_tests=False)
    assert body["tables"] == [{"key": "refund_limits", "id": t["id"], "name": "refund_limits", "reused": True, "rows": 2, "seeded_rows": 0}]
    assert await count(pg, "SELECT count(*) FROM data_tables WHERE project_id = :p AND name LIKE 'refund_limits%'", p=project.id) == 1
    [(defn,)] = await _rows(pg, "SELECT draft_definition FROM workflows WHERE id = :w", w=next(w["id"] for w in body["workflows"] if w["key"] == "eligibility"))
    assert next(n for n in defn["nodes"] if n["name"] == "Look up limit")["parameters"]["table"] == "refund_limits"

    fresh = await new_project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(fresh.id)
        await DataTableService(s).create_table(p, {"name": "refund_limits", "columns": [{"name": "segment", "type": "string"}]})
    _s, other = await install(pg, fresh, "refund-triage", reuse_tables=["refund_limits"], run_tests=True)
    assert other["tables"][0]["name"] == "refund_limits_2" and other["tables"][0]["seeded_rows"] == 2 and not other["tables"][0]["reused"]
    assert not failed_tests(other)
    # the second table got its own name, and the workflow addresses that one
    [(defn2,)] = await _rows(pg, "SELECT draft_definition FROM workflows WHERE id = :w", w=next(w["id"] for w in other["workflows"] if w["key"] == "eligibility"))
    assert next(n for n in defn2["nodes"] if n["name"] == "Look up limit")["parameters"]["table"] == "refund_limits_2"


async def test_egress_hosts_are_added_only_to_a_project_that_restricts_egress(pg):
    open_project = await new_project(pg)                       # empty allowlist = unrestricted: must stay empty
    _s, body = await install(pg, open_project, "refund-triage")
    assert body["egress_added"] == []
    assert (await _rows(pg, "SELECT settings->'egress_allowlist' FROM projects WHERE id = :p", p=open_project.id)) == [([],)]

    closed = await new_project(pg, allowlist=["other.bank.example"])
    status, body = await install(pg, closed, "refund-triage", run_tests=True)
    assert status == 201 and not failed_tests(body)
    assert body["egress_added"] == ["refunds.bank.example"]                         # this template's own hosts...
    assert body["dependencies"][0]["slug"] == "customer-notify"                       # ...its dependency added notify's
    [(allow,)] = await _rows(pg, "SELECT settings->'egress_allowlist' FROM projects WHERE id = :p", p=closed.id)
    assert allow == ["other.bank.example", "notify.bank.example", "refunds.bank.example"]


async def test_the_needs_report_lists_what_each_environment_lacks(pg):
    project = await new_project(pg)
    _s, body = await install(pg, project, "aml-alert-triage", run_tests=False)
    by_name = {n["name"]: n for n in body["needs"]}
    assert set(by_name) == {"AML_API_KEY", "QUEUES_API_KEY", "AML_HIGH_SCORE"}
    assert by_name["AML_API_KEY"] == {"kind": "secret", "name": "AML_API_KEY", "environments_missing": ["dev", "prod"],
                                      "used_by": ["main/List open alerts", "main/Close low risk"]}
    # a required variable with a default is created in the default environment only; the other still lacks it
    assert by_name["AML_HIGH_SCORE"]["environments_missing"] == ["prod"] and by_name["AML_HIGH_SCORE"]["kind"] == "variable"
    assert await count(pg, "SELECT count(*) FROM variables WHERE project_id = :p AND key = 'AML_HIGH_SCORE' AND environment = 'dev'", p=project.id) == 1
    await _add_secrets(pg, project, ["AML_API_KEY"])
    async with pg() as s:
        p = await ProjectService(s).require(project.id)
        detail = await TemplateInstaller(s).install_detail(p, body["install_id"])
    assert {n["name"]: n["environments_missing"] for n in detail["needs"]}["AML_API_KEY"] == ["prod"]
    assert detail["mapping"]["variables_added"] == [{"key": "AML_HIGH_SCORE", "environment": "dev"}]


async def test_an_existing_variable_is_never_overwritten(pg):
    from src.repositories.variable_repository import VariableRepository

    project = await new_project(pg)
    async with pg() as s:
        await VariableRepository(s).create(key="AML_HIGH_SCORE", value="90", team_id="default", environment="dev", type="number", project_id=project.id)
    _s, body = await install(pg, project, "aml-alert-triage", run_tests=False)
    assert body is not None
    assert (await _rows(pg, "SELECT value FROM variables WHERE project_id = :p AND key = 'AML_HIGH_SCORE'", p=project.id)) == [("90",)]


async def test_versions_and_unknown_templates(pg, tmp_path):
    project = await new_project(pg)
    with pytest.raises(TemplateError) as e:
        await install(pg, project, "no-such-template")
    assert (e.value.status, e.value.code) == (404, "TEMPLATE_NOT_FOUND")
    with pytest.raises(TemplateError) as e:
        await install(pg, project, "customer-notify", version="9.9.9")
    assert (e.value.status, e.value.code) == (404, "TEMPLATE_VERSION_NOT_FOUND")
    await install(pg, project, "customer-notify", version="1.0.0")
    newer = tmp_path / "customer-notify"
    shutil.copytree(Path(__file__).resolve().parents[2] / "templates" / "customer-notify", newer)
    b = json.loads((newer / "template.json").read_text())
    b["version"] = "1.1.0"
    (newer / "template.json").write_text(json.dumps(b))
    before = await counts(pg, project)
    with pytest.raises(TemplateError) as e:
        await install(pg, project, "customer-notify", catalog=load_catalog(tmp_path, strict=True))
    assert (e.value.status, e.value.code) == (409, "TEMPLATE_VERSION_INSTALLED")
    assert e.value.extra["installed_version"] == "1.0.0" and e.value.extra["catalog_version"] == "1.1.0"
    assert await counts(pg, project) == before


async def test_a_failing_install_leaves_nothing_behind(pg, tmp_path):
    shutil.copytree(BROKEN / "rollback-on-publish", tmp_path / "rollback-on-publish")
    catalog = load_catalog(tmp_path, strict=True)
    project = await new_project(pg, allowlist=["keep.bank.example"])
    before = await counts(pg, project)
    with pytest.raises(TemplateError) as e:
        await install(pg, project, "rollback-on-publish", catalog=catalog)
    err = e.value
    assert (err.status, err.code, err.extra["stage"], err.extra["workflow_key"]) == (422, "TEMPLATE_INSTALL_FAILED", "publish", "helper")
    assert err.extra["test_failures"][0]["name"] == "Doubles"
    assert await counts(pg, project) == before, "workflows, table rows, the API, the variable and the install row are all gone"
    assert (await _rows(pg, "SELECT settings->'egress_allowlist' FROM projects WHERE id = :p", p=project.id)) == [(["keep.bank.example"],)]
    assert await count(pg, "SELECT count(*) FROM workflow_refs WHERE project_id = :p", p=project.id) == 0
    # and the project is still usable: a good template installs fine afterwards
    status, _ = await install(pg, project, "customer-notify")
    assert status == 201


async def test_preview_decides_everything_and_writes_nothing(pg):
    project = await new_project(pg)
    async with pg() as s:
        p = await ProjectService(s).require(project.id)
        inst = TemplateInstaller(s)
        before = await counts(pg, project)
        out = await inst.preview(p, "refund-triage", InstallRequest())
        assert await counts(pg, project) == before
    assert out["status"] == "new" and out["template"] == {"slug": "refund-triage", "version": "1.0.0"}
    assert [w["name"] for w in out["plan"]["workflows"]] == ["Refund eligibility", "Refund triage"]
    assert out["dependencies"] == [{"slug": "customer-notify", "version": "1.0.0", "status": "will_install"}]
    assert [n["name"] for n in out["needs"]] == ["REFUNDS_API_KEY"] and out["egress_hosts"] == ["refunds.bank.example"]
    await install(pg, project, "refund-triage")
    async with pg() as s:
        p = await ProjectService(s).require(project.id)
        again = await TemplateInstaller(s).preview(p, "refund-triage", InstallRequest())
        copy_ = await TemplateInstaller(s).preview(p, "refund-triage", InstallRequest(as_copy=True))
    assert again["status"] == "already_installed" and again["installed"]["version"] == "1.0.0"
    assert again["plan"]["renamed"], "the preview shows the renames an install would make"
    assert copy_["status"] == "new"


async def test_each_copy_of_a_webhook_template_gets_its_own_path(pg):
    project = await new_project(pg)
    _s, first = await install(pg, project, "card-dispute-intake", run_tests=False)
    _s, second = await install(pg, project, "card-dispute-intake", as_copy=True, run_tests=False)
    paths = []
    for body in (first, second):
        wid = next(w["id"] for w in body["workflows"] if w["key"] == "main")
        [(defn,)] = await _rows(pg, "SELECT draft_definition FROM workflows WHERE id = :w", w=wid)
        paths.append(next(n for n in defn["nodes"] if n["type"] == "Webhook")["parameters"]["path"])
    # webhook paths are unique across the whole database (another test may have published the plain one): never equal
    assert paths[0].startswith("card-dispute-intake") and paths[1].startswith("card-dispute-intake") and paths[0] != paths[1]
    assert int(paths[1].rsplit("-", 1)[1]) > (int(paths[0].rsplit("-", 1)[1]) if paths[0] != "card-dispute-intake" else 1)
    assert await count(pg, "SELECT count(*) FROM active_triggers WHERE workflow_id IN (SELECT id FROM workflows WHERE project_id = :p)", p=project.id) == 0, \
        "an install never switches a trigger on"


async def test_export_makes_a_bundle_that_installs_somewhere_else(pg, tmp_path):
    from src.services.template_installer import export_template

    project = await new_project(pg)
    _s, body = await install(pg, project, "refund-triage")
    main = next(w["id"] for w in body["workflows"] if w["key"] == "main")
    async with pg() as s:
        bundle, warnings = await export_template(s, main, slug="refund-copy", version="2.0.0")
    assert warnings == [], warnings
    assert bundle["slug"] == "refund-copy" and bundle["version"] == "2.0.0" and bundle["format"] == "wf-template/1"
    roles = {w["key"]: w["role"] for w in bundle["workflows"]}
    assert roles == {"main": "entry", "refund_eligibility": "subworkflow", "notify_customer": "subworkflow"}
    assert {t["name"] for t in bundle["tables"]} == {"refund_limits", "notify_templates"} and all(t["seed_rows"] == [] for t in bundle["tables"])
    assert {a["name"] for a in bundle["apis"]} == {"refunds", "notify"} and all("spec" in a and "file" not in a for a in bundle["apis"])
    assert bundle["requires"]["egress_hosts"] == ["notify.bank.example", "refunds.bank.example"]
    assert [v["name"] for v in bundle["requires"]["variables"] + bundle["requires"]["secrets"]] == ["REFUNDS_API_KEY"]
    dumped = json.dumps(bundle)
    for real_id in [w["id"] for w in body["workflows"]] + [a["id"] for a in body["apis"]]:
        assert real_id not in dumped, "ids are replaced by tokens"
    assert "approved_by" not in dumped
    # it installs into another project and its tests pass there
    d = tmp_path / "refund-copy"
    d.mkdir()
    (d / "template.json").write_text(json.dumps(bundle))
    catalog = load_catalog(tmp_path, strict=True)
    other = await new_project(pg)
    status, again = await install(pg, other, "refund-copy", catalog=catalog, run_tests=True)
    assert status == 201 and not failed_tests(again), again.get("tests")
    assert [w["role"] for w in again["workflows"] if w["published_version"]] == ["subworkflow", "subworkflow"]


async def test_export_of_an_unknown_workflow_is_a_404(pg):
    from src.services.template_installer import export_template

    async with pg() as s:
        with pytest.raises(TemplateError) as e:
            await export_template(s, "wf_nope")
    assert e.value.status == 404


# ── what the mocks hide: sub-workflow input mappings, evaluated against what the tests produced ─────────────────


def _type_ok(declared: str, value) -> bool:
    return {"string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool), "object": isinstance(value, dict), "array": isinstance(value, list)}[declared]


@pytest.mark.parametrize("slug", ["refund-triage", "kyc-review", "card-dispute-intake", "aml-alert-triage"])
async def test_every_subworkflow_call_maps_inputs_that_resolve_and_fit_the_childs_declared_fields(pg, slug):
    """A mocked ExecuteWorkflow never evaluates its input mapping. Evaluate it against the items the tests did produce."""
    from src.engine.expression_engine import ExpressionEngine, expression_engine
    from src.engine.types import NodeData
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.test_runner import run_tests
    from src.services.template_bundle import token_uses

    catalog = get_catalog()
    bundle = catalog[slug].bundle
    project = await new_project(pg)
    _s, body = await install(pg, project, slug, run_tests=False)
    checked = 0
    for w in bundle.workflows:
        calls = [n for n in w.definition["nodes"] if n["type"] == "ExecuteWorkflow"]
        if not calls:
            continue
        wid = next(x["id"] for x in body["workflows"] if x["key"] == w.key)
        async with pg() as s:
            report = await run_tests(WorkflowRepository(s), await WorkflowRepository(s).get(wid), persist=False)
        for call in calls:
            target = next(u.key for u in token_uses({"nodes": [call], "settings": {}}) if u.kind == "workflow")
            if ":" in target:
                dep_slug, _, dep_key = target.partition(":")
                child = next(x for x in catalog[dep_slug].bundle.workflows if x.key == dep_key).definition
            else:
                child = next(x for x in bundle.workflows if x.key == target).definition
            declared = next(n for n in child["nodes"] if n["type"] == "ExecuteWorkflowTrigger")["parameters"]["fields"]
            upstream = [c for c in w.definition["connections"] if c["target_node"] == call["name"]]
            for result in report["results"]:
                outputs = result["outputs"]
                items = [it for c in upstream for it in (outputs.get(c["source_node"], {}).get(c["source_output"]) or [])]
                if not items or call["name"] not in result["node_status"] or result["node_status"][call["name"]] == "not_run":
                    continue
                node_states = {n: [NodeData(json=j) for j in ports.get("main", next(iter(ports.values()), []))] for n, ports in outputs.items() if ports}
                for idx in range(len(items) if call["parameters"].get("mode") == "each" else 1):
                    ctx = ExpressionEngine.create_context([NodeData(json=j) for j in items], node_states, "exec_x", idx)
                    mapped = expression_engine.resolve(copy.deepcopy(call["parameters"]["inputs"]), ctx)
                    text_ = json.dumps(mapped)
                    assert "Expression Error" not in text_, (result["name"], call["name"], mapped)
                    for f in declared:
                        if f["required"]:
                            assert mapped.get(f["name"]) not in (None, ""), (result["name"], call["name"], f["name"], mapped)
                        if mapped.get(f["name"]) is not None:
                            assert _type_ok(f["type"], mapped[f["name"]]), (call["name"], f["name"], mapped[f["name"]])
                    checked += 1
    assert checked >= 2, f"{slug}: no sub-workflow call was reached by any test"
