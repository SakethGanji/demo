"""The seed ontology: load, validate, and upsert it into the graph (PLAN-CAPABILITY-GRAPH section 2.1; CONTRACTS-CAPGRAPH.md section 4).

``knowledge/ontology/bank_ops.yaml`` is a reviewed file. ``load()`` parses and validates it (pure, no database) and raises
``OntologyInvalid`` with every issue found; ``seed()`` then upserts the nodes and edges with ``source='seed'``. Seeding is
idempotent: a second run reports everything ``unchanged`` and does not touch ``updated_at``.

Validation codes (each one is a ``KGO`` issue):

========  ==============================================================================================================
KGO001    the file is not valid YAML, or the root is not ``{version, domain, roles, concepts}``
KGO002    duplicate key
KGO003    a concept misses a required field, or has an unknown ``kind`` or a malformed key
KGO004    unknown slot ``type``
KGO005    a relation target does not exist (``is_a`` / ``requires`` / ``produces`` / ``consumes``) or is malformed
KGO006    alias not lowercase, empty, or duplicated across concepts (or roles) without being declared ``shared_aliases``
KGO007    a slot is malformed (name, ``required`` without a question, ``choice`` without options, bad ``alias_of``)
KGO008    a signature is malformed (unknown key, missing ``node_type``, or an unknown node type when a catalog is given)
KGO009    a ``part_recipe`` is malformed
KGO010    a role is malformed
KGO011    an ``is_a`` cycle
========  ==============================================================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "ontology" / "bank_ops.yaml"

SLOT_TYPES = ("role", "duration", "cron", "field", "choice", "text", "api_capability")
CONCEPT_KINDS = ("process", "entity", "control", "artifact")
CONCEPT_RELATIONS = ("is_a", "requires", "produces", "consumes")
SIGNATURE_KEYS = frozenset({"node_type", "params", "ports", "follows", "writes_table_with"})
RECIPE_KEYS = frozenset({"parts", "tables", "reuse_template"})
RECIPE_ROLES = ("entry", "subworkflow")
RECIPE_TRIGGERS = ("form", "cron", "webhook", "chat", "execute", "manual")

_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
_CONCEPT_KEY = re.compile(r"^concept:[a-z][a-z0-9_]*$")
_ROLE_KEY = re.compile(r"^role:[a-z][a-z0-9_]*$")
_COMPONENT_KEY = re.compile(r"^ui:component:[a-z][a-z0-9_]*$")
_ALIAS_OF = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    where: str = ""

    def __str__(self) -> str:
        return f"{self.code} {self.where}: {self.message}" if self.where else f"{self.code}: {self.message}"


class OntologyInvalid(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("invalid ontology:\n" + "\n".join(f"  {i}" for i in issues))
        self.issues = issues


@dataclass
class Ontology:
    version: int
    domain: str
    concepts: list[dict[str, Any]]
    roles: list[dict[str, Any]]
    path: str = ""

    def concept(self, key: str) -> dict[str, Any] | None:
        return next((c for c in self.concepts if c["key"] == key), None)

    def role(self, key: str) -> dict[str, Any] | None:
        return next((r for r in self.roles if r["key"] == key), None)


@dataclass
class SeedReport:
    nodes: dict[str, int] = field(default_factory=lambda: {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0})
    edges: dict[str, int] = field(default_factory=lambda: {"created": 0, "updated": 0, "unchanged": 0})
    #: edges whose target is not in the graph yet (``renders_with`` -> ``ui:component:*`` is written by extraction)
    deferred_edges: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.nodes["created"] or self.nodes["updated"] or self.edges["created"] or self.edges["updated"])


def short_name(concept_key: str) -> str:
    """``concept:approval`` -> ``approval`` (the prefix of slot ids such as ``approval.approver_role``)."""
    return concept_key.split(":", 1)[1]


# ── validation (pure) ─────────────────────────────────────────────────────────────────────────────────────────────────


def validate(raw: Any, *, node_types: Iterable[str] | None = None) -> list[Issue]:
    """Every issue in a parsed ontology document (an empty list means valid). ``node_types``, when given, is the node catalog's
    type names and makes an unknown signature ``node_type`` a KGO008."""
    issues: list[Issue] = []
    if not isinstance(raw, dict):
        return [Issue("KGO001", "the root must be a mapping with version, domain, roles and concepts")]
    for k in ("version", "domain", "roles", "concepts"):
        if k not in raw:
            issues.append(Issue("KGO001", f"missing top-level key {k!r}"))
    if issues:
        return issues
    if not isinstance(raw["roles"], list) or not isinstance(raw["concepts"], list):
        return [Issue("KGO001", "roles and concepts must be lists")]
    known_types = set(node_types) if node_types is not None else None

    _validate_roles(raw["roles"], issues)
    concepts = [c for c in raw["concepts"]]
    keys: list[str] = []
    for c in concepts:
        if not isinstance(c, dict) or not isinstance(c.get("key"), str):
            issues.append(Issue("KGO003", "a concept needs a string key"))
            continue
        if c["key"] in keys:
            issues.append(Issue("KGO002", "duplicate concept key", c["key"]))
        keys.append(c["key"])
    key_set = set(keys)
    slots_by_concept: dict[str, dict[str, dict[str, Any]]] = {}
    for c in concepts:
        if isinstance(c, dict) and isinstance(c.get("key"), str):
            slots_by_concept[short_name(c["key"]) if ":" in c["key"] else c["key"]] = {
                s["name"]: s for s in (c.get("slots") or []) if isinstance(s, dict) and isinstance(s.get("name"), str)}
    for c in concepts:
        if isinstance(c, dict) and isinstance(c.get("key"), str):
            _validate_concept(c, key_set, slots_by_concept, known_types, issues)
    _validate_aliases([c for c in concepts if isinstance(c, dict)], "concept", issues)
    _validate_aliases([r for r in raw["roles"] if isinstance(r, dict)], "role", issues)
    _check_is_a_cycles([c for c in concepts if isinstance(c, dict) and isinstance(c.get("key"), str)], issues)
    return issues


def _validate_roles(roles: list[Any], issues: list[Issue]) -> set[str]:
    seen: set[str] = set()
    for r in roles:
        if not isinstance(r, dict) or not isinstance(r.get("key"), str) or not _ROLE_KEY.match(r["key"]):
            issues.append(Issue("KGO010", "a role needs a key like role:ops_head", str(r.get("key") if isinstance(r, dict) else r)))
            continue
        if r["key"] in seen:
            issues.append(Issue("KGO002", "duplicate role key", r["key"]))
        seen.add(r["key"])
        if not isinstance(r.get("label"), str) or not r["label"].strip():
            issues.append(Issue("KGO010", "a role needs a label", r["key"]))
        if not isinstance(r.get("aliases"), list) or not r["aliases"]:
            issues.append(Issue("KGO010", "a role needs at least one alias", r["key"]))
    return seen


def _validate_concept(c: dict[str, Any], keys: set[str], slots_by_concept: dict[str, dict[str, dict[str, Any]]],
                      known_types: set[str] | None, issues: list[Issue]) -> None:
    key = c["key"]
    if not _CONCEPT_KEY.match(key):
        issues.append(Issue("KGO003", "key must look like concept:<slug>", key))
        return
    for f in ("label", "definition", "prompt_hint"):
        if not isinstance(c.get(f), str) or not c[f].strip():
            issues.append(Issue("KGO003", f"{f} is required", key))
    if c.get("kind") not in CONCEPT_KINDS:
        issues.append(Issue("KGO003", f"kind must be one of {CONCEPT_KINDS}, got {c.get('kind')!r}", key))
    if not isinstance(c.get("aliases"), list) or not c["aliases"]:
        issues.append(Issue("KGO006", "a concept needs at least one alias", key))
    obligations = c.get("test_obligations")
    if not isinstance(obligations, list) or not obligations or not all(isinstance(o, str) and o.strip() for o in obligations):
        issues.append(Issue("KGO003", "test_obligations must be a non-empty list of strings", key))
    for rel in CONCEPT_RELATIONS:
        for target in c.get(rel) or []:
            if not isinstance(target, str) or target not in keys:
                issues.append(Issue("KGO005", f"{rel} target {target!r} is not a concept in this file", key))
            elif target == key:
                issues.append(Issue("KGO005", f"{rel} cannot point to itself", key))
    for target in c.get("renders_with") or []:
        if not isinstance(target, str) or not _COMPONENT_KEY.match(target):
            issues.append(Issue("KGO005", f"renders_with target {target!r} must look like ui:component:<name>", key))
    for s in c.get("slots") or []:
        _validate_slot(key, s, slots_by_concept, issues)
    for sig in c.get("signatures") or []:
        _validate_signature(key, sig, known_types, issues)
    recipe = c.get("part_recipe")
    if recipe is not None:
        _validate_recipe(key, recipe, issues)
    if "shared_aliases" in c:
        sa, al = c["shared_aliases"], c.get("aliases") or []
        if not isinstance(sa, list) or any(a not in al for a in sa):
            issues.append(Issue("KGO006", "shared_aliases must be a subset of aliases", key))


def _validate_slot(ckey: str, s: Any, slots_by_concept: dict[str, dict[str, dict[str, Any]]], issues: list[Issue]) -> None:
    where = f"{ckey}"
    if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not _SLUG.match(s["name"]):
        issues.append(Issue("KGO007", "a slot needs a snake_case name", where))
        return
    where = f"{ckey}.{s['name']}"
    if s.get("type") not in SLOT_TYPES:
        issues.append(Issue("KGO004", f"unknown slot type {s.get('type')!r}; expected one of {SLOT_TYPES}", where))
    if not isinstance(s.get("required"), bool):
        issues.append(Issue("KGO007", "required must be true or false", where))
    if s.get("required") is True and "alias_of" not in s and not (isinstance(s.get("question"), str) and s["question"].strip()):
        issues.append(Issue("KGO007", "a required slot needs a question (unless it is an alias_of another slot)", where))
    if "multiple" in s and not isinstance(s["multiple"], bool):
        issues.append(Issue("KGO007", "multiple must be true or false", where))
    if s.get("type") == "choice":
        opts = s.get("options")
        if not isinstance(opts, list) or not opts or not all(isinstance(o, str) for o in opts):
            issues.append(Issue("KGO007", "a choice slot needs a list of string options", where))
        elif "default" in s and s["default"] not in opts:
            issues.append(Issue("KGO007", "the default of a choice slot must be one of its options", where))
    if "alias_of" in s:
        m = _ALIAS_OF.match(str(s["alias_of"]))
        target = slots_by_concept.get(m.group(1), {}).get(m.group(2)) if m else None
        if not m or target is None:
            issues.append(Issue("KGO007", f"alias_of {s['alias_of']!r} must name an existing <concept>.<slot>", where))
        else:
            if "alias_of" in target:
                issues.append(Issue("KGO007", "alias_of must point to the original slot, not to another alias", where))
            if target.get("type") != s.get("type"):
                issues.append(Issue("KGO007", "an alias slot must have the same type as the slot it aliases", where))


def _validate_signature(ckey: str, sig: Any, known_types: set[str] | None, issues: list[Issue]) -> None:
    if not isinstance(sig, dict) or not isinstance(sig.get("node_type"), str):
        issues.append(Issue("KGO008", "a signature needs a node_type", ckey))
        return
    extra = set(sig) - SIGNATURE_KEYS
    if extra:
        issues.append(Issue("KGO008", f"unknown signature keys {sorted(extra)}", ckey))
    for k in ("params", "ports", "follows", "writes_table_with"):
        if k in sig and (not isinstance(sig[k], list) or not all(isinstance(x, str) for x in sig[k])):
            issues.append(Issue("KGO008", f"{k} must be a list of strings", ckey))
    if known_types is not None:
        for t in [sig["node_type"], *[x for x in sig.get("follows", []) if isinstance(x, str)]]:
            if t not in known_types:
                issues.append(Issue("KGO008", f"unknown node type {t!r}", ckey))


def _validate_recipe(ckey: str, recipe: Any, issues: list[Issue]) -> None:
    if not isinstance(recipe, dict) or set(recipe) - RECIPE_KEYS:
        issues.append(Issue("KGO009", f"part_recipe must be null or a mapping with keys from {sorted(RECIPE_KEYS)}", ckey))
        return
    keys: set[str] = set()
    for p in recipe.get("parts") or []:
        if not isinstance(p, dict) or not _SLUG.match(str(p.get("key", ""))):
            issues.append(Issue("KGO009", "a recipe part needs a slug key", ckey))
            continue
        if p["key"] in keys:
            issues.append(Issue("KGO009", f"duplicate recipe part key {p['key']!r}", ckey))
        keys.add(p["key"])
        if p.get("role") not in RECIPE_ROLES:
            issues.append(Issue("KGO009", f"part {p['key']!r}: role must be one of {RECIPE_ROLES}", ckey))
        if "trigger" in p and p["trigger"] not in RECIPE_TRIGGERS:
            issues.append(Issue("KGO009", f"part {p['key']!r}: trigger must be one of {RECIPE_TRIGGERS}", ckey))
    for t in recipe.get("tables") or []:
        if not isinstance(t, dict) or not _SLUG.match(str(t.get("key", ""))) or not isinstance(t.get("columns"), list) or not t["columns"]:
            issues.append(Issue("KGO009", "a recipe table needs a slug key and a list of columns", ckey))
    if "reuse_template" in recipe and not isinstance(recipe["reuse_template"], str):
        issues.append(Issue("KGO009", "reuse_template must be a template slug", ckey))


def _validate_aliases(items: list[dict[str, Any]], noun: str, issues: list[Issue]) -> None:
    users: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        key = str(it.get("key", "?"))
        for a in it.get("aliases") or []:
            if not isinstance(a, str) or not a.strip() or a != a.strip().lower():
                issues.append(Issue("KGO006", f"alias {a!r} must be non-empty and lowercase", key))
                continue
            users.setdefault(a, []).append(it)
    for alias, owners in users.items():
        if len(owners) < 2:
            continue
        if all(isinstance(o.get("shared_aliases"), list) and alias in o["shared_aliases"] for o in owners):
            continue
        issues.append(Issue("KGO006", f"{noun} alias {alias!r} is used by {sorted(str(o.get('key')) for o in owners)}; "
                                       "declare it under shared_aliases on each, or make it unique"))


def _check_is_a_cycles(concepts: list[dict[str, Any]], issues: list[Issue]) -> None:
    parents = {c["key"]: [p for p in (c.get("is_a") or []) if isinstance(p, str)] for c in concepts}
    for start in parents:
        seen, stack = set(), list(parents[start])
        while stack:
            n = stack.pop()
            if n == start:
                issues.append(Issue("KGO011", "is_a cycle", start))
                break
            if n in seen:
                continue
            seen.add(n)
            stack.extend(parents.get(n, []))


# ── load ──────────────────────────────────────────────────────────────────────────────────────────────────────────────


def load(path: str | Path | None = None, *, node_types: Iterable[str] | None = None) -> Ontology:
    """Parse and validate the ontology file (default: ``knowledge/ontology/bank_ops.yaml``). Raises ``OntologyInvalid``."""
    p = Path(path) if path else DEFAULT_PATH
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise OntologyInvalid([Issue("KGO001", f"cannot read {p}: {e}")]) from e
    return from_dict(raw, path=str(p), node_types=node_types)


def from_dict(raw: Any, *, path: str = "", node_types: Iterable[str] | None = None) -> Ontology:
    issues = validate(raw, node_types=node_types)
    if issues:
        raise OntologyInvalid(issues)
    return Ontology(version=int(raw["version"]), domain=str(raw["domain"]), concepts=list(raw["concepts"]),
                    roles=list(raw["roles"]), path=path)


# ── graph rows (pure) ─────────────────────────────────────────────────────────────────────────────────────────────────


def concept_props(c: dict[str, Any]) -> dict[str, Any]:
    """The frozen concept ``props`` shape (CONTRACTS-CAPGRAPH.md section 4): definition, kind, slots, signatures,
    test_obligations, prompt_hint, part_recipe."""
    props: dict[str, Any] = {
        "definition": c["definition"].strip(),
        "kind": c["kind"],
        "slots": [dict(s) for s in c.get("slots") or []],
        "signatures": [dict(s) for s in c.get("signatures") or []],
        "test_obligations": list(c.get("test_obligations") or []),
        "prompt_hint": c["prompt_hint"].strip(),
        "part_recipe": c.get("part_recipe"),
    }
    if c.get("shared_aliases"):
        props["shared_aliases"] = list(c["shared_aliases"])
    return props


def node_rows(ont: Ontology) -> list[dict[str, Any]]:
    """``KgRepository.upsert_node`` keyword arguments for every role and concept (roles first)."""
    rows: list[dict[str, Any]] = []
    for r in ont.roles:
        rows.append({"key": r["key"], "kind": "role", "label": r["label"], "summary": r.get("summary", r["label"]),
                     "aliases": list(r["aliases"]), "props": {}, "source": "seed", "scope": "global"})
    for c in ont.concepts:
        rows.append({"key": c["key"], "kind": "concept", "subkind": c["kind"], "label": c["label"],
                     "summary": c["definition"].strip(), "aliases": list(c["aliases"]), "props": concept_props(c),
                     "source": "seed", "scope": "global"})
    return rows


def edge_rows(ont: Ontology) -> list[dict[str, str]]:
    """``{src, rel, dst}`` by natural key for every is_a / requires / produces / consumes / renders_with relation."""
    out: list[dict[str, str]] = []
    for c in ont.concepts:
        for rel in (*CONCEPT_RELATIONS, "renders_with"):
            for target in c.get(rel) or []:
                out.append({"src": c["key"], "rel": rel, "dst": target})
    return out


async def seed(session: Any, ontology: Ontology | None = None) -> SeedReport:
    """Upsert the ontology as global ``seed`` nodes and edges. Idempotent; never commits (the caller owns the transaction)."""
    from src.repositories.kg_repository import KgRepository

    ont = ontology or load()
    repo = KgRepository(session)
    report = SeedReport()
    ids: dict[str, str] = {}
    for row in node_rows(ont):
        res = await repo.upsert_node(**row)
        report.nodes[res.action] += 1
        ids[row["key"]] = res.node["id"]
    for e in edge_rows(ont):
        src = ids[e["src"]]
        dst = ids.get(e["dst"])
        if dst is None:
            found = await repo.get_node_by_key(e["dst"], scope="global")
            if found is None:
                report.deferred_edges.append(e)
                continue
            dst = found["id"]
        er = await repo.add_edge(src, e["rel"], dst, source="seed")
        report.edges[er.action] += 1
    return report
