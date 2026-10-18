"""Two product bugs found while building Stage 6 (B2's notes): Code-node escape rewriting and AIAgent output schema key."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine import shapes as sh
from src.engine.contracts import node_contract
from src.engine.node_registry import register_all_nodes
from src.engine.types import NodeDefinition

register_all_nodes()

SCHEMA = {"type": "object", "properties": {"complete": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["complete"]}


# ── AIAgent contract reads outputSchema ─────────────────────────────────────────────────────

def test_aiagent_contract_reads_camelcase_output_schema():
    c = node_contract(NodeDefinition(name="a", type="AIAgent", parameters={"outputSchema": SCHEMA}), {"main": sh.object_of({}, [])})
    main = c["outputs"]["main"]
    # the runtime item is {response, structured: <schema>, ...}, so the schema sits under `structured`
    assert main["type"] == "object" and {"response", "structured"} <= set(main["properties"])
    assert set(main["properties"]["structured"]["properties"]) == {"complete", "reason"}


def test_aiagent_contract_accepts_snake_case_fallback():
    c = node_contract(NodeDefinition(name="a", type="AIAgent", parameters={"output_schema": SCHEMA}), {"main": sh.object_of({}, [])})
    assert set(c["outputs"]["main"]["properties"]["structured"]["properties"]) == {"complete", "reason"}


def test_aiagent_contract_prefers_camelcase_and_stays_unknown_without_schema():
    other = {"type": "object", "properties": {"x": {"type": "string"}}}
    c = node_contract(NodeDefinition(name="a", type="AIAgent", parameters={"outputSchema": SCHEMA, "output_schema": other}), {"main": sh.object_of({}, [])})
    assert "complete" in c["outputs"]["main"]["properties"]["structured"]["properties"]
    c = node_contract(NodeDefinition(name="a", type="AIAgent", parameters={}), {"main": sh.object_of({}, [])})
    assert sh.is_unknown(c["outputs"]["main"])


def test_aiagent_json_string_schema():
    import json
    c = node_contract(NodeDefinition(name="a", type="AIAgent", parameters={"outputSchema": json.dumps(SCHEMA)}), {"main": sh.object_of({}, [])})
    assert "complete" in c["outputs"]["main"]["properties"]["structured"]["properties"]


# ── Code node keeps backslash escapes inside string literals ───────────────────────────────

def _unescape_fns():
    from src.sandbox.code_escapes import unescape_code
    path = Path(__file__).resolve().parents[2] / "src" / "sandbox" / "runtime" / "runner.py"
    spec = importlib.util.spec_from_file_location("sandbox_runner_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [unescape_code, mod.unescape_code]


@pytest.mark.parametrize("fn_index", [0, 1])
def test_string_literal_escapes_are_preserved(fn_index):
    fn = _unescape_fns()[fn_index]
    code = 'x = "a\\nb"\nreturn [new_item({"v": x})]'
    assert fn(code) == code  # real newlines already present: nothing to unescape
    ns = {}
    exec("def f():\n    " + fn(code).replace("\n", "\n    ") + "\nr = f()", {"new_item": lambda d: {"json": d}}, ns)
    assert ns["r"] == [{"json": {"v": "a\nb"}}]


@pytest.mark.parametrize("fn_index", [0, 1])
def test_single_line_code_with_literal_escapes_outside_strings_is_still_unescaped(fn_index):
    fn = _unescape_fns()[fn_index]
    # LLM tool-call JSON that arrived double-escaped: the separators between statements are literal backslash-n
    assert fn('x = 1\\ny = "a\\nb"\\nreturn items') == 'x = 1\ny = "a\\nb"\nreturn items'
    assert fn('if x:\\n\\treturn 1') == 'if x:\n\treturn 1'
    assert fn("x = 'it\\'s'\\ny = 2") == "x = 'it\\'s'\ny = 2"


@pytest.mark.parametrize("fn_index", [0, 1])
def test_comments_and_triple_quotes(fn_index):
    fn = _unescape_fns()[fn_index]
    assert fn("# don't\\nx = 1") == "# don't\nx = 1"
    assert fn('s = """a\\nb"""\\nreturn s') == 's = """a\\nb"""\nreturn s'
    assert fn("return items") == "return items"


def test_in_process_code_node_keeps_string_escapes():
    from src.nodes.data.code import CodeNode
    from src.engine.types import NodeData

    node = CodeNode()
    ctx = SimpleNamespace(node_states={}, execution_id="ex", mode="test", environment="default", project_id=None)
    nd = NodeDefinition(name="c", type="Code", parameters={"code": 'x = "a\\nb"\nreturn [new_item({"v": x, "n": len(x)})]'})
    res = asyncio.run(node._execute_in_process(ctx, nd, [NodeData(json={})]))
    assert res.outputs["main"][0].json == {"v": "a\nb", "n": 3}


def test_in_process_chr_available():
    from src.nodes.data.code import CodeNode
    from src.engine.types import NodeData

    node = CodeNode()
    ctx = SimpleNamespace(node_states={}, execution_id="ex", mode="test", environment="default", project_id=None)
    nd = NodeDefinition(name="c", type="Code", parameters={"code": 'return [new_item({"v": "a" + chr(10) + "b", "o": ord("A")})]'})
    res = asyncio.run(node._execute_in_process(ctx, nd, [NodeData(json={})]))
    assert res.outputs["main"][0].json == {"v": "a\nb", "o": 65}
