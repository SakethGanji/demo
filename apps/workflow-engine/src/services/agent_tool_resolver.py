"""Turn ``agent_tool_bindings`` rows into what the agent loop can consume.

Two outputs, because the loop accepts tools two ways:

``builtin_tool_specs``
    Passed as the ``builtinTools`` node parameter and resolved by
    ``inline_config.resolve_tools``. Entries are ``{"type": key, "parameters": {...}}``.

``extra_tools``
    Passed on the input item's ``_tools`` key — the seam at
    ``ai_agent.py:552-564``. Entries are
    ``{"name", "description", "input_schema", "execute"}`` where ``execute`` is a
    live Python closure. Everything non-builtin (MCP, OpenAPI, promoted
    workflows, node-backed tools) arrives this way, which is why the agent loop
    needs no knowledge of connectors at all.

The non-builtin providers are being built in parallel, so each is imported
lazily behind ``try/except ImportError``. A missing provider degrades to "that
tool is unavailable this run" and is reported in ``unavailable`` — it never
fails the run.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

BUILTIN_SOURCE = "builtin"

# Connector-backed sources go through the connector registry, which takes
# ``(bindings, session)`` where a binding is ``{"connector_id", "tools"}`` and
# returns ``(tools, executors)``.
_CONNECTOR_SOURCES = ("mcp", "openapi")

# (module, attribute) candidates per source. The first that imports and
# resolves wins. Signature expected:
# ``fn(bindings, session_factory) -> list[tool_dict]`` (awaited if it returns
# a coroutine). Providers receive the session *factory*, not a live session:
# the ``execute`` closures they return run mid-agent-loop, long after resolve
# time, so call-time DB work must open its own session. (A resolver built with
# a bare session — tests, legacy callers — passes that session instead.)
_PROVIDER_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "promoted": (
        ("..services.promoted_tool_service", "build_agent_tools"),
        ("..nodes.ai.connectors.promoted_tools", "build_agent_tools"),
    ),
    "node": (
        ("..nodes.ai.connectors.node_tools", "build_agent_tools"),
        ("..services.node_tool_service", "build_agent_tools"),
    ),
    # The workflow SDK as a tool: the agent writes a Python script, gets a
    # validated (and by default persisted) workflow back. Runs in the
    # subprocess sandbox, never in-process.
    "sdk": (
        ("..services.workflow_sdk_tool_service", "build_agent_tools"),
    ),
}


@dataclass
class ResolvedTools:
    """What one agent's bindings resolved to for a single run."""

    builtin_tool_specs: list[Any] = field(default_factory=list)
    extra_tools: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[dict[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.builtin_tool_specs) + len(self.extra_tools)


class AgentToolResolver:
    """Resolves bindings to ``(builtin_tool_specs, extra_tools)``."""

    def __init__(
        self, session: Any | None = None, session_factory: Any | None = None
    ) -> None:
        self._session = session
        self._session_factory = session_factory

    async def resolve(self, bindings: list[Any]) -> ResolvedTools:
        resolved = ResolvedTools()
        by_source: dict[str, list[Any]] = {}

        for binding in bindings:
            if not _attr(binding, "enabled", True):
                continue
            source = _attr(binding, "source", BUILTIN_SOURCE) or BUILTIN_SOURCE
            by_source.setdefault(source, []).append(binding)

        for binding in by_source.pop(BUILTIN_SOURCE, []):
            self._add_builtin(binding, resolved)

        connector_bindings: list[Any] = []
        for source in _CONNECTOR_SOURCES:
            connector_bindings.extend(by_source.pop(source, []))
        if connector_bindings:
            await self._add_connector_tools(connector_bindings, resolved)

        for source, source_bindings in by_source.items():
            await self._add_external(source, source_bindings, resolved)

        return resolved

    # -- builtin -----------------------------------------------------------

    def _add_builtin(self, binding: Any, resolved: ResolvedTools) -> None:
        tool_key = _attr(binding, "tool_key", "")
        if not tool_key:
            return
        config = _attr(binding, "config", {}) or {}
        alias = _attr(binding, "alias", None)

        if alias:
            # inline_config has no rename hook, so an aliased builtin is
            # resolved here and pushed through the _tools seam instead.
            renamed = self._resolve_builtin_renamed(tool_key, config, alias)
            if renamed is not None:
                resolved.extra_tools.append(renamed)
                return
            resolved.unavailable.append(
                {"source": BUILTIN_SOURCE, "tool_key": tool_key, "reason": "alias-resolve-failed"}
            )
            return

        resolved.builtin_tool_specs.append({"type": tool_key, "parameters": dict(config)})

    def _resolve_builtin_renamed(
        self, tool_key: str, config: dict[str, Any], alias: str
    ) -> dict[str, Any] | None:
        try:
            from ..nodes.ai.inline_config import resolve_tools

            tools, executors = resolve_tools([{"type": tool_key, "parameters": dict(config)}])
        except Exception:
            logger.warning("failed to resolve builtin tool '%s'", tool_key, exc_info=True)
            return None
        if not tools:
            return None
        tool = dict(tools[0])
        original_name = tool["name"]
        tool["name"] = alias
        executor = executors.get(original_name)
        if executor is not None:
            tool["execute"] = executor
        return tool

    # -- connector-backed (MCP / OpenAPI) ----------------------------------

    async def _add_connector_tools(
        self, bindings: list[Any], resolved: ResolvedTools
    ) -> None:
        """Resolve MCP/OpenAPI bindings through the connector registry."""
        try:
            # The bundle form also reports *why* a connector contributed
            # nothing, which is the difference between "the agent has no tools"
            # and "the agent's tools silently vanished".
            from ..nodes.ai.connectors.registry import resolve_connector_bundle
        except ImportError:
            for binding in bindings:
                resolved.unavailable.append(
                    {
                        "source": _attr(binding, "source", "") or "",
                        "tool_key": _attr(binding, "tool_key", ""),
                        "reason": "connector registry not available",
                    }
                )
            return

        if self._session is None and self._session_factory is None:
            for binding in bindings:
                resolved.unavailable.append(
                    {
                        "source": _attr(binding, "source", "") or "",
                        "tool_key": _attr(binding, "tool_key", ""),
                        "reason": "no database session to load connectors with",
                    }
                )
            return

        # One spec per connector, listing only the tool keys this agent bound:
        # a connector with 200 selected tools must not dump all of them into
        # an agent that asked for two.
        by_connector: dict[str, list[str]] = {}
        for binding in bindings:
            connector_id = _attr(binding, "connector_id", None)
            if not connector_id:
                resolved.unavailable.append(
                    {
                        "source": _attr(binding, "source", "") or "",
                        "tool_key": _attr(binding, "tool_key", ""),
                        "reason": "binding has no connector_id",
                    }
                )
                continue
            by_connector.setdefault(connector_id, []).append(
                _attr(binding, "tool_key", "")
            )

        if not by_connector:
            return

        specs = [
            {"connector_id": connector_id, "tools": [k for k in keys if k]}
            for connector_id, keys in by_connector.items()
        ]
        taken = [t["name"] for t in resolved.extra_tools]
        try:
            if self._session_factory is not None:
                # Fresh session for resolve-time reads only (connector rows,
                # selected tool entries). Bundle executors must not capture
                # it — they hold the connector + HTTP client instead — since
                # the tools outlive this method by the whole run.
                async with self._session_factory() as session:
                    bundle = await resolve_connector_bundle(
                        specs, session, taken_names=taken
                    )
            else:
                bundle = await resolve_connector_bundle(
                    specs, self._session, taken_names=taken
                )
            tools = bundle.get("tools") or []
            executors = bundle.get("executors") or {}
            for skip in bundle.get("skipped") or []:
                resolved.unavailable.append(
                    {
                        "source": "mcp",
                        "tool_key": ", ".join(
                            by_connector.get(skip.get("connector_id", ""), [])
                        ),
                        "reason": (
                            f"connector {skip.get('connector_id')}: "
                            f"{skip.get('reason', 'unavailable')}"
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001 — one bad connector != a dead run
            logger.warning("connector tool resolution failed", exc_info=True)
            for connector_id, keys in by_connector.items():
                for key in keys:
                    resolved.unavailable.append(
                        {
                            "source": "mcp",
                            "tool_key": key,
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )
            return

        for tool in tools or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            if "execute" not in tool and tool["name"] in (executors or {}):
                tool = {**tool, "execute": executors[tool["name"]]}
            resolved.extra_tools.append(tool)

    # -- external ----------------------------------------------------------

    async def _add_external(
        self, source: str, bindings: list[Any], resolved: ResolvedTools
    ) -> None:
        provider = self._load_provider(source)
        if provider is None:
            for binding in bindings:
                resolved.unavailable.append(
                    {
                        "source": source,
                        "tool_key": _attr(binding, "tool_key", ""),
                        "reason": f"no provider registered for source '{source}'",
                    }
                )
            return

        try:
            # Factory preferred: provider-built executors run at tool-call
            # time, when a session opened during resolve would be closed.
            produced = provider(bindings, self._session_factory or self._session)
            if inspect.isawaitable(produced):
                produced = await produced
        except Exception as exc:  # noqa: BLE001 — one bad connector != a dead run
            logger.warning("tool provider for '%s' failed", source, exc_info=True)
            for binding in bindings:
                resolved.unavailable.append(
                    {
                        "source": source,
                        "tool_key": _attr(binding, "tool_key", ""),
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
            return

        for tool in produced or []:
            if isinstance(tool, dict) and tool.get("name"):
                resolved.extra_tools.append(tool)

    def _load_provider(self, source: str):
        for module_path, attribute in _PROVIDER_CANDIDATES.get(source, ()):
            try:
                module = _import_relative(module_path)
            except ImportError:
                continue
            except Exception:
                logger.warning("error importing tool provider %s", module_path, exc_info=True)
                continue
            provider = getattr(module, attribute, None)
            if callable(provider):
                return provider
        return None


def _import_relative(module_path: str):
    """Import a module written relative to this package (leading dots)."""
    from importlib import import_module

    stripped = module_path.lstrip(".")
    level = len(module_path) - len(stripped)
    # import_module ignores `package` unless the name is relative, so the
    # single leading dot is deliberate — the anchor does the rest.
    return import_module("." + stripped, package=_package_for(level))


def _package_for(level: int) -> str:
    parts = __package__.split(".")
    if level <= 1:
        return __package__
    return ".".join(parts[: len(parts) - (level - 1)])


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
