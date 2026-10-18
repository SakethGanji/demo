"""DB-backed ``CheckLookups`` for the mistake checker: one snapshot per check, loaded up front."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CredentialModel, DataTableModel, WorkflowModel
from ..repositories.variable_repository import VariableRepository
from .project_service import effective_environment, resolve_project
from .workflow_checker import StaticLookups


async def build_lookups(
    session: AsyncSession,
    project_id: str | None,
    environment: str | None = None,
    team_id: str = "default",
) -> StaticLookups:
    """What exists for a workflow of ``project_id`` in ``environment``.

    ``environment`` is mapped onto the project the same way runs do (``effective_environment``); variables are
    the team + project rows (project wins), exactly the ``$vars`` a run would see. Tables are the project's,
    sub-workflows and credentials the team's. The egress allowlist is the project's (empty = unrestricted).
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
    return StaticLookups(
        variables=set(rows),
        secrets={k for k, v in rows.items() if v.type == "secret"},
        tables=tables,
        workflows=workflows,
        credentials=credentials,
        egress_allowlist=list(((getattr(project, "settings", None) or {}).get("egress_allowlist")) or []),
    )
