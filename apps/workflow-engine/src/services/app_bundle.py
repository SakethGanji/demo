"""Compile an application's parts into a ``wf-template/1`` bundle, and the virtual siblings a part is built against
(PLAN-CAPABILITY-GRAPH section 5.3; CONTRACTS-CAPGRAPH.md 5.1).

Pure: no database.

**Virtuals.** While part P is being specified or built, its siblings are not saved anywhere. ``virtuals(plan, P, ...)`` hands the
mistake checker a ``ChildWorkflow`` for every part P calls (``@part/<key>``, published version 1) and the planned tables' columns:

* a sibling that is already built is its proposed definition;
* otherwise it is a stub: an ``ExecuteWorkflowTrigger`` that declares the planned inputs and a ``Set`` that emits the planned
  ``output_fields``, so WF006 / WF021 / WF024 / WF026 all run on the real contract.

**Compile.** ``compile_bundle`` turns the built parts into one bundle: ``@part/<k>`` -> ``@tpl:workflow/<k>``, a planned table's name ->
``@tpl:table/<key>``, a part that reuses an existing sub-workflow -> that workflow's id; sub-workflows are ``publish: true``, entries stay
drafts; ``requires.egress_hosts`` is the literal hosts of the HttpRequest steps; the tests are the approved tests the parts carry. The
result is meant for ``template_bundle.lint_bundle(..., profile="generated")`` and ``TemplateInstaller``.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..schemas.template import BUNDLE_FORMAT
from .template_bundle import (
    DEFINITION_KEYS,
    NODE_KEYS,
    VARS_RE,
    BundleIssue,
    TemplateBundleError,
    bundle_hash,
    host_of,
    iter_strings,
)

PART_PREFIX = "@part/"
_PART_RE = re.compile(r"^@part/([a-z][a-z0-9_]{0,40})$")


class BundleCompileError(TemplateBundleError):
    """The built parts cannot be turned into a bundle (a leftover ``@part/`` reference, a part without a definition)."""


# ── virtual siblings ──────────────────────────────────────────────────────────────────────────────────────────────────


def stub_definition(title: str, contract: Mapping[str, Any] | None) -> dict[str, Any]:
    """A sub-workflow that only declares its planned contract: the trigger's input fields and a Set that emits the output fields."""
    c = contract or {}
    fields = [{"name": i["name"], "type": i.get("type") or "string", "required": bool(i.get("required"))}
              for i in c.get("inputs") or [] if isinstance(i, Mapping) and i.get("name")] or [{"name": "item", "type": "string", "required": False}]
    outs = [str(f) for f in c.get("output_fields") or [] if f] or ["result"]
    return {
        "name": title, "settings": {}, "tests": [], "test_defaults": {},
        "nodes": [
            {"name": "Start", "type": "ExecuteWorkflowTrigger", "position": {"x": 0, "y": 0}, "parameters": {"inputSource": "fields", "fields": fields}},
            {"name": "Planned output", "type": "Set", "position": {"x": 240, "y": 0},
             "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": f, "value": ""} for f in outs]}},
        ],
        "connections": [{"source_node": "Start", "target_node": "Planned output"}],
    }


def virtual_child(project_id: str, key: str, title: str, definition: dict[str, Any]) -> Any:
    from .workflow_checker import ChildWorkflow

    d = copy.deepcopy(definition)
    return ChildWorkflow(id=f"{PART_PREFIX}{key}", name=title, project_id=project_id, draft=d, versions={1: copy.deepcopy(d)},
                         published_version=1, version_numbers={1})


def virtual_tables(plan: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """``{table name: columns}`` in the data-table column format, for the checker's DataTable contracts."""
    return {t["name"]: [{"name": c["name"], "type": c["type"], **({"required": True} if c.get("required") else {})} for c in t["columns"]]
            for t in plan.get("tables") or []}


def virtuals(plan: Mapping[str, Any], key: str, built: Mapping[str, dict[str, Any] | None], project_id: str
             ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """``(virtual_children, virtual_tables)`` for part ``key``. ``built`` maps a part key to its proposed definition (None or absent
    = not built yet, a stub stands in). Reused parts get no child here: the real workflow is already in the project's lookups."""
    from .app_plan import closure, part_of

    children: dict[str, Any] = {}
    for d in closure(plan, key):
        p = part_of(plan, d)
        if p.get("reuse"):
            continue
        children[f"{PART_PREFIX}{d}"] = virtual_child(project_id, d, p["title"], built.get(d) or stub_definition(p["title"], p.get("contract")))
    return children, virtual_tables(plan)


# ── compile ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _replace_sites(defn: dict[str, Any], on_workflow: Any, on_table: Any) -> None:
    """In place: ExecuteWorkflow / WorkflowTool ``workflowId`` and ``settings.error_workflow_id`` go through ``on_workflow``, a DataTable
    ``table`` through ``on_table`` (the same sites the installer replaces tokens at)."""
    for n in defn.get("nodes") or []:
        p = n.get("parameters")
        if not isinstance(p, dict):
            continue
        if n.get("type") in ("ExecuteWorkflow", "WorkflowTool") and isinstance(p.get("workflowId"), str):
            p["workflowId"] = on_workflow(p["workflowId"])
        elif n.get("type") == "DataTable" and isinstance(p.get("table"), str):
            p["table"] = on_table(p["table"])
    s = defn.get("settings")
    if isinstance(s, dict) and isinstance(s.get("error_workflow_id"), str):
        s["error_workflow_id"] = on_workflow(s["error_workflow_id"])


def _clean_definition(raw: Mapping[str, Any]) -> dict[str, Any]:
    d = {k: copy.deepcopy(v) for k, v in raw.items() if k in DEFINITION_KEYS}
    d["nodes"] = [{k: v for k, v in n.items() if k in NODE_KEYS} for n in d.get("nodes") or [] if isinstance(n, dict)]
    d.setdefault("connections", [])
    d.setdefault("settings", {})
    return d


def _report_screen(defn: dict[str, Any]) -> None:
    """A custom report page shows the report's rows in a table (the ``report_table`` screen of the report recipe). The generated screen
    stays when the table version does not validate."""
    from ..schemas.interface import parse_interface

    iface = defn.get("interface")
    if not isinstance(iface, dict) or iface.get("kind") != "custom":
        return
    trial = copy.deepcopy(iface)
    result = trial.setdefault("screens", {}).setdefault("result", {"v": 1, "title": "Result", "layout": "card", "components": []})
    comps = [c for c in result.get("components") or [] if isinstance(c, dict)]
    if any(c.get("type") == "table" for c in comps):
        return
    result["components"] = [{"type": "heading", "text": trial.get("title") or "Report"}, {"type": "table", "bind": "result.rows"}] + [
        c for c in comps if c.get("type") not in ("heading", "result")]
    if not parse_interface(trial)[1]:
        iface.clear()
        iface.update(trial)


def _generate_interface(defn: dict[str, Any], title: str, kind: str, slug: str | None) -> None:
    """The Stage 5 starting screens for ``defn`` (a no-op when the definition has no matching trigger node)."""
    from ..schemas.interface import generate_interface

    try:
        defn["interface"] = generate_interface(defn, title, kind, slug)
    except Exception:  # noqa: BLE001 - an app without its page is still an app; the person can add the screen in the editor
        defn.pop("interface", None)


def egress_hosts(defns: Mapping[str, dict[str, Any]]) -> list[str]:
    """The literal hosts of the HttpRequest steps that call a URL (an ``api_ref`` call uses the API's own host)."""
    hosts: list[str] = []
    for d in defns.values():
        for n in d.get("nodes") or []:
            if n.get("type") != "HttpRequest":
                continue
            p = n.get("parameters") or {}
            if isinstance(p.get("api_ref"), dict) and p["api_ref"].get("spec_id"):
                continue
            h = host_of(p.get("url") or "") if isinstance(p.get("url"), str) else None
            if h and h not in hosts:
                hosts.append(h)
    return hosts


def compile_bundle(app_id: str, plan: Mapping[str, Any], request: str, built: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The raw ``wf-template/1`` bundle for the app. ``built`` maps every built part key to its proposed definition. Raises
    ``BundleCompileError`` when a part has no definition or a ``@part/`` reference is left over."""
    from .app_plan import build_order

    order = build_order(plan)
    missing = [k for k in order if not built.get(k)]
    if missing:
        raise BundleCompileError([BundleIssue("APP_PART_MISSING", f"parts.{k}", f"'{k}' has no built workflow") for k in missing])
    reuse = {p["key"]: str(p["reuse"]["capability"]).split(":", 1)[-1] for p in plan["parts"] if p.get("reuse")}
    table_by_name = {t["name"]: t["key"] for t in plan.get("tables") or []}
    kind_of = {p["key"]: p["role"] for p in plan["parts"]}
    by_key = {p["key"]: p for p in plan["parts"]}

    def on_workflow(v: str) -> str:
        m = _PART_RE.match(v)
        if not m:
            return v
        k = m.group(1)
        if k in reuse:
            return reuse[k]
        return f"@tpl:workflow/{k}" if k in order else v

    def on_table(v: str) -> str:
        return f"@tpl:table/{table_by_name[v]}" if v in table_by_name else v

    workflows, defns, names = [], {}, set()
    issues: list[BundleIssue] = []
    for k in order:
        part = by_key[k]
        d = _clean_definition(built[k])
        _replace_sites(d, on_workflow, on_table)
        want = part.get("interface") or {}
        if want.get("kind") and (not isinstance(d.get("interface"), dict) or d["interface"].get("kind") != want["kind"]):
            _generate_interface(d, part["title"], want["kind"], want.get("slug"))   # the spec can only ask for form / chat; a custom page is made here
        iface = d.get("interface")
        if isinstance(iface, dict) and want.get("slug"):
            iface["slug"] = want["slug"]
        if (part.get("interface") or {}).get("screen") == "report_table":
            _report_screen(d)
        for path, s in iter_strings(d):
            if PART_PREFIX in s:
                issues.append(BundleIssue("APP_PART_REF", f"workflows.{k}.definition.{path}", f"'{s}' still names a part that was not turned into a workflow reference"))
        name = part["title"][:255]
        while name in names:
            name = f"{part['title'][:240]} ({len(names) + 1})"
        names.add(name)
        defns[k] = d
        workflows.append({"key": k, "role": kind_of[k], "name": name, "description": f"Part '{part['title']}' of the app '{plan.get('title')}'.",
                          "publish": kind_of[k] == "subworkflow", "definition": d})
    if issues:
        raise BundleCompileError(issues)

    used_vars = sorted({m.group(1) for d in defns.values() for _p, s in iter_strings(d) for m in VARS_RE.finditer(s)})
    tables = [{"key": t["key"], "name": t["name"], "description": f"Records of the app '{plan.get('title')}'.",
               "columns": [{"name": c["name"], "type": c["type"], "required": bool(c.get("required"))} for c in t["columns"]]}
              for t in plan.get("tables") or []]
    short = app_id.removeprefix("ab_")
    raw: dict[str, Any] = {
        "format": BUNDLE_FORMAT, "slug": f"app-{short}"[:60], "version": "1.0.0",
        "name": str(plan.get("title") or "Application")[:120], "summary": (request or str(plan.get("title") or "Application")).strip()[:500] or "Application",
        "category": "application", "tags": ["app", "generated"], "min_engine_stage": 8,
        "requires": {"templates": [], "secrets": [], "variables": [{"name": v, "type": "string"} for v in used_vars],
                     "egress_hosts": egress_hosts(defns)},
        "apis": [], "tables": tables, "workflows": workflows, "ci": {"min_tests_per_workflow": 1},
        "app": {"title": str(plan.get("title") or "Application")[:120], "build_id": app_id},
    }
    return raw


def loaded_template(raw: dict[str, Any]) -> Any:
    """Lint a compiled bundle with the ``generated`` profile and wrap it for the installer. Raises ``TemplateBundleError``."""
    from .template_bundle import LoadedTemplate, validate_bundle

    bundle, issues = validate_bundle(raw, {}, expected_slug=raw.get("slug"), profile="generated")
    if issues:
        raise TemplateBundleError(issues, where="app bundle")
    return LoadedTemplate(bundle=bundle, raw=raw, api_texts={}, readme="", hash=bundle_hash(raw, {}))
