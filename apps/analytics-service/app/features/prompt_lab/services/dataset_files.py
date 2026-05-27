"""File-backed dataset upload + storage for PromptLab (v4).

The dataset is the unit of input. Each upload is re-encoded to parquet and
written to ``<storage_dir>/promptlab/datasets/<dataset_id>.parquet``. The
``dataset_id`` is content-addressed (``ds_`` + first 12 hex chars of the
sha256) so re-uploading the same bytes is idempotent.

There is no catalog database — the parquet file IS the catalog. Metadata
returned by the upload endpoint is recomputed from the file each call.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException, UploadFile

from app.infra.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_SUFFIXES = (".parquet", ".csv", ".xlsx", ".xls")
_GT_CANDIDATES = ("gt", "GT", "label", "expected")
_SPLIT_CANDIDATES = ("split", "Split", "SPLIT")

_MIN_ROWS = 10
_MAX_UNIQUE_CLASSES = 50

# Sweeper: parquet files older than 24h are removed.
DATASET_FILE_TTL_SECONDS = 24 * 60 * 60


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def promptlab_root() -> Path:
    root = Path(settings.storage_dir) / "promptlab"
    root.mkdir(parents=True, exist_ok=True)
    return root


def datasets_dir() -> Path:
    d = promptlab_root() / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dataset_path(dataset_id: str) -> Path:
    """Resolve ``<datasets_dir>/<dataset_id>.parquet`` with id sanitisation."""
    safe = Path(dataset_id).name
    if not safe or "/" in safe or "\\" in safe:
        raise ValueError(f"invalid dataset_id: {dataset_id!r}")
    if not safe.endswith(".parquet"):
        safe = f"{safe}.parquet"
    return datasets_dir() / safe


def dataset_meta_path(dataset_id: str) -> Path:
    """Sidecar metadata JSON next to the parquet."""
    safe = Path(dataset_id).name
    if not safe or "/" in safe or "\\" in safe:
        raise ValueError(f"invalid dataset_id: {dataset_id!r}")
    if safe.endswith(".parquet"):
        safe = safe[: -len(".parquet")]
    return datasets_dir() / f"{safe}.meta.json"


def load_dataset_meta(dataset_id: str) -> dict[str, Any] | None:
    """Load the sidecar metadata for a dataset, or ``None`` if missing."""
    p = dataset_meta_path(dataset_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _detect_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _read_dataset(blob: bytes, suffix: str) -> pd.DataFrame:
    """Decode ``blob`` as a DataFrame based on file suffix."""
    if suffix == ".parquet":
        return pd.read_parquet(io.BytesIO(blob))
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(blob))
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(blob))
    raise HTTPException(400, f"Unsupported file extension: {suffix}")


def _format_label(suffix: str) -> str:
    if suffix == ".parquet":
        return "parquet"
    if suffix == ".csv":
        return "csv"
    if suffix in (".xlsx", ".xls"):
        return "xlsx"
    return suffix.lstrip(".")


def _sample_values(series: pd.Series, n: int = 3) -> list[Any]:
    out: list[Any] = []
    for v in series.head(n).tolist():
        if pd.isna(v):
            out.append(None)
            continue
        try:
            json.dumps(v, default=str)
            out.append(v)
        except TypeError:
            out.append(str(v))
    return out


def _column_metadata(df: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for col in df.columns:
        series = df[col]
        try:
            n_unique = int(series.nunique(dropna=True))
        except Exception:
            n_unique = -1
        out.append(
            {
                "name": str(col),
                "dtype": str(series.dtype),
                "n_unique": n_unique,
                "samples": _sample_values(series),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def upload_dataset(
    file: UploadFile,
    *,
    name: str | None = None,
    description: str | None = None,
    target_column: str | None = None,
    input_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Validate + persist an uploaded dataset.

    ``target_column`` overrides auto-detection (``gt``/``label``/``expected``);
    required when the dataset uses a non-standard name like ``gt_scenario``.
    ``input_columns`` whitelists which columns the prompt template can see —
    critical for fairness datasets where demographic columns must NOT leak
    into the LLM prompt.

    Returns a dict matching the ``DatasetEntry`` schema.
    """
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            400,
            f"Unsupported file type {suffix!r}; allowed: "
            f"{', '.join(_ALLOWED_SUFFIXES)}",
        )

    blob = await file.read()
    if not blob:
        raise HTTPException(400, "file is empty")

    sha256 = hashlib.sha256(blob).hexdigest()

    try:
        df = _read_dataset(blob, suffix)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"failed to read dataset: {e}")

    columns = [str(c) for c in df.columns]
    if not columns:
        raise HTTPException(422, "dataset has no columns")

    # Resolve target column: explicit > auto-detect.
    if target_column:
        if target_column not in columns:
            raise HTTPException(
                422,
                f"target_column={target_column!r} not in dataset columns {columns}",
            )
        gt_col = target_column
    else:
        gt_col = _detect_column(columns, _GT_CANDIDATES)

    issues: list[str] = []
    if gt_col is None:
        issues.append(
            f"no target column resolved; pass target_column or include one of "
            f"{list(_GT_CANDIDATES)} in the dataset (columns: {columns})"
        )

    # Resolve input columns: explicit > everything except gt/split/target.
    split_col = _detect_column(columns, _SPLIT_CANDIDATES)
    if input_columns:
        missing = [c for c in input_columns if c not in columns]
        if missing:
            raise HTTPException(
                422,
                f"input_columns {missing} not present in dataset (have: {columns})",
            )
        resolved_inputs = list(input_columns)
    else:
        resolved_inputs = [c for c in columns if c != gt_col and c != split_col]

    n_rows = int(len(df))
    if n_rows < _MIN_ROWS:
        issues.append(f"dataset has {n_rows} rows (need at least {_MIN_ROWS})")

    if gt_col is not None:
        gt_series = df[gt_col].dropna()
        if len(gt_series) == 0:
            issues.append(f"target column {gt_col!r} is empty after dropna")
        else:
            gt_unique = int(gt_series.nunique())
            if gt_unique <= 1:
                issues.append(
                    f"target column {gt_col!r} has only {gt_unique} unique values"
                )
            if gt_unique > _MAX_UNIQUE_CLASSES:
                issues.append(
                    f"target column {gt_col!r} has {gt_unique} unique values "
                    f"(max {_MAX_UNIQUE_CLASSES})"
                )

    if issues:
        raise HTTPException(
            422,
            {
                "message": "dataset validation failed",
                "issues": issues,
                "columns": columns,
                "n_rows": n_rows,
            },
        )

    # Dataset id includes schema choices — re-uploading the same bytes with
    # different (target, inputs) produces a distinct id so the eval cache
    # doesn't return stale results.
    schema_sig = hashlib.sha256(
        (
            sha256
            + "\n"
            + (gt_col or "")
            + "\n"
            + ",".join(sorted(resolved_inputs))
        ).encode("utf-8")
    ).hexdigest()
    dataset_id = f"ds_{schema_sig[:12]}"

    detected_classes = sorted(
        {str(v) for v in df[gt_col].dropna().unique().tolist()}
    )
    detected_splits = (
        sorted({str(v) for v in df[split_col].dropna().unique().tolist()})
        if split_col is not None
        else []
    )

    columns_meta = _column_metadata(df)

    storage_path = dataset_path(dataset_id)
    if not storage_path.exists():
        try:
            df.to_parquet(storage_path, index=False)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"failed to persist dataset parquet: {e}")
    else:
        logger.info("dataset %s already present at %s; reusing", dataset_id, storage_path)

    try:
        file_size = int(storage_path.stat().st_size)
    except OSError:
        file_size = 0

    meta: dict[str, Any] = {
        "dataset_id": dataset_id,
        "name": name or filename,
        "description": description or "",
        "n_rows": n_rows,
        "target_column": gt_col,
        "input_columns": resolved_inputs,
        "detected_split_column": split_col,
        "detected_classes": detected_classes,
        "detected_splits": detected_splits,
        "columns": columns_meta,
        "sha256": sha256,
        "file_size_bytes": file_size,
        "storage_path": str(storage_path),
        "format_original": _format_label(suffix),
        "created_at": _utc_now_iso(),
    }

    # Sidecar metadata so the evaluator can read target/inputs back.
    try:
        dataset_meta_path(dataset_id).write_text(json.dumps(meta, default=str))
    except OSError:
        logger.warning("Failed to write dataset sidecar for %s", dataset_id, exc_info=True)

    return meta


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


def sweep_expired_dataset_files(
    *, now: float | None = None, max_age_seconds: int = DATASET_FILE_TTL_SECONDS,
) -> int:
    """Remove parquet files older than ``max_age_seconds``. Returns count removed."""
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed = 0
    d = datasets_dir()
    for p in d.iterdir():
        if not p.is_file() or p.suffix != ".parquet":
            continue
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime > cutoff:
            continue
        try:
            os.unlink(p)
            removed += 1
            logger.info("promptlab dataset sweeper: removed %s", p)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("could not unlink %s", p, exc_info=True)
    return removed
