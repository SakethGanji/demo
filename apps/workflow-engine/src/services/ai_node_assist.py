"""AI on any node (DESIGN-STAGE6-AI-BUILDER section 7): fix an error, write code, write an expression, explain.

Each task makes a proposal of kind ``node_assist`` on the workflow's stored draft, goes through the same check-and-test gate as the
builder and the same Keep / Discard / Undo. There is no spec phase: the gate is the workflow's own approved tests plus the checker.

* ``fix_error``         ``WF_AI:NODE_FIX`` -> ops, confined to the node, its direct neighbours and at most one node added next to it.
* ``write_code``        ``WF_AI:NODE_CODE`` -> ``set_parameter(node, "code", code)``; a one-item dry run of the node must pass.
* ``write_expression``  ``WF_AI:NODE_EXPR`` -> ``set_parameter(node, field, expression)``; AIX01-AIX04 and a dry evaluation must pass.
* ``explain``           ``WF_AI:NODE_EXPLAIN`` -> an answer, read only (cached in process by prompt hash).

fix_error and write_code get ONE repair round with structured feedback (the scope is one node). Run values never reach the model
unless ``WF_AI_SEND_RUN_VALUES=1`` (O3); by default it sees shapes only.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from typing import Any

from ..engine import ai_policy, wf_ai
from ..engine.wf_ai import AIError, Meter
from ..repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from . import ai_grounding, ai_repairs, workflow_diff, workflow_toolkit
from . import ai_builder_prompts as prompts
from . import ai_builder as B
from . import proposal_sandbox as sb
from .proposal_sandbox import ProposalError, ProposalNotFound

logger = logging.getLogger(__name__)

TASKS = ("fix_error", "write_code", "write_expression", "explain")
MAX_CODE_CHARS = 20000
FORBIDDEN_IMPORTS = ["os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib", "ctypes", "requests", "urllib", "http", "builtins"]


class NoFailedRun(ProposalError):
    http_status, code = 422, "no_failed_run"


# ── context about the node ──────────────────────────────────────────────────────────────────


def _neighbours(defn: dict[str, Any], name: str) -> tuple[set[str], set[str]]:
    ins = {c["source_node"] for c in defn.get("connections") or [] if c.get("target_node") == name}
    outs = {c["target_node"] for c in defn.get("connections") or [] if c.get("source_node") == name}
    return ins, outs


def _ancestors(defn: dict[str, Any], name: str) -> list[str]:
    parents: dict[str, set[str]] = {}
    for c in defn.get("connections") or []:
        parents.setdefault(c["target_node"], set()).add(c["source_node"])
    seen: list[str] = []
    stack = list(parents.get(name, ()))
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.append(n)
            stack.extend(parents.get(n, ()))
    return seen


def _shapes_of(check: dict[str, Any], defn: dict[str, Any], name: str) -> tuple[Any, dict[str, Any], Any]:
    """``(input_shape, upstream_shapes, output_shape)`` as compact shapes, from the checker's ``shapes_by_node``."""
    shapes = check.get("shapes") or {}
    item = ai_grounding._item_shape(shapes.get(name))
    inp = ai_grounding.compact_shape(item) if item is not None else "unknown"
    ups = {a: v for a, v in ai_grounding.compact_shapes({a: shapes.get(a) for a in _ancestors(defn, name) if a in shapes}).items()}
    out = ai_grounding.compact_shapes({name: shapes.get(name)}).get(name, "unknown")
    return inp, dict(list(ups.items())[:12]), out


def _node_view(node: dict[str, Any]) -> dict[str, Any]:
    params = copy.deepcopy(node.get("parameters") or {})
    out = {"name": node.get("name"), "type": node.get("type")}
    if node.get("type") in B._CODE_TYPES and isinstance(params.get("code"), str):
        out["untrusted_code"] = params.pop("code")
    out["parameters"] = params
    return out


def _findings_on(check: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return B._findings_for_model([f for f in check.get("findings") or [] if f.get("node") == name], 12)


async def _latest_error(workflow_id: str, node: str, execution_id: str | None) -> tuple[str, str, str | None]:
    """``(message, code, execution_id)`` of the node's failed run (the given one, else the latest). 404 when the run is not this workflow's."""
    from sqlalchemy import select

    from ..db.models import ExecutionModel, NodeOutputModel

    async with B._sf()() as s:
        q = (select(NodeOutputModel.error, NodeOutputModel.output, NodeOutputModel.execution_id)
             .join(ExecutionModel, ExecutionModel.id == NodeOutputModel.execution_id)
             .where(ExecutionModel.workflow_id == workflow_id, NodeOutputModel.node_name == node, NodeOutputModel.status == "error")
             .order_by(NodeOutputModel.created_at.desc()).limit(1))
        if execution_id:
            ex = await s.get(ExecutionModel, execution_id)
            if ex is None or ex.workflow_id != workflow_id:
                raise ProposalNotFound(f"Execution not found: {execution_id}")
            q = q.where(NodeOutputModel.execution_id == execution_id)
        row = (await s.execute(q)).first()
    if row is None:
        return "", "", execution_id
    err, output, eid = row
    code = ""
    if isinstance(output, dict):
        code = str(output.get("error_code") or output.get("code") or "")[:80]
    return str(err or ""), code, eid


# ── scope (fix_error) ───────────────────────────────────────────────────────────────────────


def _scope_error(op: B.ParsedOp, scope: set[str], added: set[str]) -> str | None:
    a = op.args
    allowed = scope | added
    if op.op == "add_node":
        if len(added) >= 1:
            return "Only one node may be added next to the failing node."
        return None
    if op.op in ("connect", "disconnect"):
        if a.get("source") not in allowed or a.get("target") not in allowed:
            return f"Both ends of a connection must be in scope: {', '.join(sorted(scope))}."
        return None
    if op.op in ("set_parameter", "set_parameters", "set_node_field", "set_test_default_mock"):
        return None if a.get("node") in allowed else f"'{a.get('node')}' is out of scope. You may touch: {', '.join(sorted(scope))}."
    if op.op == "remove_node":
        return None if a.get("name") in allowed else f"'{a.get('name')}' is out of scope. You may touch: {', '.join(sorted(scope))}."
    if op.op == "rename_node":
        return None if a.get("old") in scope else f"'{a.get('old')}' is out of scope."
    return f"Op '{op.op}' is not available when fixing one node."


def _confine(base: dict[str, Any], after: dict[str, Any], touched: set[str]) -> dict[str, Any]:
    """Undo whatever deterministic repairs did to nodes the user's request did not touch."""
    out = copy.deepcopy(after)
    base_nodes = {n["name"]: n for n in base.get("nodes") or []}
    out["nodes"] = [copy.deepcopy(base_nodes[n["name"]]) if n["name"] in base_nodes and n["name"] not in touched else n for n in out["nodes"]]
    keep_base = [c for c in base.get("connections") or [] if c["source_node"] not in touched and c["target_node"] not in touched]
    touched_after = [c for c in out.get("connections") or [] if c["source_node"] in touched or c["target_node"] in touched]
    out["connections"] = keep_base + touched_after
    for k in ("tests", "test_defaults", "interface", "settings"):
        if k in base:
            out[k] = copy.deepcopy(base[k])
        else:
            out.pop(k, None)
    return out


# ── the verdict ─────────────────────────────────────────────────────────────────────────────


def _node_errors(check: dict[str, Any], node: str) -> int:
    return sum(1 for f in check.get("findings") or [] if f.get("severity") == "error" and f.get("node") == node)


def verdict(new: B.RoundResult, base_errors: int, base_failed: int, node: str, *, changed: bool) -> bool:
    """No regression (checker errors, failing approved tests) and no checker error left on the node itself."""
    if not changed or new.tests is None and new.counts.get("approved_total", 0):
        return False
    return (new.errors <= base_errors and new.counts.get("approved_failed", 0) <= base_failed and _node_errors(new.check, node) == 0
            and not new.tampered)


def _samples(defn: dict[str, Any], report: dict[str, Any] | None, node: str) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """Example data for a dry run, taken from the workflow's own approved tests (synthetic fixtures, never run data): the item that
    reaches ``node`` in a test that ran, and the first item each upstream node emitted. ``(None, {})`` when no test reached it."""
    ins, _ = _neighbours(defn, node)
    ups = set(_ancestors(defn, node))
    item: dict[str, Any] | None = None
    by_node: dict[str, dict[str, Any]] = {}
    results = sorted((report or {}).get("results") or [], key=lambda r: r.get("status") != "passed")
    for r in results:
        for name, ports in (r.get("outputs") or {}).items():
            first = next((items[0] for items in ports.values() if items and isinstance(items[0], dict)), None)
            if first is None:
                continue
            if name in ups:
                by_node.setdefault(name, first)
            if name in ins and item is None:
                item = first
        if item is not None:
            break
    if item is None:  # the node's parent is the trigger: its output is the test's input
        for t in defn.get("tests") or []:
            items = (t.get("input") or {}).get("items") or []
            if (t.get("target") or {}).get("kind") == "workflow" and items and isinstance(items[0], dict) and any(
                    n.get("name") in ins and n.get("type") in B._TRIGGERS for n in defn.get("nodes") or []):
                item = items[0]
                break
    return item, by_node


def _example_input(shape_item: Any) -> dict[str, Any]:
    from .mock_gen import example_from_shape

    if shape_item is None:
        return {}
    ex = example_from_shape(shape_item)
    return ex if isinstance(ex, dict) else {}


# ── entry point ─────────────────────────────────────────────────────────────────────────────


async def run(workflow_id: str, node_name: str, task: str, actor: str, *, field: str | None = None, instruction: str | None = None,
              execution_id: str | None = None) -> tuple[int, dict[str, Any]]:
    """``POST /api/workflows/{id}/nodes/{name}/ai``. Returns ``(http_status, body)``: explain ``(200, {answer, proposal_id})``, else ``(201, ProposalView)``."""
    from ..repositories.workflow_repository import WorkflowRepository

    actor = B.clean_actor(actor)
    if task not in TASKS:
        raise B.RequestInvalid(f"Unknown task '{task}'. Tasks: {', '.join(TASKS)}.")
    cfg = wf_ai.config()
    model = cfg.model
    B.check_model(model)
    async with B._sf()() as s:
        stored = await WorkflowRepository(s).get(workflow_id)
        if stored is None:
            raise ProposalNotFound(f"Workflow not found: {workflow_id}")
        base, base_hash = sb.draft_of(stored)
        project_id, team_id = stored.project_id, getattr(stored, "team_id", None) or "default"
    node = next((n for n in base.get("nodes") or [] if n.get("name") == node_name), None)
    if node is None:
        raise ProposalNotFound(f"Node not found: {node_name}")
    if task == "write_expression" and not (field or "").strip():
        raise B.RequestInvalid("write_expression needs `field`: the parameter to write, for example body.amount.")
    if task == "write_code" and node.get("type") not in B._CODE_TYPES:
        raise B.RequestInvalid(f"write_code works on a Code node; '{node_name}' is a {node.get('type')}.")
    ctx = await B.load_ctx(project_id)
    clean_instruction, counts = ai_policy.scrub_request(instruction or "", ctx.secrets)
    error_text, error_code, error_exec = "", "", execution_id
    if task == "fix_error":
        error_text, error_code, error_exec = await _latest_error(workflow_id, node_name, execution_id)
        if not error_text and not clean_instruction.strip():
            raise NoFailedRun("This node has no failed run. Pass `execution_id` of a failed run or describe the problem in `instruction`.")

    pid = new_proposal_id()
    label = f"{task}: {node_name}" + (f" - {clean_instruction}" if clean_instruction else "")
    async with B._sf()() as s:
        repo = AIProposalRepository(s)
        await repo.insert({
            "id": pid, "project_id": project_id, "team_id": team_id, "workflow_id": workflow_id, "kind": "node_assist",
            "status": "building", "actor": actor, "request": label[:4000], "request_sha256": B._sha(instruction or label),
            "base_definition": base, "base_draft_hash": base_hash, "model": model, "provider": ai_policy.provider_of(model),
            "region": ai_policy.region()})
        await repo.add_event(pid, "spec_requested", {"request_sha256": B._sha(instruction or label), "model": model}, actor)
        if counts["secrets"] or counts["pii_total"]:
            await repo.add_event(pid, "scrubbed", {"secrets": counts["secrets"], "pii": counts["pii"]})
        await s.commit()

    t0 = time.monotonic()
    meter = Meter(max_calls=cfg.max_calls, deadline_s=cfg.build_deadline_s, case=ctx.project_id, secret_values=ctx.secrets)
    flusher = B.Flusher(pid, meter)
    try:
        with B._Busy(project_id, actor) if task != "explain" else _NoLock():
            base_check = B.check_all(base, ctx)
            common = {"flusher": flusher, "meter": meter, "ctx": ctx, "model": model}
            if task == "explain":
                return await _explain(pid, node, base, base_check, clean_instruction, t0, **common)
            return await _assist(pid, task, node, base, base_hash, base_check, clean_instruction, field, error_text, error_code, t0,
                                 stored_project=project_id, team_id=team_id, workflow_id=workflow_id, **common)
    except B.BuilderBusy:
        await _drop(pid)
        raise
    except AIError as e:
        await B._fail(pid, e, "building")
        raise B.ai_error_to_http(e, proposal_id=pid) from None
    except ProposalError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("node assist crashed")
        await B._fail(pid, AIError("internal_error", f"The assist crashed ({type(e).__name__})."), "building")
        raise B.AIFailed("The assist crashed. Nothing was saved.", ai_code="internal_error", proposal_id=pid) from None


class _NoLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *a: Any) -> None:
        return None


async def _drop(pid: str) -> None:
    """A proposal that never started (the project already has a build running) is discarded, not left ``building``."""
    async with B._sf()() as s:
        repo = AIProposalRepository(s)
        await repo.transition(pid, ["building"], "discarded")
        await s.commit()


# ── explain ─────────────────────────────────────────────────────────────────────────────────


async def _explain(pid: str, node: dict[str, Any], base: dict[str, Any], base_check: dict[str, Any], question: str, t0: float, *,
                   flusher: B.Flusher, meter: Meter, ctx: B.Ctx, model: str) -> tuple[int, dict[str, Any]]:
    inp, _, out = _shapes_of(base_check, base, node["name"])
    nv = _node_view(node)
    code = nv.pop("untrusted_code", "")
    entry = next((e for e in ai_grounding.catalog_subset(None, ctx.catalog, extra_types=[node["type"]]) if e.get("type") == node["type"]), {})
    reply = await B.ask_model("node_explain", {
        "node": nv, "catalog_entry": entry, "input_shape": {"item": inp}, "output_shape": {"item": out},
        "findings": _findings_on(base_check, node["name"]), "untrusted_question": question,
        "untrusted_description": "\n".join(x for x in (node.get("label") or "", code) if x)[:4000],
    }, prompts.NODE_EXPLAIN_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
    answer = str(reply.get("answer") or "").strip()
    async with B._sf()() as s:
        repo = AIProposalRepository(s)
        await repo.transition(pid, ["building"], "answered", {"answer": answer, "report": {"assist": {"task": "explain", "node": node["name"]}}})
        await repo.add_usage(pid, duration_ms=int((time.monotonic() - t0) * 1000))
        await s.commit()
    return 200, {"answer": answer, "proposal_id": pid}


# ── fix_error / write_code / write_expression ───────────────────────────────────────────────


async def _assist(pid: str, task: str, node: dict[str, Any], base: dict[str, Any], base_hash: str, base_check: dict[str, Any], instruction: str,
                  field: str | None, error_text: str, error_code: str, t0: float, *, flusher: B.Flusher, meter: Meter, ctx: B.Ctx, model: str,
                  stored_project: str, team_id: str, workflow_id: str, **_: Any) -> tuple[int, dict[str, Any]]:
    name = node["name"]
    proposal = {"id": pid, "project_id": stored_project, "team_id": team_id, "workflow_id": workflow_id, "spec": None}
    expected = [t for t in base.get("tests") or [] if t.get("status") == "approved" or t.get("locked")]
    cfg = wf_ai.config()
    st = B.LoopState(ctx=ctx, spec=None, max_nodes=cfg.max_nodes)
    base_names = {n["name"] for n in base.get("nodes") or []}
    inp, ups, _out = _shapes_of(base_check, base, name)
    ins, outs = _neighbours(base, name)
    scope = {name} | ins | outs
    # the starting point's own numbers: the fix must not make either worse
    b_check, _, b_report, b_counts, _ = await B.evaluate(base, ctx, proposal, None, expected)
    base_errors, base_failed = b_check["errors"], (999 if b_report is None and b_counts["approved_total"] else b_counts["approved_failed"])
    await B.add_events(pid, [("round_started", {"round": 0}, None)])

    ops_log: list[dict[str, Any]] = []
    rounds: list[B.RoundResult] = []
    stopped = "passed"
    defn = base
    feedback: list[dict[str, Any]] = []
    dry_run: dict[str, Any] | None = None
    current_code = (node.get("parameters") or {}).get("code") if task == "write_code" else ""
    current_value = ""
    if task == "write_expression":
        cur = node.get("parameters") or {}
        for part in re.findall(r"[^.\[\]]+|\[\d+\]", field or ""):
            cur = (cur.get(part) if isinstance(cur, dict) else cur[int(part.strip("[]"))] if isinstance(cur, list) and part.strip("[]").isdigit() else None)
            if cur is None:
                break
        current_value = cur if isinstance(cur, str) else (B.sb.canonical_json(cur) if cur is not None else "")
    max_rounds = 2 if task in ("fix_error", "write_code") else 1
    final_round: B.RoundResult | None = None

    for n in range(max_rounds):
        if n:
            await B.add_events(pid, [("round_started", {"round": n}, None)])
        # ---- ask ----
        if task == "fix_error":
            entry = next((e for e in ai_grounding.catalog_subset(None, ctx.catalog, extra_types=[node["type"]]) if e.get("type") == node["type"]), {})
            payload = {"node": _node_view(node), "catalog_entry": entry, "untrusted_error": error_text, "error_code": error_code,
                       "input_shape": {"item": inp}, "upstream_shapes": ups, "findings": _findings_on(base_check, name) + feedback,
                       "untrusted_instruction": instruction, "current_workflow": B.compact_definition(defn),
                       "scope": sorted(scope), "catalog": ai_grounding.catalog_subset(None, ctx.catalog, extra_types={x["type"] for x in defn["nodes"] if x["name"] in scope}),
                       "api_operations": ctx.api_ops, "variable_names": ctx.variable_names}
            if ai_policy.send_run_values():
                payload["input_shape"] = {"item": inp, "note": "run values are not included in this build of the assist"}
            reply = await B.ask_model("node_fix", payload, prompts.BUILD_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
            ops, op_errors = B.parse_ops(reply, cfg.max_ops)
            added: set[str] = set()
            allowed_ops: list[B.ParsedOp] = []
            for op in ops:
                err = _scope_error(op, scope, added)
                if err:
                    op_errors.append({"op_index": op.index, "op": op.op, "message": err})
                    ops_log.append({"round": n, "op_index": op.index, "op": op.op, "args": B._scrub_args(op.args, ctx.secrets), "result": "error", "message": err})
                    continue
                if op.op == "add_node" and op.args.get("name"):
                    added.add(op.args["name"])
                allowed_ops.append(op)
            new, more, log = B.apply_round_ops(defn, allowed_ops, st, n)
            op_errors += more
            ops_log += log
            touched = scope | {x for x in added}
            new, fixes, mock_warnings, warns = B.finalize(new, st, base_names)
            new = _confine(base, new, touched)
            new["test_defaults"] = copy.deepcopy(base.get("test_defaults") or {})  # the assist never rewrites default mocks
            n_ops = len(reply.get("ops") or [])
        elif task == "write_code":
            payload = {"node": {"name": name, "type": node["type"]}, "field": field or "code", "untrusted_instruction": instruction,
                       "untrusted_current_code": current_code or "", "input_shape": {"item": inp}, "upstream_shapes": ups,
                       "forbidden_imports": FORBIDDEN_IMPORTS, "untrusted_error": (dry_run or {}).get("message", error_text)}
            reply = await B.ask_model("node_code", payload, prompts.NODE_CODE_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
            code = str(reply.get("code") or "")
            op_errors, log = [], []
            if not code.strip() or len(code) > MAX_CODE_CHARS:
                op_errors.append({"op_index": 0, "op": "set_parameter", "message": "The model sent no code, or too much of it."})
                new = defn
            elif ai_policy.find_secret(code, ctx.secrets):
                op_errors.append({"op_index": 0, "op": "set_parameter", "message": "The code contains something that looks like a credential."})
                new = defn
            else:
                new = workflow_toolkit.set_parameter(base, name, field or "code", code)
                log = [{"round": n, "op_index": 0, "op": "set_parameter", "args": {"node": name, "path": field or "code"}, "result": "ok",
                        "message": str(reply.get("explanation") or "")[:300]}]
            ops_log += log
            fixes, mock_warnings, warns, n_ops = [], [], [], 1
            current_code = code
        else:  # write_expression
            up_names = _ancestors(defn, name)
            payload = {"node": {"name": name, "type": node["type"]}, "field": field, "untrusted_instruction": instruction,
                       "untrusted_current_value": current_value, "input_shape": {"item": inp}, "upstream_shapes": ups, "upstream_nodes": up_names}
            reply = await B.ask_model("node_expr", payload, prompts.NODE_EXPR_SCHEMA, meter=meter, flusher=flusher, ctx=ctx, model=model)
            expr = ai_repairs.normalize_expression_text(str(reply.get("expression") or "").strip())
            if expr and "{{" not in expr and re.search(r"\$json|\$node|\$vars|\$input", expr):
                expr = "{{ " + expr + " }}"
            op_errors, log, fixes, mock_warnings, warns, n_ops = [], [], [], [], [], 1
            if not expr:
                op_errors.append({"op_index": 0, "op": "set_parameter", "message": "The model sent no expression."})
                new = defn
            elif ai_policy.find_secret(expr, ctx.secrets):
                op_errors.append({"op_index": 0, "op": "set_parameter", "message": "The expression contains something that looks like a credential."})
                new = defn
            else:
                try:
                    new = workflow_toolkit.set_parameter(base, name, field or "", expr)
                    log = [{"round": 0, "op_index": 0, "op": "set_parameter", "args": {"node": name, "path": field}, "result": "ok",
                            "message": str(reply.get("explanation") or "")[:300]}]
                except workflow_toolkit.ToolkitError as e:
                    op_errors.append({"op_index": 0, "op": "set_parameter", "message": e.message})
                    new = defn
            ops_log += log
        await B.add_events(pid, [("ops_applied", {"round": n, "ok": len(log), "rejected": len(op_errors),
                                                   "auto_fixed": sum(1 for f in fixes if f.get("changed"))}, None)])
        changed = B.sb.canonical_json(new) != B.sb.canonical_json(base)
        check, coverage, report, counts, tampered = await B.evaluate(new, ctx, proposal, None, expected)
        # task-specific gates
        extra_errors: list[str] = []
        if task == "write_expression" and changed:
            extra_errors += _expression_gate(new, name, field or "", check, _samples(base, b_report, name))
        if task == "write_code" and changed:
            dry_run = await _code_dry_run(new, name, _samples(base, b_report, name)[0], ctx, proposal)
            if dry_run["status"] != "passed":
                extra_errors.append(dry_run["message"])
        r = B.RoundResult(n=n, defn=new, ops=n_ops, rejected=len(op_errors), auto_fixed=sum(1 for f in fixes if f.get("changed")), op_errors=op_errors,
                          auto_fixes=fixes, check=check, coverage=coverage, tests=report, counts=counts, tampered=tampered,
                          mock_warnings=mock_warnings, warnings=warns + extra_errors)
        await B.add_events(pid, [("checked", {"round": n, "errors": r.errors, "warnings": int(check["warnings"])}, None)] + (
            [("tested", {"round": n, "passed": counts["passed"], "failed": counts["failed"], "errored": counts["errored"]}, None)] if report is not None else []))
        rounds.append(r)
        good = verdict(r, base_errors, base_failed, name, changed=changed) and not extra_errors
        final_round = r
        if good:
            stopped = "passed"
            break
        stopped = "rounds_exhausted"
        if not changed and not op_errors and n == 0 and task != "fix_error":
            break
        # feedback for the one repair round
        feedback = [{"code": "OP_ERROR", "severity": "error", "node": name, "message": e["message"]} for e in op_errors]
        feedback += [{"code": f.get("code"), "severity": f.get("severity"), "node": f.get("node"), "message": f.get("message")}
                     for f in B._findings_for_model(check.get("findings") or [], 8) if f.get("severity") == "error"]
        feedback += [{"code": "TEST_FAILED", "severity": "error", "node": None,
                      "message": f"{t['name']}: " + "; ".join(str(x.get("message") or x) for x in t["failed"][:3]) + f" (path: {' > '.join(t['trace'])})"}
                     for t in B.distill_tests(report, 3)]
        feedback += [{"code": "CHECK", "severity": "error", "node": name, "message": m} for m in extra_errors]
        defn = new if task == "fix_error" else base

    assert final_round is not None
    final = B._canonical_definition(final_round.defn)
    diff = workflow_diff.diff_definitions(base, final)
    changed_any = B.sb.canonical_json(final) != B.sb.canonical_json(B._canonical_definition(base))
    passed = stopped == "passed" and changed_any
    report_doc = {
        "assist": {"task": task, "node": name, "field": field}, "passed": passed, "rounds": [r.report_row() for r in rounds],
        "best_round": final_round.n, "checker": {"errors": final_round.errors, "warnings": int(final_round.check.get("warnings", 0)),
                                                  "findings": B._findings_for_model(final_round.check.get("findings") or [], 100)},
        "tests": final_round.tests, "grounding": [{"code": g["code"], "node": g["node"], "message": g["message"]} for g in final_round.grounding],
        "dry_run": dry_run, "stopped_because": stopped if changed_any else "no_change", "warnings": final_round.warnings,
        "base": {"checker_errors": base_errors, "approved_failed": base_failed},
    }
    await flusher.flush()
    await ops_flush(pid, ops_log)
    await B._finish(pid, "ready", {"proposed_definition": final, "diff": diff, "report": report_doc},
                    {"status": "ready", "passed": passed, "rounds": len(rounds)}, t0)
    return 201, await B.get_view(pid)


async def ops_flush(pid: str, ops_log: list[dict[str, Any]]) -> None:
    async with B._sf()() as s:
        await AIProposalRepository(s).append_ops_log(pid, ops_log)
        await s.commit()


def _expression_gate(defn: dict[str, Any], node: str, field: str, check: dict[str, Any], samples: tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]) -> list[str]:
    """AIX01-AIX04 on the written expression, then a dry evaluation against an example item (from the workflow's own tests when a test
    reached the node, else shaped like the node's input)."""
    from ..engine.expression_engine import ExpressionEngine, expression_engine
    from ..engine.types import NodeData

    top = re.split(r"[.\[]", field, maxsplit=1)[0]
    out = [g["message"] for g in check.get("grounding") or [] if g["node"] == node and (g.get("param") or "").startswith(top)]
    if out:
        return out
    n = next((x for x in defn["nodes"] if x["name"] == node), None) or {}
    cur: Any = n.get("parameters") or {}
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", field):
        cur = cur.get(part) if isinstance(cur, dict) else (cur[int(part.strip("[]"))] if isinstance(cur, list) and part.strip("[]").isdigit() and int(part.strip("[]")) < len(cur) else None)
    try:
        shapes = check.get("shapes") or {}
        item, by_node = samples
        example = item if item is not None else _example_input(ai_grounding._item_shape(shapes.get(node)))
        states = {up: [NodeData(json=ex)] for up, ex in by_node.items()}
        for up in _ancestors(defn, node):
            if up not in states:
                states[up] = [NodeData(json=_example_input(ai_grounding._main_output(shapes.get(up))))]
        ctx = ExpressionEngine.create_context([NodeData(json=example)], states, "dry_run", variables={})
        expression_engine.resolve(cur, ctx)
    except Exception as e:  # noqa: BLE001
        return [f"The expression failed on an example item: {type(e).__name__}: {str(e)[:200]}"]
    return []


async def _code_dry_run(defn: dict[str, Any], node: str, sample: dict[str, Any] | None, ctx: B.Ctx, proposal: dict[str, Any]) -> dict[str, Any]:
    """One example item through the Code node, as a draft node-target test added to a COPY of the definition (never saved)."""
    from . import ai_spec

    if sample is None:
        try:
            item = ai_grounding._item_shape((B.check_all(defn, ctx).get("shapes") or {}).get(node))
        except Exception:  # noqa: BLE001
            item = None
        sample = _example_input(item)
    tid = ai_spec.new_test_id()
    test = {"id": tid, "name": "AI dry run", "target": {"kind": "node", "node": node}, "input": {"items": [sample]}, "mocks": {},
            "expect": [{"node": node, "op": "status", "value": "success"}], "category": "normal", "status": "draft", "locked": False, "source": "ai"}
    d = copy.deepcopy(defn)
    d["tests"] = list(d.get("tests") or []) + [test]
    try:
        async with B._sf()() as s:
            rep = await sb.run_proposal_tests(s, proposal, d, ids=[tid])
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": f"The dry run could not start: {getattr(e, 'message', None) or type(e).__name__}"}
    res = (rep.get("results") or [{}])[0]
    if res.get("status") == "passed":
        return {"status": "passed", "message": ""}
    msgs = [str(f.get("message")) for f in res.get("failures") or []][:3]
    return {"status": res.get("status", "error"), "message": "The code failed on an example item: " + "; ".join(msgs)}
