"""The eval's graph arm (WF_AI_EVAL_GRAPH): it changes the spec call and what is recorded, never the scorer."""

from __future__ import annotations

import inspect

import pytest

from tests.support import ai_eval


class FakeApi:
    """Records every call; answers just enough for ``run_case`` to stop after the spec step (the spec call fails)."""

    def __init__(self, understanding=None):
        self.calls: list[tuple[str, str, object]] = []
        self.understanding = understanding

    def _r(self, status, body):
        class R:
            status_code = status
            text = ""

            def json(_s):
                return body
        return R()

    def post(self, path, body=None, **_kw):
        self.calls.append(("post", path, body))
        if path == "/projects":
            return self._r(201, {"id": "p1"})
        if path == "/ai/builder/spec":
            prop = {"id": "pr1", "spec": {"title": "t"}, **({"understanding": self.understanding} if self.understanding else {})}
            return self._r(201, {"proposal": prop})
        if path.endswith("/spec/approve"):
            return self._r(500, {"detail": {"code": "stop", "message": "stop here"}})   # the test only needs the spec step
        return self._r(200, {"id": "x"})

    put = post

    def get(self, path, **_kw):
        self.calls.append(("get", path, None))
        return self._r(404, {})


def spec_body_of(api):
    return next(b for m, p, b in api.calls if p == "/ai/builder/spec")


CASE = {"id": "refund_over_500", "request": "Refund over 500 needs approval.", "apis": []}


@pytest.mark.parametrize("env,arms", [(None, ["base"]), ("", ["base"]), ("0", ["base"]), ("1", ["graph"]), ("both", ["base", "graph"]), ("nonsense", ["base"])])
def test_the_env_selects_the_arms(env, arms):
    assert ai_eval.graph_arms(env if env is not None else "") == arms


def test_the_environment_is_read_when_no_flag_is_passed(monkeypatch):
    monkeypatch.delenv("WF_AI_EVAL_GRAPH", raising=False)
    assert ai_eval.graph_arms() == ["base"]
    monkeypatch.setenv("WF_AI_EVAL_GRAPH", "1")
    assert ai_eval.graph_arms() == ["graph"]


def test_the_base_arm_sends_the_old_spec_body_exactly():
    assert ai_eval.spec_body("p", "r", "a", False) == {"project_id": "p", "request": "r", "actor": "a"}


def test_the_graph_arm_adds_use_graph_and_never_asks():
    assert ai_eval.spec_body("p", "r", "a", True) == {"project_id": "p", "request": "r", "actor": "a", "use_graph": True, "clarify": False}


def test_run_case_toggles_the_flags_and_records_the_understanding_summary():
    summary = {"understood": ["Approval"], "reusing": {"subworkflows": 0, "templates": 1, "patterns": 0}, "questions": 0}
    g = FakeApi({"summary": summary})
    art = ai_eval.run_case(g, CASE, graph=True, pii_noise=False)
    assert spec_body_of(g)["use_graph"] is True and spec_body_of(g)["clarify"] is False
    assert art["arm"] == "graph" and art["understanding_summary"] == summary
    b = FakeApi({"summary": summary})
    art = ai_eval.run_case(b, CASE, graph=False, pii_noise=False)
    assert "use_graph" not in spec_body_of(b) and "clarify" not in spec_body_of(b)
    assert art["arm"] == "base" and art["understanding_summary"] is None


def test_run_case_follows_the_environment_by_default(monkeypatch):
    monkeypatch.setenv("WF_AI_EVAL_GRAPH", "1")
    g = FakeApi()
    assert ai_eval.run_case(g, CASE, pii_noise=False)["arm"] == "graph" and spec_body_of(g)["use_graph"] is True
    monkeypatch.setenv("WF_AI_EVAL_GRAPH", "both")                       # "both" is a live-test mode: a single run_case without a flag is the base arm
    b = FakeApi()
    assert ai_eval.run_case(b, CASE, pii_noise=False)["arm"] == "base"


def test_the_scorer_does_not_know_about_the_arm():
    src = inspect.getsource(ai_eval.score_case) + inspect.getsource(ai_eval.check_expect) + inspect.getsource(ai_eval.judge_hidden)
    for word in ("graph", "use_graph", "understanding", "arm"):
        assert word not in src.replace("program", ""), word
    sig = inspect.signature(ai_eval.score_case)
    assert list(sig.parameters) == ["case", "art"] or list(sig.parameters)[:2] == ["case", "art"]


def result(rounds, passed=True, calls=3):
    crit = {k: {"ok": True, "why": ""} for k in ai_eval.CRITERIA}
    return {"id": "c", "holdout": False, "passed": passed, "rounds": rounds, "calls": calls, "own": [3, 3], "hidden": [3, 3], "criteria": crit,
            "tokens_in": 0, "tokens_out": 0, "seconds": 1.0, "soft": 90}


def test_the_two_arm_table_and_the_bar():
    base = [result(2), result(2), result(0, passed=False)]
    graph = [result(1), result(1), result(1)]
    c = ai_eval.compare_arms({"base": base, "graph": graph})
    assert c["arms"]["base"]["repair_rounds"] == 4 and c["arms"]["graph"]["repair_rounds"] == 3 and c["rounds_reduction"] == 0.25
    assert c["no_worse"] and c["meets_bar"]
    text = ai_eval.render_arms({"base": base, "graph": graph})
    assert "without the graph" in text and "with the graph" in text and "MET" in text and "25% fewer" in text
    # a graph arm that passes less often does not meet the bar, however few rounds it takes
    worse = ai_eval.compare_arms({"base": base, "graph": [result(0, passed=False)] * 3})
    assert worse["no_worse"] is False and worse["meets_bar"] is False
    # fewer rounds, but under 15%
    close = ai_eval.compare_arms({"base": [result(10)], "graph": [result(9)]})
    assert close["rounds_reduction"] == 0.1 and close["meets_bar"] is False
    # nothing to beat: both zero is fine, a graph arm that needs rounds is not
    assert ai_eval.compare_arms({"base": [result(0)], "graph": [result(0)]})["meets_bar"] is True
    assert ai_eval.compare_arms({"base": [result(0)], "graph": [result(1)]})["meets_bar"] is False


def test_one_arm_makes_no_claim():
    c = ai_eval.compare_arms({"graph": [result(1)]})
    assert c["complete"] is False and "meets_bar" not in c
    assert "no comparison" in ai_eval.render_arms({"graph": [result(1)]})
