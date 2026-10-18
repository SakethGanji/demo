"""Workflow service for business logic."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.exceptions import (
    WorkflowNotFoundError,
    WorkflowExecutionError,
    WorkflowInactiveError,
    ValidationError,
    WorkflowHasErrorsError,
)
from ..engine.types import (
    Workflow,
    NodeData,
)
from ..engine.types import run_status
from ..engine.graph import disabled_trigger_message
from ..engine.workflow_runner import WorkflowRunner
from ..schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    WorkflowListItem,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowPublishResponse,
    WorkflowPublishResult,
)
from ..schemas.execution import ExecutionResponse, ExecutionErrorSchema
from ..schemas.workflow import VersionListItem, VersionDetailResponse

if TYPE_CHECKING:
    from ..repositories import WorkflowRepository, ExecutionRepository
    from .node_service import NodeService


logger = logging.getLogger(__name__)


class WorkflowService:
    """Service for workflow operations."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
        node_service: NodeService,
        node_registry: object | None = None,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._execution_repo = execution_repo
        self._node_service = node_service
        self._node_registry = node_registry

    async def list_workflows(self, folder_id: str | None = None, project_id: str | None = None) -> list[WorkflowListItem]:
        """List all workflows, optionally filtered by folder / project."""
        workflows = await self._workflow_repo.list(folder_id=folder_id, project_id=project_id)
        handler_for = await self._handler_index([w.id for w in workflows])
        return [
            WorkflowListItem(
                id=w.id,
                name=w.name,
                active=w.active,
                webhook_url=f"/webhook/{w.id}",
                project_id=w.project_id,
                description=w.workflow.description,
                handler_for=handler_for.get(w.id, []),
                node_count=len(w.workflow.nodes),
                created_at=w.created_at.isoformat(),
                updated_at=w.updated_at.isoformat(),
            )
            for w in workflows
        ]

    async def get_workflow(self, workflow_id: str) -> WorkflowDetailResponse:
        """Get a workflow by ID."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowDetailResponse(
            id=stored.id,
            name=stored.name,
            active=stored.active,
            webhook_url=f"/webhook/{stored.id}",
            project_id=stored.project_id,
            definition=self._workflow_to_dict(stored.workflow),
            created_at=stored.created_at.isoformat(),
            updated_at=stored.updated_at.isoformat(),
        )

    async def _resolve_project_id(self, ref: str | None) -> str | None:
        if not ref:
            return None
        from .project_service import resolve_project
        p = await resolve_project(self._workflow_repo._session, ref)
        if not p:
            raise ValidationError(f"Project '{ref}' not found", field="project_id")
        return p.id

    async def create_workflow(self, request: WorkflowCreateRequest, created_by: str | None = None) -> WorkflowResponse:
        """Create a new workflow."""
        self._validate_workflow(request)

        internal_workflow = self._request_to_workflow(request)
        project_id = await self._resolve_project_id(getattr(request, "project_id", None))
        stored = await self._workflow_repo.create(
            internal_workflow, folder_id=getattr(request, "folder_id", None), project_id=project_id,
            created_by=created_by,
        )
        # the draft's workflow_refs rows are written by WorkflowRepository.create, in the same transaction

        return WorkflowResponse(
            id=stored.id,
            name=stored.name,
            active=stored.active,
            webhook_url=f"/webhook/{stored.id}",
            project_id=stored.project_id,
            created_at=stored.created_at.isoformat(),
        )

    async def update_workflow(
        self, workflow_id: str, request: WorkflowUpdateRequest
    ) -> WorkflowDetailResponse:
        """Update an existing workflow."""
        existing = await self._workflow_repo.get(workflow_id)
        if not existing:
            raise WorkflowNotFoundError(workflow_id)

        # Validate updated nodes/connections if provided
        self._validate_update(request, existing)

        tests, test_defaults = self._merge_tests(existing.workflow, request)

        # Build updated workflow from existing + request
        internal_workflow = Workflow(
            name=request.name or existing.workflow.name,
            nodes=[n.to_dataclass() for n in request.nodes] if request.nodes else existing.workflow.nodes,
            connections=[c.to_dataclass() for c in request.connections] if request.connections else existing.workflow.connections,
            id=workflow_id,
            description=request.description or existing.workflow.description,
            settings=request.settings or existing.workflow.settings,
            tests=tests,
            test_defaults=test_defaults,
            interface=existing.workflow.interface,       # Stage 5: edited only through the interface endpoints
        )

        project_id = await self._resolve_project_id(getattr(request, "project_id", None))
        if project_id and project_id != existing.project_id:
            await self._check_movable(workflow_id)
            await self._check_not_called(workflow_id)  # a move breaks its callers like a delete does (DESIGN 10.3)
        updated = await self._workflow_repo.update(
            workflow_id, internal_workflow, folder_id=getattr(request, "folder_id", None), project_id=project_id,
        )
        if not updated:
            raise WorkflowNotFoundError(workflow_id)
        if project_id and project_id != existing.project_id:
            await self._move_refs(workflow_id, project_id)

        return WorkflowDetailResponse(
            id=updated.id,
            name=updated.name,
            active=updated.active,
            webhook_url=f"/webhook/{updated.id}",
            project_id=updated.project_id,
            definition=self._workflow_to_dict(updated.workflow),
            created_at=updated.created_at.isoformat(),
            updated_at=updated.updated_at.isoformat(),
        )

    async def _sync_draft_refs(self, workflow_id: str, before: dict[str, Any] | None = None) -> None:
        """Keep the ``workflow_refs`` draft rows in step with the saved draft (DESIGN-STAGE8-REUSE 5.1).

        Only for writes that bypass ``WorkflowRepository`` (``restore_version`` sets the model directly); repository writes
        index themselves in ``WorkflowRepository._index_draft``.

        ``before`` is the definition the save replaced: a save that neither had nor has a sub-workflow call has nothing
        to index, so it costs no query (most saves)."""
        from .workflow_refs import refs_of, sync_refs

        session = self._workflow_repo._session
        model = await self._workflow_repo.get_model(workflow_id)
        if model is None:
            return
        if before is not None and not refs_of(before) and not refs_of(model.draft_definition):
            return
        await sync_refs(session, workflow_id, None, model.draft_definition, project_id=model.project_id)
        await session.commit()

    @staticmethod
    def _merge_tests(existing: Workflow, request: WorkflowUpdateRequest) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Tests and test_defaults after a save: renames first, then the lock rules (409 via LockedTestsError)."""
        from ..engine.test_mode import locked_test_ids, rename_node_in_test_defaults, rename_node_in_tests
        from ..schemas.workflow import normalize_tests

        stored = normalize_tests(existing.tests)
        defaults = dict(existing.test_defaults or {})
        for old, new in (request.node_renames or {}).items():
            stored = rename_node_in_tests(stored, old, new)
            defaults = rename_node_in_test_defaults(defaults, old, new)
        if request.tests is None:
            tests = stored
        else:
            tests = [t.to_stored() for t in request.tests]
            if bad := locked_test_ids(stored, tests):
                from ..engine.test_mode import LockedTestsError

                raise LockedTestsError(bad)
        if request.test_defaults is not None:
            defaults = request.test_defaults.to_stored()
        return tests, defaults

    async def _set_test_state(self, workflow_id: str, test_id: str, change: dict[str, Any], drop: tuple[str, ...] = ()) -> WorkflowDetailResponse:
        """Apply ``change`` (and remove the ``drop`` keys) on one stored test and save the draft."""
        from ..engine.test_mode import TestNotFoundError
        from ..schemas.workflow import normalize_tests

        existing = await self._workflow_repo.get(workflow_id)
        if not existing:
            raise WorkflowNotFoundError(workflow_id)
        tests = normalize_tests(existing.workflow.tests)
        target = next((t for t in tests if t["id"] == test_id), None)
        if target is None:
            raise TestNotFoundError(test_id)
        for key in drop:
            target.pop(key, None)
        target.update(change)
        wf = existing.workflow
        wf.tests = normalize_tests(tests)
        updated = await self._workflow_repo.update(workflow_id, wf)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)
        return await self.get_workflow(workflow_id)

    async def approve_test(self, workflow_id: str, test_id: str, approved_by: str) -> WorkflowDetailResponse:
        """Approve (and so lock) one test."""
        return await self._set_test_state(workflow_id, test_id, {
            "status": "approved", "locked": True, "approved_by": approved_by,
            "approved_at": datetime.now().isoformat(),
        }, drop=("stale", "stale_reason"))   # approving is the review a stale test waits for

    async def unlock_test(self, workflow_id: str, test_id: str, by: str) -> WorkflowDetailResponse:
        """Unlock one test: back to a draft, approval cleared."""
        return await self._set_test_state(workflow_id, test_id, {"status": "draft", "locked": False},
                                          drop=("approved_by", "approved_at"))

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow (refused while it is an API route handler)."""
        if await self._workflow_repo.get(workflow_id):
            await self._check_movable(workflow_id)
            await self._check_not_called(workflow_id)
        deleted = await self._workflow_repo.delete(workflow_id)
        if not deleted:
            raise WorkflowNotFoundError(workflow_id)
        return True

    async def _move_refs(self, workflow_id: str, project_id: str) -> None:
        """The workflow's own call rows (draft and every version) follow it into its new project."""
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        session = self._workflow_repo._session
        try:
            async with session.begin_nested():
                await session.execute(text("UPDATE workflow_refs SET project_id = :p WHERE parent_workflow_id = :w"),
                                      {"p": project_id, "w": workflow_id})
            await session.commit()
        except ProgrammingError as e:
            if "workflow_refs" not in str(e):
                raise

    async def _check_not_called(self, workflow_id: str) -> None:
        """409 ``WORKFLOW_IN_USE`` while a published parent version or a live release snapshot calls it (section 10.3).

        Draft-only references do not block: the parent gets a checker finding on its next check."""
        from .workflow_refs import WorkflowInUseError, blocking_references

        used_by = await blocking_references(self._workflow_repo._session, workflow_id)
        if used_by:
            raise WorkflowInUseError(used_by)

    async def list_versions(self, workflow_id: str) -> list[VersionListItem]:
        """List all versions for a workflow."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        from ..repositories.version_repository import VersionRepository
        version_repo = VersionRepository(self._workflow_repo._session)
        versions = await version_repo.list_versions(workflow_id)
        return [
            VersionListItem(
                id=v.id,
                version_number=v.version_number,
                message=v.message,
                created_by=v.created_by,
                created_at=v.created_at.isoformat(),
            )
            for v in versions
        ]

    async def get_version(self, workflow_id: str, version_id: int) -> VersionDetailResponse:
        """Get a specific version with its definition."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        from ..repositories.version_repository import VersionRepository
        version_repo = VersionRepository(self._workflow_repo._session)
        version = await version_repo.get(version_id)
        if not version or version.workflow_id != workflow_id:
            raise WorkflowNotFoundError(f"version {version_id}")

        return VersionDetailResponse(
            id=version.id,
            version_number=version.version_number,
            message=version.message,
            created_by=version.created_by,
            created_at=version.created_at.isoformat(),
            definition=version.definition,
        )

    async def publish(self, workflow_id: str, message: str | None = None) -> WorkflowPublishResult:
        """Publish the current draft as a new immutable version.

        Creates a version snapshot, sets active=true, and syncs triggers.
        Can be called multiple times — each call creates a new version.
        """
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        if msg := disabled_trigger_message(stored.workflow.nodes):
            raise ValidationError(msg, field="definition")

        # Validate before publishing
        errors = self._validate_definition_structure(stored.workflow)
        if errors:
            raise ValidationError(
                f"Cannot publish: {errors[0]}",
                field="definition",
            )

        # The mistake checker: any error finding blocks publishing (warnings/info never do).
        errors = [f for f in (await self.check(workflow_id, for_publish=True))["findings"] if f["severity"] == "error"]
        errors += await self._subworkflow_publish_findings(stored, errors)
        if errors:
            raise WorkflowHasErrorsError(errors)

        # Approved tests are the safety net: any that fails blocks publishing (drafts never do).
        from .scorecard import failing_approved_tests
        from ..schemas.workflow import normalize_tests
        failed = [
            {"test_id": t["id"], "name": t["name"], "failures": [{
                "kind": "stale", "node": None,
                "message": t.get("stale_reason") or "This test is stale: the steps it checks moved. Review it, then approve it again."}]}
            for t in normalize_tests(stored.workflow.tests) if t.get("status") == "approved" and t.get("stale")
        ]
        failed += await failing_approved_tests(self._workflow_repo, stored)
        if failed:
            from ..core.exceptions import ApprovedTestsFailError
            raise ApprovedTestsFailError(failed)

        # Webhook paths are globally unique (unique index on active_triggers.webhook_path): refuse a path another
        # workflow already owns with a clear error instead of failing later with a 500 from the database.
        paths = {(n.parameters or {}).get("path") for n in stored.workflow.nodes if n.type == "Webhook"}
        paths.discard(None)
        paths.discard("")
        if paths:
            from sqlalchemy import select as _select
            from ..db.models import ActiveTriggerModel
            clash = (await self._workflow_repo._session.execute(_select(ActiveTriggerModel).where(
                ActiveTriggerModel.webhook_path.in_(paths), ActiveTriggerModel.workflow_id != workflow_id))).scalars().first()
            if clash is not None:
                raise ValidationError(
                    f"Cannot publish: webhook path '{clash.webhook_path}' is already used by workflow {clash.workflow_id}",
                    field="path",
                )

        # Stage 5: a published interface owns its slug in the project (409 INTERFACE_SLUG_TAKEN on a clash)
        interface = await self._check_interface_for_publish(stored)

        # Create immutable version from current draft
        version_number = await self._create_version(workflow_id, message=message)

        # Sync triggers from the definition
        from ..repositories.trigger_repository import TriggerRepository
        from ..db.models import WorkflowModel
        trigger_repo = TriggerRepository(self._workflow_repo._session)
        db_workflow = await self._workflow_repo._session.get(WorkflowModel, workflow_id)
        if db_workflow:
            await trigger_repo.sync_triggers(
                workflow_id=workflow_id,
                version_id=db_workflow.published_version_id,
                definition=db_workflow.draft_definition,
                team_id=db_workflow.team_id,
            )

        await self._sync_interface_slug(stored, interface, db_workflow.published_version_id if db_workflow else None)

        # Set active
        updated = await self._workflow_repo.set_active(workflow_id, True)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        try:
            impact = await self.publish_impact(workflow_id)
        except Exception:  # the publish itself succeeded; the impact list is advice
            logger.warning("could not compute the publish impact of %s", workflow_id, exc_info=True)
            impact = []
        return WorkflowPublishResult(
            id=updated.id,
            active=True,
            version_id=db_workflow.published_version_id,
            version_number=version_number,
            impact=impact,
        )

    async def _subworkflow_publish_findings(self, stored: Any, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish-only rules for sub-workflow calls (DESIGN-STAGE8-REUSE 4.3), as checker-shaped error findings.

        WF023: a ``draft`` pin would run someone's unsaved edits under a published trigger. WF022: ``latest`` or a version
        number that does not resolve (the child exists but was never published / has no such version). A child that does
        not exist at all is the checker's WF006. Findings the checker already reported for the same node are not repeated."""
        from sqlalchemy import text

        from .workflow_refs import refs_of

        model = await self._workflow_repo.get_model(stored.id)
        refs = refs_of(model.draft_definition if model else None)
        if not refs:
            return []
        have = {(f.get("code"), f.get("node")) for f in existing}
        session = self._workflow_repo._session
        out: list[dict[str, Any]] = []

        def add(code: str, node: str, message: str, path: str) -> None:
            if (code, node) not in have:
                out.append({"code": code, "severity": "error", "message": message, "node": node,
                            "param": "version", "path": path})

        for node, child_id, spec in refs:
            if spec == "draft":
                add("WF023", node, f"'{node}' calls a sub-workflow's draft. Pin 'latest' or a version before publishing, "
                    "so the published workflow never runs unsaved edits.", child_id)
                continue
            row = (await session.execute(
                text("SELECT name, project_id, published_version_id FROM workflows WHERE id = :w"), {"w": child_id})).first()
            if row is None:
                continue  # WF006 territory
            name, child_project, published = row
            if stored.project_id and child_project != stored.project_id:
                continue  # WF027 territory (another project looks absent)
            if spec == "latest":
                if published is None:
                    add("WF022", node, f"Sub-workflow '{name}' has never been published, so '{node}' has nothing to call.", child_id)
            else:
                found = (await session.execute(
                    text("SELECT 1 FROM workflow_versions WHERE workflow_id = :w AND version_number = :n"),
                    {"w": child_id, "n": int(spec)})).first()
                if found is None:
                    add("WF022", node, f"Sub-workflow '{name}' has no version {spec}.", child_id)
        return out

    async def publish_impact(self, workflow_id: str) -> list[dict[str, Any]]:
        """After publishing a child: the parents that follow ``latest`` and may now break (section 4.3). Never blocks.

        ``[{parent_workflow_id, parent_name, node, version_spec, in_release, breaking: [findings]}]``. ``breaking`` holds
        the checker's WF024 findings for that node, from re-checking the parent's published definition against the new
        child (empty until the checker types sub-workflow calls)."""
        from sqlalchemy import text

        from .workflow_refs import WorkflowRefRepository, _releases_calling

        session = self._workflow_repo._session
        callers = [r for r in await WorkflowRefRepository(session).callers_of(workflow_id)
                   if r["parent_is_published"] and r["version_spec"] == "latest" and r["parent_workflow_id"] != workflow_id]
        in_release = {(r["parent_workflow_id"], r["node"]) for r in await _releases_calling(session, workflow_id)}
        out: list[dict[str, Any]] = []
        cache: dict[str, list[dict[str, Any]]] = {}
        for r in callers:
            pid = r["parent_workflow_id"]
            if pid not in cache:
                cache[pid] = await self._recheck_published(pid, r["parent_version_id"])
            out.append({
                "parent_workflow_id": pid, "parent_name": r["parent_name"], "node": r["node"],
                "version_spec": r["version_spec"], "in_release": (pid, r["node"]) in in_release,
                "breaking": [f for f in cache[pid] if f.get("node") == r["node"]],
            })
        self.last_publish_impact = out
        return out

    async def _recheck_published(self, parent_id: str, version_id: int) -> list[dict[str, Any]]:
        """WF024 findings of a parent's published definition against the children as they are now."""
        try:
            from ..db.models import WorkflowVersionModel
            from .check_lookups import build_lookups
            from .workflow_checker import check_workflow

            session = self._workflow_repo._session
            version = await session.get(WorkflowVersionModel, version_id)
            model = await self._workflow_repo.get_model(parent_id)
            if version is None or model is None:
                return []
            lookups = await build_lookups(session, model.project_id, None, team_id=model.team_id or "default",
                                          workflow_id=parent_id, definition=version.definition)
            findings = check_workflow(version.definition, lookups=lookups, workflow_id=parent_id)["findings"]
            return [f for f in findings if f.get("code") == "WF024"]
        except Exception:
            logger.warning("could not re-check %s against its children", parent_id, exc_info=True)
            return []

    async def _check_interface_for_publish(self, stored: Any) -> dict[str, Any] | None:
        """The interface to publish (validated), or None. Refuses an invalid interface (400) and a slug another workflow owns (409)."""
        iface = stored.workflow.interface
        if not iface:
            return None
        from ..core.exceptions import ConflictError
        from ..repositories.interface_repository import InterfaceRepository
        from ..schemas.interface import parse_interface

        clean, errors = parse_interface(iface)
        if clean is None:
            raise ValidationError(f"Cannot publish: the interface is invalid ({errors[0]['field'] or 'interface'}: {errors[0]['message']})",
                                  field="interface")
        owner = await InterfaceRepository(self._workflow_repo._session).get_slug(stored.project_id, clean["slug"])
        if owner is not None and owner["workflow_id"] != stored.id:
            raise ConflictError("INTERFACE_SLUG_TAKEN", f"The address '{clean['slug']}' is already used by another workflow in this project")
        return clean

    async def _sync_interface_slug(self, stored: Any, interface: dict[str, Any] | None, version_id: int | None) -> None:
        """Point the live slug at the version just published (or free it when this version has no interface)."""
        from ..core.exceptions import ConflictError
        from ..repositories.interface_repository import InterfaceRepository

        repo = InterfaceRepository(self._workflow_repo._session)
        if interface is None or version_id is None:
            await repo.release_slug(stored.id)
        elif not await repo.claim_slug(stored.project_id, interface["slug"], stored.id, interface["kind"], version_id):
            await repo.rollback()
            raise ConflictError("INTERFACE_SLUG_TAKEN", f"The address '{interface['slug']}' is already used by another workflow in this project")
        await repo.commit()

    async def score(self, workflow_id: str, definition: dict[str, Any] | None = None, mutations: bool = True, repeat: int = 0) -> dict[str, Any]:
        """The scorecard (services/scorecard.py). Never saves."""
        from .scorecard import score

        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        return await score(self._workflow_repo, stored, definition, mutations=mutations, repeat=repeat)

    async def apply_ops(self, workflow_id: str, ops: list[dict[str, Any]], dry_run: bool = False) -> dict[str, Any]:
        """Apply toolkit ops to the draft; returns ``{definition, check}``. Saves only when ``dry_run`` is false."""
        from ..repositories.workflow_repository import definition_to_workflow, workflow_to_definition
        from .workflow_toolkit import apply_ops

        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        definition = apply_ops(workflow_to_definition(stored.workflow), ops)
        if not dry_run:
            updated = await self._workflow_repo.update(
                workflow_id, definition_to_workflow(definition, stored.name, stored.id, stored.workflow.description))
            if not updated:
                raise WorkflowNotFoundError(workflow_id)
        return {"definition": definition, "check": await self.check(workflow_id, definition)}

    async def check(self, workflow_id: str, definition: dict[str, Any] | None = None, environment: str | None = None,
                    for_publish: bool = False) -> dict[str, Any]:
        """Run the mistake checker on ``definition`` (default: the stored draft). Never saves.

        The lookups carry this workflow's id and the definition being checked, so the sub-workflow rules (WF020-WF029) see
        the call closure of the *unsaved* definition. ``for_publish`` raises WF022 / WF023 to errors, as a publish does."""
        from .check_lookups import build_lookups
        from .workflow_checker import check_workflow

        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        if definition is None:
            definition = self._workflow_to_dict(stored.workflow)
        lookups = await build_lookups(self._workflow_repo._session, stored.project_id, environment,
                                      team_id=getattr(stored, "team_id", None) or "default",
                                      workflow_id=workflow_id, definition=definition)
        return check_workflow(definition, lookups=lookups, workflow_id=workflow_id, for_publish=for_publish)

    async def unpublish(self, workflow_id: str) -> WorkflowPublishResponse:
        """Unpublish a workflow. Disables triggers, sets active=false.

        Published versions are preserved for history — this only stops execution.
        """
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        # Deactivate triggers
        from ..repositories.trigger_repository import TriggerRepository
        trigger_repo = TriggerRepository(self._workflow_repo._session)
        await trigger_repo.deactivate_triggers(workflow_id)

        from ..repositories.interface_repository import InterfaceRepository
        irepo = InterfaceRepository(self._workflow_repo._session)
        await irepo.release_slug(workflow_id)       # the live URL stops answering (404) until the next publish
        await irepo.commit()

        updated = await self._workflow_repo.set_active(workflow_id, False)
        if not updated:
            raise WorkflowNotFoundError(workflow_id)

        return WorkflowPublishResponse(id=updated.id, active=False, version_id=None)

    async def _create_version(self, workflow_id: str, message: str | None = None) -> int:
        """Create an immutable version snapshot from the current draft."""
        from ..repositories.version_repository import VersionRepository
        from ..db.models import WorkflowModel

        # Read draft_definition directly from the model
        db_workflow = await self._workflow_repo._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            raise WorkflowNotFoundError(workflow_id)

        version_repo = VersionRepository(self._workflow_repo._session)
        version = await version_repo.create_version(
            workflow_id=workflow_id,
            definition=db_workflow.draft_definition,
            message=message,
        )

        # Update published_version_id
        db_workflow.published_version_id = version.id
        await self._workflow_repo._session.commit()

        # the version's own rows in the reference index (versions are immutable, so these never change again)
        from .workflow_refs import sync_refs
        await sync_refs(self._workflow_repo._session, workflow_id, version.id, version.definition,
                        project_id=db_workflow.project_id)
        await self._workflow_repo._session.commit()
        return version.version_number

    async def _handler_index(self, workflow_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """G5: which API routes each workflow serves."""
        try:
            from sqlalchemy import select
            from ..db.models import ApiRouteModel
        except ImportError:
            return {}
        if not workflow_ids:
            return {}
        rows = (await self._workflow_repo._session.execute(
            select(ApiRouteModel).where(ApiRouteModel.handler_workflow_id.in_(workflow_ids)))).scalars().all()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r.handler_workflow_id, []).append({"route_id": r.id, "method": r.method, "path": r.path})
        return out

    async def _check_movable(self, workflow_id: str) -> None:
        """A workflow used as a route handler cannot leave its project."""
        try:
            from sqlalchemy import select, func
            from ..db.models import ApiRouteModel
        except ImportError:
            return
        n = (await self._workflow_repo._session.execute(
            select(func.count()).select_from(ApiRouteModel).where(ApiRouteModel.handler_workflow_id == workflow_id)
        )).scalar_one()
        if n:
            from ..core.exceptions import ConflictError
            raise ConflictError("workflow_in_use", "Workflow is the handler of an API route and cannot be moved")

    async def run_workflow(
        self,
        workflow_id: str,
        input_data: dict[str, Any] | None = None,
        environment: str = "default",
    ) -> ExecutionResponse:
        """Run a saved workflow (draft definition) with optional input data.

        Delegates to ExecutionService.run_saved_workflow — the single run path."""
        from .execution_service import ExecutionService

        svc = ExecutionService(self._execution_repo, self._workflow_repo)
        # Review C2: a studio run is "manual" even when it carries input_data (was stored as "webhook").
        _, context = await svc.run_saved_workflow(workflow_id, input_data, environment=environment,
                                                  strict_environment=True, mode="manual",
                                                  metadata={"trigger": {"type": "manual"}})
        return self._build_execution_response(context)

    async def test_node(self, workflow_id: str, name: str, body: Any) -> ExecutionResponse:
        """Run only one node (see services/partial_run.py)."""
        from .partial_run import PartialRunService
        context = await PartialRunService(self._execution_repo, self._workflow_repo).test_node(
            workflow_id, name, body.source, body.input_items, environment=body.environment)
        return self._build_execution_response(context)

    async def run_to(self, workflow_id: str, name: str, input_data: dict[str, Any] | None = None,
                     environment: str = "default") -> ExecutionResponse:
        """Run from the trigger and stop after `name`."""
        from .partial_run import PartialRunService
        context = await PartialRunService(self._execution_repo, self._workflow_repo).run_to(
            workflow_id, name, input_data, environment=environment)
        return self._build_execution_response(context)

    async def run_from(self, workflow_id: str, name: str, execution_id: str | None = None,
                       environment: str = "default") -> ExecutionResponse:
        """Re-run `name` and everything after it, reusing a past execution's outputs for the ancestors."""
        from .partial_run import PartialRunService
        context = await PartialRunService(self._execution_repo, self._workflow_repo).run_from(
            workflow_id, name, execution_id, environment=environment)
        return self._build_execution_response(context)

    async def run_tests(self, workflow_id: str, ids: list[str] | None = None, definition: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the workflow's tests (see services/test_runner.py). Never saves the workflow."""
        from .test_runner import run_tests

        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        return await run_tests(self._workflow_repo, stored, definition, ids=ids, persist=True)

    async def learn_contract(self, workflow_id: str, name: str, execution_id: str | None = None) -> dict[str, Any]:
        """Infer a node's output shape from a saved execution (nothing is saved)."""
        from .contract_service import ContractService
        return await ContractService(self._execution_repo, self._workflow_repo).learn(workflow_id, name, execution_id)

    async def approve_contract(self, workflow_id: str, name: str, body: Any) -> WorkflowDetailResponse:
        """Write an approved contract onto the node in the draft."""
        from .contract_service import ContractService
        await ContractService(self._execution_repo, self._workflow_repo).approve(
            workflow_id, name, body.outputs, body.from_execution, body.approved_by)
        return await self.get_workflow(workflow_id)

    async def delete_contract(self, workflow_id: str, name: str) -> WorkflowDetailResponse:
        """Remove a node's approved contract from the draft."""
        from .contract_service import ContractService
        await ContractService(self._execution_repo, self._workflow_repo).delete(workflow_id, name)
        return await self.get_workflow(workflow_id)

    async def restore_version(self, workflow_id: str, version_id: int) -> WorkflowDetailResponse:
        """Copy a version's definition into the draft and return the workflow."""
        import copy
        from ..repositories.version_repository import VersionRepository
        from ..db.models import WorkflowModel

        db_workflow = await self._workflow_repo._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            raise WorkflowNotFoundError(workflow_id)
        version = await VersionRepository(self._workflow_repo._session).get(version_id)
        if not version or version.workflow_id != workflow_id:
            raise WorkflowNotFoundError(f"version {version_id}")
        db_workflow.draft_definition = copy.deepcopy(version.definition)
        db_workflow.updated_at = datetime.now()
        await self._workflow_repo._session.commit()
        await self._sync_draft_refs(workflow_id)
        return await self.get_workflow(workflow_id)

    async def run_adhoc_workflow(self, request: WorkflowCreateRequest) -> ExecutionResponse:
        """Run an ad-hoc workflow without saving."""
        self._validate_workflow(request)
        internal_workflow = self._request_to_workflow(request)

        from .subworkflow_service import DbSubworkflowInvoker
        from .variable_loader import load_run_variables, load_run_secrets
        from .vars_substitution import substitute_vars
        runner = WorkflowRunner()
        start_node = runner.find_start_node(internal_workflow)
        from .project_service import effective_environment, resolve_project
        project_id = await self._resolve_project_id(getattr(request, "project_id", None))
        project = await resolve_project(self._workflow_repo._session, project_id) if project_id else None
        env = effective_environment(project, request.environment, strict=True)
        variables = await load_run_variables(environment=env, project_id=project_id)
        secrets = await load_run_secrets(environment=env, project_id=project_id)
        # Resolve {{ $vars.X }} in the caller payload before the workflow runs.
        if request.input_data is not None:
            request.input_data = substitute_vars(request.input_data, variables)

        if not start_node:
            raise WorkflowExecutionError("No start node found in workflow")

        # Build initial data - wrap in webhook-style format for consistency
        if request.input_data:
            initial_data = [
                NodeData(
                    json={
                        "body": request.input_data,
                        "headers": {},
                        "query": {},
                        "method": "POST",
                        "triggeredAt": datetime.now().isoformat(),
                    }
                )
            ]
            mode = "webhook"
        else:
            initial_data = [
                NodeData(
                    json={
                        "triggeredAt": datetime.now().isoformat(),
                        "mode": "manual",
                    }
                )
            ]
            mode = "manual"

        # Pre-create execution row so node_outputs FK is satisfied during run
        from ..utils.ids import execution_id as gen_exec_id
        exec_id = gen_exec_id()
        wf_id = internal_workflow.id or "adhoc"
        await self._execution_repo.start(
            exec_id, wf_id, internal_workflow.name, mode,
            project_id=project_id or "proj_default", environment=env,
        )

        from .api_catalog import load_api_specs
        context = await runner.run(
            internal_workflow,
            start_node.name,
            initial_data,
            mode,
            workflow_repository=self._workflow_repo,
            execution_id=exec_id,
            variables=variables,
            secret_values=secrets,
            project_id=project_id,
            environment=env,
            extras={"api_specs": await load_api_specs(self._workflow_repo._session, internal_workflow.nodes)},
            subworkflow_invoker=DbSubworkflowInvoker(),
        )
        await self._execution_repo.complete(context, wf_id, internal_workflow.name)

        return self._build_execution_response(context)

    def _validate_definition_structure(self, workflow: Workflow) -> list[str]:
        """Validate workflow graph structure. Returns list of errors (empty = valid)."""
        errors = []
        node_names = {n.name for n in workflow.nodes}

        # All connection endpoints reference existing nodes
        for conn in workflow.connections:
            if conn.source_node not in node_names:
                errors.append(f"Connection references missing source node: {conn.source_node}")
            if conn.target_node not in node_names:
                errors.append(f"Connection references missing target node: {conn.target_node}")

        # At least one trigger node
        from ..engine.graph import TRIGGER_NODE_TYPES as trigger_types
        triggers = [n for n in workflow.nodes if n.type in trigger_types]
        if len(triggers) == 0:
            errors.append("Workflow has no trigger node")

        # Orphan nodes (nodes with no connections, excluding triggers and sticky notes)
        connected_nodes: set[str] = set()
        for conn in workflow.connections:
            connected_nodes.add(conn.source_node)
            connected_nodes.add(conn.target_node)
        skip_types = trigger_types | {"StickyNote"}
        for node in workflow.nodes:
            if node.name not in connected_nodes and node.type not in skip_types:
                errors.append(f"Orphan node: {node.name}")

        return errors

    def _validate_workflow(self, request: WorkflowCreateRequest) -> None:
        """Validate workflow request."""
        if not request.nodes:
            raise ValidationError("Workflow must have at least one node", field="nodes")

        node_names = [n.name for n in request.nodes]
        if len(node_names) != len(set(node_names)):
            raise ValidationError("Node names must be unique", field="nodes")

        if msg := disabled_trigger_message(request.nodes):
            raise ValidationError(msg, field="nodes")

        # Validate node types exist in registry
        if self._node_registry is not None:
            for node in request.nodes:
                if not self._node_registry.has(node.type):
                    raise ValidationError(
                        f'Unknown node type: "{node.type}"',
                        field="nodes",
                    )

        # Validate connections reference valid nodes
        node_name_set = set(node_names)
        for conn in request.connections:
            if conn.source_node not in node_name_set:
                raise ValidationError(
                    f"Connection references unknown source node: {conn.source_node}",
                    field="connections",
                )
            if conn.target_node not in node_name_set:
                raise ValidationError(
                    f"Connection references unknown target node: {conn.target_node}",
                    field="connections",
                )

    def _validate_update(self, request: WorkflowUpdateRequest, existing: Any) -> None:
        """Validate workflow update request."""
        nodes = request.nodes or []
        connections = request.connections or []

        # Validate node types exist in registry
        if self._node_registry is not None:
            for node in nodes:
                if not self._node_registry.has(node.type):
                    raise ValidationError(
                        f'Unknown node type: "{node.type}"',
                        field="nodes",
                    )

        if msg := disabled_trigger_message(nodes):
            raise ValidationError(msg, field="nodes")

        # If nodes are being updated, validate names are unique
        if nodes:
            node_names = [n.name for n in nodes]
            if len(node_names) != len(set(node_names)):
                raise ValidationError("Node names must be unique", field="nodes")

            # Validate connections reference valid nodes
            node_name_set = set(node_names)
            for conn in connections:
                if conn.source_node not in node_name_set:
                    raise ValidationError(
                        f"Connection references unknown source node: {conn.source_node}",
                        field="connections",
                    )
                if conn.target_node not in node_name_set:
                    raise ValidationError(
                        f"Connection references unknown target node: {conn.target_node}",
                        field="connections",
                    )

    def _request_to_workflow(self, request: WorkflowCreateRequest) -> Workflow:
        """Convert request to internal Workflow type."""
        return request.to_workflow()

    def _workflow_to_dict(self, workflow: Workflow) -> dict[str, Any]:
        """Convert internal Workflow to dict for API response."""
        enriched_nodes = []
        for n in workflow.nodes:
            # Compute dynamic I/O based on node type and parameters
            io_data = self._node_service.compute_node_io(n.type, n.parameters or {})

            # Get node metadata from registry
            node_info = self._node_service._node_registry.get_node_type_info(n.type)

            node_dict: dict[str, Any] = {
                "name": n.name,
                "type": n.type,
                "label": n.label,
                "parameters": n.parameters,
                "position": n.position,
                "retry_on_fail": n.retry_on_fail,
                "retry_delay": n.retry_delay,
                "disabled": n.disabled,
                "on_error": n.on_error,
                **({"contract": n.contract} if n.contract else {}),
                **({"pinnedData": [{"json": d.json} for d in n.pinned_data]} if n.pinned_data else {}),
                # Enriched I/O data for frontend
                "inputs": io_data["inputs"],
                "inputCount": io_data["inputCount"],
                "outputs": io_data["outputs"],
                "outputCount": io_data["outputCount"],
                "inputStrategy": io_data["inputStrategy"],
                "outputStrategy": io_data["outputStrategy"],
                # Node group for styling
                "group": io_data["group"],
                # Icon from node registry
                **({"icon": node_info.icon} if node_info and node_info.icon else {}),
            }

            enriched_nodes.append(node_dict)

        return {
            "name": workflow.name,
            "id": workflow.id,
            "description": workflow.description,
            "nodes": enriched_nodes,
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                    **({"waypoints": c.waypoints} if c.waypoints else {}),
                }
                for c in workflow.connections
            ],
            "settings": workflow.settings,
            "tests": list(workflow.tests or []),
            "test_defaults": dict(workflow.test_defaults or {}),
            **({"interface": dict(workflow.interface)} if workflow.interface else {}),
        }

    def _build_execution_response(self, context: Any) -> ExecutionResponse:
        """Build execution response from context."""
        node_data = {
            name: [{"json": d.json} for d in data]
            for name, data in context.node_states.items()
        }

        return ExecutionResponse(
            status=run_status(context),  # "waiting" when an Approval paused the run
            execution_id=context.execution_id,
            data=node_data,
            errors=[
                ExecutionErrorSchema(
                    node_name=e.node_name,
                    error=e.error,
                    timestamp=e.timestamp.isoformat(),
                    error_code=getattr(e, "error_code", None),
                    details=getattr(e, "details", None),
                )
                for e in context.errors
            ],
        )
