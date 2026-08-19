"""Agent CRUD, the builtin-tool catalogue, and role derivation."""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from ..core.exceptions import ValidationError, WorkflowNotFoundError
from ..schemas.agent import (
    AgentCreateRequest,
    AgentListItem,
    AgentResponse,
    AgentUpdateRequest,
    AvailableToolItem,
    ToolBindingSchema,
)

logger = logging.getLogger(__name__)

# Fields whose change alters what the agent *does*. Renaming it or moving it
# to another folder does not, so those never bump the version — a session
# pins ``agent_version``, and a spurious bump makes every open session look stale.
_CONFIG_FIELDS = (
    "model",
    "system_prompt",
    "task_template",
    "settings",
    "memory",
    "output_schema",
)

# Role is derived, never user-set: it is a *claim about capability*, and a
# user-typed claim would drift from the tools actually bound.
#   builds  — can cause an effect outside the conversation
#   watches — can read systems of record but not change them
#   asks    — can only reason and compute
_BUILDS_TOOLS = {"workflow", "code", "httpRequest", "apiRequest"}
# "sdk" builds and runs whole workflows — the strongest effect an agent can have.
_BUILDS_SOURCES = {"promoted", "node", "sdk"}
_WATCHES_TOOLS = {
    "dataProfile",
    "dataAggregate",
    "dataSample",
    "dataReport",
    "mongoQuery",
    "neo4jQuery",
}


class AgentNotFoundError(WorkflowNotFoundError):
    """Raised when an agent id does not resolve."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' not found")


class AgentService:
    """Business logic for agent definitions."""

    def __init__(self, agent_repo: Any) -> None:
        self._repo = agent_repo

    # -- catalogue ---------------------------------------------------------

    def available_tools(self) -> list[AvailableToolItem]:
        """The builtin tools an agent may bind, straight off the node classes."""
        from ..nodes.ai.inline_config import _get_tool_classes

        items: list[AvailableToolItem] = []
        for key, cls in _get_tool_classes().items():
            description = getattr(cls, "node_description", None)
            if description is None:
                items.append(
                    AvailableToolItem(
                        key=key,
                        name=cls.__name__,
                        display_name=key,
                        description="",
                    )
                )
                continue
            items.append(
                AvailableToolItem(
                    key=key,
                    source="builtin",
                    name=description.name,
                    display_name=description.display_name,
                    description=description.description,
                    icon=description.icon,
                    parameters=[_serialize(p) for p in (description.properties or [])],
                )
            )
        return items

    # -- CRUD --------------------------------------------------------------

    async def list_agents(
        self,
        team_id: str | None = None,
        folder_id: str | None = None,
        active: bool | None = None,
    ) -> list[AgentListItem]:
        agents = await self._repo.list(team_id=team_id, folder_id=folder_id, active=active)
        ids = [a.id for a in agents]
        tool_counts = await self._repo.binding_counts(ids)
        session_counts = await self._repo.session_counts(ids)
        last_runs = await self._repo.last_run_times(ids)
        return [
            AgentListItem(
                id=a.id,
                name=a.name,
                description=a.description,
                role=a.role,
                model=a.model,
                version=a.version,
                active=a.active,
                tool_count=tool_counts.get(a.id, 0),
                session_count=session_counts.get(a.id, 0),
                last_run_at=last_runs[a.id].isoformat() if a.id in last_runs else None,
                updated_at=a.updated_at.isoformat(),
            )
            for a in agents
        ]

    async def get_agent(self, agent_id: str) -> AgentResponse:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        bindings = await self._repo.get_bindings(agent_id)
        return self._to_response(agent, bindings)

    async def create_agent(self, request: AgentCreateRequest) -> AgentResponse:
        name = (request.name or "").strip()
        if not name:
            raise ValidationError("Agent name is required")

        tools = [t.model_dump() for t in (request.tools or [])]
        agent = await self._repo.create(
            {
                "team_id": request.team_id,
                "folder_id": request.folder_id,
                "name": name,
                "description": request.description,
                "role": derive_role(tools),
                "model": request.model,
                "system_prompt": request.system_prompt,
                "task_template": request.task_template,
                "settings": request.settings,
                "memory": request.memory,
                "output_schema": request.output_schema,
                "active": request.active,
                "created_by": request.created_by,
            }
        )

        bindings = []
        if tools:
            bindings = await self._repo.replace_bindings(agent.id, tools)
        return self._to_response(agent, bindings)

    async def update_agent(self, agent_id: str, request: AgentUpdateRequest) -> AgentResponse:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)

        changes = request.model_dump(exclude_unset=True, exclude_none=True)
        tools = changes.pop("tools", None)
        if "name" in changes and not (changes["name"] or "").strip():
            raise ValidationError("Agent name cannot be empty")

        bump = any(field in changes for field in _CONFIG_FIELDS) or tools is not None

        bindings: list[Any]
        if tools is not None:
            bindings = await self._repo.replace_bindings(agent_id, tools)
            changes["role"] = derive_role(tools)
        else:
            bindings = await self._repo.get_bindings(agent_id)

        updated = await self._repo.update(agent_id, changes, bump_version=bump)
        if updated is None:
            raise AgentNotFoundError(agent_id)
        return self._to_response(updated, bindings)

    async def delete_agent(self, agent_id: str) -> None:
        deleted = await self._repo.delete(agent_id)
        if not deleted:
            raise AgentNotFoundError(agent_id)

    # -- bindings ----------------------------------------------------------

    async def get_tools(self, agent_id: str) -> list[ToolBindingSchema]:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return [_binding_schema(b) for b in await self._repo.get_bindings(agent_id)]

    async def set_tools(
        self, agent_id: str, tools: list[ToolBindingSchema]
    ) -> list[ToolBindingSchema]:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        payload = [t.model_dump() for t in tools]
        bindings = await self._repo.replace_bindings(agent_id, payload)
        # Bindings are config: a changed toolset is a changed agent.
        await self._repo.update(
            agent_id, {"role": derive_role(payload)}, bump_version=True
        )
        return [_binding_schema(b) for b in bindings]

    # -- mapping -----------------------------------------------------------

    def _to_response(self, agent: Any, bindings: list[Any]) -> AgentResponse:
        return AgentResponse(
            id=agent.id,
            team_id=agent.team_id,
            folder_id=agent.folder_id,
            name=agent.name,
            description=agent.description,
            role=agent.role,
            model=agent.model,
            system_prompt=agent.system_prompt,
            task_template=agent.task_template,
            settings=agent.settings or {},
            memory=agent.memory,
            output_schema=agent.output_schema,
            version=agent.version,
            active=agent.active,
            tools=[_binding_schema(b) for b in bindings],
            created_by=agent.created_by,
            updated_by=agent.updated_by,
            created_at=agent.created_at.isoformat(),
            updated_at=agent.updated_at.isoformat(),
        )


def derive_role(tools: list[Any]) -> str:
    """asks | builds | watches, from what the bound tools can actually do."""
    watches = False
    for tool in tools:
        if isinstance(tool, dict):
            source = tool.get("source") or "builtin"
            key = tool.get("tool_key") or ""
            enabled = tool.get("enabled", True)
        else:
            source = getattr(tool, "source", "builtin") or "builtin"
            key = getattr(tool, "tool_key", "") or ""
            enabled = getattr(tool, "enabled", True)
        if not enabled:
            continue
        if source in _BUILDS_SOURCES:
            return "builds"
        if source == "builtin" and key in _BUILDS_TOOLS:
            return "builds"
        if source in ("mcp", "openapi"):
            # A remote tool's blast radius is unknown; treat it as observation
            # unless something stronger is also bound.
            watches = True
        if source == "builtin" and key in _WATCHES_TOOLS:
            watches = True
    return "watches" if watches else "asks"


def _binding_schema(binding: Any) -> ToolBindingSchema:
    return ToolBindingSchema(
        source=binding.source,
        connector_id=binding.connector_id,
        tool_key=binding.tool_key,
        alias=binding.alias,
        config=binding.config or {},
        requires_approval=binding.requires_approval,
        enabled=binding.enabled,
        position=binding.position,
    )


def _serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value
