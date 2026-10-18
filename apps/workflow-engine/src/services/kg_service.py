"""The logic behind the Knowledge API (PLAN-CAPABILITY-GRAPH section 6.1 and 7; CONTRACTS-CAPGRAPH.md section 7).

Every read goes through ``KgRepository.load_slice(project_id)``, so what a project sees is exactly the scope rule of section 1.2:
global nodes, its team's, its own, and org-visible patterns. A node outside the slice is a 404 (it hides that it exists).
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.kg_repository import GraphSlice, KgRepository

MAX_NODES = 400
GRAPH_STATUSES = ("active", "stale", "proposed")
_KIND_RANK = {"pattern": 0, "app": 1, "concept": 2, "role": 3, "capability": 4}
_HEAVY_PROPS = ("skeleton", "reference_bundle")


class KnowledgeError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message}


def not_found(what: str = "node") -> KnowledgeError:
    return KnowledgeError(404, "NOT_FOUND", f"{what} not found")


# ── views ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def node_view(n: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    props = n.get("props") or {}
    if not full:
        props = {k: v for k, v in props.items() if k not in _HEAVY_PROPS}
    return {k: n.get(k) for k in ("id", "key", "kind", "subkind", "scope", "visibility", "team_id", "project_id", "label", "summary",
                                  "aliases", "source", "status", "validated_count", "last_validated_at", "updated_at")} | {"props": props}


def edge_view(e: dict[str, Any]) -> dict[str, Any]:
    return {k: e[k] for k in ("id", "src_id", "rel", "dst_id", "props", "source")}


def node_ref(n: dict[str, Any]) -> dict[str, Any]:
    return {k: n.get(k) for k in ("id", "key", "label", "kind", "subkind")}


async def _slice(session: AsyncSession, project_id: str, statuses: Iterable[str] = GRAPH_STATUSES) -> GraphSlice:
    return await KgRepository(session).load_slice(project_id, statuses=tuple(statuses))


# ── graph ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def graph(session: AsyncSession, project_id: str, *, kinds: list[str] | None = None, focus: str | None = None, depth: int = 1,
                learned_only: bool = False) -> dict[str, Any]:
    sl = await _slice(session, project_id)
    dist: dict[str, int] = {}
    if focus:
        if sl.node(focus) is None:
            raise not_found()
        depth = 2 if depth >= 2 else 1
        dist[focus] = 0
        queue = deque([focus])
        while queue:
            cur = queue.popleft()
            if dist[cur] >= depth:
                continue
            for e in sl.out_edges(cur) + sl.in_edges(cur):
                other = e["dst_id"] if e["src_id"] == cur else e["src_id"]
                if other not in dist:
                    dist[other] = dist[cur] + 1
                    queue.append(other)
        pool = [sl.nodes[i] for i in dist]
    else:
        pool = list(sl.nodes.values())
    if learned_only:
        learned = {n["id"] for n in pool if n["source"] == "learned"}   # written by a Keep (seed template patterns are not)
        keep = set(learned)
        for e in sl.edges:
            if e["src_id"] in learned:
                keep.add(e["dst_id"])
            if e["dst_id"] in learned:
                keep.add(e["src_id"])
        pool = [n for n in pool if n["id"] in keep]
    if kinds:
        pool = [n for n in pool if n["kind"] in kinds or n["id"] == focus]
    pool.sort(key=lambda n: (dist.get(n["id"], 0), _KIND_RANK.get(n["kind"], 9), n["label"].lower(), n["id"]))
    truncated = len(pool) > MAX_NODES
    pool = pool[:MAX_NODES]
    ids = {n["id"] for n in pool}
    return {"nodes": [node_view(n) for n in pool], "truncated": truncated,
            "edges": [edge_view(e) for e in sl.edges if e["src_id"] in ids and e["dst_id"] in ids]}


# ── node detail ───────────────────────────────────────────────────────────────────────────────────────────────────────


async def node_detail(session: AsyncSession, project_id: str, node_id: str) -> dict[str, Any]:
    sl = await _slice(session, project_id, statuses=("active", "stale", "proposed", "rejected"))
    n = sl.node(node_id)
    if n is None:
        raise not_found()
    neighbours: dict[str, list[dict[str, Any]]] = {}
    for e in sl.out_edges(node_id):
        other = sl.node(e["dst_id"])
        if other:
            neighbours.setdefault(e["rel"], []).append({"direction": "out", "node": node_ref(other), "props": e["props"], "source": e["source"]})
    for e in sl.in_edges(node_id):
        other = sl.node(e["src_id"])
        if other:
            neighbours.setdefault(e["rel"], []).append({"direction": "in", "node": node_ref(other), "props": e["props"], "source": e["source"]})
    evidence = list((n.get("props") or {}).get("evidence") or [])
    if not evidence:
        evidence = [{"node": ne["node"]["key"], "tests": ne["props"].get("tests")} for ne in neighbours.get("validated_by", [])]
    events, _ = await KgRepository(session).list_learn_events(project_id, limit=200)
    refs = {e.get("ref") for e in evidence if isinstance(e, dict)}
    mine = [ev for ev in events if ev["payload"].get("pattern_key") == n["key"] or ev.get("proposal_id") in refs
            or n["key"] in (ev["payload"].get("concepts") or [])]
    return {"node": node_view(n, full=True), "neighbours": neighbours, "evidence": evidence, "learn_events": mine[:50]}


# ── capability matrix ─────────────────────────────────────────────────────────────────────────────────────────────────


async def capabilities(session: AsyncSession, project_id: str) -> dict[str, Any]:
    sl = await _slice(session, project_id, statuses=("active", "stale"))
    rows: list[dict[str, Any]] = []
    for c in sorted(sl.of_kind("concept"), key=lambda n: n["label"].lower()):
        by_sub: dict[str, int] = {}
        patterns = validated = apps = 0
        teams: set[str] = set()
        roles: dict[str, dict[str, Any]] = {}
        implementers = [sl.node(e["src_id"]) for e in sl.in_edges(c["id"], "implements")]
        for i in (x for x in implementers if x):
            if i["kind"] == "capability":
                by_sub[i["subkind"] or "other"] = by_sub.get(i["subkind"] or "other", 0) + 1
            elif i["kind"] == "pattern" and i["validated_count"] > 0:
                patterns += 1
                validated += i["validated_count"]
                teams |= {str(ev.get("project_label")) for ev in (i["props"] or {}).get("evidence") or [] if ev.get("project_label")}
            elif i["kind"] == "app":
                apps += 1
            for e in sl.out_edges(i["id"], "requires_permission"):
                r = sl.node(e["dst_id"])
                if r:
                    roles[r["id"]] = node_ref(r)
        for e in sl.out_edges(c["id"], "requires_permission"):
            r = sl.node(e["dst_id"])
            if r:
                roles[r["id"]] = node_ref(r)
        total = sum(by_sub.values()) + patterns + apps
        rows.append({"concept": node_ref(c), "implemented_by": by_sub, "implemented_total": total, "patterns": patterns,
                     "validated": validated, "apps": apps, "teams": sorted(teams), "roles": sorted(roles.values(), key=lambda r: r["label"]),
                     "gap": total == 0})
    pats = [n for n in sl.of_kind("pattern") if n["validated_count"] > 0]
    totals = {"concepts": len(rows), "implemented": sum(1 for r in rows if not r["gap"]), "gaps": sum(1 for r in rows if r["gap"]),
              "patterns": len(pats), "validated": sum(n["validated_count"] for n in pats), "apps": len(sl.of_kind("app")),
              "subworkflows": len([n for n in sl.of_kind("capability", "subworkflow") if n["scope"] == "project"])}
    return {"rows": rows, "totals": totals}


# ── memory ────────────────────────────────────────────────────────────────────────────────────────────────────────────


async def memory(session: AsyncSession, project_id: str, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    try:
        before = int(cursor) if cursor else None
    except ValueError:
        raise KnowledgeError(422, "INVALID_CURSOR", "cursor must be a number") from None
    items, nxt = await KgRepository(session).list_learn_events(project_id, limit=limit, before_id=before)
    return {"items": items, "next_cursor": str(nxt) if nxt is not None else None}


# ── understand (lexical only) ─────────────────────────────────────────────────────────────────────────────────────────


async def understand(session: AsyncSession, project: Any, request: str) -> dict[str, Any]:
    """Never an LLM call and never a row written: the live chips under the request box."""
    from ..engine import ai_policy
    from . import kg_understand

    clean, _ = ai_policy.scrub_request(request)
    sl = await _slice(session, project.id, statuses=("active", "stale"))
    ctx = kg_understand.UnderstandContext(project_id=project.id, team_id=project.team_id, slice=sl)
    return dict(await kg_understand.understand(ctx, clean, use_llm=False))


# ── rebuild and decisions ─────────────────────────────────────────────────────────────────────────────────────────────


async def rebuild(session: AsyncSession, project_id: str, llm_tagging: bool) -> dict[str, Any]:
    from . import kg_extract

    return (await kg_extract.rebuild(session, project_id, llm_tagging=llm_tagging)).dict()


async def decide(session: AsyncSession, project_id: str | None, node_id: str, to_status: str) -> dict[str, Any]:
    """Accept (``active``) or reject (``rejected``) a proposed concept: one status flip, nothing else."""
    repo = KgRepository(session)
    n = await repo.get_node(node_id)
    if n is None or (n["scope"] == "project" and n["project_id"] != project_id):
        raise not_found()
    if n["scope"] == "team" and project_id is None:
        raise not_found()
    if n["kind"] != "concept" or n["status"] != "proposed":
        raise KnowledgeError(409, "NOT_PROPOSED", "only a proposed concept can be accepted or rejected")
    flipped = await repo.set_node_status(node_id, to_status, ["proposed"])
    if flipped is None:
        raise KnowledgeError(409, "NOT_PROPOSED", "only a proposed concept can be accepted or rejected")
    await session.commit()
    return node_view(flipped)
