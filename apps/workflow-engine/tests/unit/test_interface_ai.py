"""'Change the screen...': scripted model replies, one repair round-trip, 422 / 503 / 502 (DESIGN-STAGE5-UIS section 6.3)."""

from __future__ import annotations

import copy
import json

import pytest

from src.engine import llm_provider
from src.services import interface_ai
from src.services.interface_service import InterfaceError

from ._interface_fakes import fixture

pytestmark = pytest.mark.anyio

DEFINITION = fixture()
INTERFACE = DEFINITION["interface"]
SCREEN = "task:Manager review"
CURRENT = INTERFACE["screens"][SCREEN]


class Script:
    """Replaces ``llm_provider.call_llm`` / ``is_configured``: replies are consumed in order."""

    def __init__(self, replies, configured=True):
        self.replies, self.configured, self.calls = list(replies), configured, []

    async def call(self, model, messages, temperature=0.2, tools=None, **kw):
        self.calls.append({"model": model, "messages": copy.deepcopy(messages), **kw})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return type("R", (), {"text": reply if isinstance(reply, str) else json.dumps(reply), "output_truncated": False})()


@pytest.fixture
def script(monkeypatch):
    def install(replies, configured=True):
        s = Script(replies, configured)
        monkeypatch.setattr(llm_provider, "call_llm", s.call)
        monkeypatch.setattr(llm_provider, "is_configured", lambda model: s.configured)
        return s
    return install


def with_link(spec):
    out = copy.deepcopy(spec)
    out["components"].insert(-2, {"type": "link", "label": "Carrier tracking", "bind": "node.Classify.tracking_url"})
    return out


async def edit(instruction="add the carrier tracking link", spec=None):
    return await interface_ai.edit_screen(DEFINITION, INTERFACE, SCREEN, instruction, spec or CURRENT)


async def test_a_valid_reply_becomes_the_new_spec(script):
    s = script([{"spec": with_link(CURRENT), "summary": "Added a carrier tracking link."}])
    out = await edit()
    assert out["summary"] == "Added a carrier tracking link." and any(c["type"] == "link" for c in out["spec"]["components"])
    assert out["warnings"] == []
    first = s.calls[0]
    assert first["messages"][0]["role"] == "system" and first["messages"][0]["content"].split("\n")[0] == "INTERFACE_AI:EDIT_SCREEN"
    assert first["response_format"] == {"type": "json_object"} and first["model"] == interface_ai.INTERFACE_AI_MODEL
    user = json.loads(first["messages"][1]["content"])
    assert user["instruction"] == "add the carrier tracking link" and user["screen_id"] == SCREEN and user["current_spec"] == CURRENT
    assert set(user["available"]["input"]) == {"order_id", "amount", "reason", "email"} and "Classify" in user["available"]["node"]
    assert user["available"]["task_context_labels"] == ["Summary", "Suggest"] and "components" in json.dumps(user["spec_json_schema"])


async def test_a_bare_spec_reply_is_accepted(script):
    script([with_link(CURRENT)])
    out = await edit()
    assert out["summary"] == "Updated the screen." and len(out["spec"]["components"]) == len(CURRENT["components"]) + 1


async def test_fenced_json_is_accepted(script):
    script(["```json\n" + json.dumps({"spec": CURRENT, "summary": "same"}) + "\n```"])
    assert (await edit())["summary"] == "same"


async def test_an_invalid_first_reply_gets_one_repair_round_trip(script):
    bad = {"spec": {"v": 1, "components": [{"type": "iframe", "text": "x"}]}, "summary": "bad"}
    s = script([bad, {"spec": CURRENT, "summary": "fixed"}])
    out = await edit()
    assert out["summary"] == "fixed" and len(s.calls) == 2
    retry = s.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "user"]
    assert "components[0].type" in retry[3]["content"] and retry[0]["content"].startswith("INTERFACE_AI:EDIT_SCREEN")


async def test_two_invalid_replies_are_422_ai_spec_invalid_with_reasons(script):
    bad = {"spec": {"v": 1, "components": [{"type": "iframe"}]}}
    s = script([bad, bad])
    with pytest.raises(InterfaceError) as e:
        await edit()
    assert (e.value.status, e.value.code) == (422, "AI_SPEC_INVALID") and e.value.fields["reasons"] and len(s.calls) == 2


async def test_a_binding_to_a_node_that_does_not_exist_is_an_error_not_a_warning(script):
    ghost = copy.deepcopy(CURRENT)
    ghost["components"].append({"type": "field", "bind": "node.Ghost.x"})
    script([{"spec": ghost}, {"spec": CURRENT}])
    out = await edit()
    assert out["spec"] == CURRENT


async def test_unknown_input_fields_and_downstream_nodes_are_warnings(script):
    spec = copy.deepcopy(CURRENT)
    spec["components"] += [{"type": "field", "bind": "input.phone"}, {"type": "field", "bind": "node.Issue refund.body"}]
    script([{"spec": spec}])
    out = await edit()
    assert len(out["warnings"]) == 2 and out["spec"]["components"][-1]["bind"] == "node.Issue refund.body"


async def test_not_configured_is_503(script):
    s = script([], configured=False)
    with pytest.raises(InterfaceError) as e:
        await edit()
    assert (e.value.status, e.value.code) == (503, "LLM_NOT_CONFIGURED") and s.calls == []


@pytest.mark.parametrize("reply", ["not json at all", "", RuntimeError("boom")])
async def test_provider_failure_or_garbage_is_502(script, reply):
    script([reply])
    with pytest.raises(InterfaceError) as e:
        await edit()
    assert (e.value.status, e.value.code) == (502, "AI_FAILED")
    assert "boom" not in e.value.message


async def test_a_truncated_reply_is_502(monkeypatch):
    async def call(model, messages, temperature=0.2, tools=None, **kw):
        return type("R", (), {"text": "{}", "output_truncated": True})()

    monkeypatch.setattr(llm_provider, "call_llm", call)
    monkeypatch.setattr(llm_provider, "is_configured", lambda m: True)
    with pytest.raises(InterfaceError) as e:
        await edit()
    assert e.value.code == "AI_FAILED"


async def test_only_a_custom_unpromoted_interface_with_a_known_screen_can_be_edited(script):
    script([])
    for iface, screen, code in [({**INTERFACE, "kind": "form"}, SCREEN, "WRONG_KIND"), ({**INTERFACE, "app_id": "app_1"}, SCREEN, "PROMOTED"),
                                (INTERFACE, "admin", "NOT_FOUND")]:
        with pytest.raises(InterfaceError) as e:
            await interface_ai.edit_screen(DEFINITION, iface, screen, "x", CURRENT)
        assert e.value.code == code


async def test_the_edit_never_mutates_its_inputs(script):
    before = copy.deepcopy((DEFINITION, CURRENT))
    script([{"spec": with_link(CURRENT)}])
    await edit()
    assert (DEFINITION, CURRENT) == before


async def test_the_fake_llm_matches_by_the_first_system_line():
    from src.engine.fake_llm import FakeLLM

    assert interface_ai.SYSTEM_LINE == "INTERFACE_AI:EDIT_SCREEN" and interface_ai._RULES.startswith("INTERFACE_AI:EDIT_SCREEN\n")
    assert FakeLLM  # the scripted fake is matched on `system_matches` against this line in the integration tests
