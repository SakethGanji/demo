"""The AI builder sees the project's datasets: ``load_ctx`` loads them (this project's only), the spec / build prompts carry a ``datasets``
section and its rule, the dataset readers (and the report chain when asked) get full catalog detail, and the checker holds a
``datasetSelector`` to the project's real datasets (WF006). Real Postgres for the context, the fake LLM for the prompts; no real model."""

from __future__ import annotations

import json
import uuid

import pytest

from src.db.models import ProjectDatasetModel
from src.engine import wf_ai
from src.services import ai_builder as B
from src.services import ai_builder_prompts as P
from src.services import ai_grounding
from src.services.project_service import ProjectService, resolve_project
from src.services.workflow_checker import StaticLookups, check_workflow
from tests.unit._stage6_b4_support import install_script, prompts_seen, uninstall
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

COLUMNS = [{"name": "order_id", "type": "BIGINT"}, {"name": "region", "type": "VARCHAR"}, {"name": "amount", "type": "DOUBLE"}]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wf_ai.clear_cache()
    for k in ("WF_AI_MAX_CALLS", "WF_AI_MAX_REPAIR_ROUNDS", "WF_AI_BUILD_DEADLINE_S", "WF_AI_STORE_PROMPTS"):
        monkeypatch.delenv(k, raising=False)
    B._BUSY.clear()
    yield
    uninstall()


async def add_dataset(sf, project_ref: str, name: str, columns=COLUMNS, rows: int = 1200) -> str:
    async with sf() as s:
        project = await resolve_project(s, project_ref)
        did = str(uuid.uuid4())
        link = ProjectDatasetModel(id=str(uuid.uuid4()), team_id=project.team_id or "default", project_id=project.id, dataset_id=did,
                                   name=name, file_name=f"{name}.csv", format="csv", row_count=rows, column_count=len(columns), columns=columns)
        s.add(link)
        await s.commit()
        return link.id


async def new_project(sf) -> str:
    async with sf() as s:
        return (await ProjectService(s).create(f"ai-ds-{uuid.uuid4().hex[:8]}")).id


def profile_workflow(dataset_id: str) -> dict:
    return {"name": "w", "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Profile orders", "type": "Profile", "parameters": {"sourceType": "dataset", "datasetId": dataset_id}, "position": {"x": 200, "y": 0}},
    ], "connections": [{"source_node": "Start", "source_output": "main", "target_node": "Profile orders", "target_input": "main"}]}


def wf006(defn: dict, lookups) -> list[dict]:
    return [f for f in check_workflow(defn, lookups=lookups)["findings"] if f["code"] == "WF006" and f.get("param") == "datasetId"]


# ── the context ──────────────────────────────────────────────────────────────────────────────


async def test_the_context_holds_this_projects_datasets_only(pg):
    project = await new_project(pg)
    mine = await add_dataset(pg, project, "orders_2026")
    theirs = await add_dataset(pg, await new_project(pg), "secret_payroll")
    ctx = await B.load_ctx(project)
    entry = next(d for d in ctx.datasets if d["name"] == "orders_2026")
    assert entry == {"id": mine, "name": "orders_2026", "row_count": 1200, "column_count": 3, "columns": COLUMNS}
    assert all(d["id"] != theirs and d["name"] != "secret_payroll" for d in ctx.datasets)
    assert mine in ctx.lookups.datasets and "orders_2026" in ctx.lookups.datasets
    assert theirs not in ctx.lookups.datasets and "secret_payroll" not in ctx.lookups.datasets


async def test_the_context_caps_datasets_and_columns(pg):
    wide = [{"name": f"c{i}", "type": "VARCHAR"} for i in range(40)]
    project = await new_project(pg)
    for i in range(27):
        await add_dataset(pg, project, f"ds_{i:02d}", columns=wide)
    ctx = await B.load_ctx(project)
    assert len(ctx.datasets) == 25 and all(len(d["columns"]) == 30 for d in ctx.datasets)
    assert len({v for v in ctx.lookups.datasets if v.startswith("ds_")}) == 27   # every dataset may still be named


async def test_a_project_without_datasets_sends_no_section(pg):
    ctx = await B.load_ctx(await new_project(pg))
    assert ctx.datasets == [] and ctx.lookups.datasets == set()
    assert B._ds_section(ctx) == {}


# ── the prompts ──────────────────────────────────────────────────────────────────────────────


async def test_the_spec_and_build_prompts_carry_the_datasets_and_the_rule(pg):
    project = await new_project(pg)
    link = await add_dataset(pg, project, "orders_2026")
    theirs = await add_dataset(pg, await new_project(pg), "secret_payroll")
    fake = install_script("first_try")
    v = await B.draft_spec(project, "Amounts over 500 are marked for review, the rest approved, the caller always gets the status.", "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    await B.build(v["id"], "Asha")
    seen = prompts_seen(fake)
    assert len(seen) >= 2
    for prompt in seen[:2]:   # WF_AI:SPEC and WF_AI:BUILD
        assert '"datasets":[' in prompt and link in prompt and "orders_2026" in prompt
        assert "`datasetId` set to that dataset's `id` from `datasets`" in prompt and "never invent an id" in prompt
        assert theirs not in prompt and "secret_payroll" not in prompt


def test_the_rule_and_section_render_only_when_there_are_datasets():
    ctx = {"untrusted_request": "x", "mode": "new", "catalog": [], "api_operations": [], "existing_workflows": [], "variable_names": []}
    s0, u0 = P.render("spec", ctx, max_chars=10**9)
    assert '"datasets"' not in u0 and "Project datasets." not in s0
    entry = {"id": "0190-link", "name": "orders_2026", "row_count": 3, "column_count": 1, "columns": [{"name": "a", "type": "BIGINT"}]}
    for task, extra in (("spec", ctx), ("build", {}), ("repair", {}), ("spec_repair", {})):
        s1, u1 = P.render(task, {**extra, "datasets": [entry]}, max_chars=10**9)
        assert "Project datasets." in s1 and "sourceType` \"dataset\"" in s1
        assert json.loads(u1)["datasets"] == [entry]


# ── full catalog detail ──────────────────────────────────────────────────────────────────────


def _detail(catalog: list[dict]) -> set[str]:
    return {e["type"] for e in catalog if "parameters" in e}


def test_the_dataset_readers_get_full_parameters_when_the_project_has_datasets():
    without = _detail(ai_grounding.catalog_subset(None, extra_types=ai_grounding.request_types("count the rows", False)))
    with_ds = ai_grounding.catalog_subset(None, extra_types=ai_grounding.request_types("count the rows", True))
    assert not {"Profile", "Sample", "Aggregate"} & without
    assert {"Profile", "Sample", "Aggregate"} <= _detail(with_ds)
    profile = next(e for e in with_ds if e["type"] == "Profile")
    names = {p["name"] for p in profile["parameters"]}
    assert {"sourceType", "datasetId"} <= names


@pytest.mark.parametrize("request_text", ["generate an HTML report", "email it to me", "send a summary", "analyse it", "have an LLM analyze the data"])
def test_the_report_chain_gets_full_parameters_when_asked(request_text):
    detail = _detail(ai_grounding.catalog_subset(None, extra_types=ai_grounding.request_types(request_text, False)))
    registered = {e["type"] for e in ai_grounding.full_catalog()}
    assert {"LLMChat", "SendEmail"} <= detail
    assert ("Html" in detail) == ("Html" in registered)   # Html shows up as soon as it is registered


def test_the_build_context_uses_the_same_extra_types():
    ctx = B.Ctx(project_id="p", team_id="t", secrets=set(), lookups=StaticLookups(), operations={}, api_ops=[], catalog=ai_grounding.full_catalog(),
                variable_names=[], existing_workflows=[], egress=[],
                datasets=[{"id": "x", "name": "orders_2026", "row_count": 1, "column_count": 0, "columns": []}])
    spec = {"steps": [], "trigger": {"kind": "manual"}, "tests": []}
    payload = B._build_context({"request": "profile orders_2026 and email a report"}, ctx, {"nodes": []}, spec)
    assert payload["datasets"] == ctx.datasets
    assert {"Profile", "Sample", "Aggregate", "LLMChat", "SendEmail"} <= _detail(payload["catalog"])


# ── validation ───────────────────────────────────────────────────────────────────────────────


def test_a_known_dataset_passes_and_an_unknown_one_is_wf006():
    lk = StaticLookups(datasets={"0190-link", "orders_2026"})
    assert wf006(profile_workflow("0190-link"), lk) == []
    assert wf006(profile_workflow("orders_2026"), lk) == []
    bad = wf006(profile_workflow("made-up-id"), lk)
    assert len(bad) == 1 and bad[0]["severity"] == "error" and "made-up-id" in bad[0]["message"]
    assert wf006(profile_workflow("{{ $json.dataset }}"), lk) == []   # an expression is resolved at run time


def test_without_known_datasets_nothing_is_checked():
    assert wf006(profile_workflow("anything"), StaticLookups()) == []


async def test_the_builder_lookups_hold_the_dataset_to_the_project(pg):
    project = await new_project(pg)
    link = await add_dataset(pg, project, "orders_2026")
    theirs = await add_dataset(pg, await new_project(pg), "secret_payroll")
    ctx = await B.load_ctx(project)
    assert wf006(profile_workflow(link), ctx.lookups) == []
    assert len(wf006(profile_workflow(theirs), ctx.lookups)) == 1
    assert len(wf006(profile_workflow(str(uuid.uuid4())), ctx.lookups)) == 1
