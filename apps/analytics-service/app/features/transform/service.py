"""Transformation service — validate, preview, run, profile, and publish pipelines.

A definition pins its source the same way a saved view does: the version comes
from the ``version_selector`` at run time, and the sheet is re-resolved **by
logical id**, so a confirmed rename never invalidates a saved pipeline.

Running goes through the job worker: :func:`start_run` dispatches a ``transform``
job, and the SAME registered handler executes it whether the caller asked for a
synchronous run (``inline=True``) or left it to the background loop. The handler
compiles the pipeline, materializes the result to the artifact area as a
``transform_output`` artifact, then (§21) profiles that output and diffs it
against the source sheet's profile.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ProblemException
from app.features.data_accelerator.schemas import ProfileRequest
from app.features.data_accelerator.services.diffs import compute_profile_drift
from app.features.data_accelerator.services.profiling import run_profiling
from app.features.data_accelerator.services.sampling import _persist_table
from app.features.explorer import repo as explorer_repo
from app.features.explorer.service import (
    PROFILE_ALGORITHM_VERSION,
    resolve_sheet_with_schema,
)
from app.features.library import repo as library_repo
from app.features.library.service import (
    _selector_pin,
    publish_artifact_as_version,
    resolve_publishable_artifact,
)
from app.infra.db.storage import ArtifactLayout, get_storage
from app.shared import worker
from app.shared.data_io import load_data
from app.shared.datasets import (
    get_version_sheet_rows,
    resolve_version,
    sheet_data_path,
)
from app.shared.repo import get_version
from app.shared.utils.sql import safe_value

from . import repo
from .compile import compile_pipeline
from .schemas import (
    OutputColumn,
    TransformationCompileResult,
    TransformPreview,
)
from .steps import ParseDatesStep, ReplaceStep, TransformStep

logger = logging.getLogger("analytics.transform")

_STEPS = TypeAdapter(list[TransformStep])

# §21 auto-profiling is a convenience, not a contract: skip it (rather than
# stall a run) once an output is large, mirroring MAX_SQL_MATERIALIZE_BYTES.
MAX_AUTO_PROFILE_BYTES = 256 * 1024 * 1024


def parse_steps(raw: list) -> list:
    """Validate stored/incoming step dicts into the discriminated union."""
    return _STEPS.validate_python(raw or [])


def dump_steps(steps: list) -> list[dict]:
    return [s.model_dump(mode="json", by_alias=True) for s in steps]


# ---------------------------------------------------------------------------
# Target resolution + validation
# ---------------------------------------------------------------------------

async def _pin_version_with_data(dataset_id: str, selector: dict) -> dict:
    """Resolve *selector* to a version that actually has data behind it.

    Every entry point that reads a definition's source — configure, preview and
    run — has to answer the same way when the pinned version has no file yet
    (a tag moved onto an ``uploading``/``failed`` version, say). Keeping the
    check in one place is why: preview used to skip it and answered the far more
    confusing ``sheet-not-in-version`` 404 for a state that has nothing to do
    with the sheet.
    """
    ver = await resolve_version(dataset_id, **_selector_pin(selector))
    if not ver.get("path"):
        raise HTTPException(
            404, f"Version has no data (status: {ver.get('status', 'unknown')})")
    return ver


async def _resolve_target(dataset_id: str, sheet: str | None,
                          selector: dict) -> tuple[dict, dict]:
    """(pinned version, sheet row with schema) for a definition's configuration.

    Sheet selection follows the service-wide contract — a multi-sheet version
    addressed without a sheet answers ``sheet-selection-required`` 400.
    """
    ver = await _pin_version_with_data(dataset_id, selector)
    row = await resolve_sheet_with_schema(ver, sheet)
    if not row.get("logical_sheet_id"):
        raise ProblemException(
            400, "This sheet has no logical identity (legacy version) — "
                 "transformations require one",
            code="transformation-unsupported")
    return ver, row


async def _resolve_run_sheet(ver: dict, logical_sheet_id: str) -> dict:
    """Re-resolve a definition's sheet in *ver* by logical id (rename-proof)."""
    from app.shared.datasets import ensure_sheet_schema

    rows = await get_version_sheet_rows(ver)
    row = next((r for r in rows
                if str(r.get("logical_sheet_id") or "") == logical_sheet_id), None)
    if row is None:
        raise ProblemException(
            404,
            f"The transformation's sheet is not present in version {ver['version_number']}",
            code="sheet-not-in-version", version_number=ver["version_number"])
    return await ensure_sheet_schema(ver, row)


def _validate_static_step_config(steps: list) -> None:
    """Reject step configs that can never succeed for ANY input, at save time.

    A malformed ``replace`` regex or an unrecognized ``parse_dates`` strptime
    format is a static property of the step, not of the data — but DuckDB only
    rejects it at execution, so a pipeline built with one saved clean and then
    failed at run (a scheduled job failing weeks later on a mistake made when it
    was authored — exactly what save-time validation exists to prevent). Probe
    the two properties against an in-memory connection with no data.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        for step in steps:
            if isinstance(step, ReplaceStep) and step.mode == "regex" and step.find is not None:
                try:
                    con.execute("SELECT REGEXP_REPLACE('', ?, ?, 'g')",
                                [step.find, step.replace_with])
                except duckdb.Error as e:
                    raise ProblemException(
                        422, f"Invalid regular expression in a 'replace' step: {e}",
                        code="invalid-step", step_type="replace", column=step.column) from e
            elif isinstance(step, ParseDatesStep):
                try:
                    con.execute("SELECT TRY_STRPTIME('', ?)", [step.format])
                except duckdb.Error as e:
                    raise ProblemException(
                        422, f"Invalid strptime format in a 'parse_dates' step: {e}",
                        code="invalid-step", step_type="parse_dates", format=step.format) from e
    finally:
        con.close()


def validate_pipeline(steps: list, schema_json: list[dict]) -> list[dict]:
    """Compile *steps* against *schema_json* purely to surface errors early.

    Returns the folded output columns. Any bad reference or type pairing raises
    the same problem+json the run would have raised — so a definition can never
    be saved in a state that is guaranteed to fail.
    """
    _, _, out_cols = compile_pipeline(steps, schema_json)
    _validate_static_step_config(steps)
    return out_cols


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_transformation(ds: dict, body, created_by: str) -> dict:
    dataset_id = str(ds["id"])
    selector = body.version_selector.model_dump(exclude_none=True)
    _, row = await _resolve_target(dataset_id, body.sheet, selector)
    validate_pipeline(body.steps, row["schema_json"])
    created = await repo.create_definition(
        dataset_id=dataset_id, logical_sheet_id=str(row["logical_sheet_id"]),
        name=body.name, description=body.description, version_selector=selector,
        steps=dump_steps(body.steps), created_by=created_by)
    if created is None:
        raise _name_taken(body.name)
    return created


def _name_taken(name: str | None) -> ProblemException:
    """The (dataset, name) uniqueness conflict, spelled the same way twice.

    POST reaches it via ``ON CONFLICT DO NOTHING`` and PATCH via a caught
    ``IntegrityError``; a UI branching on ``code`` must not have to care which.
    """
    return ProblemException(
        409, f"A transformation named '{name}' already exists on this dataset",
        code="transformation-name-taken")


async def update_transformation(ds: dict, definition: dict, body) -> dict:
    dataset_id = str(ds["id"])
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.description is not None:
        fields["description"] = body.description

    selector = (body.version_selector.model_dump(exclude_none=True)
                if body.version_selector is not None
                else definition["version_selector"])
    steps = body.steps if body.steps is not None else parse_steps(definition["steps"])
    retargeting = (body.sheet is not None or body.version_selector is not None
                   or body.steps is not None)

    if retargeting:
        if body.sheet is not None:
            # Only a caller who actually named a sheet gets name resolution —
            # and only then can the sheet the definition reads change.
            _, row = await _resolve_target(dataset_id, body.sheet, selector)
            fields["logical_sheet_id"] = str(row["logical_sheet_id"])
        else:
            # Re-resolve by LOGICAL ID, never by ``definition["sheet_key"]``.
            # That key is the sheet's CURRENT name, and a definition pinned to
            # an older version is read against a version where the sheet still
            # carries its OLD name: resolving by name there 404s and made the
            # definition permanently uneditable after a confirmed rename.
            ver = await _pin_version_with_data(dataset_id, selector)
            row = await _resolve_run_sheet(ver, definition["logical_sheet_id"])
        validate_pipeline(steps, row["schema_json"])
        fields["version_selector"] = selector
        fields["steps"] = dump_steps(steps)

    if not fields:
        return definition
    try:
        updated = await repo.update_definition(dataset_id, definition["id"], fields)
    except IntegrityError as exc:
        # The UPDATE has no ``ON CONFLICT`` to lean on (Postgres has none for
        # UPDATE), so the unique (dataset_id, name) violation has to be caught
        # here — otherwise a rename onto a taken name is an opaque 500.
        raise _name_taken(fields.get("name")) from exc
    if updated is None:
        # The UPDATE matched no row: the definition was deleted between the
        # route's lookup and this write.
        raise HTTPException(404, f"Transformation not found: {definition['id']}")
    return updated


# ---------------------------------------------------------------------------
# Preview — sampled, in-request, nothing persisted
# ---------------------------------------------------------------------------

async def preview_transformation(ds: dict, definition: dict,
                                 rows: int) -> TransformPreview:
    """Compile the pipeline over a bounded sample and return the rows."""
    dataset_id = str(ds["id"])
    ver = await _pin_version_with_data(dataset_id, definition["version_selector"])
    sheet_row = await _resolve_run_sheet(ver, definition["logical_sheet_id"])
    steps = parse_steps(definition["steps"])

    sql, binds, out_cols = compile_pipeline(
        steps, sheet_row["schema_json"], sample_rows=rows)
    conn = load_data(sheet_data_path(ver, sheet_row))
    try:
        cur = conn.execute(sql, binds)
        names = [d[0] for d in cur.description]
        fetched = cur.fetchall()
    except ProblemException:
        raise
    except Exception as exc:  # noqa: BLE001 — a bad pipeline is a 400, not a 500
        raise _pipeline_error(exc)
    finally:
        conn.close()

    return TransformPreview(
        columns=names,
        rows=[{n: safe_value(v) for n, v in zip(names, r)} for r in fetched],
        approximate=True,
        output_schema=[OutputColumn(**c) for c in out_cols],
        version_number=ver["version_number"],
        sheet_name=sheet_row["sheet_name"],
    )


async def compile_transformation(ds: dict, body) -> TransformationCompileResult:
    """Compile an UNSAVED pipeline: fold its schema, optionally sample rows.

    The same resolution, the same compiler and the same errors as saving one —
    only nothing is written. This is what a step-by-step builder polls while the
    pipeline is still being assembled; without it the only way to ask "is step 3
    valid, and what columns does it leave?" was to save a definition (which
    needs a unique name) and then delete it.
    """
    dataset_id = str(ds["id"])
    selector = body.version_selector.model_dump(exclude_none=True)
    ver, sheet_row = await _resolve_target(dataset_id, body.sheet, selector)

    step_columns: list[list[dict]] = []
    sql, binds, out_cols = compile_pipeline(
        body.steps, sheet_row["schema_json"], sample_rows=body.rows,
        step_columns=step_columns)
    # The builder polls compile to ask "is this valid?"; a bad regex/date format
    # must answer the same typed 422 here as it does on save, not a 200 that
    # then fails at run.
    _validate_static_step_config(body.steps)

    names: list[str] = [c["name"] for c in out_cols]
    fetched: list = []
    if body.rows is not None:
        conn = load_data(sheet_data_path(ver, sheet_row))
        try:
            cur = conn.execute(sql, binds)
            names = [d[0] for d in cur.description]
            fetched = cur.fetchall()
        except ProblemException:
            raise
        except Exception as exc:  # noqa: BLE001 — a bad pipeline is a 400, not a 500
            raise _pipeline_error(exc)
        finally:
            conn.close()

    return TransformationCompileResult(
        output_schema=[OutputColumn(**c) for c in out_cols],
        step_schemas=[[OutputColumn(**c) for c in cols] for cols in step_columns],
        columns=names,
        rows=[{n: safe_value(v) for n, v in zip(names, r)} for r in fetched],
        sampled=body.rows is not None,
        version_number=ver["version_number"],
        sheet_name=sheet_row["sheet_name"],
    )


def _pipeline_error(exc: Exception) -> Exception:
    """Map a DuckDB execution failure to a 400 naming the pipeline as the cause."""
    return ProblemException(
        400, f"Transformation pipeline failed to execute: {type(exc).__name__}: {exc}",
        code="transformation-failed")


# ---------------------------------------------------------------------------
# Run — dispatched through the job worker
# ---------------------------------------------------------------------------

async def start_run(ds: dict, definition: dict, principal, *, sync: bool) -> dict:
    """Create the run row and dispatch the ``transform`` job.

    The version is pinned HERE (not in the handler) so the run records exactly
    which version it was launched against, even when the worker picks it up
    later and ``current`` has since moved on.
    """
    dataset_id = str(ds["id"])
    ver = await _pin_version_with_data(dataset_id, definition["version_selector"])
    # Resolve the sheet up front too, so a pipeline whose sheet is missing from
    # the pinned version fails at request time rather than inside the worker.
    await _resolve_run_sheet(ver, definition["logical_sheet_id"])

    run = await repo.create_run(
        definition_id=definition["id"], dataset_version_id=str(ver["id"]),
        job_id=None, mode="full", triggered_by=principal.user_id)

    params = {"definition_id": definition["id"], "dataset_id": dataset_id,
              "version_id": str(ver["id"]), "run_id": run["id"],
              "team_id": str(ds["team_id"]), "triggered_by": principal.user_id}
    try:
        await worker.dispatch(
            "transform", params=params, dataset_id=dataset_id,
            dataset_version_id=str(ver["id"]), team_id=str(ds["team_id"]),
            inline=sync)
    except StarletteHTTPException as exc:
        # One class covers both: FastAPI's HTTPException and ProblemException
        # are each a subclass of Starlette's, and nothing in this service raises
        # Starlette's bare. Spelling it as the base is the same rule the other
        # two sites use (``library/service.py``, ``mcp/identity.py``) and it
        # cannot be half-updated the way an explicit tuple can.
        await repo.fail_run(run["id"], str(exc.detail))
        raise
    except Exception as exc:  # noqa: BLE001 — surfaced on the run row too
        # The run row is created BEFORE the dispatch, and only the registered
        # handler ever closes it (``_handle_transform``). So everything
        # ``worker.dispatch`` does *around* the handler — creating the job row,
        # ``start_job``, ``complete_job`` — could fail with the run row already
        # open and nothing left to close it: the caller is told the run failed
        # (``_pipeline_error`` is a 4xx/5xx problem+json) while the row says
        # ``running`` forever. Worse in async mode, where the failure can be
        # ``jobs.create_job`` itself: no job row is ever enqueued, so the worker
        # loop has nothing to pick up and the run is unreachable by any path.
        #
        # Failing it here is deliberately unconditional and deliberately
        # belt-and-braces: when the handler DID run and already recorded the
        # real error, ``repo.fail_run`` is guarded on ``status = 'running'`` and
        # this call is a no-op that cannot overwrite the better message — or
        # demote a run that completed and then tripped over ``complete_job``.
        await repo.fail_run(run["id"], str(exc))
        # NOT ``_pipeline_error``: nothing here says the caller's pipeline is
        # bad. The user's SQL is executed inside the handler, and every failure
        # it raises is already a problem+json caught by the branch above — so
        # what lands here is the dispatch plumbing (``jobs.create_job``, the
        # job-row writes, the result serialization). Calling that a 400
        # ``transformation-failed`` told the client to go fix a pipeline that
        # was fine, and interpolated the raw exception — connection strings and
        # all — into the response body. The detail is fixed text; the exception
        # goes to the log and to the run row's ``error``.
        logger.exception("dispatching transformation run %s failed", run["id"])
        raise ProblemException(
            503, "The transformation could not be dispatched; the run was "
                 "recorded as failed. Retry.",
            code="dispatch-failed") from exc
    return await repo.get_run(run["id"]) or run


async def _run_pipeline(params: dict, job_id: str | None) -> dict:
    """Execute one transformation run. The worker handler's whole body.

    Runs identically inline (synchronous request) and from the background loop —
    there is exactly one implementation of what a transformation *does*.
    """
    run_id = params["run_id"]
    dataset_id = params["dataset_id"]
    if job_id:
        await repo.attach_job(run_id, job_id)

    definition = await repo.get_definition(dataset_id, params["definition_id"])
    if not definition:
        raise RuntimeError(f"Transformation definition {params['definition_id']} is gone")
    ver = await get_version(params["version_id"])
    if not ver:
        raise RuntimeError(f"Version {params['version_id']} is gone")

    sheet_row = await _resolve_run_sheet(ver, definition["logical_sheet_id"])
    steps = parse_steps(definition["steps"])
    sql, binds, out_cols = compile_pipeline(steps, sheet_row["schema_json"])

    layout = ArtifactLayout("transform_output", team_id=params.get("team_id"),
                            dataset_id=dataset_id)
    source_path = sheet_data_path(ver, sheet_row)
    conn = load_data(source_path)
    try:
        source_rows = conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        try:
            conn.execute(f"CREATE TABLE transform_out AS {sql}", binds)
        except ProblemException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _pipeline_error(exc)
        output_rows = conn.execute("SELECT COUNT(*) FROM transform_out").fetchone()[0]
        filename = _persist_table(conn, "transform_out", "transform", layout)
    finally:
        conn.close()

    key = layout.key(filename)
    blob = get_storage().read_bytes(key)
    artifact = await library_repo.create_artifact(
        key, "transform_output", filename=filename, format="parquet",
        media_type="application/vnd.apache.parquet",
        size_bytes=len(blob), checksum=hashlib.sha256(blob).hexdigest(),
        created_by=params.get("triggered_by"),
        dataset_id=dataset_id, team_id=params.get("team_id"),
    )

    summary = {
        "sample_file": filename,
        "source_row_count": source_rows,
        "row_count": output_rows,
        "column_count": len(out_cols),
        "step_count": len(steps),
        "output_columns": [c["name"] for c in out_cols],
        "sheet": sheet_row["sheet_name"],
        "version_number": ver["version_number"],
    }

    output_profile, source_drift = await _profile_output(
        key, len(blob), ver, sheet_row, source_path, dataset_id)

    await repo.complete_run(run_id, result_summary=summary,
                            artifact_id=artifact["id"],
                            output_profile=output_profile,
                            source_drift=source_drift)
    return summary


async def _profile_output(artifact_key: str, size_bytes: int, ver: dict,
                          sheet_row: dict, source_path: str,
                          dataset_id: str) -> tuple[dict | None, dict | None]:
    """§21 — profile the output and diff it against the source sheet.

    Best-effort by design: profiling is a convenience on top of a run that has
    already succeeded, so an oversized output is skipped and any failure is
    logged rather than failing the transformation.
    """
    if size_bytes > MAX_AUTO_PROFILE_BYTES:
        logger.info("skipping auto-profile: output is %d bytes", size_bytes)
        return None, None
    try:
        out_profile = (await run_profiling(ProfileRequest(
            file_path=get_storage().resolve(artifact_key),
            include_correlations=True))).model_dump()
    except Exception:  # noqa: BLE001
        logger.exception("auto-profiling the transform output failed")
        return None, None

    # Prefer the source sheet's PERSISTED profile — it is the same profile the
    # rest of the product reports — and only profile ad hoc when none exists.
    source_profile: dict | None = None
    source_ref = "profile_run"
    if sheet_row.get("logical_sheet_id"):
        persisted = await explorer_repo.get_completed_run(
            str(ver["id"]), str(sheet_row["logical_sheet_id"]),
            PROFILE_ALGORITHM_VERSION)
        if persisted and persisted.get("profile"):
            source_profile = persisted["profile"]
    if source_profile is None:
        source_ref = "ad_hoc"
        try:
            source_profile = (await run_profiling(ProfileRequest(
                file_path=source_path, include_correlations=True))).model_dump()
        except Exception:  # noqa: BLE001
            logger.exception("profiling the transform source failed")
            return out_profile, None

    drift = compute_profile_drift(
        source_profile, out_profile, sheet_key=sheet_row.get("sheet_key")
    ).model_dump()
    drift["source"] = source_ref
    return out_profile, drift


async def _handle_transform(job: dict) -> dict:
    """Registered ``transform`` job handler (worker loop AND inline dispatch)."""
    params = job.get("parameters") or {}
    run_id = params.get("run_id")
    try:
        return await _run_pipeline(params, str(job["id"]))
    except Exception as exc:  # noqa: BLE001 — recorded on the run, then re-raised
        if run_id:
            await repo.fail_run(run_id, str(exc))
        raise


worker.register_handler("transform", _handle_transform)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

async def publish_transformation_run(ds: dict, run: dict, *, mode: str,
                                     name: str | None, principal) -> dict:
    """Publish a completed run's output as a new dataset or a new version.

    Non-destructive by construction: this always writes a NEW version (of a new
    or the same dataset). The source version is immutable and untouched.

    Publishing the same run twice is refused. Every POST used to mint another
    byte-identical version plus another ``transformed_from`` lineage edge, so a
    double-clicked button silently forked the dataset's history — and nothing in
    the API could be asked whether a run had been published at all. The 409
    carries the existing publication so a UI can link to it instead.
    """
    already = await repo.get_published_version(run["id"])
    if already:
        raise ProblemException(
            409,
            f"This run was already published as version "
            f"{already['published_version_number']}",
            code="run-already-published",
            published_version_id=already["published_version_id"],
            published_dataset_id=already["published_dataset_id"],
            published_version_number=already["published_version_number"])
    artifact, parent_ver = await resolve_publishable_artifact(run)
    return await publish_artifact_as_version(
        ds, artifact, parent_ver, mode=mode, name=name, principal=principal,
        relation="transformed_from", parent_sheet_key=run.get("sheet_key"),
        default_suffix="transformed",
        source_extra={"transformation_run_id": run["id"]},
    )
