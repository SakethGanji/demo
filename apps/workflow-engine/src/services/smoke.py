"""Live smoke tests (Stage 3B, CONTRACTS-STAGE3B §5).

A smoke run executes chosen tests with chosen nodes calling the real (or sandbox) system: ``live_nodes`` skip their
mock, every other outside step keeps it. Only structural assertions and contract checks can fail a smoke run; value
assertions are reported as ``info`` because live data changes. Live replies also go through drift detection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from croniter import croniter
from sqlmodel import select

from ..engine.effects import can_go_live
from ..db.models import SmokeConfigModel, SmokeRunModel
from ..repositories.workflow_repository import workflow_to_definition
from ..schemas.workflow import normalize_tests

logger = logging.getLogger(__name__)

MAX_RUN_LIST = 200
MIN_INTERVAL_MINUTES = 5  # a smoke run hits a real system: never more often than this


class SmokeError(Exception):
    """A smoke request that can't be served (``status`` is the HTTP code: 409 nothing to run, 422 bad config)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def config_json(workflow_id: str, row: SmokeConfigModel | None) -> dict[str, Any]:
    if row is None:
        return {"workflow_id": workflow_id, "enabled": False, "test_ids": [], "live_nodes": [], "schedule": None,
                "before_release": False, "next_run_at": None, "updated_at": None}
    return {"workflow_id": workflow_id, "enabled": row.enabled, "test_ids": list(row.test_ids or []),
            "live_nodes": list(row.live_nodes or []), "schedule": row.schedule, "before_release": row.before_release,
            "next_run_at": _iso(row.next_run_at), "updated_at": _iso(row.updated_at)}


def run_json(row: SmokeRunModel) -> dict[str, Any]:
    return {"id": row.id, "workflow_id": row.workflow_id, "trigger": row.trigger, "status": row.status,
            "started_at": _iso(row.started_at), "finished_at": _iso(row.finished_at), "results": list(row.results or [])}


def next_run_after(schedule: str, base: datetime | None = None) -> datetime:
    try:
        return croniter(schedule, base or datetime.now()).get_next(datetime)
    except (ValueError, KeyError) as e:
        raise SmokeError(422, f"'{schedule}' is not a valid cron schedule") from e


def check_schedule(schedule: str) -> datetime:
    """Validate a smoke cron string (422) and refuse anything that fires more often than every 5 minutes."""
    first = next_run_after(schedule)
    it = croniter(schedule, first)
    # sample several successive gaps: irregular crons ("0,1 * * * *") have a short gap that one sample can miss
    prev, gaps = first, []
    for _ in range(12):
        nxt = it.get_next(datetime)
        gaps.append((nxt - prev).total_seconds())
        prev = nxt
    if min(gaps) < MIN_INTERVAL_MINUTES * 60:
        raise SmokeError(422, f"A smoke schedule can't run more often than every {MIN_INTERVAL_MINUTES} minutes")
    return first


def _live_types_problem(definition: dict[str, Any], live_nodes: list[str]) -> str | None:
    types = {n.get("name"): n.get("type") for n in definition.get("nodes") or []}
    bad = [n for n in live_nodes if not can_go_live(types.get(n) or "")]
    if bad:
        return (f"Only HttpRequest nodes can go live in a smoke run; not allowed: {', '.join(bad)}. "
                "Database, email and other writing nodes keep their mocks.")
    return None


async def get_config(session: Any, workflow_id: str) -> dict[str, Any]:
    return config_json(workflow_id, await session.get(SmokeConfigModel, workflow_id))


async def put_config(session: Any, stored: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Validate and save a workflow's smoke config (unknown test ids / node names / cron -> 422)."""
    definition = workflow_to_definition(stored.workflow)
    test_ids = [str(t) for t in body.get("test_ids") or []]
    live_nodes = [str(n) for n in body.get("live_nodes") or []]
    known_tests = {t["id"] for t in normalize_tests(definition.get("tests"))}
    known_nodes = {n.get("name") for n in definition.get("nodes") or []}
    bad_tests = [t for t in test_ids if t not in known_tests]
    if bad_tests:
        raise SmokeError(422, f"Unknown test id(s): {', '.join(bad_tests)}")
    bad_nodes = [n for n in live_nodes if n not in known_nodes]
    if bad_nodes:
        raise SmokeError(422, f"Unknown node name(s): {', '.join(bad_nodes)}")
    if (problem := _live_types_problem(definition, live_nodes)):
        raise SmokeError(422, problem)
    schedule = (body.get("schedule") or "").strip() or None
    enabled = bool(body.get("enabled"))
    next_at = check_schedule(schedule) if schedule else None  # validates the cron string even when disabled
    row = await session.get(SmokeConfigModel, stored.id)
    if row is None:
        row = SmokeConfigModel(workflow_id=stored.id)
        session.add(row)
    keep_next = row.schedule == schedule and row.next_run_at is not None and row.enabled == enabled
    row.enabled, row.test_ids, row.live_nodes = enabled, list(dict.fromkeys(test_ids)), list(dict.fromkeys(live_nodes))
    row.schedule, row.before_release = schedule, bool(body.get("before_release"))
    row.next_run_at = (row.next_run_at if keep_next else next_at) if (schedule and enabled) else None
    row.updated_at = datetime.now()
    await session.commit()
    return config_json(stored.id, row)


async def list_runs(session: Any, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
    stmt = (select(SmokeRunModel).where(SmokeRunModel.workflow_id == workflow_id)
            .order_by(SmokeRunModel.started_at.desc(), SmokeRunModel.id.desc()).limit(max(1, min(limit, MAX_RUN_LIST))))
    return [run_json(r) for r in (await session.execute(stmt)).scalars().all()]


def _result_of(r: dict[str, Any]) -> dict[str, Any]:
    return {"test_id": r["test_id"], "name": r["name"], "status": r["status"],
            "failures": r["failures"], "live_calls": r.get("live_calls", []),
            "info": [w for w in r.get("warnings", []) if w.get("kind") == "info"]}


async def run_smoke(
    workflow_repo: Any, stored: Any, trigger: str = "manual", definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the configured smoke tests live and save a ``smoke_runs`` row. ``definition`` runs a version snapshot instead of the draft.

    Raises ``SmokeError(409)`` when there are no tests or no live nodes to run.
    """
    from . import test_runner

    session = workflow_repo._session
    cfg = await session.get(SmokeConfigModel, stored.id)
    if definition is None:
        definition = workflow_to_definition(stored.workflow)
    have = {t["id"] for t in normalize_tests(definition.get("tests"))}
    names = {n.get("name") for n in definition.get("nodes") or []}
    test_ids = [t for t in (cfg.test_ids if cfg else []) if t in have]
    live_nodes = [n for n in (cfg.live_nodes if cfg else []) if n in names]
    if not test_ids:
        raise SmokeError(409, "Pick at least one test for the smoke run")
    if not live_nodes:
        raise SmokeError(409, "Pick at least one live node for the smoke run")
    if (problem := _live_types_problem(definition, live_nodes)):  # also at run time: a config saved earlier, or a changed node
        raise SmokeError(422, problem)

    started = datetime.now()
    try:
        report = await test_runner.run_tests(workflow_repo, stored, definition, ids=test_ids, persist=False,
                                             live_nodes=live_nodes, smoke=True)
        results = [_result_of(r) for r in report["results"]]
    except Exception as e:  # the run itself broke: record it, never raise past the scheduler or the release check
        logger.warning("smoke run of %s failed", stored.id, exc_info=True)
        results = [{"test_id": None, "name": "smoke run", "status": "error", "live_calls": [], "info": [],
                    "failures": [{"kind": "setup", "node": None, "message": f"The smoke run could not run: {e}"}]}]
    statuses = {r["status"] for r in results}
    status = "error" if "error" in statuses else "failed" if "failed" in statuses else "passed"
    row = SmokeRunModel(id="smoke_" + uuid.uuid4().hex, workflow_id=stored.id, trigger=trigger, status=status,
                        started_at=started, finished_at=datetime.now(), results=results)
    session.add(row)
    await session.commit()
    return run_json(row)
