"""The application plan: which parts an app is made of (PLAN-CAPABILITY-GRAPH section 5.2; CONTRACTS-CAPGRAPH.md 5.2).

Pure: no database, no model. ``plan_app(understanding, slice)`` turns what Understand found into an ``AppPlan``:

* **fresh**: every confirmed concept brings the parts of its ``part_recipe`` (the reviewed ontology file); parts that share a recipe key
  merge. The concepts that have no recipe of their own (Approval, SLA, Escalation, the entity, ...) attach to the main entry, the
  schedule and notification concepts to the scheduled entry. Dependencies are wired by rule (the main entry calls the sub-workflows,
  the report entries call the report sub-workflow).
* **adapted**: when Understand picked a validated pattern (``understanding.adapt_from``) its scrubbed skeleton is cloned (same part keys,
  same tables, same columns) and the new slot values are substituted; the plan records ``adapted_from`` with a slot-by-slot diff.
* **reuse**: a recipe sub-workflow part that a callable sub-workflow of the project already implements becomes ``reuse`` and is not built.

There is no model call here. (PLAN section 5.2 allows an optional ``WF_AI:APP_PLAN`` step that only proposes titles; it is not built: the
deterministic plan stands, and a person can ``PUT`` any edit, which is re-validated by ``validate_app_plan``.)

``validate_app_plan`` codes (errors unless noted):

    APP001  the plan is malformed: version, title, a part or table key that is not a slug, a duplicate key
    APP002  ``depends_on`` (or a table reference) names something the plan does not have
    APP003  ``depends_on`` is cyclic
    APP004  too many parts (6) or tables (3), or no entry part
    APP005  an Approval concept inside a sub-workflow part (mirrors TPL_APPROVAL_IN_SUB / WF025)
    APP006  a part has a trigger kind that maps to no node, or the wrong one for its role
    APP007  a column type or name (`id`, `created_at`, `updated_at` are every row's own), a table name or a cron expression is invalid
    APP008  a ``reuse`` capability is not a callable sub-workflow of this project
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping

from ..schemas.template import COLUMN_TYPES, KEY_RE
from . import kg_understand as ku

PLAN_VERSION = 1
MAX_PARTS = 6
MAX_TABLES = 3
#: every data-table row has these (``DataTableService`` refuses them as column names): the recipe's `id` is the row id
RESERVED_COLUMNS = frozenset({"id", "created_at", "updated_at"})
ROLES = ("entry", "subworkflow")
#: trigger kind -> node type (``ai_grounding.TRIGGER_TYPE_BY_KIND``); a sub-workflow is always ``execute``
TRIGGERS = {"form": "FormTrigger", "webhook": "Webhook", "cron": "Cron", "manual": "Start", "chat": "ChatInput", "execute": "ExecuteWorkflowTrigger"}
#: concepts that need an Approval node: never inside a sub-workflow part
APPROVAL_CONCEPTS = frozenset({"concept:approval", "concept:four_eyes_approval", "concept:review", "concept:document_review", "concept:escalation"})
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_CRON_FIELD = re.compile(r"^[0-9*/,\-]+$")
_TITLE_RE = re.compile(r"\b(?:app|application)\s+(?:to|for|that|which)\s+(?:manage|track|handle|run|process|log|capture|record)?\s*(.+?)(?:[:.,;]|$)", re.I)

_MAIN_CONCEPT_FOR_CRON = ("concept:schedule", "concept:notification")


def issue(code: str, path: str, message: str, severity: str = "error") -> dict[str, str]:
    return {"code": code, "severity": severity, "path": path, "message": message}


def errors_of(issues: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(i) for i in issues if i.get("severity", "error") == "error"]


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────


def slug_key(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if not s or not s[0].isalpha():
        s = (fallback if not s else f"{fallback}_{s}")
    return s[:40].rstrip("_") or fallback


def _a(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _plural(word: str) -> str:
    return word if word.endswith("s") else word + "s"


def _humanise(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def _column_type(name: str) -> str:
    n = name.lower()
    if n.endswith("_at") or n in ("date", "due", "due_date", "timestamp"):
        return "datetime"
    if n in ("amount", "total", "count", "balance", "price", "quantity") or n.endswith("_amount"):
        return "number"
    return "string"


def _is_cron(v: Any) -> bool:
    f = str(v or "").split()
    return len(f) == 5 and all(_CRON_FIELD.match(x) for x in f)


def _frequency(cron: str | None) -> str:
    f = str(cron or "").split()
    if len(f) != 5:
        return "Scheduled"
    if f[4] not in ("*", "?") and f[2] == "*":
        return "Weekly"
    if f[2] not in ("*", "?"):
        return "Monthly"
    if f[1] == "*" or "/" in f[1]:
        return "Hourly"
    return "Daily"


def _slot(understanding: Mapping[str, Any], *ids: str) -> Any:
    return ku.slot_value(understanding, *ids)


def _title_of(request: str, entity: str) -> str:
    m = _TITLE_RE.search(request or "")
    if m:
        t = re.sub(r"\s+", " ", m.group(1)).strip(" .:,;")
        t = re.sub(r"^(?:the|our|all|a|an)\s+", "", t, flags=re.I)
        if 2 <= len(t) <= 60 and len(t.split()) <= 6:
            return t[:1].upper() + t[1:]
    return f"{_plural(entity).capitalize()} app"


# ── building the parts ────────────────────────────────────────────────────────────────────────────────────────────────


def _contract_for(part: Mapping[str, Any], slots: Mapping[str, Any]) -> dict[str, Any]:
    concepts = set(part.get("concepts") or [])
    if "concept:assignment" in concepts:
        by = slots.get("assignment.by_field") or "category"
        by = by[0] if isinstance(by, list) and by else by
        return {"inputs": [{"name": slug_key(str(by), "category"), "type": "string", "required": True}], "output_fields": ["owner_role"]}
    if "concept:report" in concepts:
        return {"inputs": [{"name": "since", "type": "string", "required": False}], "output_fields": ["rows", "totals"]}
    return {"inputs": [{"name": "item", "type": "string", "required": False}], "output_fields": ["result"]}


def _table_for(entity: str, recipe_table: Mapping[str, Any], slots: Mapping[str, Any], *, extra_columns: bool) -> dict[str, Any]:
    key = slug_key(str(recipe_table.get("key") or "cases"), "cases")
    cols = [str(c) if not isinstance(c, Mapping) else str(c.get("name")) for c in recipe_table.get("columns") or ["id", "status", "owner_role", "opened_at"]]
    if extra_columns:   # the deciding field and the fields the person listed become columns (an adapted plan keeps the pattern's columns)
        by = slots.get("assignment.by_field")
        extras = [by] if isinstance(by, str) else []
        fields = slots.get("exception_case.fields")
        extras += [f for f in fields if isinstance(f, str)] if isinstance(fields, list) else ([fields] if isinstance(fields, str) else [])
        for e in extras:
            e = slug_key(e, "field")
            if e not in cols:
                cols.append(e)
    name = f"{slug_key(entity, 'item')}_{key}"
    return {"key": key, "name": name[:62],
            "columns": [{"name": c, "type": _column_type(c)} for c in dict.fromkeys(cols) if c not in RESERVED_COLUMNS]}


def _interface_for(part_key: str, kind: str | None, screen: str | None, entity: str, is_main: bool) -> dict[str, Any] | None:
    if not kind:
        return None
    slug = f"log-{slug_key(entity).replace('_', '-')}" if is_main else f"{_plural(slug_key(entity)).replace('_', '-')}-report"
    out: dict[str, Any] = {"kind": kind, "slug": slug}
    if screen:
        out["screen"] = screen
    return out


def _wire(parts: list[dict[str, Any]]) -> None:
    """Dependencies by rule: report entries call the report sub-workflow; the main entry calls every other sub-workflow. Explicit
    ``depends_on`` from a recipe or a person is kept."""
    subs = [p for p in parts if p["role"] == "subworkflow"]
    report_subs = [p["key"] for p in subs if "concept:report" in p.get("concepts", [])]
    other_subs = [p["key"] for p in subs if p["key"] not in report_subs]
    main = next((p for p in parts if p["role"] == "entry" and "concept:report" not in p.get("concepts", [])), None)
    for p in parts:
        if p["role"] != "entry":
            continue
        want = report_subs if "concept:report" in p.get("concepts", []) else (other_subs if p is main else [])
        for k in want:
            if k not in p["depends_on"]:
                p["depends_on"].append(k)


def _screens(plan: Mapping[str, Any]) -> list[str]:
    entity = plan.get("entity") or "item"
    out: list[str] = []
    for p in plan.get("parts") or []:
        iface = p.get("interface") or {}
        if p["role"] == "entry" and iface.get("kind") in ("form", "chat"):
            out.append(f"{iface['kind']}: {p['title']}")
        if p["role"] == "entry" and {"concept:approval", "concept:escalation", "concept:sla"} & set(p.get("concepts") or []):
            out.append(f"task: Resolve {entity} (Approvals inbox)")
            if "concept:escalation" in (p.get("concepts") or []):
                out.append(f"task: Escalated {entity}")
        if p["role"] == "entry" and iface.get("kind") == "custom":
            out.append(f"page: {p['title']} (table)")
    return list(dict.fromkeys(out))


def _order_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def rank(p: dict[str, Any]) -> int:
        if p["role"] == "subworkflow":
            return 0
        return 2 if "concept:report" in p.get("concepts", []) else 1
    return [p for _i, p in sorted(enumerate(parts), key=lambda ip: (rank(ip[1]), ip[0]))]


def _fresh_parts(confirmed: list[str], concepts: Mapping[str, Any], entity: str, slots: Mapping[str, Any], understanding: Mapping[str, Any]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parts: dict[str, dict[str, Any]] = {}
    tables: list[dict[str, Any]] = []
    for ck in confirmed:
        recipe = (concepts[ck].part_recipe if ck in concepts else None) or {}
        for rp in recipe.get("parts") or []:
            key = slug_key(rp["key"])
            role = rp.get("role") or "entry"
            p = parts.setdefault(key, {"key": key, "role": role, "title": rp.get("title") or _humanise(key),
                                       "trigger": rp.get("trigger") or ("execute" if role == "subworkflow" else "form"), "concepts": [],
                                       "depends_on": [slug_key(d) for d in rp.get("depends_on") or []],
                                       "_interface": rp.get("interface"), "_screen": rp.get("screen")})
            if ck not in p["concepts"]:
                p["concepts"].append(ck)
        for tb in recipe.get("tables") or []:
            if len(tables) < MAX_TABLES and not any(t["key"] == slug_key(str(tb.get("key"))) for t in tables):
                tables.append(_table_for(entity, tb, slots, extra_columns=True))
    # the main entry: the intake recipe's part (renamed after the entity), or a form when the request never said how items arrive
    main_key = f"log_{slug_key(entity)}"
    channel = slots.get("intake.channel") or "form"
    if "intake" in parts:
        p = parts.pop("intake")
        p["key"], p["title"] = main_key, f"Log {_a(entity)} {entity}"
        if channel in TRIGGERS and channel != "cron":
            p["trigger"] = channel
        parts[main_key] = p
    elif not any(p["role"] == "entry" and p["trigger"] != "cron" and "concept:report" not in p["concepts"] for p in parts.values()):
        parts[main_key] = {"key": main_key, "role": "entry", "title": f"Log {_a(entity)} {entity}", "trigger": channel if channel in TRIGGERS and channel != "cron" else "form",
                           "concepts": [], "depends_on": [], "_interface": "form", "_screen": None}
    main = parts[main_key] if main_key in parts else next(p for p in parts.values() if p["role"] == "entry")
    cron = _slot(understanding, "report.schedule", "schedule.cron")
    recipients = _slot(understanding, "report.recipients_role")
    scheduled = next((p for p in parts.values() if p["role"] == "entry" and p["trigger"] == "cron"), None)
    for ck in confirmed:
        recipe = (concepts[ck].part_recipe if ck in concepts else None) or {}
        if recipe.get("parts"):
            continue
        target = scheduled if (ck in _MAIN_CONCEPT_FOR_CRON and scheduled is not None) else main
        if ck == "concept:report_output":
            target = next((p for p in parts.values() if p.get("_interface") == "custom"), target)
        if ck not in target["concepts"]:
            target["concepts"].append(ck)
    if {"concept:escalation", "concept:sla"} & set(confirmed) and "concept:approval" not in main["concepts"]:
        main["concepts"].append("concept:approval")   # a timeout on a human task is an Approval with `due_in`
    out = []
    for p in parts.values():
        want_iface, want_screen = p.pop("_interface", None), p.pop("_screen", None)
        iface = _interface_for(p["key"], want_iface, want_screen, entity, p is main) if p["role"] == "entry" else None
        if p["role"] == "entry" and p["trigger"] in ("webhook", "manual", "cron"):
            iface = None
        if iface:
            p["interface"] = iface
        if p["role"] == "subworkflow":
            p["contract"] = _contract_for(p, slots)
        if p["trigger"] == "cron":
            if isinstance(cron, str) and cron:
                p["cron"] = cron
            if isinstance(recipients, str) and recipients:
                p["recipients_role"] = recipients
        p["reuse"] = None
        out.append(p)
    # titles that name the entity
    for p in out:
        if p["key"] == "build_report":
            p["title"] = f"Build {_plural(entity)} report"
        elif p["role"] == "entry" and p["trigger"] == "cron":
            p["title"] = f"{_frequency(p.get('cron'))} {_plural(entity)} report"
        elif p.get("interface", {}).get("kind") == "custom":
            p["title"] = f"{_plural(entity).capitalize()} report"
    out = _order_parts(out)
    _wire(out)
    main_part = next(p for p in out if p["key"] == main["key"])
    if tables:
        main_part["tables"] = [t["key"] for t in tables]
        for p in out:
            if p["key"] == "build_report":
                p["tables"] = [t["key"] for t in tables]
    return out, tables


def _adapted_parts(node: Mapping[str, Any], entity: str, slots: Mapping[str, Any], understanding: Mapping[str, Any], concepts: Mapping[str, Any]
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Clone a validated pattern's skeleton. None when the pattern has no usable skeleton (the caller composes fresh)."""
    sk = ((node.get("props") or {}).get("skeleton") or {})
    if not sk.get("parts"):
        return None
    cron = _slot(understanding, "report.schedule", "schedule.cron")
    recipients = _slot(understanding, "report.recipients_role")
    tr = {"form": "form", "webhook": "webhook", "cron": "cron", "manual": "manual", "chat": "chat", "execute": "execute"}
    parts: list[dict[str, Any]] = []
    for sp in sk["parts"]:
        key = slug_key(str(sp.get("key") or "").replace("{entity}", slug_key(entity)))
        role = sp.get("role") if sp.get("role") in ROLES else "entry"
        trig = tr.get(sp.get("trigger") or "", "execute" if role == "subworkflow" else "form")
        if role == "subworkflow":
            trig = "execute"
        cs = [c for c in sp.get("concepts") or [] if isinstance(c, str)]
        title = next((rp["title"] for c in cs for rp in (((concepts[c].part_recipe if c in concepts else None) or {}).get("parts") or []) if slug_key(rp["key"]) == key), None)
        part: dict[str, Any] = {"key": key, "role": role, "title": title or _humanise(key), "trigger": trig, "concepts": cs, "depends_on": [], "reuse": None}
        parts.append(part)
    main = next((p for p in parts if p["role"] == "entry" and p["trigger"] != "cron" and "concept:report" not in p["concepts"]), None) or \
        next((p for p in parts if p["role"] == "entry"), None)
    if main is None:
        return None
    for p in parts:
        if p["role"] == "entry":
            if p is main:
                p["title"] = f"Log {_a(entity)} {entity}"
                if p["trigger"] in ("form", "chat"):
                    p["interface"] = _interface_for(p["key"], p["trigger"], None, entity, True)
            elif p["trigger"] == "cron":
                p["title"] = f"{_frequency(cron if isinstance(cron, str) else None)} {_plural(entity)} report"
                if isinstance(cron, str) and cron:
                    p["cron"] = cron
                if isinstance(recipients, str) and recipients:
                    p["recipients_role"] = recipients
            elif p["trigger"] in ("form", "chat"):
                custom = "concept:report" in p["concepts"]
                p["interface"] = _interface_for(p["key"], "custom" if custom else p["trigger"], "report_table" if custom else None, entity, False)
                p["title"] = f"{_plural(entity).capitalize()} report"
        else:
            p["contract"] = _contract_for(p, slots)
            if p["key"] == "build_report":
                p["title"] = f"Build {_plural(entity)} report"
    tables = []
    for st in sk.get("tables") or []:
        raw_key = str(st.get("key") or "cases").replace("{entity}", "cases")
        cols = [c for c in st.get("columns") or [] if isinstance(c, str)] or ["id", "status", "owner_role", "opened_at"]
        tables.append(_table_for(entity, {"key": raw_key, "columns": cols}, slots, extra_columns=False))
    tables = tables[:MAX_TABLES]
    _wire(parts)
    if tables:
        main["tables"] = [t["key"] for t in tables]
        for p in parts:
            if p["key"] == "build_report":
                p["tables"] = [t["key"] for t in tables]
    return _order_parts(parts), tables


def _slot_diff(node: Mapping[str, Any] | None, slots: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Slot values that differ between the pattern and this request: ``[{slot, from, to}]`` (the plan banner's diff)."""
    theirs = ((node or {}).get("props") or {}).get("slots") or {}
    out = []
    for sid in sorted(set(theirs) & set(slots)):
        a, b = theirs[sid], slots[sid]
        if a not in (None, "", []) and b not in (None, "", []) and a != b:
            out.append({"slot": sid, "from": a, "to": b})
    return out


def _apply_reuse(parts: list[dict[str, Any]], understanding: Mapping[str, Any]) -> None:
    subs = (understanding.get("retrieval") or {}).get("subworkflows") or []
    for p in parts:
        if p["role"] != "subworkflow" or p.get("reuse"):
            continue
        for s in subs:
            if set(s.get("concepts") or []) & (set(p["concepts"]) - {"concept:exception_case"}) and str(s.get("key", "")).startswith("wf:"):
                p["reuse"] = {"capability": s["key"], "label": s.get("label") or s["key"]}
                break


def plan_app(understanding: Mapping[str, Any], slice: Any = None, *, request: str = "") -> dict[str, Any]:
    """The ``AppPlan`` (frozen shape, section 5.2) for an Understanding. Deterministic; never raises for odd input."""
    concepts, _roles = ku.vocabulary(slice) if slice is not None else ku.seed_vocabulary()
    confirmed = [c["key"] for c in understanding.get("concepts") or [] if c.get("confirmed") and c["key"] in concepts]
    slots = {sid: s.get("value") for sid, s in (understanding.get("slots") or {}).items() if isinstance(s, Mapping)}
    entity = slug_key(str(slots.get("exception_case.entity") or "item"), "item").replace("_", " ")
    adapt = understanding.get("adapt_from") or None
    node = slice.by_key(adapt["pattern"]) if (adapt and slice is not None) else None
    built = _adapted_parts(node, entity, slots, understanding, concepts) if node is not None else None
    parts, tables = built if built is not None else _fresh_parts(confirmed, concepts, entity, slots, understanding)
    adapted = None
    if built is not None:
        meta = next((p for p in (understanding.get("retrieval") or {}).get("patterns") or [] if p.get("key") == adapt["pattern"]), {})
        adapted = {"pattern": adapt["pattern"], "label": adapt.get("label") or node.get("label"), "fit": adapt.get("fit"),
                   "validated_count": meta.get("validated_count", node.get("validated_count", 0)), "source_project": meta.get("source_project"),
                   "diff": _slot_diff(node, {**slots, "exception_case.entity": slots.get("exception_case.entity") or entity})}
    _apply_reuse(parts, understanding)
    plan: dict[str, Any] = {"v": PLAN_VERSION, "title": _title_of(request, entity), "entity": entity, "adapted_from": adapted,
                            "tables": tables, "parts": parts}
    plan["screens"] = _screens(plan)
    return plan


# ── validation ────────────────────────────────────────────────────────────────────────────────────────────────────────


def validate_app_plan(plan: Any, *, callable_keys: Iterable[str] | None = None) -> list[dict[str, str]]:
    """APP001-APP008. ``callable_keys`` are the project's callable sub-workflow capability keys (``wf:<id>``); None skips APP008."""
    issues: list[dict[str, str]] = []
    add = lambda code, path, msg, sev="error": issues.append(issue(code, path, msg, sev))  # noqa: E731
    if not isinstance(plan, Mapping):
        return [issue("APP001", "", "The plan must be an object.")]
    if plan.get("v") != PLAN_VERSION:
        add("APP001", "v", f"The plan version must be {PLAN_VERSION}.")
    if not str(plan.get("title") or "").strip():
        add("APP001", "title", "The plan needs a title.")
    parts = plan.get("parts")
    tables = plan.get("tables") or []
    if not isinstance(parts, list) or not parts:
        return issues + [issue("APP001", "parts", "The plan needs at least one part.")]
    if not isinstance(tables, list):
        return issues + [issue("APP001", "tables", "tables must be a list.")]

    keys: list[str] = []
    for i, p in enumerate(parts):
        if not isinstance(p, Mapping) or not isinstance(p.get("key"), str) or not KEY_RE.match(p["key"]):
            add("APP001", f"parts[{i}].key", "A part key is a lowercase slug (letters, digits, underscores; starts with a letter).")
            continue
        if p["key"] in keys:
            add("APP001", f"parts[{i}].key", f"The part key '{p['key']}' is used twice.")
        keys.append(p["key"])
    tkeys: list[str] = []
    for i, t in enumerate(tables):
        if not isinstance(t, Mapping) or not isinstance(t.get("key"), str) or not KEY_RE.match(t["key"]):
            add("APP001", f"tables[{i}].key", "A table key is a lowercase slug.")
            continue
        if t["key"] in tkeys:
            add("APP001", f"tables[{i}].key", f"The table key '{t['key']}' is used twice.")
        tkeys.append(t["key"])
        if not _TABLE_NAME_RE.match(str(t.get("name") or "")):
            add("APP007", f"tables[{i}].name", "A table name is letters, digits and underscores, starting with a letter.")
        cols = t.get("columns")
        if not isinstance(cols, list) or not cols:
            add("APP007", f"tables[{i}].columns", "A table needs at least one column.")
            continue
        seen: set[str] = set()
        for j, c in enumerate(cols):
            if not isinstance(c, Mapping) or not str(c.get("name") or "").strip():
                add("APP007", f"tables[{i}].columns[{j}]", "A column needs a name.")
                continue
            if c.get("type") not in COLUMN_TYPES:
                add("APP007", f"tables[{i}].columns[{j}].type", f"Column '{c['name']}': the type must be one of {', '.join(COLUMN_TYPES)}.")
            if c["name"] in RESERVED_COLUMNS:
                add("APP007", f"tables[{i}].columns[{j}].name", f"'{c['name']}' is a column every row already has; choose another name.")
            if c["name"] in seen:
                add("APP001", f"tables[{i}].columns[{j}].name", f"The column '{c['name']}' is listed twice.")
            seen.add(c["name"])
    if len(tkeys) > MAX_TABLES:
        add("APP004", "tables", f"At most {MAX_TABLES} tables ({len(tkeys)} planned).")
    workflow_parts = [p for p in parts if isinstance(p, Mapping) and not p.get("reuse")]
    if len(workflow_parts) > MAX_PARTS:
        add("APP004", "parts", f"At most {MAX_PARTS} workflows can be built in one app ({len(workflow_parts)} planned).")
    if not any(isinstance(p, Mapping) and p.get("role") == "entry" for p in parts):
        add("APP004", "parts", "An app needs at least one entry part (a form, a schedule or a webhook).")

    known = set(keys)
    graph: dict[str, list[str]] = {}
    callable_set = set(callable_keys) if callable_keys is not None else None
    for i, p in enumerate(parts):
        if not isinstance(p, Mapping) or not isinstance(p.get("key"), str):
            continue
        at = f"parts[{i}]"
        role, trig = p.get("role"), p.get("trigger")
        if role not in ROLES:
            add("APP006", f"{at}.role", "A part's role is entry or subworkflow.")
        if trig not in TRIGGERS:
            add("APP006", f"{at}.trigger", f"The trigger must be one of {', '.join(TRIGGERS)}.")
        elif role == "subworkflow" and trig != "execute":
            add("APP006", f"{at}.trigger", "A sub-workflow is started by another workflow: its trigger is 'execute'.")
        elif role == "entry" and trig == "execute":
            add("APP006", f"{at}.trigger", "An entry part starts by itself (form, cron, webhook, chat or manual), not 'execute'.")
        if trig == "cron" and role == "entry" and not _is_cron(p.get("cron")):
            add("APP007", f"{at}.cron", "A scheduled part needs a five-field cron expression.")
        if role == "subworkflow" and not p.get("reuse"):
            if set(p.get("concepts") or []) & APPROVAL_CONCEPTS:
                add("APP005", f"{at}.concepts", f"'{p['key']}' is a sub-workflow but its concepts need an Approval; approvals belong in an entry part (WF025).")
            contract = p.get("contract") or {}
            if not isinstance(contract.get("inputs"), list) or not contract["inputs"]:
                add("APP001", f"{at}.contract.inputs", "A sub-workflow declares at least one input.")
        if p.get("reuse"):
            cap = (p["reuse"] or {}).get("capability") if isinstance(p["reuse"], Mapping) else None
            if not isinstance(cap, str) or not cap.startswith("wf:"):
                add("APP008", f"{at}.reuse", "reuse names a sub-workflow as 'wf:<id>'.")
            elif callable_set is not None and cap not in callable_set:
                add("APP008", f"{at}.reuse.capability", f"{cap} is not a published, callable sub-workflow of this project.")
        deps = p.get("depends_on") or []
        if not isinstance(deps, list):
            add("APP002", f"{at}.depends_on", "depends_on must be a list of part keys.")
            deps = []
        for d in deps:
            if d not in known:
                add("APP002", f"{at}.depends_on", f"'{d}' is not a part of this plan.")
            elif d == p["key"]:
                add("APP003", f"{at}.depends_on", "A part cannot depend on itself.")
            else:
                target = next((x for x in parts if isinstance(x, Mapping) and x.get("key") == d), None)
                if target is not None and target.get("role") != "subworkflow":
                    add("APP006", f"{at}.depends_on", f"'{d}' is an entry part; only sub-workflows can be called.")
        graph[p["key"]] = [d for d in deps if d in known and d != p["key"]]
        for tk in p.get("tables") or []:
            if tk not in tkeys:
                add("APP002", f"{at}.tables", f"'{tk}' is not a table of this plan.")
    cyc = _cycle(graph)
    if cyc:
        add("APP003", "parts", "The parts depend on each other in a cycle: " + " -> ".join(cyc))
    return issues


def _cycle(graph: Mapping[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m) is None:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return None

    for n in graph:
        if state.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


# ── what the composer needs from a plan ───────────────────────────────────────────────────────────────────────────────


def part_of(plan: Mapping[str, Any], key: str) -> dict[str, Any]:
    return next(p for p in plan["parts"] if p["key"] == key)


def build_order(plan: Mapping[str, Any]) -> list[str]:
    """Keys of the parts to build (not the reused ones), every part after the parts it depends on; ties keep the plan's order."""
    parts = [p for p in plan["parts"]]
    reused = {p["key"] for p in parts if p.get("reuse")}
    done: list[str] = []

    def visit(k: str) -> None:
        if k in done:
            return
        for d in part_of(plan, k).get("depends_on") or []:
            if d in {p["key"] for p in parts}:
                visit(d)
        done.append(k)

    for p in parts:
        visit(p["key"])
    return [k for k in done if k not in reused]


def closure(plan: Mapping[str, Any], key: str) -> list[str]:
    """Every part ``key`` depends on, directly or not (a sub-workflow can call a sub-workflow)."""
    out: list[str] = []

    def visit(k: str) -> None:
        for d in part_of(plan, k).get("depends_on") or []:
            if d not in out:
                out.append(d)
                visit(d)

    visit(key)
    return out


#: concepts that only make sense on the scheduled entry: the report's recipients and schedule are not a sub-workflow's or a page's business
_SCHEDULE_ONLY = frozenset({"concept:report", "concept:schedule", "concept:notification", "concept:report_output"})


def focus_concepts(plan: Mapping[str, Any], key: str, confirmed: Iterable[str]) -> list[str]:
    """The confirmed concepts that part ``key`` is responsible for. Understanding is app-wide (its slots and checks cover every concept);
    a part is specified and checked against its own concepts only, so the assign-owner sub-workflow is not asked for an Approval. A part
    left with none keeps one harmless concept (the entity for a sub-workflow, intake for an entry) so it still gets its prompt context."""
    confirmed = list(confirmed)
    part = part_of(plan, key)
    own = [c for c in part.get("concepts") or [] if c in confirmed]
    if part.get("trigger") != "cron":
        own = [c for c in own if c not in _SCHEDULE_ONLY]
    if not own:
        fallback = "concept:intake" if part["role"] == "entry" else "concept:exception_case"
        own = [fallback] if fallback in confirmed else [c for c in part.get("concepts") or [] if c in confirmed][:1]
    return own


def part_brief(plan: Mapping[str, Any], key: str) -> dict[str, Any]:
    """The ``part`` object the spec prompt gets in ``capability_context`` (``part_key`` is the marker fake-LLM scripts match on)."""
    p = part_of(plan, key)
    tables = {t["key"]: t for t in plan.get("tables") or []}
    brief: dict[str, Any] = {
        "part_key": key, "title": p["title"], "role": p["role"], "trigger": p["trigger"], "concepts": list(p.get("concepts") or []),
        "app": plan.get("title"),
        "calls": [f"@part/{d}" for d in p.get("depends_on") or []],
        "tables": [{"name": tables[t]["name"], "columns": [c["name"] for c in tables[t]["columns"]]} for t in p.get("tables") or [] if t in tables],
    }
    if p.get("contract"):
        brief["contract"] = p["contract"]
    if p.get("interface"):
        brief["interface"] = p["interface"]
    for k in ("cron", "recipients_role"):
        if p.get(k):
            brief[k] = p[k]
    return brief


def siblings_of(plan: Mapping[str, Any], key: str) -> dict[str, Any]:
    """The contracts of the parts ``key`` may call, keyed by their virtual id (``kg_checks`` CGX05 reads ``inputs``)."""
    out: dict[str, Any] = {}
    for d in closure(plan, key):
        p = part_of(plan, d)
        c = p.get("contract") or {}
        entry = {"title": p["title"], "inputs": copy.deepcopy(c.get("inputs") or []), "output_fields": list(c.get("output_fields") or [])}
        if p.get("reuse"):
            entry["reuse_workflow_id"] = str(p["reuse"]["capability"]).split(":", 1)[-1]
        out[f"@part/{d}"] = entry
    return out
