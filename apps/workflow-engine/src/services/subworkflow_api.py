"""The logic behind the sub-workflow endpoints (DESIGN-STAGE8-REUSE sections 7.1, 11): the picker list, a workflow's I/O,
"used by", and convert-selection.

Convert selection plans with the pure ``subworkflow_extract.extract`` and persists the plan here:

* the new child workflow and the rewritten parent draft are written in ONE database transaction (one commit), so a failure
  leaves both as they were;
* the rewrite is written straight to the draft, so it bypasses the locked-test rule, and only for the remapped
  ``parent_definition["tests"]`` that the plan produced (the rest of a save keeps its rules);
* tests that assert on nodes that moved are marked ``stale`` (kept, not deleted); approving one again is the review;
* after the commit the child is published through the normal publish (checks plus tests). When that succeeds the new call
  node is set to ``latest``; otherwise it stays on ``draft`` and the response carries ``child_publish_errors``.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, text

from ..core.exceptions import WorkflowNotFoundError
from ..db.models import ProjectModel, WorkflowModel, WorkflowVersionModel
from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition

logger = logging.getLogger(__name__)


class SubworkflowApiError(Exception):
    """An error the route turns into an HTTP response: ``status`` and a ``detail`` dict."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **extra}


def parse_version(raw: str | None) -> str | int | None:
    """``'draft' | 'latest' | <n>`` from a query value; None when omitted. Anything else is a 422."""
    if raw is None or raw == "":
        return None
    v = raw.strip().lower()
    if v in ("draft", "latest"):
        return v
    if v.isdigit() and int(v) >= 1:
        return int(v)
    raise SubworkflowApiError(422, "INVALID_VERSION", "version must be 'draft', 'latest' or a version number")


async def definition_for(session: Any, wf: WorkflowModel, version: str | int | None) -> tuple[dict[str, Any], int | None]:
    """``(definition, version_number | None)`` of a pin. Omitted ``version`` = the published one, else the draft."""
    if version == "draft":
        return wf.draft_definition or {}, None
    if version in (None, "latest"):
        if wf.published_version_id is None:
            if version == "latest":
                raise SubworkflowApiError(404, "SUBWORKFLOW_VERSION_MISSING", f"'{wf.name}' has never been published.")
            return wf.draft_definition or {}, None
        row = await session.get(WorkflowVersionModel, wf.published_version_id)
    else:
        row = (await session.execute(select(WorkflowVersionModel).where(
            WorkflowVersionModel.workflow_id == wf.id, WorkflowVersionModel.version_number == int(version)))).scalars().first()
        if row is None:
            raise SubworkflowApiError(404, "SUBWORKFLOW_VERSION_MISSING", f"'{wf.name}' has no version {version}.")
    return (row.definition if row else {}) or {}, (row.version_number if row else None)


def io_findings(wf_name: str, io: Any) -> list[dict[str, Any]]:
    """Checker-shaped findings about a workflow *as a sub-workflow* (WF021, WF025, WF026, WF029)."""
    out: list[dict[str, Any]] = []
    if not io.has_trigger:
        out.append({"code": "WF021", "severity": "error", "node": None,
                    "message": f"'{wf_name}' can't be called: it has no Execute Workflow Trigger."})
    if io.has_human:
        out.append({"code": "WF025", "severity": "error", "node": None,
                    "message": f"'{wf_name}' has an Approval step; approvals must be in the top-level workflow."})
    if io.output_note:
        out.append({"code": "WF026", "severity": "warning", "node": None, "message": io.output_note})
    if io.has_trigger and (io.input_source == "passthrough" or not io.input_fields):
        out.append({"code": "WF029", "severity": "info", "node": None,
                    "message": f"'{wf_name}' declares no inputs, so callers can't be type-checked."})
    return out


class SubworkflowApi:
    def __init__(self, session: Any) -> None:
        self._s = session

    # ── GET /workflows/{id}/io ──────────────────────────────────────────────

    async def workflow_io(self, workflow_id: str, version: str | int | None) -> dict[str, Any]:
        from .check_lookups import build_lookups
        from .workflow_io import derive_io

        wf = await self._s.get(WorkflowModel, workflow_id)
        if wf is None:
            raise WorkflowNotFoundError(workflow_id)
        definition, number = await definition_for(self._s, wf, version)
        lookups = await build_lookups(self._s, wf.project_id, None, team_id=wf.team_id or "default",
                                      workflow_id=workflow_id, definition=definition)
        io = derive_io(definition, lookups)
        return {"workflow_id": workflow_id, "version_number": number, "input": io.input, "output": io.output,
                "input_fields": io.input_fields, "output_fields": io.output_fields, "callable": io.callable,
                "reasons": io.reasons, "findings": io_findings(wf.name, io)}

    # ── GET /projects/{p}/subworkflows ──────────────────────────────────────

    async def list_for_project(self, project: ProjectModel, q: str | None = None, callable_only: bool = False) -> list[dict[str, Any]]:
        from .check_lookups import build_lookups
        from .workflow_io import call_refs, derive_io

        stmt = select(WorkflowModel).where(WorkflowModel.project_id == project.id).order_by(WorkflowModel.name)
        rows = list((await self._s.execute(stmt)).scalars().all())
        if q:
            needle = q.lower()
            rows = [w for w in rows if needle in (w.name or "").lower() or needle in (w.description or "").lower()]
        counts = await self._used_by_counts(project.id)
        numbers = {}
        pub_ids = [w.published_version_id for w in rows if w.published_version_id is not None]
        if pub_ids:
            for vid, num, definition in (await self._s.execute(select(
                    WorkflowVersionModel.id, WorkflowVersionModel.version_number, WorkflowVersionModel.definition
            ).where(WorkflowVersionModel.id.in_(pub_ids)))).all():
                numbers[vid] = (num, definition)
        shared = await build_lookups(self._s, project.id, None, team_id=project.team_id or "default")
        out: list[dict[str, Any]] = []
        for w in rows:
            published = numbers.get(w.published_version_id) if w.published_version_id is not None else None
            definition = published[1] if published else (w.draft_definition or {})
            lookups = shared
            if call_refs(definition):  # a child with its own calls needs its closure to be typed and scanned for Approvals
                lookups = await build_lookups(self._s, project.id, None, team_id=project.team_id or "default",
                                              workflow_id=w.id, definition=definition)
            io = derive_io(definition, lookups)
            if callable_only and not io.callable:
                continue
            out.append({
                "id": w.id, "name": w.name, "description": w.description,
                "published_version": published[0] if published else None,
                "io_source": "published" if published else "draft",
                "callable": io.callable, "reasons": io.reasons, "inputs": io.input_fields,
                "output_fields": io.output_fields, "has_approval": io.has_human, "used_by_count": counts.get(w.id, 0),
            })
        return out

    async def _used_by_counts(self, project_id: str) -> dict[str, int]:
        try:
            res = await self._s.execute(text(
                """SELECT child_workflow_id, count(DISTINCT (parent_workflow_id, node_name)) FROM workflow_refs
                   WHERE project_id = :p AND parent_workflow_id <> child_workflow_id GROUP BY child_workflow_id"""), {"p": project_id})
            return {k: int(v) for k, v in res.all()}
        except Exception:
            logger.warning("could not count callers", exc_info=True)
            await self._s.rollback()
            return {}

    # ── GET /workflows/{id}/used-by ─────────────────────────────────────────

    async def used_by(self, workflow_id: str) -> list[dict[str, Any]]:
        from .workflow_refs import used_by

        if await self._s.get(WorkflowModel, workflow_id) is None:
            raise WorkflowNotFoundError(workflow_id)
        return await used_by(self._s, workflow_id)

    # ── POST /workflows/{id}/extract-subworkflow ────────────────────────────

    async def extract(self, workflow_id: str, nodes: list[str], name: str, dry_run: bool,
                      definition: dict[str, Any] | None, workflow_service: Any) -> dict[str, Any]:
        from ..utils.ids import workflow_id as new_workflow_id
        from .check_lookups import build_lookups
        from .subworkflow_extract import ExtractPlan, bind_child_id, extract

        parent = await self._s.get(WorkflowModel, workflow_id)
        if parent is None:
            raise WorkflowNotFoundError(workflow_id)
        base = copy.deepcopy(definition) if definition is not None else copy.deepcopy(parent.draft_definition or {})
        if definition is not None:
            base = workflow_to_definition(definition_to_workflow(base, parent.name, workflow_id, parent.description))
        lookups = await build_lookups(self._s, parent.project_id, None, team_id=parent.team_id or "default",
                                      workflow_id=workflow_id, definition=base)
        child_id = new_workflow_id()
        kwargs = dict(parent_workflow_id=workflow_id, parent_name=parent.name)
        result = extract(base, nodes, name, lookups, child_workflow_id=child_id, **kwargs)
        if isinstance(result, ExtractPlan):
            sample = await self._sample_input(workflow_id, result)
            if sample:
                result = extract(base, nodes, name, lookups, child_workflow_id=child_id, sample_input=sample, **kwargs)
        if not isinstance(result, ExtractPlan):
            raise SubworkflowApiError(422, result.code, result.message, nodes=result.nodes)
        plan = result
        body = self._plan_body(plan)
        if dry_run:
            return body

        # ── persist: the child and the rewritten parent in ONE transaction ──
        now = datetime.now()
        parent_def = bind_child_id(plan, child_id)
        parent_def = self._stale_tests(parent_def, plan)
        child_canon = workflow_to_definition(definition_to_workflow(plan.child_definition, name, child_id, None))
        parent_canon = workflow_to_definition(definition_to_workflow(parent_def, parent.name, workflow_id, parent.description))
        if (parent.draft_definition or {}).get("interface") and "interface" not in parent_canon:
            parent_canon["interface"] = parent.draft_definition["interface"]
        self._set_call_version(parent_canon, plan.call_node, "draft")  # until the child is published
        try:
            self._s.add(WorkflowModel(
                id=child_id, team_id=parent.team_id, project_id=parent.project_id, folder_id=parent.folder_id, name=name,
                description=None, active=False, draft_definition=child_canon, created_at=now, updated_at=now,
                created_by=parent.updated_by or parent.created_by))
            parent.draft_definition = parent_canon  # a new dict, so the JSONB change is detected
            parent.updated_at = now
            await self._s.flush()
            from .workflow_refs import sync_refs
            await sync_refs(self._s, child_id, None, child_canon, project_id=parent.project_id)
            await sync_refs(self._s, workflow_id, None, parent_canon, project_id=parent.project_id)
            await self._s.commit()
        except Exception:
            await self._s.rollback()
            raise

        # ── then try to publish the child; success pins the call to 'latest' ──
        publish_errors: list[dict[str, Any]] = []
        version_number: int | None = None
        try:
            await workflow_service.publish(child_id, message="Created by convert selection")
            child = await self._s.get(WorkflowModel, child_id)
            if child is not None and child.published_version_id is not None:
                version_number = (await self._s.get(WorkflowVersionModel, child.published_version_id)).version_number
        except Exception as e:  # a failed check or test: the child stays a draft and the call stays on 'draft'
            publish_errors = self._publish_errors(e)
            await self._s.rollback()
        call_version = "draft"
        if version_number is not None:
            parent = await self._s.get(WorkflowModel, workflow_id)
            latest = copy.deepcopy(parent.draft_definition or {})
            self._set_call_version(latest, plan.call_node, "latest")
            parent.draft_definition = latest
            parent.updated_at = datetime.now()
            from .workflow_refs import sync_refs
            await sync_refs(self._s, workflow_id, None, latest, project_id=parent.project_id)
            await self._s.commit()
            parent_canon, call_version = latest, "latest"
        body["parent_definition"] = parent_canon
        body["child_definition"] = child_canon
        body.update(child_workflow_id=child_id, child_version_number=version_number,
                    child_publish_errors=publish_errors, call_version=call_version)
        return body

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _plan_body(plan: Any) -> dict[str, Any]:
        full = plan.to_dict()
        slim = {k: v for k, v in full.items() if k not in ("child_definition", "parent_definition")}
        return {"plan": slim, "child_definition": full["child_definition"], "parent_definition": full["parent_definition"],
                "warnings": full["warnings"], "affected_tests": full["affected_tests"]}

    @staticmethod
    def _set_call_version(definition: dict[str, Any], node_name: str, version: str) -> None:
        for n in definition.get("nodes") or []:
            if n.get("name") == node_name:
                n.setdefault("parameters", {})["version"] = version

    @staticmethod
    def _stale_tests(parent_def: dict[str, Any], plan: Any) -> dict[str, Any]:
        """Mark the tests the plan lists as affected: they assert on nodes that moved into the sub-workflow."""
        affected = {a.get("id"): a for a in plan.affected_tests if a.get("id")}
        if not affected:
            return parent_def
        parent_def = copy.deepcopy(parent_def)
        for t in parent_def.get("tests") or []:
            a = affected.get(t.get("id")) if isinstance(t, dict) else None
            if a:
                t["stale"] = True
                t["stale_reason"] = (f"{', '.join(a.get('nodes') or [])} moved into the sub-workflow '{plan.child_name}'. "
                                     "Review this test, then approve it again.")
        return parent_def

    @staticmethod
    def _publish_errors(e: Exception) -> list[dict[str, Any]]:
        findings = getattr(e, "findings", None)
        if findings:
            return [{"kind": "check", **f} if isinstance(f, dict) else {"kind": "check", "message": str(f)} for f in findings]
        failed = getattr(e, "failed_tests", None)
        if failed:
            return [{"kind": "test", **f} for f in failed]
        return [{"kind": "publish", "message": getattr(e, "message", None) or str(e)}]

    async def _sample_input(self, workflow_id: str, plan: Any) -> list[dict[str, Any]] | None:
        """Items that reached the entry node in the parent's latest successful run (seeds the child's draft test)."""
        try:
            from ..repositories.execution_repository import ExecutionRepository

            if not plan.entry:
                return None
            rec = await ExecutionRepository(self._s).find_latest_successful(workflow_id)
            if rec is None:
                return None
            sel = set(plan.selection)
            parent_def = (await self._s.get(WorkflowModel, workflow_id)).draft_definition or {}
            sources = [c["source_node"] for c in parent_def.get("connections") or []
                       if c.get("target_node") == plan.entry and c.get("source_node") not in sel]
            for src in sources:
                items = rec.node_data.get(src)
                if items:
                    return [dict(i.json) for i in items[:3] if isinstance(i.json, dict)] or None
        except Exception:
            logger.warning("could not sample the latest run for convert-selection", exc_info=True)
        return None
