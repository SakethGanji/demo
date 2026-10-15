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


class EgressGuard:
    """Per-call host policy, enforced on every request the run's httpx client sends."""

    def __init__(self, project_allowlist: list[str], secret_hosts: dict[str, list[str]]) -> None:
        self.project_allowlist = project_allowlist or []
        self.secret_hosts = secret_hosts  # referenced secret key -> allowed hosts
        self.violation: PolicyError | None = None

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

    async def httpx_hook(self, request) -> None:
        self.check_host(request.url.host)
