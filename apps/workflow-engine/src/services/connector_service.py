"""Connector lifecycle: register, discover, select, test.

The one rule this file exists to enforce is that **importing a tool is not
selecting it**. A discovery run against a busy MCP server writes a hundred rows;
every one of them lands with ``selected=False``. Selection is a separate,
explicit act, because every selected tool's schema is serialized into the
model's context on every single call — an unselected import costs nothing, and
an accidental one costs the agent its attention budget.

Re-discovery is therefore careful about what it preserves:

* ``tool_name`` is **pinned** at first import. A transcript, a saved agent
  binding and an operator's memory all reference the name.
* ``selected`` survives. An upstream release that adds five tools must not
  silently deselect the two an agent depends on, nor select the new ones.
* A tool that has disappeared upstream is tombstoned (``removed_at``), not
  deleted, so its selection comes back if the server does.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ConnectorToolModel, ToolConnectorModel
from ..nodes.ai.connectors.base import CallerIdentity, ConnectorError
from ..nodes.ai.connectors.egress import check_egress
from ..nodes.ai.connectors.envelope import shape_result
from ..nodes.ai.connectors.registry import connector_from_row, entry_from_row
from ..utils.ids import connector_id as new_connector_id

logger = logging.getLogger(__name__)

SUPPORTED_KINDS = ("mcp",)


class ConnectorNotFoundError(Exception):
    def __init__(self, connector_id: str) -> None:
        super().__init__(f"Connector '{connector_id}' not found")
        self.message = f"Connector '{connector_id}' not found"


class ConnectorConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConnectorService:
    """All connector reads and writes. One session, one request."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- CRUD -------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        kind: str = "mcp",
        team_id: str = "default",
        tool_prefix: str = "",
        headers: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        spec_url: str | None = None,
    ) -> ToolConnectorModel:
        if kind not in SUPPORTED_KINDS:
            raise ConnectorConflictError(
                f"kind '{kind}' is not supported yet (have: {', '.join(SUPPORTED_KINDS)})"
            )
        config = dict(config or {})
        # Fail at registration, not at the first agent turn.
        check_egress(base_url, allow_private=bool(config.get("allow_private_hosts", True)))

        existing = await self.session.execute(
            select(ToolConnectorModel)
            .where(ToolConnectorModel.team_id == team_id)
            .where(ToolConnectorModel.name == name)
        )
        if existing.scalars().first() is not None:
            raise ConnectorConflictError(
                f"a connector named '{name}' already exists in team '{team_id}'"
            )

        row = ToolConnectorModel(
            id=new_connector_id(),
            team_id=team_id,
            name=name,
            kind=kind,
            base_url=base_url.strip(),
            spec_url=spec_url,
            tool_prefix=tool_prefix or "",
            config=config,
            selection={},
            headers=dict(headers or {}),
            enabled=True,
            status="pending",
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list(
        self, *, team_id: str | None = "default", kind: str | None = None
    ) -> list[ToolConnectorModel]:
        stmt = select(ToolConnectorModel)
        if team_id is not None:
            stmt = stmt.where(ToolConnectorModel.team_id == team_id)
        if kind:
            stmt = stmt.where(ToolConnectorModel.kind == kind)
        stmt = stmt.order_by(ToolConnectorModel.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, connector_id: str) -> ToolConnectorModel:
        row = await self.session.get(ToolConnectorModel, connector_id)
        if row is None:
            raise ConnectorNotFoundError(connector_id)
        return row

    async def update(self, connector_id: str, patch: dict[str, Any]) -> ToolConnectorModel:
        row = await self.get(connector_id)
        for field in ("name", "base_url", "tool_prefix", "enabled", "spec_url"):
            if field in patch and patch[field] is not None:
                setattr(row, field, patch[field])
        for field in ("headers", "config", "selection"):
            if field in patch and patch[field] is not None:
                setattr(row, field, dict(patch[field]))
        if "base_url" in patch and patch["base_url"]:
            check_egress(
                row.base_url,
                allow_private=bool((row.config or {}).get("allow_private_hosts", True)),
            )
        row.updated_at = datetime.now()
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, connector_id: str) -> None:
        row = await self.get(connector_id)
        # Tools go with it: the FK is ON DELETE CASCADE, but ORM-level deletes
        # do not fire it for rows this session has not loaded.
        await self.session.execute(
            ConnectorToolModel.__table__.delete().where(
                ConnectorToolModel.connector_id == connector_id
            )
        )
        await self.session.delete(row)
        await self.session.commit()

    # -- discovery --------------------------------------------------------

    async def discover(
        self, connector_id: str, *, identity: CallerIdentity | None = None
    ) -> dict[str, Any]:
        row = await self.get(connector_id)
        connector = connector_from_row(row, identity=identity)

        existing_rows = list(
            (
                await self.session.execute(
                    select(ConnectorToolModel).where(
                        ConnectorToolModel.connector_id == connector_id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_remote = {r.remote_id: r for r in existing_rows}
        pinned = {r.remote_id: r.tool_name for r in existing_rows}

        started = time.monotonic()
        try:
            manifest = await connector.discover(identity=identity, pinned=pinned)
        except ConnectorError as exc:
            row.status = "error"
            row.last_error = exc.message[:2000]
            row.updated_at = datetime.now()
            await self.session.commit()
            raise
        finally:
            await connector.aclose()

        now = datetime.now()
        added, updated, changed = 0, 0, 0
        seen: set[str] = set()

        for entry in manifest.tools:
            seen.add(entry.remote_id)
            existing = by_remote.get(entry.remote_id)
            if existing is None:
                self.session.add(
                    ConnectorToolModel(
                        connector_id=connector_id,
                        selected=False,  # import is not selection
                        first_seen_at=now,
                        last_seen_at=now,
                        **entry.to_row(),
                    )
                )
                added += 1
                continue
            if existing.schema_hash != entry.schema_hash:
                changed += 1
                logger.info(
                    "connector %s: tool %s changed upstream (selected=%s)",
                    row.name, existing.tool_name, existing.selected,
                )
            existing.description = entry.description
            existing.input_schema = entry.input_schema
            existing.optional_args = list(entry.optional_args)
            existing.invoke = entry.invoke
            existing.read_only = entry.read_only
            existing.unsupported_reason = entry.unsupported_reason
            existing.schema_hash = entry.schema_hash
            existing.est_tokens = entry.est_tokens
            existing.last_seen_at = now
            existing.removed_at = None
            # tool_name and selected are deliberately NOT touched.
            updated += 1

        removed = 0
        for existing in existing_rows:
            if existing.remote_id not in seen and existing.removed_at is None:
                existing.removed_at = now
                removed += 1

        row.status = "ready"
        row.last_error = None
        row.instructions = manifest.instructions
        row.source_hash = manifest.source_hash
        row.last_discovered_at = now
        row.updated_at = now
        await self.session.commit()

        return {
            "connector_id": connector_id,
            "status": row.status,
            "tools_discovered": len(manifest.tools),
            "added": added,
            "updated": updated,
            "schema_changed": changed,
            "removed": removed,
            "est_tokens": manifest.est_tokens,
            "instructions": manifest.instructions,
            "server_info": manifest.server_info,
            "protocol_version": manifest.protocol_version,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    # -- tools ------------------------------------------------------------

    async def list_tools(
        self,
        connector_id: str,
        *,
        selected: bool | None = None,
        q: str | None = None,
        include_removed: bool = False,
    ) -> list[ConnectorToolModel]:
        await self.get(connector_id)
        stmt = select(ConnectorToolModel).where(
            ConnectorToolModel.connector_id == connector_id
        )
        if selected is not None:
            stmt = stmt.where(ConnectorToolModel.selected.is_(selected))
        if not include_removed:
            stmt = stmt.where(ConnectorToolModel.removed_at.is_(None))
        stmt = stmt.order_by(ConnectorToolModel.tool_name)
        rows = list((await self.session.execute(stmt)).scalars().all())
        if q:
            needle = q.lower().strip()
            rows = [
                r for r in rows
                if needle in r.tool_name.lower()
                or needle in (r.description or "").lower()
                or needle in r.remote_id.lower()
            ]
        return rows

    async def set_selection(
        self,
        connector_id: str,
        *,
        select_names: Sequence[str] = (),
        deselect_names: Sequence[str] = (),
        select_all: bool | None = None,
    ) -> dict[str, Any]:
        """Bulk select/deselect. Names match ``tool_name`` or ``remote_id``."""
        await self.get(connector_id)
        rows = list(
            (
                await self.session.execute(
                    select(ConnectorToolModel)
                    .where(ConnectorToolModel.connector_id == connector_id)
                    .where(ConnectorToolModel.removed_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        wanted_on = set(select_names or ())
        wanted_off = set(deselect_names or ())
        unknown = (wanted_on | wanted_off) - {r.tool_name for r in rows} - {
            r.remote_id for r in rows
        }
        changed = 0
        for row in rows:
            keys = {row.tool_name, row.remote_id}
            target = row.selected
            if select_all is not None:
                target = select_all
            if keys & wanted_on:
                target = True
            if keys & wanted_off:
                target = False
            if target != row.selected:
                row.selected = target
                changed += 1
        await self.session.commit()
        selected_now = [r.tool_name for r in rows if r.selected]
        return {
            "connector_id": connector_id,
            "changed": changed,
            "selected_count": len(selected_now),
            "selected": selected_now,
            "unknown": sorted(unknown),
            "est_tokens": sum(r.est_tokens for r in rows if r.selected),
        }

    async def test_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        identity: CallerIdentity | None = None,
    ) -> dict[str, Any]:
        """Call one tool exactly the way an agent would, and show the wire args.

        ``arguments_sent`` is the point of this endpoint: it is what survived
        the optional-argument pruning, so an operator can see that the empty
        ``dataset_id`` the UI sent never reached the server.
        """
        row = await self.get(connector_id)
        tool_rows = await self.list_tools(connector_id, include_removed=True)
        match = next(
            (r for r in tool_rows if r.tool_name == tool_name or r.remote_id == tool_name),
            None,
        )
        if match is None:
            raise ConnectorNotFoundError(f"{connector_id}/{tool_name}")

        connector = connector_from_row(row, identity=identity)
        entry = entry_from_row(match)
        sent = connector.prune_arguments(arguments or {}, entry)
        started = time.monotonic()
        try:
            client = connector.client(identity=identity)
            raw = await client.call_tool(entry.remote_id, sent)
            result = shape_result(raw)
            is_error = bool(isinstance(raw, dict) and raw.get("isError"))
        except ConnectorError as exc:
            result = {"error": exc.message}
            is_error = True
        finally:
            await connector.aclose()

        return {
            "connector_id": connector_id,
            "tool_name": entry.tool_name,
            "remote_name": entry.remote_id,
            "arguments_sent": sent,
            "dropped_arguments": sorted(set(arguments or {}) - set(sent)),
            "is_error": is_error,
            "result": result,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
