"""Mounting the MCP surface on the FastAPI app.

Mounted, not deployed separately. The reasons, in the order they matter:

* **Drift.** A standalone adapter drifts from the API it wraps; this repo
  already contains that failure mode elsewhere. Here the tools sit beside the
  routes they call and are exercised by the same test suite.
* **Identity.** A sidecar has to trust a forwarded ``X-User-Id``. Mounted, MCP
  requests arrive through the service's own ``get_principal`` and there is no
  second identity path to keep honest.
* **Incentives.** Every client-side workaround for a service bug is a bug that
  should have been fixed upstream. Sharing a process makes fixing it upstream
  the path of least resistance.

Transport: **stateless**. The streamable-HTTP session manager runs a stateful
session's handlers in a task spawned by whichever request created the session,
so anything bound to that task at session creation would outlive the request
that set it. Statelessness removes the shared session entirely — plus, with
27 request/response tools and no server-initiated messages, there is nothing a
session would buy. JSON responses rather than SSE, for the same reason.

The mount is a Starlette ``Mount``, not an ``APIRouter``, so it contributes
nothing to the ``/api/v1`` OpenAPI document: MCP is a different protocol, and
one JSON-RPC endpoint described as a REST operation would be a lie in a 128-op
spec.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from .identity import MCPIdentityASGIMiddleware
from .server import build

logger = logging.getLogger(__name__)

MCP_PATH_SUFFIX = "/mcp"


class MountedMCP:
    """Everything the mounted endpoint needs, with a lifespan of its own."""

    def __init__(self, app, *, api_prefix: str, log_level: str = "WARNING") -> None:
        self.server, self.client = build(app, api_prefix=api_prefix, log_level=log_level)
        self.path = f"{api_prefix}{MCP_PATH_SUFFIX}"
        # Built for its side effect: the SDK documents `session_manager` as the
        # seam for mounting a server into an existing app, and it is only
        # populated once this factory has run. The Starlette app it returns
        # brings its own routing and lifespan, neither of which we want.
        self.server.streamable_http_app(
            streamable_http_path=self.path,
            json_response=True,
            stateless_http=True,
            # The SDK's own Host/Origin allowlist is for standalone local
            # servers; left on it defaults to a localhost-only list and 421s
            # every other deployment of this service. Origin policy here is the
            # app's CORS middleware, and identity is an explicit X-User-Id
            # header — a custom header, so a rebound origin cannot send one
            # without passing preflight, and there is no ambient cookie to ride.
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            ),
        )
        self.asgi_app = MCPIdentityASGIMiddleware(self.server.session_manager.handle_request)

    @property
    def session_manager(self):
        return self.server.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self.session_manager.run():
            try:
                yield
            finally:
                await self.client.aclose()


def mount(app, *, api_prefix: str, log_level: str = "WARNING") -> MountedMCP:
    """Attach the MCP endpoint to *app* at ``{api_prefix}/mcp``.

    Returns the handle whose ``lifespan()`` the app's own lifespan must enter;
    the session manager refuses to serve before its task group exists.
    """
    mounted = MountedMCP(app, api_prefix=api_prefix, log_level=log_level)
    # A Route holding a raw ASGI app rather than a Mount: a Mount would 307 the
    # bare path to a trailing slash, and MCP clients POST to the exact URL they
    # were configured with. Starlette hands a non-function endpoint the ASGI
    # three-tuple untouched, which is what the session manager wants. Not an
    # APIRoute, so FastAPI's schema generator ignores it.
    app.router.routes.append(
        Route(
            mounted.path,
            endpoint=mounted.asgi_app,
            methods=["GET", "POST", "DELETE"],
            name="mcp",
        )
    )
    logger.info("MCP endpoint mounted at %s", mounted.path)
    return mounted
