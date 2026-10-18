"""Understand: request -> concepts -> retrieval -> slots -> questions (PLAN-CAPABILITY-GRAPH section 4.1; CONTRACTS-CAPGRAPH.md section 4.1).

One deterministic pass plus at most one optional LLM call (owner decision, 2026-10-18: no multi-agent machinery):

1. **recognise** (always, no LLM). The request's word stems are matched against concept aliases (multi-word aliases are phrases). Each hit
   scores 0.85 (phrase) or 0.65 (one word), several hits combine, so a concept with any direct hit is *confirmed* (``CONFIRM_SCORE``).
   Concepts that a confirmed one ``requires`` or is ``is_a`` of are added as *implied* (score 0.4, never confirmed).
2. **LLM confirmation** (only when ``use_llm`` and ``ctx.ask_model`` is set): ONE call that may add concepts from the closed vocabulary and
   state slot values. Keys outside the vocabulary are dropped, values are re-validated by type. A failure degrades to the lexical result plus
   a warning and never fails the request.
3. **retrieve** what already exists (``kg_retrieve.retrieve``), then **fill slots**. Precedence per slot:
   answer > the request (deterministic extractors) > the LLM > a matching pattern's value (never for roles, durations or schedules: those
   are facts the person must state) > the ontology default.
4. **questions**: one per *required* slot of a confirmed concept that is still empty (decision D3), deduplicated through ``alias_of``,
   at most ``MAX_QUESTIONS``, ordered by concept score. Everything else is an assumption. A question that has an answer stays in the list
   with its ``answer`` set, so the count of questions asked is stable across the re-run that applies the answers.

``ctx.ask_model`` is ``async (payload: dict) -> dict``: it receives the ``WF_AI:UNDERSTAND`` payload (``untrusted_request``,
``concept_vocabulary``, ``roles``, ``slot_types``) and returns the parsed reply (``{concepts:[{key, evidence}], slots:{}, shape, parts_hint}``).
``ctx.extras`` may carry ``installed_templates`` (slugs never offered again) and ``prior`` (an earlier Understanding whose LLM-sourced
concepts and slots are kept when the answers are applied, so no second LLM call is needed).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Awaitable, Callable, TypedDict

from src.repositories.kg_repository import GraphSlice
from src.services import kg_slots
from src.services.kg_retrieve import Retrieval, empty_retrieval, retrieve

logger = logging.getLogger(__name__)

IMPLEMENTED = True

UNDERSTANDING_VERSION = 1
#: decision D3: only missing REQUIRED slots of confirmed concepts block, and never more than this many questions
MAX_QUESTIONS = 4
#: concepts scoring below this are shown as "suggested", not confirmed (PLAN section 11)
CONFIRM_SCORE = 0.5
UNDERSTAND_SYSTEM_LINE = "WF_AI:UNDERSTAND"

_PHRASE_SCORE = 0.85
_WORD_SCORE = 0.65
_LLM_SCORE = 0.7
_IMPLIED_SCORE = 0.4
MAX_VOCABULARY = 30
#: slot types whose value is a business fact the person must state: a pattern from another team never fills them
_NEVER_FROM_PATTERN = ("role", "duration", "cron")
_APP_CUE = re.compile(r"\b(an? )?(app|application)\b")
SHAPE_QUESTION_ID = "q_shape"


class UnderstandingConcept(TypedDict):
    key: str
    label: str
    score: float
    source: str  # lexical | llm | implied
    evidence: str
    confirmed: bool


# "from" is a Python keyword, so the slot shape uses the functional TypedDict form.
# from: request | llm | answer | default | pattern
UnderstandingSlot = TypedDict("UnderstandingSlot", {"value": Any, "from": str})


class UnderstandingQuestion(TypedDict):
    id: str  # "q_<concept slug>.<slot>"
    concept: str
    slot: str
    kind: str  # a slot type, or "choice" for the app-or-workflow question
    text: str
    options: list[str]
    required: bool
    answer: Any


class UnderstandingSummary(TypedDict):
    understood: list[str]  # concept labels
    reusing: dict[str, int]  # {"subworkflows": n, "templates": n, "patterns": n}
    questions: int


class Understanding(TypedDict):
    v: int
    concepts: list[UnderstandingConcept]
    slots: dict[str, UnderstandingSlot]  # keyed "<concept slug>.<slot>"
    questions: list[UnderstandingQuestion]
    assumptions: list[str]
    retrieval: Retrieval
    shape: str  # workflow | app
    adapt_from: dict[str, Any] | None  # {pattern, label, fit}
    summary: UnderstandingSummary
    warnings: list[str]


@dataclass
class UnderstandContext:
    """What ``understand`` needs besides the request."""

    project_id: str
    team_id: str | None
    slice: GraphSlice
    #: the closed-vocabulary LLM call: ``async (payload: dict) -> dict`` (see the module docstring); ``None`` means lexical only
    ask_model: Callable[..., Awaitable[Any]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def empty_understanding() -> Understanding:
    return {
        "v": UNDERSTANDING_VERSION, "concepts": [], "slots": {}, "questions": [], "assumptions": [],
        "retrieval": empty_retrieval(), "shape": "workflow", "adapt_from": None,
        "summary": {"understood": [], "reusing": {"subworkflows": 0, "templates": 0, "patterns": 0}, "questions": 0},
        "warnings": [],
    }


# ── the vocabulary: concepts and roles, from the graph slice (or the seed file when the graph is not seeded) ───────────


@dataclass
class Concept:
    key: str
    label: str
    aliases: list[str]
    definition: str = ""
    slots: list[dict[str, Any]] = field(default_factory=list)
    signatures: list[dict[str, Any]] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    is_a: list[str] = field(default_factory=list)
    test_obligations: list[str] = field(default_factory=list)
    prompt_hint: str = ""
    part_recipe: dict[str, Any] | None = None

    @property
    def slug(self) -> str:
        return self.key.split(":", 1)[-1]


@lru_cache(maxsize=1)
def _seed_vocabulary() -> tuple[dict[str, Concept], list[dict[str, Any]]]:
    from src.services import kg_ontology

    ont = kg_ontology.load()
    concepts = {c["key"]: Concept(
        key=c["key"], label=c["label"], aliases=list(c["aliases"]), definition=c["definition"].strip(), slots=[dict(s) for s in c.get("slots") or []],
        signatures=[dict(s) for s in c.get("signatures") or []], requires=list(c.get("requires") or []), is_a=list(c.get("is_a") or []),
        test_obligations=list(c.get("test_obligations") or []), prompt_hint=c["prompt_hint"].strip(), part_recipe=c.get("part_recipe"))
        for c in ont.concepts}
    roles = [{"key": r["key"], "label": r["label"], "aliases": list(r["aliases"])} for r in ont.roles]
    return concepts, roles


def seed_vocabulary() -> tuple[dict[str, Concept], list[dict[str, Any]]]:
    """The reviewed seed file's concepts and roles (cached)."""
    return _seed_vocabulary()


def vocabulary(slice: GraphSlice) -> tuple[dict[str, Concept], list[dict[str, Any]]]:
    """``({concept key: Concept}, [role dicts])``. The graph slice wins (it holds human-accepted concepts and team roles); when it has no
    concept at all (the graph was never seeded) the reviewed seed file is used, so Understand works on a bare database."""
    nodes = [n for n in slice.nodes.values() if n["kind"] == "concept" and n.get("status", "active") == "active"]
    if not nodes:
        return _seed_vocabulary()
    concepts: dict[str, Concept] = {}
    for n in nodes:
        p = n.get("props") or {}
        rel = lambda r: [d["key"] for d in (slice.node(e["dst_id"]) for e in slice.out_edges(n["id"], r)) if d]  # noqa: E731
        concepts[n["key"]] = Concept(
            key=n["key"], label=n["label"], aliases=[str(a) for a in n.get("aliases") or []], definition=p.get("definition") or n.get("summary") or "",
            slots=[dict(s) for s in p.get("slots") or []], signatures=[dict(s) for s in p.get("signatures") or []], requires=rel("requires"),
            is_a=rel("is_a"), test_obligations=list(p.get("test_obligations") or []), prompt_hint=p.get("prompt_hint") or "", part_recipe=p.get("part_recipe"))
    roles = [{"key": n["key"], "label": n["label"], "aliases": [str(a) for a in n.get("aliases") or []]}
             for n in slice.nodes.values() if n["kind"] == "role" and n.get("status", "active") == "active"]
    return concepts, roles or _seed_vocabulary()[1]


# ── step 1: lexical recognition ───────────────────────────────────────────────────────────────────────────────────────


def _tokens(text: str) -> list[str]:
    from src.services.ai_grounding import _stem

    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower())]


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


#: Patterns an alias list cannot express (a number after a comparison, an optional word, a verb followed by one of several objects), as
#: regexes over the lowercased request; each hit scores like a phrase. Plain phrases live in the ``bank_ops.yaml`` aliases, not here.
SIGNALS: dict[str, tuple[str, ...]] = {
    "concept:threshold_check": (
        r"\b(over|under|above|below|less than|more than|greater than|at least|up to|exceed\w*)\s+\$?\d",
        r"\b(score|amount|balance|total)\s+(is\s+)?(below|above|under|over)\b",
    ),
    "concept:notification": (
        r"(?<!not )(?<!never )\b(e-?mail|message|notify|inform|page|text)\s+(the |our |each )?(customer|requester|them|finance|ops|managers?|team|applicant|client|person|officer)\b",
        r"\blet (the |our )?\w+ know\b",
        r"\bsend (the |our )?\w+ (a |an )?(message|e-?mail|confirmation)\b",
        r"\bask the customer\b",
    ),
    "concept:intake": (
        r"\b(on|through|via|in|with)\s+an?\s+(\w+\s+)?form\b",
        r"\bfills? in an? (\w+\s+)?form\b",
    ),
    "concept:reconciliation": (r"\bcompar\w+\b[^.]*\b(ledger|settlements?|entries|two|records)\b",),
    "concept:sla": (r"\b(handled|resolved|actioned|closed)\s+(in|within)\s+(the next\s+)?(\d+|an?)\b",),
}
#: ordinary uses of an alias word that are not the concept ("shown on screen" is not Screening, "in that case" is not a case, "we schedule the
#: payment" is a verb, not Schedule). Matched text is blanked before recognition.
MASKS: tuple[str, ...] = (
    r"\bon[- ]screen\b", r"\b(other|that|which|either|any|each) case\b", r"\b(we|to|and|then|will|be|is|never|not)\s+schedul\w*", r"\bevidence summary\b",
    # "approved automatically" is a rule, not a person deciding: not an Approval
    r"\bauto[- ]?approv\w*", r"\bautomatically\s+approv\w*", r"\bapprov\w*\s+(automatically|auto|at once|immediately|instantly|straight away|without\b)",
)


def _masked(request: str) -> str:
    t = str(request or "").lower()
    for m in MASKS:
        t = re.sub(m, " ", t)
    return t


def recognise(request: str, concepts: dict[str, Concept]) -> dict[str, dict[str, Any]]:
    """``{concept key: {score, evidence, aliases}}`` for every concept with at least one alias (a phrase or a stemmed word) or ``SIGNALS``
    pattern in ``request``. Ordinary uses of an alias word listed in ``MASKS`` are ignored."""
    text = _masked(request)
    hay = _tokens(text)
    found: dict[str, dict[str, Any]] = {}
    for key, c in concepts.items():
        hits: list[tuple[float, str]] = []
        for alias in c.aliases:
            toks = _tokens(alias)
            if toks and _contains(hay, toks):
                hits.append((_PHRASE_SCORE if len(toks) > 1 else _WORD_SCORE, alias))
        for pat in SIGNALS.get(key, ()):
            m = re.search(pat, text)
            if m:
                hits.append((_PHRASE_SCORE, m.group(0).strip()))
        if hits:
            miss = 1.0
            for sc, _ in hits:
                miss *= 1 - sc
            best = max(hits, key=lambda h: (h[0], len(h[1])))
            found[key] = {"score": round(min(0.99, 1 - miss), 2), "evidence": f"'{best[1]}'", "aliases": [a for _, a in hits]}
    return found


def _closure(keys: list[str], concepts: dict[str, Concept]) -> list[str]:
    """Concepts the given ones need or specialise (``requires`` and ``is_a``), transitively, not already given."""
    out: list[str] = []
    stack = list(keys)
    seen = set(keys)
    while stack:
        c = concepts.get(stack.pop())
        for k in (c.requires + c.is_a) if c else []:
            if k in concepts and k not in seen:
                seen.add(k)
                out.append(k)
                stack.append(k)
    return out


# ── step 2: the one optional LLM call ─────────────────────────────────────────────────────────────────────────────────


def _llm_payload(request: str, found: dict[str, Any], concepts: dict[str, Concept], roles: list[dict[str, Any]]) -> dict[str, Any]:
    order = list(found) + [k for k in concepts if k not in found]
    vocab = []
    for k in order[:MAX_VOCABULARY]:
        c = concepts[k]
        vocab.append({"key": k, "label": c.label, "definition": c.definition,
                      "slots": [{"name": s["name"], "type": s["type"]} for s in c.slots if "alias_of" not in s]})
    return {"untrusted_request": request, "concept_vocabulary": vocab, "roles": [{"key": r["key"], "label": r["label"]} for r in roles],
            "slot_types": list(kg_slots_types())}


def kg_slots_types() -> tuple[str, ...]:
    from src.services.kg_ontology import SLOT_TYPES

    return SLOT_TYPES


def _llm_slot_value(slot: dict[str, Any], value: Any, roles: list[dict[str, Any]]) -> Any:
    """A value the model gave, re-validated by type; an unknown role becomes ``{"new_role": label}``. None when unusable."""
    try:
        return kg_slots.validate_value(slot, value, roles)
    except kg_slots.AnswerInvalid:
        if slot.get("type") == "role" and isinstance(value, str) and value.strip() and not slot.get("multiple"):
            return {"new_role": value.strip()[:80]}
        return None


# ── step 3: slots ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _slot_defs(known: list[str], concepts: dict[str, Concept]) -> tuple[dict[str, tuple[str, dict[str, Any]]], dict[str, str]]:
    """``({slot id: (concept key, slot)}, {alias slot id: original slot id})`` over the recognised concepts. A slot that is an
    ``alias_of`` another is the same fact as the original, which is added even when its own concept was not recognised."""
    defs: dict[str, tuple[str, dict[str, Any]]] = {}
    alias: dict[str, str] = {}
    for key in known:
        c = concepts[key]
        for s in c.slots:
            sid = f"{c.slug}.{s['name']}"
            if s.get("alias_of"):
                alias[sid] = str(s["alias_of"])
                oc = concepts.get("concept:" + str(s["alias_of"]).split(".")[0])
                orig = next((x for x in (oc.slots if oc else []) if x["name"] == str(s["alias_of"]).split(".", 1)[1]), None)
                if oc is not None and orig is not None:
                    defs.setdefault(str(s["alias_of"]), (oc.key, orig))
            else:
                defs.setdefault(sid, (key, s))
    return defs, alias


def _entity_word(found_entry: dict[str, Any] | None) -> str | None:
    for a in (found_entry or {}).get("aliases", []):
        if " " not in a and a.isalpha():
            return a[:-1] if a.endswith("s") and len(a) > 3 else a
    return None


def _from_request(request: str, sid: str, concept_key: str, slot: dict[str, Any], roles: list[dict[str, Any]], found: dict[str, Any],
                  caps: dict[str, str]) -> Any:
    t = slot["type"]
    if t == "duration":
        return kg_slots.extract_duration(request)
    if t == "cron":
        return kg_slots.extract_cron(request)
    if t == "role":
        if slot.get("multiple"):
            return None
        key = kg_slots.extract_role(request, slot["name"], roles)
        if key is None:   # a role the request names but nobody seeded: a stated fact, kept as an assumption the person can change
            label = kg_slots.extract_new_role(request, slot["name"], roles)
            return {"new_role": label} if label else None
        return key
    if t == "field":
        return None if slot.get("multiple") else kg_slots.extract_field(request)
    if t == "choice":
        return kg_slots.extract_choice(request, slot.get("options") or [])
    if t == "text" and slot["name"] == "limit":
        return kg_slots.extract_threshold(request)
    if t == "text" and slot["name"] == "entity":
        return _entity_word(found.get(concept_key))
    if t == "api_capability":
        return caps.get(concept_key)
    return None


def _capabilities(retrieval: Retrieval, known: list[str], concepts: dict[str, Concept], slice: GraphSlice) -> dict[str, str]:
    """``{concept key: the capability key that sends/does it}``: an API operation of this project that implements the concept, else a node
    type named by one of the concept's signatures that exists as a node capability (``node:SendEmail`` for Notification)."""
    out: dict[str, str] = {}
    for api in retrieval["apis"]:
        node = slice.by_key(api["key"])
        for e in slice.out_edges(node["id"], "implements") if node else []:
            d = slice.node(e["dst_id"])
            if d and d["key"] in known:
                out.setdefault(d["key"], api["key"])
    for key in known:
        if key in out:
            continue
        for sig in concepts[key].signatures:
            if sig.get("node_type") == "SendEmail" and "SendEmail" in retrieval["node_types"]:
                out[key] = "node:SendEmail"
                break
    return out


def _clean_answers(answers: dict[str, Any] | None) -> tuple[dict[str, Any], set[str]]:
    """``({slot id: raw answer}, {concept keys switched off})`` from the keys the studio posts: ``q_<slot id>``, ``<slot id>``, ``concepts_off``."""
    by_slot: dict[str, Any] = {}
    off: set[str] = set()
    for k, v in (answers or {}).items():
        if k == "concepts_off":
            off = {x if str(x).startswith("concept:") else f"concept:{x}" for x in (v if isinstance(v, list) else [v])}
        elif k.startswith("q_"):
            by_slot[k[2:]] = v
        else:
            by_slot[k] = v
    return by_slot, off


def _humanise(name: str) -> str:
    return name.replace("_", " ")


async def understand(
    ctx: UnderstandContext, request: str, *, answers: dict[str, Any] | None = None, use_llm: bool = False,
) -> Understanding:
    """See the module docstring. ``answers`` beat everything else, an LLM beats nothing; an LLM failure degrades to the lexical result with
    a warning. Never raises for a bad request, a bad answer (it is ignored with a warning) or a model failure."""
    request = request or ""
    concepts, roles = vocabulary(ctx.slice)
    out = empty_understanding()
    warnings: list[str] = out["warnings"]
    slot_answers, off = _clean_answers(answers)
    prior: dict[str, Any] = ctx.extras.get("prior") or {}

    # 1. recognise
    found = recognise(request, concepts)
    entries: dict[str, UnderstandingConcept] = {
        k: {"key": k, "label": concepts[k].label, "score": f["score"], "source": "lexical", "evidence": f["evidence"], "confirmed": f["score"] >= CONFIRM_SCORE}
        for k, f in found.items()}
    llm_slots: dict[str, Any] = {}
    llm_shape: str | None = None

    # 2. the one optional model call (or the LLM part of the prior run, when the answers are applied without a second call)
    reply: dict[str, Any] | None = None
    if use_llm and ctx.ask_model is not None:
        try:
            reply = await ctx.ask_model(_llm_payload(request, found, concepts, roles))
            if not isinstance(reply, dict):
                raise TypeError("the reply is not an object")
        except Exception as e:  # noqa: BLE001 - never fails the request
            logger.info("understand: the model call failed (%s); keyword matching only", type(e).__name__)
            warnings.append("The language model could not be reached, so only keyword matching was used.")
            reply = None
    elif prior:
        reply = {"concepts": [{"key": c["key"], "evidence": c.get("evidence", "")} for c in prior.get("concepts", []) if c.get("source") == "llm"],
                 "slots": {sid: s["value"] for sid, s in (prior.get("slots") or {}).items() if s.get("from") == "llm"},
                 "shape": prior.get("shape")}
    if reply:
        for item in reply.get("concepts") or []:
            key = item.get("key") if isinstance(item, dict) else None
            if key not in concepts:
                continue  # closed vocabulary: anything else is dropped
            ev = str(item.get("evidence") or "")[:120]
            if key in entries:
                entries[key]["score"] = max(entries[key]["score"], 0.8)
                entries[key]["confirmed"] = True
            else:
                entries[key] = {"key": key, "label": concepts[key].label, "score": _LLM_SCORE, "source": "llm", "evidence": ev, "confirmed": True}
        llm_slots = reply.get("slots") if isinstance(reply.get("slots"), dict) else {}
        llm_shape = reply.get("shape") if reply.get("shape") in ("workflow", "app") else None

    for k in off:
        if k in entries:
            entries[k]["confirmed"] = False
    confirmed = [k for k, e in entries.items() if e["confirmed"]]
    for k in _closure(confirmed, concepts):
        entries.setdefault(k, {"key": k, "label": concepts[k].label, "score": _IMPLIED_SCORE, "source": "implied", "evidence": "needed by a recognised concept",
                               "confirmed": False})
    ordered = sorted(entries.values(), key=lambda e: (-e["score"], e["key"]))
    out["concepts"] = ordered
    known = [e["key"] for e in ordered]

    # shape
    shape = "app" if (_APP_CUE.search(request.lower()) or llm_shape == "app") else "workflow"
    if slot_answers.get(SHAPE_QUESTION_ID[2:]) in ("app", "workflow"):
        shape = slot_answers[SHAPE_QUESTION_ID[2:]]
    out["shape"] = shape

    # 3. retrieval, slots
    hits = [{"key": e["key"], "label": e["label"], "score": e["score"], "source": e["source"], "evidence": e["evidence"], "confirmed": e["confirmed"]}
            for e in ordered]
    out["retrieval"] = retrieve(ctx.slice, hits, request, installed_templates=ctx.extras.get("installed_templates") or (), shape=shape)
    seed = next((p for p in out["retrieval"]["patterns"] if p.get("adapt")), None)
    if seed is not None:
        out["adapt_from"] = {"pattern": seed["key"], "label": seed["label"], "fit": seed["fit"]}
    pattern_slots: dict[str, Any] = {}
    if seed is not None:
        pn = ctx.slice.by_key(seed["key"])
        pattern_slots = dict(((pn or {}).get("props") or {}).get("slots") or {})

    defs, alias_of = _slot_defs(known, concepts)
    caps = _capabilities(out["retrieval"], known, concepts, ctx.slice)
    allowed_caps = set(caps.values()) | {a["key"] for a in out["retrieval"]["apis"]} | ({"node:SendEmail"} if "SendEmail" in out["retrieval"]["node_types"] else set())
    slots: dict[str, UnderstandingSlot] = {}
    pre_answer_from: dict[str, str | None] = {}  # how a slot would be filled without any answer ("what the person is asked")
    for sid, (ckey, slot) in defs.items():
        val, how = None, None
        v = _from_request(request, sid, ckey, slot, roles, found, caps)
        if v not in (None, "", []):
            val, how = v, "request"
        if val is None:
            lv = llm_slots.get(sid)
            if lv is None:
                lv = next((x for a, x in llm_slots.items() if alias_of.get(a) == sid), None)
            if lv is not None:
                cleaned = _llm_slot_value(slot, lv, roles)
                if cleaned not in (None, "", []):
                    val, how = cleaned, "llm"
        if val is None and seed is not None and slot["type"] not in _NEVER_FROM_PATTERN:
            pv = pattern_slots.get(sid)
            if pv not in (None, "", []):
                val, how = pv, "pattern"
        if val is None and slot.get("default") not in (None, "", []):
            val, how = slot["default"], "default"
        pre_answer_from[sid] = how
        if how is not None:
            slots[sid] = {"value": val, "from": how}

    # answers beat everything (an answer to an alias id is an answer to its original)
    valid_answers: dict[str, Any] = {}
    for sid, raw in slot_answers.items():
        if sid == SHAPE_QUESTION_ID[2:]:
            continue
        sid = alias_of.get(sid, sid)
        if sid not in defs:
            warnings.append(f"An answer for '{sid}' was ignored: Understand does not know that slot.")
            continue
        try:
            valid_answers[sid] = kg_slots.validate_value(defs[sid][1], raw, roles, allowed_caps=allowed_caps)
        except kg_slots.AnswerInvalid as e:
            warnings.append(f"The answer for '{sid}' was ignored: {e}")
    for sid, v in valid_answers.items():
        slots[sid] = {"value": v, "from": "answer"}
    out["slots"] = slots

    # 4. questions: required slots of confirmed concepts that nothing but an answer could fill
    questions: list[UnderstandingQuestion] = []
    dropped: list[str] = []
    seen_q: set[str] = set()
    for e in ordered:
        if not e["confirmed"]:
            continue
        c = concepts[e["key"]]
        for s in c.slots:
            if not s.get("required"):
                continue
            sid = alias_of.get(f"{c.slug}.{s['name']}", f"{c.slug}.{s['name']}") if s.get("alias_of") else f"{c.slug}.{s['name']}"
            if sid in seen_q or sid not in defs:
                continue
            seen_q.add(sid)
            if pre_answer_from.get(sid) is not None:
                continue
            ckey, orig = defs[sid]
            if len(questions) >= MAX_QUESTIONS:
                dropped.append(f"{concepts[ckey].label}: {_humanise(orig['name'])} is not set.")
                continue
            questions.append(_question(sid, ckey, concepts[ckey], orig, roles, request, valid_answers.get(sid)))
    if shape == "app" and out["adapt_from"] is None:   # optional, so it is outside the cap on blocking questions
        n = len({p["key"] for k in confirmed for p in ((concepts[k].part_recipe or {}).get("parts") or [])})
        if n >= 2:
            ans = slot_answers.get(SHAPE_QUESTION_ID[2:])
            questions.append({"id": SHAPE_QUESTION_ID, "concept": "", "slot": "shape", "kind": "choice", "required": False,
                              "text": f"This looks like an application with {n} parts. Build it as an app?", "options": ["app", "workflow"],
                              "answer": ans if ans in ("app", "workflow") else None})
    out["questions"] = questions

    # assumptions: defaults and pattern values the person can still change
    confirmed_set = set(confirmed)
    for sid, s in slots.items():
        if s["from"] in ("default", "pattern") and s["value"] not in ("", None) and defs[sid][0] in confirmed_set:
            ckey = defs[sid][0]
            out["assumptions"].append(f"{concepts[ckey].label}: {_humanise(defs[sid][1]['name'])} is {_show(s['value'])}"
                                      f"{' (taken from a similar app)' if s['from'] == 'pattern' else ''}.")
    out["assumptions"] += dropped
    r = out["retrieval"]
    out["summary"] = {"understood": [e["label"] for e in ordered if e["confirmed"]],
                      "reusing": {"subworkflows": len(r["subworkflows"]), "templates": len(r["templates"]), "patterns": len(r["patterns"])},
                      "questions": len(questions)}
    return out


def _show(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _question(sid: str, ckey: str, c: Concept, slot: dict[str, Any], roles: list[dict[str, Any]], request: str, answer: Any) -> UnderstandingQuestion:
    t = slot["type"]
    options: list[str] = []
    if t == "role":
        options = [r["key"] for r in roles]
    elif t == "choice":
        options = list(slot.get("options") or [])
    elif t == "duration":
        options = ["4h", "24h", "48h"]
    elif t == "cron":
        options = ["0 8 * * *", "0 8 * * 1"]
    elif t == "field":
        mentioned = kg_slots.extract_field(request)
        options = ([mentioned] if mentioned else []) + [f for f in kg_slots.COMMON_FIELDS if f != mentioned]
    q: UnderstandingQuestion = {"id": f"q_{sid}", "concept": ckey, "slot": slot["name"], "kind": t, "required": True,
                                "text": slot.get("question") or f"{c.label} needs {_humanise(slot['name'])}.", "options": options, "answer": answer}
    if slot.get("multiple"):
        q["multiple"] = True  # type: ignore[typeddict-unknown-key]
    if t == "role":
        q["option_labels"] = {r["key"]: r["label"] for r in roles}  # type: ignore[typeddict-unknown-key]
    return q


# ── answers: validation for the route ─────────────────────────────────────────────────────────────────────────────────


def check_answers(prev: Understanding, answers: dict[str, Any], slice: GraphSlice) -> dict[str, Any]:
    """Validate ``{question id: value}`` against the questions of ``prev``; returns the answers in canonical form (same keys).

    Raises ``kg_slots.AnswerInvalid`` carrying ``.qid``: an unknown question id, a value that does not fit its slot, or a required question
    left without an answer."""
    concepts, roles = vocabulary(slice)
    by_id = {q["id"]: q for q in prev.get("questions") or []}
    allowed_caps = {a["key"] for a in prev.get("retrieval", {}).get("apis", [])} | {"node:SendEmail"}
    out: dict[str, Any] = {}
    for qid, raw in (answers or {}).items():
        key = qid if qid.startswith("q_") else f"q_{qid}"
        q = by_id.get(key)
        if q is None:
            raise _invalid(qid, "That question is not part of this request.")
        if q["slot"] == "shape":
            if raw not in ("app", "workflow"):
                raise _invalid(qid, "Pick app or workflow.")
            out[key] = raw
            continue
        c = concepts.get(q["concept"])
        slot = next((s for s in (c.slots if c else []) if s["name"] == q["slot"]), None)
        if slot is None:
            raise _invalid(qid, "That question can no longer be answered.")
        try:
            out[key] = kg_slots.validate_value(slot, raw, roles, allowed_caps=allowed_caps)
        except kg_slots.AnswerInvalid as e:
            raise _invalid(qid, str(e)) from None
    for q in prev.get("questions") or []:
        if q["required"] and q["answer"] is None and q["id"] not in out:
            raise _invalid(q["id"], "This question needs an answer.")
    return out


def _invalid(qid: str, message: str) -> kg_slots.AnswerInvalid:
    e = kg_slots.AnswerInvalid(message)
    e.qid = qid  # type: ignore[attr-defined]
    return e


def open_questions(u: Understanding | dict[str, Any] | None) -> list[dict[str, Any]]:
    """The required questions that still have no answer: when any is left the proposal waits in ``clarify``."""
    return [q for q in (u or {}).get("questions") or [] if q.get("required") and q.get("answer") is None]


def slot_value(u: Understanding | dict[str, Any] | None, *ids: str) -> Any:
    """The value of the first of ``ids`` that the understanding holds (an alias id such as ``escalation.after`` is stored under its original,
    ``sla.due_in``, so callers list both)."""
    slots = (u or {}).get("slots") or {}
    for i in ids:
        if i in slots:
            return slots[i]["value"]
    return None


# ── what the spec and build prompts get ───────────────────────────────────────────────────────────────────────────────


def capability_context(u: Understanding | dict[str, Any], *, part: dict[str, Any] | None = None, siblings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ``capability_context`` prompt section (CONTRACTS-CAPGRAPH.md 4.3): confirmed concepts with their definition, hint and test
    obligations, the confirmed slot values, and what already exists. Plain data; empty dict when nothing was understood."""
    seed, _ = _seed_vocabulary()
    cs = []
    for c in u.get("concepts") or []:
        if not c.get("confirmed"):
            continue
        d = seed.get(c["key"])
        cs.append({"key": c["key"], "label": c["label"], "definition": d.definition if d else "", "prompt_hint": d.prompt_hint if d else "",
                   "test_obligations": d.test_obligations if d else []})
    if not cs:
        return {}
    r = u.get("retrieval") or {}
    reuse = {
        "subworkflows": [{"workflow_id": s["key"].split(":", 1)[-1], "label": s["label"], "why": s.get("why", "")} for s in r.get("subworkflows", [])],
        "templates": [{"slug": t["key"].split(":", 1)[-1], "label": t["label"]} for t in r.get("templates", [])],
        "apis": [a["key"].split(":", 1)[-1] for a in r.get("apis", [])],
    }
    out: dict[str, Any] = {"concepts": cs, "slots": {sid: s["value"] for sid, s in (u.get("slots") or {}).items()}, "reuse": reuse}
    if part:
        out["part"] = part
    if siblings:
        out["siblings"] = siblings
    return out
