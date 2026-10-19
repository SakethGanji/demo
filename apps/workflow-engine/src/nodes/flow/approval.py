"""Approval node - pause the run until a person approves or rejects (Stage 5, DESIGN-STAGE5-UIS section 4.2).

A real run never sleeps here. The first time the node runs it returns ``NodeExecutionResult(suspend=...)``: the runner
records the pause, nothing downstream is queued, and ``ExecutionRepository.complete`` turns it into a ``human_tasks`` row and a
``waiting`` execution. When the task is decided the resumer re-enters the run at this node with
``context.extras["resume"]``; the node then emits its input plus an ``approval`` object on the port of the decision.

Test mode: a mock (``{items, port}``) replaces the node before it runs, so it never suspends; an unmocked Approval fails
``TEST_UNMOCKED_EFFECT`` (effect ``human``).

``notify_email``: the resolved recipients ride in the suspend payload (``notify``); ``ExecutionRepository.complete`` emails
them a link to the task right after the task row is committed. A failed or skipped email never blocks the run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ...engine import shapes as sh
from ..base import (
    BaseNode,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
    NodeTypeDescription,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult

PORTS = ("approved", "rejected", "timed_out")
FORM_FIELD_TYPES = ("text", "textarea", "number", "select", "checkbox")
_DUE_RE = re.compile(r"^(\d+)([mhd])$")
_UNIT = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}
MIN_DUE = timedelta(minutes=1)
MAX_DUE = timedelta(days=90)
MAX_CONTEXT_BYTES = 64 * 1024
MAX_CONTEXT_VALUE_BYTES = 8 * 1024


class ApprovalConfigError(ValueError):
    """The node's own parameters are unusable (bad ``due_in``, bad form field). The node fails with this message."""

    code = "APPROVAL_CONFIG_INVALID"


def parse_due_in(value: Any) -> timedelta | None:
    """``"3h"`` -> 3 hours; empty -> None. Minimum 1 m, maximum 90 d."""
    if value in (None, ""):
        return None
    m = _DUE_RE.match(str(value).strip())
    if not m:
        raise ApprovalConfigError(f"due_in must look like 30m, 3h or 2d (got {value!r})")
    delta = int(m.group(1)) * _UNIT[m.group(2)]
    if delta < MIN_DUE or delta > MAX_DUE:
        raise ApprovalConfigError("due_in must be between 1m and 90d")
    return delta


def normalize_form(raw: Any) -> list[dict[str, Any]]:
    """Validate the approver form (``[{name, label, type, required?, options?}]``); returns clean dicts."""
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ApprovalConfigError("form must be a list of fields")
    if not isinstance(raw, list):
        raise ApprovalConfigError("form must be a list of fields")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in raw:
        if not isinstance(f, dict) or not str(f.get("name") or "").strip():
            raise ApprovalConfigError("every form field needs a name")
        name = str(f["name"]).strip()
        ftype = f.get("type") or "text"
        if ftype not in FORM_FIELD_TYPES:
            raise ApprovalConfigError(f"form field '{name}' has an unknown type '{ftype}'")
        if name in seen:
            raise ApprovalConfigError(f"form field '{name}' appears twice")
        seen.add(name)
        field: dict[str, Any] = {"name": name, "label": str(f.get("label") or name), "type": ftype,
                                 "required": bool(f.get("required", False))}
        if ftype == "select":
            opts = f.get("options") or []
            if not isinstance(opts, list) or not opts:
                raise ApprovalConfigError(f"select field '{name}' needs options")
            field["options"] = [str(o.get("value") if isinstance(o, dict) else o) for o in opts]
        out.append(field)
    return out


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def cap_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Keep a task context within 64 KB: oversized values become a truncated JSON string, ``_truncated`` is set."""
    out: dict[str, Any] = {}
    truncated = False
    budget = MAX_CONTEXT_BYTES - 64
    used = 0
    for k, v in ctx.items():
        size = _json_size(v)
        if size > MAX_CONTEXT_VALUE_BYTES:
            v = (v if isinstance(v, str) else json.dumps(v, default=str))[:MAX_CONTEXT_VALUE_BYTES]
            truncated = True
            size = MAX_CONTEXT_VALUE_BYTES
        if used + size > budget:
            truncated = True
            continue
        used += size
        out[k] = v
    if truncated:
        out["_truncated"] = True
    return out


def _dig(value: Any, path: str) -> Any:
    cur = value
    for part in [p for p in path.split(".") if p != ""]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def screen_bindings(context: Any, node_name: str) -> dict[str, Any]:
    """The ``node.<Node>.<path>`` values the task screen binds, captured now so the task page never reads node outputs."""
    from ...schemas.interface import split_node_bind, task_screen_bindings

    iface = getattr(context.workflow, "interface", None)
    out: dict[str, Any] = {}
    names = [n.name for n in context.workflow.nodes]
    for b in task_screen_bindings(iface, node_name):
        split = split_node_bind("node." + b, names)
        if split is None:
            continue
        node, path = split
        items = context.node_states.get(node) or []
        value = _dig(items[0].json, path) if items else None
        out[b] = value
    return out


def trigger_input(context: Any) -> dict[str, Any]:
    """The first item the workflow's trigger emitted (the submitter's input), for ``input.*`` bindings."""
    from ...engine.graph import TRIGGER_NODE_TYPES

    iface = getattr(context.workflow, "interface", None) or {}
    names = [iface.get("trigger_node")] if iface.get("trigger_node") else []
    names += [n.name for n in context.workflow.nodes if n.type in TRIGGER_NODE_TYPES]
    for name in names:
        items = context.node_states.get(name)
        if items:
            return dict(items[0].json)
    return {}


class ApprovalNode(BaseNode):
    """Approval - pause until a person approves, rejects, or the deadline passes."""

    node_description = NodeTypeDescription(
        name="Approval",
        display_name="Approval",
        description="Pause the run until a person approves or rejects (a durable task in the inbox)",
        icon="fa:user-check",
        group=["flow", "ui"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(name="approved", display_name="Approved", schema={"type": "unknown", "passthrough": True}),
            NodeOutputDefinition(name="rejected", display_name="Rejected", schema={"type": "unknown", "passthrough": True}),
            NodeOutputDefinition(name="timed_out", display_name="Timed out", schema={"type": "unknown", "passthrough": True}),
        ],
        properties=[
            NodeProperty(display_name="Title", name="title", type="string", default="",
                         description="Task title shown in the inbox (expressions allowed). Default: the node name"),
            NodeProperty(display_name="Assignees", name="assignees", type="json", default=[],
                         description="Typed emails or names. Empty = anyone in the project. Not enforced yet"),
            NodeProperty(display_name="Context", name="context", type="json", default={},
                         description="What the approver sees: {label: expression}"),
            NodeProperty(display_name="Form", name="form", type="json", default=[],
                         description="Extra inputs for the approver: [{name, label, type, required, options}]"),
            NodeProperty(display_name="Allow Comment", name="allow_comment", type="boolean", default=True),
            NodeProperty(display_name="Require Comment On Reject", name="require_comment_on_reject", type="boolean", default=False),
            NodeProperty(display_name="Due In", name="due_in", type="string", default="", placeholder="3h",
                         description="30m, 3h or 2d (1m to 90d). Empty = no deadline"),
            NodeProperty(
                display_name="On Timeout", name="on_timeout", type="options", default="timed_out",
                options=[
                    NodePropertyOption(name="Timed out port", value="timed_out"),
                    NodePropertyOption(name="Reject", value="reject"),
                    NodePropertyOption(name="Approve", value="approve"),
                ],
            ),
            NodeProperty(display_name="Four Eyes", name="four_eyes", type="boolean", default=False,
                         description="Placeholder: stored and shown, not enforced until identity exists"),
            NodeProperty(display_name="Notify Email", name="notify_email", type="string", default="",
                         placeholder="{{ $vars.APPROVER_EMAIL }}",
                         description="Email these addresses (comma-separated, expressions allowed) a link to the task. Empty = no email"),
            NodeProperty(display_name="Notify Message", name="notify_message", type="string", default="",
                         description="Optional extra text for the approver email", type_options={"rows": 3}),
        ],
    )

    @property
    def type(self) -> str:
        return "Approval"

    @property
    def description(self) -> str:
        return "Pause the run until a person approves or rejects"

    def contract(self, params, inputs, lookups=None):
        """Derived: the item passes through on every port with an ``approval`` object added."""
        item = sh.input_shape(inputs)
        approval = sh.object_of(
            {
                "decision": sh.scalar("string", "declared"),
                "comment": sh.scalar("string", "declared"),
                "form_data": sh.scalar("object", "declared"),
                "decided_by": sh.scalar("string", "declared"),
                "decided_at": sh.scalar("string", "declared"),
                "task_id": sh.scalar("string", "declared"),
            },
            ["decision"],
            "declared",
        )
        if sh.is_closed_object(item):
            props = dict(item["properties"])
            props["approval"] = approval
            shape = sh.object_of(props, list(item.get("required", [])) + ["approval"], "derived")
        else:
            shape = sh.unknown()
        import copy

        return sh.make_contract(inputs, {p: copy.deepcopy(shape) for p in PORTS})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData, NodeExecutionResult, Suspension

        resume = context.extras.get("resume")
        if isinstance(resume, dict) and resume.get("node") == node_definition.name and not resume.get("consumed"):
            resume["consumed"] = True  # a loop that reaches this node again suspends again (the dict stays: complete() reads task_id)
            return self._on_decision(resume, input_data)

        from ...engine.expression_engine import ExpressionEngine, expression_engine
        from ...services.vars_substitution import redact_deep

        params = node_definition.parameters
        items = input_data or [NodeData(json={})]
        ectx = ExpressionEngine.create_context(items, context.node_states, context.execution_id, 0, variables=context.variables)

        def render(value: Any) -> Any:
            return expression_engine.resolve(value, ectx)

        title = render(params.get("title")) if params.get("title") else ""
        title = str(title).strip() if title not in (None, "") else ""
        title = (title or node_definition.name)[:500]

        raw_ctx = params.get("context") or {}
        if isinstance(raw_ctx, str):
            try:
                raw_ctx = json.loads(raw_ctx)
            except ValueError:
                raw_ctx = {}
        rendered_ctx = {str(k): render(v) for k, v in raw_ctx.items()} if isinstance(raw_ctx, dict) else {}
        form = normalize_form(params.get("form"))
        assignees = params.get("assignees") or []
        if isinstance(assignees, str):
            assignees = [assignees]
        assignees = [str(a).strip() for a in (render(list(assignees)) if isinstance(assignees, list) else []) if isinstance(a, (str, int)) and str(a).strip()]

        delta = parse_due_in(render(params.get("due_in")) if params.get("due_in") else "")
        due_at = (datetime.now(timezone.utc) + delta).isoformat() if delta else None
        on_timeout = params.get("on_timeout") or "timed_out"
        if on_timeout not in ("timed_out", "reject", "approve"):
            raise ApprovalConfigError(f"on_timeout must be timed_out, reject or approve (got {on_timeout!r})")

        # What a task screen binds is captured now (redacted): the public task page never reads node outputs.
        captured = {"_bindings": screen_bindings(context, node_definition.name), "_input": trigger_input(context)}
        secrets = context.extras.get("secret_values") or set()
        body = cap_context(rendered_ctx)
        body.update(captured)
        payload = redact_deep(
            {
                "title": title,
                "context": body,
                "form": form,
                "assignees": assignees,
                "due_at": due_at,
                "on_timeout": on_timeout,
                "allow_comment": _flag(params.get("allow_comment"), True),
                "require_comment_on_reject": _flag(params.get("require_comment_on_reject"), False),
                "four_eyes": _flag(params.get("four_eyes"), False),
                **_notify_of(render, params),
            },
            secrets,
        )
        return NodeExecutionResult(outputs={}, suspend=Suspension(kind="approval", payload=payload))

    @staticmethod
    def _on_decision(resume: dict[str, Any], input_data: list[NodeData]) -> NodeExecutionResult:
        from ...engine.types import NodeData, NodeExecutionResult

        decision = resume.get("decision") or {}
        port = decision.get("decision") if decision.get("decision") in PORTS else "timed_out"
        approval = {
            "decision": port,
            "comment": decision.get("comment"),
            "form_data": decision.get("form_data") or {},
            "decided_by": decision.get("decided_by"),
            "decided_at": decision.get("decided_at"),
            "task_id": resume.get("task_id"),
        }
        items = [NodeData(json={**d.json, "approval": approval}) for d in (input_data or [NodeData(json={})])]
        return NodeExecutionResult(outputs={p: (items if p == port else None) for p in PORTS},
                                   metadata={"approvalDecision": port})


def _notify_of(render: Any, params: dict[str, Any]) -> dict[str, Any]:
    """``{"notify": {to, invalid, message}}`` when ``notify_email`` resolves to something, else nothing (a plain Approval's
    payload is unchanged). The email itself is sent once the task row exists (``services.approval_notify``)."""
    from ...services.approval_notify import split_recipients

    if not params.get("notify_email"):
        return {}
    to, invalid = split_recipients(render(params.get("notify_email")))
    if not to and not invalid:
        return {}
    message = render(params.get("notify_message")) if params.get("notify_message") else ""
    message = "" if message in (None, "") else str(message).strip()[:2000]
    return {"notify": {"to": to, "invalid": invalid, "message": message}}


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)
