"""In-process HTTP client over this service's own API.

The tools reach the platform the way any other client does — an HTTP request to
``/api/v1/...`` — but the request never leaves the process: ``ASGITransport``
hands it straight to the FastAPI app. The service's own test suite does exactly
this (``tests/conftest.py``).

Why not call the service layer directly? Because every one of the 57 call sites
in the tool modules would then need its authorization re-established by hand.
Going through the route keeps ``get_principal``, ``ensure_dataset_permission``,
the 404-hides-existence rule and the sensitive-column masking on the real code
path, so MCP RBAC cannot silently diverge from REST RBAC. The cost is FastAPI's
dependency resolution and JSON round-trip per call, with no socket, no TLS and
no network hop.

Identity is **not** baked into the client. One ``AsyncClient`` is shared for the
process (so connection state is pooled, not leaked per tool call) and the acting
user's ``X-User-Id`` is attached per request from the request-scoped identity.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import identity

_PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "code"}


class ProblemError(Exception):
    """An RFC 7807 error from the analytics API."""

    def __init__(self, status: int, detail: str, code: str, extra: dict[str, Any]):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code
        self.extra = extra

    @property
    def sheets(self) -> list[str]:
        """Populated on `sheet-selection-required`."""
        value = self.extra.get("sheets")
        return value if isinstance(value, list) else []

    @property
    def available_columns(self) -> list[str]:
        """Populated on `unknown-column`."""
        value = self.extra.get("available")
        return value if isinstance(value, list) else []


class AnalyticsClient:
    """Calls this service's REST API over an in-process ASGI transport."""

    def __init__(self, app: Any, *, api_prefix: str, timeout_s: float = 60.0) -> None:
        self._api_prefix = api_prefix
        self._timeout_s = timeout_s
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=f"http://mcp.internal{api_prefix}",
            timeout=timeout_s,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=_clean(params))

    async def post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return await self._request("POST", path, params=_clean(params), json=_clean(body or {}))

    async def put(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return await self._request("PUT", path, params=_clean(params), json=_clean(body or {}))

    async def patch(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return await self._request("PATCH", path, params=_clean(params), json=_clean(body or {}))

    async def merge_patch(self, path: str, body: dict[str, Any]) -> Any:
        """PATCH a body verbatim, explicit nulls included.

        The other verbs drop ``None`` so an unset tool parameter is never sent.
        The metadata PATCH routes distinguish an absent field (keep) from an
        explicit null (clear), and clearing is only reachable if the null
        survives to the wire.
        """
        return await self._request("PATCH", path, json=body)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = identity.current_identity().headers()
        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProblemError(
                0,
                f"The analytics service did not respond within {self._timeout_s:.0f}s. "
                "Narrow the request (fewer rows, a filter, or a smaller sheet) and retry.",
                "timeout",
                {},
            ) from exc

        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise _problem_from(response)


def _problem_from(response: httpx.Response) -> ProblemError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    extra = {k: v for k, v in body.items() if k not in _PROBLEM_KEYS}
    return ProblemError(
        status=response.status_code,
        detail=str(body.get("detail") or response.reason_phrase or "request failed"),
        code=str(body.get("code") or f"http-{response.status_code}"),
        extra=extra,
    )


def _clean(mapping: dict[str, Any] | None) -> dict[str, Any]:
    """Drop None values so we never send explicit nulls the API would reject."""
    if not mapping:
        return {}
    return {k: v for k, v in mapping.items() if v is not None}
