"""``run_workflow``: backend code calls a workflow of its own project through the broker.

It is a broker function without a node of its own: the broker runs it as an ``ExecuteWorkflow`` node (Stage 8) on a
one-node workflow, through the same DB sub-workflow invoker every other run site uses. So the child's input goes
through its Execute Workflow Trigger, the guards (not callable, depth, cycle, approval) apply, the child gets its own
execution row, and a failed child is a ``SUBWORKFLOW_*`` error with the child's execution id and failing node.

What this module decides, and nothing else:

* the function's declaration (catalogue entry, SDK parameters);
* which workflow a reference names: an id or an exact name, only inside the caller's project (another project's
  workflow is ``workflow_not_found``, exactly like a missing one);
* which version runs: a draft environment runs the draft; a release runs the version that release pinned, else the
  workflow's latest published version (never a draft);
* the HTTP status of each sub-workflow error code.
"""

from __future__ import annotations

from typing import Any

from .policy import PolicyError

FUNCTION = "run_workflow"
#: The function's identity in tokens, ``allowed_functions`` and the broker_calls log (``snake(NODE_TYPE) == FUNCTION``).
#: Not a registered node: the raw ``ExecuteWorkflow`` node stays out of reach of SDK callers.
NODE_TYPE = "RunWorkflow"
#: The node name the call runs under; the child row's ``parent_node_name`` and the release pin key use it.
CALL_NODE = "run_workflow"

DESCRIPTION = ("Run a workflow of this project and return its output items. The workflow needs an Execute Workflow "
               "Trigger; a draft environment runs its draft, a release runs the version the release pinned (else its "
               "latest published version).")


def properties() -> list[Any]:
    from ..nodes.base import NodeProperty

    return [
        NodeProperty(display_name="Workflow", name="workflow", type="string", required=True,
                     description="The workflow's name or id (a workflow of this project)"),
        NodeProperty(display_name="Input", name="input", type="json",
                     description="The input: one object (one item) or a list of objects (one item each), "
                                 "received by the workflow's Execute Workflow Trigger"),
        NodeProperty(display_name="Timeout (seconds)", name="timeoutSeconds", type="number", default=300,
                     description="How long the workflow may run (at most 3600)"),
    ]


#: Sub-workflow error code -> broker HTTP status. Anything else (``SUBWORKFLOW_FAILED``, a node code) is 502.
STATUS_OF = {
    "SUBWORKFLOW_NOT_FOUND": 404,
    "SUBWORKFLOW_VERSION_MISSING": 409,
    "SUBWORKFLOW_NOT_CALLABLE": 422,
    "SUBWORKFLOW_INPUT_INVALID": 422,
    "APPROVAL_IN_SUBWORKFLOW": 422,
    "RECURSION_LIMIT": 508,
    "SUBWORKFLOW_CYCLE": 508,
    "SUBWORKFLOW_TIMEOUT": 504,
}


def input_items(value: Any) -> list[Any]:
    """``input`` as the child's items: None = one empty item, an object = one item, a list = one item per entry."""
    if value is None:
        return [{}]
    if isinstance(value, list):
        return value or [{}]
    return [value]


async def resolve(session: Any, project_id: str, ref: Any) -> Any:
    """The ``WorkflowModel`` ``ref`` names in ``project_id``: by id, else by exact name. 404 / 409 as ``PolicyError``."""
    from sqlalchemy import select

    from ..db.models import WorkflowModel

    if not isinstance(ref, str) or not ref.strip():
        raise PolicyError("invalid_request", 400, "run_workflow needs `workflow`: a workflow name or id")
    ref = ref.strip()
    model = await session.get(WorkflowModel, ref)
    if model is not None and model.project_id == project_id:
        return model
    rows = (await session.execute(select(WorkflowModel).where(
        WorkflowModel.project_id == project_id, WorkflowModel.name == ref))).scalars().all()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise PolicyError("workflow_ambiguous", 409, f"{len(rows)} workflows in this project are named '{ref}'; call it by id",
                          workflow=ref, workflow_ids=sorted(r.id for r in rows))
    # 404-hides-existence: a workflow of another project reads exactly like one that doesn't exist
    raise PolicyError("workflow_not_found", 404, f"workflow '{ref}' not found in this project", workflow=ref)


async def release_for(session: Any, claims: Any, project: Any) -> tuple[bool, Any]:
    """``(in_release, release | None)``: the release the calling code runs in, if any.

    The token names it when the caller ran from one (a python route of a release, a PythonStep of a release-pinned run);
    otherwise a ``kind: release`` environment means its active release, and no active release means "live, no pins"."""
    from ..db.models import ReleaseModel
    from ..services.project_service import env_kind

    rid = getattr(claims, "release_id", None)
    if rid:
        release = await session.get(ReleaseModel, rid)
        if release is not None and release.project_id == claims.project_id:
            return True, release
    if project is None or env_kind(project, claims.environment) != "release":
        return False, None
    from ..services.release_service import ReleaseService

    return True, await ReleaseService(session).active_release(claims.project_id, claims.environment)


async def target(session: Any, claims: Any, project: Any, ref: Any) -> tuple[Any, str | int, dict[str, Any]]:
    """``(workflow model, version spec, extras for the call)``.

    Draft environment: ``"draft"``, no extras. Release: the version the snapshot pinned for this workflow, else the
    latest published one, as a concrete version number; the extras are the release's (module sources, sub-workflow pins),
    so the child and its own sub-workflow calls run exactly as the release would run them, plus a pin for this call."""
    from ..services.subworkflow_service import pin_key, version_number_of

    model = await resolve(session, claims.project_id, ref)
    in_release, release = await release_for(session, claims, project)
    if not in_release:
        return model, "draft", {}
    snapshot = (release.snapshot or {}) if release is not None else {}
    pinned = {w.get("workflow_id"): w.get("version_id") for w in snapshot.get("workflows") or [] if isinstance(w, dict)}
    vid = pinned.get(model.id)
    if vid in (None, -1):
        vid = model.published_version_id
    if vid is None:
        raise PolicyError("SUBWORKFLOW_VERSION_MISSING", 409,
                          f"Sub-workflow '{model.name}' has never been published, and a release only runs published versions.",
                          workflow_id=model.id)
    number = await version_number_of(session, int(vid))
    if number is None:
        raise PolicyError("SUBWORKFLOW_VERSION_MISSING", 409, f"Sub-workflow '{model.name}' has no version {vid}.",
                          workflow_id=model.id)
    extras: dict[str, Any] = {}
    if release is not None:
        from ..services.route_dispatcher import release_extras

        extras = release_extras(release) or {}
        if "release_pins" in extras:
            extras["release_pins"] = {**extras["release_pins"], pin_key(None, CALL_NODE): int(vid)}
    return model, int(number), extras
