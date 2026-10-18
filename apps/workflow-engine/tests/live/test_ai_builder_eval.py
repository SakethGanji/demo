"""Stage 6 live eval (DESIGN-STAGE6-AI-BUILDER section 11.4): 15 bank-style requests against REAL Gemini through the AI builder.

Opt-in and billable. Skipped unless WORKFLOW_LIVE_GEMINI=1 and the builder's model has credentials. Run from apps/workflow-engine with
the ignored env file loaded into the shell (never printed, never copied):

    set -a; . ./.env.local; set +a
    WORKFLOW_LIVE_GEMINI=1 venv/bin/pytest tests/live/test_ai_builder_eval.py -s -q                       # all 15 + the bar
    WORKFLOW_LIVE_GEMINI=1 WF_AI_EVAL_CASES=refund_over_500 venv/bin/pytest tests/live/test_ai_builder_eval.py -s -q   # one case
    WORKFLOW_LIVE_GEMINI=1 venv/bin/pytest tests/live/test_ai_builder_eval.py -s -q -k "refund or loan"                 # by name

Knobs (env): WF_AI_EVAL_CASES / EVAL_CASES=a,b  WF_AI_EVAL_REPEAT=2 (a case passes only if every repeat does)  WF_AI_EVAL_BAR=0.8
WF_AI_EVAL_RESULTS=<path>.json (the markdown table is written beside it)  WF_AI_RECORD_DIR=<dir> (cassettes; default: next to the results)
WF_AI_EVAL_NO_PII=1 (do not append the canary secret and PII sentence to each request).
WF_AI_EVAL_GRAPH=1 (the graph arm: the spec call carries use_graph=true, clarify=false and each case records understanding.summary) |
both (run every case in both arms and print the two-arm table: pass rate, repair rounds and calls, without and with the graph; the bar is "no worse
on pass rate and at least 15% fewer repair rounds"). The scorer is the same in both arms. Unset: the old eval, unchanged.

Each case: project + fake bank APIs + canary secret, spec, approve, build, Keep (accept_failing), then the hidden scenarios. Passing
recordings are saved as <record dir>/<case>.json with their verdict (copy them to tests/fixtures/ai_builder/cassettes after review,
owner decision O6); failing ones go to <record dir>/failed/. The engine runs with WF_AI_STORE_PROMPTS=1 so the leak check (H6) can read
the scrubbed prompts; this prints names and counts only, never prompt text.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.engine import llm_provider, wf_ai
from tests.support import ai_cassette, ai_eval

ENABLED = os.environ.get("WORKFLOW_LIVE_GEMINI") == "1"
MODEL = wf_ai.config().model
pytestmark = [
    pytest.mark.skipif(not ENABLED, reason="set WORKFLOW_LIVE_GEMINI=1 to make real Gemini calls"),
    pytest.mark.skipif(ENABLED and not llm_provider.is_configured(MODEL), reason="no Gemini credentials configured"),
]

SELECTED = ai_eval.selected_ids()
try:
    CASE_IDS = [c["id"] for c in ai_eval.load_cases(SELECTED)]
except KeyError as _e:  # an unknown id in WF_AI_EVAL_CASES: say so when the tier runs, never break collection of the suite
    CASE_IDS = []
    if ENABLED:
        raise
REPEAT = max(1, int(os.environ.get("WF_AI_EVAL_REPEAT", "1") or 1))
BAR = float(os.environ.get("WF_AI_EVAL_BAR", str(ai_eval.DEFAULT_BAR)) or ai_eval.DEFAULT_BAR)
BUILD_TIMEOUT_S = 330.0
ARM_LIST = ai_eval.graph_arms()
BY_ARM: dict[str, list[dict[str, Any]]] = {a: [] for a in ARM_LIST}
RESULTS: list[dict[str, Any]] = BY_ARM[ARM_LIST[-1]]          # the old single-arm name: the (last) arm that ran; the bar test checks every arm
STARTED = time.monotonic()
_OUT = ai_eval.results_path()
RECORD_DIR = Path(os.environ.get("WF_AI_RECORD_DIR") or (_OUT.parent / f"{_OUT.stem}-cassettes"))


@pytest.fixture(scope="module")
def stack() -> Iterator[Any]:
    """A real engine (own throwaway DB, real Gemini, prompt storage on, recorder on) and a client that waits long enough for a build."""
    from tests.integration.conftest import EngineServer, StudioClient, TestDatabase

    db = TestDatabase()
    db.create()
    srv = None
    try:
        db.migrate("apply")
        creds = {k: v for k, v in os.environ.items()
                 if k.startswith(("GEMINI_", "GOOGLE_", "WORKFLOW_GEMINI_", "LLM_PROXY_", "WF_AI_", "WORKFLOW_MODULE_GEN_MODEL", "WORKFLOW_TEST_AI_MODEL"))}
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        creds.update(ai_cassette.record_env(RECORD_DIR))
        creds["WF_AI_STORE_PROMPTS"] = "1"
        srv = EngineServer(db, extra_env=creds, label="-live-ai-eval")
        srv.start()
        client = StudioClient(srv.base_url)
        yield client
    finally:
        try:
            _report()
        finally:
            if srv:
                srv.stop()
            db.drop()


def _line(r: dict[str, Any]) -> str:
    bad = [f"{k}: {v['why']}" for k, v in r["criteria"].items() if not v["ok"]]
    return (f"{r['id']:<30} {'PASS' if r['passed'] else 'FAIL'}  own {r['own'][0]}/{r['own'][1]}  hidden {r['hidden'][0]}/{r['hidden'][1]}  rounds {r['rounds']}  "
            f"calls {r['calls']}  {r['seconds']:.0f}s" + (("  <- " + " | ".join(bad)) if bad else ""))


def _report() -> None:
    if not any(BY_ARM.values()):
        return
    for arm, rs in BY_ARM.items():
        if rs and len(ARM_LIST) > 1:
            print(f"\n== arm: {arm} ==")
        if rs:
            print("\n" + ai_eval.render_table(rs, BAR))
    if len(ARM_LIST) > 1:
        print("\n" + ai_eval.render_arms(BY_ARM, BAR))
        for r in BY_ARM.get("graph") or []:
            print(f"  graph understood {r['id']}: {r.get('understanding_summary')}")
    meta = {"model": MODEL, "repeat": REPEAT, "bar": BAR, "wall_s": round(time.monotonic() - STARTED, 1), "record_dir": str(RECORD_DIR)}
    try:
        from src.services.ai_builder_prompts import prompts_version

        meta["prompts_version"] = prompts_version()
    except Exception:  # noqa: BLE001
        pass
    for arm, rs in BY_ARM.items():
        if not rs:
            continue
        out = _OUT if len(ARM_LIST) == 1 else _OUT.with_name(f"{_OUT.stem}-{arm}{_OUT.suffix}")
        path = ai_eval.write_results(rs, out, {**meta, "arm": arm, "comparison": ai_eval.compare_arms(BY_ARM, BAR) if len(ARM_LIST) > 1 else None}, BAR)
        print(f"results ({arm}): {path} (+ {path.with_suffix('.md').name})")
        if not ai_eval.summarize(rs, BAR)["meets_bar"]:
            print(f"bar NOT met ({arm}): see the FAIL lines above for the criterion that failed")


def _keep_cassette(art: dict[str, Any], res: dict[str, Any]) -> str | None:
    pid = art.get("project_id")
    src = RECORD_DIR / f"{pid}.json"
    if not pid or not src.exists():
        return None
    cassette = ai_cassette.load_cassette(src)
    cassette["case"], cassette["verdict"] = res["id"], res["verdict"]
    problems = ai_cassette.scan_cassette(cassette)
    good = res["passed"] and not problems
    dst = (RECORD_DIR / f"{res['id']}.json") if good and not (RECORD_DIR / f"{res['id']}.json").exists() else (RECORD_DIR / "failed" / f"{res['id']}--{pid}.json")
    ai_cassette.save_cassette(dst, cassette)
    src.unlink()
    if problems:
        print(f"  cassette NOT kept for {res['id']}: {problems[0]}")
    return str(dst)


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_live_case(case_id: str, stack: Any) -> None:
    case = ai_eval.load_case(case_id)
    for arm in ARM_LIST:
        worst: dict[str, Any] | None = None
        passes = []
        for n in range(REPEAT):
            art = ai_eval.run_case(stack, case, build_timeout=BUILD_TIMEOUT_S, pii_noise=os.environ.get("WF_AI_EVAL_NO_PII") != "1",
                                   log=lambda m: print(m, flush=True), graph=(arm == "graph"))
            res = ai_eval.score_case(case, art)
            res["arm"], res["understanding_summary"] = arm, art.get("understanding_summary")      # recorded beside the score, never in it
            res["cassette"] = _keep_cassette(art, res)
            passes.append(res["passed"])
            if worst is None or (worst["passed"] and not res["passed"]):
                worst = res
            print(f"[{arm} {n + 1}/{REPEAT}] " + _line(res), flush=True)
        assert worst is not None
        worst["repeats"] = passes
        worst["passed"] = all(passes)
        worst["verdict"]["passed"] = worst["passed"]
        BY_ARM[arm].append(worst)
        if len(CASE_IDS) == 1 and len(ARM_LIST) == 1:  # a single-case run is a debugging run: it must pass
            assert worst["passed"], _line(worst)


def test_live_bar() -> None:
    """The owner's bar over the cases that ran, in every arm: pass rate >= WF_AI_EVAL_BAR (default 0.8), and >= 9/10 on the dev cases when all 10 ran.
    With both arms it also prints the two-arm table; "compounding quality" is claimed only if the graph arm is no worse and has >= 15% fewer repair rounds."""
    if len(RESULTS) < 2:
        pytest.skip("the bar needs a run of several cases (a single case asserts itself)")
    if len(ARM_LIST) > 1:
        print("\n" + ai_eval.render_arms(BY_ARM, BAR), flush=True)
    for arm, rs in BY_ARM.items():
        s = ai_eval.summarize(rs, BAR)
        fails = [_line(r) for r in rs if not r["passed"]]
        assert s["meets_bar"], (f"[{arm}] pass rate {s['rate']:.1%} (need {BAR:.0%}), dev {s['dev'][0]}/{s['dev'][1]} (need {s['dev_needed']})\n" + "\n".join(fails))
