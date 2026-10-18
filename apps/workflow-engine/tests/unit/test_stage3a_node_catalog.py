from src.engine.effects import NODE_EFFECTS
from src.engine.node_registry import node_registry, register_all_nodes
from src.services.node_catalog import build_node_catalog

register_all_nodes()


def test_catalog_covers_every_registered_type():
    cat = build_node_catalog()
    assert {c["type"] for c in cat} == set(node_registry.list())
    for c in cat:
        assert set(c) == {"type", "display_name", "effect", "parameters", "contract_kind", "recipe", "example"}
        assert c["effect"] == NODE_EFFECTS[c["type"]]
        assert c["contract_kind"] in ("derived", "declared", "observed")
        assert set(c["recipe"]) == {"checks", "categories"}
        assert c["example"]["type"] == c["type"] and isinstance(c["example"]["parameters"], dict)


def test_catalog_entries():
    cat = {c["type"]: c for c in build_node_catalog()}
    assert cat["Set"]["contract_kind"] == "derived" and cat["Webhook"]["contract_kind"] == "declared"
    assert cat["HttpRequest"]["recipe"]["categories"][-1] == "ext_empty"
    assert cat["AIAgent"]["recipe"] == {"checks": [], "categories": []}
    assert any(p["name"] == "condition" for p in cat["If"]["parameters"])
