"""DL4 (m-e, M-b): a version's column schema, so data.csv reads back with the types it was written with.

A CSV has no types: pandas guesses them on every read, so a ZIP code "02139" came back as 2139 and an int column with
one new row came back as "1.0". Each version now stores `schema.json` beside data.csv:

    {"columns": {"zip": "text", "default": "integer", "income": "number", "flag": "bool"}}

- text      read as strings (leading zeros, ID-like codes and numbers cast to text stay text)
- integer   written without ".0" (a nullable integer while it has missing cells), read as pandas reads integers
- number    floats
- bool      true / false

`read()` applies it; `to_csv_bytes()` writes with it; `schema_of()` derives it from a frame (with the parent's schema
as a hint, so an int column that gained a missing cell stays "integer"). Datasets made before DL4 have no schema.json
and are read exactly as before."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_FILE = "schema.json"
KINDS = ("text", "integer", "number", "bool")
LEADING_ZERO = re.compile(r"^\s*[-+]?0\d")


def kind_of(s: pd.Series, hint: str | None = None) -> str:
    k = s.dtype.kind
    if k == "b":
        return "bool"
    if k in "iu":
        return "integer"
    if k == "f":
        v = s.dropna()
        if hint == "integer" and (v.empty or bool((v == v.round()).all())):
            return "integer"
        return "number"
    return "text"


def schema_of(df: pd.DataFrame, hint: dict[str, str] | None = None, force: dict[str, str] | None = None) -> dict[str, Any]:
    hint, force = hint or {}, force or {}
    return {"columns": {str(c): force.get(c) or kind_of(df[c], hint.get(c)) for c in df.columns}}


def columns(schema: dict[str, Any] | None) -> dict[str, str]:
    return dict((schema or {}).get("columns") or {})


def load(dir_: Path) -> dict[str, Any] | None:
    p = Path(dir_) / SCHEMA_FILE
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


def save(dir_: Path, schema: dict[str, Any]) -> None:
    (Path(dir_) / SCHEMA_FILE).write_text(json.dumps(schema, indent=1))


def _dtypes(schema: dict[str, Any] | None, usecols: Any = None) -> dict[str, Any]:
    return {c: str for c, k in columns(schema).items() if k == "text"}


def read(src: Any, schema: dict[str, Any] | None = None, **kw: Any) -> pd.DataFrame:
    """data.csv (a path, or bytes / a buffer) read with its schema: text columns as strings. A path finds its own
    schema.json beside it when `schema` is not given."""
    if schema is None and isinstance(src, (str, Path)):
        schema = load(Path(src).parent)
    dt = _dtypes(schema)
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    if dt:
        kw["dtype"] = {**dt, **(kw.get("dtype") or {})}
    from .frames import parse_csv  # §6.5 R-G: the one parse lives in frames (frame_io is its private helper)

    return parse_csv(src, schema, **kw)


def prepare(df: pd.DataFrame, schema: dict[str, Any] | None) -> pd.DataFrame:
    """The frame as it is written: integer columns without '.0' (nullable Int64 while a cell is missing), text columns
    as strings ('2139.0' never appears for a number cast to text)."""
    out = df.copy()
    for c, k in columns(schema).items():
        if c not in out.columns:
            continue
        s = out[c]
        if k == "integer" and s.dtype.kind == "f":
            out[c] = s.round().astype("Int64")
        elif k == "text" and s.dtype.kind != "O":
            out[c] = s.map(lambda v: None if pd.isna(v) else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))).astype(object)
    return out


def to_csv_bytes(df: pd.DataFrame, schema: dict[str, Any] | None) -> bytes:
    return prepare(df, schema).to_csv(index=False).encode()


def roundtrip(df: pd.DataFrame, schema: dict[str, Any] | None) -> pd.DataFrame:
    """Exactly what data.csv will read back as."""
    return read(to_csv_bytes(df, schema), schema)


def leading_zero_columns(text_frame: pd.DataFrame, parsed: pd.DataFrame) -> list[str]:
    """Columns pandas read as numbers whose raw text has codes with leading zeros ('02139', '007'): ZIP codes, IDs.
    `text_frame` is the same file read with dtype=str."""
    out = []
    for c in parsed.columns:
        if parsed[c].dtype.kind not in "iuf" or c not in text_frame.columns:
            continue
        raw = text_frame[c].dropna().astype(str)
        if len(raw) and raw.str.match(LEADING_ZERO).any() and not raw.str.contains(r"[.eE]").any():
            out.append(str(c))
    return out
