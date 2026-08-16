"""Model Context Protocol surface for the analytics platform.

The 27 tools an LLM client sees, mounted as an ASGI sub-app on this service
rather than deployed as a separate adapter. ``asgi.mount`` is the entry point;
``identity`` explains why a mounted MCP endpoint needs request-scoped identity
and how it gets it.
"""

from __future__ import annotations

from .asgi import MountedMCP, mount

__all__ = ["MountedMCP", "mount"]
