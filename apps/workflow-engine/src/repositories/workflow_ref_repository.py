"""Repository for the ``workflow_refs`` index (DESIGN-STAGE8-REUSE section 5.1).

One row per ExecuteWorkflow node per (parent workflow, parent version or draft). Draft rows are replaced on every draft
save; version rows are written once at publish and never change. All writes are plain SQL on the caller's session and do
**not** commit: the caller owns the transaction.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: ``(node_name, child_workflow_id, version_spec)``
Ref = tuple[str, str, str]


class WorkflowRefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace(self, project_id: str, workflow_id: str, version_id: int | None, refs: Iterable[Ref]) -> int:
        """Make the stored rows of (workflow, version-or-draft) exactly ``refs``; returns the row count written."""
        rows = list(refs)
        await self._session.execute(
            text("""DELETE FROM workflow_refs
                    WHERE parent_workflow_id = :w AND parent_version_id IS NOT DISTINCT FROM CAST(:v AS integer)"""),
            {"w": workflow_id, "v": version_id},
        )
        if rows:
            await self._session.execute(
                text("""INSERT INTO workflow_refs
                          (project_id, parent_workflow_id, parent_version_id, node_name, child_workflow_id, version_spec)
                        VALUES (:p, :w, CAST(:v AS integer), :n, :c, :s)"""),
                [{"p": project_id, "w": workflow_id, "v": version_id, "n": n, "c": c, "s": s} for n, c, s in rows],
            )
        return len(rows)

    async def count(self) -> int:
        return int((await self._session.execute(text("SELECT count(*) FROM workflow_refs"))).scalar_one())

    async def draft_edges(self, project_id: str) -> list[dict[str, Any]]:
        """Draft rows of a project: ``{parent_workflow_id, node, child_workflow_id, version_spec}``."""
        return await self._rows(
            "WHERE r.project_id = :p AND r.parent_version_id IS NULL", {"p": project_id})

    async def published_edges(self, project_id: str) -> list[dict[str, Any]]:
        """Rows of every parent's *currently published* version (the live publish pointer)."""
        return await self._rows(
            """JOIN workflows w ON w.id = r.parent_workflow_id AND w.published_version_id = r.parent_version_id
               WHERE r.project_id = :p""", {"p": project_id})

    async def callers_of(self, child_workflow_id: str) -> list[dict[str, Any]]:
        """Every row that calls ``child_workflow_id`` (draft and version rows), with the parent's name and live pointer."""
        res = await self._session.execute(
            text("""SELECT r.parent_workflow_id, r.parent_version_id, r.node_name AS node, r.version_spec,
                           r.project_id, w.name AS parent_name,
                           (w.published_version_id IS NOT NULL AND w.published_version_id = r.parent_version_id)
                               AS parent_is_published
                    FROM workflow_refs r JOIN workflows w ON w.id = r.parent_workflow_id
                    WHERE r.child_workflow_id = :c
                    ORDER BY r.parent_workflow_id, r.parent_version_id NULLS FIRST, r.node_name"""),
            {"c": child_workflow_id},
        )
        return [dict(r) for r in res.mappings().all()]

    async def blocking_published_parents(self, child_workflow_id: str) -> list[dict[str, Any]]:
        """Parents whose *published* version calls the child (what blocks a delete, section 10.3)."""
        return [r for r in await self.callers_of(child_workflow_id)
                if r["parent_is_published"] and r["parent_workflow_id"] != child_workflow_id]

    async def _rows(self, tail: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        res = await self._session.execute(
            text(f"""SELECT r.parent_workflow_id, r.parent_version_id, r.node_name AS node,
                            r.child_workflow_id, r.version_spec
                     FROM workflow_refs r {tail} ORDER BY r.parent_workflow_id, r.node_name"""),
            params,
        )
        return [dict(r) for r in res.mappings().all()]
