"""Helpers of the capability-graph unit tests (WP-B): a seeded in-memory ``GraphSlice`` built from the real seed ontology, and small builders
for the capability / pattern nodes retrieval looks at. Not a test module."""

from __future__ import annotations

import itertools
from typing import Any

from src.repositories.kg_repository import GraphSlice
from src.services import kg_ontology

PROJECT = "proj_a"
_ids = itertools.count(1)


def node(key: str, kind: str, label: str, *, subkind: str | None = None, scope: str = "global", project_id: str | None = None, props: dict | None = None,
         aliases: list | None = None, validated_count: int = 0, visibility: str = "scope", status: str = "active", source: str = "seed") -> dict[str, Any]:
    return {"id": f"kn_{next(_ids):012d}", "key": key, "kind": kind, "subkind": subkind, "scope": scope, "team_id": None, "project_id": project_id,
            "visibility": visibility, "label": label, "summary": "", "aliases": aliases or [], "props": props or {}, "source": source, "status": status,
            "validated_count": validated_count, "last_validated_at": None}


def edge(src: dict, rel: str, dst: dict, **props: Any) -> dict[str, Any]:
    return {"id": next(_ids), "src_id": src["id"], "rel": rel, "dst_id": dst["id"], "props": props, "source": "seed", "confidence": 1.0}


def seeded(project_id: str = PROJECT, *, with_node_types: bool = True) -> tuple[dict[str, dict], list[dict], dict[str, dict]]:
    """``(nodes by id, edges, nodes by key)`` of the seed ontology, plus node-type capabilities with ``implements`` edges from the signatures
    (what ``kg_extract`` writes in a seeded database)."""
    ont = kg_ontology.load()
    nodes: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for row in kg_ontology.node_rows(ont):
        n = node(row["key"], row["kind"], row["label"], subkind=row.get("subkind"), props=row["props"], aliases=row["aliases"])
        nodes[n["id"]] = n
        by_key[n["key"]] = n
    edges = [edge(by_key[e["src"]], e["rel"], by_key[e["dst"]]) for e in kg_ontology.edge_rows(ont) if e["dst"] in by_key]
    if with_node_types:
        for c in ont.concepts:
            for sig in c.get("signatures") or []:
                t = sig["node_type"]
                if f"node:{t}" not in by_key:
                    n = node(f"node:{t}", "capability", t, subkind="node_type")
                    nodes[n["id"]] = n
                    by_key[n["key"]] = n
                edges.append(edge(by_key[f"node:{t}"], "implements", by_key[c["key"]]))
    return nodes, edges, by_key


def make_slice(project_id: str = PROJECT, extra: list[dict] | None = None, extra_edges: list[tuple[dict, str, Any]] | None = None) -> GraphSlice:
    """``extra_edges`` are ``(src node, rel, dst)`` where ``dst`` is a node or a natural key of a node in this slice (``"concept:report"``)."""
    nodes, edges, by_key = seeded(project_id)
    for n in extra or []:
        nodes[n["id"]] = n
        by_key[n["key"]] = n
    for src, rel, dst in extra_edges or []:
        edges.append(edge(src, rel, by_key[dst] if isinstance(dst, str) else dst))
    return GraphSlice(project_id=project_id, team_id="t1", nodes=nodes, edges=edges, version="v1")


def concept_node(sl: GraphSlice, key: str) -> dict[str, Any]:
    n = sl.by_key(key)
    assert n is not None, key
    return n


def subworkflow(wf_id: str, label: str, *, project_id: str = PROJECT, callable_: bool = True, published: int | None = 1) -> dict[str, Any]:
    return node(f"wf:{wf_id}", "capability", label, subkind="subworkflow", scope="project", project_id=project_id, source="extracted",
                props={"callable": callable_, "published_version": published, "inputs": [{"name": "category", "type": "string", "required": True}],
                       "output_fields": ["owner_role"]})


def pattern(key: str, label: str, concepts: list[str], *, shape: str = "workflow", validated: int = 1, slots: dict | None = None,
            reference: dict | None = None, project_label: str = "Payments Ops") -> dict[str, Any]:
    props = {"shape": shape, "concepts": concepts, "slots": slots or {}, "skeleton": {}, "reference_bundle": reference or {},
             "source": {"project_label": project_label}, "evidence": []}
    return node(key, "pattern", label, subkind=shape, props=props, validated_count=validated, visibility="org", source="learned")
