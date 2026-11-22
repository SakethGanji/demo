"""Tasks, queues, review, quality, rules, proposals, migration (labeling plan §6.2; LB-B: L07, L09, L06b guests).

Annotate and review routes are authorised by **task membership**, not by a team role (review C3): a guest annotator
(a principal with no role in the team) is added to one task and can lease, see masked context and answer there, and
nothing else (`GET /datasets/{id}/rows` stays 403 for them). These routes are `public()` for the team-role gate and
check membership in the handler in every authz mode. Annotators write labels only through `answers` against their own
leased assignment (review M12); direct events stay `label.task_manage` (LB-A's route)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, query, require
from ..authz.deps import public
from ..authz import scope as _scope
from . import lbb_common as C
from . import tasks as T
from . import workflows as W

router = APIRouter(prefix="/api")

# scope kinds for LB-B objects (team via the layer's root), registered without editing authz/scope.py
_scope.RESOLVERS.setdefault("label_task", _scope._via("label_task", "SELECT l.root_id FROM label_tasks t JOIN label_layers l ON l.id = t.layer_id WHERE t.id = ?", _scope._dataset))
_scope.RESOLVERS.setdefault("label_prompt", _scope._via("label_prompt", "SELECT l.root_id FROM label_prompts p JOIN label_layers l ON l.id = p.layer_id WHERE p.id = ?", _scope._dataset))
_scope.RESOLVERS.setdefault("label_llm_run", _scope._via("label_llm_run", "SELECT l.root_id FROM label_llm_runs r JOIN label_prompts p ON p.id = r.prompt_id JOIN label_layers l ON l.id = p.layer_id WHERE r.id = ?", _scope._dataset))
_scope.RESOLVERS.setdefault("label_proposal", _scope._via("label_proposal", "SELECT l.root_id FROM label_proposals p JOIN label_layers l ON l.id = p.layer_id WHERE p.id = ?", _scope._dataset))
_scope.RESOLVERS.setdefault("label_unmask_request", _scope._via("label_unmask_request", "SELECT l.root_id FROM approval_requests a JOIN label_layers l ON l.id = a.object_id WHERE a.id = ?", _scope._dataset))

MEMBERSHIP = public("label task membership is checked in the handler (guest annotators, labeling review C3)")


def _b(body: Any) -> dict[str, Any]:
    from fastapi import HTTPException

    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(400, {"code": "bad_body", "message": "The body is a JSON object."})
    return body


# -- tasks ------------------------------------------------------------------------------------------------------------------

@router.post("/label-layers/{layer_id}/tasks", status_code=201, dependencies=[require("label.task_manage", path("layer_id", "label_layer"))])
async def create_task(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    by = C.caller(request)
    return await run_in_threadpool(T.create_task, layer_id, _b(body), by)


@router.get("/label-layers/{layer_id}/tasks", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def list_tasks(layer_id: str) -> dict[str, Any]:
    def go():
        with C.db().connect() as con:
            C.layer(con, layer_id)
            return [T.public_task(T.get_task(con, r["id"])) for r in C.rows(con, "SELECT id FROM label_tasks WHERE layer_id = ? ORDER BY created_at", (layer_id,))]
    return {"tasks": await run_in_threadpool(go)}


@router.get("/label-tasks/{task_id}", dependencies=[require("label.read", path("task_id", "label_task"))])
async def get_task(task_id: str) -> dict[str, Any]:
    def go():
        with C.db().connect() as con:
            t = T.public_task(T.get_task(con, task_id))
            t["members"] = T.members(con, task_id)
            t["counts"] = {r["status"]: r["n"] for r in C.rows(con, "SELECT status, COUNT(*) AS n FROM label_assignments WHERE task_id = ? GROUP BY status", (task_id,))}
            c = t["counts"]
            t["progress"] = {"total": sum(c.values()), "open": c.get("open", 0), "leased": c.get("leased", 0), "done": c.get("done", 0),
                             "skipped": c.get("skipped", 0), "disputed": len(T.disputed_rows(con, T.get_task(con, task_id))),
                             "gold": C.one(con, "SELECT COUNT(*) AS n FROM label_assignments WHERE task_id = ? AND is_gold = 1", (task_id,))["n"]}
            sch = C.schema(con, t["layer_id"], t["schema_version"])
            t["classes"] = (C.classes_of(sch["spec_obj"], t["columns"][0]) if sch and t["columns"] else None) or []
            return t
    return await run_in_threadpool(go)


@router.patch("/label-tasks/{task_id}", dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def patch_task(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(T.patch_task, task_id, _b(body), C.caller(request))


@router.post("/label-tasks/{task_id}/members", status_code=201, dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def add_member(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    b = _b(body)
    return await run_in_threadpool(T.add_member, task_id, str(b.get("principal") or b.get("name") or ""), b.get("role", "annotator"), C.caller(request))


@router.delete("/label-tasks/{task_id}/members/{principal}", dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def remove_member(task_id: str, principal: str, request: Request, role: str = "annotator") -> dict[str, Any]:
    by = C.caller(request)

    def go():
        pid = C.resolve_principal(principal)
        with C.db().connect() as con:
            n = con.execute("DELETE FROM label_task_members WHERE task_id = ? AND principal_id = ? AND role = ?", (task_id, pid, role)).rowcount
            con.execute("UPDATE label_assignments SET status = 'open', assignee = NULL, leased_until = NULL WHERE task_id = ? AND assignee = ? AND status = 'leased'", (task_id, pid))
            C.audit(con, by, "label_task_member_removed", {"task_id": task_id, "principal_id": pid, "role": role}, object_kind="label_task", object_id=task_id)
        return {"removed": bool(n)}
    return await run_in_threadpool(go)


@router.post("/label-tasks/{task_id}/members/{principal}/resume", dependencies=[MEMBERSHIP])
async def resume(task_id: str, principal: str, request: Request) -> dict[str, Any]:
    return await run_in_threadpool(T.resume_member, task_id, principal, C.caller(request))


@router.post("/label-tasks/{task_id}/gold", dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def set_gold(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(T.set_gold, task_id, _b(body), C.caller(request))


@router.get("/label-tasks/{task_id}/gold", dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def get_gold(task_id: str) -> dict[str, Any]:
    return await run_in_threadpool(T.gold_summary, task_id)


@router.post("/label-tasks/{task_id}/fill", dependencies=[require("label.task_manage", path("task_id", "label_task"))])
async def fill(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(T.fill, task_id, _b(body), C.caller(request))


@router.post("/label-tasks/{task_id}/next", dependencies=[MEMBERSHIP])
async def next_items(task_id: str, request: Request, n: int = 25) -> dict[str, Any]:
    return await run_in_threadpool(T.next_items, task_id, C.caller(request), n)


@router.post("/label-tasks/{task_id}/answers", dependencies=[MEMBERSHIP])
async def answers(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    b = _b(body)
    return await run_in_threadpool(T.answer, task_id, C.caller(request), list(b.get("items") or []))


@router.get("/label-tasks/{task_id}/review", dependencies=[MEMBERSHIP])
async def review(task_id: str, request: Request, kind: str = "disputed") -> dict[str, Any]:
    return await run_in_threadpool(T.review, task_id, kind, C.caller(request))


@router.post("/label-tasks/{task_id}/adjudicate", dependencies=[MEMBERSHIP])
async def adjudicate(task_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    b = _b(body)
    items = list(b.get("items") or ([{k: b.get(k) for k in ("row_id", "column", "value", "comment")} | {"comment": b.get("note") or b.get("comment")}]
                                    if b.get("row_id") is not None else []))
    return await run_in_threadpool(T.adjudicate, task_id, C.caller(request), items)


@router.get("/label-tasks/{task_id}/quality", dependencies=[MEMBERSHIP])
async def quality(task_id: str, request: Request) -> dict[str, Any]:
    """Personnel data (review M12): reviewers of the task or its managers only, never shares."""
    pid = C.caller(request)

    def go():
        from fastapi import HTTPException

        with C.db().connect() as con:
            T.get_task(con, task_id)
            if T.membership(con, task_id, pid, "reviewer") is None and not T._is_manager(con, task_id, pid):
                raise HTTPException(403, {"code": "forbidden", "message": "Task quality is for its reviewers and managers."})
        return T.quality(task_id)
    return await run_in_threadpool(go)


@router.get("/me/label-tasks", dependencies=[public("lists only the caller's own task memberships")])
async def my_tasks(request: Request) -> dict[str, Any]:
    pid = C.caller(request)

    def go():
        with C.db().connect() as con:
            return C.rows(con, "SELECT t.id, t.name, t.layer_id, t.state, m.role, m.paused_at, m.guest FROM label_task_members m"
                               " JOIN label_tasks t ON t.id = m.task_id WHERE m.principal_id = ? ORDER BY t.created_at", (pid,))
    return {"tasks": await run_in_threadpool(go)}


# -- rules ------------------------------------------------------------------------------------------------------------------

@router.post("/label-layers/{layer_id}/rules", status_code=201, dependencies=[require("label.task_manage", path("layer_id", "label_layer"))])
async def create_rule(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(W.create_rule, layer_id, _b(body), C.caller(request))


@router.post("/label-rules/{rule_id}/run", dependencies=[require("label.task_manage", path("rule_id", "label_prompt"))])
async def run_rule(rule_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(W.run_rule, rule_id, _b(body).get("version_id"), C.caller(request))


@router.get("/label-layers/{layer_id}/rules/analysis", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def rule_analysis(layer_id: str) -> dict[str, Any]:
    return await run_in_threadpool(W.rule_analysis, layer_id)


# -- proposals ----------------------------------------------------------------------------------------------------------------

@router.post("/label-layers/{layer_id}/proposals", status_code=201, dependencies=[require("label.propose", path("layer_id", "label_layer"))])
async def propose(layer_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    return await run_in_threadpool(W.propose, layer_id, _b(body), C.caller(request))


@router.get("/label-layers/{layer_id}/proposals", dependencies=[require("label.read", path("layer_id", "label_layer"))])
async def list_proposals(layer_id: str) -> dict[str, Any]:
    return {"proposals": await run_in_threadpool(W.proposals, layer_id)}


@router.post("/label-proposals/{proposal_id}/decide", dependencies=[require("label.proposal_decide", path("proposal_id", "label_proposal"))])
async def decide(proposal_id: str, request: Request, body: Any = Body(None)) -> dict[str, Any]:
    b = _b(body)
    approve = bool(b.get("approve")) or str(b.get("decision") or "").lower() == "approve"
    return await run_in_threadpool(W.decide, proposal_id, approve, str(b.get("comment") or ""), C.caller(request), request.app)


# -- migration (L09) -------------------------------------------------------------------------------------------------------------

@router.post("/admin/labels/migrate", dependencies=[require("data.version", query("run_id", "run"))])
async def migrate(request: Request, run_id: str, dry_run: bool = False) -> dict[str, Any]:
    return await run_in_threadpool(W.migrate_run, request.app, run_id, C.caller(request), dry_run)
