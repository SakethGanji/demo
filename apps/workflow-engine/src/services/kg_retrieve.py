"""Retrieval over the scoped graph slice (PLAN-CAPABILITY-GRAPH section 3; CONTRACTS-CAPGRAPH.md section 3).

POC rules (owner decision, 2026-10-18): no scoring tiers and no fit bands. ``retrieve`` lists what implements the request's concepts, in a
plain order (most concepts covered first, then the most often validated, then by label). A pattern *matches* when it covers at least two of
the request's confirmed concepts (one, when the request has only one); the first matching pattern of the request's shape is the adapt seed.
``fit`` and ``score`` stay in the shape for the studio but are only informational overlaps: nothing is filtered or chosen by them.

``load_slice`` delegates to the repository (no cache). ``retrieve`` is pure over a loaded slice.
"""

from __future__ import annotations

from typing import Any, Iterable, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.kg_repository import GraphSlice, KgRepository

IMPLEMENTED = True

#: kept for import compatibility with Wave 0; NOT used any more (the POC has no fit bands, see the module docstring)
ADAPT_FIT = 0.6
SIMILAR_FIT = 0.4
#: a pattern matches a request when it covers this many of the request's confirmed concepts (or all of them when there are fewer)
MIN_SHARED_CONCEPTS = 2


class ConceptHit(TypedDict, total=False):
    """A recognised concept as ``kg_understand`` hands it to retrieval."""

    key: str  # "concept:approval"
    label: str
    score: float
    source: str  # lexical | llm | implied
    evidence: str
    confirmed: bool


class Retrieval(TypedDict):
    """The fixed output shape (also ``understanding.retrieval``); every list may be empty."""

    subworkflows: list[dict[str, Any]]  # {key, label, score, why, concepts}
    templates: list[dict[str, Any]]  # {key, label, score, why}
    patterns: list[dict[str, Any]]  # {key, label, fit, validated_count, source_project, adapt}
    apis: list[dict[str, Any]]  # {key, why}
    node_types: list[str]
    roles: list[dict[str, Any]]  # {key, label, used_for, apps}


def empty_retrieval() -> Retrieval:
    return {"subworkflows": [], "templates": [], "patterns": [], "apis": [], "node_types": [], "roles": []}


async def load_slice(session: AsyncSession, project_id: str) -> GraphSlice:
    """The scoped subgraph for ``project_id`` (global nodes, the team's, the project's, org-visible patterns). No cache in the POC
    (one query pair per call); callers must treat the result as read-only."""
    return await KgRepository(session).load_slice(project_id)


def _concept_nodes(slice: GraphSlice, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for k in keys:
        n = slice.by_key(k)
        if n is not None and n["kind"] == "concept":
            out[k] = n
    return out


def _order(items: list[dict[str, Any]], *tiebreak: str) -> list[dict[str, Any]]:
    return sorted(items, key=lambda i: (-i["_n"], -i.get("_validated", 0), *[str(i.get(t) or "") for t in tiebreak]))


def _strip(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def retrieve(slice: GraphSlice, concepts: list[ConceptHit], request: str, *, installed_templates: Iterable[str] = (),
             shape: str = "workflow") -> Retrieval:
    """Pure. What in the slice implements the concepts: callable sub-workflows, templates not yet installed, API operations of this
    project, node types, matching patterns and the roles the concepts need. ``installed_templates`` are template slugs the project
    already has (never offered again); ``shape`` is the request's shape, used to pick the adapt seed. A concept the slice does not
    know is skipped."""
    out = empty_retrieval()
    confirmed = [c["key"] for c in concepts if c.get("confirmed", True) and c.get("key")]
    wanted = list(dict.fromkeys([c["key"] for c in concepts if c.get("key")]))
    nodes = _concept_nodes(slice, wanted)
    if not nodes:
        return out
    installed = set(installed_templates)
    node_types: list[str] = []
    for k, n in nodes.items():
        for sig in (n.get("props") or {}).get("signatures") or []:
            if isinstance(sig, dict) and sig.get("node_type") and sig["node_type"] not in node_types:
                node_types.append(sig["node_type"])

    # who implements what: capability / pattern node id -> concept keys
    implementers: dict[str, set[str]] = {}
    for k, n in nodes.items():
        for e in slice.in_edges(n["id"], "implements"):
            implementers.setdefault(e["src_id"], set()).add(k)

    subs: list[dict[str, Any]] = []
    tpls: list[dict[str, Any]] = []
    apis: list[dict[str, Any]] = []
    pats: list[dict[str, Any]] = []
    for nid, ckeys in implementers.items():
        n = slice.node(nid)
        if n is None:
            continue
        labels = [nodes[c]["label"] for c in sorted(ckeys)]
        why = "implements " + ", ".join(labels)
        base = {"key": n["key"], "label": n["label"], "_n": len(ckeys), "_validated": int(n.get("validated_count") or 0)}
        props = n.get("props") or {}
        if n["kind"] == "pattern":
            shared = ckeys & set(confirmed)
            need = min(MIN_SHARED_CONCEPTS, len(confirmed))
            if len(shared) < max(need, 1):
                continue
            theirs = set(props.get("concepts") or []) | ckeys
            union = theirs | set(confirmed)
            src = (props.get("source") or {}).get("project_label")
            pats.append({**base, "_n": len(shared), "fit": round(len(shared) / len(union), 2) if union else 0.0,
                         "validated_count": int(n.get("validated_count") or 0), "source_project": src, "adapt": False,
                         "_shape": props.get("shape") or "workflow"})
        elif n["kind"] == "capability":
            sub = n.get("subkind")
            if sub in ("subworkflow", "workflow"):
                # the Stage 8 rule: callable, published, and in THIS project (WF027)
                if n["scope"] == "project" and n.get("project_id") == slice.project_id and props.get("callable") and props.get("published_version"):
                    subs.append({**base, "score": round(len(ckeys) / len(wanted), 2), "why": why, "concepts": sorted(ckeys)})
            elif sub == "template":
                if n["key"].split(":", 1)[-1] not in installed:
                    tpls.append({**base, "score": round(len(ckeys) / len(wanted), 2), "why": why})
            elif sub == "api_operation":
                if n["scope"] == "project" and n.get("project_id") == slice.project_id:
                    apis.append({**base, "why": why})
            elif sub == "node_type":
                t = n["key"].split(":", 1)[-1]
                if t not in node_types:
                    node_types.append(t)
    out["subworkflows"] = _strip(_order(subs, "label"))
    out["templates"] = _strip(_order(tpls, "label"))
    out["apis"] = [{"key": a["key"], "why": a["why"]} for a in _order(apis, "key")]
    pats = _order(pats, "label")
    seed = next((p for p in pats if p["_shape"] == shape), None)
    if seed is not None:
        seed["adapt"] = True
    out["patterns"] = _strip(pats)
    out["node_types"] = sorted(node_types)

    # roles the concepts (or the matching patterns) need
    roles: dict[str, dict[str, Any]] = {}
    for k, n in nodes.items():
        for e in slice.out_edges(n["id"], "requires_permission"):
            r = slice.node(e["dst_id"])
            if r is not None:
                roles.setdefault(r["key"], {"key": r["key"], "label": r["label"], "used_for": [], "apps": 0})["used_for"].append(k)
    for r in roles.values():
        rn = slice.by_key(r["key"])
        r["apps"] = len({e["src_id"] for e in slice.in_edges(rn["id"], "requires_permission") if (slice.node(e["src_id"]) or {}).get("kind") in ("pattern", "app")}) if rn else 0
    out["roles"] = sorted(roles.values(), key=lambda r: (-r["apps"], r["key"]))
    return out


def similarity(a: str, b: str) -> float:
    """The embedding seam: lexical today (Jaccard of ``ai_grounding.match_terms`` stems), swappable for cosine later."""
    from .ai_grounding import match_terms

    x, y = set(match_terms(a)), set(match_terms(b))
    return round(len(x & y) / len(x | y), 3) if x and y else 0.0
