"""From "this agent is bound to these connectors" to a list of callable tools.

This is the seam the agent runtime calls. It reads the *selected* rows for each
bound connector, rebuilds the connector object, and returns tools in the
engine's one tool shape.

Two rules are enforced here rather than at discovery, because they are only
knowable once several connectors are in the same list:

* **Import is not selection.** Discovery writes every row with
  ``selected=False``. Nothing here loads a row that has not been selected, so a
  freshly discovered 200-tool server contributes nothing to any agent's context
  until somebody chooses.
* **Cross-connector name collisions.** Two servers may each publish ``search``.
  Names are unique per connector, not globally, so the second one is renamed
  deterministically and the rename logged, rather than one tool silently
  shadowing the other in the dict the agent loop builds.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.models import ConnectorToolModel, ToolConnectorModel
from .base import CallerIdentity, ToolManifestEntry
from .mcp.connector import MCPConnector
from .naming import derive_tool_name

logger = logging.getLogger(__name__)

Executor = Callable[..., Awaitable[Any]]

_CONNECTOR_CLASSES: dict[str, type[MCPConnector]] = {"mcp": MCPConnector}


def normalize_bindings(bindings: Any) -> list[dict[str, Any]]:
    """Accept the several shapes a binding is stored in, return one.

    ``["conn_1"]``, ``[{"connector_id": "conn_1", "tools": ["a", "b"]}]`` and
    ``{"conn_1": ["a"]}`` all mean something reasonable; a caller should not have
    to care which the agent record happens to hold.
    """
    if not bindings:
        return []
    if isinstance(bindings, dict):
        return [
            {"connector_id": key, "tools": value if isinstance(value, list) else None}
            for key, value in bindings.items()
        ]
    if isinstance(bindings, str):
        return [{"connector_id": bindings, "tools": None}]

    out: list[dict[str, Any]] = []
    for item in bindings:
        if isinstance(item, str):
            out.append({"connector_id": item, "tools": None})
        elif isinstance(item, dict):
            connector_id = item.get("connector_id") or item.get("id")
            if not connector_id:
                continue
            tools = item.get("tools") or item.get("tool_names")
            out.append(
                {
                    "connector_id": str(connector_id),
                    "tools": list(tools) if isinstance(tools, (list, tuple)) else None,
                }
            )
    return out


def entry_from_row(row: ConnectorToolModel) -> ToolManifestEntry:
    return ToolManifestEntry(
        remote_id=row.remote_id,
        tool_name=row.tool_name,
        description=row.description or "",
        input_schema=row.input_schema or {},
        optional_args=list(row.optional_args or []),
        invoke=row.invoke or {},
        read_only=bool(row.read_only),
        unsupported_reason=row.unsupported_reason,
        schema_hash=row.schema_hash or "",
        est_tokens=row.est_tokens or 0,
    )


def connector_from_row(
    row: ToolConnectorModel, *, identity: CallerIdentity | None = None
) -> MCPConnector:
    cls = _CONNECTOR_CLASSES.get(row.kind)
    if cls is None:
        raise ValueError(f"connector kind {row.kind!r} is not implemented")
    return cls(
        connector_id=row.id,
        name=row.name,
        base_url=row.base_url,
        headers=dict(row.headers or {}),
        tool_prefix=row.tool_prefix or "",
        config=dict(row.config or {}),
        identity=identity or CallerIdentity(),
    )


async def load_selected_entries(
    session: AsyncSession, connector_id: str, *, only: Sequence[str] | None = None
) -> list[ConnectorToolModel]:
    stmt = (
        select(ConnectorToolModel)
        .where(ConnectorToolModel.connector_id == connector_id)
        .where(ConnectorToolModel.selected.is_(True))
        .where(ConnectorToolModel.removed_at.is_(None))
        .order_by(ConnectorToolModel.tool_name)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if only is not None:
        wanted = set(only)
        rows = [r for r in rows if r.tool_name in wanted or r.remote_id in wanted]
    return rows


async def resolve_connector_tools(
    bindings: Any,
    session: AsyncSession,
    *,
    identity: CallerIdentity | None = None,
    team_id: str | None = None,
    taken_names: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Executor]]:
    """Build the tool list for a set of connector bindings.

    Returns ``(tools, executors)`` where ``tools`` are engine tool dicts and
    ``executors`` maps tool name to the same coroutine, for callers that want to
    dispatch by name without re-scanning the list.
    """
    bundle = await resolve_connector_bundle(
        bindings, session, identity=identity, team_id=team_id, taken_names=taken_names
    )
    return bundle["tools"], bundle["executors"]


async def resolve_connector_bundle(
    bindings: Any,
    session: AsyncSession,
    *,
    identity: CallerIdentity | None = None,
    team_id: str | None = None,
    taken_names: Iterable[str] = (),
) -> dict[str, Any]:
    """As :func:`resolve_connector_tools`, plus the metadata a prompt wants.

    ``instructions`` is a list of ``{connector, text}``: the server's own
    ``instructions`` string from ``initialize``, stored at discovery. It tells
    an agent how the tools fit together — which one to start from, what a "not
    found" really means — and is worth more than any individual description.
    """
    specs = normalize_bindings(bindings)
    tools: list[dict[str, Any]] = []
    executors: dict[str, Executor] = {}
    instructions: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    used: set[str] = set(taken_names)

    for spec in specs:
        connector_id = spec["connector_id"]
        row = await session.get(ToolConnectorModel, connector_id)
        if row is None:
            skipped.append({"connector_id": connector_id, "reason": "not found"})
            continue
        if team_id is not None and row.team_id != team_id:
            skipped.append({"connector_id": connector_id, "reason": "belongs to another team"})
            continue
        if not row.enabled:
            skipped.append({"connector_id": connector_id, "reason": "disabled"})
            continue

        try:
            connector = connector_from_row(row, identity=identity)
        except ValueError as exc:
            skipped.append({"connector_id": connector_id, "reason": str(exc)})
            continue

        entries = [
            entry_from_row(r)
            for r in await load_selected_entries(session, connector_id, only=spec["tools"])
        ]
        if not entries:
            skipped.append({"connector_id": connector_id, "reason": "no tools selected"})
            continue

        built = connector.build_tools(entries, identity=identity)
        for tool in built:
            name = tool["name"]
            if name in used:
                new_name = derive_tool_name(
                    f"{row.name}:{name}", taken=used, prefix=""
                )
                logger.warning(
                    "connector %s: tool name %r already bound; exposing it as %r",
                    row.name, name, new_name,
                )
                tool["name"] = new_name
                name = new_name
            used.add(name)
            tools.append(tool)
            executors[name] = tool["execute"]

        if row.instructions:
            instructions.append({"connector": row.name, "text": row.instructions})

    return {
        "tools": tools,
        "executors": executors,
        "instructions": instructions,
        "skipped": skipped,
        "est_tokens": sum(
            len(t["description"]) // 4 + len(str(t["input_schema"])) // 4 for t in tools
        ),
    }
