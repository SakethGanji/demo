"""DB-backed ``CheckLookups`` for the mistake checker: one snapshot per check, loaded up front."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CredentialModel, DataTableModel, WorkflowModel, WorkflowVersionModel
from ..repositories.variable_repository import VariableRepository
from .project_service import effective_environment, resolve_project
from .api_catalog import project_operations
from .workflow_checker import ChildWorkflow, StaticLookups
from .workflow_io import call_refs

#: how much of the call closure one check loads (a runaway or hostile graph must not turn a check into a table scan)
MAX_CLOSURE_WORKFLOWS = 300


async def build_lookups(
    session: AsyncSession,
    project_id: str | None,
    environment: str | None = None,
    team_id: str = "default",
    workflow_id: str | None = None,
    definition: dict[str, Any] | None = None,
) -> StaticLookups:
    """What exists for a workflow of ``project_id`` in ``environment``.

    ``environment`` is mapped onto the project the same way runs do (``effective_environment``); variables are
    the team + project rows (project wins), exactly the ``$vars`` a run would see. Tables are the project's,
    sub-workflows and credentials the team's. The egress allowlist is the project's (empty = unrestricted).

    Sub-workflow checks (Stage 8) need the workflow being checked: pass ``workflow_id`` (its id) and ``definition`` (the
    unsaved definition, default the saved draft). Only the call closure of that definition is loaded (children, their
    drafts, published versions and any pinned versions, transitively); without ``workflow_id`` / ``definition`` the
    lookups hold no children and the sub-workflow checks that need them are skipped.
    """
    project = await resolve_project(session, project_id) if project_id else None
    env = effective_environment(project, environment)
    rows = await VariableRepository(session).load_runtime_rows(team_id=team_id, environment=env, project_id=project.id if project else None)
    tables: dict[str, list[dict]] = {}
    if project is not None:
        for t in (await session.execute(select(DataTableModel).where(DataTableModel.project_id == project.id))).scalars():
            tables[t.name] = list(t.columns or [])
            tables[t.id] = tables[t.name]
    workflows = set((await session.execute(select(WorkflowModel.id).where(WorkflowModel.team_id == team_id))).scalars())
    credentials = set((await session.execute(select(CredentialModel.id).where(CredentialModel.team_id == team_id))).scalars())
    seed = definition
    if seed is None and workflow_id:
        row = await session.get(WorkflowModel, workflow_id)
        seed = row.draft_definition if row is not None else None
    children = await load_call_closure(session, team_id, project.id if project else None, seed) if seed else {}
    return StaticLookups(
        children=children,
        current_workflow_id=workflow_id,
        current_project_id=project.id if project else None,
        variables=set(rows),
        secrets={k for k, v in rows.items() if v.type == "secret"},
        tables=tables,
        workflows=workflows,
        credentials=credentials,
        api_specs=await project_operations(session, project.id if project else None),
        egress_allowlist=list(((getattr(project, "settings", None) or {}).get("egress_allowlist")) or []),
    )


async def load_call_closure(
    session: AsyncSession, team_id: str, project_id: str | None, definition: dict[str, Any] | None
) -> dict[str, ChildWorkflow]:
    """The workflows ``definition`` calls, transitively: each with its draft, the version ``latest`` stands for and every
    version some caller pins. A level costs a fixed number of queries, not one per workflow; the walk stops at
    ``MAX_CLOSURE_WORKFLOWS``. A workflow of another project is loaded as a name and project only (WF027 stops there), so
    a check never walks into another project, and an id that doesn't exist is simply absent (WF006 reports it)."""
    children: dict[str, ChildWorkflow] = {}
    asked: set[str] = set()  # ids already looked up (found or not)
    published_id: dict[str, int | None] = {}  # workflow id -> published version row id
    number_of: dict[int, int] = {}  # version row id -> version number
    needs: dict[str, set[str]] = {}  # workflow id -> version specs some definition asks for
    fetched: set[tuple[str, int]] = set()

    def absorb(d: dict[str, Any] | None) -> None:
        for r in call_refs(d):
            needs.setdefault(r.workflow_id, set()).add(r.spec)

    absorb(definition)
    while True:
        progressed = False
        new_ids = [w for w in needs if w not in asked][: max(MAX_CLOSURE_WORKFLOWS - len(asked), 0)]
        if new_ids:
            progressed = True
            asked.update(new_ids)
            q = select(WorkflowModel).where(WorkflowModel.id.in_(new_ids), WorkflowModel.team_id == team_id)
            same: list[str] = []
            for w in (await session.execute(q)).scalars():
                mine = project_id is None or w.project_id == project_id
                children[w.id] = ChildWorkflow(id=w.id, name=w.name, project_id=w.project_id, draft=w.draft_definition if mine else None)
                if mine:
                    same.append(w.id)
                    published_id[w.id] = w.published_version_id
                    absorb(w.draft_definition)
            if same:
                q2 = select(WorkflowVersionModel.id, WorkflowVersionModel.workflow_id, WorkflowVersionModel.version_number).where(
                    WorkflowVersionModel.workflow_id.in_(same))
                for vid, wid, num in (await session.execute(q2)).all():
                    number_of[vid] = num
                    children[wid].version_numbers.add(num)
                for wid in same:
                    pid = published_id.get(wid)
                    children[wid].published_version = number_of.get(pid) if pid is not None else None
        want: dict[str, set[int]] = {}
        for wid, asked_specs in needs.items():
            c = children.get(wid)
            if c is None or wid not in published_id:
                continue  # unknown, or another project: meta only
            nums = set()
            if "latest" in asked_specs and c.published_version is not None:
                nums.add(c.published_version)
            nums |= {int(sp) for sp in asked_specs if sp.isdigit() and int(sp) in c.version_numbers}
            nums = {n for n in nums if (wid, n) not in fetched}
            if nums:
                want[wid] = nums
        if want:
            q3 = select(WorkflowVersionModel.workflow_id, WorkflowVersionModel.version_number, WorkflowVersionModel.definition).where(
                WorkflowVersionModel.workflow_id.in_(list(want)))
            for wid, num, d in (await session.execute(q3)).all():
                if num in want.get(wid, ()):
                    fetched.add((wid, num))
                    children[wid].versions[num] = d
                    absorb(d)
                    progressed = True
        if not progressed:
            return children
