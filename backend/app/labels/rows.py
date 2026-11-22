"""Rows of a version's table by __row_id (labeling plan §5.2). POC path: pyarrow with an `in` filter per part (the
parts are sorted by __row_id, so row-group statistics prune); the DuckDB semi-join of §5.2 is the 10M-row path."""

from __future__ import annotations

from typing import Any, Iterable

from .values import LabelError


def version_info(con: Any, version_id: str) -> dict[str, Any]:
    r = con.execute("SELECT id, COALESCE(root_id, id) AS root, storage, state, primary_table FROM datasets WHERE id = ?",
                    (version_id,)).fetchone()
    if not r:
        raise LabelError(404, "version_not_found", f"No dataset version {version_id}.")
    return dict(r)


def table_of(version_id: str, table: str):
    from .. import frames

    try:
        return frames.lake_table(frames.Table(version_id, table))
    except frames.FrameError as e:
        raise LabelError(409, "labels_need_lake_version", str(e)) from None


def column_names(version_id: str, table: str) -> list[str]:
    return [c["name"] for c in table_of(version_id, table).schema]


def read(version_id: str, table: str, row_ids: Iterable[int] | None, columns: list[str] | None = None) -> dict[int, dict[str, Any]]:
    """{row_id: {column: value}} for the rows of `row_ids` present in the version (None: every row)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    lt = table_of(version_id, table)
    names = [c["name"] for c in lt.schema]
    want = [c for c in (columns or names) if c in names]
    ids = None if row_ids is None else sorted({int(i) for i in row_ids})
    if ids is not None and not ids:
        return {}
    pieces = []
    for p in lt.parts:
        t = pq.read_table(p["path"], columns=["__row_id", *want])
        if ids is not None:
            t = t.filter(pc.is_in(t.column("__row_id"), value_set=pa.array(ids, type=t.schema.field("__row_id").type)))
        pieces.append(t)
    at = pa.concat_tables(pieces, promote_options="default") if len(pieces) > 1 else pieces[0]
    out: dict[int, dict[str, Any]] = {}
    for r in at.to_pylist():
        out[int(r["__row_id"])] = r
    return out


def all_ids(version_id: str, table: str) -> list[int]:
    import pyarrow.parquet as pq

    lt = table_of(version_id, table)
    ids: list[int] = []
    for p in lt.parts:
        ids += pq.read_table(p["path"], columns=["__row_id"]).column("__row_id").to_pylist()
    return ids
