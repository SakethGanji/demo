"""MCP (Model Context Protocol) connector: streamable-HTTP transport + discovery."""

from .connector import MCPConnector
from .jsonrpc import MCPHttpClient, MCPProtocolError, PROTOCOL_VERSION

__all__ = ["MCPConnector", "MCPHttpClient", "MCPProtocolError", "PROTOCOL_VERSION"]
