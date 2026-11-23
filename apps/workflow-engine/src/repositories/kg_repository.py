"""Persistence of the Semantic Capability Graph (PLAN-CAPABILITY-GRAPH section 1; CONTRACTS-CAPGRAPH.md sections 1-3).

Raw SQL over ``kg_nodes`` / ``kg_edges`` / ``kg_learn_events``, in the style of ``ai_proposal_repository``:

* Methods never commit: the caller owns the transaction (Keep learns inside its own session).
* Rows come back as plain dicts (JSONB decoded, timestamps as aware ``datetime``).
* ``upsert_node`` is keyed by the natural key per scope and is idempotent: writing identical content is reported as
  ``unchanged`` and does not touch ``updated_at`` (the slice cache is keyed by ``max(updated_at)``). A node is only ever
  overwritten by the *same source* that wrote it, so a rebuild (``extracted``) never clobbers ``seed``, ``learned`` or ``human`` nodes.
* ``add_edge`` enforces the integrity rules of CONTRACTS-CAPGRAPH.md section 5 (``EdgeIntegrityError`` carries a ``KGE`` code).
* ``kg_learn_events`` is append-only: there is deliberately no update or delete method for it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

KINDS = ("concept", "capability", "role", "pattern", "app")
SCOPES = ("global", "team", "project")
SOURCES = ("seed", "extracted", "learned", "human", "llm_suggested")
STATUSES = ("active", "stale", "proposed", "rejected")
VISIBILITIES = ("scope", "org")
RELS = (
    "is_a", "requires", "implements", "requires_permission", "composes", "produces",
    "consumes", "escalates_to", "renders_with", "validated_by", "derived_from",
)
LEARN_TRIGGERS = ("seed", "rebuild", "kept_workflow", "kept_app", "human")
CAPABILITY_SUBKINDS = ("node_type", "api_operation", "workflow", "subworkflow", "interface", "screen_component", "template")
CONCEPT_SUBKINDS = ("process", "entity", "control", "artifact")
PATTERN_SUBKINDS = ("workflow", "app")

#: statuses a retrieval slice contains by default (``proposed`` concepts wait for a human; ``rejected`` are gone)
DEFAULT_SLICE_STATUSES = ("active", "stale")


# ── natural keys (CONTRACTS-CAPGRAPH.md section 1; one place so no builder spells a key by hand) ──────────────────────


def concept_key(slug: str) -> str:
    return f"concept:{slug}"


def role_key(slug: str) -> str:
    return f"role:{slug}"


def node_type_key(node_type: str) -> str:
    return f"node:{node_type}"


def api_key(api_ref: str) -> str:
    return f"api:{api_ref}"


def workflow_key(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


def interface_key(workflow_id: str, kind: str) -> str:
    return f"ui:{workflow_id}:{kind}"


def screen_component_key(component: str) -> str:
    return f"ui:component:{component}"


def template_key(slug: str) -> str:
    return f"tpl:{slug}"


def pattern_key(skeleton_hash: str) -> str:
    """``pattern:<sha256(canonical skeleton)[:16]>``; pass the full hex digest or its first 16 characters."""
    return f"pattern:{skeleton_hash[:16]}"


def app_key(app_build_id: str) -> str:
    return f"app:{app_build_id}"


def new_node_id() -> str:
    return "kn_" + uuid.uuid4().hex[:12]


# ── errors ────────────────────────────────────────────────────────────────────────────────────────────────────────────


class EdgeIntegrityError(ValueError):
    """An edge broke an integrity rule. ``code`` is one of KGE001..KGE006."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def check_edge(rel: str, src: dict[str, Any], dst: dict[str, Any]) -> tuple[str, str] | None:
    """The integrity rules of CONTRACTS-CAPGRAPH.md section 5 over two node rows (pure). ``(code, message)`` or None."""
    if rel not in RELS:
        return "KGE005", f"unknown relation {rel!r}"
    if src["id"] == dst["id"]:
        return "KGE005", "an edge cannot point to its own node"
    if rel == "implements" and dst["kind"] != "concept":
        return "KGE001", f"implements must end at a concept, not a {dst['kind']} ({dst['key']})"
    if rel == "requires_permission" and dst["kind"] != "role":
        return "KGE002", f"requires_permission must end at a role, not a {dst['kind']} ({dst['key']})"
    if rel == "escalates_to":
        role_to_role = src["kind"] == "role" and dst["kind"] == "role"
        wf_to_role = (src["kind"] == "capability" and src.get("subkind") in ("workflow", "subworkflow")
                      and dst["kind"] == "role")
        if not (role_to_role or wf_to_role):
            return "KGE003", f"escalates_to runs between roles or from a workflow to a role ({src['key']} -> {dst['key']})"
    return _scope_violation(src, dst)


def _scope_violation(src: dict[str, Any], dst: dict[str, Any]) -> tuple[str, str] | None:
    """KGE004: a project node never points into another project, and nothing points into another team's nodes."""
    ss, ds = src["scope"], dst["scope"]
    if ss == "project" and ds == "project" and src.get("project_id") != dst.get("project_id"):
        return "KGE004", f"{src['key']} and {dst['key']} belong to different projects"
    s_team, d_team = src.get("team_id"), dst.get("team_id")
    if ds != "global" and ss != "global" and s_team and d_team and s_team != d_team:
        return "KGE004", f"{src['key']} and {dst['key']} belong to different teams"
    if ds == "project" and ss != "project" and src["kind"] != "pattern":
        # global and team nodes (ontology, roles, templates) must not depend on one project's private nodes; a learned
        # pattern may cite its evidence (validated_by / derived_from) because load_slice never returns the target to others
        return "KGE004", f"{src['key']} ({ss}) cannot point to the project node {dst['key']}"
    return None


# ── rows ──────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _jload(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def _node(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    out.pop("inserted", None)
    for k in ("aliases", "props"):
        out[k] = _jload(out.get(k)) if out.get(k) is not None else ([] if k == "aliases" else {})
    return out


def _edge(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    out.pop("inserted", None)
    out["props"] = _jload(out.get("props")) or {}
    if out.get("confidence") is not None:
        out["confidence"] = float(out["confidence"])
    return out


def _event(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    out["payload"] = _jload(out.get("payload")) or {}
    return out


@dataclass
class UpsertResult:
    node: dict[str, Any]
    #: created | updated | unchanged | skipped (a node with this key exists but was written by another source)
    action: str


@dataclass
class EdgeResult:
    edge: dict[str, Any]
    action: str  # created | updated | unchanged


@dataclass
class GraphSlice:
    """The scoped subgraph one project can see (CONTRACTS-CAPGRAPH.md section 3). Plain data; ``kg_retrieve`` walks it in Python."""

    #: None for a team-wide slice (``load_team_slice``)
    project_id: str | None
    team_id: str | None
    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    #: ``max(updated_at)`` over the nodes, as an ISO string ('' when empty): the cache key of ``(project_id, version)``
    version: str = ""
    _out: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)
    _in: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)
    _by_key: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for e in self.edges:
            self._out.setdefault(e["src_id"], []).append(e)
            self._in.setdefault(e["dst_id"], []).append(e)
        rank = {"project": 0, "team": 1, "global": 2}
        for n in sorted(self.nodes.values(), key=lambda n: (rank[n["scope"]], n["id"])):
            self._by_key.setdefault(n["key"], []).append(n)

    def node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.get(node_id)

    def by_key(self, key: str) -> dict[str, Any] | None:
        """The node with this natural key; when a project, team and global node share it, the most specific wins."""
        found = self._by_key.get(key)
        return found[0] if found else None

    def of_kind(self, kind: str, subkind: str | None = None) -> list[dict[str, Any]]:
        return [n for n in self.nodes.values() if n["kind"] == kind and (subkind is None or n.get("subkind") == subkind)]

    def out_edges(self, node_id: str, rel: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self._out.get(node_id, []) if rel is None or e["rel"] == rel]

    def in_edges(self, node_id: str, rel: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self._in.get(node_id, []) if rel is None or e["rel"] == rel]


# ── repository ────────────────────────────────────────────────────────────────────────────────────────────────────────


class KgRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── nodes ─────────────────────────────────────────────────────────────────

    async def upsert_node(
        self, *, key: str, kind: str, label: str, source: str, scope: str = "global", team_id: str | None = None,
        project_id: str | None = None, subkind: str | None = None, visibility: str = "scope", summary: str = "",
        aliases: list[str] | None = None, props: dict[str, Any] | None = None, status: str | None = None,
        node_id: str | None = None,
    ) -> UpsertResult:
        """Insert the node, or update the one with the same ``(scope, team, project, key)`` written by the same ``source``.

        ``status`` None keeps the stored status on update (and is ``active`` on insert): re-running an extraction must not
        un-reject a concept a human rejected. Pass ``"active"`` explicitly to revive a ``stale`` node.
        """
        _check_enum("kind", kind, KINDS)
        _check_enum("scope", scope, SCOPES)
        _check_enum("source", source, SOURCES)
        _check_enum("visibility", visibility, VISIBILITIES)
        if status is not None:
            _check_enum("status", status, STATUSES)
        if scope == "global":
            team_id = project_id = None
        elif scope == "team":
            project_id = None
            if not team_id:
                raise ValueError("a team-scoped node needs team_id")
        elif not project_id:
            raise ValueError("a project-scoped node needs project_id")
        else:
            # the unique index includes team_id, so a project node's team is always the project's own: two callers that pass
            # different (or no) team_id for the same project and key must still hit the same row
            team_id = (await self._s.execute(text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id})).scalar() or team_id
        params = {
            "id": node_id or new_node_id(), "key": key, "kind": kind, "subkind": subkind, "scope": scope, "team_id": team_id,
            "project_id": project_id, "visibility": visibility, "label": label, "summary": summary or "",
            "aliases": json.dumps(list(aliases or [])), "props": json.dumps(props or {}, default=str), "source": source,
            "status": status,
        }
        res = await self._s.execute(
            text("""
                INSERT INTO kg_nodes (id, key, kind, subkind, scope, team_id, project_id, visibility, label, summary,
                                      aliases, props, source, status)
                VALUES (:id, :key, :kind, :subkind, :scope, :team_id, :project_id, :visibility, :label, :summary,
                        CAST(:aliases AS jsonb), CAST(:props AS jsonb), :source, COALESCE(CAST(:status AS text), 'active'))
                ON CONFLICT (scope, (coalesce(team_id, '')), (coalesce(project_id, '')), key) DO UPDATE SET
                    kind = EXCLUDED.kind, subkind = EXCLUDED.subkind, visibility = EXCLUDED.visibility,
                    label = EXCLUDED.label, summary = EXCLUDED.summary, aliases = EXCLUDED.aliases, props = EXCLUDED.props,
                    status = COALESCE(CAST(:status AS text), kg_nodes.status), updated_at = now()
                WHERE kg_nodes.source = EXCLUDED.source AND (
                    kg_nodes.kind, kg_nodes.subkind, kg_nodes.visibility, kg_nodes.label, kg_nodes.summary, kg_nodes.aliases,
                    kg_nodes.props, kg_nodes.status
                ) IS DISTINCT FROM (
                    EXCLUDED.kind, EXCLUDED.subkind, EXCLUDED.visibility, EXCLUDED.label, EXCLUDED.summary, EXCLUDED.aliases,
                    EXCLUDED.props, COALESCE(CAST(:status AS text), kg_nodes.status))
                RETURNING *, (xmax = 0) AS inserted
            """),
            params,
        )
        m = res.mappings().first()
        if m is not None:
            node = _node(m)
            return UpsertResult(node, "created" if m["inserted"] else "updated")
        existing = await self.get_node_by_key(key, scope=scope, team_id=team_id, project_id=project_id)
        assert existing is not None  # the conflict row exists, or the insert would have returned
        return UpsertResult(existing, "unchanged" if existing["source"] == source else "skipped")

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        m = (await self._s.execute(text("SELECT * FROM kg_nodes WHERE id = :id"), {"id": node_id})).mappings().first()
        return _node(m) if m else None

    async def get_node_by_key(
        self, key: str, *, scope: str = "global", team_id: str | None = None, project_id: str | None = None,
    ) -> dict[str, Any] | None:
        m = (await self._s.execute(
            text("""SELECT * FROM kg_nodes WHERE scope = :scope AND key = :key
                    AND (:scope <> 'team' OR team_id = :team) AND (:scope <> 'project' OR project_id = :pid)"""),
            {"scope": scope, "key": key, "team": team_id, "pid": project_id},
        )).mappings().first()
        return _node(m) if m else None

    async def set_node_status(
        self, node_id: str, to_status: str, from_statuses: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Conditional status change (accept / reject a proposed concept, revive or stale a node). None when the guard fails."""
        _check_enum("status", to_status, STATUSES)
        sql = "UPDATE kg_nodes SET status = :to, updated_at = now() WHERE id = :id"
        params: dict[str, Any] = {"id": node_id, "to": to_status}
        if from_statuses is not None:
            sql += " AND status = ANY(:from_statuses)"
            params["from_statuses"] = list(from_statuses)
        m = (await self._s.execute(text(sql + " RETURNING *"), params)).mappings().first()
        return _node(m) if m else None

    async def mark_stale(
        self, *, source: str, alive_keys: Iterable[str], project_id: str | None = None, kind: str | None = None,
        subkinds: Iterable[str] | None = None,
    ) -> int:
        """Rebuild support: active nodes of ``source`` (in this project, or global when ``project_id`` is None) whose key is
        not in ``alive_keys`` become ``stale``. Returns how many changed. Other sources are never touched."""
        _check_enum("source", source, SOURCES)
        sql = ("UPDATE kg_nodes SET status = 'stale', updated_at = now() WHERE source = :src AND status = 'active' "
               "AND NOT (key = ANY(:alive))")
        params: dict[str, Any] = {"src": source, "alive": list(alive_keys)}
        if project_id is None:
            sql += " AND scope = 'global'"
        else:
            sql += " AND project_id = :pid"
            params["pid"] = project_id
        if kind is not None:
            sql += " AND kind = :kind"
            params["kind"] = kind
        if subkinds is not None:
            sql += " AND subkind = ANY(:subkinds)"
            params["subkinds"] = list(subkinds)
        return len((await self._s.execute(text(sql + " RETURNING id"), params)).all())

    async def bump_validated(self, node_id: str, *, props_patch: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Atomic ``validated_count + 1`` (never read-modify-write), stamping ``last_validated_at``.
        ``props_patch`` is merged into ``props`` in the same statement (top-level keys, e.g. the grown ``evidence`` list)."""
        m = (await self._s.execute(
            text("""UPDATE kg_nodes SET validated_count = validated_count + 1, last_validated_at = now(), updated_at = now(),
                    props = props || CAST(:patch AS jsonb) WHERE id = :id RETURNING *"""),
            {"id": node_id, "patch": json.dumps(props_patch or {}, default=str)},
        )).mappings().first()
        return _node(m) if m else None

    # ── edges ─────────────────────────────────────────────────────────────────

    async def add_edge(
        self, src_id: str, rel: str, dst_id: str, *, source: str, props: dict[str, Any] | None = None, confidence: float = 1.0,
    ) -> EdgeResult:
        """Insert ``src --rel--> dst`` after the integrity rules; an existing edge is updated when props or confidence differ.

        Raises ``EdgeIntegrityError`` (KGE001..KGE006); KGE006 is a node id that does not exist."""
        _check_enum("source", source, SOURCES)
        rows = (await self._s.execute(
            text("SELECT * FROM kg_nodes WHERE id = ANY(:ids)"), {"ids": [src_id, dst_id]},
        )).mappings().all()
        by_id = {r["id"]: _node(r) for r in rows}
        for nid in (src_id, dst_id):
            if nid not in by_id:
                raise EdgeIntegrityError("KGE006", f"node {nid!r} does not exist")
        bad = check_edge(rel, by_id[src_id], by_id[dst_id])
        if bad:
            raise EdgeIntegrityError(*bad)
        m = (await self._s.execute(
            text("""
                INSERT INTO kg_edges (src_id, rel, dst_id, props, source, confidence)
                VALUES (:src, :rel, :dst, CAST(:props AS jsonb), :source, :conf)
                ON CONFLICT (src_id, rel, dst_id) DO UPDATE SET props = EXCLUDED.props, confidence = EXCLUDED.confidence
                WHERE (kg_edges.props, kg_edges.confidence) IS DISTINCT FROM (EXCLUDED.props, EXCLUDED.confidence)
                RETURNING *, (xmax = 0) AS inserted
            """),
            {"src": src_id, "rel": rel, "dst": dst_id, "props": json.dumps(props or {}, default=str), "source": source,
             "conf": float(confidence)},
        )).mappings().first()
        if m is not None:
            return EdgeResult(_edge(m), "created" if m["inserted"] else "updated")
        cur = (await self._s.execute(
            text("SELECT * FROM kg_edges WHERE src_id = :s AND rel = :r AND dst_id = :d"), {"s": src_id, "r": rel, "d": dst_id},
        )).mappings().one()
        return EdgeResult(_edge(cur), "unchanged")

    async def remove_edges(self, src_id: str, *, rel: str | None = None, source: str | None = None) -> int:
        """Delete the outgoing edges of a node (optionally one relation and/or one source). Returns how many were removed."""
        sql = "DELETE FROM kg_edges WHERE src_id = :s"
        params: dict[str, Any] = {"s": src_id}
        if rel is not None:
            sql += " AND rel = :r"
            params["r"] = rel
        if source is not None:
            sql += " AND source = :src"
            params["src"] = source
        return (await self._s.execute(text(sql), params)).rowcount or 0

    async def neighbours(self, node_id: str) -> list[dict[str, Any]]:
        """Every edge touching the node, in either direction, each with the other node's key/label/kind under ``other``."""
        res = await self._s.execute(
            text("""SELECT e.*, (e.src_id = :id) AS outgoing, n.id AS o_id, n.key AS o_key, n.label AS o_label, n.kind AS o_kind,
                           n.subkind AS o_subkind
                    FROM kg_edges e JOIN kg_nodes n ON n.id = CASE WHEN e.src_id = :id THEN e.dst_id ELSE e.src_id END
                    WHERE e.src_id = :id OR e.dst_id = :id ORDER BY e.id"""),
            {"id": node_id},
        )
        out = []
        for m in res.mappings().all():
            e = _edge({k: v for k, v in m.items() if not k.startswith("o_") and k != "outgoing"})
            e["outgoing"] = bool(m["outgoing"])
            e["other"] = {"id": m["o_id"], "key": m["o_key"], "label": m["o_label"], "kind": m["o_kind"], "subkind": m["o_subkind"]}
            out.append(e)
        return out

    # ── the scoped slice ──────────────────────────────────────────────────────

    async def load_slice(self, project_id: str, *, statuses: Iterable[str] = DEFAULT_SLICE_STATUSES) -> GraphSlice:
        """One query for nodes and one for edges over the scope rule (CONTRACTS-CAPGRAPH.md section 2): global nodes, this team's
        nodes, this project's nodes, and learned patterns visible org-wide. Edges are kept only when both ends are in the slice,
        so other projects' capabilities (and the evidence edges that point at them) never leak through a visible pattern."""
        team_id = (await self._s.execute(text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id})).scalar()
        return await self._scoped_slice(project_id, team_id, [project_id], statuses)

    async def load_team_slice(self, team_id: str, *, statuses: Iterable[str] = DEFAULT_SLICE_STATUSES) -> GraphSlice:
        """The team-wide slice: the same scope rule as ``load_slice`` with every project of ``team_id`` in place of one project
        (global nodes, the team's nodes, the project nodes of each project of the team, org-visible patterns). ``project_id`` is
        None on the returned slice, so nothing that is bound to one project (callable sub-workflows, API operations) is offered."""
        pids = [p["id"] for p in await self.team_projects(team_id)]
        return await self._scoped_slice(None, team_id, pids, statuses)

    async def team_projects(self, team_id: str) -> list[dict[str, Any]]:
        """``[{id, slug, name}]`` of the team's projects, by name."""
        res = await self._s.execute(
            text("SELECT id, slug, name FROM projects WHERE team_id = :t ORDER BY lower(name), id"), {"t": team_id},
        )
        return [dict(m) for m in res.mappings().all()]

    async def _scoped_slice(
        self, project_id: str | None, team_id: str | None, project_ids: list[str], statuses: Iterable[str],
    ) -> GraphSlice:
        """THE scope rule, in one place. ``project_ids`` is ``[project_id]`` for a project slice, the team's projects for a team slice."""
        rows = (await self._s.execute(
            text("""SELECT * FROM kg_nodes WHERE status = ANY(:st) AND (
                        scope = 'global'
                        OR (scope = 'team' AND team_id = :team)
                        OR (scope = 'project' AND project_id = ANY(:pids))
                        OR (kind = 'pattern' AND visibility = 'org'))"""),
            {"st": list(statuses), "team": team_id, "pids": list(project_ids)},
        )).mappings().all()
        nodes = {r["id"]: _node(r) for r in rows}
        edges: list[dict[str, Any]] = []
        if nodes:
            res = await self._s.execute(
                text("SELECT * FROM kg_edges WHERE src_id = ANY(:ids) AND dst_id = ANY(:ids) ORDER BY id"), {"ids": list(nodes)},
            )
            edges = [_edge(m) for m in res.mappings().all()]
        newest = max((n["updated_at"] for n in nodes.values()), default=None)
        return GraphSlice(project_id=project_id, team_id=team_id, nodes=nodes, edges=edges,
                          version=newest.isoformat() if isinstance(newest, datetime) else "")

    # ── learn events (append-only) ────────────────────────────────────────────

    async def add_learn_event(
        self, *, trigger: str, summary: str, project_id: str | None = None, team_id: str | None = None,
        actor: str | None = None, proposal_id: str | None = None, payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _check_enum("trigger", trigger, LEARN_TRIGGERS)
        m = (await self._s.execute(
            text("""INSERT INTO kg_learn_events (project_id, team_id, actor, trigger, proposal_id, summary, payload)
                    VALUES (:p, :t, :a, :trg, :pid, :s, CAST(:pl AS jsonb)) RETURNING *"""),
            {"p": project_id, "t": team_id, "a": actor, "trg": trigger, "pid": proposal_id, "s": summary,
             "pl": json.dumps(payload or {}, default=str)},
        )).mappings().one()
        return _event(m)

    async def list_learn_events(
        self, project_id: str | None = None, *, limit: int = 50, before_id: int | None = None, team_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Newest first; ``(events, next_before_id)``. ``project_id`` None lists the whole org (global seed events included), or,
        with ``team_id``, the events of the team's projects, the team's own and the global ones (``project_id`` wins when both)."""
        limit = max(1, min(int(limit), 200))
        where, params = ["TRUE"], {"lim": limit + 1}
        if project_id is not None:
            where.append("project_id = :p")
            params["p"] = project_id
        elif team_id is not None:
            where.append("(project_id IN (SELECT id FROM projects WHERE team_id = :t)"
                         " OR (project_id IS NULL AND (team_id IS NULL OR team_id = :t)))")
            params["t"] = team_id
        if before_id is not None:
            where.append("id < :b")
            params["b"] = int(before_id)
        res = await self._s.execute(
            text(f"SELECT * FROM kg_learn_events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :lim"), params,
        )
        rows = [_event(m) for m in res.mappings().all()]
        nxt = None
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = rows[-1]["id"]
        return rows, nxt


def _check_enum(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {name} {value!r}; expected one of {allowed}")
