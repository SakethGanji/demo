"""Upload a CSV over HTTP the way the UI does (multipart, field `file`), then check every profile field."""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from .test_samples import SUMMARY_FIELDS

PROFILE_FIELDS = {
    "rows", "n_cols", "kind", "target", "suggested_target", "label_candidates", "columns", "classes", "n_classes",
    "imbalance_ratio", "missing", "duplicates", "warnings", "splits", "task", "target_stats",
    "target_source",  # F4: given / guessed / confirmed
}
COLUMN_FIELDS = {"name", "type", "dtype", "role", "missing", "missing_share", "unique", "example"}

PHRASES = {
    "praise": ["Great product, it works exactly as described and setup took minutes", "Love it, the battery lasts for days and it feels solid"],
    "complaint": ["The strap broke after one week and support never answered my emails", "Arrived late and the screen was cracked when I opened the box"],
    "question": ["Does this model work with older chargers or do I need the new cable", "Can I return it after thirty days if it is still sealed"],
}


def make_csv() -> tuple[bytes, pd.DataFrame]:
    """60 rows: an ID, a text column, a category with missing values, numbers with missing values, a label."""
    rows = []
    labels = ["praise"] * 30 + ["complaint"] * 20 + ["question"] * 10
    for i, lbl in enumerate(labels):
        rows.append({
            "review_id": f"R-{1000 + i}",
            "review_text": f"{PHRASES[lbl][i % 2]} (order {i})",
            "stars": [5, 4, 1, 2, 3][i % 5] if i % 10 else None,
            "product_line": None if i % 15 == 0 else ["wearables", "audio", "home"][i % 3],
            "price": round(19.5 + i * 1.25, 2),
            "label": lbl,
        })
    # two exact duplicates of row 3's content, with new IDs
    for j in range(2):
        rows.append({**rows[3], "review_id": f"R-dup-{j}"})
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode(), df


@pytest.fixture(scope="module")
def uploaded(client):
    content, df = make_csv()
    r = client.post("/api/datasets", files={"file": ("my_reviews.csv", content, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json(), df


def test_upload_returns_full_profile(uploaded):
    d, df = uploaded
    assert set(d) == SUMMARY_FIELDS | {"profile"}
    assert d["id"].startswith("ds_") and d["name"] == "my_reviews" and d["filename"] == "my_reviews.csv"
    assert d["source"] == "upload" and d["sample"] is False and d["note"] == "Uploaded file"
    assert d["rows"] == 62 and d["n_cols"] == 6 and d["kind"] == "text" and d["target"] == "label" and d["n_classes"] == 3
    assert d["size_bytes"] == len(make_csv()[0])

    p = d["profile"]
    assert set(p) == PROFILE_FIELDS
    assert (p["rows"], p["n_cols"], p["kind"], p["target"], p["suggested_target"]) == (62, 6, "text", "label", "label")
    assert p["label_candidates"][0] == "label" and "product_line" in p["label_candidates"]
    assert "review_text" not in p["label_candidates"] and "review_id" not in p["label_candidates"]


def test_upload_column_types_and_stats(uploaded):
    p = uploaded[0]["profile"]
    cols = {c["name"]: c for c in p["columns"]}
    assert list(cols) == ["review_id", "review_text", "stars", "product_line", "price", "label"]
    for c in cols.values():
        assert COLUMN_FIELDS <= set(c)
    assert {n: c["type"] for n, c in cols.items()} == {
        "review_id": "id", "review_text": "text", "stars": "number", "product_line": "category", "price": "number", "label": "category",
    }
    assert {n: c["role"] for n, c in cols.items()} == {
        "review_id": "ignored", "review_text": "feature", "stars": "feature", "product_line": "feature", "price": "feature", "label": "target",
    }
    # stars is missing on rows 0, 10, ..., 50; the duplicates copy row 3, which has a value
    assert cols["stars"]["missing"] == 6 and cols["stars"]["missing_share"] == round(6 / 62, 6)
    assert cols["stars"]["stats"]["min"] == 1 and cols["stars"]["stats"]["max"] == 5
    assert cols["product_line"]["missing"] == 4  # rows 0, 15, 30, 45
    assert cols["product_line"]["unique"] == 3
    assert {t["value"] for t in cols["product_line"]["top"]} == {"wearables", "audio", "home"}
    assert cols["price"]["stats"]["min"] == 19.5 and cols["price"]["missing"] == 0
    assert cols["review_text"]["stats"]["mean_chars"] > 40 and cols["review_text"]["unique"] == 60
    assert cols["review_id"]["unique"] == 62 and cols["review_id"]["example"] == "R-1000"
    assert cols["label"]["example"] == "praise"


def test_upload_balance_missing_duplicates(uploaded):
    p = uploaded[0]["profile"]
    assert p["classes"] == [
        {"name": "praise", "count": 32, "share": round(32 / 62, 6)},
        {"name": "complaint", "count": 20, "share": round(20 / 62, 6)},
        {"name": "question", "count": 10, "share": round(10 / 62, 6)},
    ]
    assert p["n_classes"] == 3 and p["imbalance_ratio"] == 3.2
    assert p["missing"] == {"cells": 10, "share": round(10 / (62 * 6), 6), "rows_with_missing": 8, "columns_with_missing": ["stars", "product_line"]}
    assert p["duplicates"] == {"rows": 2, "share": round(2 / 62, 6), "text": {"review_text": 2}, "conflicting_labels": 0, "ignoring": ["review_id"]}
    assert any("duplicate" in w for w in p["warnings"])
    assert not any("rare" in w for w in p["warnings"])  # 10/32 is above the rare threshold


def test_upload_splits(uploaded, data_dir):
    d = uploaded[0]
    s = d["profile"]["splits"]
    assert s["stratified"] is True and s["seed"] == 42 and s["target"] == "label"
    assert s["holdout_rows"] == 13 and sum(s["fold_rows"]) == 49 and len(s["fold_rows"]) == 5
    assert s["holdout_classes"] == {"praise": 7, "complaint": 4, "question": 2}
    raw = json.loads((data_dir / "datasets" / d["id"] / "splits.json").read_text())
    assert len(raw["fold"]) == 62 and len(raw["holdout"]) == 13


def test_uploaded_dataset_is_listed_and_fetchable(client, uploaded):
    d = uploaded[0]
    listed = client.get("/api/datasets").json()["datasets"]
    assert d["id"] in [x["id"] for x in listed]
    assert [x["source"] for x in listed[:2]] == ["sample", "sample"]
    got = client.get(f"/api/datasets/{d['id']}")
    assert got.status_code == 200 and got.json() == d


def test_stored_file_round_trips(uploaded, data_dir):
    d, df = uploaded
    stored = pd.read_csv(data_dir / "datasets" / d["id"] / "data.csv")
    assert list(stored.columns) == list(df.columns) and len(stored) == len(df)


def test_upload_with_explicit_target(client):
    content, _ = make_csv()
    r = client.post("/api/datasets", files={"file": ("reviews.csv", content, "text/csv")}, data={"target": "product_line"})
    assert r.status_code == 201, r.text
    p = r.json()["profile"]
    assert p["target"] == "product_line" and p["suggested_target"] == "label"
    assert {c["name"]: c["role"] for c in p["columns"]}["label"] == "feature"
    assert p["n_classes"] == 3 and sum(c["count"] for c in p["classes"]) == 58  # 4 rows have no value
    assert any("no label" in w for w in p["warnings"])
    assert p["splits"]["target"] == "product_line"


def test_upload_tsv(client):
    df = pd.DataFrame({"text": [f"sentence number {i} has a few words in it" for i in range(20)], "y": ["a"] * 8 + ["b"] * 8 + ["c"] * 4})
    r = client.post("/api/datasets", files={"file": ("small.tsv", df.to_csv(sep="\t", index=False).encode(), "text/tab-separated-values")})
    assert r.status_code == 201, r.text
    p = r.json()["profile"]
    assert [c["type"] for c in p["columns"]] == ["text", "category"] and p["target"] == "y"
    # 4 rows of 'c' can't fill a stratified holdout and five folds, so it falls back to plain splits
    assert p["splits"]["stratified"] is False and p["splits"]["holdout_rows"] == 4


def test_upload_without_label_column(client):
    df = pd.DataFrame({"a": range(30), "b": [i * 0.5 for i in range(30)]})
    r = client.post("/api/datasets", files={"file": ("numbers.csv", df.to_csv(index=False).encode(), "text/csv")})
    assert r.status_code == 201
    p = r.json()["profile"]
    assert p["target"] is None and p["classes"] == [] and p["n_classes"] == 0 and p["imbalance_ratio"] is None
    assert any("No column looks like a label" in w for w in p["warnings"])
    assert p["splits"]["stratified"] is False and p["splits"]["target"] is None


@pytest.mark.parametrize("name,content,message", [
    ("notes.parquet", b"PAR1....", "is not a CSV"),
    ("empty.csv", b"", "empty"),
    ("blank.csv", b"   \n\n", "empty"),
    ("one_col.csv", b"a\n1\n2\n3\n", "two columns"),
    ("one_row.csv", b"a,b\n1,2\n", "fewer than two data rows"),
    ("bad.csv", b'a,b\n"unterminated,1\n2,3\n', "Could not read"),
])
def test_upload_rejects_bad_files(client, name, content, message):
    r = client.post("/api/datasets", files={"file": (name, content, "text/csv")})
    assert r.status_code == 400
    assert message in r.json()["detail"]


def test_upload_rejects_unknown_target(client):
    content, _ = make_csv()
    r = client.post("/api/datasets", files={"file": ("x.csv", content, "text/csv")}, data={"target": "nope"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_rejected_upload_leaves_nothing_behind(client, data_dir):
    before = sorted(p.name for p in (data_dir / "datasets").iterdir())
    n = len(client.get("/api/datasets").json()["datasets"])
    client.post("/api/datasets", files={"file": ("x.csv", make_csv()[0], "text/csv")}, data={"target": "nope"})
    assert sorted(p.name for p in (data_dir / "datasets").iterdir()) == before
    assert len(client.get("/api/datasets").json()["datasets"]) == n


def test_upload_requires_a_file(client):
    assert client.post("/api/datasets").status_code == 422


def test_unknown_dataset_is_404(client):
    r = client.get("/api/datasets/ds_missing")
    assert r.status_code == 404 and "ds_missing" in r.json()["detail"]
