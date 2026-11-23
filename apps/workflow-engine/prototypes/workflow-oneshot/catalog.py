"""Render the engine's node registry as a compact reference for the prompt: details, no big examples."""
import json

from src.engine.node_registry import node_registry as NodeRegistry, register_all_nodes

register_all_nodes()

SKIP = {"Sample", "Profile", "Aggregate", "Neo4j", "MongoDB", "ChatInput", "ErrorTrigger", "Html"}  # rarely needed in bank flows


def _prop(p: dict, indent: str = "  ") -> list[str]:
    bits = [p.get("type", "")]
    if p.get("required"):
        bits.append("required")
    d = p.get("default")
    if d not in (None, "", [], {}):
        bits.append("default " + json.dumps(d))
    opts = [o.get("value") for o in p.get("options") or [] if isinstance(o, dict) and "value" in o]
    if opts and p.get("type") in ("options", "multiOptions"):
        bits.append("one of " + "|".join(str(o) for o in opts))
    show = (p.get("displayOptions") or {}).get("show")
    if show:
        bits.append("only when " + ", ".join(f"{k} in {v}" for k, v in show.items()))
    desc = (p.get("description") or "").strip()
    line = f"{indent}- {p['name']} ({', '.join(b for b in bits if b)})" + (f": {desc}" if desc else "")
    out = [line]
    if p.get("type") == "collection" and p.get("properties"):
        out[0] += " -- a list of objects, each with:" if (p.get("typeOptions") or {}).get("multipleValues") or p.get("default") == [] else " -- an object with:"
        for s in p["properties"]:
            out += _prop(s, indent + "    ")
    return out


def render(skip: set[str] = SKIP) -> str:
    lines = []
    for info in NodeRegistry.get_node_info_full():
        if info.type in skip:
            continue
        outs = [o.get("name") for o in info.outputs or []] or ["main"]
        lines.append(f"### {info.type}: {info.description}")
        lines.append(f"  outputs: {', '.join(outs)}" + (" (dynamic)" if info.output_strategy else ""))
        for p in info.properties:
            if p.get("type") == "notice":
                continue
            lines += _prop(p)
    return "\n".join(lines)


if __name__ == "__main__":
    t = render()
    print(t)
    print(f"\n# {len(t)} chars", flush=True)
