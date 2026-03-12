"""Cross-cutting data I/O — loading, exporting, conversion, metadata extraction."""

from __future__ import annotations

import hashlib
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


def load_data(
    file_path: str | None = None,
    data: list[dict[str, Any]] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Load data into a DuckDB connection as view/table ``df``.

    CSV and Parquet files are loaded as views (lazy — DuckDB reads only what
    queries touch). Excel and inline JSON are materialized as tables since they
    need pandas conversion.
    """
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


@dataclass
class SheetInfo:
    """Metadata for a single Excel sheet converted to parquet."""
    name: str
    parquet_path: str
    row_count: int
    column_count: int
    is_default: bool = False


@dataclass
class ConversionResult:
    """Result of converting an uploaded file to parquet."""
    conn: duckdb.DuckDBPyConnection
    sheets: list[SheetInfo] = field(default_factory=list)

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

    if suffix in (".csv", ".parquet"):
        read_expr = _read_expr(source_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(f"COPY (SELECT * FROM {read_expr}) TO '{dest_path}' (FORMAT PARQUET)")
        conn.execute(f"CREATE VIEW df AS SELECT * FROM read_parquet('{dest_path}')")

    elif suffix in (".xlsx", ".xls"):
        xls = pd.ExcelFile(source_path, engine="openpyxl")
        sheet_names = xls.sheet_names

        # First pass: read all non-empty sheets into memory
        sheet_frames: list[tuple[str, pd.DataFrame]] = []
        for name in sheet_names:
            pdf = pd.read_excel(xls, sheet_name=name, engine="openpyxl")
            if not pdf.empty:
                sheet_frames.append((name, pdf))
        xls.close()

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

    return ConversionResult(conn=conn, sheets=sheets)


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
