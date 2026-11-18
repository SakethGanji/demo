"""The two generated samples: present on first start, profiled correctly, with ground-truth sidecars."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from app.samples import TICKET_CLASSES, generate_churn, generate_tickets

SUMMARY_FIELDS = {"id", "name", "filename", "source", "sample", "note", "created_at", "size_bytes", "rows", "n_cols", "kind", "target", "n_classes"}


def _col(profile, name):
    return next(c for c in profile["columns"] if c["name"] == name)


def test_list_has_both_samples_first(client):
    r = client.get("/api/datasets")
    assert r.status_code == 200
    ds = r.json()["datasets"]
    assert [d["id"] for d in ds[:2]] == ["support-tickets", "account-churn"]
    for d in ds:
        assert set(d) == SUMMARY_FIELDS
    t, c = ds[0], ds[1]
    assert t["sample"] is True and t["source"] == "sample" and "sample" in t["note"].lower()
    assert (t["rows"], t["n_cols"], t["kind"], t["target"], t["n_classes"]) == (20_000, 7, "text", "category", 6)
    assert (c["rows"], c["n_cols"], c["kind"], c["target"], c["n_classes"]) == (10_000, 11, "tabular", "churned", 2)
    assert t["size_bytes"] > 1_000_000 and c["size_bytes"] > 100_000


def test_tickets_profile(client):
    r = client.get("/api/datasets/support-tickets")
    assert r.status_code == 200
    d = r.json()
    p = d["profile"]
    assert d["id"] == "support-tickets" and p["rows"] == 20_000 and p["n_cols"] == 7
    assert p["kind"] == "text"
    assert p["target"] == p["suggested_target"] == "category"
    assert p["label_candidates"][0] == "category"

    types = {c["name"]: c["type"] for c in p["columns"]}
    assert types["ticket_id"] == "id" and types["body"] == "text"
    assert types["channel"] == types["priority"] == types["customer_tier"] == types["category"] == "category"
    roles = {c["name"]: c["role"] for c in p["columns"]}
    assert roles["ticket_id"] == "ignored" and roles["category"] == "target" and roles["body"] == "feature"
    body = _col(p, "body")
    assert body["stats"]["mean_chars"] > 60 and body["missing"] == 0 and body["unique"] > 15_000

    classes = {c["name"]: c for c in p["classes"]}
    assert set(classes) == set(TICKET_CLASSES)
    assert sum(c["count"] for c in p["classes"]) == 20_000
    assert abs(sum(c["share"] for c in p["classes"]) - 1) < 1e-4
    assert p["classes"][-1]["name"] == "Security" and classes["Security"]["share"] < 0.05  # the rare class
    assert p["n_classes"] == 6 and p["imbalance_ratio"] > 5

    assert p["missing"] == {"cells": 0, "share": 0.0, "rows_with_missing": 0, "columns_with_missing": []}
    dup = p["duplicates"]
    assert 300 <= dup["rows"] <= 320  # 300 planted, plus a few natural collisions
    assert dup["ignoring"] == ["ticket_id"]
    assert dup["text"]["body"] >= dup["rows"]
    assert any("Security" in w for w in p["warnings"]) and any("duplicate" in w for w in p["warnings"])


def test_churn_profile(client):
    p = client.get("/api/datasets/account-churn").json()["profile"]
    assert p["kind"] == "tabular" and p["target"] == "churned"
    types = {c["name"]: c["type"] for c in p["columns"]}
    assert types == {
        "account_id": "id", "plan": "category", "region": "category", "contract": "category", "seats": "number",
        "mrr_usd": "number", "tenure_months": "number", "tickets_90d": "number", "last_login_days": "number",
        "nps": "number", "churned": "category",
    }
    classes = {c["name"]: c["share"] for c in p["classes"]}
    assert set(classes) == {"No", "Yes"} and 0.10 < classes["Yes"] < 0.18
    assert p["missing"]["columns_with_missing"] == ["region", "nps"]
    assert 400 < _col(p, "nps")["missing"] < 900 and 100 < _col(p, "region")["missing"] < 300
    assert p["missing"]["cells"] == _col(p, "nps")["missing"] + _col(p, "region")["missing"]
    seats = _col(p, "seats")["stats"]
    assert seats["min"] >= 1 and seats["max"] > seats["mean"] > seats["min"]
    assert {t["value"] for t in _col(p, "plan")["top"]} == {"starter", "business", "enterprise"}
    assert p["duplicates"]["rows"] == 0


def test_ticket_label_noise_sidecar_matches_the_file(data_dir):
    d = data_dir / "datasets" / "support-tickets"
    side = json.loads((d / "label_noise.json").read_text())
    df = pd.read_csv(d / "data.csv")
    assert side["n_flipped"] == len(side["flipped"]) == 300
    rows = [f["row"] for f in side["flipped"]]
    assert len(set(rows)) == 300 and all(0 <= r < len(df) for r in rows)
    for f in side["flipped"]:
        assert f["true_label"] != f["given_label"]
        assert df.at[f["row"], "category"] == f["given_label"]
        assert df.at[f["row"], "ticket_id"] == f["ticket_id"]
    # Flipped rows are never duplicated, so each flip is one unambiguous row.
    content = df.drop(columns="ticket_id")
    dup_any = content.duplicated(keep=False).to_numpy()
    assert not dup_any[rows].any()


def test_churn_signal_sidecar(data_dir):
    side = json.loads((data_dir / "datasets" / "account-churn" / "signal.json").read_text())
    assert side["label_column"] == "churned" and side["positive_class"] == "Yes"
    assert set(side["coefficients"]) >= {"intercept", "last_login_days", "contract=monthly"}


@pytest.mark.parametrize("dataset_id", ["support-tickets", "account-churn"])
def test_holdout_and_folds(client, data_dir, dataset_id):
    p = client.get(f"/api/datasets/{dataset_id}").json()["profile"]
    s = p["splits"]
    n = p["rows"]
    assert s["seed"] == 42 and s["stratified"] is True and s["n_folds"] == 5 and s["holdout_share"] == 0.2
    assert s["target"] == p["target"]
    assert s["holdout_rows"] == n // 5 and sum(s["fold_rows"]) == n - s["holdout_rows"]
    assert max(s["fold_rows"]) - min(s["fold_rows"]) <= 1

    raw = json.loads((data_dir / "datasets" / dataset_id / "splits.json").read_text())
    fold = np.asarray(raw["fold"])
    holdout = np.asarray(raw["holdout"])
    assert len(fold) == n and set(np.unique(fold)) == {-1, 0, 1, 2, 3, 4}
    assert set(holdout) == set(np.flatnonzero(fold == -1))  # holdout and folds are disjoint and cover every row

    # Stratified: each class has the same share (±1 row) in the holdout and every fold.
    y = pd.read_csv(data_dir / "datasets" / dataset_id / "data.csv")[p["target"]].astype(str).to_numpy()
    shares = {c["name"]: c["count"] for c in p["classes"]}
    assert s["holdout_classes"] == {k: int((y[holdout] == k).sum()) for k in shares}
    for k, total in shares.items():
        assert abs((y[holdout] == k).sum() - total * 0.2) <= 1
        for f in range(5):
            assert abs(((fold == f) & (y == k)).sum() - total * 0.16) <= 2


def test_generators_are_deterministic():
    a, sa = generate_tickets(n=2000, n_flips=30)
    b, sb = generate_tickets(n=2000, n_flips=30)
    pd.testing.assert_frame_equal(a, b)
    assert sa == sb
    c, _ = generate_tickets(n=2000, n_flips=30, seed=7)
    assert not a["body"].equals(c["body"])
    pd.testing.assert_frame_equal(generate_churn(n=500)[0], generate_churn(n=500)[0])


def test_ticket_text_overlaps_between_classes():
    """Vocabulary is shared between classes, so no single word gives the label away."""
    df, _ = generate_tickets(n=3000, n_flips=0)
    has_refund = df["body"].str.contains("refund", case=False)
    assert df.loc[has_refund, "category"].nunique() >= 3
    has_account = df["body"].str.contains("account", case=False)
    assert df.loc[has_account, "category"].nunique() == 6
