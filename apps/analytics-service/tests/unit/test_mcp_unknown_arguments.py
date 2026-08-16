"""``UnknownArgumentGuard``: an undeclared tool argument fails the call.

The defect it exists for is a *silent success*. The MCP SDK builds each tool's
argument model with pydantic's default ``extra="ignore"``, so
``query_rows(dataset_id=…, limitt=1)`` drops ``limitt``, applies the default
limit, and returns ``isError: false``. The caller gets a confident answer to a
question they did not ask, and a model has nothing to self-correct from.

Driven against a hand-built server here rather than the mounted endpoint: what
is under test is which argument keys are accepted for which declared schema,
and that is not an HTTP behaviour. ``tests/test_mcp_endpoint.py`` covers the
same guard through the real transport, with the real 27 tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from app.features.mcp.strict_args import UnknownArgumentGuard


@dataclass
class _Ctx:
    """Just enough of ``ServerRequestContext`` for the guard."""

    method: str
    params: Any


@pytest.fixture(scope="module")
def server() -> MCPServer:
    mcp: MCPServer = MCPServer(name="test")

    @mcp.tool(name="fetch", description="one required, two optional")
    async def fetch(
        dataset_id: Annotated[str, Field(description="required")],
        limit: Annotated[int | None, Field(description="optional")] = None,
        sheet: Annotated[str | None, Field(description="optional")] = None,
    ) -> str:
        return f"{dataset_id}/{limit}/{sheet}"

    @mcp.tool(name="ping", description="takes nothing at all")
    async def ping() -> str:
        return "pong"

    return mcp


@pytest.fixture
def guard(server) -> UnknownArgumentGuard:
    return UnknownArgumentGuard(server)


async def _call(guard, name, arguments, *, method="tools/call"):
    """Run the guard; return ``("passed", ctx)`` or ``("rejected", text)``."""
    seen: list[Any] = []

    async def call_next(ctx):
        seen.append(ctx)
        return {"content": [{"type": "text", "text": "ran"}], "isError": False}

    result = await guard(_Ctx(method=method, params={"name": name, "arguments": arguments}),
                         call_next)
    if seen:
        return "passed", seen[0]
    return "rejected", "".join(b.text for b in result.content)


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


async def test_a_misspelled_argument_is_rejected_not_dropped(guard):
    outcome, text = await _call(guard, "fetch", {"dataset_id": "d1", "limitt": 5})
    assert outcome == "rejected"
    assert "limitt" in text
    # One step to a correct retry: the likely fix, then the whole vocabulary.
    assert "did you mean 'limit'" in text
    assert "Valid arguments: dataset_id, limit, sheet." in text
    # And it must be unambiguous that the tool did not run — the whole failure
    # mode is a result that looks like it did.
    assert "Nothing ran." in text


async def test_the_rejection_is_a_tool_error_not_a_protocol_error(guard):
    """Same shape the SDK gives a ToolError, so clients need one code path."""
    async def call_next(ctx):  # pragma: no cover - must not run
        raise AssertionError("the tool ran")

    result = await guard(
        _Ctx(method="tools/call", params={"name": "ping", "arguments": {"x": 1}}),
        call_next,
    )
    assert result.is_error is True
    assert [b.type for b in result.content] == ["text"]


async def test_every_unknown_key_is_named_in_one_message(guard):
    """A model that has to discover its mistakes one round trip at a time is
    being charged for the guard rather than helped by it."""
    outcome, text = await _call(guard, "fetch", {"dataset_id": "d1", "limitt": 5, "zzzz": 1})
    assert outcome == "rejected"
    assert "limitt" in text and "zzzz" in text
    assert "Unknown arguments" in text          # plural
    assert "did you mean 'limit'" in text
    # No suggestion is invented for a key that resembles nothing.
    assert "'zzzz' (did you mean" not in text


async def test_a_tool_declaring_no_arguments_accepts_none(guard):
    outcome, text = await _call(guard, "ping", {"anything": 1})
    assert outcome == "rejected"
    assert "This tool takes no arguments." in text
    # `properties: {}` is a positive declaration, not an absent one.
    assert "Valid arguments" not in text


# ---------------------------------------------------------------------------
# What must keep working
# ---------------------------------------------------------------------------


async def test_a_fully_correct_call_is_untouched(guard):
    outcome, ctx = await _call(guard, "fetch", {"dataset_id": "d1", "limit": 5, "sheet": "s"})
    assert outcome == "passed"
    assert ctx.params["arguments"] == {"dataset_id": "d1", "limit": 5, "sheet": "s"}


async def test_omitting_optional_arguments_is_not_an_unknown_argument(guard):
    """The common case: two of the three parameters legitimately left out."""
    assert (await _call(guard, "fetch", {"dataset_id": "d1"}))[0] == "passed"
    assert (await _call(guard, "fetch", {"dataset_id": "d1", "sheet": "s"}))[0] == "passed"
    assert (await _call(guard, "ping", {}))[0] == "passed"


async def test_a_missing_required_argument_is_still_the_sdks_to_report(guard):
    """This guard answers one question — is the key declared. Required-ness,
    types and values stay with the arg model, which reports them better."""
    assert (await _call(guard, "fetch", {"limit": 5}))[0] == "passed"


async def test_an_unknown_tool_name_is_passed_through(guard):
    """METHOD-level errors are the SDK's; a second copy here would drift."""
    assert (await _call(guard, "no_such_tool", {"whatever": 1}))[0] == "passed"


async def test_other_methods_are_not_inspected(guard):
    outcome, _ = await _call(guard, "fetch", {"bogus": 1}, method="tools/list")
    assert outcome == "passed"


@pytest.mark.parametrize("params", [None, "not-a-mapping", {"name": 7, "arguments": {}},
                                    {"name": "fetch", "arguments": "not-a-mapping"},
                                    {"name": "fetch"}],
                         ids=["none", "scalar", "name-not-str", "args-not-dict", "no-args"])
async def test_malformed_params_are_left_to_the_sdks_validation(guard, params):
    """Anything this malformed is a protocol-level fault with a better error
    downstream; the guard must not turn it into an argument-name complaint."""
    seen = []

    async def call_next(ctx):
        seen.append(ctx)
        return None

    await guard(_Ctx(method="tools/call", params=params), call_next)
    assert seen, params


# ---------------------------------------------------------------------------
# Schemas that do not close their inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema", [
    {"type": "object", "additionalProperties": True, "properties": {"a": {}}},
    {"type": "object", "additionalProperties": {"type": "string"}, "properties": {"a": {}}},
    {"type": "object"},
], ids=["additional-true", "additional-subschema", "no-properties-block"])
def test_an_open_schema_is_not_policed(schema):
    """A tool that declares extra keys meaningful, or declares nothing at all,
    is passed through: rejecting there would contradict what ``tools/list``
    publishes, or would produce an error with no vocabulary to offer."""
    assert UnknownArgumentGuard._properties(schema) is None


def test_an_empty_properties_block_is_a_real_declaration():
    """Not the same as an absent one — it says 'this tool takes nothing'."""
    assert UnknownArgumentGuard._properties({"type": "object", "properties": {}}) == []


async def test_declared_names_come_from_the_servers_own_registry(server, guard):
    """No hand-maintained copy: the guard's vocabulary is whatever the server
    publishes, so a tool signature change cannot leave the guard behind."""
    published = {t.name: sorted((t.input_schema or {}).get("properties", {}))
                 for t in await server.list_tools()}
    assert await guard._schemas() == published
