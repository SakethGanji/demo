"""Interface chat: one turn in flight, history for the run, replies, token scoping (DESIGN-STAGE5-UIS section 6.2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.types import NodeData
from src.services import chat_service
from src.services.chat_service import ChatService, reply_text, trim_history
from src.services.interface_service import InterfaceError

from ._interface_fakes import FakeChatRepo, FakeInterfaceRepo, RunRecorder, fixture, make_service, now

pytestmark = pytest.mark.anyio


async def chat_setup():
    d = fixture("chat_support_ui.json")
    svc, irepo, rec = make_service(FakeInterfaceRepo(d, slug="support-chat"))
    chat = ChatService(object(), repo=FakeChatRepo(), interfaces=svc)
    t = await svc.resolve_live("bank", "support-chat", kinds=("chat",))
    created = await chat.create_session(t, "  Dana  ")
    return chat, t, rec, created


async def code_of(coro) -> tuple[int, str]:
    try:
        await coro
    except InterfaceError as e:
        return e.status, e.code
    return 200, ""


async def test_create_session_returns_a_token_that_is_stored_hashed():
    chat, t, _, created = await chat_setup()
    row = chat.repo.sessions[created["session_id"]]
    assert created["session_id"].startswith("chs_") and created["session_token"].startswith("cs_")
    assert row["token_hash"] != created["session_token"] and row["user_label"] == "Dana" and row["mode"] == "live"


async def test_a_form_interface_has_no_chat():
    svc, *_ = make_service()
    t = await svc.resolve_live("bank", "refunds")
    assert await code_of(ChatService(object(), repo=FakeChatRepo(), interfaces=svc).create_session(t, None)) == (404, "NOT_FOUND")


async def test_a_turn_starts_a_run_with_session_history_and_label():
    chat, t, rec, c = await chat_setup()
    out = await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    assert out == {"seq": 1, "execution_id": "exec_1"}
    call = rec.calls[0]
    assert call["start_node"] == "Chat message" and call["workflow_id"] == "wf_1"
    assert {k: v for k, v in call["item"].items() if k != "timestamp"} == {"message": "hello", "session_id": c["session_id"], "history": [], "user_label": "Dana"}
    assert call["metadata"]["interface"] == {"slug": "support-chat", "mode": "live", "chat_session_id": c["session_id"]}
    assert callable(call["on_finished"])


async def test_second_turn_gets_the_first_turns_history():
    chat, t, rec, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    await chat.record_outcome(c["session_id"], ctx(body={"text": "Hi Dana"}))
    chat.repo.run_status["exec_1"] = "success"
    await chat.send(t, c["session_id"], c["session_token"], "and again", "m2")
    history = rec.calls[1]["item"]["history"]
    assert [(h["role"], h["content"]) for h in history] == [("user", "hello"), ("assistant", "Hi Dana")]


async def test_one_turn_in_flight_per_session_is_409_busy():
    chat, t, rec, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "one", "m1")
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], "two", "m2")) == (409, "CHAT_BUSY")
    assert len(rec.calls) == 1
    chat.repo.run_status["exec_1"] = "success"                       # the run just ended; its reply is about to be written: still busy
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], "two", "m2")) == (409, "CHAT_BUSY")
    chat.repo.fresh = False                                          # ...but a run that died long ago without a reply no longer blocks
    assert (await chat.send(t, c["session_id"], c["session_token"], "three", "m3"))["seq"] == 2


async def test_busy_stays_while_the_run_is_waiting_for_an_approval():
    chat, t, _, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "one", "m1")
    chat.repo.run_status["exec_1"] = "waiting"
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], "two", "m2")) == (409, "CHAT_BUSY")


async def test_client_message_id_dedupes_a_retry():
    chat, t, rec, c = await chat_setup()
    first = await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    again = await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    assert again == first and len(rec.calls) == 1 and len(chat.repo.msgs[c["session_id"]]) == 1


async def test_a_replay_wins_over_busy():
    chat, t, _, c = await chat_setup()
    first = await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    assert await chat.send(t, c["session_id"], c["session_token"], "hello", "m1") == first


@pytest.mark.parametrize("token", [None, "cs_wrong", ""])
async def test_wrong_token_is_404_everywhere(token):
    chat, t, _, c = await chat_setup()
    assert await code_of(chat.send(t, c["session_id"], token, "hi", "m1")) == (404, "NOT_FOUND")
    assert await code_of(chat.messages(t, c["session_id"], token)) == (404, "NOT_FOUND")


async def test_another_sessions_token_and_unknown_sessions_are_404():
    chat, t, _, c = await chat_setup()
    other = await chat.create_session(t, None)
    assert await code_of(chat.messages(t, c["session_id"], other["session_token"])) == (404, "NOT_FOUND")
    assert await code_of(chat.messages(t, "chs_nope", c["session_token"])) == (404, "NOT_FOUND")


async def test_a_session_of_another_mode_or_slug_is_not_visible():
    chat, t, _, c = await chat_setup()
    chat.repo.sessions[c["session_id"]]["mode"] = "preview"
    assert await code_of(chat.messages(t, c["session_id"], c["session_token"])) == (404, "NOT_FOUND")


async def test_message_limits_and_validation():
    chat, t, rec, c = await chat_setup()
    big = "x" * (chat_service.MAX_MESSAGE_BYTES + 1)
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], big, "m1")) == (413, "PAYLOAD_TOO_LARGE")
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], "   ", "m1")) == (422, "VALIDATION_FAILED")
    assert await code_of(chat.send(t, c["session_id"], c["session_token"], "hi", "sys:final:x")) == (422, "VALIDATION_FAILED")
    assert (await chat.send(t, c["session_id"], c["session_token"], "x" * chat_service.MAX_MESSAGE_BYTES, "m2"))["seq"] == 1
    assert rec.calls


async def test_a_failed_start_leaves_an_error_message_and_frees_the_session():
    chat, t, rec, c = await chat_setup()
    rec.fail = RuntimeError("down")
    with pytest.raises(RuntimeError):
        await chat.send(t, c["session_id"], c["session_token"], "hi", "m1")
    msgs = chat.repo.msgs[c["session_id"]]
    assert [m["role"] for m in msgs] == ["user", "error"] and chat.repo.sessions[c["session_id"]]["busy_execution_id"] is None


async def test_messages_page_with_after_and_busy():
    chat, t, _, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    page = await chat.messages(t, c["session_id"], c["session_token"])
    assert page["busy"] is True and page["next_after"] == 1 and [m["role"] for m in page["messages"]] == ["user"]
    assert set(page["messages"][0]) == {"seq", "role", "content", "created_at"}
    await chat.record_outcome(c["session_id"], SimpleNamespace(execution_id="exec_1", errors=[], webhook_response=SimpleNamespace(body={"text": "yo"}), node_states={}, last_completed_node=None))
    chat.repo.run_status["exec_1"] = "success"
    page = await chat.messages(t, c["session_id"], c["session_token"], after=1)
    assert page["busy"] is False and page["messages"][0]["content"] == "yo" and page["next_after"] == 2
    assert (await chat.messages(t, c["session_id"], c["session_token"], after=2))["messages"] == []


# ── replies ──────────────────────────────────────────────────────────────────────────────────


def ctx(body=None, last=None, **kw):
    states = {"Last": [NodeData(json=last)]} if last is not None else {}
    return SimpleNamespace(execution_id="exec_1", errors=kw.get("errors", []), webhook_response=SimpleNamespace(body=body) if body is not None else None,
                           node_states=states, last_completed_node="Last" if last is not None else None, suspensions=kw.get("suspensions"))


@pytest.mark.parametrize("c,expected", [
    (ctx(body={"text": "a", "message": "b"}), "a"),
    (ctx(body={"message": "b", "response": "c"}), "b"),
    (ctx(body={"response": "c"}), "c"),
    (ctx(body="plain"), "plain"),
    (ctx(body={"other": 1}, last={"response": "from node"}), "from node"),
    (ctx(last={"response": "r", "text": "t"}), "r"),
    (ctx(last={"output": "o"}), "o"),
    (ctx(body={"n": 1}), '{"n": 1}'),
    (ctx(last={"n": 1}), '{"n": 1}'),
    (ctx(), ""),
])
def test_reply_text_priority(c, expected):
    assert reply_text(c) == expected


def test_reply_text_is_capped_at_16kb():
    assert len(reply_text(ctx(body={"text": "x" * 40000})).encode()) <= chat_service.MAX_MESSAGE_BYTES


def test_history_is_capped_by_count_and_size():
    msgs = [{"role": "user", "content": f"m{i}", "created_at": now()} for i in range(30)]
    out = trim_history(msgs)
    assert len(out) == 20 and out[0]["content"] == "m10" and out[-1]["content"] == "m29"
    big = [{"role": "user", "content": "x" * 10000, "created_at": now()} for _ in range(10)]
    out = trim_history(big)
    assert sum(len(m["content"]) for m in out) <= chat_service.MAX_HISTORY_BYTES and len(out) == 3


async def test_outcomes_success_error_and_waiting():
    chat, t, _, c = await chat_setup()
    sid = c["session_id"]
    await chat.send(t, sid, c["session_token"], "hello", "m1")
    waiting = ctx(suspensions=[{"suspension": SimpleNamespace(payload={"title": "Manager review"})}])
    await chat.record_outcome(sid, waiting)
    assert chat.repo.msgs[sid][-1]["content"] == "Waiting for Manager review…" and chat.repo.sessions[sid]["busy_execution_id"] == "exec_1"
    await chat.record_outcome(sid, waiting)                                  # idempotent
    assert len(chat.repo.msgs[sid]) == 2
    await chat.record_outcome(sid, ctx(body={"text": "Approved!"}))           # the resumed run's final reply is a new message
    assert chat.repo.msgs[sid][-1] == {**chat.repo.msgs[sid][-1], "role": "assistant", "content": "Approved!"}
    assert chat.repo.sessions[sid]["busy_execution_id"] is None
    await chat.record_outcome(sid, ctx(body={"text": "Approved!"}))           # the hook and the starting task both report: one message
    assert len(chat.repo.msgs[sid]) == 3


async def test_a_failed_turn_writes_an_error_without_internals():
    chat, t, _, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    await chat.record_outcome(c["session_id"], ctx(errors=[SimpleNamespace(node_name="Assistant", error="API key sk-123 rejected")]))
    msg = chat.repo.msgs[c["session_id"]][-1]
    assert msg["role"] == "error" and msg["content"] == "Something went wrong (ref exec_1)." and "sk-123" not in msg["content"]
    await chat.record_outcome(c["session_id"], None)                          # a crashed task reports None
    assert chat.repo.sessions[c["session_id"]]["busy_execution_id"] is None


async def test_an_empty_reply_becomes_done():
    chat, t, _, c = await chat_setup()
    await chat.send(t, c["session_id"], c["session_token"], "hello", "m1")
    await chat.record_outcome(c["session_id"], ctx())
    assert chat.repo.msgs[c["session_id"]][-1]["content"] == "Done."


def test_register_hooks_waits_for_the_hook_list(monkeypatch):
    from src.repositories import execution_repository as er

    monkeypatch.delattr(er, "on_complete_hooks", raising=False)
    monkeypatch.delattr(er.ExecutionRepository, "on_complete_hooks", raising=False)
    assert chat_service.register_hooks() is False
    hooks: list = []
    monkeypatch.setattr(er, "on_complete_hooks", hooks, raising=False)
    assert chat_service.register_hooks() is True and chat_service.register_hooks() is True
    assert hooks == [chat_service.on_execution_complete]
