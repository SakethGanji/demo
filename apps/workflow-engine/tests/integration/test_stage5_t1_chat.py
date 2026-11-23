"""Stage 5 T1: chat sessions (DESIGN-STAGE5-UIS 6.2, 3.4, 4.4, 10.2): a message, the run, the reply, and the waiting message
while a run is held at an Approval; one turn in flight, dedupe, history caps, reply selection, token scoping, restart."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import pytest

from .conftest import StudioClient, uniq
from .test_stage5_t1_support import (  # noqa: F401  (fixtures + helpers)
    C, N, Flow, Stack, concurrently, decide, exec_row, fresh_stack, new_project, poll_until, publish_flow, stack, task_row,
)

pytestmark = pytest.mark.integration


def chat_workflow(variant: str = "approval", *, respond_body: dict | None = None, due_in: str = "3h") -> dict:
    slug = uniq("chat")
    iface = {"kind": "chat", "slug": slug, "title": "Support", "trigger_node": "Chat message", "result_node": "Chat reply",
             "access": {"mode": "public", "public_tasks": False, "hide_progress": False}}
    nodes = [N("Chat message", "ChatInput", {})]
    conns: list[dict] = []
    reply = N("Chat reply", "Respond", {"statusCode": 200, "body": respond_body or {"text": "{{ $json.text }}"}}, 800)

    def say(name, text, x):
        return N(name, "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "text", "value": text}]}, x)

    if variant == "approval":
        nodes += [N("Confirm", "Approval", {"title": "Confirm: {{ $json.message }}", "due_in": due_in, "on_timeout": "timed_out",
                                           "context": {"Question": "{{ $json.message }}"}}, 200),
                  say("Approved reply", "approved: {{ $json.message }}", 400), say("Rejected reply", "rejected: {{ $json.message }}", 400),
                  say("Timed out reply", "timed out: {{ $json.message }}", 400), reply]
        conns = [C("Chat message", "Confirm"), C("Confirm", "Approved reply", "approved"), C("Confirm", "Rejected reply", "rejected"),
                 C("Confirm", "Timed out reply", "timed_out"), C("Approved reply", "Chat reply"), C("Rejected reply", "Chat reply"),
                 C("Timed out reply", "Chat reply")]
    elif variant == "echo":
        nodes += [say("Answer", "echo {{ $json.message }} after {{ length($json.history) }}", 200), reply]
        conns = [C("Chat message", "Answer"), C("Answer", "Chat reply")]
    elif variant == "history_count":
        nodes += [say("Answer", "n={{ length($json.history) }}", 200), reply]
        conns = [C("Chat message", "Answer"), C("Answer", "Chat reply")]
    elif variant == "opaque":                          # the last node has no response/text/message/output key
        nodes += [N("Answer", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "data", "value": "x"}]}, 200), reply]
        conns = [C("Chat message", "Answer"), C("Answer", "Chat reply")]
    elif variant == "fail":
        nodes += [N("Explode", "StopAndError", {"errorType": "error", "message": "INTERNAL-DETAIL password=hunter2"}, 200)]
        conns = [C("Chat message", "Explode")]
    elif variant == "no_respond":                      # no Respond: the last node's output decides the reply
        iface.pop("result_node")
        nodes += [N("Last", "Set", {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "first", "value": "ignored-number"}, *respond_body["fields"]]}, 200)]
        conns = [C("Chat message", "Last")]
    else:
        raise AssertionError(variant)
    return {"name": uniq("chatwf"), "settings": {}, "nodes": nodes, "connections": conns, "interface": iface}


def chat_flow(stack: Stack, variant: str = "approval", **kw) -> Flow:
    return publish_flow(stack, chat_workflow(variant, **kw))


@dataclass
class Chat:
    flow: Flow
    sid: str
    token: str
    client: StudioClient

    @classmethod
    def open(cls, flow: Flow, client: StudioClient | None = None, label: str | None = "Dana") -> "Chat":
        c = client or StudioClient(flow.api.base_url)
        r = c.post(f"{flow.base}/chat/sessions", {"user_label": label} if label else {})
        assert r.status_code == 201, r.text
        d = r.json()
        assert set(d) == {"session_id", "session_token"}
        return cls(flow, d["session_id"], d["session_token"], c)

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def path(self) -> str:
        return f"{self.flow.base}/chat/sessions/{self.sid}/messages"

    def send(self, content: str, cid: str | None = None):
        return self.client.post(self.path, {"content": content, "client_message_id": cid or uuid.uuid4().hex}, headers=self.auth)

    def messages(self, **params) -> dict:
        r = self.client.get(self.path, params=params, headers=self.auth)
        assert r.status_code == 200, r.text
        return r.json()

    def wait_idle(self, timeout: float = 30) -> dict:
        return poll_until(self.messages, lambda p: not p["busy"], timeout, every=0.15, what="the turn to finish")

    def turn(self, content: str) -> list[dict]:
        before = len(self.messages()["messages"])
        r = self.send(content)
        assert r.status_code == 202, r.text
        page = self.wait_idle()
        return page["messages"][before:]

    def pairs(self) -> list[tuple[str, str]]:
        return [(m["role"], m["content"]) for m in self.messages(limit=100)["messages"]]


def the_task(flow: Flow, exec_id: str) -> dict:
    def find():
        items = flow.api.get("/tasks", params={"project_id": flow.pid, "status": "all", "limit": 100}).json()["items"]
        return next((t for t in items if t["execution_id"] == exec_id), None)
    return poll_until(find, lambda t: t is not None, what="the chat task")


# ── a turn ────────────────────────────────────────────────────────────────────


def test_a_message_runs_the_workflow_and_the_reply_lands_after_it(stack):
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow)
    r = chat.send("hello", "c1")
    assert r.status_code == 202 and r.json()["seq"] == 1 and r.json()["execution_id"].startswith("exec_") and set(r.json()) == {"seq", "execution_id"}
    page = chat.wait_idle()
    assert set(page) == {"messages", "busy", "next_after"}
    assert [(m["seq"], m["role"], m["content"]) for m in page["messages"]] == [(1, "user", "hello"), (2, "assistant", "echo hello after 0")]
    assert all(set(m) >= {"seq", "role", "content", "created_at"} for m in page["messages"])
    assert page["next_after"] == 2
    # the run is a normal execution of the published workflow
    assert exec_row(stack, r.json()["execution_id"])["status"] == "success"


def test_history_reaches_the_run_oldest_dropped_at_20_messages(stack):
    flow = chat_flow(stack, "history_count")
    chat = Chat.open(flow)
    counts = []
    for i in range(12):
        new = chat.turn(f"m{i}")
        counts.append(int(new[-1]["content"][2:]))
    assert counts[:4] == [0, 2, 4, 6]
    assert counts[10] == 20 and counts[11] == 20, counts                               # capped at the last 20 messages
    assert len(chat.messages(limit=100)["messages"]) == 24                              # the stored conversation is whole


def test_history_is_capped_at_32kb_oldest_first(stack):
    flow = chat_flow(stack, "history_count")
    chat = Chat.open(flow)
    big = "x" * 15000
    counts = [int(chat.turn(big + str(i))[-1]["content"][2:]) for i in range(6)]
    assert counts[0] == 0 and counts[1] == 2
    assert all(c <= 5 for c in counts[3:]), counts                                      # 15 KB messages: only ~2 fit in 32 KB
    assert max(counts) < 10


def test_the_chat_input_item_has_the_documented_shape(stack):
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow, label="Dana Q")
    chat.turn("one")
    sent = chat.send("two")
    chat.wait_idle()
    out = stack.api.get(f"/executions/{sent.json()['execution_id']}").json()["node_data"]["Chat message"][0]["json"]
    assert out["message"] == "two" and out["session_id"] == chat.sid and out["user_label"] == "Dana Q" and out["timestamp"]
    assert [(h["role"], h["content"]) for h in out["history"]] == [("user", "one"), ("assistant", "echo one after 0")]    # excludes the current message
    assert all(h["at"] for h in out["history"])


def test_a_failed_turn_adds_an_error_message_with_a_reference_and_frees_the_session(stack):
    flow = chat_flow(stack, "fail")
    chat = Chat.open(flow)
    r = chat.send("boom")
    page = chat.wait_idle()
    last = page["messages"][-1]
    assert last["role"] == "error" and last["content"] == f"Something went wrong (ref {r.json()['execution_id']})."
    blob = json.dumps(page)
    assert "INTERNAL-DETAIL" not in blob and "hunter2" not in blob and "Explode" not in blob
    again = chat.send("try again")
    assert again.status_code == 202 and again.json()["seq"] == 3


REPLIES = [
    ("text", {"text": "T"}, "T"), ("message", {"message": "M"}, "M"), ("response", {"response": "R"}, "R"),
    ("text wins over message", {"message": "M", "text": "T"}, "T"),
    ("json fallback", {"answer": 42}, '{"answer": 42}'),
]


@pytest.mark.parametrize("label,body,expect", REPLIES, ids=[r[0] for r in REPLIES])
def test_the_reply_is_taken_from_the_respond_body_in_the_documented_order(stack, label, body, expect):
    flow = chat_flow(stack, "opaque", respond_body=body)
    chat = Chat.open(flow)
    got = chat.turn("q")
    assert got[-1]["role"] == "assistant"
    if expect.startswith("{"):
        assert json.loads(got[-1]["content"]) == json.loads(expect)
    else:
        assert got[-1]["content"] == expect


@pytest.mark.parametrize("key,expect", [("response", "from response"), ("text", "from text"), ("message", "from message"), ("output", "from output")])
def test_without_a_respond_node_the_last_nodes_output_is_the_reply(stack, key, expect):
    flow = chat_flow(stack, "no_respond", respond_body={"fields": [{"name": key, "value": expect}]})
    chat = Chat.open(flow)
    assert chat.turn("q")[-1]["content"] == expect


def test_a_reply_over_16kb_is_truncated(stack):
    flow = chat_flow(stack, "echo", respond_body={"answer": "{{ $json.text }}"})
    chat = Chat.open(flow)
    got = chat.turn("y" * 9000)                                                   # echoed twice into the JSON fallback
    assert got[-1]["role"] == "assistant" and 0 < len(got[-1]["content"].encode()) <= 16 * 1024


# ── input limits and dedupe ───────────────────────────────────────────────────


def test_message_limits_and_strict_bodies(stack):
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow)
    assert chat.send("x" * (16 * 1024 + 1)).status_code == 413
    assert chat.send("").status_code == 422
    assert chat.client.post(chat.path, {"content": "hi"}, headers=chat.auth).status_code == 422                       # client_message_id required
    assert chat.client.post(chat.path, {"content": "hi", "client_message_id": "c", "role": "assistant"}, headers=chat.auth).status_code == 422
    assert chat.client.post(chat.path, {"content": 5, "client_message_id": "c"}, headers=chat.auth).status_code == 422
    assert chat.client.post(chat.path, {"content": "hi", "client_message_id": "c" * 129}, headers=chat.auth).status_code == 422
    assert chat.messages()["messages"] == []                                       # none of that stored anything
    ok = chat.send("x" * (16 * 1024))
    assert ok.status_code == 202
    chat.wait_idle()


def test_concurrent_sends_one_turn_in_flight_the_rest_chat_busy_and_history_stays_ordered(stack):
    flow = chat_flow(stack, "approval")                                           # holds the turn open at an Approval
    chat = Chat.open(flow)
    res = concurrently(6, lambda i: chat.send(f"m{i}", f"cm-{i}"))
    codes = sorted(r.status_code for r in res)
    assert codes == [202] + [409] * 5, [r.text for r in res]
    assert all(r.json()["detail"]["code"] == "CHAT_BUSY" for r in res if r.status_code == 409)
    msgs = poll_until(lambda: chat.messages()["messages"], lambda m: len(m) >= 2, what="the waiting message")
    assert [m["seq"] for m in msgs] == [1, 2] and msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"
    # clean up: approve it
    ex = next(r.json()["execution_id"] for r in res if r.status_code == 202)
    decide(stack.api, the_task(flow, ex)["id"], "approved")
    chat.wait_idle()
    assert [m["seq"] for m in chat.messages()["messages"]] == [1, 2, 3]


def test_the_same_client_message_id_sent_concurrently_is_one_message_and_one_run(stack):
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow)
    res = concurrently(5, lambda i: chat.send("once", "same-id"))
    assert [r.status_code for r in res] == [202] * 5, [r.text for r in res]
    assert {r.json()["seq"] for r in res} == {1}
    page = chat.wait_idle()
    assert [(m["seq"], m["role"]) for m in page["messages"]] == [(1, "user"), (2, "assistant")]
    assert stack.q("SELECT count(*) FROM executions WHERE workflow_id = %s", (flow.wid,))[0][0] == 1
    late = chat.send("once", "same-id")
    assert late.status_code == 202 and late.json()["seq"] == 1 and late.json()["execution_id"].startswith("exec_")      # a late retry gets the run id


def test_a_duplicate_client_message_id_returns_the_original_202_body_even_while_the_run_is_starting(stack):
    """6.2: 'A duplicate client_message_id returns the original 202' (seq AND execution_id)."""
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow)
    res = concurrently(5, lambda i: chat.send("once", "same-id2"))
    bodies = {json.dumps(r.json(), sort_keys=True) for r in res}
    assert len(bodies) == 1 and all(r.json()["execution_id"] for r in res), bodies
    chat.wait_idle()


def test_messages_page_with_after_and_limit_without_gaps_or_duplicates(stack):
    flow = chat_flow(stack, "echo")
    chat = Chat.open(flow)
    for i in range(4):
        chat.turn(f"t{i}")
    allm = chat.messages(limit=100)["messages"]
    assert [m["seq"] for m in allm] == list(range(1, 9))
    seen, after = [], 0
    for _ in range(10):
        p = chat.messages(after=after, limit=3)
        seen += [m["seq"] for m in p["messages"]]
        if not p["messages"]:
            break
        assert p["next_after"] == p["messages"][-1]["seq"]
        after = p["next_after"]
    assert seen == list(range(1, 9))
    empty = chat.messages(after=8)
    assert empty["messages"] == [] and empty["next_after"] in (8, None)
    for bad in ({"limit": 0}, {"limit": 101}, {"after": -1}, {"limit": "x"}):
        assert chat.client.get(chat.path, params=bad, headers=chat.auth).status_code == 422, bad


# ── an approval inside a chat flow ────────────────────────────────────────────


def test_a_run_held_at_an_approval_says_so_stays_busy_and_replies_after_the_decision(stack):
    flow = chat_flow(stack, "approval")
    chat = Chat.open(flow)
    r = chat.send("refund order A-1")
    assert r.status_code == 202
    exec_id = r.json()["execution_id"]
    task = the_task(flow, exec_id)
    assert task["title"] == "Confirm: refund order A-1"
    page = poll_until(chat.messages, lambda p: len(p["messages"]) >= 2, what="the waiting message")
    assert (page["messages"][1]["role"], page["messages"][1]["seq"]) == ("assistant", 2)
    assert page["messages"][1]["content"].startswith("Waiting for Confirm: refund order A-1")
    assert page["busy"] is True                                                    # the turn is still in flight
    busy = chat.send("second question")
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "CHAT_BUSY"
    time.sleep(1.0)
    assert len(chat.messages()["messages"]) == 2                                   # one waiting message, not one per poll

    assert decide(stack.api, task["id"], "approved", by="Dana").json()["outcome"] == "decided"
    page = chat.wait_idle()
    assert [(m["seq"], m["role"]) for m in page["messages"]] == [(1, "user"), (2, "assistant"), (3, "assistant")]
    assert page["messages"][2]["content"] == "approved: refund order A-1"
    assert page["busy"] is False
    nxt = chat.send("thanks", "c-after")
    assert nxt.status_code == 202 and nxt.json()["seq"] == 4
    decide(stack.api, the_task(flow, nxt.json()["execution_id"])["id"], "rejected")
    chat.wait_idle()


def test_rejecting_and_timing_out_each_give_their_own_reply_and_free_the_session(stack):
    flow = chat_flow(stack, "approval")
    chat = Chat.open(flow)
    r1 = chat.send("one")
    decide(stack.api, the_task(flow, r1.json()["execution_id"])["id"], "rejected", by="Eli")
    chat.wait_idle()
    r2 = chat.send("two")
    t2 = the_task(flow, r2.json()["execution_id"])
    stack.tick(task_id=t2["id"], make_due=True)
    page = chat.wait_idle()
    texts = [m["content"] for m in page["messages"] if m["role"] == "assistant" and not m["content"].startswith("Waiting")]
    assert texts == ["rejected: one", "timed out: two"]
    assert page["busy"] is False


def test_cancelling_the_waiting_run_frees_the_chat_session(stack):
    """A chat must never be stuck CHAT_BUSY on a run that no longer exists."""
    flow = chat_flow(stack, "approval")
    chat = Chat.open(flow)
    r = chat.send("will be cancelled")
    the_task(flow, r.json()["execution_id"])
    assert stack.api.post(f"/executions/{r.json()['execution_id']}/cancel").status_code == 200
    again = poll_until(lambda: chat.send("hello again"), lambda x: x.status_code != 409, timeout=20, what="the session to be free after cancel")
    assert again.status_code == 202, again.text
    decide(stack.api, the_task(flow, again.json()["execution_id"])["id"], "approved")
    chat.wait_idle()


def test_a_chat_run_that_waited_survives_an_engine_crash_and_still_replies(fresh_stack):
    s = fresh_stack
    flow = chat_flow(s, "approval")
    chat = Chat.open(flow)
    r = chat.send("durable question")
    task = the_task(flow, r.json()["execution_id"])
    poll_until(chat.messages, lambda p: len(p["messages"]) >= 2, what="the waiting message")
    s.crash()
    s.restart()
    chat.client = StudioClient(s.engine.base_url)
    flow.stack = s
    page = chat.messages()
    assert page["busy"] is True and len(page["messages"]) == 2                     # the session is still mid-turn after the restart
    assert chat.send("pushy").status_code == 409
    assert decide(s.api, task["id"], "approved").json()["outcome"] == "decided"
    page = chat.wait_idle()
    assert [m["content"] for m in page["messages"] if m["role"] == "assistant"][-1] == "approved: durable question"
    assert page["busy"] is False and [m["seq"] for m in page["messages"]] == [1, 2, 3]


# ── session scoping ───────────────────────────────────────────────────────────


def test_session_tokens_are_scoped_to_their_session_and_interface(stack):
    a, b = chat_flow(stack, "echo"), chat_flow(stack, "echo")
    c1, c2, cb = Chat.open(a), Chat.open(a), Chat.open(b)
    c1.turn("private")
    c = c1.client
    for who, hdr in (("other session's token", c2.auth), ("other interface's token", cb.auth), ("no token", {}), ("garbage", {"Authorization": "Bearer nope"}),
                     ("not bearer", {"Authorization": c1.token})):
        r = c.get(c1.path, headers=hdr)
        assert r.status_code == 404, who
        assert "private" not in r.text
        w = c.post(c1.path, {"content": "intrude", "client_message_id": "x"}, headers=hdr)
        assert w.status_code == 404, who
    assert c.get(c1.path.replace(c1.sid, "chs_nope"), headers=c1.auth).status_code == 404
    assert c.get(f"{b.base}/chat/sessions/{c1.sid}/messages", headers=c1.auth).status_code == 404              # right token, wrong interface
    assert c.get(c1.path, params={"token": c1.token}).status_code == 404                                    # never accepted in the URL
    assert [m["content"] for m in c1.messages()["messages"]][0] == "private"
    assert c2.messages()["messages"] == []                                                                    # and nothing leaked sideways


def test_creating_a_session_validates_the_label_and_a_form_interface_has_no_chat(stack):
    flow = chat_flow(stack, "echo")
    c = StudioClient(stack.engine.base_url)
    assert c.post(f"{flow.base}/chat/sessions", {"user_label": "x" * 201}).status_code == 422
    assert c.post(f"{flow.base}/chat/sessions", {"user_label": "ok", "role": "admin"}).status_code == 422
    assert c.post(f"{flow.base}/chat/sessions", {}).status_code == 201
    r = c.post(f"{flow.base}/chat/sessions", {})
    assert r.status_code == 201 and r.json()["session_id"] != c.post(f"{flow.base}/chat/sessions", {}).json()["session_id"]
    assert r.headers["cache-control"] == "no-store"
    tokens = {Chat.open(flow).token for _ in range(3)}
    assert len(tokens) == 3
    # the token is stored hashed, never in clear
    assert stack.q("SELECT count(*) FROM chat_sessions WHERE token_hash = %s", (r.json()["session_token"],))[0][0] == 0
