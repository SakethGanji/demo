"""``studio.api`` — declare HTTP route handlers in a project module.

    from studio import api, Response, HTTPError

    @api.get("/orders/{id}", auth="app_token", name="getOrder")
    def get_order(req):
        return {"id": req.params["id"]}

The engine discovers decorators statically (no code runs) when the module is saved; at
request time the handler runs inside the sandbox with ``req`` = a ``Request``.
"""

from __future__ import annotations

from typing import Any, Callable

_ROUTES: list[dict[str, Any]] = []


class Request:
    def __init__(self, data: dict) -> None:
        self.method = data.get("method")
        self.path = data.get("path")
        self.route = data.get("route")
        self.params = dict(data.get("params") or {})
        self.query = dict(data.get("query") or {})
        self.headers = dict(data.get("headers") or {})
        self.body = data.get("body")

    def json(self) -> Any:
        return self.body


class Response:
    def __init__(self, status: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status, self.body, self.headers = int(status), body, dict(headers or {})


class HTTPError(Exception):
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.body, self.headers = int(status), body, dict(headers or {})


def _route(method: str):
    def decorator_factory(path: str, *, auth: str = "none", name: str | None = None,
                          description: str | None = None) -> Callable:
        def decorator(fn: Callable) -> Callable:
            _ROUTES.append({"method": method, "path": path, "auth": auth, "name": name, "function": fn.__name__})
            return fn
        return decorator
    return decorator_factory


get = _route("GET")
post = _route("POST")
put = _route("PUT")
patch = _route("PATCH")
delete = _route("DELETE")
