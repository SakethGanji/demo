"""Vela: findings computed from real runs (a planted leaky column must be flagged, an imbalanced label gets threshold
advice), each with one patch whose body trains; the other findings on crafted run records; the LLM wording order
(claude CLI → Anthropic API → computed) with a fake `claude`, its number guard and its timeout."""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.vela import findings as F
from app.vela import wording as W

from .conftest import start_run, wait_for_run

HP = {"GBM": {"num_boost_round": 30}, "LR": {}}


def _upload(client, df: pd.DataFrame, name: str) -> dict:
    r = client.post("/api/datasets", files={"file": (name, df.to_csv(index=False).encode(), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def leaky_run(client):
    rng = np.random.default_rng(3)
    n = 900
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    y = (x1 + 0.5 * x2 + rng.normal(size=n) > 0.6).astype(int)
    df = pd.DataFrame({"x1": x1.round(4), "x2": x2.round(4), "region": rng.choice(["n", "s", "e"], n),
                       "collections_flag": np.where(y == 1, "sent", "none"),  # recorded after the outcome
                       "outcome": np.where(y == 1, "bad", "good")})
    ds = _upload(client, df, "leaky.csv")
    run = start_run(client, dataset_id=ds["id"], label="outcome", time_limit=60, fit_kwargs={"hyperparameters": HP})
    return wait_for_run(client, run["id"])


def test_leaky_column_is_flagged_and_its_patch_retrains_without_it(client, leaky_run):
    assert leaky_run["status"] == "succeeded"
    v = client.get(f"/api/runs/{leaky_run['id']}/vela").json()
    assert v["errors"] == []
    f = v["findings"][0]
    assert f["kind"] == "leakage" and f["severity"] == "critical"
    s = f["numbers"]["suspects"][0]
    assert s["feature"] == "collections_flag" and s["single_score"] >= 0.97 and s["share"] >= 0.5
    assert "collections_flag" in f["text"] and f"{s['single_score']:.3f}" in f["text"]
    patch = f["patch"]
    assert patch["changes"] == [{"path": "drop_columns", "from": [], "to": ["collections_flag"],
                                 "label": "leave the suspect column(s) out of training"}]
    assert patch["body"]["drop_columns"] == ["collections_flag"] and patch["body"]["dataset_id"] == leaky_run["dataset_id"]
    # every finding states a number and offers exactly one patch
    for x in v["findings"]:
        assert x["patch"] and x["patch"]["changes"] and any(ch.isdigit() for ch in x["text"]), x
    assert v["wording"] is None
    # apply: the patched body trains, without the column, and records where it came from
    child = start_run(client, **patch["body"], parent_run_id=leaky_run["id"], patch={"id": patch["id"], "changes": patch["changes"]},
                      source="vela")
    child = wait_for_run(client, child["id"])
    assert child["status"] == "succeeded"
    assert "collections_flag" not in child["features"] and child["config"]["parent_run_id"] == leaky_run["id"]
    assert client.get(f"/api/runs/{child['id']}/gym").json()["parent_run_id"] == leaky_run["id"]
    after = client.get(f"/api/runs/{child['id']}/vela").json()
    assert not [x for x in after["findings"] if x["kind"] == "leakage"]
    # honest: without the leak the holdout score drops
    assert child["holdout"]["scores"]["accuracy"] < leaky_run["holdout"]["scores"]["accuracy"]


def test_imbalance_gets_threshold_advice(client):
    rng = np.random.default_rng(5)
    n = 1500
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    y = (1.4 * x1 - x2 + rng.normal(size=n) > 2.6).astype(int)
    assert 0.03 < y.mean() < 0.15
    ds = _upload(client, pd.DataFrame({"x1": x1, "x2": x2, "default": np.where(y == 1, "yes", "no")}), "imbalanced.csv")
    run = wait_for_run(client, start_run(client, dataset_id=ds["id"], label="default", time_limit=60, feature_importance=False,
                                         fit_kwargs={"hyperparameters": HP, "calibrate_decision_threshold": False})["id"])
    v = client.get(f"/api/runs/{run['id']}/vela").json()
    f = next(x for x in v["findings"] if x["kind"] == "imbalance")
    assert f["numbers"]["share"] == pytest.approx(y.mean(), abs=0.01) and f["numbers"]["calibrated"] is False
    assert f["numbers"]["f1_best"] >= f["numbers"]["f1_at_05"]
    assert f["patch"]["changes"][0]["path"] == "calibrate_threshold" and f["patch"]["changes"][0]["to"] == "f1"


def test_vela_refuses_unfinished_or_unknown_runs(client):
    assert client.get("/api/runs/run_missing/vela").status_code == 404


# -- the other findings, on crafted records --------------------------------------------------------------------------

BASE = {"dataset_id": "d", "label": "y", "presets": "medium_quality", "time_limit": 60.0, "fit_kwargs": {}, "drop_columns": []}


def test_budget_finding():
    rec = {"label": "y", "config": {"time_limit": 60}, "result": {"fit_seconds": 59.0, "leaderboard": [{"stack_level": 1}],
                                                                  "resolved": {"fit_kwargs": {"time_limit": 60, "num_stack_levels": 1}}}}
    out: list = []
    F.f_budget(rec, BASE, [{"type": "model_failed", "skipped": True}, {"type": "model_failed", "skipped": True}], out)
    f = out[0]
    assert f["numbers"]["used"] == pytest.approx(59 / 60) and f["numbers"]["skipped"] == 2
    assert f["numbers"]["stack_levels_fitted"] == 0 and f["numbers"]["stack_levels_planned"] == 1
    assert f["patch"]["changes"] == [{"path": "time_limit", "from": 60.0, "to": 120.0, "label": "τ = 60 s → 120 s"}]
    out = []
    F.f_budget({**rec, "result": {**rec["result"], "fit_seconds": 20.0, "resolved": {"fit_kwargs": {"time_limit": 60}}}}, BASE, [], out)
    assert out == []


def test_columns_finding():
    ds = {"profile": {"rows": 1000, "columns": [{"name": "c", "type": "category", "unique": 1},
                                                 {"name": "zip", "type": "category", "unique": 800},
                                                 {"name": "x", "type": "number", "unique": 900}]}}
    out: list = []
    F.f_columns({"result": {"features": ["c", "zip", "x"]}}, BASE, ds, out)
    f = out[0]
    assert f["numbers"]["constant"] == ["c"] and f["numbers"]["high_cardinality"] == [{"column": "zip", "unique": 800}]
    assert f["patch"]["body"]["drop_columns"] == ["c", "zip"] and "80%" in f["text"]


def test_drift_finding():
    batches = [{"id": "b1", "version": "m v1", "n_rows": 400, "drift": {"features": [{"feature": "util", "psi": 0.42},
                                                                                       {"feature": "income", "psi": 0.04}],
                                                                          "prediction": {"psi": 0.2}}}]
    out: list = []
    F.f_drift({}, BASE, batches, out)
    f = out[0]
    assert f["numbers"]["psi"] == 0.42 and f["numbers"]["shifted"] == ["util"] and "0.420" in f["text"]
    assert f["patch"]["body"]["drop_columns"] == ["util"]
    out = []
    F.f_drift({}, BASE, [{"id": "b", "drift": {"features": [{"feature": "a", "psi": 0.1}]}}], out)
    assert out == []


def test_weak_class_finding():
    rec = {"result": {"problem_type": "multiclass", "holdout": {"classes": ["a", "b", "c"],
                                                                "confusion_matrix": [[90, 5, 5], [30, 60, 10], [2, 3, 95]]},
                      "threshold": None}}
    out: list = []
    F.f_weak_class(rec, BASE, None, out)
    f = out[0]
    assert f["numbers"]["class"] == "b" and f["numbers"]["recall"] == pytest.approx(0.6)
    assert f["numbers"]["confused_with"] == "a" and f["numbers"]["confused_n"] == 30
    assert f["patch"]["changes"][0] == {"path": "predictor_kwargs.sample_weight", "from": None, "to": "balance_weight",
                                        "label": "wᵢ ∝ n / (K · n_{yᵢ}): each class carries equal total weight"}


def test_overfit_finding(tmp_path):
    rng = np.random.default_rng(0)
    n = 400
    y = rng.choice(["no", "yes"], n)
    pred = np.where(rng.random(n) < 0.8, y, np.where(y == "yes", "no", "yes"))  # 80% accurate on the holdout
    pd.DataFrame({"row": range(n), "y_true": y, "y_pred": pred}).to_csv(tmp_path / "holdout_predictions.csv", index=False)
    rec = {"config": {"run_dir": str(tmp_path)}, "result": {
        "problem_type": "binary", "eval_metric": {"name": "accuracy"}, "best_model": "M", "positive_class": "yes",
        "oof": {"available": False}, "leaderboard": [{"model": "M", "family": "GBM", "score_val": 0.95, "score_test": 0.80}]}}
    hp = F.holdout_frame(rec)
    out: list = []
    F.f_overfit(rec, BASE, hp, out)
    f = out[0]
    se = f["numbers"]["se"]
    assert 0.01 < se < 0.03  # √(0.8·0.2/400) = 0.02
    assert f["numbers"]["z"] == pytest.approx(0.15 / se) and f["severity"] == "critical"
    assert f["patch"]["changes"][0]["path"] == "fit_kwargs.num_bag_folds" and f["patch"]["changes"][0]["to"] == 5


# -- wording ----------------------------------------------------------------------------------------------------------

def _fake_claude(tmp_path: Path, reply: dict | str, *, exit_code: int = 0, sleep: float = 0) -> str:
    p = tmp_path / "claude"
    payload = json.dumps({"type": "result", "is_error": False, "result": reply if isinstance(reply, str) else json.dumps(reply),
                          "modelUsage": {"claude-sonnet-5-5": {}}, "total_cost_usd": 0.01})
    p.write_text(f"#!/bin/sh\ncat > /dev/null\nsleep {sleep}\necho '{payload}'\nexit {exit_code}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


FINDINGS = [{"id": "imbalance", "title": "Imbalanced label", "text": "The minority class is 8.2% of rows; F₁ is 0.31 at t = 0.5.",
             "computed_text": "The minority class is 8.2% of rows; F₁ is 0.31 at t = 0.5."},
            {"id": "budget", "title": "Budget", "text": "The fit took 59 s of τ = 60 s.", "computed_text": "The fit took 59 s of τ = 60 s."}]


def test_wording_order_and_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_VELA_LLM", "auto")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ENSEMBLE_CLAUDE_BIN", _fake_claude(tmp_path, {
        "imbalance": "Only 8.2% of rows are the minority class, so F₁ sits at 0.31 at t = 0.5.",
        "budget": "The fit used most of its budget."}))  # drops the numbers: must keep the computed text
    w = W.word(FINDINGS, {"name": "r"})
    assert w["source"] == "claude-cli" and w["model"] == "claude-sonnet-5-5"
    assert w["texts"]["imbalance"].startswith("Only 8.2%") and w["kept"]["imbalance"] is True
    assert w["texts"]["budget"] == FINDINGS[1]["computed_text"] and w["kept"]["budget"] is False
    # the CLI fails and there is no API key: the computed text, with both attempts recorded
    monkeypatch.setenv("ENSEMBLE_CLAUDE_BIN", _fake_claude(tmp_path, "boom", exit_code=1))
    w = W.word(FINDINGS, {"name": "r"})
    assert w["source"] == "computed" and [a["source"] for a in w["attempts"]] == ["claude-cli", "anthropic-api"]
    assert w["texts"] == {f["id"]: f["computed_text"] for f in FINDINGS}
    # a CLI that hangs is cut off at the timeout
    monkeypatch.setenv("ENSEMBLE_CLAUDE_BIN", _fake_claude(tmp_path, {}, sleep=5))
    monkeypatch.setenv("ENSEMBLE_VELA_TIMEOUT", "1")
    w = W.word(FINDINGS, {"name": "r"})
    assert w["source"] == "computed" and "timed out" in w["attempts"][0]["error"] and w["seconds"] < 4
    monkeypatch.setenv("ENSEMBLE_VELA_LLM", "off")
    assert W.word(FINDINGS, {})["disabled"] is True


def test_wording_over_http_is_cached_and_says_its_source(client, leaky_run):
    w = client.post(f"/api/runs/{leaky_run['id']}/vela/wording").json()
    assert w["source"] == "computed" and w["disabled"] is True  # the test server runs with ENSEMBLE_VELA_LLM=off
    v = client.get(f"/api/runs/{leaky_run['id']}/vela").json()
    assert v["wording"]["source"] == "computed" and v["wording"]["key"] == v["key"]
