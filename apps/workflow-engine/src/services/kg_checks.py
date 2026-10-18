"""Concept checks on a built workflow (PLAN-CAPABILITY-GRAPH section 4.3; CONTRACTS-CAPGRAPH.md 4.3, CGX01-CGX05).

Deterministic and pure: ``check(defn, understanding)`` returns warning-level findings in the checker's shape plus a ``fix_hint``. They rank
after the mistake checker and the grounding checks, travel to the repair prompt as ordinary ``findings`` (code ``CGX*``), and never stop Keep.

CGX01  a confirmed role slot is not used by any step (no step carries ``role:<key>``)
CGX02  Escalation is confirmed, but no Approval has ``due_in`` equal to the SLA with a wired ``timed_out`` port, and there is no Cron sweep
CGX03  a callable sub-workflow that implements a confirmed concept is not called, and steps covering that concept were built inline
CGX04  a Cron node's expression differs from the confirmed schedule slot
CGX05  an app part calls ``@part/<key>`` with inputs that do not match the sibling's contract (only when ``siblings`` is given)
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from . import kg_understand as ku

IMPLEMENTED = True

#: role slot -> where the role must appear: node types whose parameters should carry it (informational for the hint)
_ROLE_SLOT_STEP = {
    "approver_role": "Approval", "second_approver_role": "second Approval", "target_role": "escalation Approval", "reviewer_role": "Approval",
    "resolution_role": "Approval", "recipients_role": "SendEmail",
}


def finding(code: str, message: str, fix_hint: str, node: str | None = None) -> dict[str, Any]:
    return {"code": code, "severity": "warning", "message": f"{message} {fix_hint}", "node": node, "param": None, "path": None, "fix_hint": fix_hint}


def _nodes(defn: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [n for n in defn.get("nodes") or [] if isinstance(n, dict) and not n.get("disabled")]


def _params(n: Mapping[str, Any]) -> Mapping[str, Any]:
    return n.get("parameters") or {}


def _roles_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith("role:") else []
    if isinstance(value, list):
        return [r for v in value for r in _roles_in(v)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _roles_in(v)]
    return []


def _confirmed(u: Mapping[str, Any]) -> set[str]:
    return {c["key"] for c in u.get("concepts") or [] if c.get("confirmed")}


def _wired(defn: Mapping[str, Any], node: str, port: str) -> bool:
    return any(c.get("source_node") == node and c.get("source_output") == port and c.get("target_node") for c in defn.get("connections") or [])


def _norm_due(v: Any) -> str:
    return str(v or "").strip().lower()


def check(defn: Mapping[str, Any] | None, understanding: Mapping[str, Any] | None, *, siblings: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Concept findings for ``defn`` under ``understanding`` (empty when there is no understanding or no confirmed concept)."""
    if not defn or not understanding:
        return []
    confirmed = _confirmed(understanding)
    out: list[dict[str, Any]] = []
    nodes = _nodes(defn)
    out += _cgx01(nodes, understanding, confirmed)
    out += _cgx02(defn, nodes, understanding, confirmed)
    out += _cgx03(nodes, understanding, confirmed)
    out += _cgx04(nodes, understanding, confirmed)
    out += _cgx05(nodes, siblings)
    return out


def _cgx01(nodes: list[dict[str, Any]], u: Mapping[str, Any], confirmed: set[str]) -> list[dict[str, Any]]:
    text = json.dumps([_params(n) for n in nodes], default=str)
    labels = {r["key"]: r["label"] for r in ku.seed_vocabulary()[1]}
    out, seen = [], set()
    for sid, s in (u.get("slots") or {}).items():
        ckey = "concept:" + sid.split(".", 1)[0]
        name = sid.split(".", 1)[1] if "." in sid else sid
        if name not in _ROLE_SLOT_STEP or ckey not in confirmed or s.get("from") in ("default", "pattern"):
            continue
        for role in _roles_in(s.get("value")):
            if role in seen or role in text:
                continue
            seen.add(role)
            out.append(finding("CGX01", f"The role {labels.get(role, role)} was confirmed for {sid} but no step uses it.",
                               f"Put \"{role}\" in the `assignees` of the {_ROLE_SLOT_STEP[name]} step (or the recipients of the SendEmail step) exactly like that."))
    return out


def _cgx02(defn: Mapping[str, Any], nodes: list[dict[str, Any]], u: Mapping[str, Any], confirmed: set[str]) -> list[dict[str, Any]]:
    if "concept:escalation" not in confirmed:
        return []
    due = _norm_due(ku.slot_value(u, "sla.due_in", "escalation.after"))
    approvals = [n for n in nodes if n.get("type") == "Approval" and _params(n).get("due_in")]
    good = [n for n in approvals if (not due or _norm_due(_params(n).get("due_in")) == due or due.endswith("bd")) and _wired(defn, n["name"], "timed_out")]
    if good:
        return []
    if any(n.get("type") == "Cron" for n in nodes) and any(n.get("type") == "DataTable" for n in nodes):
        return []  # a sweep: Cron -> read the table -> act on what is overdue
    want = f" {due}" if due else ""
    if not approvals:
        msg, hint = "Escalation is confirmed but no Approval has `due_in` set.", f"Set `due_in` to \"{due or '<the SLA>'}\" on the Approval and wire its `timed_out` port to the escalation step."
    elif not any(_wired(defn, n["name"], "timed_out") for n in approvals):
        msg, hint = "Escalation is confirmed but the Approval's `timed_out` port is not wired.", "Connect the `timed_out` port to a second Approval or a notification for the escalation role."
    else:
        msg, hint = f"Escalation is confirmed but no Approval's `due_in` equals the SLA{want}.", f"Set `due_in` to \"{due}\" on the Approval whose `timed_out` port is wired."
    return [finding("CGX02", msg, hint, node=(approvals[0]["name"] if approvals else None))]


def _cgx03(nodes: list[dict[str, Any]], u: Mapping[str, Any], confirmed: set[str]) -> list[dict[str, Any]]:
    called = {_params(n).get("workflowId") for n in nodes if n.get("type") == "ExecuteWorkflow"}
    concepts = ku.seed_vocabulary()[0]
    types = {n.get("type") for n in nodes}
    out = []
    for sub in (u.get("retrieval") or {}).get("subworkflows", []):
        wid = sub["key"].split(":", 1)[-1]
        covered = [c for c in sub.get("concepts") or [] if c in confirmed]
        if wid in called or not covered:
            continue
        inline = [c for c in covered if c in concepts and any(sig.get("node_type") in types for sig in concepts[c].signatures)]
        if inline:
            out.append(finding("CGX03", f"The sub-workflow {sub['label']} ({wid}) already does {concepts[inline[0]].label}, but it is not called and the steps were built inline.",
                               f"Replace those steps with an ExecuteWorkflow node whose workflowId is \"{wid}\"."))
    return out


def _cgx04(nodes: list[dict[str, Any]], u: Mapping[str, Any], confirmed: set[str]) -> list[dict[str, Any]]:
    if not ({"concept:report", "concept:schedule"} & confirmed):
        return []
    want = ku.slot_value(u, "report.schedule", "schedule.cron")
    if not isinstance(want, str):
        return []
    out = []
    for n in nodes:
        if n.get("type") != "Cron":
            continue
        p = _params(n)
        have = " ".join(str(p.get("cronExpression") or "").split()) if p.get("mode") == "cron" else None
        if have != " ".join(want.split()):
            out.append(finding("CGX04", f"The Cron step does not use the confirmed schedule {want}.",
                               f"Set `mode` to \"cron\" and `cronExpression` to \"{want}\".", node=n.get("name")))
    return out


def _cgx05(nodes: list[dict[str, Any]], siblings: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for n in nodes:
        wid = _params(n).get("workflowId") if n.get("type") == "ExecuteWorkflow" else None
        if not (isinstance(wid, str) and wid.startswith("@part/")):
            continue
        sib = (siblings or {}).get(wid) or (siblings or {}).get(wid.removeprefix("@part/"))
        if sib is None:
            continue
        declared = {i["name"] for i in sib.get("inputs") or []}
        required = {i["name"] for i in sib.get("inputs") or [] if i.get("required")}
        given = _params(n).get("inputs")
        given = set(given) if isinstance(given, dict) else set()
        missing, extra = sorted(required - given), sorted(given - declared)
        if missing or extra:
            bits = ([f"is missing {', '.join(missing)}"] if missing else []) + ([f"passes unknown {', '.join(extra)}"] if extra else [])
            out.append(finding("CGX05", f"The call to {wid} {' and '.join(bits)}.",
                               f"Map exactly the inputs of {wid}: {', '.join(sorted(declared)) or 'none'}.", node=n.get("name")))
    return out
