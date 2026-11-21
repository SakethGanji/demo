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

`read_artifact` reads files the app itself wrote next to a run (holdout_predictions.csv, scores.csv, reference
tables): they are not datasets and have no schema. It exists so the grep gate (§6.5) has exactly one place to allow.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Union

import pandas as pd

from . import config
from . import frame_io as FIO

MODES = ("engine_v1", "native")
PRIMARY_TABLE = "data"  # the one table of every CSV-era version


# --------------------------------------------------------------------------- refs


@dataclass(frozen=True)
class Table:
    version_id: str
    table: str = PRIMARY_TABLE


@dataclass(frozen=True)
class JoinFrame:
    join_frame_id: str


@dataclass(frozen=True)
class LegacyPath:
    """A data.csv named by path: old run configs (`csv_path`). Read with the schema.json beside it."""
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
    """The CSV-store file behind a ref (the only place that knows the layout `datasets/<id>/data.csv`)."""
    if isinstance(ref, LegacyPath):
        return Path(ref.path)
    if isinstance(ref, Table):
        if ref.table != PRIMARY_TABLE:
            raise FrameError(f"{ref.version_id} has one table ('{PRIMARY_TABLE}'), not '{ref.table}'.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", ref.version_id) or ".." in ref.version_id:
            raise FrameError(f"Bad dataset id {ref.version_id!r}.")
        return _settings().datasets_dir / ref.version_id / "data.csv"
    raise FrameError("Saved joins are read from Parquet join frames (P14); there are none on the CSV store.")


def table(version_id: str, table: str = PRIMARY_TABLE) -> Table:
    return Table(version_id, table)


def version_file(version_id: str) -> Path:
    """The file a CSV-store version is written to (the writers of datasets.py until P07's VersionWriter, hashing and
    run configs' `csv_path` key). Never read it directly: read `Table(version_id)`."""
    return path_of(Table(version_id))


def read_version(version_id: str, columns: Iterable[str] | Callable[[str], bool] | None = None,
                 rows: range | slice | None = None) -> pd.DataFrame:
    """`read(Table(version_id))`: the version's one table, typed (what `DatasetStore.load_frame` used to return)."""
    return read(Table(version_id), columns, rows)


def resolve(cfg: dict[str, Any]) -> InputRef:
    """A run config's input: `input_ref` (new configs), else `csv_path` (every config so far: the exact file the fit
    read, so an old run re-reads what it trained on), else `dataset_id`."""
    ref = from_json(cfg.get("input_ref"))
    if ref is not None:
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
    return to_json(Table(dataset_id))


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
        if isinstance(rows, slice):
            rows = range(rows.start or 0, rows.stop if rows.stop is not None else 2 ** 62, rows.step or 1)
        if rows.step != 1 or rows.start < 0:
            raise FrameError("rows is a contiguous, non-negative range.")
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
    p = path_of(ref)
    kinds = FIO.columns(FIO.load(p.parent))
    head = FIO.read(p, FIO.load(p.parent), nrows=200)
    sch = FIO.schema_of(head, kinds)
    return [ColumnInfo(str(c), kinds.get(str(c)) or sch["columns"][str(c)]) for c in head.columns]


def column_names(ref: InputRef) -> list[str]:
    return [c.name for c in columns(ref)]


def n_rows(ref: InputRef) -> int:
    p = path_of(ref)
    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def parse_csv(src: Any, schema: dict[str, Any] | None = None, **kw: Any) -> pd.DataFrame:
    """The CSV parse behind every typed read (frame_io.read delegates here): text columns of the schema as strings."""
    return pd.read_csv(src, **kw)


def export_schema(version_id: str) -> dict[str, Any]:
    """§4.1: the `schema.json` written into every export zip: the version's tables and their column types, so the file
    reads back with the types it was trained on (a ZIP '02139' column is VARCHAR, not a number). On the CSV store there
    is one table, `data` (data.csv); with Parquet (P05) this is the manifest's tables and schemas."""
    from .lake.values import type_of_kind

    ref = Table(version_id)
    stored = FIO.load(path_of(ref).parent) is not None
    return {"format": "ensemble.export_schema/1", "version_id": version_id, "read_mode": "engine_v1",
            "tables": [{"name": PRIMARY_TABLE, "file": "data.csv", "rows": n_rows(ref),
                        "columns": [{"name": c.name, "kind": c.kind, "type": type_of_kind(c.kind)} for c in columns(ref)]}],
            "source": "schema.json" if stored else "inferred (the version predates DL4's schema.json)"}


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
