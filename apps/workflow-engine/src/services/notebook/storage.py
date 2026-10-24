"""Full row sets of cell results on disk: ``<notebook_storage_dir>/<nb>/<run>/<cell>[.<row>].json``.

The DB keeps a preview (``PREVIEW_ROWS`` rows); ``output_ref`` is the key below the storage root. Keys are built here
from ids the engine generated, and every read re-checks that the resolved path stays under the root.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PREVIEW_ROWS = 50
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def root() -> Path:
    from ...core.config import settings

    return Path(os.environ.get("WORKFLOW_NOTEBOOK_STORAGE_DIR") or settings.notebook_storage_dir)


def _key(notebook_id: str, run_id: str, cell_id: str, row_index: int | None) -> str:
    for part in (notebook_id, run_id, cell_id):
        if not _SAFE.match(part):
            raise ValueError(f"unsafe storage key part {part!r}")
    name = cell_id if row_index is None else f"{cell_id}.{int(row_index)}"
    return f"{notebook_id}/{run_id}/{name}.json"


def _path(key: str) -> Path:
    base = root().resolve()
    p = (base / key).resolve()
    if base != p and base not in p.parents:
        raise ValueError("storage key escapes the storage root")
    return p


def write_rows(notebook_id: str, run_id: str, cell_id: str, row_index: int | None, rows: list[dict[str, Any]],
               columns: list[str] | None = None) -> str:
    key = _key(notebook_id, run_id, cell_id, row_index)
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"columns": columns or [], "rows": rows}, default=str))
    tmp.replace(p)
    return key


def read_rows(key: str) -> dict[str, Any] | None:
    """``{columns, rows}`` or None when the file is gone."""
    try:
        return json.loads(_path(key).read_text())
    except (OSError, ValueError):
        return None


def columns_of(rows: list[dict[str, Any]]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows[:200]:
        if isinstance(r, dict):
            for k in r:
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
    return cols


def preview(rows: list[dict[str, Any]], columns: list[str] | None = None, *, truncated: bool = False,
            cap: int | None = None, **extra: Any) -> dict[str, Any]:
    """The DB preview of a row set: ``{columns, rows<=50, row_count, truncated, cap, ...extra}``.

    ``row_count`` is the size of the full set (in storage when it is over 50 rows); ``truncated`` says the *source* had
    more rows than ``cap`` (e.g. a query stopped at its limit)."""
    cols = columns or columns_of(rows)
    out = {"columns": cols, "rows": rows[:PREVIEW_ROWS], "row_count": len(rows), "truncated": truncated, "cap": cap}
    out.update(extra)
    return out
