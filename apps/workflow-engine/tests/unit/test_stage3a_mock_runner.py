"""Runner test mode: mock priority, items/error/timeout/http/rules mocks, expressions, unmocked outside steps,
retries and on_error under mocks, frozen clock + seeded RNG, no sleeping, secrets, request capture."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from src.engine.expression_engine import expression_engine
from src.engine.test_mode import TestRunOptions, current_rng, current_time, parse_now, plan_mock
from src.engine.types import ExecutionContext, NodeData, NodeExecutionResult, Workflow
from src.engine.workflow_runner import WorkflowRunner
from src.nodes.flow.wait import WaitNode

from ._runner_fakes import PassNode, items, make_runner, node, wf

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class CountingFail:
    input_count = 1

    def __init__(self, log):
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append(node_def.name)
        raise RuntimeError("real boom")


def runner_with_outside_steps():
    """The fake runner, with real outside-step type names ("Postgres" -> database, "HttpRequest" -> network) backed by PassNode."""
    runner, log = make_runner()
    runner._registry._nodes.update({"Postgres": PassNode(log), "SendEmail": CountingFail(log)})
    return runner, log


def opts(mocks=None, defaults=None, **kw):
    return TestRunOptions(mocks=mocks or {}, default_mocks=defaults or {}, now=NOW, seed=1, persist=False, **kw)


def chain(*mid_nodes):
    names = [n.name for n in mid_nodes]
    return wf([node("a"), *mid_nodes, node("z")], list(zip(["a", *names], [*names, "z"])))


# ── effects and the unmocked failure ─────────────────────────────────────────

async def test_effect_none_nodes_run_for_real_in_test_mode():
    runner, log = make_runner()
    ctx = await runner.run(chain(node("m")), "a", items({"k": 1}), test_options=opts())
    assert log == ["a", "m", "z"] and not ctx.errors


async def test_an_unmocked_outside_step_fails_with_code_details_and_stops():
    runner, log = runner_with_outside_steps()
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}), test_options=opts())
    assert log == ["a"]  # never executed, nothing downstream
    [err] = ctx.errors
    assert err.error_code == "TEST_UNMOCKED_EFFECT" and err.node_name == "db"
    assert err.details == {"node": "db", "effect": "database", "fixes": ["generate_from_spec", "use_last_real_reply"]}


async def test_unmocked_failure_bypasses_retries_and_on_error():
    runner, log = runner_with_outside_steps()
    w = chain(node("mail", "SendEmail", retry_on_fail=3, retry_delay=0, on_error="continue"))
    ctx = await runner.run(w, "a", items({"k": 1}), test_options=opts())
    assert log == ["a"] and ctx.errors[0].error_code == "TEST_UNMOCKED_EFFECT"
    assert "z" not in ctx.node_states  # on_error=continue did not keep the run going
    w2 = chain(node("mail", "SendEmail", on_error="error_output"))
    ctx = await runner.run(w2, "a", items({"k": 1}), test_options=opts())
    assert ctx.errors and "mail" not in ctx.test_outputs


async def test_pinned_data_satisfies_an_outside_step_and_a_disabled_one_passes_through():
    runner, log = runner_with_outside_steps()
    pinned = node("db", "Postgres", pinned_data=items({"row": 1}))
    ctx = await runner.run(chain(pinned), "a", items({"k": 1}), test_options=opts())
    assert not ctx.errors and ctx.node_states["db"][0].json == {"row": 1} and log == ["a", "z"]
    ctx = await runner.run(chain(node("db", "Postgres", disabled=True)), "a", items({"k": 1}), test_options=opts())
    assert not ctx.errors and ctx.node_states["db"][0].json == {"k": 1, "via": "a"}


async def test_outside_effect_runs_normally_outside_test_mode():
    runner, log = runner_with_outside_steps()
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}))
    assert not ctx.errors and log == ["a", "db", "z"]


# ── mock priority ────────────────────────────────────────────────────────────

async def test_priority_test_mock_beats_default_beats_pinned_beats_real():
    runner, log = runner_with_outside_steps()
    pinned = node("db", "Postgres", pinned_data=items({"src": "pinned"}))
    w = chain(pinned)
    test_mock = {"db": {"items": [{"src": "test"}]}}
    default_mock = {"db": {"items": [{"src": "default"}]}}
    ctx = await runner.run(w, "a", items({}), test_options=opts(test_mock, default_mock))
    assert ctx.node_states["db"][0].json == {"src": "test"}
    ctx = await runner.run(w, "a", items({}), test_options=opts({}, default_mock))
    assert ctx.node_states["db"][0].json == {"src": "default"}
    ctx = await runner.run(w, "a", items({}), test_options=opts())
    assert ctx.node_states["db"][0].json == {"src": "pinned"}
    plain = chain(node("db", "Postgres"))
    ctx = await runner.run(plain, "a", items({}), test_options=opts({}, default_mock))
    assert ctx.node_states["db"][0].json == {"src": "default"}
    assert log == ["a", "z", "a", "z", "a", "z", "a", "z"]  # the Postgres node itself never ran


async def test_a_mock_also_replaces_a_node_that_would_run_for_real():
    runner, log = make_runner()
    ctx = await runner.run(chain(node("m")), "a", items({"k": 1}), test_options=opts({"m": {"items": [{"fake": True}]}}))
    assert log == ["a", "z"] and ctx.node_states["m"][0].json == {"fake": True} and ctx.node_states["z"][0].json["fake"] is True


async def test_rules_mock_without_a_match_falls_through_to_the_next_level():
    runner, _ = runner_with_outside_steps()
    rules = {"rules": [{"when": {"path": "k", "op": "eq", "value": 99}, "then": {"items": [{"hit": 1}]}}]}
    default = {"db": {"items": [{"src": "default"}]}}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}), test_options=opts({"db": rules}, default))
    assert ctx.node_states["db"][0].json == {"src": "default"}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}), test_options=opts({"db": rules}))
    assert ctx.errors[0].error_code == "TEST_UNMOCKED_EFFECT"  # no match and no default: unmocked
    with_default = {**rules, "default": {"items": [{"hit": 0}]}}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}), test_options=opts({"db": with_default}))
    assert ctx.node_states["db"][0].json == {"hit": 0}


# ── smart mocks and expressions ──────────────────────────────────────────────

async def test_rules_pick_per_input_item_first_match_wins():
    runner, _ = runner_with_outside_steps()
    mock = {"rules": [
        {"when": {"path": "amount", "op": "gt", "value": 1000}, "then": {"items": [{"decision": "deny"}]}},
        {"when": {"path": "amount", "op": "gt", "value": 100}, "then": {"items": [{"decision": "review"}]}},
    ], "default": {"items": [{"decision": "approve"}]}}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"amount": 5000}, {"amount": 500}, {"amount": 5}),
                           test_options=opts({"db": mock}))
    assert [i.json["decision"] for i in ctx.node_states["db"]] == ["deny", "review", "approve"]


async def test_expressions_in_items_resolve_per_input_item():
    runner, _ = runner_with_outside_steps()
    mock = {"items": [{"order": "{{ $json.order_id }}", "total": "{{ $json.amount * 2 }}", "static": 1}]}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"order_id": "o1", "amount": 3}, {"order_id": "o2", "amount": 4}),
                           test_options=opts({"db": mock}))
    assert [(i.json["order"], i.json["total"]) for i in ctx.node_states["db"]] == [("o1", 6), ("o2", 8)]


async def test_items_without_expressions_are_emitted_once_not_per_input_item():
    runner, _ = runner_with_outside_steps()
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"x": 1}, {"x": 2}, {"x": 3}),
                           test_options=opts({"db": {"items": [{"fixed": 1}]}}))
    assert len(ctx.node_states["db"]) == 1


async def test_mock_port_routes_to_the_named_output():
    runner, log = runner_with_outside_steps()
    w = wf([node("a"), node("db", "Postgres"), node("t"), node("f")], [("a", "db"), ("db", "t", "true"), ("db", "f", "false")])
    ctx = await runner.run(w, "a", items({}), test_options=opts({"db": {"items": [{"v": 1}], "port": "true"}}))
    assert "t" in ctx.node_states and "f" not in ctx.node_states
    assert ctx.test_outputs["db"] == {"true": [NodeData(json={"v": 1})]}


async def test_plan_mock_resolves_expressions_in_error_messages():
    ctx = ExecutionContext(workflow=Workflow(name="t", nodes=[], connections=[]), execution_id="e", start_time=NOW, mode="manual")
    plan = plan_mock({"error": {"message": "no order {{ $json.id }}", "code": "E1"}}, "db", "Postgres", items({"id": 7}), ctx)
    assert plan.kind == "raise" and str(plan.error) == "no order 7" and plan.error.code == "E1"
    timeout = plan_mock({"timeout": True}, "db", "Postgres", items({}), ctx)
    assert isinstance(timeout.error, TimeoutError) and str(timeout.error) == "TEST_MOCK_TIMEOUT: db"
    assert plan_mock({"http": {"status": 200}}, "db", "Postgres", items({}), ctx).error.code == "TEST_BAD_MOCK"
    assert plan_mock({"nonsense": 1}, "db", "Postgres", items({}), ctx) is None


# ── error and timeout mocks run inside the retry loop ────────────────────────

async def test_error_mock_is_retried_like_a_real_failure():
    runner, log = runner_with_outside_steps()
    w = chain(node("db", "Postgres", retry_on_fail=2, retry_delay=0))
    ctx = await runner.run(w, "a", items({}), test_options=opts({"db": {"error": {"message": "db down"}}}))
    assert ctx.node_metrics["db"]["retries"] == 2 and ctx.node_metrics["db"]["maxRetries"] == 2
    assert "after 3 attempts" in ctx.errors[0].error and "z" not in ctx.node_states


async def test_error_mock_respects_on_error_modes():
    runner, _ = runner_with_outside_steps()
    mock = {"db": {"error": {"message": "db down", "code": "E_DB"}}}
    ctx = await runner.run(chain(node("db", "Postgres")), "a", items({"k": 1}), test_options=opts(mock))
    assert ctx.errors[0].error_code == "E_DB" and "z" not in ctx.node_states
    ctx = await runner.run(chain(node("db", "Postgres", on_error="continue")), "a", items({"k": 1}), test_options=opts(mock))
    assert ctx.node_states["db"][0].json["error"] == "db down" and "z" in ctx.node_states
    w = wf([node("a"), node("db", "Postgres", on_error="error_output"), node("ok"), node("alert")],
           [("a", "db"), ("db", "ok"), ("db", "alert", "error")])
    ctx = await runner.run(w, "a", items({"k": 1}), test_options=opts(mock))
    assert not ctx.errors and "alert" in ctx.node_states and "ok" not in ctx.node_states
    assert ctx.node_states["alert"][0].json["error"] == {"message": "db down", "node": "db"}


async def test_timeout_mock_raises_timeouterror_inside_the_loop():
    runner, _ = runner_with_outside_steps()
    w = chain(node("db", "Postgres", retry_on_fail=1, retry_delay=0))
    ctx = await runner.run(w, "a", items({}), test_options=opts({"db": {"timeout": True}}))
    assert "TEST_MOCK_TIMEOUT: db" in ctx.errors[0].error and ctx.node_metrics["db"]["retries"] == 1


async def test_retry_delay_is_skipped_in_test_mode():
    runner, _ = runner_with_outside_steps()
    w = chain(node("db", "Postgres", retry_on_fail=2, retry_delay=5000))
    started = time.perf_counter()
    await runner.run(w, "a", items({}), test_options=opts({"db": {"error": {"message": "x"}}}))
    assert time.perf_counter() - started < 2


# ── request capture, secrets, clock, rng, wait, persistence ──────────────────

async def test_outside_steps_record_their_resolved_parameters():
    runner, _ = runner_with_outside_steps()
    w = chain(node("db", "Postgres", parameters={"query": "select {{ $json.n }}", "credentialId": "c1", "_credentials": {"pw": "x"}}))
    ctx = await runner.run(w, "a", items({"n": 5}), test_options=opts({"db": {"items": [{}]}}))
    [req] = ctx.test_requests["db"]
    assert req["params"]["query"] == "select 5" and "_credentials" not in req["params"]


async def test_secret_vars_are_masked_and_plain_vars_resolve():
    runner, _ = make_runner()
    captured = {}

    class Spy:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            captured.update(context.variables)
            return NodeExecutionResult(outputs={"main": input_data})

    runner._registry._nodes["Spy"] = Spy()
    await runner.run(chain(node("s", "Spy")), "a", items({}), variables={"TOKEN": "sekret", "REGION": "eu"},
                     secret_values={"sekret"}, test_options=opts())
    assert captured == {"TOKEN": "<secret:TOKEN>", "REGION": "eu"}
    captured.clear()
    await runner.run(chain(node("s", "Spy")), "a", items({}), variables={"TOKEN": "sekret"}, secret_values={"sekret"})
    assert captured == {"TOKEN": "sekret"}  # real runs are untouched


async def test_frozen_clock_drives_the_expression_helpers_and_is_restored_afterwards():
    runner, _ = make_runner()
    seen = {}

    class Clock:
        input_count = 1

        async def execute(self, context, node_def, input_data):
            from src.engine.expression_engine import ExpressionEngine
            ectx = ExpressionEngine.create_context(input_data, {}, "e")
            seen["ctx"] = (context.frozen_now, context.rng.random())
            seen["date"] = expression_engine.resolve("{{ date_now() }}", ectx)
            seen["now"] = expression_engine.resolve("{{ now() }}", ectx)
            seen["ts"] = expression_engine.resolve("{{ timestamp() }}", ectx)
            return NodeExecutionResult(outputs={"main": input_data})

    runner._registry._nodes["Clock"] = Clock()
    await runner.run(chain(node("c", "Clock")), "a", items({}), test_options=opts())
    assert seen["date"] == "2026-01-01T09:00:00+00:00" and seen["now"] == 1767258000000 and seen["ts"] == 1767258000
    first = seen["ctx"]
    await runner.run(chain(node("c", "Clock")), "a", items({}), test_options=opts())
    assert seen["ctx"] == first  # same seed, same random stream
    assert abs(current_time().timestamp() - time.time()) < 5  # the freeze does not leak out of the run


async def test_current_time_and_rng_helpers_follow_the_frozen_values():
    from src.engine.test_mode import frozen_now_var, test_rng_var
    import random

    t1, t2 = frozen_now_var.set(NOW), test_rng_var.set(random.Random(7))
    try:
        assert current_time() == NOW and current_time(timezone.utc) == NOW
        a = current_rng().randint(0, 10**6)
    finally:
        frozen_now_var.reset(t1)
        test_rng_var.reset(t2)
    assert a == random.Random(7).randint(0, 10**6)
    assert parse_now("2026-02-03T04:05:06Z").year == 2026 and parse_now(None).isoformat() == "2026-01-01T09:00:00+00:00"
    assert parse_now("2026-02-03T04:05:06").tzinfo is not None


async def test_the_ai_tools_read_the_frozen_clock_and_seeded_rng():
    from src.engine.test_mode import frozen_now_var, test_rng_var
    from src.nodes.ai.tools.current_time_tool import CurrentTimeToolNode
    from src.nodes.ai.tools.random_number_tool import RandomNumberToolNode
    import random

    t1, t2 = frozen_now_var.set(NOW), test_rng_var.set(random.Random(3))
    try:
        assert CurrentTimeToolNode._execute("UTC")["datetime"] == "2026-01-01T09:00:00+00:00"
        assert CurrentTimeToolNode._execute("UTC")["day_of_week"] == "Thursday"
        assert RandomNumberToolNode._execute({"min": 1, "max": 1000}, 1, 100)["result"] == random.Random(3).randint(1, 1000)
    finally:
        frozen_now_var.reset(t1)
        test_rng_var.reset(t2)


async def test_wait_does_not_sleep_in_test_mode():
    ctx = ExecutionContext(workflow=Workflow(name="t", nodes=[], connections=[]), execution_id="e", start_time=NOW, mode="manual")
    ctx.test_options = opts()
    nd = node("w", "Wait", parameters={"unit": "seconds", "duration": 60})
    started = time.perf_counter()
    result = await WaitNode().execute(ctx, nd, items({"k": 1}))
    assert time.perf_counter() - started < 1 and result.outputs["main"][0].json == {"k": 1}


async def test_test_run_records_what_each_port_emitted_and_node_inputs():
    runner, _ = make_runner()
    w = wf([node("a"), node("b", "Branch"), node("t")], [("a", "b"), ("b", "t", "true")])
    ctx = await runner.run(w, "a", items({"k": 1}), test_options=opts())
    assert set(ctx.test_outputs["b"]) == {"true"} and ctx.test_outputs["b"]["true"][0].json["k"] == 1
    assert ctx.test_inputs["b"][0].json["k"] == 1
    plain = await runner.run(w, "a", items({"k": 1}))
    assert plain.test_outputs == {} and plain.test_options is None  # nothing is recorded outside test mode


async def test_shared_client_in_test_mode_refuses_real_traffic():
    from src.engine.test_mode import refusing_client
    async with refusing_client() as client:
        with pytest.raises(RuntimeError, match="disabled in test mode"):
            await client.get("http://example.com")


# ── http mocks through the real HttpRequest node ─────────────────────────────

def real_runner() -> WorkflowRunner:
    from src.engine.node_registry import register_all_nodes
    register_all_nodes()
    return WorkflowRunner()


def http_wf(**params) -> Workflow:
    base = {"method": "POST", "url": "http://api.test/orders/{{ $json.id }}", "body": '{"amount": {{ $json.amount }}}', "responseType": "json"}
    return Workflow(name="t", nodes=[node("Start", "Start"), node("Api", "HttpRequest", parameters={**base, **params})], connections=[])


def http_workflow(**params) -> Workflow:
    from src.engine.types import Connection
    w = http_wf(**params)
    w.connections = [Connection(source_node="Start", target_node="Api")]
    return w


async def test_http_mock_goes_through_the_real_node_and_records_the_request():
    runner = real_runner()
    mock = {"http": {"status": 200, "body": {"ok": True, "echo": "{{ $json.id }}"}, "headers": {"x-trace": "abc"}}}
    ctx = await runner.run(http_workflow(), "Start", items({"id": "o9", "amount": 42}), test_options=opts({"Api": mock}))
    assert not ctx.errors
    out = ctx.node_states["Api"][0].json
    assert out["statusCode"] == 200 and out["body"] == {"ok": True, "echo": "o9"} and out["headers"]["x-trace"] == "abc"
    [req] = ctx.test_requests["Api"]
    assert req["method"] == "POST" and req["url"] == "http://api.test/orders/o9" and req["body"] == {"amount": 42}
    assert ctx.node_metrics["Api"]["requestUrl"] == "http://api.test/orders/o9"  # the node's own metadata ran too


async def test_http_404_mock_is_a_normal_item_not_an_exception():
    runner = real_runner()
    ctx = await runner.run(http_workflow(), "Start", items({"id": "x", "amount": 1}),
                           test_options=opts({"Api": {"http": {"status": 404, "body": {"error": "gone"}}}}))
    assert not ctx.errors and ctx.node_states["Api"][0].json["statusCode"] == 404
    assert ctx.node_states["Api"][0].json["body"] == {"error": "gone"}


async def test_http_error_and_timeout_mocks_raise_from_the_transport_and_still_record_the_request():
    runner = real_runner()
    ctx = await runner.run(http_workflow(), "Start", items({"id": "x", "amount": 1}),
                           test_options=opts({"Api": {"error": {"message": "502 bad gateway", "code": "E_GW"}}}))
    assert ctx.errors[0].error == "502 bad gateway" and ctx.errors[0].error_code == "E_GW"
    assert len(ctx.test_requests["Api"]) == 1
    ctx = await runner.run(http_workflow(), "Start", items({"id": "x", "amount": 1}), test_options=opts({"Api": {"timeout": True}}))
    assert ctx.errors[0].error == "TEST_MOCK_TIMEOUT: Api"


async def test_http_rules_answer_each_request_by_its_input_item():
    runner = real_runner()
    mock = {"rules": [{"when": {"path": "amount", "op": "gt", "value": 500}, "then": {"http": {"status": 403, "body": {"d": "deny"}}}}],
            "default": {"http": {"status": 200, "body": {"d": "ok"}}}}
    ctx = await runner.run(http_workflow(), "Start", items({"id": "a", "amount": 5}, {"id": "b", "amount": 900}),
                           test_options=opts({"Api": mock}))
    assert [(o.json["statusCode"], o.json["body"]["d"]) for o in ctx.node_states["Api"]] == [(200, "ok"), (403, "deny")]
    assert [r["url"] for r in ctx.test_requests["Api"]] == ["http://api.test/orders/a", "http://api.test/orders/b"]


async def test_http_error_mock_retries_and_error_output_routes():
    runner = real_runner()
    w = http_workflow()
    w.nodes[1].retry_on_fail, w.nodes[1].retry_delay, w.nodes[1].on_error = 2, 0, "error_output"
    ctx = await runner.run(w, "Start", items({"id": "a", "amount": 1}), test_options=opts({"Api": {"error": {"message": "down"}}}))
    assert not ctx.errors and ctx.node_metrics["Api"]["retries"] == 2 and len(ctx.test_requests["Api"]) == 3
    assert ctx.test_outputs["Api"]["error"][0].json["error"] == {"message": "down", "node": "Api"}


async def test_unmocked_http_request_fails_and_a_real_run_still_works_without_options():
    runner = real_runner()
    ctx = await runner.run(http_workflow(), "Start", items({"id": "a", "amount": 1}), test_options=opts())
    assert ctx.errors[0].error_code == "TEST_UNMOCKED_EFFECT" and ctx.errors[0].details["effect"] == "network"
