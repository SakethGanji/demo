"""P03 (plan §4.1, DLA4 #4): the one typed reader. Every read of a dataset version, a run's input, or a user's batch
file goes through here, so every consumer sees the same column types as the fit did.

    InputRef = Table(version_id, table) | JoinFrame(join_frame_id) | LegacyPath(path)
    read(ref, columns=None, rows=None, *, mode="engine_v1", hash_into=None) -> DataFrame   index = __row_id
    columns(ref) -> list[ColumnInfo];  n_rows(ref) -> int
    read_upload(src, *, filename=None, kind="batch") -> DataFrame                         user files: typed rules
    resolve(run_cfg) -> InputRef                                                           a run's input

This is the **CSV store** stage: a version is still `datasets/<id>/data.csv` + `schema.json` (DL4). `engine_v1` is
exactly what the training worker has always read (`frame_io.read(raw, schema.json)`: text columns stay text, so a ZIP
"02139" is never the number 2139); `native` is the same on the CSV store (Arrow types come with Parquet, P05). The
index is the row's position in data.csv, which is what `__row_id` is for a CSV-era version (and what every split,
holdout and SHAP row already names).

**P05: Parquet versions.** A version with `storage = 'parquet'` (every new upload) is read from its lake table parts
(`version_tables` / `table_files` → content-addressed blobs). `Table(v, "data")` names the version's primary table.
`engine_v1` turns the Arrow types into what the same data read from a CSV gave the fit: ints without nulls → int64,
ints with nulls → float64, DOUBLE / DECIMAL → float64, VARCHAR → object, DATE / TIMESTAMP → ISO text, BOOLEAN → bool.
The index is `__row_id`. `hash_into=ReadHash()` hashes every part's bytes as they are parsed and gives the table's
`table_sha256` (§3.3), so the worker's data gate compares exactly what it read with what the version committed.

`read_artifact` reads files the app itself wrote next to a run (holdout_predictions.csv, scores.csv, reference
tables): they are not datasets and have no schema. It exists so the grep gate (§6.5) has exactly one place to allow.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Union

import pandas as pd

from . import config
from . import frame_io as FIO

MODES = ("engine_v1", "native")
PRIMARY_TABLE = "data"  # the one table of every CSV-era version

#: P12: the table a request works on in a multi-table version (`POST /runs {table}`, `?table=` on diff / edit). Unset,
#: `Table(v)` / `table(v)` name the version's primary table, as before. Set with `use_table(name)` for one request: the
#: profile, the split, the run's input ref and every `table(v)` / `read_version(v)` below it then name that table.
import contextvars  # noqa: E402
from contextlib import contextmanager  # noqa: E402

CURRENT_TABLE: contextvars.ContextVar[str | None] = contextvars.ContextVar("ensemble_table", default=None)


def current_table() -> str | None:
    return CURRENT_TABLE.get()


#: P14: the join frame a run reads (`POST /runs {join_id}`): `input_ref_for(v)` then names the frame, while `table(v)`
#: (the profile's and the split's table) stays the join's grain table.
CURRENT_JOIN: contextvars.ContextVar[str | None] = contextvars.ContextVar("ensemble_join_frame", default=None)


@contextmanager
def use_join(join_frame_id: str | None):
    if not join_frame_id:
        yield
        return
    tok = CURRENT_JOIN.set(str(join_frame_id))
    try:
        yield
    finally:
        CURRENT_JOIN.reset(tok)


@contextmanager
def use_table(name: str | None):
    """`with use_table("bureau"):` every primary-table read of a version below it reads `bureau` (None: no change)."""
    if not name:
        yield
        return
    tok = CURRENT_TABLE.set(str(name))
    try:
        yield
    finally:
        CURRENT_TABLE.reset(tok)


# --------------------------------------------------------------------------- refs


@dataclass(frozen=True)
class Table:
    version_id: str
    table: str = PRIMARY_TABLE


@dataclass(frozen=True)
class JoinFrame:
    """P14: a materialised saved join (`join_frames` row): one Parquet cache blob, indexed by the grain's __row_id."""
    join_frame_id: str

    @property
    def version_id(self) -> str:  # what read errors name
        return self.join_frame_id


@dataclass(frozen=True)
class LegacyPath:
    """A data.csv named by path: old run configs (`csv_path`). Read with the schema.json beside it. P10: once its
    version is migrated, the path resolves to the version's Parquet table, read in engine_v1 mode with the CSV's
    positional index (what that run's readers have always been given); see `legacy_version`."""
    path: str


InputRef = Union[Table, JoinFrame, LegacyPath]


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    kind: str  # frame_io kinds: text | integer | number | bool


class FrameError(ValueError):
    """The ref names nothing readable (no such version / table / file)."""


def to_json(ref: InputRef) -> dict[str, Any]:
    if isinstance(ref, Table):
        return {"kind": "table", "version_id": ref.version_id, "table": ref.table}
    if isinstance(ref, JoinFrame):
        return {"kind": "join_frame", "join_frame_id": ref.join_frame_id}
    return {"kind": "legacy_path", "path": str(ref.path)}


def from_json(d: Any) -> InputRef | None:
    if not isinstance(d, dict):
        return None
    k = d.get("kind")
    if k == "table" and d.get("version_id"):
        return Table(str(d["version_id"]), str(d.get("table") or PRIMARY_TABLE))
    if k == "join_frame" and d.get("join_frame_id"):
        return JoinFrame(str(d["join_frame_id"]))
    if k == "legacy_path" and d.get("path"):
        return LegacyPath(str(d["path"]))
    return None


def _settings() -> config.Settings:
    return config.load_settings()


def path_of(ref: InputRef) -> Path:
    """The CSV-store file behind a ref (the only place that knows the layout `datasets/<id>/data.csv`). For a Parquet
    version it is the path data.csv *would* have; it does not exist (use `read`, `lake_table` or `is_parquet`)."""
    if isinstance(ref, LegacyPath):
        return Path(ref.path)
    if isinstance(ref, Table):
        if ref.table != PRIMARY_TABLE:
            if storage(ref.version_id) != "parquet":
                raise FrameError(f"{ref.version_id} has one table ('{PRIMARY_TABLE}'), not '{ref.table}'.")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", ref.version_id + ref.table) or ".." in ref.version_id + ref.table:
                raise FrameError(f"Bad dataset id or table {ref.version_id!r} / {ref.table!r}.")
            # P12: a named table of a multi-table version: a path that never exists (runs read input_ref, not this)
            return _settings().datasets_dir / ref.version_id / "tables" / f"{ref.table}.csv"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", ref.version_id) or ".." in ref.version_id:
            raise FrameError(f"Bad dataset id {ref.version_id!r}.")
        return _settings().datasets_dir / ref.version_id / "data.csv"
    raise FrameError("Saved joins are read from Parquet join frames (P14); there are none on the CSV store.")


# --------------------------------------------------------------------------- P05: Parquet versions in the lake


@dataclass(frozen=True)
class LakeTable:
    version_id: str
    name: str
    table_sha256: str
    n_rows: int
    schema: list[dict[str, Any]]
    parts: list[dict[str, Any]]  # [{part, role, sha256, key, path, n_rows, bytes}] in table order


def _db():
    from .db import Database

    return Database(_settings().db_path)


def _store():
    from .lake.store import open_store  # P10b: local or S3 (ENSEMBLE_BLOB_STORE)

    return open_store(_settings())


def storage(version_id: str) -> str | None:
    """'parquet' for a lake version, 'csv' (or None) for a CSV-era one."""
    try:
        with _db().connect() as con:
            r = con.execute("SELECT storage FROM datasets WHERE id = ?", (version_id,)).fetchone()
    except Exception:  # noqa: BLE001 - no catalog (a scratch path): the CSV store
        return None
    return r[0] if r else None


def is_parquet(ref: Any) -> bool:
    if isinstance(ref, str):
        ref = Table(ref)
    if isinstance(ref, JoinFrame):
        return True
    return isinstance(ref, Table) and storage(ref.version_id) == "parquet"


def _join_frame_table(ref: JoinFrame) -> LakeTable:
    """P14: a join frame as a one-part table: the part is its cache blob (role 'frame'), and its `table_sha256` is that
    blob's sha256, the hash a run on the frame records as read_sha256 (§5.8)."""
    with _db().connect() as con:
        r = con.execute("SELECT f.n_rows, f.schema_json, f.cache_blob, b.key, b.bytes FROM join_frames f"
                        " LEFT JOIN blobs b ON b.sha256 = f.cache_blob WHERE f.id = ?", (ref.join_frame_id,)).fetchone()
    if not r:
        raise FrameError(f"No join frame {ref.join_frame_id}.")
    if not r["cache_blob"] or not r["key"]:
        raise FrameError(f"Join frame {ref.join_frame_id} has no cache blob: materialise it again "
                         "(POST /api/datasets/{root}/versions/{v}/frames {join_id}).")
    st = _store()
    try:
        path = str(st.local_copy(r["key"]))
    except FileNotFoundError:
        path = str(st.cache_path(r["key"]))
    parts = [{"part": 0, "role": "frame", "sha256": r["cache_blob"], "key": r["key"], "n_rows": int(r["n_rows"]),
              "bytes": r["bytes"], "path": path}]
    return LakeTable(ref.join_frame_id, ref.join_frame_id, r["cache_blob"], int(r["n_rows"]), json.loads(r["schema_json"]), parts)


def lake_table(ref: Table | JoinFrame) -> LakeTable:
    """The committed table behind a ref: its sha, schema and parts (Table(v, 'data') = the primary table)."""
    if isinstance(ref, JoinFrame):
        return _join_frame_table(ref)
    with _db().connect() as con:
        v = con.execute("SELECT storage, primary_table FROM datasets WHERE id = ?", (ref.version_id,)).fetchone()
        if not v or v["storage"] != "parquet":
            raise FrameError(f"{ref.version_id} is not a Parquet version.")
        name = ref.table
        if name == PRIMARY_TABLE and current_table():  # P12: the request's table (use_table)
            name = current_table()
        if name == PRIMARY_TABLE and not con.execute("SELECT 1 FROM version_tables WHERE version_id = ? AND table_name = ?",
                                                    (ref.version_id, name)).fetchone():
            name = v["primary_table"]
        t = con.execute("SELECT table_sha256, n_rows, schema_json FROM version_tables WHERE version_id = ? AND table_name = ?",
                        (ref.version_id, name)).fetchone()
        if not t:
            raise FrameError(f"{ref.version_id} has no table {ref.table!r}.")
        parts = [dict(r) for r in con.execute(
            "SELECT f.part, f.role, f.blob_sha256 AS sha256, b.key, f.n_rows, b.bytes FROM table_files f"
            " JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = ? AND f.table_name = ? ORDER BY f.part",
            (ref.version_id, name))]
    st = _store()
    for p in parts:  # P10b: a local file (on S3, the sha-verified read cache); a missing blob keeps FileNotFoundError
        try:
            p["path"] = str(st.local_copy(p["key"]))
        except FileNotFoundError:
            p["path"] = str(st.cache_path(p["key"]))
    return LakeTable(ref.version_id, name, t["table_sha256"], int(t["n_rows"]), json.loads(t["schema_json"]), parts)


def lake_sql(lt: LakeTable) -> str:
    """The DuckDB relation over a committed table's parts (for engine-side work: export, profile)."""
    return "read_parquet([" + ",".join("'" + str(p["path"]).replace("'", "''") + "'" for p in lt.parts) + "])"


class ReadHash:
    """`hash_into` for `read`: on a Parquet table it collects each part's sha256 (of the exact bytes parsed) and its
    `hexdigest()` is the table_sha256 recomputed from them (§3.3); on a CSV it is the sha256 of data.csv's bytes."""

    def __init__(self) -> None:
        self._h = hashlib.sha256()
        self.parts: list[tuple[str, str]] = []  # (role, sha256)
        self.expected_parts: list[str] = []
        self.kind = "csv"

    def update(self, b: bytes) -> None:
        self._h.update(b)

    def add_part(self, role: str, sha: str, expected: str) -> None:
        self.kind = "frame" if role == "frame" else "parquet"  # P14: a join frame's one cache file
        self.parts.append((role, sha))
        self.expected_parts.append(expected)

    @property
    def bad_parts(self) -> list[int]:
        return [i for i, ((_, s), e) in enumerate(zip(self.parts, self.expected_parts)) if s != e]

    def hexdigest(self) -> str:
        if self.kind == "frame":  # P14: a join frame is identified by its cache file's sha256 (§5.8: read_sha256)
            return self.parts[0][1]
        if self.kind == "parquet":
            from .lake.manifest import table_sha256

            return table_sha256(self.parts)
        return self._h.hexdigest()


def hash_table(ref: Table | JoinFrame) -> tuple[str, str, list[int]]:
    """(table_sha256 recomputed from the part bytes now, the committed one, indexes of parts that differ)."""
    lt = lake_table(ref)
    rh = ReadHash()
    for p in lt.parts:
        h = hashlib.sha256()
        try:
            with open(p["path"], "rb") as f:
                while b := f.read(8 * 1024 * 1024):
                    h.update(b)
            sha = h.hexdigest()
        except FileNotFoundError:
            sha = "missing"
        rh.add_part(p["role"], sha, p["sha256"])
    return rh.hexdigest(), lt.table_sha256, rh.bad_parts


_KIND_OF_TYPE = {"BIGINT": "integer", "INTEGER": "integer", "SMALLINT": "integer", "TINYINT": "integer", "HUGEINT": "integer",
                 "UBIGINT": "integer", "UINTEGER": "integer", "USMALLINT": "integer", "UTINYINT": "integer",
                 "DOUBLE": "number", "FLOAT": "number", "REAL": "number", "BOOLEAN": "bool"}


def kind_of_type(t: str) -> str:
    t = (t or "").upper()
    if t.startswith("DECIMAL") or t.startswith("NUMERIC"):
        return "number"
    return _KIND_OF_TYPE.get(t, "text")


def _v1_column(arr: Any, name: str) -> Any:
    """One Arrow column as the fit saw the same values read from CSV (engine_v1)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc

    t = arr.type
    if pa.types.is_integer(t):
        if arr.null_count == 0:
            return pd.Series(arr.to_numpy(zero_copy_only=False).astype("int64"), name=name)
        return pd.Series(pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False), name=name)
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return pd.Series(pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False), name=name)
    if pa.types.is_boolean(t):
        if arr.null_count == 0:
            return pd.Series(arr.to_numpy(zero_copy_only=False).astype(bool), name=name)
        v = arr.to_numpy(zero_copy_only=False).astype(object)
        v[pd.isna(v)] = np.nan  # F-X2 m1: a missing cell is NaN, as pandas reads it from CSV (not None)
        return pd.Series(v, name=name, dtype=object)
    if pa.types.is_date(t) or pa.types.is_timestamp(t):
        if pa.types.is_date(t):
            arr = pc.strftime(pc.cast(arr, pa.timestamp("s")), format="%Y-%m-%d")
        else:
            arr = pc.cast(arr, pa.string())
    elif not (pa.types.is_string(t) or pa.types.is_large_string(t)):
        arr = pc.cast(arr, pa.string())
    v = arr.to_numpy(zero_copy_only=False)
    if arr.null_count:
        v = v.astype(object)
        v[pd.isna(v)] = np.nan  # a missing text cell is NaN, as pandas reads it from CSV
    return pd.Series(v, name=name, dtype=object)


def _read_parquet(ref: Table | JoinFrame, columns: Any, rows: range | None, mode: str, hash_into: Any) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.parquet as pq

    lt = lake_table(ref)
    names = [c["name"] for c in lt.schema]
    keep = _cols_filter(columns)
    want = [c for c in names if keep is None or keep(c)]
    if rows is not None and hash_into is None:  # F-X5a: a window reads only the row groups it covers, never the table
        return _read_window(ref, lt, want, rows, mode)
    pieces = []
    for p in lt.parts:
        try:
            if hash_into is not None:
                buf = Path(p["path"]).read_bytes()  # hash exactly the bytes that are parsed (DL3)
                sha = hashlib.sha256(buf).hexdigest()
                if hasattr(hash_into, "add_part"):
                    hash_into.add_part(p["role"], sha, p["sha256"])
                else:
                    hash_into.update(buf)
                src: Any = pa.BufferReader(buf)
            else:
                src = p["path"]
            pieces.append(pq.read_table(src, columns=["__row_id", *want]))
        except FileNotFoundError as e:
            raise FrameError(f"A file of {ref.version_id} ({p['sha256'][:12]}…) is missing from the lake.") from e
    # P13: parts written at different times may differ in Parquet nullability (DuckDB writes REQUIRED for a column it
    # knows has no nulls): an append part next to its base must still concatenate
    at = pa.concat_tables(pieces, promote_options="default") if len(pieces) > 1 else pieces[0]
    if rows is not None:
        at = at.slice(rows.start, max(0, len(rows)))
    idx = pd.Index(at.column("__row_id").to_numpy(zero_copy_only=False), name=None)
    if mode == "native":
        df = at.drop(["__row_id"]).to_pandas()
        df.index = idx
        return df
    cols = {c: _v1_column(at.column(c), c) for c in want}
    df = pd.DataFrame(cols, columns=want)
    df.index = idx
    return df


def _read_window(ref: Table | JoinFrame, lt: "LakeTable", want: list[str], rows: range, mode: str) -> pd.DataFrame:
    """F-X5a: rows [start, stop) of the parts' concatenation, read row group by row group (the groups outside the window are
    skipped). The same frame as slicing the whole read (engine_v1 types of the window's own cells)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    start, stop = rows.start, rows.start + max(0, len(rows))
    pieces, at_row = [], 0
    for p in lt.parts:
        if at_row >= stop:
            break
        try:
            pf = pq.ParquetFile(p["path"])
        except FileNotFoundError as e:
            raise FrameError(f"A file of {ref.version_id} ({p['sha256'][:12]}…) is missing from the lake.") from e
        for rg in range(pf.num_row_groups):
            n = pf.metadata.row_group(rg).num_rows
            lo, hi = at_row, at_row + n
            at_row = hi
            if hi <= start:
                continue
            if lo >= stop:
                break
            t = pf.read_row_group(rg, columns=["__row_id", *want])
            pieces.append(t.slice(max(0, start - lo), min(hi, stop) - max(lo, start)))
    if pieces:
        at = pa.concat_tables(pieces, promote_options="default") if len(pieces) > 1 else pieces[0]
    else:
        sch = pq.ParquetFile(lt.parts[0]["path"]).schema_arrow if lt.parts else None
        at = sch.empty_table().select(["__row_id", *want]) if sch is not None else pa.table({"__row_id": pa.array([], pa.int64())})
    idx = pd.Index(at.column("__row_id").to_numpy(zero_copy_only=False), name=None)
    if mode == "native":
        df = at.drop(["__row_id"]).to_pandas()
        df.index = idx
        return df
    df = pd.DataFrame({c: _v1_column(at.column(c), c) for c in want}, columns=want)
    df.index = idx
    return df


def table(version_id: str, table: str | None = None) -> Table:
    """The ref of a version's table: `table`, else the request's table (`use_table`), else the primary table."""
    return Table(version_id, table or current_table() or PRIMARY_TABLE)


def version_file(version_id: str) -> Path:
    """The file a CSV-store version is written to (the writers of datasets.py until P07's VersionWriter, hashing and
    run configs' `csv_path` key). Never read it directly: read `Table(version_id)`."""
    return path_of(Table(version_id))


def read_version(version_id: str, columns: Iterable[str] | Callable[[str], bool] | None = None,
                 rows: range | slice | None = None) -> pd.DataFrame:
    """`read(Table(version_id))`: the version's one table, typed (what `DatasetStore.load_frame` used to return)."""
    return read(Table(version_id), columns, rows)


def legacy_version(ref: Any) -> str | None:
    """P10, the legacy path resolver: the migrated version a CSV-era path names (`…/datasets/<id>/data.csv` of a version
    that is Parquet now), else None."""
    if not isinstance(ref, LegacyPath):
        return None
    from .lake.migrate import version_of_legacy_path

    vid = version_of_legacy_path(ref.path)
    if vid is None or Path(ref.path).exists() or storage(vid) != "parquet":
        return None
    return vid


def resolve(cfg: dict[str, Any]) -> InputRef:
    """A run config's input: `input_ref` (new configs), else `csv_path` (every config so far: the exact file the fit
    read, so an old run re-reads what it trained on), else `dataset_id`.
    P10: a run that read a CSV-era version (no committed `input_table.table_sha256`) resolves to its `csv_path`, which
    reads the migrated table with the positions that run's rows are named by."""
    ref = from_json(cfg.get("input_ref"))
    if ref is not None:
        if (isinstance(ref, Table) and cfg.get("csv_path") and not (cfg.get("input_table") or {}).get("table_sha256")
                and legacy_version(LegacyPath(str(cfg["csv_path"]))) == ref.version_id):
            return LegacyPath(str(cfg["csv_path"]))
        return ref
    if cfg.get("csv_path"):
        return LegacyPath(str(cfg["csv_path"]))
    if cfg.get("join_frame_id"):
        return JoinFrame(str(cfg["join_frame_id"]))
    if cfg.get("dataset_id"):
        return Table(str(cfg["dataset_id"]))
    raise FrameError("This run's config names no input (no input_ref, csv_path or dataset_id).")


def input_ref_for(dataset_id: str) -> dict[str, Any]:
    """The `input_ref` a new run config carries (`csv_path` is kept beside it for old readers)."""
    if CURRENT_JOIN.get():  # P14: POST /runs {join_id}: the run reads the join frame
        return to_json(JoinFrame(CURRENT_JOIN.get()))
    return to_json(table(dataset_id))  # P12: the request's table (POST /runs {table}), else the primary


# --------------------------------------------------------------------------- reads


def _cols_filter(columns: Iterable[str] | Callable[[str], bool] | None) -> Any:
    if columns is None:
        return None
    if callable(columns):
        return columns
    want = list(dict.fromkeys(str(c) for c in columns))
    keep = set(want)
    return lambda c: c in keep


def read(ref: InputRef, columns: Iterable[str] | Callable[[str], bool] | None = None, rows: range | slice | None = None, *,
         mode: str = "engine_v1", hash_into: Any = None) -> pd.DataFrame:
    """The table behind `ref` with its schema types. `columns` keeps only those (names not in the table are skipped;
    the caller's own lookup then says which is missing). `rows` (a range / slice with step 1) reads that window; the
    index stays the row id. `hash_into` (a hashlib object) is fed exactly the bytes parsed (DL3: hash what the fit
    reads)."""
    if mode not in MODES:
        raise FrameError(f"mode is one of {', '.join(MODES)}, not {mode!r}.")
    if isinstance(rows, slice):
        rows = range(rows.start or 0, rows.stop if rows.stop is not None else 2 ** 62, rows.step or 1)
    if rows is not None and (rows.step != 1 or rows.start < 0):
        raise FrameError("rows is a contiguous, non-negative range.")
    if isinstance(ref, (Table, JoinFrame)) and is_parquet(ref):
        return _read_parquet(ref, columns, rows, mode, hash_into)
    lv = legacy_version(ref)
    if lv is not None:  # P10: a CSV-era run's path, migrated: the same frame, by position
        if hash_into is not None:
            hash_into.update(legacy_csv_bytes(lv))
        df = _read_parquet(Table(lv), columns, rows, mode, None)
        start = rows.start if rows is not None else 0
        df.index = pd.RangeIndex(start, start + len(df))
        return df
    p = path_of(ref)
    if not p.exists():
        raise FrameError(f"No data file for {to_json(ref)}.")
    schema = FIO.load(p.parent)
    kw: dict[str, Any] = {}
    uc = _cols_filter(columns)
    if uc is not None:
        kw["usecols"] = uc
    start = 0
    if rows is not None:
        start = rows.start
        if start:
            kw["skiprows"] = range(1, start + 1)
        kw["nrows"] = max(0, len(rows))
    if hash_into is not None:
        raw = p.read_bytes()
        hash_into.update(raw)
        df = FIO.read(raw, schema, **kw)
    else:
        df = FIO.read(p, schema, **kw)
    if start:
        df.index = pd.RangeIndex(start, start + len(df))
    return df


def read_csv_path(path: str | Path, columns: Any = None) -> pd.DataFrame:
    """A data.csv named by path (old request files carry paths): `read(LegacyPath(path))`."""
    return read(LegacyPath(str(path)), columns)


def columns(ref: InputRef) -> list[ColumnInfo]:
    if legacy_version(ref):
        ref = Table(legacy_version(ref))
    if isinstance(ref, (Table, JoinFrame)) and is_parquet(ref):
        return [ColumnInfo(c["name"], kind_of_type(c["type"])) for c in lake_table(ref).schema]
    p = path_of(ref)
    kinds = FIO.columns(FIO.load(p.parent))
    head = FIO.read(p, FIO.load(p.parent), nrows=200)
    sch = FIO.schema_of(head, kinds)
    return [ColumnInfo(str(c), kinds.get(str(c)) or sch["columns"][str(c)]) for c in head.columns]


def column_names(ref: InputRef) -> list[str]:
    return [c.name for c in columns(ref)]


def n_rows(ref: InputRef) -> int:
    if legacy_version(ref):
        ref = Table(legacy_version(ref))
    if isinstance(ref, (Table, JoinFrame)) and is_parquet(ref):
        return lake_table(ref).n_rows
    p = path_of(ref)
    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def parse_csv(src: Any, schema: dict[str, Any] | None = None, **kw: Any) -> pd.DataFrame:
    """The CSV parse behind every typed read (frame_io.read delegates here): text columns of the schema as strings."""
    return pd.read_csv(src, **kw)


def input_sha256(cfg: dict[str, Any]) -> str | None:
    """P05: the sha256 of what a run read, recomputed now: a Parquet table's table_sha256 from its part bytes, or the
    data.csv bytes of a CSV-era input (what the registry and the data gate compare)."""
    ref = resolve(cfg)
    if isinstance(ref, (Table, JoinFrame)) and is_parquet(ref):  # P14: a join frame: its cache file's sha256
        return hash_table(ref)[0]
    lv = legacy_version(ref)
    if lv is not None:  # P10: an old run re-verifies against the data.csv bytes it read, kept as a legacy_csv blob
        try:
            return hashlib.sha256(legacy_csv_bytes(lv)).hexdigest()
        except FrameError:
            return None
    p = path_of(ref)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(8 * 1024 * 1024):
            h.update(b)
    return h.hexdigest()


def legacy_csv_bytes(version_id: str) -> bytes:
    """P10: the data.csv bytes a migrated version was made from (its legacy_csv blob)."""
    from .lake.migrate import legacy_blob

    b = legacy_blob(_db(), version_id)
    if not b:
        raise FrameError(f"{version_id} has no legacy CSV blob.")
    st = _store()
    try:
        with st.open(b["key"]) as f:
            return f.read()
    except FileNotFoundError as e:
        raise FrameError(f"The legacy CSV of {version_id} ({b['sha256'][:12]}…) is missing from the lake.") from e


def data_identity(version_id: str, table: str = PRIMARY_TABLE) -> str:
    """P08: a short, stable identity of a version table's rows, for caches keyed on content (table_stats, diff_cache,
    the Data Doctor's label job): `table_sha256` for a Parquet table; for a CSV-era one, `csv:` + the stored data.csv
    sha256 (else its size and mtime, for a version that was never hashed). Never opens a Parquet file."""
    ref = Table(version_id, table)
    if is_parquet(ref):
        return lake_table(ref).table_sha256
    try:
        with _db().connect() as con:
            r = con.execute("SELECT COALESCE(legacy_sha256, sha256) FROM datasets WHERE id = ?", (version_id,)).fetchone()
    except Exception:  # noqa: BLE001
        r = None
    if r and r[0]:
        return f"csv:{r[0]}"
    st = path_of(ref).stat()
    return f"csv:{st.st_size}:{int(st.st_mtime)}"


def export_schema(version_id: str) -> dict[str, Any]:
    """§4.1: the `schema.json` written into every export zip: the version's tables and their column types, so the file
    reads back with the types it was trained on (a ZIP '02139' column is VARCHAR, not a number). On the CSV store there
    is one table, `data` (data.csv); with Parquet (P05) this is the manifest's tables and schemas."""
    from .lake.values import type_of_kind

    ref = Table(version_id)
    if is_parquet(ref):  # P05: the committed table's schema, with each column's stored type and the reason it was chosen
        lt = lake_table(ref)
        return {"format": "ensemble.export_schema/1", "version_id": version_id, "read_mode": "engine_v1",
                "tables": [{"name": lt.name, "file": "data.csv", "rows": lt.n_rows, "table_sha256": lt.table_sha256,
                            "columns": [{"name": c["name"], "kind": kind_of_type(c["type"]), "type": c["type"],
                                         "nullable": c.get("nullable"), **({"reason": c["reason"]} if c.get("reason") else {})}
                                        for c in lt.schema]}],
                "source": "the version's manifest (Parquet)"}
    stored = FIO.load(path_of(ref).parent) is not None
    return {"format": "ensemble.export_schema/1", "version_id": version_id, "read_mode": "engine_v1",
            "tables": [{"name": PRIMARY_TABLE, "file": "data.csv", "rows": n_rows(ref),
                        "columns": [{"name": c.name, "kind": c.kind, "type": type_of_kind(c.kind)} for c in columns(ref)]}],
            "source": "schema.json" if stored else "inferred (the version predates DL4's schema.json)"}


# --------------------------------------------------------------------------- P20: the masked scan


def scan(version_id: str, table: str | None = None, *, mask_for: Any):
    """P20 (§6.3): the one DuckDB relation over a version table for every read that **shows** rows: the grid, rows
    pages and lookups, filter preview, diff samples, export and the agent's CSV. `mask_for` is a `lake.mask.Mask`
    (`mask.for_request(...)` for a request, `mask.for_version(...)` for the masked view) or `"masked"` (the same as
    `for_version`). It is keyword-only and has no default, so no caller gets rows without deciding about masking.
    Returns a `lake.pages.Source` whose `rel` masks the PII columns in SQL (a WHERE, a search or a sort sees only
    masked values; masked columns are VARCHAR). Training and the engine read unmasked through `read`."""
    from .lake import mask as M
    from .lake import pages as PG

    src = PG.source(version_id, table)
    m = M.for_version(version_id, src.table) if mask_for == "masked" else mask_for
    if not isinstance(m, M.Mask):
        raise FrameError("scan(): mask_for is a lake.mask.Mask or 'masked'.")
    return PG.masked(src, m)


def scan_csv(src: Any, dest: str | Path | None = None, *, drop: Iterable[str] = (), limit: int | None = None) -> bytes | Path:
    """A scanned (masked) relation written as CSV by DuckDB's COPY, in __row_id order, header on: to `dest`, or
    returned as bytes. The masking happens inside the COPY, never after (C §3 #18)."""
    import tempfile

    from .lake import duck
    from .lake import pages as PG

    drop = set(drop)
    cols = ", ".join(PG.q(c) for c in src.names if c not in drop)
    lim = f" LIMIT {int(limit)}" if limit else ""
    out = Path(dest) if dest is not None else Path(tempfile.mkstemp(suffix=".csv")[1])
    with duck.job() as con:
        con.execute("SET preserve_insertion_order = true")
        for name, obj in (src.bind or {}).items():
            con.register(name, obj)
        con.execute(f"COPY (SELECT {cols} FROM {src.rel} ORDER BY {PG.q(PG.ROW_ID)}{lim}) TO '{str(out).replace(chr(39), chr(39) * 2)}'"
                    " (FORMAT csv, HEADER true)")
    if dest is not None:
        return out
    try:
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def read_artifact(path: str | Path, **kw: Any) -> pd.DataFrame:
    """A CSV the app wrote itself (run outputs, reference tables): no schema, read as written."""
    return pd.read_csv(path, **kw)


# --------------------------------------------------------------------------- user files


def parse_text(text: str, sep: str, name: str = "", *, fix_hint: str = "") -> pd.DataFrame:
    """Decoded CSV text with the typed import rules: ';' files with decimal commas, codes with leading zeros ('02139',
    '007') kept as text (`attrs["kept_as_text"]`). Errors are `DatasetError` with a plain message, never pandas' text.
    Shared by the dataset upload (`datasets.read_upload`) and user batch files (`read_upload`)."""
    from .datasets import DatasetError

    dec = "," if sep == ";" else "."
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, decimal=dec)
    except pd.errors.EmptyDataError as e:
        raise DatasetError("The file has no header row.") from e
    except pd.errors.ParserError as e:
        m = re.search(r"Expected (\d+) fields in line (\d+), saw (\d+)", str(e))
        if m:
            raise DatasetError(f"Line {m[2]} has {m[3]} fields but the header has {m[1]}.{fix_hint}") from e
        raise DatasetError(f"Could not read {Path(name).name or 'the file'} as CSV.") from e
    try:
        raw = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=True)
        keep = FIO.leading_zero_columns(raw, df)
    except Exception:  # noqa: BLE001 - only a refinement of the typed read
        keep = []
    for c in keep:
        df[c] = raw[c].where(raw[c].notna(), None).astype(object)
    df.attrs["kept_as_text"] = keep
    return df


def read_upload(src: Any, *, filename: str | None = None, kind: str = "batch", sep: str | None = None) -> pd.DataFrame:
    """A user's file (bytes, a path or a buffer) with the typed import rules: header required, the encoding and the
    separator detected, codes with leading zeros ('02139', '007') kept as text, ';' files read with decimal commas.
    `kind` is 'batch' (rows to score / compare) or 'labels' (an annotation upload). Parquet is read as stored; an
    Excel workbook (.xlsx) is read from its first sheet, with the same leading-zero rule."""
    from .datasets import DatasetError, decode_upload, sniff_sep

    name = filename or (str(src) if isinstance(src, (str, Path)) else "")
    if isinstance(src, (str, Path)):
        content = Path(src).read_bytes()
    elif isinstance(src, (bytes, bytearray)):
        content = bytes(src)
    else:
        content = src.read()
    if re.search(r"\.parquet$", name, re.I) or content[:4] == b"PAR1":
        try:
            df = pd.read_parquet(io.BytesIO(content))
        except Exception as e:  # noqa: BLE001
            raise DatasetError(f"Could not read {Path(name).name or 'the file'} as Parquet.") from e
    elif re.search(r"\.xlsx$", name, re.I) or (content[:2] == b"PK" and not re.search(r"\.(csv|tsv|txt)$", name, re.I)):
        try:
            raw = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype=str)
            df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        except Exception as e:  # noqa: BLE001
            raise DatasetError(f"Could not read {Path(name).name or 'the file'} as an Excel workbook.") from e
        keep = FIO.leading_zero_columns(raw, df)
        for c in keep:
            df[c] = raw[c].where(raw[c].notna(), None).astype(object)
        df.attrs["kept_as_text"] = keep
    else:
        if not content.strip():
            raise DatasetError("The file is empty.")
        text, _enc = decode_upload(content)
        if sep is None:
            sep = "\t" if name.lower().endswith(".tsv") else sniff_sep(text)
        df = parse_text(text, sep, name)
    df.attrs.setdefault("kept_as_text", [])
    df.attrs["upload_kind"] = kind
    return df


def align_to(df: pd.DataFrame, ref: InputRef) -> pd.DataFrame:
    """A user file's columns cast to the kinds of the table it is compared with or scored against: a text column of
    the table stays text in the file ('02139', never 2139), a number column becomes numbers."""
    kinds = {c.name: c.kind for c in columns(ref)}
    out = df.copy()
    for c in out.columns:
        k = kinds.get(str(c))
        if k == "text" and out[c].dtype.kind != "O":
            out[c] = out[c].map(lambda v: None if pd.isna(v) else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))).astype(object)
        elif k in ("integer", "number") and out[c].dtype.kind == "O":
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


# --------------------------------------------------------------------------- P14: the one join code path (§5.8)


def _qi(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def key_family(t: str) -> str:
    """F-X3b (M6): the family a join key compares in. 'number' (integers and decimals: compared as DOUBLE), 'temporal'
    (DATE and TIMESTAMP without a time zone: compared as TIMESTAMP, so 2024-01-05 = 2024-01-05 00:00:00),
    'temporal_tz' (TIMESTAMP WITH TIME ZONE: only with itself), 'bool', 'text' (VARCHAR), else 'other:<TYPE>'. Two keys
    of different families never compare (400 at validation); nothing is compared as text across types."""
    u = (t or "").upper().strip()
    if u in ("TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"):
        return "temporal_tz"
    if u == "DATE" or u.startswith("TIMESTAMP"):
        return "temporal"
    k = kind_of_type(t)
    if k in ("integer", "number"):
        return "number"
    if k == "bool":
        return "bool"
    if u in ("VARCHAR", "TEXT", "STRING") or u.startswith("VARCHAR"):
        return "text"
    return "other:" + u


def is_date_type(t: str) -> bool:
    """F-X3b (M5): a column a date window can use (DATE or a TIMESTAMP without a time zone)."""
    return key_family(t) == "temporal"


def _key_expr(alias: str, col: str, typ: str, other: str) -> str:
    """One side of a join condition. Equal types compare as stored; otherwise both sides are cast to their family's
    type (DOUBLE for numbers, TIMESTAMP for DATE against TIMESTAMP) so DuckDB never guesses an implicit cast. Mixed
    families are refused by validation before SQL is built (F-X3b M6: never VARCHAR across types)."""
    x = f"{alias}.{_qi(col)}"
    if (typ or "").upper() == (other or "").upper():
        return x
    f = key_family(typ)
    if f == "number":
        return f"CAST({x} AS DOUBLE)"
    if f == "temporal" and key_family(other) == "temporal":
        return f"CAST({x} AS TIMESTAMP)"
    return f"CAST({x} AS VARCHAR)"  # same family, other spelling (VARCHAR(n)); validation refuses the rest


def _agg_expr(a: dict[str, Any], src: str, window: str | None) -> str:
    col = f"{src}.{_qi(a['column'])}" if a.get("column") else None
    fn = a["fn"]
    e = {"count": f"count({col})" if col else "count(*)", "count_distinct": f"count(DISTINCT {col})",
         "sum": f"sum(CAST({col} AS DOUBLE))", "mean": f"avg(CAST({col} AS DOUBLE))",
         "min": f"min({col})", "max": f"max({col})"}[fn]
    if window:  # FILTER belongs to the aggregate itself
        e += f" FILTER (WHERE {window})"
    return f"CAST({e} AS DOUBLE)" if fn in ("sum", "mean") else e


def _window_cond(w: dict[str, Any], src: str, grain: str) -> str:
    """paid_on within `days` of app_date: days < 0 looks back [rel + days, rel); days > 0 looks ahead (rel, rel + days]."""
    d = f"TRY_CAST({src}.{_qi(w['date'])} AS DATE)"
    r = f"TRY_CAST({grain}.{_qi(w['relative_to'])} AS DATE)"
    n = int(w["days"])
    return f"{d} >= {r} + ({n}) AND {d} < {r}" if n < 0 else f"{d} > {r} AND {d} <= {r} + ({n})"


def join_output_columns(spec: dict[str, Any], inputs: list[LakeTable]) -> list[tuple[str, str, str]]:
    """(output name, origin 'grain' | 'join:<i>', source column or agg fn) of every frame column after __row_id."""
    out = [(c["name"], "grain", c["name"]) for c in inputs[0].schema if c["name"] != "__row_id"]
    for i, j in enumerate(spec["joins"]):
        lt = inputs[i + 1]
        if j["type"] == "aggregate":
            out += [(j.get("prefix", "") + a["name"], f"join:{i}", a["fn"]) for a in j["aggs"]]
        else:
            right = {r for _, r in j["on"]}
            cols = j.get("columns") or [c["name"] for c in lt.schema if c["name"] not in right and c["name"] != "__row_id"]
            out += [(j["prefix"] + c, f"join:{i}", c) for c in cols]
    return out


def join_sql(spec: dict[str, Any], inputs: list[LakeTable]) -> str:
    """The frame's SELECT: the grain's rows (every one, in __row_id order) LEFT JOINed to each n:1 table and to each
    aggregate (GROUP BY the key; GROUP BY the grain row when a window needs the grain's date). No free SQL: every name
    is a validated column, quoted."""
    g = inputs[0]
    gtypes = {c["name"]: c["type"] for c in g.schema}
    sel = ["g.__row_id"] + [f"g.{_qi(c['name'])}" for c in g.schema if c["name"] != "__row_id"]
    frm = [f"{lake_sql(g)} g"]
    for i, j in enumerate(spec["joins"]):
        lt = inputs[i + 1]
        jtypes = {c["name"]: c["type"] for c in lt.schema}
        a = f"j{i}"
        if j["type"] == "aggregate":
            windowed = any(x.get("window") for x in j["aggs"])
            if windowed:  # per grain row: the window is relative to the grain row's own date
                on = " AND ".join(f"{_key_expr('g2', l, gtypes[l], jtypes[r])} = {_key_expr('p', r, jtypes[r], gtypes[l])}"
                                  for l, r in j["on"])
                aggs = ", ".join(f"{_agg_expr(x, 'p', _window_cond(x['window'], 'p', 'g2') if x.get('window') else None)}"
                                 f" AS {_qi('a' + str(k))}" for k, x in enumerate(j["aggs"]))
                frm.append(f"LEFT JOIN (SELECT g2.__row_id AS __gid, {aggs} FROM {lake_sql(g)} g2 JOIN {lake_sql(lt)} p"
                           f" ON {on} GROUP BY g2.__row_id) {a} ON {a}.__gid = g.__row_id")
            else:
                keys = ", ".join(f"p.{_qi(r)} AS {_qi('k' + str(k))}" for k, (_, r) in enumerate(j["on"]))
                grp = ", ".join(f"p.{_qi(r)}" for _, r in j["on"])
                nn = " AND ".join(f"p.{_qi(r)} IS NOT NULL" for _, r in j["on"])
                aggs = ", ".join(f"{_agg_expr(x, 'p', None)} AS {_qi('a' + str(k))}" for k, x in enumerate(j["aggs"]))
                on = " AND ".join(f"{_key_expr('g', l, gtypes[l], jtypes[r])} = {_key_expr(a, 'k' + str(k), jtypes[r], gtypes[l])}"
                                  for k, (l, r) in enumerate(j["on"]))
                frm.append(f"LEFT JOIN (SELECT {keys}, {aggs} FROM {lake_sql(lt)} p WHERE {nn} GROUP BY {grp}) {a} ON {on}")
            for k, x in enumerate(j["aggs"]):
                v = f"{a}.{_qi('a' + str(k))}"
                if x["fn"] in ("count", "count_distinct"):
                    v = f"COALESCE({v}, 0)"  # no joined row: 0, not missing
                sel.append(f"{v} AS {_qi(j.get('prefix', '') + x['name'])}")
        else:
            right = {r for _, r in j["on"]}
            cols = j.get("columns") or [c["name"] for c in lt.schema if c["name"] not in right and c["name"] != "__row_id"]
            on = " AND ".join(f"{_key_expr('g', l, gtypes[l], jtypes[r])} = {_key_expr(a, r, jtypes[r], gtypes[l])}"
                              for l, r in j["on"])
            frm.append(f"LEFT JOIN {lake_sql(lt)} {a} ON {on}")
            sel += [f"{a}.{_qi(c)} AS {_qi(j['prefix'] + c)}" for c in cols]
    return f"SELECT {', '.join(sel)} FROM {' '.join(frm)} ORDER BY g.__row_id"


class GrainError(FrameError):
    """The frame does not have exactly the grain's rows (§5.8's assert): it is refused, never cached."""


def materialise(spec: dict[str, Any], inputs: list[LakeTable], out: Path) -> dict[str, Any]:
    """P14 (§5.8): **the only join code path** (training, profiling, export and batch scoring read what it writes).
    Streams the frame from DuckDB to one Parquet file at `out` (a job connection: its memory limit and spill cap, never a
    pandas frame), sorted by the grain's __row_id. Then asserts count(frame) = count(grain) and
    count(DISTINCT __row_id) = count(*), and that the ids are strictly increasing; otherwise GrainError."""
    import time

    import numpy as np
    import pyarrow.parquet as pq

    from .lake import duck

    t0 = time.perf_counter()
    sql = join_sql(spec, inputs)
    target = str(out).replace("'", "''")
    with duck.job() as con:
        con.execute("SET preserve_insertion_order=true")  # the COPY keeps the ORDER BY (job connections turn it off)
        con.execute(f"COPY ({sql}) TO '{target}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)")
        n, nd = con.execute(f"SELECT count(*), count(DISTINCT __row_id) FROM read_parquet('{target}')").fetchall()[0]
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{target}')").fetchall()
    g = inputs[0]
    ids = pq.read_table(str(out), columns=["__row_id"]).column(0).to_numpy(zero_copy_only=False)
    ordered = bool(len(ids) < 2 or np.all(np.diff(ids) > 0))
    if int(n) != g.n_rows or int(nd) != int(n) or not ordered:
        raise GrainError(f"The join does not keep the grain: {g.name} has {g.n_rows:,} rows, the frame {int(n):,} "
                         f"({int(nd):,} distinct __row_id{'' if ordered else ', not in __row_id order'}). A joined table "
                         "repeats a key: declare it n:1 only when its key is unique, or aggregate it.")
    schema = [{"name": r[0], "type": r[1]} for r in desc if r[0] != "__row_id"]
    return {"n_rows": int(n), "schema": schema, "seconds": round(time.perf_counter() - t0, 3)}


def compare_frames(a: str | Path, b: str | Path) -> dict[str, Any]:
    """F-X3b: two frame files compared by __row_id, every cell (IS DISTINCT FROM), as registration parity compares
    the training and serving frames. {ok, rows, ids_unmatched, columns_equal, types_equal, mismatched_cells, summary}."""
    from .lake import duck

    pa_, pb = str(a).replace("'", "''"), str(b).replace("'", "''")
    with duck.job() as con:
        da = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{pa_}')").fetchall()}
        db_ = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{pb}')").fetchall()}
        common = [c for c in da if c in db_ and c != "__row_id"]
        diffs = ", ".join(f"sum(CASE WHEN t.{_qi(c)} IS DISTINCT FROM s.{_qi(c)} THEN 1 ELSE 0 END)" for c in common)
        row = con.execute(
            f"SELECT (SELECT count(*) FROM read_parquet('{pa_}')), (SELECT count(*) FROM read_parquet('{pb}')),"
            f" sum(CASE WHEN t.__row_id IS NULL OR s.__row_id IS NULL THEN 1 ELSE 0 END){', ' + diffs if diffs else ''}"
            f" FROM read_parquet('{pa_}') t FULL OUTER JOIN read_parquet('{pb}') s ON t.__row_id = s.__row_id").fetchone()
    na, nb, unmatched = int(row[0]), int(row[1]), int(row[2] or 0)
    bad = {c: int(v or 0) for c, v in zip(common, row[3:]) if v}
    cols_eq, types_eq = list(da) == list(db_), all(da[c] == db_[c] for c in common)
    problems = []
    if na != nb or unmatched:
        problems.append(f"rows differ: {na:,} vs {nb:,}, {unmatched:,} __row_id without a partner")
    if not cols_eq:
        problems.append("the columns differ: " + ", ".join(sorted(set(da) ^ set(db_)) or ["order"]))
    if not types_eq:
        problems.append("types differ: " + ", ".join(f"{c} {da[c]} vs {db_[c]}" for c in common if da[c] != db_[c]))
    if bad:
        problems.append("cells differ: " + ", ".join(f"{c} ({n:,} rows)" for c, n in bad.items()))
    return {"ok": not problems, "rows": {"cache": na, "rebuilt": nb, "ids_unmatched": unmatched}, "columns_equal": cols_eq,
            "types_equal": types_eq, "mismatched_cells": bad,
            "summary": "cache = the frame the pinned inputs join to (every cell)" if not problems else "; ".join(problems)}


def run_artifact_csv(path: Path | str, **kw: Any) -> pd.DataFrame:
    """F-X5b (grep gate 1): a CSV a run wrote into its own run dir (e.g. scorecard/scorecard.csv), read back. Not a
    dataset version: versions are read with read() / scan()."""
    return pd.read_csv(path, **kw)
