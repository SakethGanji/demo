"""Per-item loop history, the loop settings (run N at a time, skip a failed item, stop after N failures in a row) and
the repeat-until tries. Every setting left at its default must run exactly as before (see
test_loop_characterisation.py); these pin what the new settings do and what gets recorded."""

from __future__ import annotations

import pytest

from src.engine import loop_history as lh
from tests.unit._runner_fakes import items, node, wf
from tests.unit.test_loop_characterisation import ROWS, loop_wf, plain, poll_wf, runner

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def passes(ctx, loop="Each"):
    return lh.serialise_loops(ctx)[loop]["passes"]


FAILING = [{"id": "w1", "amount": 50}, {"id": "w2", "amount": 500, "fail": True}, {"id": "w3", "amount": 20}]


async def test_each_pass_is_recorded_with_its_item_branch_and_outputs():
    r, _ = runner()
    ctx = await r.run(loop_wf(), "Start", items(*ROWS))
    rec = lh.serialise_loops(ctx)["Each"]
    assert rec["totalItems"] == 3 and rec["concurrency"] == 1 and rec["onItemFailure"] == "stop"
    ps = rec["passes"]
    assert [p["index"] for p in ps] == [0, 1, 2]
    assert [p["status"] for p in ps] == ["ok", "ok", "ok"]
    assert [p["items"][0]["id"] for p in ps] == ["w1", "w2", "w3"]
    assert [p["nodes"]["Big"]["ports"] for p in ps] == [["false"], ["true"], ["false"]]
    assert ps[1]["nodes"]["Flag"]["items"][0]["id"] == "w2" and "Flag" not in ps[0]["nodes"]
    assert ps[0]["nodes"]["Check"]["items"][0] == {"id": "w1", "amount": 50, "mark": "Start", "checked": True}
    assert ps[0]["order"] == ["Check", "Big", "Release"]
    assert "Summary" not in ps[2]["nodes"]  # after the loop, not inside it


async def test_a_stopping_failure_marks_the_pass_failed():
    r, _ = runner()
    ctx = await r.run(loop_wf(), "Start", items(*FAILING))
    ps = passes(ctx)
    assert [p["status"] for p in ps] == ["ok", "failed"]
    assert ps[1]["failedAt"] == "Check" and "check failed for w2" in ps[1]["error"]
    assert ps[1]["nodes"]["Check"]["status"] == "failed"


async def test_skip_keeps_going_and_the_run_succeeds():
    r, log = runner()
    ctx = await r.run(loop_wf({"onItemFailure": "skip"}), "Start", items(*FAILING))
    assert not ctx.errors
    done = plain(ctx, "Summary")[0]
    assert done["total_items"] == 3 and [i["id"] for i in done["items"]] == ["w1", "w2", "w3"]
    assert ("Check", "w3") in log
    ps = passes(ctx)
    assert [p["status"] for p in ps] == ["ok", "skipped", "ok"]
    assert ps[1]["nodes"]["Check"]["status"] == "skipped"
    rec = lh.serialise_loops(ctx)["Each"]
    assert rec["skipped"] == 1 and rec["longestFailStreak"] == 1 and not rec.get("stoppedEarly")
    assert ctx.node_metrics["Check"]["status"] == "success"  # the last item's run


async def test_stop_after_failures_in_a_row():
    rows = [{"id": f"w{i}", "amount": 1, "fail": i in (1, 2, 3)} for i in range(6)]
    r, log = runner()
    ctx = await r.run(loop_wf({"onItemFailure": "skip", "stopAfterFailures": 2}), "Start", items(*rows))
    assert [e.error_code for e in ctx.errors] == [None, "LOOP_STOPPED_EARLY"]
    assert ctx.errors[0].node_name == "Check" and ctx.errors[1].node_name == "Each"
    assert [x[1] for x in log if x[0] == "Check"] == ["w0", "w1", "w2"]
    assert "Summary" not in ctx.node_states
    rec = lh.serialise_loops(ctx)["Each"]
    assert rec["stoppedEarly"] is True and [p["status"] for p in rec["passes"]] == ["ok", "skipped", "skipped"]


async def test_stop_after_failures_not_reached():
    rows = [{"id": f"w{i}", "amount": 1, "fail": i in (1, 3)} for i in range(5)]
    r, _ = runner()
    ctx = await r.run(loop_wf({"onItemFailure": "skip", "stopAfterFailures": 2}), "Start", items(*rows))
    assert not ctx.errors and plain(ctx, "Summary")[0]["total_items"] == 5
    assert lh.serialise_loops(ctx)["Each"]["longestFailStreak"] == 1


def _strip(ctx):
    return {k: [d.json for d in v] for k, v in ctx.node_states.items()}


async def test_concurrency_gives_the_same_output_as_one_after_another():
    # later items finish first: the output order must still be the items' order
    rows = [{"id": f"w{i}", "amount": 50 if i % 2 else 500, "delay": 0.05 * (5 - i)} for i in range(5)]
    r1, log1 = runner()
    seq = await r1.run(loop_wf(), "Start", items(*rows))
    r2, log2 = runner()
    par = await r2.run(loop_wf({"concurrency": 3}), "Start", items(*rows))
    assert not seq.errors and not par.errors
    assert _strip(par) == _strip(seq)
    assert par.node_run_counts == seq.node_run_counts
    assert sorted(log2) == sorted(log1) and log2 != log1  # same work, a different finishing order
    rec = lh.serialise_loops(par)["Each"]
    assert rec["concurrency"] == 3 and [p["status"] for p in rec["passes"]] == ["ok"] * 5
    assert [p["items"][0]["id"] for p in rec["passes"]] == [f"w{i}" for i in range(5)]
    assert [p["nodes"]["Big"]["ports"] for p in rec["passes"]] == [["true"], ["false"], ["true"], ["false"], ["true"]]
    assert "concurrencyFallback" not in par.node_metrics["Each"]


async def test_concurrency_actually_overlaps():
    import time

    rows = [{"id": f"w{i}", "amount": 1, "delay": 0.2} for i in range(4)]
    r, _ = runner()
    t = time.perf_counter()
    ctx = await r.run(loop_wf({"concurrency": 4}), "Start", items(*rows))
    assert not ctx.errors and time.perf_counter() - t < 0.6


async def test_concurrency_with_skip():
    rows = [{"id": f"w{i}", "amount": 1, "fail": i == 2, "delay": 0.01 * (5 - i)} for i in range(5)]
    r, _ = runner()
    ctx = await r.run(loop_wf({"concurrency": 2, "onItemFailure": "skip"}), "Start", items(*rows))
    assert not ctx.errors
    assert plain(ctx, "Summary")[0]["total_items"] == 5
    assert [p["status"] for p in passes(ctx)] == ["ok", "ok", "skipped", "ok", "ok"]


async def test_concurrency_with_stop_fails_the_run():
    rows = [{"id": f"w{i}", "amount": 1, "fail": i == 1} for i in range(6)]
    r, log = runner()
    ctx = await r.run(loop_wf({"concurrency": 2}), "Start", items(*rows))
    assert [e.node_name for e in ctx.errors] == ["Check"]
    assert "Summary" not in ctx.node_states
    assert len([x for x in log if x[0] == "Check"]) < 6  # no new items start after the failure


async def test_concurrency_with_stop_leaves_the_same_state_as_one_after_another():
    # item 1 fails slowly while items 2 and 3 finish first: their results must not be kept
    rows = [{"id": "w0", "amount": 1}, {"id": "w1", "amount": 1, "fail": True, "delay": 0.1},
            {"id": "w2", "amount": 500}, {"id": "w3", "amount": 1}]
    r1, _ = runner()
    seq = await r1.run(loop_wf(), "Start", items(*rows))
    r2, _ = runner()
    par = await r2.run(loop_wf({"concurrency": 3}), "Start", items(*rows))
    assert [(e.node_name, e.error) for e in par.errors] == [(e.node_name, e.error) for e in seq.errors]
    assert _strip(par) == _strip(seq)
    assert par.node_run_counts == seq.node_run_counts
    rec = lh.serialise_loops(par)["Each"]
    assert [p["status"] for p in rec["passes"]] == ["ok", "failed"]
    assert rec["ranAfterStop"] and rec["failed"] == 1


async def test_concurrency_stop_early():
    rows = [{"id": f"w{i}", "amount": 1, "fail": i >= 1} for i in range(8)]
    r, _ = runner()
    ctx = await r.run(loop_wf({"concurrency": 2, "onItemFailure": "skip", "stopAfterFailures": 3}), "Start", items(*rows))
    assert [e.error_code for e in ctx.errors][-1] == "LOOP_STOPPED_EARLY"
    assert lh.serialise_loops(ctx)["Each"]["stoppedEarly"] is True


async def test_a_body_that_cannot_run_side_by_side_falls_back():
    w = loop_wf({"concurrency": 3})
    w.nodes.append(node("Gate", "Poll", parameters={"condition": "{{ true }}", "interval": 1, "timeout": 5}))
    # Release -> Gate -> Each instead of Release -> Each
    from src.engine.types import Connection

    w.connections = [c for c in w.connections if not (c.source_node == "Release" and c.target_node == "Each")]
    w.connections += [Connection("Release", "Gate"), Connection("Gate", "Each", source_output="done")]
    r, _ = runner()
    ctx = await r.run(w, "Start", items(*ROWS))
    assert not ctx.errors and plain(ctx, "Summary")[0]["total_items"] == 3
    assert "Gate" in ctx.node_metrics["Each"]["concurrencyFallback"]


async def test_nested_loops_record_the_inner_passes_in_the_outer_pass():
    w = wf(
        [node("Start", "Mark"), node("Outer", "Loop", parameters={"batchSize": 1}), node("Inner", "Loop", parameters={"maxIterations": 2}),
         node("Step", "Mark"), node("After", "Mark"), node("End", "Mark")],
        [("Start", "Outer"), ("Outer", "Inner", "loop"), ("Inner", "Step", "loop"), ("Step", "Inner"),
         ("Inner", "After", "done"), ("After", "Outer"), ("Outer", "End", "done")],
    )
    r, _ = runner()
    ctx = await r.run(w, "Start", items({"id": "a"}, {"id": "b"}))
    assert not ctx.errors
    saved = lh.serialise_loops(ctx)
    assert [p["status"] for p in saved["Outer"]["passes"]] == ["ok", "ok"]
    assert saved["Outer"]["passes"][0]["nodes"]["Step"]["runs"] == 2
    assert len(saved["Inner"]["passes"]) == 2  # pass indexes restart for each outer item; the latest is kept


async def test_history_is_bounded():
    big = "x" * 10_000
    rows = [{"id": f"w{i}", "amount": 1, "blob": big} for i in range(lh.MAX_PASSES + 5)]
    r, _ = runner()
    w = wf([node("Start", "Mark"), node("Each", "Loop"), node("Check", "Check"), node("Summary", "Mark")],
           [("Start", "Each"), ("Each", "Check", "loop"), ("Check", "Each"), ("Each", "Summary", "done")])
    w.settings = {"max_iterations": 10_000}
    ctx = await r.run(w, "Start", items(*rows))
    rec = lh.serialise_loops(ctx)["Each"]
    assert len(rec["passes"]) == lh.MAX_PASSES and rec["truncated"] is True
    item = rec["passes"][0]["nodes"]["Check"]["items"][0]
    assert item["_truncated"] is True and len(item["blob"]) <= lh.MAX_STR
    assert plain(ctx, "Summary")[0]["total_items"] == lh.MAX_PASSES + 5  # outputs untouched


async def test_poll_tries_are_recorded(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr("src.nodes.flow.poll.asyncio.sleep", no_sleep)
    r, _ = runner()
    ctx = await r.run(poll_wf("{{ $json.attempt >= 3 }}"), "Start", items({"id": "p1"}))
    saved = lh.serialise_polls(ctx)["Until"]
    (s,) = saved["sessions"]
    assert [t["result"] for t in s["tries"]] == ["pending", "pending", "pending", "met"]
    assert [t["n"] for t in s["tries"]] == [1, 2, 3, 4]
    assert s["tries"][3]["value"]["attempt"] == 3 and s["outcome"] == "met" and s["attempts"] == 4


async def test_poll_timeout_is_recorded(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr("src.nodes.flow.poll.asyncio.sleep", no_sleep)
    clock = iter([0.0, 0.0, 0.5, 10.0, 10.0, 10.0])
    monkeypatch.setattr("src.nodes.flow.poll.time.time", lambda: next(clock))
    r, _ = runner()
    ctx = await r.run(poll_wf("{{ false }}", timeout=5), "Start", items({"id": "p1"}))
    assert "Late" in ctx.node_states
    (s,) = lh.serialise_polls(ctx)["Until"]["sessions"]
    assert [t["result"] for t in s["tries"]][-1] == "timeout" and s["outcome"] == "timeout"


def test_merge_saved_adds_resumed_passes():
    old = {"Each": {"passes": [{"index": 0, "status": "ok"}], "totalItems": 3}}
    new = {"Each": {"passes": [{"index": 1, "status": "ok"}], "totalItems": 3}}
    assert [p["index"] for p in lh.merge_saved(old, new, "loops")["Each"]["passes"]] == [0, 1]


async def test_concurrency_above_the_cap_is_clamped_and_recorded():
    # 1000 at a time would start every item at once; the cap is MAX_LOOP_CONCURRENCY (20): two waves of 0.2 s
    import time

    from src.nodes.flow.loop import MAX_LOOP_CONCURRENCY

    assert MAX_LOOP_CONCURRENCY == 20
    rows = [{"id": f"w{i}", "amount": 1, "delay": 0.2} for i in range(30)]
    r, _ = runner()
    t = time.perf_counter()
    ctx = await r.run(loop_wf({"concurrency": 1000}), "Start", items(*rows))
    took = time.perf_counter() - t
    assert not ctx.errors and plain(ctx, "Summary")[0]["total_items"] == 30
    assert took >= 0.38, f"ran everything at once ({took:.2f}s)"
    rec = lh.serialise_loops(ctx)["Each"]
    assert rec["concurrency"] == 20
    assert "1000" in rec["concurrencyClamped"] and "20" in rec["concurrencyClamped"]
    assert ctx.node_metrics["Each"]["concurrencyClamped"] == rec["concurrencyClamped"]


@pytest.mark.parametrize("raw, want", [(None, 1), (1, 1), (3, 3), (20, 20), ("lots", 1), (-3, 1), (0, 1)])
async def test_valid_and_bad_concurrency_values_keep_their_old_meaning(raw, want):
    params = {} if raw is None else {"concurrency": raw}
    r, _ = runner()
    ctx = await r.run(loop_wf(params), "Start", items(*ROWS))
    rec = lh.serialise_loops(ctx)["Each"]
    assert not ctx.errors and rec["concurrency"] == want
    assert "concurrencyClamped" not in rec and "concurrencyClamped" not in ctx.node_metrics["Each"]
