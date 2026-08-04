"""Library service — execute saved analytics definitions and publish results.

A run pins the version via the definition's version_selector, executes the
existing sampling/aggregation/profiling services, records a jobs row (the
operational record) plus an analytics_runs row (the durable product record),
and registers the stored output as an artifact. Publishing turns a run's
artifact into a new dataset or a new version, with lineage back to the source.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException

from app.features.data_accelerator.schemas import (
    AggregateRequest,
    ProfileRequest,
    SampleRequest,
)
from app.features.data_accelerator.services.aggregation import run_aggregation
from app.features.data_accelerator.services.profiling import run_profiling
from app.features.data_accelerator.services.sampling import run_sampling_pipeline
from app.features.files import repo as files_repo
from app.infra.db.storage import DatasetLayout, get_storage, sample_key
from app.shared import jobs
from app.shared.data_io import (
    DEFAULT_SHEET_NAME,
    SCHEMA_EXTRACTOR_VERSION,
    build_sheet_schema,
    describe_parquet,
    extract_metadata,
    load_data,
    manifest_fingerprint,
)
from app.shared.datasets import resolve_version
from app.shared.repo import get_version

from . import repo


def _selector_pin(selector: dict | None) -> dict[str, Any]:
    selector = selector or {"mode": "current"}
    mode = selector.get("mode", "current")
    if mode == "tag":
        return {"tag": selector.get("tag")}
    if mode == "version":
        return {"version_number": selector.get("version_number")}
    return {}


async def execute_definition(ds: dict, definition: dict, principal) -> tuple[dict, dict]:
    """Run a saved definition. Returns (run row, inline result dict)."""
    dataset_id = str(ds["id"])
    pin = _selector_pin(definition.get("version_selector"))
    ver = await resolve_version(dataset_id, **pin)

    base = {"dataset_id": dataset_id, "sheet": definition.get("sheet"), **pin}
    params = definition.get("params") or {}
    kind = definition["kind"]

    job = await jobs.create_job(
        "analytics", dataset_id=dataset_id, dataset_version_id=str(ver["id"]),
        team_id=str(ds["team_id"]),
        parameters={"definition_id": definition["id"], "kind": kind},
    )
    await jobs.start_job(str(job["id"]))
    run = await repo.create_run(definition["id"], str(ver["id"]), str(job["id"]),
                                principal.user_id)
    try:
        artifact_key: str | None = None
        artifact_type = None
        if kind == "sample":
            resp = await run_sampling_pipeline(SampleRequest(**{**params, **base}))
            summary = {"original_count": resp.original_count,
                       "sampled_count": resp.sampled_count,
                       "sample_file": resp.sample_file}
            if resp.sample_file:
                artifact_key, artifact_type = sample_key(resp.sample_file), "sample_output"
        elif kind == "aggregate":
            resp = await run_aggregation(AggregateRequest(**{**params, **base}))
            summary = {"original_count": resp.original_count,
                       "group_count": resp.group_count,
                       "result_file": resp.result_file}
            if resp.result_file:
                artifact_key, artifact_type = sample_key(resp.result_file), "aggregation_output"
        else:  # profile
            resp = await run_profiling(ProfileRequest(**{**params, **base}))
            summary = {"row_count": resp.row_count, "column_count": resp.column_count}

        artifact_id = None
        if artifact_key:
            storage = get_storage()
            blob = storage.read_bytes(artifact_key)
            artifact = await repo.create_artifact(
                artifact_key, artifact_type, format="parquet",
                media_type="application/vnd.apache.parquet",
                size_bytes=len(blob),
                checksum=hashlib.sha256(blob).hexdigest(),
                created_by=principal.user_id,
            )
            artifact_id = artifact["id"]

        run = await repo.complete_run(run["id"], result_summary=summary,
                                      artifact_id=artifact_id)
        await jobs.complete_job(str(job["id"]), result=summary)
        return run, resp.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        await repo.fail_run(run["id"], str(e))
        await jobs.fail_job(str(job["id"]), str(e))
        raise HTTPException(500, f"Analytics run failed: {e}")


async def publish_run(ds: dict, run: dict, *, mode: str, name: str | None,
                      principal) -> dict:
    """Turn a completed run's artifact into a new dataset or a new version."""
    if run["status"] != "completed":
        raise HTTPException(409, f"Run is not completed (status: {run['status']})")
    if not run.get("artifact_id"):
        raise HTTPException(409, "Run produced no publishable artifact (profile runs don't)")
    artifact = await repo.get_artifact(run["artifact_id"])
    if not artifact:
        raise HTTPException(409, "Run artifact no longer exists")
    parent_ver = await get_version(run["dataset_version_id"]) if run.get("dataset_version_id") else None
    if not parent_ver:
        raise HTTPException(409, "Source version of this run no longer exists")

    team_id = str(ds["team_id"])
    if mode == "new_dataset":
        target = await files_repo.create_dataset(
            name=name or f"{ds['name']} ({run.get('kind', 'derived')})",
            team_id=team_id, owner_id=principal.user_id,
        )
    else:
        target = ds
    target_id = str(target["id"])

    version = await files_repo.create_version(
        target_id, storage_type="temp", status="uploading",
        source={"type": "published", "from_dataset_id": str(ds["id"]),
                "from_version_number": parent_ver["version_number"],
                "analytics_run_id": run["id"]},
    )
    version_id, version_number = str(version["id"]), version["version_number"]

    storage = get_storage()
    layout = DatasetLayout(team_id, target_id, version_number)
    layout.ensure_dirs()
    blob = storage.read_bytes(artifact["storage_key"])
    storage.write_bytes(layout.canonical_parquet, blob)
    path = storage.resolve(layout.canonical_parquet)

    conn = load_data(file_path=path)
    try:
        meta = extract_metadata(conn)
    finally:
        conn.close()
    checksum = hashlib.sha256(blob).hexdigest()
    columns, fingerprint = build_sheet_schema(describe_parquet(path))
    sheet_row = {
        "sheet_key": DEFAULT_SHEET_NAME, "sheet_name": DEFAULT_SHEET_NAME,
        "sheet_index": 0, "is_default": True,
        "storage_key": layout.canonical_parquet,
        "row_count": meta["row_count"], "column_count": meta["column_count"],
        "size_bytes": len(blob), "checksum": checksum,
        "schema_json": columns, "schema_fingerprint": fingerprint,
        "schema_extractor_version": SCHEMA_EXTRACTOR_VERSION,
    }
    await files_repo.complete_version(
        version_id, path=path, size_bytes=len(blob),
        row_count=meta["row_count"], checksum=checksum,
        source_checksum=artifact.get("checksum"),
        manifest_checksum=manifest_fingerprint([sheet_row]),
        sheet_count=1,
    )
    await files_repo.insert_version_sheets(version_id, [sheet_row])
    layout.write_manifest(row_count=meta["row_count"], size_bytes=len(blob),
                          checksum=checksum, published_from_run=run["id"])

    await repo.record_lineage(
        target_id, version_id,
        parent_dataset_id=str(ds["id"]), parent_version_id=str(parent_ver["id"]),
        parent_dataset_name=ds["name"], parent_version_number=parent_ver["version_number"],
        parent_sheet_key=run.get("sheet"), relation="published_from",
    )
    return {"dataset_id": target_id, "dataset_name": target["name"],
            "version_id": version_id, "version_number": version_number, "mode": mode}
