"""The sandbox of AI proposals (Stage 6, DESIGN-STAGE6-AI-BUILDER sections 2, 4.8, 6.4 and 6.11).

Three jobs, all without the AI:

1. **Test an unsaved definition.** ``ProposalStored`` is the duck-typed ``stored`` that ``test_runner.run_tests`` needs;
   ``run_proposal_tests`` runs a proposal's definition and tests with ``persist=False`` (no execution rows, no cache).
2. **Fingerprint what the AI may not touch.** ``canonical_hash`` / ``draft_hash`` / ``fingerprint`` /
   ``tampered_tests`` are the pure pieces of the test-immutability gate and of the moved-draft check.
3. **Keep / Undo / Discard** against the normal save path. Each is race-safe and idempotent: the proposal row is
   *claimed* with ``UPDATE ... WHERE status = ...`` in the same transaction as the workflow write, so exactly one of N
   concurrent callers does the work and the others get the same answer back (``already=True``) or ``bad_state``.

Errors are ``ProposalError`` subclasses carrying the design's HTTP status and code (section 4); the routes map them.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.ai_proposal_repository import AIProposalRepository
from .workflow_refs import refresh_draft_index
from ..repositories.workflow_repository import WorkflowRepository, definition_to_workflow, workflow_to_definition

_APPROVAL_STAMPS = ("approved_by", "approved_at")
DISCARDABLE = ("spec_draft", "spec_approved", "ready", "failed")


# ── errors ───────────────────────────────────────────────────────────────────────────────


class ProposalError(Exception):
    """A refused proposal action. ``http_status`` / ``code`` / ``extra`` build the house envelope ``{"detail": {...}}``."""

    http_status = 400
    code = "proposal_error"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class ProposalNotFound(ProposalError):
    http_status, code = 404, "not_found"


class BadState(ProposalError):
    http_status, code = 409, "bad_state"

    def __init__(self, status: str, message: str | None = None) -> None:
        super().__init__(message or f"This proposal is {status}; that action is not allowed now.", status=status)
        self.status = status


class DraftMoved(ProposalError):
    http_status, code = 409, "draft_moved"


class LockedTests(ProposalError):
    http_status, code = 409, "locked_tests"

    def __init__(self, test_ids: list[str], message: str | None = None) -> None:
        super().__init__(message or "Approved tests can't be changed.", test_ids=list(test_ids))
        self.test_ids = list(test_ids)


class TestsFailing(ProposalError):
    __test__ = False
    http_status, code = 409, "tests_failing"


# ── hashing and the test-immutability gate ───────────────────────────────────────────────


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no spaces, UTF-8 characters kept; ``default=str`` so a stray datetime can't break it."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def draft_hash(stored: Any) -> str:
    """Hash of a stored workflow's draft, always taken through ``workflow_to_definition`` so both sides of every
    comparison (proposal start, Keep, Undo) see the same normalised form."""
    return canonical_hash(workflow_to_definition(stored.workflow))


def draft_of(stored: Any) -> tuple[dict[str, Any], str]:
    """``(definition, hash)`` of a stored workflow: what a proposal records as ``base_definition`` / ``base_draft_hash``."""
    d = workflow_to_definition(stored.workflow)
    return d, canonical_hash(d)


def approved_tests(tests: Any) -> list[dict[str, Any]]:
    """The approved tests of a list, normalised, stripped of who/when approved them, sorted by id."""
    from ..schemas.workflow import normalize_tests

    out = []
    for t in normalize_tests(list(tests or [])):
        if t.get("status") == "approved":
            out.append({k: v for k, v in t.items() if k not in _APPROVAL_STAMPS})
    return sorted(out, key=lambda t: t["id"])


def fingerprint(tests: Any) -> str:
    """sha256 of the canonical approved tests (``spec_fingerprint``). Draft tests, approver and time do not count."""
    return canonical_hash(approved_tests(tests))


def tampered_tests(expected: Any, definition: dict[str, Any] | None) -> list[str]:
    """Ids of ``expected`` approved tests that are missing from ``definition`` or differ there (layer 1 and 2 of the gate)."""
    have = {t["id"]: t for t in approved_tests((definition or {}).get("tests"))}
    return [t["id"] for t in approved_tests(expected) if have.get(t["id"]) != t]


# ── the unsaved-workflow test path (spike W1-S1) ─────────────────────────────────────────


@dataclass
class ProposalStored:
    """The duck-typed ``stored`` of ``test_runner.run_tests`` for a workflow that does not exist (yet).

    ``id`` is the proposal id for a new workflow (nothing is written under it: ``persist=False``) and the real workflow
    id for a modify proposal, so lookups that key on the workflow behave as in a normal run.
    """

    id: str
    project_id: str
    team_id: str | None
    name: str
    workflow: Any  # SimpleNamespace(description, definition)
    active: bool = False


def proposal_stored(proposal: dict[str, Any], definition: dict[str, Any]) -> ProposalStored:
    name = (definition.get("name") or (proposal.get("spec") or {}).get("title") or "AI proposal")
    return ProposalStored(
        id=proposal.get("workflow_id") or proposal["id"], project_id=proposal["project_id"], team_id=proposal.get("team_id"),
        name=str(name)[:255], workflow=SimpleNamespace(description=definition.get("description"), definition=definition),
    )


async def run_proposal_tests(
    session: AsyncSession, proposal: dict[str, Any], definition: dict[str, Any], *, ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run the tests of an unsaved ``definition``; returns the ``TestRunReport``. Writes no execution rows."""
    from .test_runner import run_tests

    return await run_tests(WorkflowRepository(session), proposal_stored(proposal, definition), definition, ids=ids, persist=False)


# ── Keep / Undo / Discard ────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _lock_workflow(session: AsyncSession, workflow_id: str) -> None:
    """Row-lock the workflow until this transaction ends, and drop anything the session cached about it."""
    await session.execute(text("SELECT id FROM workflows WHERE id = :id FOR UPDATE"), {"id": workflow_id})
    session.expire_all()


def _stamp_new_approvals(tests: list[dict[str, Any]], known_ids: set[str], by: str, at: str) -> list[dict[str, Any]]:
    out = []
    for t in tests:
        t = dict(t)
        if t["id"] not in known_ids and t.get("status") == "approved":
            t.setdefault("approved_by", by)
            t.setdefault("approved_at", at)
        out.append(t)
    return out


def _as_draft(t: dict[str, Any]) -> dict[str, Any]:
    t = {k: v for k, v in t.items() if k not in _APPROVAL_STAMPS}
    t["status"], t["locked"] = "draft", False
    return t


def _check_structure(workflow: Any) -> None:
    from ..engine.node_registry import node_registry
    from .workflow_service import WorkflowService

    WorkflowService(None, None, None, node_registry)._validate_workflow(  # type: ignore[arg-type]
        SimpleNamespace(nodes=workflow.nodes, connections=workflow.connections))


def _result(row: dict[str, Any], already: bool) -> dict[str, Any]:
    return {"proposal": row, "workflow_id": row.get("workflow_id"), "draft_hash": row.get("kept_draft_hash"), "already": already}


async def _load(repo: AIProposalRepository, proposal_id: str, project_id: str | None) -> dict[str, Any]:
    row = await repo.get(proposal_id, project_id)
    if row is None:
        raise ProposalNotFound(f"Proposal not found: {proposal_id}")
    return row


async def keep(
    session: AsyncSession, proposal_id: str, actor: str, *, accept_failing: bool = False, name: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Make a ``ready`` proposal the workflow's draft. Returns ``{proposal, workflow_id, draft_hash, already}``.

    Refuses (nothing is written): ``bad_state`` (not ready), ``tests_failing`` (red tests without ``accept_failing``),
    ``locked_tests`` (the approved tests changed, by fingerprint or by the normal lock rules), ``draft_moved`` (the draft
    is not the one the proposal started from). A second Keep of a kept proposal returns the first one's answer.
    """
    from ..engine.test_mode import locked_test_ids
    from ..schemas.workflow import normalize_tests

    repo = AIProposalRepository(session)
    row = await _load(repo, proposal_id, project_id)
    if row["status"] == "kept":
        return _result(row, True)
    if row["status"] != "ready":
        raise BadState(row["status"])
    proposed = row.get("proposed_definition")
    if not isinstance(proposed, dict):
        raise BadState(row["status"], "This proposal has no workflow to keep.")
    if (row.get("report") or {}).get("passed") is False and not accept_failing:
        raise TestsFailing("The tests are failing. Keep it anyway only with accept_failing.")

    # defence in depth, layer 2: the approved spec tests must still be exactly what was approved, and still be in the result
    spec_tests = (row.get("spec") or {}).get("tests") or []
    if row.get("spec_fingerprint"):
        if fingerprint(spec_tests) != row["spec_fingerprint"]:
            raise LockedTests([t["id"] for t in approved_tests(spec_tests)], "The approved tests no longer match their fingerprint.")
        if bad := tampered_tests(spec_tests, proposed):
            raise LockedTests(bad)

    now = _now()
    stamp_by = row.get("spec_approved_by") or actor
    stamp_at = (row.get("spec_approved_at") or now).isoformat()
    wrepo = WorkflowRepository(session)
    try:
        wf_id = row.get("workflow_id")
        is_new = row["kind"] == "build" or (wf_id is None and row.get("base_definition") is None)
        if is_new:
            wf_id = wrepo._generate_id()
            final = copy.deepcopy(proposed)
            final["tests"] = _stamp_new_approvals(normalize_tests(final.get("tests")), set(), stamp_by, stamp_at)
            title = (name or final.get("name") or (row.get("spec") or {}).get("title") or "AI workflow")
            wf = definition_to_workflow(final, str(title).strip()[:255] or "AI workflow", wf_id, final.get("description"))
            _check_structure(wf)
            expected_hash = canonical_hash(workflow_to_definition(wf))
        else:
            if wf_id is None:
                raise DraftMoved("The workflow this proposal was made for no longer exists.")
            expected_hash = None

        if is_new:  # the claim names a workflow this same transaction is about to insert
            await session.execute(text("SET CONSTRAINTS ai_proposals_workflow_fk DEFERRED"))
        claim = await repo.transition(proposal_id, ["ready"], "kept", {
            "kept_by": actor, "kept_at": now, "accepted_failing": bool(accept_failing), "kept_draft_hash": expected_hash,
            **({"workflow_id": wf_id} if is_new else {}),
        })
        if claim is None:  # lost the race: the winner's result is ours too
            await session.rollback()
            again = await _load(repo, proposal_id, project_id)
            if again["status"] == "kept":
                return _result(again, True)
            raise BadState(again["status"])
        await repo.add_event(proposal_id, "kept", {"actor": actor, "workflow_id": wf_id, "accepted_failing": bool(accept_failing)}, actor)

        if is_new:
            await wrepo.create(wf, project_id=row["project_id"], created_by=actor)  # commits claim + event + workflow together
            await refresh_draft_index(session, wf.id or wf_id)  # Stage 8: a kept draft is indexed like a normal save
            final_hash = expected_hash
        else:
            await _lock_workflow(session, wf_id)
            stored = await wrepo.get(wf_id)
            if stored is None:
                raise DraftMoved("The workflow this proposal was made for no longer exists.")
            if not row.get("base_draft_hash") or draft_hash(stored) != row["base_draft_hash"]:
                raise DraftMoved("The workflow changed since this proposal started. Save your edits and ask again.")
            stored_tests = normalize_tests(stored.workflow.tests)
            known = {t["id"] for t in stored_tests}
            incoming = normalize_tests(proposed.get("tests"))
            # a test new in this proposal is neutralised to a draft for the lock rules; everything else must obey them as in a normal save
            if bad := locked_test_ids(stored_tests, [t if t["id"] in known else _as_draft(t) for t in incoming]):
                raise LockedTests(bad)
            final = copy.deepcopy(proposed)
            final["tests"] = _stamp_new_approvals(incoming, known, stamp_by, stamp_at)
            wf = definition_to_workflow(final, stored.name, wf_id, stored.workflow.description)
            _check_structure(wf)
            await wrepo.update(wf_id, wf)  # commits claim + event + workflow together
            await refresh_draft_index(session, wf_id)
            if "interface" not in final and stored.workflow.interface:
                await wrepo.set_interface(wf_id, None)  # the proposal removed the interface; a plain save would keep the old one
            after = await wrepo.get(wf_id)
            final_hash = draft_hash(after) if after else None
        if final_hash != expected_hash:
            await repo.update_fields(proposal_id, {"kept_draft_hash": final_hash})
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return _result(await _load(repo, proposal_id, project_id), False)


async def undo(session: AsyncSession, proposal_id: str, actor: str, *, project_id: str | None = None) -> dict[str, Any]:
    """Put a ``kept`` proposal back. A modify restores ``base_definition``; a new workflow is deleted if never published.

    ``draft_moved`` when the draft is no longer the one Keep wrote. The tests this proposal added may be removed again
    (they are its own); every other locked test obeys the lock rules. A second Undo returns the first one's answer.
    """
    from ..engine.test_mode import locked_test_ids
    from ..schemas.workflow import normalize_tests

    repo = AIProposalRepository(session)
    row = await _load(repo, proposal_id, project_id)
    if row["status"] == "undone":
        return _result(row, True)
    if row["status"] != "kept":
        raise BadState(row["status"])
    wf_id = row.get("workflow_id")
    wrepo = WorkflowRepository(session)
    try:
        claim = await repo.transition(proposal_id, ["kept"], "undone")
        if claim is None:
            await session.rollback()
            again = await _load(repo, proposal_id, project_id)
            if again["status"] == "undone":
                return _result(again, True)
            raise BadState(again["status"])
        await repo.add_event(proposal_id, "undone", {"actor": actor, "workflow_id": wf_id}, actor)
        if wf_id is None:
            raise DraftMoved("The workflow no longer exists, so there is nothing to undo.")
        await _lock_workflow(session, wf_id)
        stored = await wrepo.get(wf_id)
        if stored is None:
            raise DraftMoved("The workflow no longer exists, so there is nothing to undo.")
        if not row.get("kept_draft_hash") or draft_hash(stored) != row["kept_draft_hash"]:
            raise DraftMoved("The workflow changed after this proposal was kept, so it can't be undone safely.")
        base = row.get("base_definition")
        if base is None:  # it created the workflow
            model = await wrepo.get_model(wf_id)
            if model is not None and (model.published_version_id is not None or model.active):
                raise BadState("kept", "This workflow has been published, so it can't be removed by Undo.")
            await wrepo.delete(wf_id)
            await session.commit()
        else:
            proposed_ids = {t["id"] for t in normalize_tests((row.get("proposed_definition") or {}).get("tests"))}
            base_tests = normalize_tests(base.get("tests"))
            base_ids = {t["id"] for t in base_tests}
            stored_tests = [t for t in normalize_tests(stored.workflow.tests) if t["id"] in base_ids or t["id"] not in proposed_ids]
            if bad := locked_test_ids(stored_tests, base_tests):
                raise LockedTests(bad)
            wf = definition_to_workflow(base, stored.name, wf_id, stored.workflow.description)
            await wrepo.update(wf_id, wf)
            await refresh_draft_index(session, wf_id)
            if "interface" not in base and stored.workflow.interface:
                await wrepo.set_interface(wf_id, None)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return _result(await _load(repo, proposal_id, project_id), False)


async def discard(session: AsyncSession, proposal_id: str, actor: str, *, project_id: str | None = None) -> dict[str, Any]:
    """Drop a proposal that is not kept. Writes nothing to any workflow. Idempotent."""
    repo = AIProposalRepository(session)
    row = await _load(repo, proposal_id, project_id)
    if row["status"] == "discarded":
        return _result(row, True)
    try:
        won = await repo.transition(proposal_id, DISCARDABLE, "discarded")
        if won is None:
            await session.rollback()
            again = await _load(repo, proposal_id, project_id)
            if again["status"] == "discarded":
                return _result(again, True)
            raise BadState(again["status"])
        await repo.add_event(proposal_id, "discarded", {"actor": actor}, actor)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return _result(won, False)
