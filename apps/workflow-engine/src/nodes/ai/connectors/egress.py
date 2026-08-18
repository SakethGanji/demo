"""Where a connector is allowed to send a request.

A connector URL is operator-supplied, not model-supplied, so this is not the
same threat model as an open-ended fetch tool. It is still worth a check:
a connector row is a piece of *data* in a shared team database, and an
unprivileged user who can create one otherwise has a request forger inside the
cluster's network boundary.

Two specifics that matter more than the IP ranges:

**Redirects.** ``check_egress`` is called on the URL the connector was
registered with. The shared ``context.http_client`` the engine hands tools is
built with ``follow_redirects=True`` (``workflow_runner.py:156``), so a server
that answers ``302 Location: http://169.254.169.254/...`` walks the request
straight past this function. Every request this package makes therefore passes
``follow_redirects=False`` explicitly, and re-checks any redirect it chooses to
follow. That is a property of the caller, not of this module, which is why it
is written down here.

**Link-local.** ``169.254.0.0/16`` is refused even when private addresses are
allowed, because a workflow engine that reaches its sibling services over
``http://localhost:8001`` must permit private ranges, and cloud metadata lives
in that block.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlsplit

from .base import ConnectorError

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Refused unconditionally. Cloud instance metadata and its IPv6 equivalent.
_ALWAYS_BLOCKED = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)

_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def _classify(host: str) -> tuple[bool, bool]:
    """Return (is_always_blocked, is_private) for a hostname or literal IP."""
    if host.lower() in _LOCAL_NAMES:
        return False, True
    candidates: list[ipaddress._BaseAddress] = []
    try:
        candidates.append(ipaddress.ip_address(host))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            # Unresolvable here does not mean unresolvable at request time; let
            # the request fail with a real connection error instead of a
            # misleading policy error.
            return False, False
        for info in infos:
            try:
                candidates.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    blocked = any(addr in net for addr in candidates for net in _ALWAYS_BLOCKED)
    private = any(addr in net for addr in candidates for net in _PRIVATE_NETWORKS)
    return blocked, private


def check_egress(url: str, *, allow_private: bool = True) -> str:
    """Validate a connector URL. Returns it normalized, or raises.

    Args:
        url: absolute http(s) URL.
        allow_private: whether RFC1918/loopback targets are permitted. True by
            default because sibling services on ``localhost`` are the main use
            case; set False for connectors registered by untrusted users.

    Raises:
        ConnectorError: with ``status=400``, because the URL is the caller's
            mistake, not the remote's.
    """
    if not url or not isinstance(url, str):
        raise ConnectorError("connector URL is empty", status=400)

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ConnectorError(
            f"unsupported URL scheme {parsed.scheme or '(none)'!r}; "
            "only http and https are allowed",
            status=400,
        )
    if parsed.username or parsed.password:
        raise ConnectorError(
            "credentials in the URL are not supported; use the connector's headers",
            status=400,
        )
    host = parsed.hostname
    if not host:
        raise ConnectorError(f"connector URL {url!r} has no host", status=400)

    blocked, private = _classify(host)
    if blocked:
        raise ConnectorError(
            f"host {host!r} resolves into a link-local range (cloud metadata); refused",
            status=400,
        )
    if private and not allow_private:
        raise ConnectorError(
            f"host {host!r} resolves to a private address and this connector "
            "does not allow private hosts",
            status=400,
        )
    return url.strip()


def same_origin(first: str, second: str) -> bool:
    """Whether two URLs share scheme, host and port — used to vet a redirect."""
    a, b = urlsplit(first), urlsplit(second)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
