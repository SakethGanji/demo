"""P08 (plan §5.4 R5, §9.1 P08): the read paths over one version table, in DuckDB, never a full pandas read.

- **Keyset pages** on `__row_id`: `WHERE __row_id > :after [AND filter] ORDER BY __row_id LIMIT n`. Tables are written
  sorted by `__row_id` (§3.1), so DuckDB prunes row groups and stops at the first n rows (measured 30 ms at 10M).
- **A jump to a position** (`offset`) reads the one part file that holds it, by `file_row_number`: no OFFSET scan.
- **Search** (`q`): case-insensitive "contains" over the columns that can hold it. `table_stats` narrows the columns:
  a column whose every value is known (≤ 20 distinct) is matched in Python and becomes an `IN`, an all-digit column is
  skipped for a query with a letter, a number column is searched only for a number-like query. The first n hits come
  back; the count is a separate call with its own 3 s budget (`count`).
- **Filter preview**: the edit compiler's whitelisted filter AST (`dataset_edit._C.where`) as the WHERE.
- **Sort** by another column is refused above `SORT_MAX_ROWS` (1M) with "filter first" (R5); below it is allowed.
- A CSV-era version (P10 migrates them) is bound as an Arrow table from `frames.read_version` (bounded: the CSV store
  holds the samples and older uploads); its `__row_id` is the row's position, as `_row` always was.

Every query runs on the interactive connection (semaphore of 8, 10 s interrupt, `duck.Interactive`).
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .. import frames
from . import duck

ROW_ID = "__row_id"
SORT_MAX_ROWS = 1_000_000
COUNT_BUDGET_S = 3.0
MAX_LIMIT = 500
CSV_BIND = "_csv_src"


class PageError(ValueError):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


@dataclass
class Source:
    """One version table as a DuckDB relation."""

    version_id: str
    table: str
    storage: str                      # 'parquet' | 'csv'
    identity: str                     # table_sha256, or csv:<sha256>
    n_rows: int
    columns: list[tuple[str, str]]    # (name, DuckDB type), no __row_id
    rel: str                          # a FROM item
    parts: list[dict[str, Any]] = field(default_factory=list)   # parquet: {path, n_rows, start} in table order
    arrow: Any = None                 # csv: the Arrow table bound as CSV_BIND
    exprs: dict[str, str] = field(default_factory=dict)  # P20: masked column → its SQL over the stored column
    mask: Any = None                  # P20: the lake.mask.Mask applied (frames.scan)

    def wrap(self, base: str, extra: str = "") -> str:
        """P20: `base` (a FROM item over the stored columns) as a relation whose masked columns are masked, so a
        WHERE, a search or a sort sees only masked values."""
        if not self.exprs:
            return base
        cols = ", ".join(f"{self.exprs[c]} AS {q(c)}" if c in self.exprs else q(c) for c in self.names)
        return f"(SELECT {extra + ', ' if extra else ''}{q(ROW_ID)}, {cols} FROM {base})"

    @property
    def bind(self) -> dict[str, Any] | None:
        return {CSV_BIND: self.arrow} if self.arrow is not None else None

    def type_of(self, c: str) -> str:
        return dict(self.columns)[c]

    @property
    def names(self) -> list[str]:
        return [c for c, _ in self.columns]


_csv_cache: OrderedDict[tuple[str, str], Any] = OrderedDict()
_csv_lock = threading.Lock()


def _csv_arrow(version_id: str, identity: str) -> Any:
    """A CSV-era version's rows as Arrow (engine_v1 types, `__row_id` = position), cached for the last 4 versions."""
    import pyarrow as pa

    k = (version_id, identity)
    with _csv_lock:
        if k in _csv_cache:
            _csv_cache.move_to_end(k)
            return _csv_cache[k]
    df = frames.read_version(version_id).reset_index(drop=True)
    df.columns = [str(c) for c in df.columns]
    df.insert(0, ROW_ID, range(len(df)))
    at = pa.Table.from_pandas(df, preserve_index=False)
    with _csv_lock:
        _csv_cache[k] = at
        while len(_csv_cache) > 4:
            _csv_cache.popitem(last=False)
    return at


_meta: OrderedDict[tuple[str, str], Source] = OrderedDict()
_meta_lock = threading.Lock()


def meta_con():
    """A light read-only connection to the metadata DB (no schema / migration pass, unlike `Database()`), for the hot
    read paths. Committed version tables are immutable, so what it reads can be cached."""
    import sqlite3

    con = sqlite3.connect(f"file:{frames._settings().db_path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _parquet_meta(version_id: str, table: str | None) -> Source | None:
    """The Parquet table behind (version, table) from version_tables / table_files, or None for a CSV-era version."""
    con = meta_con()
    try:
        v = con.execute("SELECT storage, primary_table FROM datasets WHERE id = ?", (version_id,)).fetchone()
        if not v or v["storage"] != "parquet":
            return None
        name = table or frames.PRIMARY_TABLE
        if name == frames.PRIMARY_TABLE and not con.execute("SELECT 1 FROM version_tables WHERE version_id = ? AND table_name = ?",
                                                            (version_id, name)).fetchone():
            name = v["primary_table"]
        t = con.execute("SELECT table_sha256, n_rows, schema_json FROM version_tables WHERE version_id = ? AND table_name = ?",
                        (version_id, name)).fetchone()
        if not t:
            raise PageError(404, "table_not_found", f"{version_id} has no table {table!r}.")
        parts = con.execute("SELECT f.n_rows, b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256"
                            " WHERE f.version_id = ? AND f.table_name = ? ORDER BY f.part", (version_id, name)).fetchall()
    finally:
        con.close()
    st = frames._store()
    start, out = 0, []
    for p in parts:
        out.append({"path": str(st.uri(p["key"])), "key": p["key"], "n_rows": int(p["n_rows"]), "start": start})
        start += int(p["n_rows"])
    rel = "read_parquet([" + ",".join(_lit(p["path"]) for p in out) + "])"
    return Source(version_id, name, "parquet", t["table_sha256"], int(t["n_rows"]),
                  [(c["name"], c["type"]) for c in json.loads(t["schema_json"])], rel, out)


def _ensure_cached(src: Source) -> None:
    """P10b: on S3 with the read cache, a memoised Source's part paths are cache files that may since have been evicted,
    deleted or altered. `local_copy` re-checks each one (stat against the fill record) and refills it, verified; the
    path does not change (it is the sha)."""
    st = frames._store()
    if src.storage != "parquet" or getattr(st, "read_mode", None) != "cache":
        return
    for p in src.parts or []:
        if p.get("key"):
            st.local_copy(p["key"])


def source(version_id: str, table: str | None = None) -> Source:
    k = (version_id, table or "")
    with _meta_lock:
        hit = _meta.get(k)
        if hit is not None:
            _meta.move_to_end(k)
    if hit is not None:
        _ensure_cached(hit)
        return hit
    src = _parquet_meta(version_id, table)
    if src is not None:
        with _meta_lock:
            _meta[k] = src
            while len(_meta) > 512:
                _meta.popitem(last=False)
        return src
    if table not in (None, frames.PRIMARY_TABLE):
        raise PageError(404, "table_not_found", f"{version_id} has one table ('{frames.PRIMARY_TABLE}'), not {table!r}.")
    ident = frames.data_identity(version_id)
    at = _csv_arrow(version_id, ident)
    cols = [(f.name, _arrow_type(f.type)) for f in at.schema if f.name != ROW_ID]
    return Source(version_id, frames.PRIMARY_TABLE, "csv", ident, at.num_rows, cols, CSV_BIND, arrow=at)


def masked(src: Source, m: Any) -> Source:
    """P20: `src` with the columns of mask `m` (lake.mask.Mask) masked in SQL. The masked columns become VARCHAR."""
    from dataclasses import replace

    if m is None or not getattr(m, "cols", None):
        return replace(src, mask=m)
    exprs = {c: m.sql(c, q(c), t) for c, t in src.columns if c in m.cols}
    out = replace(src, columns=[(c, "VARCHAR" if c in exprs else t) for c, t in src.columns], exprs=exprs, mask=m)
    out.rel = out.wrap(src.rel)
    return out


def _arrow_type(t: Any) -> str:
    import pyarrow as pa

    if pa.types.is_integer(t):
        return "BIGINT"
    if pa.types.is_floating(t):
        return "DOUBLE"
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    return "VARCHAR"


def kind(t: str) -> str:
    """The grid's column kind for a DuckDB type (number | integer | text | bool | date)."""
    t = (t or "").upper()
    if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT"):
        return "integer"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith(("DECIMAL", "NUMERIC")):
        return "number"
    if t == "BOOLEAN":
        return "bool"
    if t == "DATE" or t.startswith("TIMESTAMP"):
        return "date"
    return "text"


# -- conditions ------------------------------------------------------------------------------------------------------

_NUMLIKE = re.compile(r"^[-+]?[0-9.eE]+$")
_DATELIKE = re.compile(r"^[0-9:T .\-/]+$")


def search_where(src: Source, text: str, stats: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """(SQL condition, the columns it searches) for "any column contains `text`", case-insensitive. 'false' when no
    column can hold it."""
    needle = text.strip().lower()
    if not needle:
        return "true", []
    by_col = {c["name"]: c for c in (stats or {}).get("columns") or []}
    has_alpha = any(ch.isalpha() for ch in needle)
    parts, searched = [], []
    for name, t in src.columns:
        if name in src.exprs:  # P20: a masked column is not searched (a hit would tell its value)
            continue
        k, st, x = kind(t), by_col.get(name) or {}, q(name)
        if k in ("integer", "number"):
            if st.get("top_complete") and _NUMLIKE.match(needle):  # every value is known (a code, a label)
                hits = [v["value"] for v in st.get("top") or [] if needle in str(v["value"]).lower()]
                if hits:
                    parts.append(f"CAST({x} AS VARCHAR) IN ({', '.join(_lit(h) for h in hits)})")
                    searched.append(name)
                continue
            if _NUMLIKE.match(needle):
                parts.append(f"contains(CAST({x} AS VARCHAR), {_lit(needle)})")
                searched.append(name)
            continue
        if k == "date":
            if _DATELIKE.match(needle):
                parts.append(f"contains(CAST({x} AS VARCHAR), {_lit(needle)})")
                searched.append(name)
            continue
        if k == "bool":
            if needle in "true" or needle in "false":
                parts.append(f"contains(lower(CAST({x} AS VARCHAR)), {_lit(needle)})")
                searched.append(name)
            continue
        if st.get("top_complete"):  # every value is known: match them here, search none or an IN
            hits = [v["value"] for v in st.get("top") or [] if needle in str(v["value"]).lower()]
            if hits:
                parts.append(f"{x} IN ({', '.join(_lit(h) for h in hits)})")
                searched.append(name)
            continue
        if st.get("digits_only") and not all(ch.isdigit() for ch in needle):
            continue
        if st.get("max_len") is not None and len(needle) > int(st["max_len"]):
            continue
        like = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parts.append(f"{x} ILIKE {_lit('%' + like + '%')} ESCAPE '\\'" if has_alpha else f"contains({x}, {_lit(needle)})")
        searched.append(name)
    return (" OR ".join(f"({p})" for p in parts) if parts else "false"), searched


def filter_where(src: Source, flt: Any) -> str:
    """The edit compiler's filter AST ({column, cmp, value} or a list of them, ANDed) as a WHERE, checked against the
    table's types (§4.4: a bad value is a 400, never an engine error)."""
    import duckdb

    from ..dataset_edit import EditError, _C

    if flt in (None, "", [], {}):
        return "true"
    if isinstance(flt, str):
        try:
            flt = json.loads(flt)
        except ValueError as e:
            raise PageError(400, "filter_invalid", "filter is JSON: {\"column\", \"cmp\", \"value\"} or a list of them.") from e
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE _f (" + ", ".join(f"{q(n)} {t}" for n, t in [(ROW_ID, "BIGINT"), *src.columns]) + ")")
        try:
            return _C(con, "_f", None, 0).where(flt, "filter", 0)
        except EditError as e:
            raise PageError(400, "filter_invalid", str(e)) from e
    finally:
        con.close()


# -- pages -----------------------------------------------------------------------------------------------------------

def _select(src: Source, columns: list[str] | None, with_pos: bool) -> tuple[str, str]:
    """(select list, FROM item). With positions, a Parquet table is read with each part's file_row_number."""
    cols = ", ".join(q(c) for c in (columns or src.names))
    if not with_pos:
        return f"{q(ROW_ID)}, {cols}", src.rel
    if src.storage == "csv":
        return f"{q(ROW_ID)}, {q(ROW_ID)} AS __pos, {cols}", src.rel
    files = ", ".join(_lit(p["path"]) for p in src.parts)
    case = " ".join(f"WHEN {_lit(p['path'])} THEN {p['start']}" for p in src.parts)
    rel = src.wrap(f"read_parquet([{files}], filename='__file', file_row_number=true)", "__file, file_row_number")
    return f"{q(ROW_ID)}, (CASE __file {case} END) + file_row_number AS __pos, {cols}", rel


def _rows(at: Any, src: Source) -> list[dict[str, Any]]:
    from .. import dataset_edit as DE

    out = []
    for r in at.to_pylist():
        d = {k: DE._jsonable(v) for k, v in r.items() if k not in (ROW_ID, "__pos")}
        d["_row_id"] = r[ROW_ID]
        if "__pos" in r:
            d["_row"] = r["__pos"]
        out.append(d)
    return out


def page(src: Source, *, after: int | None = None, offset: int | None = None, limit: int = 50, where: str = "true",
         columns: list[str] | None = None, sort: str | None = None, desc: bool = False, positions: bool = True) -> dict[str, Any]:
    """One page of rows. `after` (keyset, exclusive __row_id) is the normal way; `offset` jumps to a row position;
    `sort` orders by another column (≤ SORT_MAX_ROWS rows only)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    if columns:
        bad = [c for c in columns if c not in src.names]
        if bad:
            raise PageError(400, "column_not_found", f"No column {bad[0]!r} in {src.version_id}.")
    I = duck.interactive()
    sel, rel = _select(src, columns, positions)
    filtered = where.strip() != "true"
    if sort:
        if sort not in src.names:
            raise PageError(400, "column_not_found", f"No column {sort!r} in {src.version_id}.")
        if src.n_rows > SORT_MAX_ROWS:
            raise PageError(400, "sort_refused", f"Sorting {src.n_rows:,} rows by '{sort}' is refused above {SORT_MAX_ROWS:,} rows: "
                                                 "filter first (pages are in row order).", max_rows=SORT_MAX_ROWS)
        off = max(0, int(offset or 0))
        at = I.query(f"SELECT {sel} FROM {rel} WHERE {where} ORDER BY {q(sort)} {'DESC' if desc else 'ASC'} NULLS LAST, {q(ROW_ID)}"
                     f" LIMIT {limit} OFFSET {off}", fetch="arrow", bind=src.bind)
        rows = _rows(at, src)
        return {"rows": rows, "next_offset": off + len(rows) if len(rows) == limit else None, "next_after": None,
                "sort": sort, "desc": bool(desc), "how": "sorted"}
    if offset is not None and after is None and not filtered and src.storage == "parquet":
        rows = _position_page(src, max(0, int(offset)), limit, columns)
        nxt = rows[-1]["_row_id"] if len(rows) == limit else None
        return {"rows": rows, "next_after": nxt, "how": "position"}
    if offset is not None and after is None and (filtered or src.storage == "csv"):
        off = max(0, int(offset))
        if filtered and off > 100_000:
            raise PageError(400, "offset_too_far", "With a search or filter, page on with `after` (the last _row_id), not an offset past 100,000.")
        at = I.query(f"SELECT {sel} FROM {rel} WHERE {where} ORDER BY {q(ROW_ID)} LIMIT {limit} OFFSET {off}", fetch="arrow",
                     bind=src.bind)
        rows = _rows(at, src)
        return {"rows": rows, "next_after": rows[-1]["_row_id"] if len(rows) == limit else None, "how": "filtered offset" if filtered else "offset"}
    lo = -1 if after is None else int(after)
    if filtered:  # two steps: the ids of the first hits (only the searched columns are read), then those rows
        ids = [int(r[0]) for r in I.query(f"SELECT {q(ROW_ID)} FROM {src.rel} WHERE {q(ROW_ID)} > ? AND ({where})"
                                          f" ORDER BY {q(ROW_ID)} LIMIT {limit}", [lo], bind=src.bind)]
        if not ids:
            return {"rows": [], "next_after": None, "how": "keyset"}
        at = I.query(f"SELECT {sel} FROM {rel} WHERE {q(ROW_ID)} BETWEEN {ids[0]} AND {ids[-1]}"
                     f" AND {q(ROW_ID)} IN ({', '.join(map(str, ids))}) ORDER BY {q(ROW_ID)}", fetch="arrow", bind=src.bind)
    else:
        at = I.query(f"SELECT {sel} FROM {rel} WHERE {q(ROW_ID)} > ? ORDER BY {q(ROW_ID)} LIMIT {limit}", [lo],
                     fetch="arrow", bind=src.bind)
    rows = _rows(at, src)
    return {"rows": rows, "next_after": rows[-1]["_row_id"] if len(rows) == limit else None, "how": "keyset"}


def _position_page(src: Source, offset: int, limit: int, columns: list[str] | None) -> list[dict[str, Any]]:
    """Rows [offset, offset + limit) by position: only the part files that hold them, by file_row_number."""
    I = duck.interactive()
    cols = ", ".join(q(c) for c in (columns or src.names))
    out: list[dict[str, Any]] = []
    for p in src.parts:
        if len(out) >= limit:
            break
        end = p["start"] + p["n_rows"]
        if offset >= end:
            continue
        lo = max(0, offset - p["start"])
        need = limit - len(out)
        rel = src.wrap(f"read_parquet({_lit(p['path'])}, file_row_number=true)", "file_row_number")
        at = I.query(f"SELECT {q(ROW_ID)}, {p['start']} + file_row_number AS __pos, {cols} FROM {rel} WHERE file_row_number >= {lo} AND file_row_number < {lo + need} ORDER BY file_row_number",
                     fetch="arrow")
        out += _rows(at, src)
        offset = end
    return out


def count(src: Source, where: str, budget_s: float = COUNT_BUDGET_S) -> dict[str, Any]:
    """The number of rows matching `where`, or {count: null, timed_out: true} after `budget_s`."""
    if where.strip() == "true":
        return {"count": src.n_rows, "timed_out": False, "exact": True}
    try:
        n = duck.interactive().query(f"SELECT count(*) FROM {src.rel} WHERE {where}", timeout_s=budget_s, fetch="one", bind=src.bind)[0]
        return {"count": int(n), "timed_out": False, "exact": True}
    except duck.DuckTimeout:
        return {"count": None, "timed_out": True, "budget_s": budget_s,
                "message": f"counting took longer than {budget_s:g} s and was stopped; the page itself is complete."}


def lookup(src: Source, value: str, column: str | None, limit: int) -> dict[str, Any]:
    """G10: rows where `column` equals `value` as text; without a column, the first text column that has it."""
    if column is not None and column not in src.names:
        raise PageError(400, "column_not_found", f"No column '{column}' in {src.version_id}.")
    cols = [column] if column else [c for c, t in src.columns if kind(t) == "text"]
    I = duck.interactive()
    for c in cols:
        at = I.query(f"SELECT * FROM {src.rel} WHERE CAST({q(c)} AS VARCHAR) = ? ORDER BY {q(ROW_ID)} LIMIT {max(1, min(limit, 50))}",
                     [value], fetch="arrow", bind=src.bind)
        if at.num_rows:
            n = I.query(f"SELECT count(*) FROM {src.rel} WHERE CAST({q(c)} AS VARCHAR) = ?", [value], fetch="one", bind=src.bind)[0]
            rows = [{k: v for k, v in r.items() if k != "_row_id"} for r in _rows(at, src)]
            return {"column": c, "n": int(n), "rows": rows}
    return {"column": column, "n": 0, "rows": []}
