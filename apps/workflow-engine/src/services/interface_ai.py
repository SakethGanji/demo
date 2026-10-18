"""'Change the screen...' (Stage 5, DESIGN-STAGE5-UIS section 6.3): the model edits one ScreenSpec.

``edit_screen`` asks the model (Gemini through ``llm_provider.call_llm``) for a new spec, validates it against the
ScreenSpec schema and the binding rules, and allows ONE repair round-trip. The first system line is fixed
(``INTERFACE_AI:EDIT_SCREEN``) so a scripted fake LLM can match it; every test patches ``call_llm`` or scripts the fake.
Nothing is saved here: the route returns ``{spec, summary, warnings}`` and the studio PUTs the interface.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..engine import llm_provider
from ..engine.test_ai import TestAIError, parse_json_object
from ..schemas import interface as schema
from .interface_service import InterfaceError

logger = logging.getLogger(__name__)

SYSTEM_LINE = "INTERFACE_AI:EDIT_SCREEN"
INTERFACE_AI_MODEL = os.environ.get("WORKFLOW_INTERFACE_AI_MODEL") or os.environ.get("WORKFLOW_TEST_AI_MODEL") or "gemini-3.5-flash"
_MAX_OUTPUT_TOKENS = 8000

_RULES = f"""{SYSTEM_LINE}
You edit ONE declarative screen of a workflow interface. Reply with ONE JSON object and nothing else:
{{"spec": <the complete new screen>, "summary": "<one short sentence about what changed>"}}

ScreenSpec v1: {{"v": 1, "title": str, "layout": "card"|"page", "components": [Component, ...]}}
Component types (closed set): {", ".join(schema.COMPONENT_TYPES)}.
Component keys (all optional except type): text, label, bind, value, template, currency, size (sm|md|lg|xl),
tone (neutral|ai|info|success|warn|danger), due, item, columns, approve_label, reject_label, help.
Bindings are plain paths, never code. Roots (closed set):
  input.<field>             a field of the form the person filled in
  node.<Node name>.<path>   the first item of an UPSTREAM node's output (task screens only)
  task.title|status|due_at|assignees|context|form   (task screens only; task.context.<Label> for one context entry)
  result.<path>             the result screen only
  run.id|status|started_at
Templates use {{{{path}}}} with the same roots; {{{{value}}}} is the bound value, list items use their own keys.
Rules: keep every component the user did not ask to change; use only the bindings listed under "available";
links must be https; never invent fields, nodes or component types; at most {schema.MAX_COMPONENTS} components.
"""


def _available(definition: dict[str, Any], interface: dict[str, Any], screen_id: str, node_keys: dict[str, list[str]]) -> dict[str, Any]:
    from ..engine import form_fields as ff

    shape = schema.trigger_input_schema(definition, interface)
    props = (shape or {}).get("properties") or {}
    inputs = {n: props[n].get("type") for n in ff.field_order(shape, None) if n in props} if shape else {}
    out: dict[str, Any] = {"input": inputs, "run": list(schema.RUN_FIELDS)}
    if screen_id.startswith("task:"):
        from ..engine.graph import ancestors

        conns = schema._connections(definition)
        up = ancestors(conns, screen_id[5:])
        out["node"] = {n: node_keys.get(n, []) for n in up if n in {x.get("name") for x in definition.get("nodes") or []}}
        out["task"] = list(schema.TASK_FIELDS)
        node = next((n for n in definition.get("nodes") or [] if n.get("name") == screen_id[5:]), None)
        ctx = ((node or {}).get("parameters") or {}).get("context")
        if isinstance(ctx, dict):
            out["task_context_labels"] = list(ctx)
    if screen_id == "result":
        out["result"] = "the body of the workflow's result node"
    return out


async def _ask(messages: list[dict[str, str]]) -> dict[str, Any]:
    if not llm_provider.is_configured(INTERFACE_AI_MODEL):
        raise InterfaceError(503, "LLM_NOT_CONFIGURED", "AI isn't set up on this engine.")
    try:
        response = await llm_provider.call_llm(
            INTERFACE_AI_MODEL, messages, temperature=0.2, response_format={"type": "json_object"}, max_tokens=_MAX_OUTPUT_TOKENS)
    except Exception as e:   # noqa: BLE001 - provider down, quota, unmatched fake script ...
        logger.warning("interface-ai call failed: %s", type(e).__name__)
        raise InterfaceError(502, "AI_FAILED", f"The model call failed ({type(e).__name__}).") from e
    if getattr(response, "output_truncated", False):
        raise InterfaceError(502, "AI_FAILED", "The model's reply was cut off.")
    try:
        return parse_json_object(response.text)
    except TestAIError as e:
        raise InterfaceError(502, "AI_FAILED", e.message) from e


def _spec_of(reply: dict[str, Any]) -> tuple[Any, str]:
    spec = reply.get("spec") if isinstance(reply.get("spec"), dict) else (reply if "components" in reply else None)
    return spec, str(reply.get("summary") or "").strip()[:500]


def check_spec(spec: Any, definition: dict[str, Any], interface: dict[str, Any], screen_id: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """``(clean_spec, errors, warnings)``. Structural problems and bindings to nodes that don't exist are errors."""
    clean, errs = schema.parse_screen_spec(spec)
    if clean is None:
        return None, [f"{e['field'] or 'spec'}: {e['message']}" for e in errs], []
    probe = {**definition, "interface": {**interface, "kind": "custom", "screens": {screen_id: clean}}}
    errors: list[str] = []
    warnings: list[str] = []
    for f in schema.interface_findings(probe):
        if f["code"] != "WF018":
            continue
        (errors if "no such node" in f["message"] else warnings).append(f["message"])
    return (clean if not errors else None), errors, warnings


async def edit_screen(
    definition: dict[str, Any], interface: dict[str, Any], screen_id: str, instruction: str, spec: dict[str, Any],
    node_keys: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Apply ``instruction`` to ``spec``. Returns ``{spec, summary, warnings}``; raises ``InterfaceError``
    (503 LLM_NOT_CONFIGURED, 502 AI_FAILED, 422 AI_SPEC_INVALID)."""
    if not schema._SCREEN_ID_RE.match(screen_id):
        raise InterfaceError(404, "NOT_FOUND", "unknown screen")
    if interface.get("kind") != "custom":
        raise InterfaceError(409, "WRONG_KIND", "Only a custom interface has screens.")
    if interface.get("app_id"):
        raise InterfaceError(409, "PROMOTED", "This interface was promoted to an app; change it in the app builder.")
    user = json.dumps({
        "instruction": instruction, "screen_id": screen_id, "current_spec": spec,
        "available": _available(definition, interface, screen_id, node_keys or {}),
        "spec_json_schema": schema.screen_spec_json_schema(),
    }, ensure_ascii=False, default=str)
    messages = [{"role": "system", "content": _RULES}, {"role": "user", "content": user}]
    reply = await _ask(messages)
    new_spec, summary = _spec_of(reply)
    clean, errors, warnings = check_spec(new_spec, definition, interface, screen_id)
    if clean is None:
        retry = messages + [
            {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False, default=str)[:20000]},
            {"role": "user", "content": "That screen is not valid:\n- " + "\n- ".join(errors[:12]) +
             "\nReply again with the complete corrected JSON object."}]
        new_spec, summary2 = _spec_of(await _ask(retry))
        summary = summary2 or summary
        clean, errors, warnings = check_spec(new_spec, definition, interface, screen_id)
        if clean is None:
            raise InterfaceError(422, "AI_SPEC_INVALID", "The model's screen wasn't valid.", fields={"reasons": errors[:12]})
    return {"spec": clean, "summary": summary or "Updated the screen.", "warnings": warnings}
