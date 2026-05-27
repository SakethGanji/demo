"""PromptLab API — exactly two endpoints (v9).

  POST /prompt-lab/datasets   multipart upload  -> DatasetEntry
                              register a dataset; returns dataset_id

  POST /prompt-lab/evaluate   JSON body         -> EvaluateResponse
                              dataset_id is required; pass the id returned
                              by /datasets. No multipart / no inline file —
                              keep file bytes off the workflow path.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, HTTPException, UploadFile

from .schemas import (
    DatasetColumnInfo,
    DatasetEntry,
    EvaluateRequest,
    EvaluateResponse,
)
from .services import dataset_files
from .services.evaluator import run_evaluation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompt-lab")


def _parse_json_form(value: str | None, field_name: str) -> list[str] | None:
    """Parse a multipart form value as a JSON array of strings, or comma list."""
    if value is None or value == "":
        return None
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"{field_name} is not valid JSON: {e}")
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise HTTPException(400, f"{field_name} must be a JSON list of strings")
        return parsed
    return [s.strip() for s in value.split(",") if s.strip()]


@router.post("/datasets", response_model=DatasetEntry)
async def upload_dataset_endpoint(
    file: UploadFile,
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    target_column: str | None = Form(default=None),
    input_columns: str | None = Form(default=None),
) -> DatasetEntry:
    """Upload a labeled dataset. Returns a ``dataset_id`` to pass to /evaluate."""
    inputs_parsed = _parse_json_form(input_columns, "input_columns")
    entry = await dataset_files.upload_dataset(
        file,
        name=name,
        description=description,
        target_column=target_column,
        input_columns=inputs_parsed,
    )
    return DatasetEntry(
        dataset_id=entry["dataset_id"],
        name=entry["name"],
        description=entry["description"],
        n_rows=entry["n_rows"],
        target_column=entry["target_column"],
        input_columns=entry["input_columns"],
        detected_split_column=entry["detected_split_column"],
        detected_classes=entry["detected_classes"],
        detected_splits=entry["detected_splits"],
        columns=[DatasetColumnInfo(**c) for c in entry["columns"]],
        sha256=entry["sha256"],
        file_size_bytes=entry["file_size_bytes"],
        storage_path=entry["storage_path"],
        format_original=entry["format_original"],
        created_at=entry["created_at"],
    )


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(request: EvaluateRequest) -> EvaluateResponse:
    """Run a classification evaluation against a pre-registered dataset.

    ``dataset_id`` is required (upload via ``POST /datasets`` to obtain one).
    Same (prompt, dataset, model, splits, target, inputs) returns a cached
    response — no LLM call, no spend.
    """
    return await run_evaluation(request)
