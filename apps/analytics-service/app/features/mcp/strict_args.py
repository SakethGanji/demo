"""Reject unknown tool arguments instead of silently dropping them.

The MCP SDK builds one pydantic model per tool from the tool function's
signature, and that model inherits pydantic's default ``extra="ignore"``
(``ArgModelBase.model_config`` in ``mcp/server/mcpserver/utilities/func_metadata``
sets only ``arbitrary_types_allowed``). So a misspelled parameter never reaches
the tool and never produces an error:

    query_rows(dataset_id=..., limitt=1)   ->  isError: false, 200 rows

``limitt`` is dropped, ``limit`` keeps its default, and the caller is handed a
confident answer to a question they did not ask. That is the worst failure mode
a tool surface has — worse than an error, because nothing signals it. A model
cannot self-correct from a success.

This was twice written off as "not interceptable without patching the SDK".
It is not. ``MCPServer`` accepts a ``middleware`` sequence (and exposes the
composed list as ``MCPServer.middleware``) whose members wrap every inbound
JSON-RPC message *before* params validation, so the raw ``arguments`` dict is
readable exactly as the client sent it — extra keys included. The same seam
:mod:`app.features.mcp.identity` already uses to bind the acting caller.

What this does NOT do:

* **Unknown tool names.** The SDK already answers those, and a second copy of
  that check would drift from it. A name this guard does not recognise is
  passed straight through.
* **Types, required-ness, or values.** Still the SDK's arg model. This guard
  only answers "is this key declared at all", which is the one question the arg
  model refuses to ask.

Declared arguments come from the server's own tool registry
(``MCPServer.list_tools()``), never a hand-maintained list here — a second copy
of the tool surface is precisely the drift this guard exists to prevent.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

from mcp_types import CallToolResult, TextContent

logger = logging.getLogger(__name__)

CALL_METHOD = "tools/call"

#: Cutoff for the "did you mean" hint. 0.6 is difflib's own default: high
#: enough that an unrelated name is not suggested, low enough to catch a
#: transposition, a doubled letter, or a missing underscore.
_SUGGESTION_CUTOFF = 0.6


def _text_result(message: str) -> CallToolResult:
    """The same error kind a ``ToolError`` produces, so callers get one shape.

    ``is_error=True`` with text, not a JSON-RPC error: the spec reserves
    protocol errors for "could not find the tool", and says a fault in the call
    itself SHOULD come back inside the result "so the LLM can see and
    self-correct" — which is exactly the point here. It is also what
    ``MCPServer._handle_call_tool`` already renders for a ``ToolError`` and for
    the arg model's own validation failures, so a client needs no second code
    path for what is, to it, one thing: the tool did not run, and here is why.

    Returning rather than raising short-circuits the chain. ``ServerRunner``
    documents that a middleware which skips ``call_next`` "owns its result,
    envelope included", so this bypasses ``_serialize``: the per-version field
    sieve and the 2026-era ``serverInfo`` ``_meta`` stamp do not run. The one
    visible consequence is that ``resultType: "complete"`` is emitted even on a
    pre-2026 connection, where the sieve would have dropped it — the SDK's own
    model documents that field as "always serialized; older peers ignore it",
    and modern peers MUST receive it, so emitting it unconditionally is the
    forward-correct side to err on.
    """
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _reject(tool_name: str, unknown: list[str], declared: list[str]) -> CallToolResult:
    """Name every offending key, suggest a fix, and list the valid vocabulary.

    Written to be correctable in one step, which is the design principle the
    whole tool surface is built on (see ``_common.explain``): the caller is told
    what was wrong, what it probably should have been, what is allowed, and that
    nothing ran.
    """
    parts = []
    for key in unknown:
        close = difflib.get_close_matches(key, declared, n=1, cutoff=_SUGGESTION_CUTOFF)
        parts.append(f"'{key}' (did you mean '{close[0]}'?)" if close else f"'{key}'")

    noun = "argument" if len(unknown) == 1 else "arguments"
    valid = (
        f"Valid arguments: {', '.join(declared)}."
        if declared
        else "This tool takes no arguments."
    )
    return _text_result(
        f"Unknown {noun} for tool '{tool_name}': {', '.join(parts)}. {valid} "
        "Nothing ran. An unrecognised argument would otherwise be dropped before the "
        "tool sees it, and the answer you got back would be to a different question "
        "than the one you asked — so resend the call with the name corrected or removed."
    )


class UnknownArgumentGuard:
    """``ServerMiddleware`` that fails a ``tools/call`` carrying undeclared keys.

    Holds the server rather than a copy of its schemas, and reads the declared
    property names out of it on first use. The tool set is fixed at
    :func:`app.features.mcp.server.build` time — every ``register()`` call has
    run before this guard is installed, and nothing adds a tool afterwards — so
    the snapshot cannot go stale in this service. It is a snapshot, though: a
    tool registered at runtime would be treated as unknown and passed through,
    which fails open rather than rejecting a legitimate call.
    """

    def __init__(self, server: Any) -> None:
        self._server = server
        self._declared: dict[str, list[str] | None] | None = None

    async def _schemas(self) -> dict[str, list[str] | None]:
        """``{tool_name: sorted declared property names, or None to skip}``.

        ``None`` means "this tool's schema does not close its input, so do not
        police it" — see :meth:`_properties`.
        """
        if self._declared is None:
            self._declared = {
                tool.name: self._properties(tool.input_schema or {})
                for tool in await self._server.list_tools()
            }
        return self._declared

    @staticmethod
    def _properties(schema: dict[str, Any]) -> list[str] | None:
        """Declared argument names, or ``None`` when the schema is open.

        Two deliberate choices about schemas that do not pin their inputs:

        ``additionalProperties`` truthy (``True``, or a sub-schema)
            Skipped. The tool has *declared* that keys beyond ``properties``
            are meaningful, and rejecting them would contradict the contract
            the server publishes in ``tools/list``. Every tool here is
            generated from a fixed Python signature and none of them set it,
            so nothing in this service takes this branch today; it exists so a
            future open-ended tool does not have to know about this guard.

        no ``properties`` block at all
            Skipped. There is no declared vocabulary to check against and none
            to offer in the error, so a rejection could only say "that is
            wrong" without saying what is right — useless to a model trying to
            correct itself. Note this is NOT the same as an empty
            ``properties: {}``, which *is* a positive declaration that the tool
            takes nothing (``whoami``) and is enforced as such: any argument to
            it is rejected.
        """
        if schema.get("additionalProperties"):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        return sorted(properties)

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method != CALL_METHOD:
            return await call_next(ctx)

        params = ctx.params
        if not isinstance(params, dict):
            return await call_next(ctx)
        name, arguments = params.get("name"), params.get("arguments")
        # Anything malformed enough that these are not the expected types is
        # the SDK's params validation to report, not ours.
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return await call_next(ctx)

        declared = (await self._schemas()).get(name)
        if declared is None:
            # Either an unknown tool (the SDK's error to raise) or an
            # open/undeclared schema. Both pass through.
            return await call_next(ctx)

        unknown = [key for key in arguments if key not in declared]
        if not unknown:
            return await call_next(ctx)

        logger.info("rejected tools/call %s: undeclared arguments %s", name, unknown)
        return _reject(name, unknown, declared)
