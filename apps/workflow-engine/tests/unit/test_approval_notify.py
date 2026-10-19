"""Approval ``notify_email``: recipients resolved into the suspend payload, the approver email (with the studio task link),
sent after the task row exists, and never blocking the run (SMTP failure, no SMTP, bad addresses). Test mode never sends.

SMTP is always monkeypatched (``deliver_email``): nothing here touches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core import config
from src.engine.test_mode import TestRunOptions
from src.engine.types import Suspension
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.flow.approval import ApprovalNode
from src.nodes.integrations import send_email as send_email_mod
from src.nodes.integrations.send_email import EmailSendError
from src.services import approval_notify as an

from ._runner_fakes import FakeRegistry, items, node, wf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class FakeDeliver:
    """Stands in for ``send_email.deliver_email``: records each call, or raises ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    async def __call__(self, recipients, subject, text, html, from_email=None):
        self.calls.append({"to": list(recipients), "subject": subject, "text": text, "html": html})
        if self.error:
            raise self.error
        return "<msg-1@example.com>"


@pytest.fixture
def smtp_on(monkeypatch):
    monkeypatch.setattr(config.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(config.settings, "studio_url", "http://studio.example:5174")
    fake = FakeDeliver()
    monkeypatch.setattr(send_email_mod, "deliver_email", fake)
    return fake


def approval_runner():
    log: list[str] = []
    runner = WorkflowRunner()
    reg = FakeRegistry(log)
    reg._nodes["Approval"] = ApprovalNode()
    runner._registry = reg
    return runner, log


def flow(**params):
    return wf(
        [node("in"), node("review", "Approval", parameters=params), node("yes"), node("no"), node("late")],
        [("in", "review"), ("review", "yes", "approved"), ("review", "no", "rejected"), ("review", "late", "timed_out")],
    )


TASK = {
    "id": "tsk_0001",
    "title": "Refund A-1 for $742",
    "context": {"Order": "A-1", "Amount": 742, "_bindings": {"x": 1}, "_input": {"secret": "no"}},
    "due_at": datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc),
}


# ── recipients ────────────────────────────────────────────────────────────────

def test_recipients_are_split_checked_and_deduplicated():
    assert an.split_recipients("a@x.example, b@x.example;c@x.example") == (["a@x.example", "b@x.example", "c@x.example"], [])
    assert an.split_recipients(" a@x.example , nope, , A@X.example, bad@") == (["a@x.example"], ["nope", "bad@"])
    assert an.split_recipients(["a@x.example", "two words@x"]) == (["a@x.example"], ["two words@x"])
    assert an.split_recipients("") == ([], []) and an.split_recipients(None) == ([], [])


async def test_notify_email_is_resolved_from_vars_into_the_suspend_payload():
    runner, _ = approval_runner()
    w = flow(title="Review {{ $json.order }}", notify_email="{{ $vars.APPROVER_EMAIL }}, not-an-address",
             notify_message="Please look at {{ $json.order }}")
    ctx = await runner.run(w, "in", items({"order": "A-1"}), variables={"APPROVER_EMAIL": "dana@bank.example"})
    p = ctx.suspensions[0]["suspension"].payload
    assert p["notify"] == {"to": ["dana@bank.example"], "invalid": ["not-an-address"], "message": "Please look at A-1"}


async def test_an_empty_notify_email_leaves_the_payload_as_it_was():
    runner, _ = approval_runner()
    ctx = await runner.run(flow(), "in", items({"a": 1}))
    assert "notify" not in ctx.suspensions[0]["suspension"].payload
    # an expression that resolves to nothing is the same as no notify_email
    ctx = await runner.run(flow(notify_email="{{ $vars.APPROVER_EMAIL }}"), "in", items({"a": 1}), variables={"APPROVER_EMAIL": ""})
    assert "notify" not in ctx.suspensions[0]["suspension"].payload


# ── the email ─────────────────────────────────────────────────────────────────

def test_the_link_is_the_studio_task_route(monkeypatch):
    monkeypatch.setattr(config.settings, "studio_url", "http://studio.example:5174/")
    assert an.task_link("ops-team", "tsk_9") == "http://studio.example:5174/p/ops-team/approvals/tsk_9"
    assert an.task_link("a b", "tsk_9", base_url="https://s.example") == "https://s.example/p/a%20b/approvals/tsk_9"


def test_the_default_studio_url_is_the_local_studio():
    assert config.Settings.model_fields["studio_url"].default == "http://localhost:5174"


def test_the_email_has_the_title_workflow_project_context_deadline_and_link():
    link = "http://studio.example:5174/p/ops/approvals/tsk_0001"
    subject, html, text = an.build_email(TASK, workflow_name="Refund triage", project_name="Ops", link=link,
                                         message="Over the limit")
    assert subject == "Approval needed: Refund A-1 for $742"
    for part in (html, text):
        assert "Refund A-1 for $742" in part and "Refund triage" in part and "Ops" in part
        assert "Over the limit" in part and "2026-01-03 09:00 UTC" in part and "A-1" in part and "742" in part
        assert link in part
        assert "_bindings" not in part and "secret" not in part  # captured keys are not shown
    assert f'href="{link}"' in html and "Open the task" in html and html.startswith("<!DOCTYPE html>")


def test_context_values_are_capped_and_escaped():
    ctx = {f"k{i}": "v" for i in range(20)} | {"k0": "<b>" + "x" * 2000}
    _, html, text = an.build_email({"id": "t", "title": "T <x>", "context": ctx}, workflow_name="w", project_name="p", link="l")
    rows = an.context_rows(ctx)
    assert len(rows) == an.MAX_CONTEXT_ROWS + 1 and rows[-1][0] == "…"
    assert len(rows[0][1]) == an.MAX_CONTEXT_VALUE_CHARS
    assert "<b>" not in html and "&lt;b&gt;" in html and "T &lt;x&gt;" in html
    assert "Decide by" not in text  # no deadline, no line


# ── sending ───────────────────────────────────────────────────────────────────

async def test_a_send_goes_to_the_valid_addresses_with_the_task_link(smtp_on):
    out = await an.send_notification({"to": ["dana@bank.example"], "invalid": ["nope"], "message": ""}, TASK,
                                      workflow_name="Refund triage", project_name="Ops", project_slug="ops")
    assert out["sent"] is True and out["message_id"] == "<msg-1@example.com>"
    assert out["to"] == ["dana@bank.example"] and out["invalid"] == ["nope"] and "error" not in out
    (call,) = smtp_on.calls
    assert call["to"] == ["dana@bank.example"] and call["subject"] == "Approval needed: Refund A-1 for $742"
    assert "http://studio.example:5174/p/ops/approvals/tsk_0001" in call["html"]
    assert "http://studio.example:5174/p/ops/approvals/tsk_0001" in call["text"]


async def test_an_smtp_failure_is_recorded_not_raised(monkeypatch, smtp_on, caplog):
    smtp_on.error = EmailSendError("Couldn't send the email via smtp.example.com:1025: 550 mailbox unavailable")
    out = await an.send_notification({"to": ["dana@bank.example"]}, TASK, workflow_name="w", project_name="p", project_slug="p")
    assert out["sent"] is False and "550 mailbox unavailable" in out["error"]
    assert any("approver email failed" in r.getMessage() for r in caplog.records)


async def test_no_smtp_host_sends_nothing_and_says_so(monkeypatch):
    fake = FakeDeliver()
    monkeypatch.setattr(send_email_mod, "deliver_email", fake)
    monkeypatch.setattr(config.settings, "smtp_host", None)
    out = await an.send_notification({"to": ["dana@bank.example"]}, TASK, workflow_name="w", project_name="p", project_slug="p")
    assert out["sent"] is False and out["note"] == an.NO_SMTP_NOTE and fake.calls == []


async def test_only_bad_addresses_sends_nothing_and_records_them(smtp_on):
    out = await an.send_notification({"to": [], "invalid": ["nope"]}, TASK, workflow_name="w", project_name="p", project_slug="p")
    assert out == {"to": [], "invalid": ["nope"], "sent": False, "error": an.NO_RECIPIENT_ERROR, "at": out["at"]}
    assert smtp_on.calls == []


# ── test mode ─────────────────────────────────────────────────────────────────

async def test_a_mocked_approval_never_suspends_so_never_sends(smtp_on):
    runner, _ = approval_runner()
    mock = {"approval": {"decision": "approved"}}
    ctx = await runner.run(flow(notify_email="dana@bank.example"), "in", items({"a": 1}),
                           test_options=TestRunOptions(mocks={"review": mock}, now=NOW, seed=1, persist=False))
    assert ctx.suspensions == [] and ctx.errors == []
    assert smtp_on.calls == []


async def test_task_creation_in_test_mode_queues_no_email(monkeypatch):
    """Defence in depth: even a test-mode suspension that reached ``complete`` is never handed to the notifier."""
    from src.engine import task_resumer
    from src.repositories.execution_repository import ExecutionRepository

    monkeypatch.setattr(task_resumer, "build_snapshot", lambda context, entry: {})

    class Tasks:
        async def insert(self, row):
            return True

    entry = {"node": "review", "run_index": 0,
             "suspension": Suspension(kind="approval", payload={"title": "t", "notify": {"to": ["dana@bank.example"]}})}
    ex = SimpleNamespace(project_id="proj_default", workflow_id="wf_1")
    for mode, expected in (("test", 0), ("manual", 1)):
        ctx = SimpleNamespace(suspensions=[entry], version_id=None, execution_id="exe_1", mode=mode)
        queued = await ExecutionRepository(None)._create_tasks(Tasks(), ctx, ex)
        assert len(queued) == expected


# ── against the database: the task exists and the run waits whatever the email does ─────────

def _snap(task: dict) -> dict:
    s = task["snapshot"]
    return json.loads(s) if isinstance(s, (str, bytes)) else s


@pytest.fixture
async def world(pg):
    from ._pg_world import install_counter, make_world, remove_counter

    install_counter()
    try:
        yield await make_world(pg)
    finally:
        remove_counter()


async def _pause(world, **params):
    from ._pg_world import approval_flow

    stored = await world.create(approval_flow(**params))
    _, ctx = await world.run(stored.id)
    return ctx.execution_id, await world.task(ctx.execution_id)


async def test_an_smtp_failure_still_leaves_a_waiting_run_and_an_open_task(world, smtp_on):
    smtp_on.error = EmailSendError("Couldn't send the email via smtp.example.com:1025: Connection refused")
    exec_id, task = await _pause(world, title="Check it", notify_email="dana@bank.example, nope")
    assert (await world.execution(exec_id))["status"] == "waiting"
    assert task["status"] == "open"
    notify = _snap(task)["notify"]
    assert notify["sent"] is False and "Connection refused" in notify["error"]
    assert notify["to"] == ["dana@bank.example"] and notify["invalid"] == ["nope"]
    (call,) = smtp_on.calls
    assert f"/approvals/{task['id']}" in call["html"]  # sent after the row existed: the link has the task id

    from src.services.task_service import TaskService

    async with world.sf() as s:
        detail = await TaskService(s).get(task["id"])
    assert detail["notify"]["sent"] is False and detail["status"] == "open"


async def test_a_sent_email_is_recorded_on_the_task(world, smtp_on):
    exec_id, task = await _pause(world, title="Check it", notify_email="dana@bank.example")
    assert (await world.execution(exec_id))["status"] == "waiting"
    notify = _snap(task)["notify"]
    assert notify["sent"] is True and notify["message_id"] == "<msg-1@example.com>"
    (call,) = smtp_on.calls
    assert call["subject"] == "Approval needed: Check it" and call["to"] == ["dana@bank.example"]
    assert "http://studio.example:5174/p/" in call["text"] and call["text"].rstrip().endswith(f"/approvals/{task['id']}")


async def test_no_notify_email_sends_nothing_and_records_nothing(world, smtp_on):
    exec_id, task = await _pause(world, title="Check it")
    assert (await world.execution(exec_id))["status"] == "waiting"
    assert smtp_on.calls == [] and "notify" not in _snap(task)

    from src.services.task_service import TaskService

    async with world.sf() as s:
        assert "notify" not in await TaskService(s).get(task["id"])
