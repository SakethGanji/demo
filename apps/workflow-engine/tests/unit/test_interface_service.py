"""Public interface service: resolution (404 hides everything), schema, submit, idempotency, the run view (DESIGN-STAGE5-UIS section 6.1)."""

from __future__ import annotations

import copy

import pytest

from src.services.interface_service import InterfaceError, bearer, clean_idempotency_key, hash_token, reachable_count, token_matches

from ._interface_fakes import FakeInterfaceRepo, RunRecorder, fixture, make_service

pytestmark = pytest.mark.anyio

GOOD = {"order_id": "A-12345", "amount": 742, "reason": "damaged", "email": "dana@example.com"}


async def status_of(coro) -> int:
    try:
        await coro
    except InterfaceError as e:
        return e.status
    return 200


# ── resolution ───────────────────────────────────────────────────────────────────────────────


async def test_a_published_interface_resolves_with_the_live_environment():
    svc, *_ = make_service()
    t = await svc.resolve_live("bank", "refunds")
    assert (t.project_id, t.workflow_id, t.mode, t.environment, t.version_id, t.trigger_node, t.kind) == \
        ("proj_1", "wf_1", "live", "prod", 7, "Refund request", "custom")


@pytest.mark.parametrize("project,slug", [("nope", "refunds"), ("bank", "other"), ("proj_2", "refunds")])
async def test_unknown_project_or_slug_is_404(project, slug):
    svc, *_ = make_service()
    assert await status_of(svc.resolve_live(project, slug)) == 404


async def test_unpublished_workflow_is_404():
    svc, *_ = make_service(FakeInterfaceRepo(active=False))
    assert await status_of(svc.resolve_live("bank", "refunds")) == 404


async def test_studio_only_access_is_404():
    d = fixture()
    d["interface"]["access"] = {"mode": "studio"}
    svc, *_ = make_service(FakeInterfaceRepo(d))
    assert await status_of(svc.resolve_live("bank", "refunds")) == 404


async def test_a_version_without_the_interface_or_with_another_slug_is_404():
    d = fixture()
    d["interface"]["slug"] = "renamed"
    svc, *_ = make_service(FakeInterfaceRepo(d, slug="refunds"))
    assert await status_of(svc.resolve_live("bank", "refunds")) == 404
    d2 = fixture()
    del d2["interface"]
    svc, repo, _ = make_service(FakeInterfaceRepo(d2, slug="refunds"))
    assert await status_of(svc.resolve_live("bank", "refunds")) == 404


async def test_kinds_are_enforced():
    svc, *_ = make_service()
    assert await status_of(svc.resolve_live("bank", "refunds", kinds=("chat",))) == 404
    assert (await svc.resolve_live("bank", "refunds", kinds=("form", "custom"))).kind == "custom"


# ── schema ───────────────────────────────────────────────────────────────────────────────────


async def test_schema_exposes_fields_and_public_screens_only():
    svc, *_ = make_service()
    out = svc.schema_of(await svc.resolve_live("bank", "refunds"))
    assert out["kind"] == "custom" and out["title"] == "Refund request" and out["submit_label"] == "Request refund"
    assert [f["name"] for f in out["fields"]] == ["order_id", "amount", "reason", "email"]
    assert set(out["screens"]) == {"start", "result"}                      # task screens are never public by default
    blob = repr(out)
    for secret in ("Classify", "REFUND_CLASSIFY", "Manager review", "__REFUND_API_URL__", "Idempotency-Key", "$node"):
        assert secret not in blob
    assert set(out) == {"kind", "title", "description", "submit_label", "fields", "screens"}


async def test_schema_adds_task_screens_only_when_public_tasks_is_on():
    d = fixture()
    d["interface"]["access"] = {"mode": "public", "public_tasks": True}
    svc, *_ = make_service(FakeInterfaceRepo(d))
    assert "task:Manager review" in svc.schema_of(await svc.resolve_live("bank", "refunds"))["screens"]


async def test_a_form_kind_has_no_screens_key():
    d = fixture()
    d["interface"] = {**d["interface"], "kind": "form", "screens": {}}
    svc, *_ = make_service(FakeInterfaceRepo(d))
    assert "screens" not in svc.schema_of(await svc.resolve_live("bank", "refunds"))


# ── submit ───────────────────────────────────────────────────────────────────────────────────


async def test_submit_validates_then_starts_one_detached_run():
    svc, repo, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    out = await svc.submit(t, GOOD, "key-1")
    assert out["execution_id"] == "exec_1" and out["submission_id"].startswith("sub_") and out["run_token"].startswith("rt_")
    (call,) = rec.calls
    assert call["workflow_id"] == "wf_1" and call["start_node"] == "Refund request" and call["environment"] == "prod" and call["version_id"] == 7
    assert call["item"]["order_id"] == "A-12345" and call["item"]["_submission"]["id"] == out["submission_id"] and call["item"]["_submission"]["mode"] == "live"
    assert call["metadata"] == {"trigger": {"type": "interface", "slug": "refunds"},
                                "interface": {"slug": "refunds", "submission_id": out["submission_id"], "mode": "live"}}
    sub = repo.submissions[out["submission_id"]]
    assert sub["execution_id"] == "exec_1" and sub["run_token_hash"] == hash_token(out["run_token"]) and out["run_token"] not in repr(sub)


@pytest.mark.parametrize("fields,code,field", [
    ({**GOOD, "evil": "x"}, "UNKNOWN_FIELD", "evil"),
    ({**GOOD, "order_id": "bad"}, "VALIDATION_FAILED", "order_id"),
    ({**GOOD, "amount": "742"}, "VALIDATION_FAILED", "amount"),
    ({**GOOD, "amount": 0}, "VALIDATION_FAILED", "amount"),
    ({**GOOD, "reason": "because"}, "VALIDATION_FAILED", "reason"),
    ({k: v for k, v in GOOD.items() if k != "email"}, "VALIDATION_FAILED", "email"),
    ([], "VALIDATION_FAILED", ""),
])
async def test_invalid_submissions_are_422_and_start_nothing(fields, code, field):
    svc, repo, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    with pytest.raises(InterfaceError) as e:
        await svc.submit(t, fields, "k")
    assert (e.value.status, e.value.code) == (422, code) and field in e.value.fields
    assert rec.calls == [] and repo.submissions == {}


async def test_a_template_in_a_value_stays_data():
    svc, _, rec = make_service()
    d = fixture()
    d["nodes"][0]["parameters"]["input_schema"]["properties"]["reason"] = {"type": "string"}
    svc, _, rec = make_service(FakeInterfaceRepo(d))
    t = await svc.resolve_live("bank", "refunds")
    await svc.submit(t, {**GOOD, "reason": "{{ $vars.SECRET }}"}, "k")
    assert rec.calls[0]["item"]["reason"] == "{{ $vars.SECRET }}"


async def test_the_same_idempotency_key_returns_the_same_run_with_a_fresh_token():
    svc, repo, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    first = await svc.submit(t, GOOD, "dup")
    second = await svc.submit(t, GOOD, "dup")
    assert len(rec.calls) == 1
    assert (second["submission_id"], second["execution_id"]) == (first["submission_id"], first["execution_id"])
    assert second["run_token"] != first["run_token"]
    # the reissued token is the one that works
    repo.add_run("exec_1", "running")
    assert (await svc.run_view(t, "exec_1", second["run_token"]))["status"] == "running"
    assert await status_of(svc.run_view(t, "exec_1", first["run_token"])) == 404


async def test_different_keys_are_different_runs():
    svc, _, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    a, b = await svc.submit(t, GOOD, "a"), await svc.submit(t, GOOD, "b")
    assert a["execution_id"] != b["execution_id"] and len(rec.calls) == 2


async def test_a_failed_start_releases_the_key_so_a_retry_can_run():
    svc, repo, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    rec.fail = RuntimeError("engine down")
    with pytest.raises(RuntimeError):
        await svc.submit(t, GOOD, "retry")
    assert repo.submissions == {}
    rec.fail = None
    assert (await svc.submit(t, GOOD, "retry"))["execution_id"] == "exec_1"


async def test_a_replay_before_the_first_request_finished_starting_waits_then_gives_up(monkeypatch):
    svc, repo, _ = make_service()
    t = await svc.resolve_live("bank", "refunds")
    await repo.insert_submission(id="sub_x", project_id="proj_1", workflow_id="wf_1", slug="refunds", key="slow", token_hash="h", mode="live")

    async def no_sleep(_):
        return None

    monkeypatch.setattr("src.services.interface_service.asyncio.sleep", no_sleep)
    with pytest.raises(InterfaceError) as e:
        await svc.submit(t, GOOD, "slow")
    assert (e.value.status, e.value.code) == (409, "SUBMISSION_IN_PROGRESS")


async def test_chat_interfaces_do_not_take_submissions():
    d = fixture("chat_support_ui.json")
    svc, *_ = make_service(FakeInterfaceRepo(d, slug="support-chat"))
    t = await svc.resolve_live("bank", "support-chat")
    assert await status_of(svc.submit(t, {}, "k")) == 404


def test_idempotency_key_rules():
    assert clean_idempotency_key(" abc ", "body") == "abc" and clean_idempotency_key(None, "body-key") == "body-key"
    assert clean_idempotency_key("hdr", "body") == "hdr"                      # the header wins
    for bad in (None, "", "   ", "x" * 129, "has space", "semi;colon", "new\nline"):
        with pytest.raises(InterfaceError) as e:
            clean_idempotency_key(bad, None)
        assert e.value.status == 400
    assert clean_idempotency_key("x" * 128, None)


def test_tokens_and_bearer():
    assert bearer("Bearer abc") == "abc" and bearer("bearer  abc ") == "abc" and bearer("Basic abc") is None and bearer(None) is None and bearer("Bearer ") is None
    h = hash_token("t")
    assert token_matches("t", h) and not token_matches("u", h) and not token_matches(None, h) and not token_matches("t", None)


# ── run view ─────────────────────────────────────────────────────────────────────────────────


async def started(extra_run=None):
    svc, repo, rec = make_service()
    t = await svc.resolve_live("bank", "refunds")
    out = await svc.submit(t, GOOD, "k")
    return svc, repo, t, out


KEYS = {"execution_id", "status", "progress", "waiting_for", "result", "message", "error"}


async def test_a_running_run_shows_progress_with_a_label_only():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "running")
    repo.progress_by_run["exec_1"] = {"done": 3, "current": "Classify"}
    view = await svc.run_view(t, "exec_1", out["run_token"])
    assert set(view) == KEYS and view["status"] == "running"
    assert view["progress"] == {"done": 3, "total": 10, "current": "Classify"}
    assert view["result"] is None and view["error"] is None and view["waiting_for"] == []


async def test_progress_current_uses_the_node_label_and_can_be_hidden():
    svc, repo, t, out = await started()
    t.definition["nodes"][1]["label"] = "Checking your request"
    repo.add_run("exec_1", "running")
    repo.progress_by_run["exec_1"] = {"done": 1, "current": "Classify"}
    assert (await svc.run_view(t, "exec_1", out["run_token"]))["progress"]["current"] == "Checking your request"
    t.interface["access"] = {"mode": "public", "hide_progress": True}
    assert "progress" not in await svc.run_view(t, "exec_1", out["run_token"])


async def test_a_waiting_run_lists_task_titles_only():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "waiting")
    repo.tasks["exec_1"] = [{"id": "tsk_1", "title": "Refund A-12345 · $742", "status": "open", "due_at": None, "context": {"Summary": "secret"},
                             "node_name": "Manager review"},
                            {"id": "tsk_2", "title": "done", "status": "approved", "due_at": None}]
    view = await svc.run_view(t, "exec_1", out["run_token"])
    assert view["status"] == "waiting" and view["waiting_for"] == [{"task_id": "tsk_1", "title": "Refund A-12345 · $742", "due_at": None}]
    assert "secret" not in repr(view) and "Manager review" not in repr(view)


async def test_success_carries_the_stored_result_or_the_success_message():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "success", result={"status": "approved", "refund_id": "rf_1"})
    view = await svc.run_view(t, "exec_1", out["run_token"])
    assert view["result"] == {"status": "approved", "refund_id": "rf_1"} and view["message"] is None
    repo.add_run("exec_1", "success")
    view = await svc.run_view(t, "exec_1", out["run_token"])
    assert view["result"] is None and view["message"] == "Your request was received."


async def test_a_failed_run_never_exposes_the_node_error():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "failed", error="Postgres password for db-prod.internal rejected", errors=[{"node_name": "Issue refund"}])
    view = await svc.run_view(t, "exec_1", out["run_token"])
    assert view["status"] == "failed" and view["error"] == {"code": "RUN_FAILED", "message": "Something went wrong. Reference exec_1"}
    assert "Postgres" not in repr(view) and "Issue refund" not in repr(view) and view["result"] is None


@pytest.mark.parametrize("db,public", [("success", "success"), ("failed", "failed"), ("cancelled", "cancelled"), ("waiting", "waiting"),
                                       ("running", "running"), ("error", "failed"), ("timeout", "failed"), ("weird", "running")])
async def test_status_mapping(db, public):
    svc, repo, t, out = await started()
    repo.add_run("exec_1", db)
    assert (await svc.run_view(t, "exec_1", out["run_token"]))["status"] == public


async def test_result_is_hidden_unless_the_run_succeeded():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "failed", result={"status": "partial"})
    assert (await svc.run_view(t, "exec_1", out["run_token"]))["result"] is None


async def test_token_scoping_every_mismatch_is_404():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "running")
    other = await svc.submit(t, GOOD, "k2")
    repo.add_run("exec_2", "running")
    assert await status_of(svc.run_view(t, "exec_1", None)) == 404
    assert await status_of(svc.run_view(t, "exec_1", "rt_wrong")) == 404
    assert await status_of(svc.run_view(t, "exec_1", other["run_token"])) == 404          # another submission's token
    assert await status_of(svc.run_view(t, "exec_9", out["run_token"])) == 404            # unknown execution
    assert await status_of(svc.run_view(t, "exec_1", out["run_token"])) == 200
    repo.runs["exec_1"]["workflow_id"] = "wf_other"
    assert await status_of(svc.run_view(t, "exec_1", out["run_token"])) == 404


async def test_a_preview_token_does_not_open_the_live_view():
    svc, repo, t, out = await started()
    repo.add_run("exec_1", "running")
    repo.submissions[out["submission_id"]]["mode"] = "preview"
    assert await status_of(svc.run_view(t, "exec_1", out["run_token"])) == 404


def test_reachable_count_counts_enabled_nodes_from_the_trigger():
    d = fixture()
    assert reachable_count(d, "Refund request") == 10
    d["nodes"][4]["disabled"] = True                         # Issue refund
    assert reachable_count(d, "Refund request") == 9
    assert reachable_count(d, "Ghost") == 0


# ── preview ──────────────────────────────────────────────────────────────────────────────────


async def test_preview_submit_runs_the_draft_in_the_default_environment_with_a_prefixed_key(monkeypatch):
    svc, repo, rec = make_service()

    async def resolve_preview(workflow_id, kinds=None):
        t = await svc.resolve_live("bank", "refunds")
        t.mode, t.version_id, t.environment = "preview", None, "default"
        return t

    monkeypatch.setattr(svc, "resolve_preview", resolve_preview)
    out = await svc.preview_submit("wf_1", GOOD, "pk")
    assert set(out) == {"execution_id", "submission_id"}
    call = rec.calls[0]
    assert call["version_id"] is None and call["environment"] == "default" and call["metadata"]["interface"]["mode"] == "preview"
    assert next(iter(repo.submissions.values()))["idempotency_key"] == "pv:pk" and next(iter(repo.submissions.values()))["mode"] == "preview"


async def test_preview_data_gathers_input_nodes_tasks_and_result(monkeypatch):
    svc, repo, _ = make_service()
    repo.add_run("exec_1", "waiting", result=None)
    repo.outputs["exec_1"] = {"Refund request": GOOD, "Classify": {"summary": "s"}}
    repo.tasks["exec_1"] = [{"id": "tsk_1", "workflow_id": "wf_1", "execution_id": "exec_1", "node_name": "Manager review", "title": "T",
                             "status": "open", "assignees": [], "due_at": None, "created_at": None, "decided_by": None, "decided_at": None}]
    from src.engine.types import Workflow
    from src.repositories import workflow_repository as wr

    class FakeWfRepo:
        def __init__(self, session): ...
        async def get(self, wid):
            return type("S", (), {"workflow": wr.definition_to_workflow(fixture(), "Refund triage (UI)", "wf_1"), "name": "Refund triage (UI)", "project_id": "proj_1"})()

    monkeypatch.setattr(wr, "WorkflowRepository", FakeWfRepo)
    data = await svc.preview_data("wf_1", "exec_1")
    assert data["input"] == GOOD and data["nodes"]["Classify"] == {"summary": "s"} and data["status"] == "waiting"
    assert data["tasks"][0]["id"] == "tsk_1" and data["tasks"][0]["workflow_name"] == "Refund triage (UI)"
    assert (await svc.preview_data("wf_1", None))["execution_id"] == "exec_1"             # defaults to the latest run
    assert await status_of(svc.preview_data("wf_1", "exec_missing")) == 404
