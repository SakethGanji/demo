"""Row diff across a column whose dtype changed between the two versions.

A type change is exactly what a diff is FOR — the schema diff already reports
it as a first-class outcome (`BIGINT → VARCHAR`). The row diff used to build
its comparison over the common column NAMES without reconciling the two
versions' types, so DuckDB resolved `l."score" IS DISTINCT FROM r."score"` by
casting the text side back to INT64 and threw on the first non-numeric value —
a 500 on the one input the feature exists to explain.
"""

from __future__ import annotations

import duckdb

from app.features.data_accelerator.services.rowdiff import reconcile_types
from conftest import auth, upload_file

# id 1 keeps the same value across a pure representation change (10 → "10"),
# id 2 is a real edit into a value no number can hold, id 3 only moves in the
# same-typed column, id 4 goes NULL → value.
V1_CSV = "id,score,label\n1,10,a\n2,20,b\n3,30,c\n4,,d\n"
V2_CSV = "id,score,label\n1,10,a\n2,high,b\n3,30,z\n4,40,d\n"


async def _typed_versions(client, admin_id, tmp_path, v1=V1_CSV, v2=V2_CSV):
    """Two CSV versions of `id,score,label` whose `score` dtype disagrees."""
    a, b = tmp_path / "v1.csv", tmp_path / "v2.csv"
    a.write_text(v1)
    b.write_text(v2)
    ds = (await upload_file(client, admin_id, a, name="v1.csv"))["dataset_id"]
    await upload_file(client, admin_id, b, name="v2.csv", dataset_id=ds)
    return ds


def url(ds, sheet="data", a=1, b=2):
    return f"/api/v1/datasets/{ds}/versions/{a}/sheets/{sheet}/row-diff/{b}"


# --- the reconciliation rule, stated directly ---------------------------------

def test_agreeing_types_are_not_cast_at_all():
    """The guarantee that keeps this fix from changing any existing answer."""
    conn = duckdb.connect()
    types = {"a": "BIGINT", "b": "VARCHAR"}
    assert reconcile_types(conn, types, dict(types), ["a", "b"]) == {}


def test_a_pair_with_a_common_type_keeps_comparing_in_that_type():
    conn = duckdb.connect()
    assert reconcile_types(conn, {"n": "BIGINT"}, {"n": "DOUBLE"}, ["n"]) \
        == {"n": "DOUBLE"}
    assert reconcile_types(conn, {"t": "DATE"}, {"t": "TIMESTAMP"}, ["t"]) \
        == {"t": "TIMESTAMP"}


def test_a_pair_with_no_common_type_falls_back_to_text():
    """Only here does the diff answer a differently-shaped question."""
    conn = duckdb.connect()
    assert reconcile_types(conn, {"s": "BIGINT"}, {"s": "VARCHAR"}, ["s"]) \
        == {"s": "VARCHAR"}
    assert reconcile_types(conn, {"d": "DATE"}, {"d": "VARCHAR"}, ["d"]) \
        == {"d": "VARCHAR"}


# --- through the API ----------------------------------------------------------

async def test_the_schema_diff_already_calls_this_a_type_change(client, admin_id,
                                                                tmp_path):
    """The premise: the sibling endpoint treats this input as normal, not fatal.

    Also pins the fixture — if ingest ever stops inferring `score` as an
    integer in v1, the row-diff assertions below would be testing nothing.
    """
    ds = await _typed_versions(client, admin_id, tmp_path)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    changes = {c["column"]: (c["from_dtype"], c["to_dtype"])
               for c in r.json()["type_changes"]}
    assert "score" in changes
    assert changes["score"][0] != changes["score"][1]


async def test_row_diff_answers_across_a_changed_dtype(client, admin_id, tmp_path):
    """The bug: this returned 500 instead of a diff."""
    ds = await _typed_versions(client, admin_id, tmp_path)
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert (body["added"], body["removed"]) == (0, 0)
    # id 1 is unchanged: only its representation moved, not its value.
    assert (body["changed"], body["unchanged"]) == (3, 1)
    assert sorted(body["compared_columns"]) == ["label", "score"]
    assert body["column_changes"] == [{"column": "score", "changed_rows": 2},
                                      {"column": "label", "changed_rows": 1}]

    cells = {(c["row_key"], c["column_name"]): c for c in body["changed_sample"]}
    assert (cells["2", "score"]["before_value"],
            cells["2", "score"]["after_value"]) == ("20", "high")
    # The reported values are rendered from the stored column, so the reader
    # sees what each version actually holds — the cast is comparison-only.
    assert ("1", "score") not in cells


async def test_null_on_one_side_of_a_changed_dtype_still_reads_as_changed(
        client, admin_id, tmp_path):
    """`IS DISTINCT FROM` semantics must survive the type reconciliation."""
    ds = await _typed_versions(client, admin_id, tmp_path)
    body = (await client.post(url(ds), headers=auth(admin_id),
                              json={"key": ["id"]})).json()

    cells = {(c["row_key"], c["column_name"]): c for c in body["changed_sample"]}
    assert ("4", "score") in cells
    assert cells["4", "score"]["before_value"] is None
    assert cells["4", "score"]["after_value"] == "40"


async def test_a_column_typed_the_same_in_both_versions_is_unaffected(
        client, admin_id, tmp_path):
    """`label` is VARCHAR on both sides; reconciling `score` must not touch it."""
    ds = await _typed_versions(client, admin_id, tmp_path)
    body = (await client.post(url(ds), headers=auth(admin_id),
                              json={"key": ["id"], "columns": ["label"]})).json()

    assert body["compared_columns"] == ["label"]
    assert (body["changed"], body["unchanged"]) == (1, 3)
    assert body["column_changes"] == [{"column": "label", "changed_rows": 1}]
    cells = {(c["row_key"], c["column_name"]): c for c in body["changed_sample"]}
    assert (cells["3", "label"]["before_value"],
            cells["3", "label"]["after_value"]) == ("c", "z")


async def test_a_numeric_widening_still_compares_as_numbers(client, admin_id,
                                                            tmp_path):
    """BIGINT → DOUBLE has a common type, so 30 → 30.0 is not a row change.

    Falling back to a text comparison for every dtype disagreement would
    report all four rows as changed here, which is noise: the schema diff
    already reports the widening, and no row's value moved.
    """
    ds = await _typed_versions(
        client, admin_id, tmp_path,
        v1="id,score\n1,10\n2,20\n3,30\n4,40\n",
        v2="id,score\n1,10.0\n2,20.0\n3,30.5\n4,40.0\n")
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["changed"], body["unchanged"]) == (1, 3)
    assert body["column_changes"] == [{"column": "score", "changed_rows": 1}]


async def test_a_changed_dtype_on_the_KEY_column_does_not_500(client, admin_id,
                                                              tmp_path):
    """The key is joined with `IS NOT DISTINCT FROM`, so it has the same hazard."""
    ds = await _typed_versions(
        client, admin_id, tmp_path,
        v1="id,score\n1,10\n2,20\n3,30\n",
        v2="id,score\nA1,10\n2,25\n3,30\n")
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    # "1" and "A1" are different keys; 2 is repriced, 3 is untouched.
    assert (body["added"], body["removed"]) == (1, 1)
    assert (body["changed"], body["unchanged"]) == (1, 1)
