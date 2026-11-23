"""Stage 6 / DESIGN-STAGE6-AI-BUILDER §6.8, §6.9, §11.5: ``engine/wf_ai.ask`` (the only path to the model), the recorder, and the cassette converter.

No real LLM: a fake ``ModelAdapter`` stands in, and for the adapter-level cases ``llm_provider.call_llm`` is patched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest

from src.engine import fake_llm, llm_provider, wf_ai
from src.engine.llm_provider import LLMResponse, LLMUsage
from src.engine.wf_ai import AIError, AIReply, Meter

MODEL = "gemini-3.5-flash"
SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
GOOD = json.dumps({"ok": True})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("WF_AI_") or k == "WORKFLOW_MODULE_GEN_MODEL":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm_provider, "is_configured", lambda model: True)
    wf_ai.clear_cache()
    yield
    wf_ai.clear_cache()


class FakeAdapter:
    name = "fake"

    def __init__(self, replies=None, *, usage=(10, 5), delay=0.0):
        self.replies = list(replies if replies is not None else [GOOD])
        self.usage = usage
        self.delay = delay
        self.calls: list[dict] = []

    async def ask_json(self, system, user, schema, *, max_tokens, timeout_s):
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens, "timeout_s": timeout_s})
        if self.delay:
            await asyncio.sleep(self.delay)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, AIReply):
            return r
        u = LLMUsage(input_tokens=self.usage[0], output_tokens=self.usage[1]) if self.usage else None
        return AIReply(text=r, usage=u, model=MODEL, provider="gemini", finish="stop")


def run(task, system_tail="role", user="{}", adapter=None, meter=None, **kw):
    system = wf_ai.SYSTEM_LINES[task] + "\n" + system_tail
    meter = meter or Meter()
    adapter = adapter or FakeAdapter()
    return asyncio.run(wf_ai.ask(task, system, user, SCHEMA, meter=meter, adapter=adapter, **kw)), meter, adapter


# ── system-line guard ───────────────────────────────────────────────────────────────────────


def test_system_lines_match_the_contract_table():
    assert wf_ai.SYSTEM_LINES == {
        "spec": "WF_AI:SPEC", "spec_repair": "WF_AI:SPEC_REPAIR", "build": "WF_AI:BUILD", "repair": "WF_AI:REPAIR", "node_fix": "WF_AI:NODE_FIX",
        "node_code": "WF_AI:NODE_CODE", "node_expr": "WF_AI:NODE_EXPR", "node_explain": "WF_AI:NODE_EXPLAIN", "chat_route": "WF_AI:CHAT_ROUTE",
        "json_fix": "WF_AI:JSON_FIX"}


def test_a_system_prompt_that_does_not_start_with_its_line_is_refused_before_any_call():
    ad = FakeAdapter()
    with pytest.raises(ValueError):
        asyncio.run(wf_ai.ask("build", "You are a helpful assistant", "{}", SCHEMA, meter=Meter(), adapter=ad))
    assert ad.calls == []


@pytest.mark.parametrize("system", ["WF_AI:SPEC_REPAIR\nrole", "WF_AI:SPECX\nrole", " WF_AI:SPEC\nrole", "x\nWF_AI:SPEC\nrole", "wf_ai:spec\nrole", "WF_AI:BUILD\nrole"])
def test_prefix_lookalikes_do_not_pass_the_spec_guard(system):
    ad = FakeAdapter()
    with pytest.raises(ValueError):
        asyncio.run(wf_ai.ask("spec", system, "{}", SCHEMA, meter=Meter(), adapter=ad))
    assert ad.calls == []


def test_unknown_task_is_refused():
    with pytest.raises(ValueError):
        asyncio.run(wf_ai.ask("nope", "WF_AI:NOPE\nx", "{}", SCHEMA, meter=Meter(), adapter=FakeAdapter()))


def test_a_caller_cannot_ask_json_fix_directly():
    with pytest.raises(ValueError):
        asyncio.run(wf_ai.ask("json_fix", "WF_AI:JSON_FIX\nx", "{}", SCHEMA, meter=Meter(), adapter=FakeAdapter()))


def test_system_line_alone_is_accepted():
    out, _, _ = run("build", system_tail="")
    # "WF_AI:BUILD\n" with empty tail still starts with the line
    assert out == {"ok": True}


# ── policy / configuration ──────────────────────────────────────────────────────────────────


def test_model_outside_the_allowlist_is_refused_without_a_call():
    ad = FakeAdapter()
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=Meter(), adapter=ad, model="gpt-4o"))
    assert e.value.code == "model_not_allowed" and ad.calls == []


def test_model_from_env_is_checked_too(monkeypatch):
    monkeypatch.setenv("WF_AI_MODEL", "gpt-4o")
    with pytest.raises(AIError) as e:
        run("build")
    assert e.value.code == "model_not_allowed"


def test_default_model_is_gemini_3_5_flash_and_env_overrides(monkeypatch):
    assert wf_ai.config().model == MODEL
    monkeypatch.setenv("WORKFLOW_MODULE_GEN_MODEL", "gemini-x")
    assert wf_ai.config().model == "gemini-x"
    monkeypatch.setenv("WF_AI_MODEL", "gemini-y")
    assert wf_ai.config().model == "gemini-y"


def test_not_configured_when_no_credentials(monkeypatch):
    monkeypatch.setattr(llm_provider, "is_configured", lambda model: False)
    with pytest.raises(AIError) as e:
        run("build")
    assert e.value.code == "not_configured"


def test_config_defaults_match_the_budget_table():
    c = wf_ai.config()
    assert (c.max_repair_rounds, c.max_calls, c.call_timeout_s, c.build_deadline_s, c.max_output_tokens, c.max_prompt_chars, c.max_ops, c.max_nodes) == \
        (3, 8, 60, 150, 16000, 160000, 60, 40)
    assert c.thinking_level == "low" and c.temperature == 0.2
    assert c.price_in_per_mtok is None and c.price_out_per_mtok is None


def test_config_reads_the_env_on_every_call(monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_CALLS", "3")
    assert wf_ai.config().max_calls == 3
    monkeypatch.setenv("WF_AI_MAX_CALLS", "not-a-number")
    assert wf_ai.config().max_calls == 8


# ── call budget ─────────────────────────────────────────────────────────────────────────────


def test_call_budget_is_enforced_and_the_extra_call_never_reaches_the_model():
    ad, m = FakeAdapter(), Meter(max_calls=2)
    for _ in range(2):
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=ad))
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=ad))
    assert e.value.code == "call_budget"
    assert len(ad.calls) == 2 and m.calls == 2


def test_budget_defaults_to_wf_ai_max_calls(monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_CALLS", "1")
    ad, m = FakeAdapter(), Meter()
    asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=ad))
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("repair", "WF_AI:REPAIR\nx", "{}", SCHEMA, meter=m, adapter=ad))
    assert e.value.code == "call_budget"


def test_json_fix_counts_against_the_budget():
    ad = FakeAdapter(["garbage", GOOD])
    m = Meter(max_calls=1)
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=ad))
    assert e.value.code == "call_budget"
    assert len(ad.calls) == 1


def test_budget_is_per_meter_not_global():
    for _ in range(3):
        run("build", meter=Meter(max_calls=1))


# ── prompt size ─────────────────────────────────────────────────────────────────────────────


def test_prompt_larger_than_the_limit_is_refused_without_a_call(monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_PROMPT_CHARS", "1000")
    ad = FakeAdapter()
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", json.dumps({"untrusted_request": "a" * 5000}), SCHEMA, meter=Meter(), adapter=ad))
    assert e.value.code == "prompt_too_large"
    assert ad.calls == []


def test_prompt_exactly_at_the_limit_is_allowed(monkeypatch):
    system, user = "WF_AI:BUILD\nx", "{}"
    monkeypatch.setenv("WF_AI_MAX_PROMPT_CHARS", str(len(system) + len(user)))
    ad = FakeAdapter()
    asyncio.run(wf_ai.ask("build", system, user, SCHEMA, meter=Meter(), adapter=ad))
    assert len(ad.calls) == 1
    monkeypatch.setenv("WF_AI_MAX_PROMPT_CHARS", str(len(system) + len(user) - 1))
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", system, user, SCHEMA, meter=Meter(), adapter=FakeAdapter()))
    assert e.value.code == "prompt_too_large"


def test_a_huge_prompt_with_multibyte_text_is_refused(monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_PROMPT_CHARS", "2000")
    with pytest.raises(AIError) as e:
        run("build", user=json.dumps({"untrusted_request": "中" * 5000}))
    assert e.value.code == "prompt_too_large"


def test_the_json_fix_prompt_is_also_size_limited(monkeypatch):
    # the fix prompt carries at most 4000 chars of the bad reply plus the schema; it must never exceed the cap silently
    monkeypatch.setenv("WF_AI_MAX_PROMPT_CHARS", "1500")
    ad = FakeAdapter(["x" * 20000, GOOD])
    with pytest.raises(AIError) as e:
        run("build", adapter=ad)
    assert e.value.code in ("prompt_too_large", "bad_json")
    for c in ad.calls:
        assert len(c["system"]) + len(c["user"]) <= 1500


# ── timeouts and deadline ───────────────────────────────────────────────────────────────────


def test_call_timeout_is_passed_to_the_adapter(monkeypatch):
    monkeypatch.setenv("WF_AI_CALL_TIMEOUT_S", "12")
    _, _, ad = run("build")
    assert ad.calls[0]["timeout_s"] == pytest.approx(12)


def test_default_call_timeout_is_60s():
    _, _, ad = run("build")
    assert ad.calls[0]["timeout_s"] == pytest.approx(60)


def test_max_output_tokens_default_and_override(monkeypatch):
    _, _, ad = run("build")
    assert ad.calls[0]["max_tokens"] == 16000
    _, _, ad = run("build", max_tokens=123)
    assert ad.calls[0]["max_tokens"] == 123


def test_adapter_timeout_becomes_an_ai_timeout_error_and_is_counted():
    ad = FakeAdapter([asyncio.TimeoutError()])
    m = Meter()
    with pytest.raises(AIError) as e:
        run("build", adapter=ad, meter=m)
    assert e.value.code == "timeout"
    assert m.calls == 1
    assert m.events and m.events[-1]["outcome"] == "timeout"


def test_real_adapter_enforces_the_per_call_timeout(monkeypatch):
    monkeypatch.setenv("WF_AI_CALL_TIMEOUT_S", "0.2")

    async def slow(*a, **k):
        await asyncio.sleep(5)
        return LLMResponse(text=GOOD)

    monkeypatch.setattr(llm_provider, "call_llm", slow)
    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=Meter()))
    assert e.value.code == "timeout" and time.monotonic() - t0 < 3


def test_ask_itself_enforces_the_timeout_even_for_an_adapter_that_ignores_it(monkeypatch):
    # §6.9 step 2: ask() "enforces ... per-call timeout" (asyncio.wait_for)
    monkeypatch.setenv("WF_AI_CALL_TIMEOUT_S", "0.2")
    ad = FakeAdapter(delay=2.0)

    async def go():
        return await asyncio.wait_for(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=Meter(), adapter=ad), 5)

    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        asyncio.run(go())
    assert e.value.code == "timeout"
    assert time.monotonic() - t0 < 1.5


def test_deadline_in_the_past_is_refused_without_a_call():
    ad = FakeAdapter()
    m = Meter(deadline_s=0.0)
    with pytest.raises(AIError) as e:
        run("build", adapter=ad, meter=m)
    assert e.value.code == "deadline" and ad.calls == []


def test_remaining_deadline_caps_the_call_timeout():
    m = Meter(deadline_s=5.0)
    _, _, ad = run("build", meter=m)
    assert ad.calls[0]["timeout_s"] <= 5.0


def test_deadline_is_checked_before_the_json_fix_call():
    ad = FakeAdapter(["garbage", GOOD])
    m = Meter(deadline_s=0.3)

    class Slow(FakeAdapter):
        async def ask_json(self, *a, **k):
            await asyncio.sleep(0.4)
            return await super().ask_json(*a, **k)

    ad = Slow(["garbage", GOOD])
    with pytest.raises(AIError) as e:
        run("build", adapter=ad, meter=m)
    assert e.value.code == "deadline"
    assert len(ad.calls) == 1


# ── provider failures ───────────────────────────────────────────────────────────────────────


def test_provider_error_is_wrapped_and_does_not_leak_its_text():
    ad = FakeAdapter([RuntimeError("401 key=sk-abcdefghijklmnopqrstuvwx rejected")])
    with pytest.raises(AIError) as e:
        run("build", adapter=ad)
    assert e.value.code == "provider_failed"
    assert "sk-abcdefgh" not in str(e.value) and "sk-abcdefgh" not in e.value.message


def test_failed_calls_are_still_counted_and_logged():
    m = Meter()
    with pytest.raises(AIError):
        run("build", adapter=FakeAdapter([RuntimeError("boom")]), meter=m)
    assert m.calls == 1 and m.events[-1]["outcome"] == "provider_failed"


def test_unknown_adapter_name_is_a_provider_error(monkeypatch):
    monkeypatch.setenv("WF_AI_ADAPTER", "nope")
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=Meter()))
    assert e.value.code == "provider_failed"


# ── JSON_FIX ────────────────────────────────────────────────────────────────────────────────


def test_bad_json_triggers_exactly_one_json_fix_call_then_succeeds():
    ad = FakeAdapter(["this is not json", GOOD])
    out, m, _ = run("build", adapter=ad)
    assert out == {"ok": True}
    assert len(ad.calls) == 2
    assert ad.calls[1]["system"].startswith("WF_AI:JSON_FIX")
    assert m.calls == 2


def test_json_fix_carries_the_error_the_head_and_the_schema():
    bad = "{" + "x" * 9000
    ad = FakeAdapter([bad, GOOD])
    run("build", adapter=ad)
    fix = json.loads(ad.calls[1]["user"])
    assert fix["error"]
    assert fix["reply_head"] == bad[:4000] and len(fix["reply_head"]) == 4000
    assert fix["schema"] == SCHEMA


def test_two_failures_raise_bad_json_after_exactly_two_calls():
    ad = FakeAdapter(["nope", "still nope"])
    m = Meter()
    with pytest.raises(AIError) as e:
        run("build", adapter=ad, meter=m)
    assert e.value.code == "bad_json"
    assert len(ad.calls) == 2 and m.calls == 2


def test_json_fix_is_never_chained():
    ad = FakeAdapter(["a", "b", "c", GOOD])
    with pytest.raises(AIError):
        run("build", adapter=ad)
    assert len(ad.calls) == 2


def test_missing_required_key_is_a_bad_reply():
    ad = FakeAdapter([json.dumps({"other": 1}), GOOD])
    out, _, _ = run("build", adapter=ad)
    assert out == {"ok": True} and len(ad.calls) == 2


def test_a_json_array_is_a_bad_reply():
    ad = FakeAdapter(["[1,2,3]", GOOD])
    out, _, _ = run("build", adapter=ad)
    assert out == {"ok": True} and len(ad.calls) == 2


def test_truncated_reply_triggers_json_fix_even_if_the_text_parses():
    trunc = AIReply(text=GOOD, usage=None, model=MODEL, provider="gemini", finish="length")
    ad = FakeAdapter([trunc, GOOD])
    out, _, _ = run("build", adapter=ad)
    assert len(ad.calls) == 2 and out == {"ok": True}


def test_fenced_json_is_accepted_without_a_fix():
    ad = FakeAdapter(["```json\n" + GOOD + "\n```"])
    out, _, _ = run("build", adapter=ad)
    assert out == {"ok": True} and len(ad.calls) == 1


def test_json_fix_events_are_logged_with_outcomes():
    m = Meter()
    run("build", adapter=FakeAdapter(["nope", GOOD]), meter=m)
    assert [e["system_line"] for e in m.events] == ["WF_AI:BUILD", "WF_AI:JSON_FIX"]
    assert m.events[0]["outcome"] == "bad_json" and m.events[1]["outcome"] == "ok"


# ── cache rules ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("task", ["spec", "node_explain"])
def test_spec_and_node_explain_are_cached(task):
    ad, m = FakeAdapter(), Meter()
    a, _, _ = run(task, user='{"q":1}', adapter=ad, meter=m)
    b, _, _ = run(task, user='{"q":1}', adapter=ad, meter=m)
    assert a == b
    assert len(ad.calls) == 1
    assert m.calls == 1 and m.cached_hits == 1


@pytest.mark.parametrize("task", ["build", "repair", "spec_repair", "node_fix", "node_code", "node_expr", "chat_route"])
def test_other_tasks_are_never_cached(task):
    ad, m = FakeAdapter(), Meter()
    run(task, user='{"q":1}', adapter=ad, meter=m)
    run(task, user='{"q":1}', adapter=ad, meter=m)
    assert len(ad.calls) == 2 and m.calls == 2 and m.cached_hits == 0


def test_cache_hit_is_logged_as_an_llm_call_event_with_cached_true_and_zero_tokens():
    ad, m = FakeAdapter(), Meter()
    run("spec", user="{}", adapter=ad, meter=m)
    run("spec", user="{}", adapter=ad, meter=m)
    assert [e["cached"] for e in m.events] == [False, True]
    assert m.events[1]["input_tokens"] == 0 and m.events[1]["output_tokens"] == 0
    assert m.usage()["llm_calls"] == 1


def test_cache_key_covers_model_system_and_user(monkeypatch):
    ad = FakeAdapter()
    run("spec", system_tail="a", user="{}", adapter=ad)
    run("spec", system_tail="b", user="{}", adapter=ad)
    run("spec", system_tail="a", user='{"x":1}', adapter=ad)
    assert len(ad.calls) == 3
    monkeypatch.setenv("WF_AI_ALLOWED_MODELS", f"{MODEL},gemini-other")
    asyncio.run(wf_ai.ask("spec", "WF_AI:SPEC\na", "{}", SCHEMA, meter=Meter(), adapter=ad, model="gemini-other"))
    assert len(ad.calls) == 4


def test_cached_result_is_a_copy():
    ad = FakeAdapter()
    a, _, _ = run("spec", adapter=ad)
    a["ok"] = "mutated"
    b, _, _ = run("spec", adapter=ad)
    assert b == {"ok": True}


def test_failed_calls_are_not_cached():
    ad = FakeAdapter(["nope", "nope", GOOD])
    with pytest.raises(AIError):
        run("spec", adapter=ad)
    out, _, _ = run("spec", adapter=ad)
    assert out == {"ok": True}


def test_cache_is_an_lru_of_256_entries():
    ad = FakeAdapter()
    for i in range(257):
        run("spec", user=json.dumps({"i": i}), adapter=ad)
    n = len(ad.calls)
    run("spec", user=json.dumps({"i": 256}), adapter=ad)
    assert len(ad.calls) == n  # newest is still cached
    run("spec", user=json.dumps({"i": 0}), adapter=ad)
    assert len(ad.calls) == n + 1  # oldest was evicted


def test_cache_hit_is_served_even_after_the_call_budget_is_used_up():
    ad = FakeAdapter()
    m = Meter(max_calls=1)
    run("spec", adapter=ad, meter=m)
    out, _, _ = run("spec", adapter=ad, meter=m)
    assert out == {"ok": True} and m.calls == 1


def test_cache_hit_does_not_skip_the_model_policy(monkeypatch):
    ad = FakeAdapter()
    run("spec", adapter=ad)
    monkeypatch.setenv("WF_AI_ALLOWED_MODELS", "something-else")
    with pytest.raises(AIError) as e:
        run("spec", adapter=ad)
    assert e.value.code == "model_not_allowed"


# ── metering ────────────────────────────────────────────────────────────────────────────────


def test_usage_is_added_to_the_meter_and_the_event():
    _, m, _ = run("build", adapter=FakeAdapter(usage=(120, 30)))
    assert (m.input_tokens, m.output_tokens, m.calls) == (120, 30, 1)
    ev = m.events[0]
    assert ev["input_tokens"] == 120 and ev["output_tokens"] == 30 and ev["task"] == "build" and ev["model"] == MODEL


def test_missing_usage_adds_zero_tokens_but_still_counts_the_call():
    _, m, _ = run("build", adapter=FakeAdapter(usage=None))
    assert (m.input_tokens, m.output_tokens) == (0, 0)
    assert m.calls == 1 and len(m.events) == 1


def test_usage_accumulates_over_calls_including_json_fix():
    _, m, _ = run("build", adapter=FakeAdapter(["nope", GOOD], usage=(100, 10)))
    assert (m.input_tokens, m.output_tokens, m.calls) == (200, 20, 2)


def test_usage_dict_shape_and_null_cost_without_prices():
    _, m, _ = run("build")
    u = m.usage()
    assert set(u) >= {"llm_calls", "input_tokens", "output_tokens", "cost_usd", "duration_ms"}
    assert u["cost_usd"] is None


def test_cost_needs_both_prices(monkeypatch):
    _, m, _ = run("build", adapter=FakeAdapter(usage=(1_000_000, 500_000)))
    monkeypatch.setenv("WF_AI_PRICE_IN_PER_MTOK", "2")
    assert m.cost_usd() is None
    monkeypatch.setenv("WF_AI_PRICE_OUT_PER_MTOK", "10")
    assert m.cost_usd() == pytest.approx(2 * 1 + 10 * 0.5)


def test_event_hash_is_sha256_of_model_system_user_and_no_prompt_text_by_default():
    system, user = "WF_AI:BUILD\nrole text", '{"untrusted_request":"hello"}'
    m = Meter()
    asyncio.run(wf_ai.ask("build", system, user, SCHEMA, meter=m, adapter=FakeAdapter()))
    ev = m.events[0]
    assert ev["prompt_sha256"] == hashlib.sha256(f"{MODEL}\n{system}\n{user}".encode()).hexdigest()
    assert wf_ai.prompt_sha256(MODEL, system, user) == ev["prompt_sha256"]
    assert ev["system_line"] == "WF_AI:BUILD"
    blob = json.dumps(ev)
    assert "hello" not in blob and "role text" not in blob


def test_prompts_are_stored_in_events_only_when_asked(monkeypatch):
    monkeypatch.setenv("WF_AI_STORE_PROMPTS", "1")
    m = Meter()
    asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", '{"a":1}', SCHEMA, meter=m, adapter=FakeAdapter()))
    assert "WF_AI:BUILD" in json.dumps(m.events[0])


def test_event_listener_receives_events_and_a_broken_listener_does_not_break_the_call():
    seen = []
    out, _, _ = run("build", meter=Meter(on_event=seen.append))
    assert len(seen) == 1 and out == {"ok": True}

    def boom(_):
        raise RuntimeError("listener bug")

    out, _, _ = run("build", meter=Meter(on_event=boom))
    assert out == {"ok": True}


# ── real adapters through llm_provider.call_llm ─────────────────────────────────────────────


def test_json_schema_adapter_requests_constrained_json_and_reports_usage(monkeypatch):
    seen = {}

    async def fake_call(model, messages, **kw):
        seen.update(model=model, messages=messages, kw=kw)
        return LLMResponse(text=GOOD, usage=LLMUsage(input_tokens=7, output_tokens=3))

    monkeypatch.setattr(llm_provider, "call_llm", fake_call)
    m = Meter()
    out = asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", '{"a":1}', SCHEMA, meter=m))
    assert out == {"ok": True} and (m.input_tokens, m.output_tokens) == (7, 3)
    assert seen["model"] == MODEL
    assert seen["messages"][0] == {"role": "system", "content": "WF_AI:BUILD\nr"} and seen["messages"][1]["role"] == "user"
    assert seen["kw"]["response_format"] == {"type": "json_object", "schema": SCHEMA}
    assert seen["kw"]["temperature"] == 0.2 and seen["kw"]["max_tokens"] == 16000


def test_function_call_adapter_reads_the_forced_tool(monkeypatch):
    from src.engine.llm_provider import ToolCall

    monkeypatch.setenv("WF_AI_ADAPTER", "function_call")

    async def fake_call(model, messages, **kw):
        assert kw["tool_choice"] == "required" and kw["tools"][0]["name"] == "emit_result"
        return LLMResponse(text=None, tool_calls=[ToolCall(id="1", name="emit_result", args={"ok": True})])

    monkeypatch.setattr(llm_provider, "call_llm", fake_call)
    assert asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", "{}", SCHEMA, meter=Meter())) == {"ok": True}


def test_fake_llm_script_drives_ask_end_to_end(monkeypatch):
    fake = fake_llm.FakeLLM(fake_llm.parse_script({"rules": [
        {"id": "b", "when": {"system_matches": "^WF_AI:BUILD"}, "respond": {"text": GOOD}}], "default": {"raise": "unmatched"}}))

    async def via_fake(model, messages, **kw):
        return await fake.call(model, messages, **{k: v for k, v in kw.items() if k in ("temperature", "tools")})

    monkeypatch.setattr(llm_provider, "call_llm", via_fake)
    m = Meter()
    out = asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", "{}", SCHEMA, meter=m))
    assert out == {"ok": True} and m.calls == 1 and m.input_tokens == 0  # the fake sends no usage
    assert len(fake.calls) == 1


# ── recorder (§11.5) ────────────────────────────────────────────────────────────────────────


def _record(tmp_path, monkeypatch, replies, *, secrets=None, case="refund_over_500", user='{"untrusted_request":"my private request"}', task="build"):
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(tmp_path))
    m = Meter(case=case, secret_values=set(secrets or ()))
    return asyncio.run(wf_ai.ask(task, wf_ai.SYSTEM_LINES[task] + "\nrole", user, SCHEMA, meter=m, adapter=FakeAdapter(replies))), m


def test_recorder_writes_the_documented_cassette_shape(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, [GOOD], task="spec")
    doc = json.loads((tmp_path / "refund_over_500.json").read_text())
    assert doc["case"] == "refund_over_500" and doc["model"] == MODEL and doc["prompts_version"]
    assert doc["calls"] == [{"n": 0, "system_line": "WF_AI:SPEC", "prompt_sha256": doc["calls"][0]["prompt_sha256"], "reply_text": GOOD,
                             "usage": {"in": 10, "out": 5}}]
    assert len(doc["calls"][0]["prompt_sha256"]) == 64


def test_recorder_never_stores_prompts(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, [GOOD], user='{"untrusted_request":"my private request"}')
    text = (tmp_path / "refund_over_500.json").read_text()
    assert "my private request" not in text and "role" not in text.replace("reply_text", "")


def test_recorder_appends_calls_in_order_and_records_json_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(tmp_path))
    m = Meter(case="c1")
    asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=FakeAdapter(["nope", GOOD])))
    asyncio.run(wf_ai.ask("repair", "WF_AI:REPAIR\nx", "{}", SCHEMA, meter=m, adapter=FakeAdapter([GOOD])))
    calls = json.loads((tmp_path / "c1.json").read_text())["calls"]
    assert [(c["n"], c["system_line"]) for c in calls] == [(0, "WF_AI:BUILD"), (1, "WF_AI:JSON_FIX"), (2, "WF_AI:REPAIR")]


def test_recorder_is_off_without_the_env(tmp_path):
    run("build", meter=Meter(case="zzz"))
    assert list(tmp_path.iterdir()) == []


def test_recorder_case_name_cannot_escape_the_directory(tmp_path, monkeypatch):
    sub = tmp_path / "rec"
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(sub))
    m = Meter(case="../../evil/..\\x")
    asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=FakeAdapter()))
    files = [p for p in tmp_path.rglob("*.json")]
    assert files and all(sub in p.parents for p in files)


@pytest.mark.parametrize("label,reply", [
    ("card", json.dumps({"ok": True, "note": "card 4111111111111111"})),
    ("iban", json.dumps({"ok": True, "note": "GB82WEST12345698765432"})),
    ("ssn", json.dumps({"ok": True, "note": "ssn 123-45-6789"})),
    ("api key", json.dumps({"ok": True, "note": "AIzaSyA1234567890abcdefghijklmnopqrstuv"})),
    ("pem", json.dumps({"ok": True, "note": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"})),
    ("canary", json.dumps({"ok": True, "note": "CANARY-0badc0de-1234"})),
    ("password pair", json.dumps({"ok": True, "note": "password=Hunter2Hunter2"})),
])
def test_recorder_aborts_when_a_reply_holds_a_secret_or_high_confidence_pii(tmp_path, monkeypatch, label, reply):
    with pytest.raises(AIError) as e:
        _record(tmp_path, monkeypatch, [reply])
    assert e.value.code == "record_aborted"
    assert not (tmp_path / "refund_over_500.json").exists(), "nothing may be written on abort"
    for needle in ("4111111111111111", "GB82WEST", "123-45-6789", "AIzaSyA", "BEGIN PRIVATE", "CANARY-0badc0de", "Hunter2Hunter2"):
        assert needle not in str(e.value)


def test_recorder_aborts_on_a_known_project_secret(tmp_path, monkeypatch):
    with pytest.raises(AIError) as e:
        _record(tmp_path, monkeypatch, [json.dumps({"ok": True, "x": "use zk_live_9f8e7d6c5b"})], secrets={"zk_live_9f8e7d6c5b"})
    assert e.value.code == "record_aborted" and "zk_live_9f8e7d6c5b" not in str(e.value)
    assert not list(tmp_path.iterdir())


def test_recorder_abort_leaves_an_existing_cassette_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(tmp_path))
    m = Meter(case="c")
    asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=m, adapter=FakeAdapter()))
    before = (tmp_path / "c.json").read_text()
    with pytest.raises(AIError):
        asyncio.run(wf_ai.ask("repair", "WF_AI:REPAIR\nx", "{}", SCHEMA, meter=m, adapter=FakeAdapter([json.dumps({"ok": 1, "n": "4111111111111111"})])))
    assert (tmp_path / "c.json").read_text() == before


def test_recorder_does_not_abort_on_email_phone_or_amounts(tmp_path, monkeypatch):
    _record(tmp_path, monkeypatch, [json.dumps({"ok": True, "note": "mail ana@example.com or 415-555-2671, refund $1,234.56 over 10,000"})])
    assert (tmp_path / "refund_over_500.json").exists()


def test_a_bad_json_reply_with_a_card_aborts_before_the_json_fix_call(tmp_path, monkeypatch):
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(tmp_path))
    ad = FakeAdapter(["not json 4111111111111111", GOOD])
    with pytest.raises(AIError) as e:
        asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nx", "{}", SCHEMA, meter=Meter(case="c"), adapter=ad))
    assert e.value.code == "record_aborted"
    assert len(ad.calls) == 1, "the unsafe reply must not be echoed into a JSON_FIX prompt"
    assert not list(tmp_path.iterdir())


# ── cassette converter (tests/support/ai_cassette.py) ───────────────────────────────────────

from tests.support import ai_cassette as cas  # noqa: E402


def _cassette(*pairs, case="c"):
    return {"case": case, "model": MODEL, "prompts_version": "x",
            "calls": [{"n": i, "system_line": line, "prompt_sha256": "0" * 64, "reply_text": text, "usage": {"in": 1, "out": 1}} for i, (line, text) in enumerate(pairs)]}


def _replay(script, line, n=1):
    fake = fake_llm.FakeLLM(fake_llm.parse_script(script))
    out = []
    for _ in range(n):
        msgs = [{"role": "system", "content": line + "\nrole"}, {"role": "user", "content": "{}"}]
        out.append(asyncio.run(fake.call(MODEL, msgs)).text)
    return out


def test_to_fake_script_one_rule_per_system_line_and_nth():
    c = _cassette(("WF_AI:SPEC", "s0"), ("WF_AI:BUILD", "b0"), ("WF_AI:BUILD", "b1"), ("WF_AI:REPAIR", "r0"))
    script = cas.to_fake_script(c)
    assert len(script["rules"]) == 4
    assert script["default"] == {"raise": "unmatched"}
    assert cas.expected_calls(c) == {"WF_AI:SPEC": 1, "WF_AI:BUILD": 2, "WF_AI:REPAIR": 1}


def test_replay_returns_each_recorded_reply_in_order_per_system_line():
    c = _cassette(("WF_AI:BUILD", "b0"), ("WF_AI:SPEC", "s0"), ("WF_AI:BUILD", "b1"))
    fake = fake_llm.FakeLLM(fake_llm.parse_script(cas.to_fake_script(c)))

    def call(line):
        return asyncio.run(fake.call(MODEL, [{"role": "system", "content": line + "\nrole"}, {"role": "user", "content": "{}"}])).text

    assert call("WF_AI:BUILD") == "b0"
    assert call("WF_AI:SPEC") == "s0"
    assert call("WF_AI:BUILD") == "b1"
    with pytest.raises(fake_llm.FakeLLMUnmatched):
        call("WF_AI:BUILD")  # an unrecorded extra call fails loudly


def test_spec_rule_does_not_swallow_spec_repair_calls():
    c = _cassette(("WF_AI:SPEC", "s0"), ("WF_AI:SPEC_REPAIR", "sr0"))
    fake = fake_llm.FakeLLM(fake_llm.parse_script(cas.to_fake_script(c)))
    msgs = lambda line: [{"role": "system", "content": line + "\nx"}, {"role": "user", "content": "{}"}]  # noqa: E731
    assert asyncio.run(fake.call(MODEL, msgs("WF_AI:SPEC_REPAIR"))).text == "sr0"
    assert asyncio.run(fake.call(MODEL, msgs("WF_AI:SPEC"))).text == "s0"


@pytest.mark.parametrize("text", ["reply with ${SECRET_VAR}", "@file:/etc/passwd"])
def test_replies_that_fake_llm_would_interpolate_are_refused(text):
    with pytest.raises(cas.CassetteError):
        cas.to_fake_script(_cassette(("WF_AI:BUILD", text)))


@pytest.mark.parametrize("doc", [{}, {"case": "x"}, {"case": "x", "calls": [{"system_line": "WF_AI:NOPE", "reply_text": "{}"}]},
                                 {"case": "x", "calls": [{"system_line": "WF_AI:BUILD", "reply_text": 3}]}, [], None])
def test_malformed_cassettes_are_rejected(doc):
    with pytest.raises(cas.CassetteError):
        cas.validate_cassette(doc)


def test_scan_cassette_flags_secrets_and_pii_in_replies():
    bad = _cassette(("WF_AI:BUILD", "ok"), ("WF_AI:BUILD", "card 4111111111111111"), ("WF_AI:BUILD", "has hunter2-secret inside"))
    problems = cas.scan_cassette(bad, {"hunter2-secret"})
    assert len(problems) == 2 and "call 1" in problems[0] and "call 2" in problems[1]
    assert not any("4111111111111111" in p or "hunter2-secret" in p for p in problems)
    assert cas.scan_cassette(_cassette(("WF_AI:BUILD", "{}")), set()) == []


def test_load_save_roundtrip_adopt_and_verdict(tmp_path):
    c = _cassette(("WF_AI:BUILD", "{}"), case="pid-123")
    cas.save_cassette(tmp_path / "pid-123.json", c)
    out = cas.adopt_recording(tmp_path, "pid-123", "refund_over_500", {"passed": True})
    assert out.name == "refund_over_500.json" and not (tmp_path / "pid-123.json").exists()
    doc = cas.load_cassette(out)
    assert doc["case"] == "refund_over_500" and doc["verdict"] == {"passed": True}
    cas.write_verdict(out, {"passed": False})
    assert cas.load_cassette(out)["verdict"] == {"passed": False}


def test_load_cassette_of_a_missing_or_corrupt_file_is_a_cassette_error(tmp_path):
    with pytest.raises(cas.CassetteError):
        cas.load_cassette(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(cas.CassetteError):
        cas.load_cassette(tmp_path / "bad.json")


def test_count_by_system_line_reads_the_first_line_of_the_system_message():
    calls = [{"messages": [{"role": "system", "content": "WF_AI:BUILD\nrole"}]}, {"messages": [{"role": "system", "content": "WF_AI:BUILD\nx"}]},
             {"messages": [{"role": "system", "content": "WF_AI:JSON_FIX\nx"}]}, {"messages": []}]
    assert cas.count_by_system_line(calls) == {"WF_AI:BUILD": 2, "WF_AI:JSON_FIX": 1, "": 1}


def test_prompts_stale_compares_with_the_current_prompts_version():
    from src.services.ai_builder_prompts import prompts_version

    assert cas.prompts_stale({"prompts_version": prompts_version()}) is False
    assert cas.prompts_stale({"prompts_version": "deadbeef"}) is True


def test_a_recorded_cassette_replays_through_ask(tmp_path, monkeypatch):
    """Record with the recorder, convert, replay through the fake LLM: the same replies come back."""
    monkeypatch.setenv("WF_AI_RECORD_DIR", str(tmp_path))
    m = Meter(case="rt")
    first = asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", "{}", SCHEMA, meter=m, adapter=FakeAdapter([json.dumps({"ok": 1})])))
    doc = cas.load_cassette(tmp_path / "rt.json")
    fake = fake_llm.FakeLLM(fake_llm.parse_script(cas.to_fake_script(doc)))

    async def via_fake(model, messages, **kw):
        return await fake.call(model, messages)

    monkeypatch.delenv("WF_AI_RECORD_DIR")
    monkeypatch.setattr(llm_provider, "call_llm", via_fake)
    again = asyncio.run(wf_ai.ask("build", "WF_AI:BUILD\nr", "{}", SCHEMA, meter=Meter()))
    assert again == first


# ── adversarial input ───────────────────────────────────────────────────────────────────────


def test_a_lone_surrogate_in_the_prompt_is_an_ai_error_or_handled_never_a_crash():
    """A JSON body may carry "\\ud800"; the hash/encode step must not raise UnicodeEncodeError (that would be a 500)."""
    try:
        run("build", user=json.dumps({"untrusted_request": "bad \ud800 text"}, ensure_ascii=False))
    except AIError:
        pass
