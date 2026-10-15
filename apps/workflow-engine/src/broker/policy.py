"""Broker policy (BACKEND-DESIGN §5.2–5.3).

* Which nodes are callable as functions.
* Secrets only as bare ``{{ $vars.KEY }}``; ``credentialId`` refused.
* Secret → host binding (``variables.allowed_hosts``; null = refused, decision 3).
* Project egress allowlist.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from ..services.vars_substitution import _VARS_RE

# Nodes that are never functions: triggers, graph-only flow control, UI, and code runners.
NOT_EXPOSED = {
    "Start", "Webhook", "Cron", "ErrorTrigger", "ExecuteWorkflowTrigger",
    "If", "Switch", "Merge", "Loop", "Wait", "Poll", "StopAndError", "ExecuteWorkflow",
    "ChatInput", "Code", "PythonStep", "Respond",
}
# Egress classification per node type: "none" = pure data, "http" = via the run's http client
# (checked per request), anything else = opaque egress (cannot be host-checked).
EGRESS_NONE = {"Set", "Filter", "ItemLists", "Sample", "Profile", "Aggregate", "DataTable"}
EGRESS_HTTP = {"HttpRequest"}


class PolicyError(Exception):
    def __init__(self, code: str, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.extra = extra


def snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return s.lower()


def is_exposed(node_cls_or_type: Any) -> bool:
    t = node_cls_or_type if isinstance(node_cls_or_type, str) else getattr(node_cls_or_type, "type", "")
    if t in NOT_EXPOSED:
        return False
    try:
        from ..engine.node_registry import node_registry
        node = node_registry.get(t)
    except Exception:
        return False
    explicit = getattr(type(node), "function_exposed", None)
    if explicit is not None:
        return bool(explicit)
    desc = node.node_description
    if desc and "trigger" in (desc.group or []):
        return False
    return True


def egress_kind(node_type: str) -> str:
    if node_type in EGRESS_NONE:
        return "none"
    if node_type in EGRESS_HTTP:
        return "http"
    if node_type == "Postgres":
        return "postgres"
    return "opaque"


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _has_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_has_key(v, key) for v in value)
    return False


def secret_refs(parameters: Any, items: Any = None) -> set[str]:
    """Keys referenced as bare ``{{ $vars.KEY }}``. Raises for any other ``$vars`` usage."""
    keys: set[str] = set()
    for s in list(_strings(parameters)) + list(_strings(items or [])):
        if "$vars" not in s:
            continue
        keys.update(_VARS_RE.findall(s))
        if "$vars" in _VARS_RE.sub("", s):
            raise PolicyError("secret_expression_not_allowed", 422,
                              "$vars may only be used as a bare {{ $vars.KEY }} placeholder")
    return keys


def check_parameters(parameters: dict[str, Any], items: list[Any]) -> set[str]:
    if _has_key(parameters, "credentialId") or _has_key(parameters, "_credentials"):
        raise PolicyError("credential_not_allowed", 422, "credentialId is not allowed in broker calls; use $vars secrets")
    return secret_refs(parameters, items)


def host_matches(host: str, patterns: list[str]) -> bool:
    host = (host or "").lower()
    for p in patterns or []:
        p = p.lower()
        if p.startswith("*."):
            if host.endswith(p[1:]) or host == p[2:]:
                return True
        elif fnmatch.fnmatchcase(host, p) or host == p:
            return True
    return False


# ── engine self-address (audit #1) ──────────────────────────────────────────

# Ports this engine serves on: recorded from every request's ASGI scope (core middleware) plus the
# configured port. A read-only token may never reach any of them on a local address.
OWN_PORTS: set[int] = set()
_LOCAL_ALIASES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def note_own_port(port: int | None) -> None:
    if port:
        OWN_PORTS.add(int(port))


def _own_ports() -> set[int]:
    from ..core.config import settings
    return OWN_PORTS | {int(settings.port)}


def _is_internal_ip(ip) -> bool:
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_reserved
                or ip.is_multicast or getattr(ip, "ipv4_mapped", None) and _is_internal_ip(ip.ipv4_mapped))


async def _resolve(host: str) -> list:
    import asyncio
    import ipaddress
    import socket

    h = host.strip("[]")
    try:
        return [ipaddress.ip_address(h)]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    return out


class EgressGuard:
    """Per-call host policy, enforced on every request the run's httpx client sends."""

    def __init__(self, project_allowlist: list[str], secret_hosts: dict[str, list[str]],
                 read_only: bool = False) -> None:
        self.project_allowlist = project_allowlist or []
        self.secret_hosts = secret_hosts  # referenced secret key -> allowed hosts
        self.read_only = read_only
        self.violation: PolicyError | None = None

    def _explicitly_allowed(self, host: str) -> bool:
        """A host the owner named for this call: the project allowlist or a used secret's allowed_hosts."""
        pats = list(self.project_allowlist) + [h for hosts in self.secret_hosts.values() for h in hosts]
        return bool(pats) and host_matches(host, pats)

    def _deny_ro(self, why: str, host: str) -> None:
        self.violation = PolicyError("read_only", 403, f"read-only token: {why}", function="http_request", host=host)
        raise self.violation

    async def check_read_only_destination(self, url) -> None:
        """Audit #1: a read-only token never reaches the engine itself (any local alias on an engine port,
        or the public base URL) and reaches loopback/private/link-local addresses only when the host is
        explicitly allowlisted for this call. DNS is resolved here, before connecting; redirects are off."""
        host = (url.host or "").lower().strip("[]")
        port = url.port or (443 if url.scheme == "https" else 80)
        from ..core.config import settings
        if settings.public_base_url:
            from urllib.parse import urlparse
            pub = urlparse(settings.public_base_url)
            if (pub.hostname or "").lower() == host and (pub.port or (443 if pub.scheme == "https" else 80)) == port:
                self._deny_ro("requests to this engine are not allowed", host)
        ips = await _resolve(host)
        if not ips and host not in _LOCAL_ALIASES:
            self._deny_ro(f"cannot resolve '{host}'", host)
        local = host in _LOCAL_ALIASES or any(_is_internal_ip(ip) for ip in ips)
        if local and port in _own_ports():
            self._deny_ro("requests to this engine are not allowed", host)
        if local and not self._explicitly_allowed(host):
            self._deny_ro(f"'{host}' is a local/private address that is not allowlisted for this call", host)

    def check_host(self, host: str) -> None:
        host = (host or "").lower()
        if self.project_allowlist and not host_matches(host, self.project_allowlist):
            self.violation = PolicyError("host_not_allowed", 403, f"host '{host}' is not in the project egress allowlist", host=host)
            raise self.violation
        for key, hosts in self.secret_hosts.items():
            if not host_matches(host, hosts):
                self.violation = PolicyError("host_not_allowed", 403,
                                             f"secret '{key}' may not be sent to host '{host}'", host=host, key=key)
                raise self.violation

    def check_postgres_connection(self, connection_string: str) -> None:
        """Validate the actual libpq destination after secret substitution.

        Only a single explicit TCP host is supported. Alternative destinations
        (hostaddr, services, sockets and failover lists) must not bypass bindings.
        """
        from psycopg.conninfo import conninfo_to_dict

        try:
            info = conninfo_to_dict(connection_string)
        except Exception:
            raise PolicyError("invalid_connection", 422, "Invalid PostgreSQL connection string") from None
        host = info.get("host", "")
        if (not host or any(c in host for c in ",/ ") or
                any(k in info for k in ("hostaddr", "service", "servicefile")) or
                "," in info.get("port", "")):
            raise PolicyError("invalid_connection", 422,
                              "PostgreSQL requires one explicit TCP host; services, hostaddr and sockets are not supported")
        self.check_host(host)

    async def httpx_hook(self, request) -> None:
        self.check_host(request.url.host)
        if self.read_only:
            await self.check_read_only_destination(request.url)


# ── read-only claim (review D5) ──────────────────────────────────────────────

READ_ONLY_DATA_OPS = {"get", "list"}
READ_ONLY_HTTP_METHODS = {"GET", "HEAD"}


def check_read_only(node_type: str, parameters: dict[str, Any]) -> None:
    """Raise ``read_only`` (403) for anything that can write data or send data out.

    Parameters are checked as given (before expression resolution): an expression where a
    literal operation/method is expected is refused, since it could resolve to a write."""
    def deny(why: str, **extra: Any) -> None:
        raise PolicyError("read_only", 403, f"read-only token: {why}", function=snake(node_type), **extra)

    if node_type == "DataTable":
        op = parameters.get("operation", "list")
        if not isinstance(op, str) or op.strip().lower() not in READ_ONLY_DATA_OPS:
            deny(f"data_table operation '{op}' writes data (only get/list are allowed)", operation=str(op))
        return
    if node_type == "HttpRequest":
        method = parameters.get("method", "GET")
        if not isinstance(method, str) or method.strip().upper() not in READ_ONLY_HTTP_METHODS:
            deny(f"http_request method '{method}' is not GET/HEAD", operation=str(method))
        return
    if node_type == "Postgres":
        deny("external SQL cannot be safely checked for writes")
    if node_type == "SendEmail":
        deny("send_email sends data out")
    if egress_kind(node_type) == "opaque":
        deny(f"'{snake(node_type)}' has egress that cannot be checked")
