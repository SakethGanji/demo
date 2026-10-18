"""Deterministic concept signatures over a workflow definition (PLAN-CAPABILITY-GRAPH section 2.2; CONTRACTS-CAPGRAPH.md section 4).

``detect(defn)`` is pure. A concept is found when ANY of its signatures (``props.signatures`` of the concept node, see
``knowledge/ontology/bank_ops.yaml``) matches. A signature is ``{node_type, params, ports, follows, writes_table_with}``:

* ``node_type``         a node of this type exists;
* ``params``            ... and every named parameter is set on it;
* ``ports``             ... and every named output port is wired to something;
* ``follows``           ... and, for each type named, a node of that type is upstream of it (any distance);
* ``writes_table_with`` ... and (DataTable) every named column appears in the row it writes.

Signatures on ``HttpRequest``, ``Merge``, ``Loop`` and ``Respond`` are ignored (``WEAK_NODE_TYPES``): the node type alone says
nothing about the meaning, and they would tag every workflow that calls an API or returns a body. API operations are tagged by
``kg_extract`` from their names instead.

Detection also *reads slot values back* from the definition (assignees, ``due_in``, the Cron expression, the Switch field, the
table name) keyed ``"<concept slug>.<slot>"``. Learning uses them to turn a kept workflow into a slotted skeleton.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping, TypedDict

IMPLEMENTED = True

WEAK_NODE_TYPES = frozenset({"HttpRequest", "Merge", "Loop", "Respond"})
_EXPR = re.compile(r"^\s*\{\{\s*\$json\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}\s*$")
_ROLE = re.compile(r"^role:[a-z][a-z0-9_]*$")


class Detection(TypedDict):
    """One concept found in a definition."""

    concept: str  # concept key, e.g. "concept:escalation"
    evidence: dict[str, Any]  # {"nodes": [node names], "ports": [wired port names]}
    slots: dict[str, Any]  # slot values read back from the definition, keyed "<concept slug>.<slot>", e.g. {"sla.due_in": "48h"}


@lru_cache(maxsize=1)
def _seed_concepts() -> tuple[dict[str, Any], ...]:
    from . import kg_ontology

    return tuple({"key": c["key"], "signatures": list(c.get("signatures") or [])} for c in kg_ontology.load().concepts)


def _signatures_of(concept: Mapping[str, Any]) -> list[dict[str, Any]]:
    sigs = concept.get("signatures")
    if sigs is None:
        sigs = (concept.get("props") or {}).get("signatures")
    return [s for s in (sigs or []) if isinstance(s, Mapping)]


# ── graph helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────


class _Graph:
    def __init__(self, defn: Mapping[str, Any]) -> None:
        self.nodes: list[dict[str, Any]] = [n for n in (defn.get("nodes") or []) if isinstance(n, Mapping) and n.get("name")]
        self.by_name = {n["name"]: n for n in self.nodes}
        self.edges = [c for c in (defn.get("connections") or []) if isinstance(c, Mapping)]
        self._parents: dict[str, set[str]] = {}
        for c in self.edges:
            self._parents.setdefault(c.get("target_node"), set()).add(c.get("source_node"))

    def ancestors(self, name: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self._parents.get(name, ()))
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in self.by_name:
                continue
            seen.add(cur)
            stack.extend(self._parents.get(cur, ()))
        return seen

    def wired_ports(self, name: str) -> set[str]:
        return {str(c.get("source_output") or "main") for c in self.edges if c.get("source_node") == name}

    def targets(self, name: str, port: str) -> list[str]:
        return [c["target_node"] for c in self.edges if c.get("source_node") == name and (c.get("source_output") or "main") == port
                and c.get("target_node") in self.by_name]

    def ordered(self) -> list[dict[str, Any]]:
        """Nodes in a stable walk order: triggers (no parents) first, then breadth-first by connection order."""
        roots = [n for n in self.nodes if not self._parents.get(n["name"])]
        out, seen, queue = [], set(), list(roots)
        while queue:
            n = queue.pop(0)
            if n["name"] in seen:
                continue
            seen.add(n["name"])
            out.append(n)
            for c in self.edges:
                if c.get("source_node") == n["name"] and c.get("target_node") in self.by_name:
                    queue.append(self.by_name[c["target_node"]])
        out += [n for n in self.nodes if n["name"] not in seen]
        return out


def ordered_nodes(defn: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _Graph(defn).ordered()


def _is_set(v: Any) -> bool:
    return v not in (None, "", [], {})


def _walk(obj: Any):
    """Every (key, value) pair and every string in a parameter tree."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _written_columns(params: Mapping[str, Any]) -> set[str]:
    cols: set[str] = set()
    for k, v in _walk({"data": params.get("data"), "matchColumns": params.get("matchColumns")}):
        if isinstance(k, str):
            cols.add(k.lower())
        if isinstance(v, str):
            cols.update(w.lower() for w in re.findall(r"\$json\.([A-Za-z_][A-Za-z0-9_]*)", v))
    return cols


def _node_matches(g: _Graph, node: Mapping[str, Any], sig: Mapping[str, Any]) -> bool:
    params = node.get("parameters") or {}
    if not all(_is_set(params.get(p)) for p in sig.get("params") or []):
        return False
    if sig.get("ports") and not set(sig["ports"]) <= g.wired_ports(node["name"]):
        return False
    if sig.get("follows"):
        types_up = {g.by_name[a].get("type") for a in g.ancestors(node["name"])}
        if not set(sig["follows"]) <= types_up:
            return False
    if sig.get("writes_table_with"):
        if params.get("operation") not in ("insert", "update", "upsert", "create"):
            return False
        if not {c.lower() for c in sig["writes_table_with"]} <= _written_columns(params):
            return False
    return True


# ── slot read-back ────────────────────────────────────────────────────────────────────────────────────────────────────


def _field_of(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    m = _EXPR.match(value)
    return m.group(1) if m else (value.strip() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value.strip()) else None)


def _assignees(node: Mapping[str, Any]) -> list[str]:
    a = (node.get("parameters") or {}).get("assignees") or []
    if isinstance(a, str):
        a = [a]
    return [str(x).strip() for x in a if isinstance(x, (str, int)) and str(x).strip()]


def _role_of(node: Mapping[str, Any]) -> str | None:
    return next((a for a in _assignees(node) if _ROLE.match(a)), None)


def entity_of_table(table: str) -> str:
    """``exception_cases`` -> ``exception``; ``fraud_alerts`` -> ``fraud_alert``."""
    t = re.sub(r"[^a-z0-9_]+", "_", str(table).lower()).strip("_")
    t = re.sub(r"_(cases?|records?|items?|log)$", "", t) or t
    return t[:-1] if t.endswith("s") and len(t) > 3 else t


def _slots_for(concept: str, g: _Graph, hit_nodes: list[dict[str, Any]], defn: Mapping[str, Any]) -> dict[str, Any]:
    slug = concept.split(":", 1)[1]
    out: dict[str, Any] = {}
    by_type: dict[str, list[dict[str, Any]]] = {}
    for n in g.ordered():
        by_type.setdefault(n.get("type"), []).append(n)
    if slug == "four_eyes_approval":
        approvals = [n for n in g.ordered() if n.get("type") == "Approval"]
        if approvals and _role_of(approvals[0]):
            out["four_eyes_approval.approver_role"] = _role_of(approvals[0])
        if hit_nodes and _role_of(hit_nodes[0]):
            out["four_eyes_approval.second_approver_role"] = _role_of(hit_nodes[0])
    elif slug in ("approval", "review", "document_review"):
        first = next((n for n in hit_nodes if n.get("type") == "Approval"), None)
        role = _role_of(first) if first is not None else None
        if role:
            out[f"{slug}.approver_role" if slug == "approval" else f"{slug}.reviewer_role"] = role
    if slug in ("sla", "escalation"):
        node = next((n for n in hit_nodes if (n.get("parameters") or {}).get("due_in")), None)
        if node is not None:
            due = str(node["parameters"]["due_in"]).strip()
            out["sla.due_in"] = due
            if slug == "escalation":
                out["escalation.after"] = due
    if slug == "escalation":
        node = next((n for n in hit_nodes if n.get("type") == "Approval"), None)
        if node is not None:
            for t in g.targets(node["name"], "timed_out"):
                if g.by_name[t].get("type") == "Approval" and _role_of(g.by_name[t]):
                    out["escalation.target_role"] = _role_of(g.by_name[t])
                    break
    if slug in ("schedule", "report"):
        cron = next((n for n in by_type.get("Cron", []) if (n.get("parameters") or {}).get("cronExpression")), None)
        if cron is not None:
            out["schedule.cron" if slug == "schedule" else "report.schedule"] = str(cron["parameters"]["cronExpression"]).strip()
    if slug == "report":
        for n in by_type.get("SendEmail", []):
            to = str((n.get("parameters") or {}).get("toEmail") or "")
            if _ROLE.match(to.strip()):
                out["report.recipients_role"] = to.strip()
                break
    if slug == "assignment":
        for n in hit_nodes:
            p = n.get("parameters") or {}
            f = _field_of(p.get("field"))
            if f is None:
                for r in p.get("rules") or []:
                    f = _field_of(r.get("field")) if isinstance(r, Mapping) else None
                    if f:
                        break
            if f:
                out["assignment.by_field"] = f
                break
    if slug in ("exception_case", "customer_request"):
        node = next((n for n in hit_nodes if (n.get("parameters") or {}).get("table")), None)
        table = (node or {}).get("parameters", {}).get("table")
        if isinstance(table, str) and table and "{{" not in table and slug == "exception_case":
            out["exception_case.entity"] = entity_of_table(table)
    if slug == "notification":
        if by_type.get("SendEmail"):
            out["notification.channel"] = "node:SendEmail"
    if slug == "intake":
        trig = next((n for n in hit_nodes), None)
        if trig is not None:
            out["intake.channel"] = {"FormTrigger": "form", "Webhook": "webhook", "ChatInput": "chat"}.get(trig.get("type"), "form")
    return out


# ── detect ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def detect(defn: Mapping[str, Any], *, concepts: list[dict[str, Any]] | None = None) -> list[Detection]:
    """Pure. The concepts whose signature (``props.signatures`` of the concept node) matches ``defn``, with the slot values the
    definition already contains (assignees, ``due_in``, a Cron expression, the Switch field, a table name).

    ``concepts`` are concept node rows (``KgNode`` dicts, or ``{"key", "signatures"}``); None means the seed ontology from
    ``kg_ontology.load()``. Detections come back in the order of ``concepts``."""
    if not isinstance(defn, Mapping):
        return []
    g = _Graph(defn)
    out: list[Detection] = []
    for c in (concepts if concepts is not None else _seed_concepts()):
        hits: list[dict[str, Any]] = []
        ports: set[str] = set()
        for sig in _signatures_of(c):
            if sig.get("node_type") in WEAK_NODE_TYPES:
                continue
            for n in g.nodes:
                if n.get("type") == sig.get("node_type") and _node_matches(g, n, sig) and n not in hits:
                    hits.append(n)
                    ports |= set(sig.get("ports") or [])
        if not hits:
            continue
        key = c["key"]
        out.append({"concept": key, "evidence": {"nodes": [n["name"] for n in hits], "ports": sorted(ports)},
                    "slots": _slots_for(key, g, hits, defn)})
    return out
