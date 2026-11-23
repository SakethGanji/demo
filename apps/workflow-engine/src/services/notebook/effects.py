"""Write classification of cells (BUILD-PLAN §1.2, conservative: anything unknown writes).

| kind              | effect                                                                                       |
|-------------------|----------------------------------------------------------------------------------------------|
| change, store     | write                                                                                        |
| http              | write unless the method is GET/HEAD                                                          |
| workflow          | write if any node of the pinned version is database / human / subworkflow, SendEmail, or an   |
|                   | HttpRequest whose method is not GET/HEAD (ExecuteWorkflow is followed into the child)        |
| browser           | read unless ``spec.writes``                                                                  |
| for_each          | write if any body cell writes                                                                |
| query             | read                                                                                         |
| rule, data, note, approval, person | none; wait: none, or read when it polls a query (``mode: condition``)       |
| ask               | llm                                                                                          |
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...engine.effects import effect_of

READ_METHODS = {"GET", "HEAD"}
_NONE_KINDS = {"rule", "data", "note", "approval", "person"}  # approval / person: removed kinds, stored cells only


def node_writes(node: dict[str, Any]) -> bool:
    t = node.get("type") or ""
    params = node.get("parameters") or {}
    if t == "HttpRequest":
        return str(params.get("method") or "GET").upper() not in READ_METHODS
    if t == "SendEmail":
        return True
    if t == "Postgres":
        op = str(params.get("operation") or "executeQuery").lower()
        q = str(params.get("query") or "").lstrip().lower()
        return not (op in ("query", "executequery", "select") and (q.startswith("select") or q.startswith("with")))
    return effect_of(t) in ("database", "human", "subworkflow") and t != "ExecuteWorkflow"


async def _definition(session: AsyncSession, workflow_id: str, version: Any) -> dict[str, Any] | None:
    from ...db.models import WorkflowModel, WorkflowVersionModel
    from sqlalchemy import select

    wf = await session.get(WorkflowModel, workflow_id)
    if wf is None:
        return None
    vrow = None
    if isinstance(version, int) and not isinstance(version, bool):
        vrow = (await session.execute(select(WorkflowVersionModel).where(
            WorkflowVersionModel.workflow_id == workflow_id, WorkflowVersionModel.version_number == version))).scalars().first()
    elif version in ("published", "latest", None) and wf.published_version_id:
        vrow = await session.get(WorkflowVersionModel, wf.published_version_id)
    if vrow is not None:
        return vrow.definition or {}
    return wf.draft_definition or {}


async def workflow_writes(session: AsyncSession, workflow_id: str, version: Any = "published",
                          _seen: frozenset[str] = frozenset()) -> bool:
    """True when running that workflow (version) may write somewhere. Unknown workflow = write."""
    if not workflow_id:
        return True
    if workflow_id in _seen:  # a cycle: the nodes are already being looked at
        return False
    d = await _definition(session, workflow_id, version)
    if d is None:
        return True
    for n in d.get("nodes") or []:
        if n.get("disabled"):
            continue
        if n.get("type") == "ExecuteWorkflow":
            p = n.get("parameters") or {}
            child = p.get("workflowId")
            if isinstance(child, dict):
                child = child.get("value") or child.get("id")
            if not isinstance(child, str) or "{{" in child:
                return True
            ver = p.get("version") or "latest"
            if ver == "draft":
                ver = None
            if await workflow_writes(session, child, ver, _seen | {workflow_id}):
                return True
            continue
        if node_writes(n):
            return True
    return False


def static_effect(kind: str, spec: dict[str, Any], children_effects: list[str] | None = None) -> str | None:
    """The effect when it can be told from the spec alone; None for ``workflow`` (needs the DB)."""
    spec = spec or {}
    if kind in ("change", "store"):
        return "write"
    if kind == "http":
        return "read" if str(spec.get("method") or "GET").upper() in READ_METHODS else "write"
    if kind == "browser":
        return "write" if spec.get("writes") else "read"
    if kind == "query":
        return "read"
    if kind == "ask":
        return "llm"
    if kind in _NONE_KINDS:
        return "none"
    if kind == "wait":
        return "read" if spec.get("mode") == "condition" else "none"
    if kind == "for_each":
        effs = children_effects or []
        if "write" in effs:
            return "write"
        if "read" in effs:
            return "read"
        if "llm" in effs:
            return "llm"
        return "none"
    if kind == "workflow":
        return None
    return "write"


async def cell_effect(session: AsyncSession, kind: str, spec: dict[str, Any],
                      children: list[tuple[str, dict[str, Any]]] | None = None) -> str:
    """``children``: ``[(kind, spec)]`` of a for-each body."""
    if kind == "for_each":
        effs = [await cell_effect(session, k, s) for k, s in (children or [])]
        return static_effect(kind, spec, effs) or "write"
    eff = static_effect(kind, spec)
    if eff is not None:
        return eff
    # workflow
    wid = (spec or {}).get("workflow_id")
    if not isinstance(wid, str) or "{{" in wid:
        return "write"
    return "write" if await workflow_writes(session, wid, (spec or {}).get("version", "published")) else "read"


def is_write(effect: str) -> bool:
    return effect == "write"
