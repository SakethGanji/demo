"""n8n-import placeholders (unmapped nodes, disabled Code nodes) have UNKNOWN output, not passthrough."""
from __future__ import annotations

from src.engine.contracts import node_contract
from src.engine.shapes import is_unknown
from src.engine.types import NodeDefinition
from src.schemas.workflow import NodeDefinitionSchema
from src.services.n8n_import import import_n8n

IN = {"main": {"type": "object", "properties": {"a": {"type": "string"}}}}
PLACEHOLDER = {"outputs": {"main": {"type": "unknown", "x-source": "unknown"}}, "source": "import_placeholder"}


def nd(typ="Set", disabled=True, contract=None, params=None):
    return NodeDefinition(name="N", type=typ, parameters=params or {}, disabled=disabled, contract=contract)


def test_plain_disabled_node_is_still_passthrough():
    out = node_contract(nd(), IN)["outputs"]["main"]
    assert out["properties"] == {"a": {"type": "string"}}


def test_disabled_placeholder_output_is_unknown():
    assert is_unknown(node_contract(nd(contract=PLACEHOLDER), IN)["outputs"]["main"])


def test_disabled_code_placeholder_is_unknown():
    assert is_unknown(node_contract(nd("Code", contract=PLACEHOLDER), IN)["outputs"]["main"])


def test_observed_override_on_disabled_node_does_not_beat_passthrough():
    obs = {"outputs": {"main": {"type": "object", "properties": {"z": {"type": "string"}}}}, "source": "observed"}
    assert "z" not in (node_contract(nd(contract=obs), IN)["outputs"]["main"].get("properties") or {})


def test_schema_accepts_import_placeholder_source():
    n = NodeDefinitionSchema(name="N", type="Set", disabled=True,
                             contract={**PLACEHOLDER, "approved_by": "n8n-import"})
    assert n.to_dataclass().contract["source"] == "import_placeholder"


def _n(name, typ, params=None, **kw):
    return {"name": name, "type": f"n8n-nodes-base.{typ}", "typeVersion": 1, "position": [0, 0],
            "parameters": params or {}, **kw}


def test_importer_stamps_placeholder_on_unmapped_and_code_nodes_only():
    w, _ = import_n8n({"name": "w", "nodes": [_n("T", "manualTrigger"), _n("X", "slack"),
                                              _n("C", "code", {"jsCode": "return items"}), _n("S", "set")],
                       "connections": {"T": {"main": [[{"node": "X", "type": "main", "index": 0}]]},
                                       "X": {"main": [[{"node": "C", "type": "main", "index": 0}]]}}})
    by = {n["name"]: n for n in w["nodes"]}
    for name in ("X", "C"):
        assert by[name]["disabled"] and by[name]["contract"]["source"] == "import_placeholder"
        assert by[name]["contract"]["outputs"]["main"]["type"] == "unknown"
    assert "contract" not in by["T"] and "contract" not in by["S"]
