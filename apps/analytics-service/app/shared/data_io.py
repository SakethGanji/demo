"""Cross-cutting data I/O — loading, exporting, conversion, metadata extraction."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from fastapi import HTTPException, UploadFile

from app.infra.config import settings
from app.shared.utils.sql import quote_ident, safe_value


def _read_expr(path: Path) -> str:
    """Return the DuckDB read expression for a file path."""
    suffix = path.suffix.lower()
    # Quote the path for SQL safety
    escaped = str(path).replace("'", "''")
    if suffix == ".csv":
        return f"read_csv_auto('{escaped}')"
    elif suffix == ".parquet":
        return f"read_parquet('{escaped}')"
    else:
        raise HTTPException(400, f"Unsupported file format: {suffix}")


def _connect_s3() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with httpfs loaded and S3 credentials configured."""
    from app.infra.db.storage import duckdb_s3_statements

    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
    except duckdb.Error:
        pass  # offline is fine if the extension is already installed
    conn.execute("LOAD httpfs")
    for stmt in duckdb_s3_statements():
        conn.execute(stmt)
    return conn


def _load_s3(uri: str) -> duckdb.DuckDBPyConnection:
    """Create view ``df`` over an s3:// parquet/CSV object (lazy, via httpfs)."""
    suffix = "." + uri.rsplit(".", 1)[-1].lower() if "." in uri.rsplit("/", 1)[-1] else ""
    escaped = uri.replace("'", "''")
    if suffix == ".parquet":
        expr = f"read_parquet('{escaped}')"
    elif suffix == ".csv":
        expr = f"read_csv_auto('{escaped}')"
    else:
        raise HTTPException(400, f"Unsupported object-store format: {suffix or uri}. Supported: .csv, .parquet")

    conn = _connect_s3()
    try:
        conn.execute(f"CREATE VIEW df AS SELECT * FROM {expr}")
    except duckdb.Error as e:
        conn.close()
        raise HTTPException(404, f"File not found or unreadable: {uri} ({type(e).__name__})")
    return conn


def load_data(
    file_path: str | None = None,
    data: list[dict[str, Any]] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Load data into a DuckDB connection as view/table ``df``.

    CSV and Parquet files are loaded as views (lazy — DuckDB reads only what
    queries touch; ``s3://`` URIs stream via httpfs). Excel and inline JSON are
    materialized as tables since they need pandas conversion.
    """
    if file_path and file_path.startswith("s3://"):
        return _load_s3(file_path)

    conn = duckdb.connect()

    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        if path.is_dir():
            # Directory of parquet files (partitioned / hive-style)
            glob_expr = str(path / "**/*.parquet").replace("'", "''")
            conn.execute(f"CREATE VIEW df AS SELECT * FROM read_parquet('{glob_expr}', hive_partitioning=true)")
        else:
            suffix = path.suffix.lower()
            if suffix in (".csv", ".parquet"):
                conn.execute(f"CREATE VIEW df AS SELECT * FROM {_read_expr(path)}")
            elif suffix in (".xlsx", ".xls"):
                pdf = pd.read_excel(path, engine="openpyxl")
                conn.execute("CREATE TABLE df AS SELECT * FROM pdf")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file format: {suffix}. Supported: .csv, .parquet, .xlsx, .xls",
                )
    elif data:
        pdf = pd.DataFrame(data)
        conn.execute("CREATE TABLE df AS SELECT * FROM pdf")
    else:
        raise HTTPException(
            status_code=400, detail="Either file_path or data must be provided"
        )

    return conn


def export_dataframe(
    conn_or_df, output_path: str, output_format: str, table_name: str = "df",
) -> str:
    """Write data to file. Uses DuckDB COPY for CSV/Parquet (fast), pandas for Excel."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = output_format.lower()
    qtable = quote_ident(table_name)
    if isinstance(conn_or_df, pd.DataFrame):
        df = conn_or_df
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt in ("xlsx", "excel"):
            df.to_excel(path, index=False, engine="openpyxl")
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            raise HTTPException(400, f"Unsupported format: {fmt}")
    else:
        conn = conn_or_df
        if fmt == "csv":
            conn.execute(f"COPY {qtable} TO '{path}' (FORMAT CSV, HEADER)")
        elif fmt == "parquet":
            conn.execute(f"COPY {qtable} TO '{path}' (FORMAT PARQUET)")
        elif fmt in ("xlsx", "excel"):
            df = conn.execute(f"SELECT * FROM {qtable}").fetchdf()
            df.to_excel(path, index=False, engine="openpyxl")
        else:
            raise HTTPException(400, f"Unsupported format: {fmt}")
    return str(path)


# ---------------------------------------------------------------------------
# Column & sheet name normalization + schema capture
# ---------------------------------------------------------------------------

# Names pandas/DuckDB invent for blank headers ("Unnamed: 3", "column0", …).
_SYNTHETIC_COLUMN = re.compile(r"^(unnamed(:\s*\d+)?|column\d*)$", re.IGNORECASE)

DEFAULT_SHEET_NAME = "data"  # synthetic sheet name for non-Excel sources

# Bumped whenever schema extraction/normalization rules change, so stored
# schema_json rows can be told apart by the code that produced them.
SCHEMA_EXTRACTOR_VERSION = "1"

# Bumped whenever conversion behavior changes (recorded in source provenance).
CONVERSION_VERSION = "2"


def normalize_sheet_key(sheet_name: str) -> str:
    """Stable identifier for a sheet — the cross-version diff join key.

    Must stay in sync with the SQL backfill in the sheets migration:
    lowercased, non-alphanumeric runs collapsed to ``_``, trimmed.
    """
    key = re.sub(r"[^a-z0-9]+", "_", sheet_name.lower()).strip("_")
    return key or "sheet"


def normalize_sheet_keys(sheet_names: list[str]) -> list[str]:
    """Unique sheet keys for a workbook, in order.

    Distinct names can normalize to the same key ("Q-1" and "Q.1" → q_1);
    collisions get a deterministic ``_2``/``_3`` suffix so no sheet is ever
    silently unaddressable.
    """
    keys: list[str] = []
    used: set[str] = set()
    for name in sheet_names:
        base = normalize_sheet_key(name)
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}_{n}"
        used.add(candidate)
        keys.append(candidate)
    return keys


def parsing_provenance(source_format: str) -> dict[str, Any]:
    """Parser/conversion identity recorded in version ``source`` JSONB.

    Lets two byte-identical uploads that produced different canonical
    artifacts be explained by parser or option changes.
    """
    import openpyxl

    if source_format in ("xlsx", "xls"):
        parser = {"name": "pandas+openpyxl", "pandas": pd.__version__,
                  "openpyxl": openpyxl.__version__}
        options = {
            "include_hidden_sheets": True,
            "header_row": 0,
            "empty_sheet_policy": "skip",
            "formula_mode": "cached_values",
            "merged_cell_policy": "pandas_default",
        }
    elif source_format == "inline_json":
        parser = {"name": "pandas+duckdb", "pandas": pd.__version__,
                  "duckdb": duckdb.__version__}
        options = {}
    else:
        parser = {"name": "duckdb", "duckdb": duckdb.__version__}
        options = {"csv_dialect": "read_csv_auto"} if source_format == "csv" else {}

    return {
        "format": source_format,
        "parser": parser,
        "conversion_version": CONVERSION_VERSION,
        "options": options,
    }


def manifest_fingerprint(sheet_rows: list[dict[str, Any]]) -> str | None:
    """sha256 over the ordered (sheet_key, sheet_index, checksum) manifest.

    This is the version's canonical content identity — two versions with equal
    manifest checksums hold identical sheet artifacts in identical order.
    None if any sheet lacks a checksum (identity would be incomplete).
    """
    if not sheet_rows or any(not r.get("checksum") for r in sheet_rows):
        return None
    entries = [
        {"sheet_key": r["sheet_key"], "sheet_index": r["sheet_index"], "checksum": r["checksum"]}
        for r in sorted(sheet_rows, key=lambda r: r["sheet_index"])
    ]
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def normalize_column_names(names: list[Any]) -> list[str]:
    """Normalize raw header cells to unique snake_case column names.

    Blank/synthetic headers become ``column_{position}``; duplicates get a
    ``_2``/``_3`` suffix in order of appearance. Originals are kept alongside
    by callers — this never renames data, it is metadata only.
    """
    normalized: list[str] = []
    used: set[str] = set()
    for pos, raw in enumerate(names):
        cell = "" if raw is None else str(raw).strip()
        if not cell or _SYNTHETIC_COLUMN.match(cell):
            base = f"column_{pos}"
        else:
            base = re.sub(r"[^a-z0-9]+", "_", cell.lower()).strip("_") or f"column_{pos}"
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}_{n}"
        used.add(candidate)
        normalized.append(candidate)
    return normalized


def build_sheet_schema(
    described: list[tuple],
    original_names: list[Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Build (schema columns, fingerprint) from DuckDB DESCRIBE rows.

    ``described`` rows are (column_name, column_type, null, …) as returned by
    ``DESCRIBE``. ``original_names`` are the raw header cells when the physical
    parquet names were mangled (pandas dedup, blank headers); falls back to the
    physical names. The fingerprint hashes the *normalized* schema — equal
    fingerprints mean identical shape (names, types, nullability, order).
    """
    physical = [r[0] for r in described]
    originals = (
        list(original_names)
        if original_names is not None and len(original_names) == len(physical)
        else list(physical)
    )
    normalized = normalize_column_names(originals)

    cleaned = ["" if o is None else str(o).strip() for o in originals]
    header_counts: dict[str, int] = {}
    for c in cleaned:
        if c:
            header_counts[c] = header_counts.get(c, 0) + 1

    columns = [
        {
            "name": physical[i],
            "original_name": cleaned[i],
            "normalized_name": normalized[i],
            "dtype": described[i][1],
            "nullable": (described[i][2] or "YES") != "NO",
            "position": i,
            # Flags for diff/UI: was the header duplicated in the source, and
            # was the name invented because the header was blank/synthetic?
            "header_was_duplicated": header_counts.get(cleaned[i], 0) > 1,
            "generated_name": not cleaned[i] or bool(_SYNTHETIC_COLUMN.match(cleaned[i])),
        }
        for i in range(len(physical))
    ]
    fingerprint = schema_fingerprint(columns)
    return columns, fingerprint


def schema_fingerprint(columns: list[dict[str, Any]]) -> str:
    """sha256 over the normalized schema (name, dtype, nullability, in order)."""
    canonical = [[c["normalized_name"], c["dtype"], c["nullable"]] for c in columns]
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def describe_parquet(parquet_path: str) -> list[tuple]:
    """DESCRIBE a parquet file (local path or s3:// URI) with a throwaway connection."""
    escaped = str(parquet_path).replace("'", "''")
    conn = _connect_s3() if str(parquet_path).startswith("s3://") else duckdb.connect()
    try:
        return conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}')").fetchall()
    finally:
        conn.close()


@dataclass
class SheetInfo:
    """Metadata for a single Excel sheet converted to parquet."""
    name: str
    parquet_path: str
    row_count: int
    column_count: int
    is_default: bool = False
    visibility: str = "visible"  # visible | hidden | very_hidden
    original_columns: list[Any] | None = None  # raw header cells, pre-mangling


@dataclass
class ConversionResult:
    """Result of converting an uploaded file to parquet."""
    conn: duckdb.DuckDBPyConnection
    sheets: list[SheetInfo] = field(default_factory=list)
    excluded_sheets: list[str] = field(default_factory=list)  # skipped via include_sheets

    @property
    def is_multi_sheet(self) -> bool:
        return len(self.sheets) > 1

    @property
    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]

    @property
    def default_sheet(self) -> str | None:
        for s in self.sheets:
            if s.is_default:
                return s.name
        return self.sheets[0].name if self.sheets else None


def convert_to_parquet(
    source_path: Path,
    dest_path: Path,
    *,
    sheet_path_fn: callable | None = None,
    include_sheets: set[str] | None = None,
) -> ConversionResult:
    """Convert any supported file to Parquet using DuckDB native readers.

    For Excel files with multiple sheets, each non-empty sheet gets its own
    parquet in the sheets/ directory. The canonical parquet (``dest_path``) is a
    symlink (local) or copy pointing at the first sheet — no data duplication.

    For single-sheet Excel files, the sheet is written directly to
    ``dest_path`` and no sheets/ entry is created.

    Args:
        source_path: Path to source file (csv, parquet, xlsx, xls).
        dest_path: Path for the canonical (main) parquet output.
        sheet_path_fn: Optional callable ``(sheet_name: str) -> str`` returning
            the output path for each sheet parquet. If not provided, all sheets
            are written to ``dest_path`` (last one wins — use only for single-sheet).

    Returns:
        ConversionResult with an open DuckDB connection (view ``df`` pointing at
        the canonical parquet) and sheet metadata for Excel files.
    """
    conn = duckdb.connect()
    suffix = source_path.suffix.lower()
    sheets: list[SheetInfo] = []
    excluded: list[str] = []

    if suffix in (".csv", ".parquet"):
        read_expr = _read_expr(source_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(f"COPY (SELECT * FROM {read_expr}) TO '{dest_path}' (FORMAT PARQUET)")
        conn.execute(f"CREATE VIEW df AS SELECT * FROM read_parquet('{dest_path}')")

    elif suffix in (".xlsx", ".xls"):
        xls = pd.ExcelFile(source_path, engine="openpyxl")
        sheet_names = xls.sheet_names

        # Workbook-level metadata pandas discards: sheet visibility and the raw
        # header cells (pandas mangles duplicates to "a.1" and blanks to
        # "Unnamed: N" — we keep the originals as metadata).
        visibility: dict[str, str] = {}
        raw_headers: dict[str, list[Any]] = {}
        try:
            for ws in xls.book.worksheets:
                state = getattr(ws, "sheet_state", "visible")
                visibility[ws.title] = {
                    "visible": "visible", "hidden": "hidden", "veryHidden": "very_hidden",
                }.get(state, "visible")
                for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                    raw_headers[ws.title] = list(row)
                    break
        except Exception:
            pass  # metadata capture must never fail an ingest

        # First pass: read all non-empty (and included) sheets into memory
        sheet_frames: list[tuple[str, pd.DataFrame]] = []
        for name in sheet_names:
            if include_sheets is not None and name not in include_sheets:
                excluded.append(name)
                continue
            pdf = pd.read_excel(xls, sheet_name=name, engine="openpyxl")
            if not pdf.empty:
                sheet_frames.append((name, pdf))
        xls.close()
        if include_sheets is not None and not sheet_frames:
            raise ValueError(
                f"include_sheets matched no non-empty sheets. Workbook has: {sheet_names}")

        if not sheet_frames:
            # All sheets empty — write empty canonical parquet
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            empty = pd.DataFrame()
            conn.execute("CREATE TABLE _empty AS SELECT * FROM empty")
            conn.execute(f"COPY _empty TO '{dest_path}' (FORMAT PARQUET)")
            conn.execute("DROP TABLE _empty")
        elif len(sheet_frames) == 1:
            # Single sheet — write directly to canonical, no sheets/ dir
            name, pdf = sheet_frames[0]
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            conn.execute("CREATE TABLE _tmp AS SELECT * FROM pdf")
            conn.execute(f"COPY _tmp TO '{dest_path}' (FORMAT PARQUET)")
            conn.execute("DROP TABLE _tmp")
            sheets.append(SheetInfo(
                name=name,
                parquet_path=str(dest_path),
                row_count=len(pdf),
                column_count=len(pdf.columns),
                is_default=True,
                visibility=visibility.get(name, "visible"),
                original_columns=raw_headers.get(name),
            ))
        else:
            # Multiple sheets — each gets its own parquet, canonical = first sheet
            first_sheet_path: str | None = None
            for idx, (name, pdf) in enumerate(sheet_frames):
                is_first = idx == 0

                if sheet_path_fn is not None:
                    sp = sheet_path_fn(name)
                    Path(sp).parent.mkdir(parents=True, exist_ok=True)
                    sheet_conn = duckdb.connect()
                    sheet_conn.execute("CREATE TABLE _s AS SELECT * FROM pdf")
                    sheet_conn.execute(f"COPY _s TO '{sp}' (FORMAT PARQUET)")
                    sheet_conn.close()
                    sheet_parquet_path = str(sp)
                else:
                    sheet_parquet_path = str(dest_path)

                if is_first:
                    first_sheet_path = sheet_parquet_path

                sheets.append(SheetInfo(
                    name=name,
                    parquet_path=sheet_parquet_path,
                    row_count=len(pdf),
                    column_count=len(pdf.columns),
                    is_default=is_first,
                    visibility=visibility.get(name, "visible"),
                    original_columns=raw_headers.get(name),
                ))

            # Canonical parquet = copy/symlink of first sheet
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if first_sheet_path and first_sheet_path != str(dest_path):
                # Symlink avoids data duplication on local storage
                if dest_path.exists() or dest_path.is_symlink():
                    dest_path.unlink()
                dest_path.symlink_to(Path(first_sheet_path).resolve())
            elif not dest_path.exists():
                # Fallback: write first sheet directly
                name, pdf = sheet_frames[0]
                conn.execute("CREATE TABLE _tmp AS SELECT * FROM pdf")
                conn.execute(f"COPY _tmp TO '{dest_path}' (FORMAT PARQUET)")
                conn.execute("DROP TABLE _tmp")

        # Point df view at canonical parquet
        escaped = str(dest_path).replace("'", "''")
        conn.execute(f"CREATE VIEW df AS SELECT * FROM read_parquet('{escaped}')")

    else:
        raise ValueError(f"Unsupported format: {suffix}")

    return ConversionResult(conn=conn, sheets=sheets, excluded_sheets=excluded)


def extract_metadata(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Pull row count, columns, and preview from a DuckDB connection with table/view 'df'."""
    row_count: int = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
    desc_rows = conn.execute("DESCRIBE df").fetchall()
    columns = [{"name": r[0], "dtype": r[1]} for r in desc_rows]
    preview_rows = conn.execute("SELECT * FROM df LIMIT 5").fetchdf()
    preview = [
        {k: safe_value(v) for k, v in row.items()}
        for row in preview_rows.to_dict(orient="records")
    ]
    return {
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "preview": preview,
    }


async def stream_to_disk(upload: UploadFile, dest: Path) -> int:
    """Stream an UploadFile to disk in chunks. Returns total bytes written."""
    total = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await upload.read(settings.upload_chunk_size)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


def make_dataset_id(label: str) -> str:
    ts = int(time.time() * 1000)
    hash_suffix = hashlib.sha256(f"{ts}_{label}".encode()).hexdigest()[:8]
    return f"ds_{ts}_{hash_suffix}"
