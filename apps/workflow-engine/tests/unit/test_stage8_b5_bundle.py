"""Stage 8 B5: template bundles without a database. Every error code of the validator is produced by a fixture or an
in-memory mutation; the shipped catalog validates; tokens, the secret lints, planning and the no-op authz seam are exact."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from src.schemas.template import InstallRequest, TemplateBundle
from src.services import authz
from src.services.template_bundle import (
    FAILURE_CATEGORIES,
    BundleIssue,
    TemplateBundleError,
    bundle_hash,
    iban_ok,
    load_catalog,
    load_template_dir,
    luhn_ok,
    parse_bundle,
    parse_semver,
    real_emails,
    replace_tokens,
    satisfies,
    secret_findings,
    stray_tokens,
    token_uses,
    validate_bundle,
)
from src.services.template_installer import (
    ProjectSnapshot,
    api_content_hash,
    columns_compatible,
    plan_install,
    workflow_order,
)

ENGINE = Path(__file__).resolve().parents[2]
SHIPPED = ENGINE / "templates"
BROKEN = ENGINE / "tests" / "fixtures" / "templates_broken"
EXPECTED = json.loads((BROKEN / "_expected.json").read_text())


# ── the shipped catalog ──────────────────────────────────────────────────────────────────────


def test_the_six_starter_templates_validate_and_are_what_the_design_lists():
    catalog = load_catalog(SHIPPED, strict=True)
    assert sorted(catalog) == ["aml-alert-triage", "card-dispute-intake", "customer-notify", "kyc-review", "loan-precheck",
                               "refund-triage"]
    for slug, lt in catalog.items():
        b = lt.bundle
        assert b.slug == slug and b.format == "wf-template/1" and parse_semver(b.version)
        assert lt.readme.strip(), f"{slug} ships a README"
        assert lt.hash == bundle_hash(lt.raw, lt.api_texts)
        for w in b.workflows:
            assert len(w.definition["tests"]) >= b.ci.min_tests_per_workflow
            assert all(t["status"] == "approved" for t in w.definition["tests"])
            assert any(t["category"] in FAILURE_CATEGORIES for t in w.definition["tests"])
            if w.role == "subworkflow":
                assert w.publish is True and not any(n["type"] == "Approval" for n in w.definition["nodes"])
            else:
                assert w.publish is False
    entries_with_approval = {s for s, lt in catalog.items() for w in lt.bundle.workflows
                             if w.role == "entry" and any(n["type"] == "Approval" for n in w.definition["nodes"])}
    assert entries_with_approval == {"refund-triage", "kyc-review"}
    subs = {s: sorted(w.key for w in lt.bundle.workflows if w.role == "subworkflow") for s, lt in catalog.items()}
    assert subs == {"aml-alert-triage": ["score"], "card-dispute-intake": [], "customer-notify": ["notify"], "kyc-review": ["screen"],
                    "loan-precheck": [], "refund-triage": ["eligibility"]}
    assert [r.slug for r in catalog["refund-triage"].bundle.requires.templates] == ["customer-notify"]
    assert [s.name for s in catalog["refund-triage"].bundle.requires.secrets] == ["REFUNDS_API_KEY"]


def test_workflow_order_puts_callees_first():
    lt = load_catalog(SHIPPED, strict=True)["refund-triage"]
    assert workflow_order(lt.bundle) == ["eligibility", "main"]


def test_tokens_are_replaced_at_replaceable_places_only():
    lt = load_catalog(SHIPPED, strict=True)["refund-triage"]
    main = next(w for w in lt.bundle.workflows if w.key == "main").definition
    uses = token_uses(main)
    assert {(u.kind, u.key) for u in uses} == {("workflow", "eligibility"), ("workflow", "customer-notify:notify"), ("api", "refunds")}
    done = replace_tokens(main, lambda kind, key: f"<{kind}:{key}>")
    assert stray_tokens(done) == [] and "@tpl:" not in json.dumps(done)
    assert "@tpl:" in json.dumps(main), "the original is not modified"
    with pytest.raises(TemplateBundleError) as e:
        d = copy.deepcopy(main)
        d["nodes"][0]["parameters"]["note"] = "@tpl:workflow/eligibility"
        replace_tokens(d, lambda k, v: "x")
    assert e.value.issues[0].code == "TPL_TOKEN_MISUSE"


# ── every error ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,code", sorted(EXPECTED.items()))
def test_each_broken_fixture_reports_its_own_code(name, code):
    if code == "OK":
        assert load_template_dir(BROKEN / name).bundle.slug == name
        return
    with pytest.raises(TemplateBundleError) as e:
        load_template_dir(BROKEN / name)
    codes = {i.code for i in e.value.issues}
    assert code in codes, f"{name}: wanted {code}, got {sorted(codes)}"
    for i in e.value.issues:
        assert isinstance(i, BundleIssue) and i.message and isinstance(i.path, str)


def test_every_error_code_of_the_validator_is_covered_by_a_fixture_or_a_test_below():
    covered = set(EXPECTED.values()) - {"OK"} | {"TPL_FEW_SHOT", "TPL_REQUIRES", "TPL_REQUIRES_CYCLE"}
    import re
    doc = Path(ENGINE / "src/services/template_bundle.py").read_text().split('"""', 2)[1]
    declared = set(re.findall(r"^    (TPL_[A-Z_]+)\s", doc, flags=re.M))
    assert declared <= covered, f"no test for {sorted(declared - covered)}"


def test_the_base_fixture_is_valid_so_each_error_comes_from_its_one_mutation():
    base = json.loads((BROKEN / "rollback-on-publish" / "template.json").read_text())
    bundle, issues = validate_bundle(base, {"svc": (BROKEN / "rollback-on-publish/apis/svc.yaml").read_text()}, expected_slug="rollback-on-publish")
    assert issues == [] and isinstance(bundle, TemplateBundle)


def raw(slug="demo"):
    return json.loads((BROKEN / "rollback-on-publish" / "template.json").read_text()) | {"slug": slug}


def test_a_structural_error_names_every_offending_field():
    bad = raw()
    bad["name"] = ""
    bad["workflows"][0]["role"] = "boss"
    del bad["summary"]
    with pytest.raises(TemplateBundleError) as e:
        parse_bundle(bad)
    paths = {i.path for i in e.value.issues}
    assert {"name", "summary", "workflows.0.role"} <= paths
    assert all(i.code == "TPL_SCHEMA" for i in e.value.issues)


def test_not_an_object_and_unknown_format_are_refused():
    with pytest.raises(TemplateBundleError) as e:
        parse_bundle([1, 2])
    assert e.value.issues[0].code == "TPL_SCHEMA"
    with pytest.raises(TemplateBundleError) as e:
        validate_bundle({"format": "n8n/1"})
    assert e.value.issues[0].code == "TPL_FORMAT" and "n8n/1" in e.value.issues[0].message


def test_a_bad_few_shot_is_reported():
    b = raw()
    b["few_shot"] = {"request": "do a thing", "spec": {"steps": "not a list"}}
    _bundle, issues = validate_bundle(b, {"svc": (BROKEN / "rollback-on-publish/apis/svc.yaml").read_text()})
    assert "TPL_FEW_SHOT" in {i.code for i in issues}


def test_catalog_level_requires_checks(tmp_path):
    def put(slug, mutate):
        d = tmp_path / slug
        shutil.copytree(BROKEN / "rollback-on-publish", d)
        b = json.loads((d / "template.json").read_text())
        b["slug"] = slug
        mutate(b)
        (d / "template.json").write_text(json.dumps(b))
        return d

    put("lib", lambda b: None)
    put("needs-missing", lambda b: b["requires"].update(templates=[{"slug": "nope", "version": "*"}]))
    put("needs-newer", lambda b: b["requires"].update(templates=[{"slug": "lib", "version": "^2"}]))
    put("a", lambda b: b["requires"].update(templates=[{"slug": "b", "version": "*"}]))
    put("b", lambda b: b["requires"].update(templates=[{"slug": "a", "version": "*"}]))
    with pytest.raises(TemplateBundleError) as e:
        load_catalog(tmp_path, strict=True)
    msgs = " | ".join(str(i) for i in e.value.issues)
    assert "'nope' is not in the catalog" in msgs
    assert "does not satisfy '^2'" in msgs
    assert "TPL_REQUIRES_CYCLE" in msgs
    survivors = load_catalog(tmp_path, strict=False)
    assert sorted(survivors) == ["lib"], "non-strict (production) skips the bad bundles and keeps the good one"


def test_a_cross_template_token_must_point_at_a_real_subworkflow(tmp_path):
    shutil.copytree(BROKEN / "rollback-on-publish", tmp_path / "lib")
    lib = json.loads((tmp_path / "lib/template.json").read_text())
    lib["slug"] = "lib"
    (tmp_path / "lib/template.json").write_text(json.dumps(lib))
    shutil.copytree(BROKEN / "rollback-on-publish", tmp_path / "user")
    user = json.loads((tmp_path / "user/template.json").read_text())
    user["slug"] = "user"
    user["requires"]["templates"] = [{"slug": "lib", "version": "*"}]
    call = next(n for n in user["workflows"][0]["definition"]["nodes"] if n["name"] == "Call helper")
    call["parameters"]["workflowId"] = "@tpl:workflow/lib:ghost"
    (tmp_path / "user/template.json").write_text(json.dumps(user))
    with pytest.raises(TemplateBundleError) as e:
        load_catalog(tmp_path, strict=True)
    assert any(i.code == "TPL_TOKEN_UNRESOLVED" and "no sub-workflow with key 'ghost'" in i.message for i in e.value.issues)
    call["parameters"]["workflowId"] = "@tpl:workflow/lib:helper"
    (tmp_path / "user/template.json").write_text(json.dumps(user))
    assert sorted(load_catalog(tmp_path, strict=True)) == ["lib", "user"]


def test_a_missing_directory_is_an_empty_catalog(tmp_path):
    assert load_catalog(tmp_path / "nowhere", strict=True) == {}


# ── lints, pure ──────────────────────────────────────────────────────────────────────────────


def test_luhn_and_iban_detectors():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert iban_ok("GB82WEST12345698765432") and not iban_ok("GB82WEST12345698765431")
    fake = next(f"400000123456789{d}" for d in range(10) if luhn_ok(f"400000123456789{d}"))
    assert luhn_ok(fake) and secret_findings(f"card {fake} is a reserved test number") == []
    assert any("card" in f for f in secret_findings("pay 4111 1111 1111 1111"))
    assert any("IBAN" in f for f in secret_findings("to GB82 WEST 1234 5698 7654 32 please"))
    assert secret_findings("an order number 1234567890123 is not a card") == []
    assert any("bearer" in f for f in secret_findings("Authorization: Bearer abcdefghijklmnop1234"))
    assert secret_findings("Authorization: Bearer {{ $vars.SOME_KEY }}") == []
    assert any("private key" in f for f in secret_findings("-----BEGIN RSA PRIVATE KEY-----"))
    assert any("password" in f for f in secret_findings("postgres://admin:hunter22@db.example/x"))
    assert real_emails("a@example.com b@example.com") == [] and real_emails("x@bank.com") == ["x@bank.com"]


def test_semver_and_ranges():
    assert parse_semver("1.2.3") == (1, 2, 3) and parse_semver("1.2") is None and parse_semver("v1.2.3") is None
    assert satisfies("1.4.0", "^1") and satisfies("1.4.0", "^1.2.0") and not satisfies("1.1.0", "^1.2.0")
    assert not satisfies("2.0.0", "^1") and satisfies("2.0.0", "*") and satisfies("1.0.0", None)
    assert satisfies("1.0.0", "1.0.0") and not satisfies("1.0.1", "1.0.0")
    assert not satisfies("1.0.0", "^x")


def test_the_hash_depends_on_the_bundle_and_its_api_files():
    a = {"x": 1}
    assert bundle_hash(a, {"k": "v"}) == bundle_hash({"x": 1}, {"k": "v"})
    assert bundle_hash(a, {"k": "v"}) != bundle_hash(a, {"k": "w"}) != bundle_hash({"x": 2}, {"k": "w"})


# ── planning ─────────────────────────────────────────────────────────────────────────────────


def refund():
    return load_catalog(SHIPPED, strict=True)["refund-triage"]


def plan_for(snapshot, **kw):
    lt = refund()
    from src.services.template_bundle import normalized_apis
    return plan_install(lt.bundle, snapshot, api_specs=normalized_apis(lt.bundle, lt.api_texts), **kw)


def test_a_clean_project_keeps_every_name():
    p = plan_for(ProjectSnapshot())
    assert p.workflow_names == {"main": "Refund triage", "eligibility": "Refund eligibility"}
    assert p.order == ["eligibility", "main"] and p.renamed == [] and p.clashes == []
    assert p.tables["refund_limits"].name == "refund_limits" and p.slugs == {"main": "refund-triage"}


def test_clashing_names_are_numbered_from_two():
    snap = ProjectSnapshot(workflow_names={"Refund triage", "Refund triage (2)", "Refund eligibility"},
                           tables={"refund_limits": [], "refund_limits_2": []}, interface_slugs={"refund-triage"})
    p = plan_for(snap)
    assert p.workflow_names == {"main": "Refund triage (3)", "eligibility": "Refund eligibility (2)"}
    assert p.tables["refund_limits"].name == "refund_limits_3"
    assert p.slugs == {"main": "refund-triage-2"}
    assert {r["kind"] for r in p.renamed} == {"workflow", "table", "interface_slug"}


def test_fail_mode_lists_every_clash_and_renames_nothing():
    snap = ProjectSnapshot(workflow_names={"Refund triage", "Refund eligibility"}, tables={"refund_limits": []}, interface_slugs={"refund-triage"})
    p = plan_for(snap, clash="fail")
    assert {(c["kind"], c["name"]) for c in p.clashes} == {("workflow", "Refund triage"), ("workflow", "Refund eligibility"),
                                                         ("table", "refund_limits"), ("interface_slug", "refund-triage")}
    assert p.renamed == []


def test_table_reuse_needs_opt_in_and_compatible_columns():
    ok_cols = [{"name": "segment", "type": "string", "required": True}, {"name": "auto_limit", "type": "number", "required": True},
               {"name": "note", "type": "string", "required": False}]
    snap = ProjectSnapshot(tables={"refund_limits": ok_cols})
    assert plan_for(snap).tables["refund_limits"].name == "refund_limits_2"           # no opt-in: renamed
    assert plan_for(snap, reuse_tables=["refund_limits"]).tables["refund_limits"].reuse is True
    wrong_type = [{"name": "segment", "type": "number", "required": True}, {"name": "auto_limit", "type": "number", "required": True}]
    assert plan_for(ProjectSnapshot(tables={"refund_limits": wrong_type}), reuse_tables=["refund_limits"]).tables["refund_limits"].name == "refund_limits_2"
    missing_col = [{"name": "segment", "type": "string", "required": True}]
    assert not columns_compatible(missing_col, refund().bundle.tables[0].columns)
    extra_required = ok_cols + [{"name": "owner", "type": "string", "required": True}]
    assert not columns_compatible(extra_required, refund().bundle.tables[0].columns)
    assert columns_compatible(extra_required[:2] + [{"name": "owner", "type": "string", "required": True, "default": "x"}], refund().bundle.tables[0].columns)


def test_an_identical_api_is_reused_and_a_different_one_is_renamed():
    lt = refund()
    from src.services.template_bundle import normalized_apis
    spec = normalized_apis(lt.bundle, lt.api_texts)["refunds"]
    same = plan_for(ProjectSnapshot(apis={"refunds": api_content_hash(spec)}))
    assert same.apis["refunds"].reuse is True
    other = plan_for(ProjectSnapshot(apis={"refunds": "different-hash"}))
    assert other.apis["refunds"].name == "refunds (2)" and not other.apis["refunds"].reuse


def test_webhook_paths_are_suffixed_on_clash():
    lt = load_catalog(SHIPPED, strict=True)["card-dispute-intake"]
    free = plan_install(lt.bundle, ProjectSnapshot())
    assert free.webhook_paths == {("main", "Dispute webhook"): "card-dispute-intake"}
    taken = plan_install(lt.bundle, ProjectSnapshot(webhook_paths={"card-dispute-intake"}))
    assert taken.webhook_paths == {("main", "Dispute webhook"): "card-dispute-intake-2"}
    assert taken.renamed[0]["kind"] == "webhook_path"


def test_install_request_is_strict():
    assert InstallRequest().clash == "rename" and InstallRequest().run_tests is True
    with pytest.raises(Exception):
        InstallRequest(clash="overwrite")
    with pytest.raises(Exception):
        InstallRequest(surprise=1)


# ── the authz seam ───────────────────────────────────────────────────────────────────────────


def test_authz_is_a_no_op_that_says_yes_and_reads_the_optional_actor():
    class Req:
        headers = {"x-user-id": " dana "}

    for action in authz.ACTIONS:
        assert authz.authorize(Req(), action, {"anything": 1}) is True
    assert authz.authorize(None, "template.install") is True
    assert authz.actor_of(Req()) == "dana" and authz.actor_of(None) is None
    assert set(authz.ACTIONS) >= {"template.install", "template.export", "workflow.extract", "subworkflow.call", "workflow.delete.force"}
