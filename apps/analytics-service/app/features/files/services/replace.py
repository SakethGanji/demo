"""Copy-on-write sheet replacement.

Creates a NEW immutable version where one sheet's data comes from an uploaded
single-table file and every other sheet reuses the current version's parquet
artifacts (no data copied). Lineage records the base version.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.features.library import repo as library_repo
from app.infra.db.storage import DatasetLayout, get_storage
from app.shared.data_io import (
    SCHEMA_EXTRACTOR_VERSION,
    build_sheet_schema,
    convert_to_parquet,
    describe_parquet,
    manifest_fingerprint,
    stream_to_disk,
)
from app.shared.datasets import (
    _find_sheet,
    _get_current_version_or_404,
    ensure_sheet_schema,
    get_version_sheet_rows,
)
from app.shared.scanning import scan_upload

from .. import repo

_ALLOWED = {".csv", ".parquet", ".xlsx", ".xls"}


async def replace_sheet(ds: dict, sheet_name: str, file: UploadFile, principal) -> dict:
    dataset_id = str(ds["id"])
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(400, f"Unsupported file type: {suffix}")

    base_ver = await _get_current_version_or_404(dataset_id)
    base_rows = [await ensure_sheet_schema(base_ver, r)
                 for r in await get_version_sheet_rows(base_ver)]
    target = _find_sheet(base_rows, sheet_name)
    if not target:
        available = ", ".join(r["sheet_name"] for r in base_rows) or "none"
        raise HTTPException(404, f"Sheet not found: {sheet_name} (available: {available})")

    version = await repo.create_version(
        dataset_id, storage_type="temp", status="uploading",
        source={"type": "sheet_replace", "replaced_sheet": target["sheet_key"],
                "base_version": base_ver["version_number"], "filename": file.filename},
    )
    version_id, version_number = str(version["id"]), version["version_number"]
    storage = get_storage()
    layout = DatasetLayout(str(ds["team_id"]), dataset_id, version_number, file.filename)
    layout.ensure_dirs()

    try:
        with tempfile.TemporaryDirectory(prefix="accel_replace_") as td:
            raw = Path(td) / f"raw{suffix}"
            await stream_to_disk(file, raw)
            scan = await scan_upload(raw, filename=file.filename)
            if not scan.ok:
                raise HTTPException(400, f"Upload rejected by scanner: {scan.reason or 'infected'}")
            source_checksum = hashlib.sha256(raw.read_bytes()).hexdigest()

            local_parquet = Path(td) / "sheet.parquet"
            result = convert_to_parquet(raw, local_parquet)
            if result.is_multi_sheet:
                raise HTTPException(
                    400, "Sheet replacement needs a single-table file — this workbook has "
                         f"{len(result.sheets)} sheets")
            row_count = result.conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
            result.conn.close()

            columns, fingerprint = build_sheet_schema(
                describe_parquet(str(local_parquet)),
                result.sheets[0].original_columns if result.sheets else None,
            )
            new_key = layout.sheet_parquet(target["sheet_key"])
            storage.put_file(new_key, local_parquet)
            replaced_row = {
                "sheet_key": target["sheet_key"],
                "sheet_name": target["sheet_name"],
                "sheet_index": target["sheet_index"],
                "visibility": target.get("visibility", "visible"),
                "status": "ready",
                "is_default": bool(target.get("is_default")),
                "storage_key": new_key,
                "row_count": row_count,
                "column_count": len(columns),
                "size_bytes": local_parquet.stat().st_size,
                "checksum": hashlib.sha256(local_parquet.read_bytes()).hexdigest(),
                "schema_json": columns,
                "schema_fingerprint": fingerprint,
                "schema_extractor_version": SCHEMA_EXTRACTOR_VERSION,
            }

        # Copy-on-write: every other sheet points at the base version's parquet.
        new_rows = []
        for r in base_rows:
            if r["sheet_key"] == target["sheet_key"]:
                new_rows.append(replaced_row)
                continue
            if not r.get("storage_key"):
                raise HTTPException(
                    409, f"Sheet '{r['sheet_name']}' has no addressable artifact "
                         f"(pre-Phase-1 version) — re-upload the workbook instead")
            new_rows.append({k: r.get(k) for k in (
                "sheet_key", "sheet_name", "sheet_index", "visibility", "status",
                "is_default", "storage_key", "row_count", "column_count",
                "size_bytes", "checksum", "schema_json", "schema_fingerprint",
                "schema_extractor_version")})

        default_row = next((r for r in new_rows if r.get("is_default")), new_rows[0])
        path = storage.resolve(default_row["storage_key"])
        total_rows = sum(r.get("row_count") or 0 for r in new_rows)
        await repo.complete_version(
            version_id, path=path,
            size_bytes=sum(r.get("size_bytes") or 0 for r in new_rows),
            row_count=total_rows, checksum=default_row.get("checksum"),
            source_checksum=source_checksum,
            manifest_checksum=manifest_fingerprint(new_rows),
            sheet_count=len(new_rows),
        )
        await repo.insert_version_sheets(version_id, new_rows)
        layout.write_manifest(
            row_count=total_rows, sheet_count=len(new_rows),
            replaced_sheet=target["sheet_key"],
            base_version=base_ver["version_number"],
        )
        await library_repo.record_lineage(
            dataset_id, version_id,
            parent_dataset_id=dataset_id, parent_version_id=str(base_ver["id"]),
            parent_dataset_name=ds["name"],
            parent_version_number=base_ver["version_number"],
            parent_sheet_key=target["sheet_key"], relation="sheet_replaced_from",
        )
    except HTTPException:
        await repo.fail_version(version_id, "Sheet replacement failed")
        raise
    except Exception as e:
        await repo.fail_version(version_id, str(e))
        raise HTTPException(500, f"Sheet replacement failed: {e}")

    reused = [r["sheet_name"] for r in new_rows if r["sheet_key"] != target["sheet_key"]]
    return {
        "dataset_id": dataset_id,
        "version_id": version_id,
        "version_number": version_number,
        "replaced_sheet": target["sheet_name"],
        "reused_sheets": reused,
        "row_count": total_rows,
    }
