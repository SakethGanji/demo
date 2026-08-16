"""Library service — execute saved analytics definitions and publish results.

A run pins the version via the definition's version_selector, executes the
existing sampling/aggregation/profiling services, records a jobs row (the
operational record) plus an analytics_runs row (the durable product record),
and registers the stored output as an artifact. Publishing turns a run's
artifact into a new dataset or a new version, with lineage back to the source.
"""

from __future__ import annotations

import hashlib
from typing import Any, NamedTuple

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ProblemException
from app.features.data_accelerator.schemas import (
    AggregateRequest,
    PivotRequest,
    ProfileRequest,
    SampleRequest,
)
from app.features.data_accelerator.services.aggregation import run_aggregation
from app.features.data_accelerator.services.pivot import run_pivot
from app.features.data_accelerator.services.profiling import run_profiling
from app.features.data_accelerator.services.sampling import run_sampling_pipeline
from app.features.files import repo as files_repo
from app.infra.db.storage import ArtifactLayout, DatasetLayout, get_storage
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


#: Which request model each definition ``kind`` is executed through.
_REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "sample": SampleRequest,
    "aggregate": AggregateRequest,
    "pivot": PivotRequest,
    "profile": ProfileRequest,
}

#: Params that would replace the definition's *source* outright. The underlying
#: request models accept ``file_path`` and inline ``data`` because the direct
#: /sample and /aggregate endpoints support them — and those endpoints gate
#: ``file_path`` behind ``_authorize_source`` because it reads straight off the
#: server filesystem with no team scoping. A definition has no such gate: it is
#: authorized by the dataset it hangs off. Every executor prefers
#: ``request.file_path``/``request.data`` over ``dataset_id``, so a stored
#: ``params["file_path"]`` would silently retarget the run at an arbitrary file
#: and register the result as an artifact in the caller's own team.
_FORBIDDEN_SOURCE_PARAMS = ("file_path", "data")

#: Params that would silently defeat the definition's ``version_selector``.
#: ``base`` only overrides the keys it contains, and a ``mode=current`` selector
#: contributes none of these — so a stored ``version_number`` would run against
#: that version while the analytics_runs row, the publish parent and the whole
#: lineage chain record the *current* one. Stripped rather than rejected: the
#: definition's own selector is the declared mechanism and these are redundant.
_IGNORED_TARGET_PARAMS = ("dataset_id", "version_id", "version_number", "tag")

#: Run kinds whose output derives from more than one dataset. They need a
#: publish path that authorizes every source and records every lineage parent,
#: which the generic library route does not do. See ``publish_run``.
_CROSS_DATASET_KINDS = frozenset({"join"})


def build_definition_request(kind: str, params: dict, base: dict) -> BaseModel:
    """Bind a saved definition's stored params to the request model for *kind*.

    Definitions are stored as free-form JSON, so a definition saved under an
    older, looser schema can hold a value the current model rejects. The
    canonical case is ``sort_order: "ASC"``, saved when the field was a bare
    ``str``: it used to run and quietly sort *descending*, and is now a
    ``Literal["asc", "desc"]``. A definition saved without ``group_by`` is the
    same shape of problem and predates that change.

    Left to pydantic, the resulting ``ValidationError`` is an unhandled
    exception: ``execute_definition``'s blanket ``except Exception`` reports it
    as a 500 "Analytics run failed", and ``compute_definition`` as a bare 500.
    Neither is a server fault — the *request* was fine, the *stored definition*
    is not — and neither names the offending parameter. Both are 400s here,
    carrying the parameter, the reason, and the rejected value so the caller
    can fix the definition rather than guess.

    This is also the one choke point where the stored params meet the request
    model, so it is where source and version smuggling is stopped — see
    ``_FORBIDDEN_SOURCE_PARAMS`` and ``_IGNORED_TARGET_PARAMS``.

    Called before any job/run row is opened: a definition that cannot even be
    bound never ran, so it must not leave a failed run behind.
    """
    model = _REQUEST_MODELS.get(kind)
    if model is None:
        # `join` definitions are real rows in analytics_definitions (the guided
        # join builder writes them) and they list alongside the rest, so the UI
        # can and does offer them here. They are executed via POST
        # /joins/execute, which authorizes both sides. Without this, the bare
        # dict lookup raised KeyError and the caller got an opaque 500.
        raise ProblemException(
            400,
            f"A '{kind}' definition cannot be run from the library. "
            "Join definitions are executed through POST /api/v1/joins/execute, "
            "which authorizes both sides of the join.",
            code="kind-not-runnable", kind=kind,
        )

    smuggled = [k for k in _FORBIDDEN_SOURCE_PARAMS if params.get(k) is not None]
    if smuggled:
        raise ProblemException(
            400,
            f"Saved '{kind}' definition sets {', '.join(smuggled)} in its params. "
            "A definition reads the dataset it is saved on — it may not name its "
            "own source. Remove those parameters from the definition.",
            code="definition-source-not-allowed", kind=kind,
            params=sorted(smuggled),
        )
    params = {k: v for k, v in params.items() if k not in _IGNORED_TARGET_PARAMS}

    try:
        return model(**{**params, **base})
    except ValidationError as exc:
        errors = [
            {
                "param": ".".join(str(p) for p in err["loc"]) or "(root)",
                "reason": err["msg"],
                "value": err.get("input"),
            }
            for err in exc.errors(include_url=False)
        ]
        summary = "; ".join(f"{e['param']} — {e['reason']}" for e in errors)
        raise ProblemException(
            400,
            f"Saved '{kind}' definition has invalid parameters: {summary}. "
            "Update the definition before running it again.",
            code="invalid-definition", kind=kind,
            errors=jsonable_encoder(errors),
        ) from exc


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
    # Bind the stored params first: an unbindable definition is a 400, and
    # raising it here means no job and no failed run row for something that
    # never started. See ``build_definition_request``.
    request = build_definition_request(kind, params, base)

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
        team_id = str(ds["team_id"])
        if kind == "sample":
            layout = ArtifactLayout("sample_output", team_id=team_id,
                                    dataset_id=dataset_id)
            resp = await run_sampling_pipeline(request, layout)
            summary = {"original_count": resp.original_count,
                       "sampled_count": resp.sampled_count,
                       "sample_file": resp.sample_file}
            if resp.sample_file:
                artifact_key, artifact_type = layout.key(resp.sample_file), "sample_output"
        elif kind == "aggregate":
            layout = ArtifactLayout("aggregation_output", team_id=team_id,
                                    dataset_id=dataset_id)
            resp = await run_aggregation(request, layout)
            summary = {"original_count": resp.original_count,
                       "group_count": resp.group_count,
                       "result_file": resp.result_file}
            if resp.result_file:
                artifact_key, artifact_type = layout.key(resp.result_file), "aggregation_output"
        elif kind == "pivot":
            layout = ArtifactLayout("pivot_output", team_id=team_id,
                                    dataset_id=dataset_id)
            resp = await run_pivot(request, layout)
            summary = {"original_count": resp.original_count,
                       "row_count": resp.row_count,
                       "result_file": resp.result_file}
            if resp.result_file:
                artifact_key, artifact_type = layout.key(resp.result_file), "pivot_output"
        else:  # profile
            resp = await run_profiling(request)
            summary = {"row_count": resp.row_count, "column_count": resp.column_count}

        artifact_id = None
        if artifact_key:
            storage = get_storage()
            blob = storage.read_bytes(artifact_key)
            artifact = await repo.create_artifact(
                artifact_key, artifact_type,
                filename=artifact_key.rsplit("/", 1)[-1],
                format="parquet",
                media_type="application/vnd.apache.parquet",
                size_bytes=len(blob),
                checksum=hashlib.sha256(blob).hexdigest(),
                created_by=principal.user_id,
                dataset_id=dataset_id, team_id=str(ds["team_id"]),
            )
            artifact_id = artifact["id"]

        run = await repo.complete_run(run["id"], result_summary=summary,
                                      artifact_id=artifact_id)
        await jobs.complete_job(str(job["id"]), result=summary)
        return run, resp.model_dump()
    except Exception as exc:
        # ONE bookkeeping path for every way a run can end badly. Whatever
        # escapes the block above — a ProblemException, a FastAPI
        # HTTPException, or an unexpected error — closes the run and the job
        # first, so no path can leave a row in `running`. A row that is neither
        # succeeded nor failed is unrecoverable: nothing retries it, nothing
        # reaps it, and it accumulates.
        #
        # The catch has to be Starlette's HTTPException, not FastAPI's:
        # ``ProblemException`` subclasses Starlette's, so `except
        # fastapi.HTTPException` did NOT match it and every actionable 4xx
        # raised DURING a run (unknown-column, sheet-selection-required,
        # invalid-sort-order, unknown-operator) fell through to the generic
        # branch and surfaced as a 500 "Analytics run failed". Re-raising the
        # original preserves its status, its problem+json ``code`` and its
        # extra fields. FastAPI's own HTTPException is a subclass, so it takes
        # the same route — it used to be re-raised with NO bookkeeping at all,
        # which is what left runs stuck.
        await repo.fail_run(run["id"], str(exc))
        await jobs.fail_job(str(job["id"]), str(exc))
        if isinstance(exc, StarletteHTTPException):
            raise
        raise HTTPException(500, f"Analytics run failed: {exc}") from exc


async def resolve_publishable_artifact(run: dict) -> tuple[dict, dict]:
    """(artifact, parent version) for a completed run, or a 409 explaining why not.

    Shared by every publish path — a run must be completed, must have produced
    an artifact, and its source version must still exist.

    Each refusal carries its own ``code`` and structured fields. All four used
    to be bare ``HTTPException(409)``s, which the problem renderer collapses to
    the generic ``"conflict"`` — the same code the *already published* refusal
    (``run-already-published``) and the *wrong route* one (``publish-wrong-route``)
    are distinguished by. A caller therefore had to pattern-match English prose
    to tell "still running, keep polling" from "failed, disable the button" from
    "the artifact was garbage-collected, re-run it". The status codes and the
    ``detail`` strings are unchanged; only the machine-readable envelope grew.
    """
    if run["status"] != "completed":
        # One slug for every non-terminal/terminal-but-unsuccessful state, with
        # the state itself in a field: a client switches on `run_status`, not
        # prose. The field cannot be called `status` — problem+json already
        # spends that key on the HTTP status code, and `problem_response` takes
        # it positionally, so an extra named `status` is a TypeError.
        raise ProblemException(
            409, f"Run is not completed (status: {run['status']})",
            code="run-not-completed", run_status=run["status"],
            run_error=run.get("error"))
    if not run.get("artifact_id"):
        raise ProblemException(
            409, "Run produced no publishable artifact (profile runs don't)",
            code="run-has-no-artifact", kind=run.get("kind"))
    artifact = await repo.get_artifact(run["artifact_id"])
    if not artifact:
        raise ProblemException(
            409, "Run artifact no longer exists",
            code="run-artifact-missing", artifact_id=str(run["artifact_id"]))
    parent_ver = await get_version(run["dataset_version_id"]) if run.get("dataset_version_id") else None
    if not parent_ver:
        raise ProblemException(
            409, "Source version of this run no longer exists",
            code="run-source-version-missing",
            dataset_version_id=(str(run["dataset_version_id"])
                                if run.get("dataset_version_id") else None))
    return artifact, parent_ver


async def publish_run(ds: dict, run: dict, *, mode: str, name: str | None,
                      principal) -> dict:
    """Turn a completed run's artifact into a new dataset or a new version."""
    # A join run is an ordinary analytics run hanging off the join's LEFT
    # dataset, so it satisfies this route's `run["dataset_id"] == dataset_id`
    # check and used to publish here happily. Two things go wrong when it does:
    # the right-hand parent is never recorded (this path passes no
    # extra_lineage, so a two-parent derivation is stored as a one-parent one),
    # and the right-hand dataset is never authorized — permissions are
    # team-scoped, and a relationship may cross teams, so left-side WRITE alone
    # would materialize the other team's columns. POST /joins/{run_id}/publish
    # does both. Keep exactly one publish path for joins.
    if run.get("kind") in _CROSS_DATASET_KINDS:
        raise ProblemException(
            409,
            f"A '{run['kind']}' run has more than one source dataset and must be "
            f"published through POST /api/v1/joins/{run['id']}/publish, which "
            "authorizes both sides and records both lineage parents.",
            code="publish-wrong-route", kind=run["kind"],
            publish_path=f"/api/v1/joins/{run['id']}/publish",
        )
    artifact, parent_ver = await resolve_publishable_artifact(run)
    # Kind-specific relation so derived datasets say HOW they were derived;
    # published_from stays as the fallback for future run kinds.
    relation = {"sample": "sampled_from",
                "aggregate": "aggregated_from",
                "pivot": "pivoted_from",
                "join": "joined_from"}.get(run.get("kind"), "published_from")
    return await publish_artifact_as_version(
        ds, artifact, parent_ver, mode=mode, name=name, principal=principal,
        relation=relation, parent_sheet_key=run.get("sheet"),
        default_suffix=run.get("kind", "derived"),
        source_extra={"analytics_run_id": run["id"]},
    )


async def _parent_dataset_of(ds: dict, parent_ver: dict) -> dict:
    """The dataset *parent_ver* actually belongs to — not the publish target.

    ``ds`` is where the new version is being written; ``parent_ver`` is where
    the data came from, and for a join those are not the same dataset. The
    source version's own ``dataset_id`` is the only truthful answer, so it wins
    whenever it disagrees with the target.
    """
    parent_dataset_id = parent_ver.get("dataset_id")
    if parent_dataset_id is None or str(parent_dataset_id) == str(ds["id"]):
        return ds
    from app.shared.repo import get_dataset

    parent_ds = await get_dataset(str(parent_dataset_id))
    # A version cannot outlive its dataset, but never fabricate a parent id:
    # report the id the version names, with no name, rather than the target's.
    return parent_ds or {"id": str(parent_dataset_id), "name": None}


async def publish_artifact_as_version(
    ds: dict, artifact: dict, parent_ver: dict, *, mode: str, name: str | None,
    principal, relation: str, parent_sheet_key: str | None,
    default_suffix: str, source_extra: dict | None = None,
    extra_lineage: list[dict] | None = None,
) -> dict:
    """Publish a stored artifact as a new dataset or a new version of *ds*.

    The full version machinery — blob copy, canonical parquet, sheet row,
    checksums, manifest, lineage — lives here so every producer (analytics
    runs, transformations, joins) publishes identically and records only its
    own ``relation``. *extra_lineage* records ADDITIONAL parents (a join has
    two), each a dict of ``record_lineage`` keyword arguments.

    ``ds`` is the publish TARGET. Every parent field is derived from
    ``parent_ver`` instead — see ``_parent_dataset_of``.
    """
    team_id = str(ds["team_id"])
    if mode == "new_dataset":
        target_name = name or f"{ds['name']} ({default_suffix})"
        # Publishing must never silently shadow an existing dataset — the
        # datasets table has no unique name constraint, so guard here. Its own
        # code so a "pick another name" dialog can branch on it, rather than the
        # generic `conflict` the run-state refusals were already given slugs to
        # be distinguished from.
        if await repo.dataset_name_taken(team_id, target_name):
            raise ProblemException(
                409, f"A dataset named '{target_name}' already exists in this team",
                code="dataset-name-taken", name=target_name)
        target = await files_repo.create_dataset(
            name=target_name, team_id=team_id, owner_id=principal.user_id,
        )
    else:
        target = ds
    target_id = str(target["id"])
    # The provenance of this version is its SOURCE, which for a join published
    # into the right-hand dataset (or into any third dataset the caller can
    # write) is not `ds` at all. Pairing the target's id with the source's
    # version number described a version that dataset never had.
    parent_ds = await _parent_dataset_of(ds, parent_ver)

    version = await files_repo.create_version(
        target_id, storage_type="temp", status="uploading",
        source={"type": "published", "from_dataset_id": str(parent_ds["id"]),
                "from_version_number": parent_ver["version_number"],
                **(source_extra or {})},
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
                          checksum=checksum,
                          published_from_artifact=artifact["id"])

    await repo.record_lineage(
        target_id, version_id,
        parent_dataset_id=str(parent_ds["id"]), parent_version_id=str(parent_ver["id"]),
        parent_dataset_name=parent_ds["name"],
        parent_version_number=parent_ver["version_number"],
        parent_sheet_key=parent_sheet_key, relation=relation,
    )
    # A cross-dataset producer (a join) has more than one parent; each extra
    # side is recorded as its own lineage row against the same new version.
    for extra in extra_lineage or []:
        await repo.record_lineage(target_id, version_id, **extra)
    return {"dataset_id": target_id, "dataset_name": target["name"],
            "version_id": version_id, "version_number": version_number, "mode": mode}


# ---------------------------------------------------------------------------
# Chart rendering (compute-only — no run row, no artifact)
# ---------------------------------------------------------------------------

class DefinitionResult(NamedTuple):
    """What a chart render gets back from re-running a saved definition.

    ``truncated`` and ``total_rows`` exist because the aggregate/pivot services
    clip their own output — ``PivotResponse.truncated`` /
    ``AggregateResponse.truncated`` — and this used to be a bare 3-tuple, so the
    flag had no way out of the function. A pivot definition holding
    ``params.limit: 50`` over 500 pivot rows therefore rendered as
    ``truncated: false, total_rows == row_count``: the chart asserted
    completeness for a set the server had cut. See ``ChartRenderResponse``.

    ``total_rows`` is ``None`` when the source clipped the result, because none
    of the underlying responses report how many rows there would have been —
    only that there were more. ``None`` means "unknown, and more than
    ``row_count``"; reporting ``len(rows)`` there would be an outright lie.
    """

    columns: list[str]
    rows: list[dict]
    masked_columns: list[str]
    truncated: bool = False
    total_rows: int | None = None


async def compute_definition(
    ds: dict, definition: dict, principal=None,
) -> DefinitionResult:
    """Run a saved definition and return its rows plus how complete they are.

    ``execute_definition`` is the durable path: it opens a job, writes an
    analytics_runs row, and registers an artifact. Rendering a chart is a read
    — doing that on every render would fill the run history with noise — so
    this recomputes the same operation and returns the rows directly.

    *principal* is what makes the dictionary's declared sensitivity bind here
    too. Chart rendering is a raw read path: a ``sample`` definition returns
    dataset rows verbatim, and an ``aggregate``/``pivot`` group key carries the
    raw values of the column it grouped on. Without masking, plotting a chart
    over a declared-PII column returned exactly the values ``/views/{id}/run``
    and ``/download`` withhold. Passing ``principal=None`` masks nothing and
    exists only for internal callers that have already authorized raw access.
    """
    dataset_id = str(ds["id"])
    pin = _selector_pin(definition.get("version_selector"))
    base = {"dataset_id": dataset_id, "sheet": definition.get("sheet"), **pin}
    params = {**(definition.get("params") or {}), "return_data": True}
    kind = definition["kind"]

    if kind not in ("pivot", "aggregate", "sample"):
        raise ProblemException(
            400, f"A '{kind}' definition produces no tabular result to chart",
            code="kind-not-chartable", kind=kind)
    # Same 400-not-500 contract as the run path: a chart whose definition holds
    # params the current schema rejects reports which param, not "unexpected
    # error". See ``build_definition_request``.
    request = build_definition_request(kind, params, base)

    # persist=False matters: nothing registers an artifact on this path, so a
    # written blob would be an orphan no /samples request could authorize and
    # no dataset delete would reach — on *every* chart render.
    layout = ArtifactLayout("chart_render", team_id=str(ds["team_id"]),
                            dataset_id=dataset_id)
    if kind == "pivot":
        resp = await run_pivot(request, layout, persist=False)
        columns, rows = list(resp.columns), list(resp.data or [])
        truncated = bool(resp.truncated)
    elif kind == "aggregate":
        resp = await run_aggregation(request, layout, persist=False)
        columns, rows = list(resp.columns), list(resp.data or [])
        truncated = bool(resp.truncated)
    else:
        resp = await run_sampling_pipeline(request, layout, persist=False)
        rows = list(resp.data or [])
        columns = list(rows[0].keys()) if rows else []
        # A sample is *deliberately* a subset — that is what the definition
        # asked for, not a cap the server imposed — so it is not "truncated".
        # SampleResponse carries no truncation flag either.
        truncated = False

    masked = await _mask_definition_rows(ds, definition, rows, principal)
    return DefinitionResult(columns, rows, masked, truncated,
                            None if truncated else len(rows))


async def _mask_definition_rows(ds: dict, definition: dict, rows: list[dict],
                                principal) -> list[str]:
    """Mask declared-sensitive columns in *rows* in place-ish; return their names.

    Resolves the same sheet the definition ran against so the dictionary's
    column metadata can be looked up, then applies the shared masking policy.
    Returns [] whenever the caller may see raw values or nothing is declared,
    which is one metadata read on an ordinary dataset.
    """
    if principal is None or not rows:
        return []
    from app.shared.datasets import (
        ensure_sheet_schema,
        resolve_version_sheet_row,
    )
    from app.shared.masking import mask_rows, resolve_masking

    dataset_id = str(ds["id"])
    pin = _selector_pin(definition.get("version_selector"))
    ver = await resolve_version(dataset_id, **pin)
    sheet_row = await resolve_version_sheet_row(ver, definition.get("sheet"))
    if sheet_row is None:
        return []
    sheet_row = await ensure_sheet_schema(ver, sheet_row)
    masked = await resolve_masking(dataset_id, sheet_row, principal)
    if not masked:
        return []

    # A pivot widens the distinct values of its column dimension into output
    # COLUMN NAMES. Masking cell values cannot reach those, so a pivot over a
    # declared-sensitive dimension would publish the very values it withholds,
    # as headers. Refuse rather than return a half-masked chart.
    pivot_dimension = _pivot_dimension(definition)
    if pivot_dimension in masked:
        raise ProblemException(
            403,
            f"This chart pivots on '{pivot_dimension}', which the data dictionary "
            "marks sensitive — its values would become the chart's column headers. "
            "Viewing it requires elevated access.",
            code="sensitive-data-restricted", column=pivot_dimension)

    # Aggregate/pivot outputs rename measure columns (``amount_sum``) but keep
    # group-key columns under their source name, which is the leak that matters
    # — the category axis is the group key.
    present = {c: t for c, t in masked.items() if any(c in row for row in rows)}
    if present:
        rows[:] = mask_rows(rows, present)
    return sorted(present)


def _pivot_dimension(definition: dict) -> str | None:
    """The column whose values a pivot definition turns into output columns."""
    if definition.get("kind") != "pivot":
        return None
    columns = (definition.get("params") or {}).get("columns")
    if isinstance(columns, dict):
        return columns.get("column")
    return columns if isinstance(columns, str) else None
