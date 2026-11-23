"""Platform index: kit components (props parsed from the .tsx types), SDK symbols, templates, and the tool schemas. Pure, no DB."""

from __future__ import annotations

import asyncio

from src.services import platform_index as pi


def test_search_components_table_with_filters_returns_datatable_first_with_props():
    hits = pi.search_components("table with filters")
    assert hits and hits[0]["name"] == "DataTable"
    props = {p["name"]: p for p in hits[0]["props"]}
    assert {"rows", "columns", "rowKey", "filters", "searchText", "selectable"} <= set(props)
    assert props["rows"]["required"] and props["rowKey"]["required"] and not props["filters"]["required"]
    assert hits[0]["example"].startswith("<DataTable rows={...} columns={...} rowKey={...}")
    assert hits[0]["import"] == "import { DataTable } from './kit'"


def test_props_come_from_inline_and_intersection_types_with_defaults():
    drawer = pi.get_component("Drawer")
    names = {p["name"]: p for p in drawer["props"]}
    assert {"open", "onClose", "title", "children", "width", "tone"} <= set(names)  # `Children & { ... }`
    assert names["open"]["required"] and names["width"]["default"] == "'lg'"
    button = pi.get_component("Button")
    assert {"variant", "size", "loading"} <= {p["name"] for p in button["props"]}


def test_get_component_is_exact_and_case_insensitive():
    assert pi.get_component("datatable")["name"] == "DataTable"
    assert pi.get_component("NoSuchThing") is None


def test_kit_index_is_one_short_line_per_component():
    lines = pi.kit_index_lines()
    assert any(l.startswith("DataTable:") for l in lines)
    assert sum(len(l) + 1 for l in lines) < 6000  # replaces a 5.5 KB hand-written prop list


def test_search_and_get_sdk_symbols():
    assert pi.search_sdk("start run")[0]["name"] == "studio.jobs.start"
    assert pi.get_sdk_symbol("studio.jobs.start")["signature"].startswith("studio.jobs.start(")
    assert pi.get_sdk_symbol("jobs.start")["name"] == "studio.jobs.start"  # suffix is enough when unambiguous
    assert pi.get_sdk_symbol("no.such") is None
    assert pi.search_sdk("insert a row into a table")[0]["name"].startswith("studio.db.table")


def test_templates_are_indexed():
    hits = pi.search_templates("refund approval")
    assert hits and hits[0]["name"] == "refund-triage"
    assert pi.get_template("refund-triage")["summary"]


def test_tool_schemas_are_valid_and_dispatch_without_a_db_for_platform_lookups():
    names = [t["name"] for t in pi.TOOLS]
    assert len(set(names)) == len(names) and {"search_components", "get_component", "search_sdk", "get_sdk_symbol", "get_table_schema",
                                              "get_route_signature"} <= set(names)
    for t in pi.TOOLS:
        assert t["input_schema"]["type"] == "object" and set(t["input_schema"]["required"]) <= set(t["input_schema"]["properties"])
    out = asyncio.run(pi.dispatch_tool(None, "p", "get_component", {"name": "DataTable"}))
    assert out["name"] == "DataTable"
    assert "error" in asyncio.run(pi.dispatch_tool(None, "p", "get_component", {"name": "Nope"}))
    assert "error" in asyncio.run(pi.dispatch_tool(None, "p", "bogus", {}))
