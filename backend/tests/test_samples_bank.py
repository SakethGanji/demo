"""The two bank samples added for the engine: credit default (binary, imbalanced) and loan amount (regression)."""

from __future__ import annotations

import json

import pandas as pd

from app.samples import generate_credit, generate_loan_amount


def test_four_samples_in_order(client):
    ids = [d["id"] for d in client.get("/api/datasets").json()["datasets"]]
    assert ids[:4] == ["support-tickets", "account-churn", "credit-default", "loan-amount"]


def test_credit_default_profile(client, data_dir):
    d = client.get("/api/datasets/credit-default").json()
    p = d["profile"]
    assert (d["rows"], d["n_cols"], d["kind"], d["target"], d["n_classes"]) == (12_000, 14, "tabular", "defaulted", 2)
    assert p["task"] == "binary" and p["target_stats"] is None
    shares = {c["name"]: c["share"] for c in p["classes"]}
    assert 0.06 < shares["Yes"] < 0.10  # imbalanced
    types = {c["name"]: c["type"] for c in p["columns"]}
    assert types["applicant_id"] == "id" and types["employment"] == "category" and types["credit_score"] == "number"
    assert set(p["missing"]["columns_with_missing"]) == {"income_usd", "months_employed", "region"}
    assert p["splits"]["stratified"] is True
    side = json.loads((data_dir / "datasets" / "credit-default" / "signal.json").read_text())
    assert side["label_column"] == "defaulted" and side["positive_class"] == "Yes" and "coefficients" in side


def test_loan_amount_profile(client, data_dir):
    d = client.get("/api/datasets/loan-amount").json()
    p = d["profile"]
    assert (d["rows"], d["kind"], d["target"], d["n_classes"]) == (8_000, "tabular", "loan_amount_usd", 0)
    assert p["task"] == "regression" and p["classes"] == [] and p["imbalance_ratio"] is None
    st = p["target_stats"]
    assert st["min"] >= 1000 and 30_000 < st["mean"] < 45_000 and st["std"] > 5_000
    assert "40 rows have no label and will be dropped." in p["warnings"]
    assert p["splits"]["stratified"] is False  # a continuous label isn't stratified
    side = json.loads((data_dir / "datasets" / "loan-amount" / "signal.json").read_text())
    df = pd.read_csv(data_dir / "datasets" / "loan-amount" / "data.csv")
    assert sorted(df.index[df["loan_amount_usd"].isna()].tolist()) == side["missing_label_rows"]


def test_bank_samples_are_deterministic():
    a, _ = generate_credit()
    b, _ = generate_credit()
    pd.testing.assert_frame_equal(a, b)
    a, _ = generate_loan_amount()
    b, _ = generate_loan_amount()
    pd.testing.assert_frame_equal(a, b)


def test_classification_profiles_name_their_task(client):
    assert client.get("/api/datasets/support-tickets").json()["profile"]["task"] == "multiclass"
    assert client.get("/api/datasets/account-churn").json()["profile"]["task"] == "binary"
