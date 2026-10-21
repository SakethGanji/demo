"""``jobs``: backend code drives the Jobs service (apps/jobs-service) through the broker.

The Jobs service runs the heavy, long work beside Studio: browser/API test runs, AI triage, exploration. A python route
starts a job (``studio.jobs.start`` answers with a ``job_id`` at once) and later polls it (``get``, ``events``,
``frame``, ``artifacts``, ``artifact``), cancels it, or pauses / steps / resumes it (``control``). Like ``run_workflow`` it is a broker function without a node of
its own: ``BrokerService.call`` hands it to :func:`call` here.

What this module decides, and nothing else:

* the operations and their parameters (``operation`` + ``body`` / ``job_id`` / ``after`` / ``name``);
* where the service is and how it is authenticated: engine config only (``JOBS_SERVICE_URL``,
  ``JOBS_SERVICE_TOKEN``, ``JOBS_DEV``). Sandboxed code can't name another host, and never sees the URL or token;
* size caps: a frame is returned base64 up to ``FRAME_MAX_BYTES``, an artifact up to ``ARTIFACT_MAX_BYTES``; a larger
  artifact comes back as a reference (no bytes). Both stay well under the sandbox's 10 MB output cap, so a route can
  return what it got;
* a per-call timeout (``settings.jobs_call_timeout_s``, default 10 s) well under the 30 s route limit;
* which operations read (allowed for read-only tokens) and how upstream failures map to broker error codes.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote, urlparse

from .policy import PolicyError, host_matches

FUNCTION = "jobs"
#: The function's identity in tokens, ``allowed_functions`` and the broker_calls log (``snake(NODE_TYPE) == FUNCTION``).
NODE_TYPE = "Jobs"

OPERATIONS = ("start", "get", "events", "frame", "artifacts", "artifact", "cancel", "control")
#: ``control`` actions the Jobs service understands (``takeover`` is reserved there and answers 501 for now).
CONTROL_ACTIONS = ("pause", "resume", "step", "takeover")
READ_OPERATIONS = frozenset({"get", "events", "frame", "artifacts", "artifact"})

#: The token used when ``JOBS_DEV`` is on and ``JOBS_SERVICE_TOKEN`` is unset. Must equal the Jobs service's own dev
#: default (apps/jobs-service, ``JOBS_DEV=1``). Never used outside dev mode.
DEV_TOKEN = "dev-jobs-token"

FRAME_MAX_BYTES = 2 * 1024 * 1024
#: base64 of 6 MB is 8 MB: a route returning it stays under the sandbox output cap (10 MB) and the 16 MB file limit.
ARTIFACT_MAX_BYTES = 6 * 1024 * 1024
#: JSON answers (status, events, artifact list) above this are refused rather than buffered into the sandbox.
JSON_MAX_BYTES = 4 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@ -]{0,199}$")

DESCRIPTION = ("Start and poll Jobs-service jobs (test runs, AI triage, exploration). Use studio.jobs.start/get/events/"
               "frame/artifacts/artifact/cancel.")


class JobsConfig:
    """Resolved from engine settings at call time (so tests and a restart pick up new values)."""

    def __init__(self, url: str, token: str | None, timeout_s: float) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s


def config() -> JobsConfig:
    from ..core.config import settings

    token = settings.jobs_service_token or (DEV_TOKEN if settings.jobs_dev else None)
    timeout = max(1.0, min(float(settings.jobs_call_timeout_s or 10.0), 25.0))
    return JobsConfig(settings.jobs_service_url or "http://127.0.0.1:8095", token, timeout)


def secret_values() -> set[str]:
    """Values the broker must redact from every answer and log (the service token)."""
    tok = config().token
    return {tok} if tok else set()


# ── parameters ────────────────────────────────────────────────────────────────


def operation_of(parameters: dict[str, Any]) -> str:
    op = parameters.get("operation")
    if not isinstance(op, str) or op.strip().lower() not in OPERATIONS:
        raise PolicyError("invalid_request", 400, f"jobs needs `operation`: one of {', '.join(OPERATIONS)}",
                          operation=str(op))
    return op.strip().lower()


def _job_id(parameters: dict[str, Any]) -> str:
    jid = parameters.get("job_id")
    if not isinstance(jid, str) or not _ID_RE.match(jid) or ".." in jid:
        raise PolicyError("invalid_request", 400, "`job_id` must be the id studio.jobs.start returned", job_id=str(jid)[:80])
    return jid


def _name(parameters: dict[str, Any]) -> str:
    name = parameters.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name or "/" in name:
        raise PolicyError("invalid_request", 400, "`name` must be an artifact name from studio.jobs.artifacts",
                          name=str(name)[:80])
    return name


def target_hosts(body: dict[str, Any]) -> list[str]:
    """Hostnames a start body points the job at: ``target.base_url`` and ``target.allowed_hosts``."""
    target = body.get("target") if isinstance(body.get("target"), dict) else {}
    hosts: list[str] = []
    base = target.get("base_url")
    if isinstance(base, str) and base:
        h = urlparse(base).hostname
        if h:
            hosts.append(h.lower())
    for h in target.get("allowed_hosts") or []:
        if isinstance(h, str) and h:
            hosts.append((urlparse(h).hostname if "://" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h).lower())
    return hosts


def check_start(parameters: dict[str, Any], allowlist: list[str], secret_hosts: dict[str, list[str]]) -> None:
    """Policy for ``start``: the body is an object; when the project has an egress allowlist every target host must be
    in it; a secret used in the body (e.g. ``credentials.password = studio.secret('BANK_PW')``) must be bound to every
    target host, since that is where the job will send it."""
    body = parameters.get("body")
    if not isinstance(body, dict):
        raise PolicyError("invalid_request", 400, "studio.jobs.start needs the job body (a dict)")
    hosts = target_hosts(body)
    if allowlist:
        for h in hosts:
            if not host_matches(h, allowlist):
                raise PolicyError("host_not_allowed", 403, f"job target host '{h}' is not in the project egress allowlist",
                                  host=h)
    for key, bound in secret_hosts.items():
        if not hosts:
            raise PolicyError("secret_not_bound", 403, f"secret '{key}' needs a job target (target.base_url)", key=key)
        for h in hosts:
            if not host_matches(h, bound):
                raise PolicyError("host_not_allowed", 403, f"secret '{key}' may not be sent to host '{h}'", host=h, key=key)


def loggable(parameters: dict[str, Any]) -> dict[str, Any]:
    """Parameters as stored in broker_calls: credential values never land in the log, secret or not."""
    body = parameters.get("body")
    if isinstance(body, dict) and isinstance(body.get("credentials"), dict):
        body = {**body, "credentials": {k: "***" for k in body["credentials"]}}
        return {**parameters, "body": body}
    return parameters


# ── the call ──────────────────────────────────────────────────────────────────


def _request(op: str, parameters: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]:
    """(method, path, json body, query) for one operation. The path is built from validated ids only."""
    if op == "start":
        return "POST", "/jobs", parameters["body"], None
    jid = quote(_job_id(parameters), safe="")
    if op == "get":
        return "GET", f"/jobs/{jid}", None, None
    if op == "events":
        after = parameters.get("after", 0)
        try:
            after = max(0, int(after or 0))
        except (TypeError, ValueError):
            raise PolicyError("invalid_request", 400, "`after` must be an integer (the `next` of the previous call)") from None
        return "GET", f"/jobs/{jid}/events", None, {"after": after}
    if op == "frame":
        return "GET", f"/jobs/{jid}/frame", None, None
    if op == "artifacts":
        return "GET", f"/jobs/{jid}/artifacts", None, None
    if op == "artifact":
        return "GET", f"/jobs/{jid}/artifacts/{quote(_name(parameters), safe='')}", None, None
    if op == "control":
        action = parameters.get("action")
        if not isinstance(action, str) or action.strip().lower() not in CONTROL_ACTIONS:
            raise PolicyError("invalid_request", 400, f"`action` must be one of {', '.join(CONTROL_ACTIONS)}",
                              action=str(action)[:40])
        return "POST", f"/jobs/{jid}/control", {"action": action.strip().lower()}, None
    return "POST", f"/jobs/{jid}/cancel", None, None


def _upstream_error(status: int, payload: Any, op: str) -> PolicyError:
    detail = payload.get("detail") if isinstance(payload, dict) else None
    msg = (detail if isinstance(detail, str) else (payload.get("error") if isinstance(payload, dict) else None)) or ""
    msg = str(msg)[:300]
    if status in (401, 403):
        # the engine's credentials were refused: the caller can't fix that, and must not learn anything about them
        return PolicyError("jobs_auth_failed", 502, "the Jobs service refused the engine's credentials")
    if status == 404:
        return PolicyError("job_not_found", 404, msg or f"job or artifact not found ({op})")
    if status == 501:
        return PolicyError("jobs_not_implemented", 501, msg or "not implemented by the Jobs service")
    if 400 <= status < 500:
        return PolicyError("jobs_rejected", 422 if status == 422 else 400, msg or f"the Jobs service rejected {op} ({status})",
                           upstream_status=status, upstream=payload if isinstance(payload, (dict, list)) else None)
    return PolicyError("jobs_failed", 502, msg or f"the Jobs service failed ({status})", upstream_status=status)


async def _read_capped(resp: Any, cap: int) -> bytes | None:
    """The body, or None when it is larger than ``cap`` (stops reading at cap + 1 bytes)."""
    size = resp.headers.get("content-length")
    if size is not None and size.isdigit() and int(size) > cap:
        return None
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


async def call(parameters: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
    """Run one operation; returns the broker's success body ``{ok, items: [result], metadata}``.

    ``transport`` is for tests (an ``httpx`` transport); the client never follows redirects, so the fixed host is the
    only host reached."""
    import json

    import httpx

    op = operation_of(parameters)
    cfg = config()
    if not cfg.token:
        raise PolicyError("jobs_not_configured", 503,
                          "the Jobs service is not configured on this engine (JOBS_SERVICE_TOKEN, or JOBS_DEV=1 in dev)")
    method, path, body, query = _request(op, parameters)
    headers = {"Authorization": f"Bearer {cfg.token}", "Accept": "application/json"}
    kwargs: dict[str, Any] = {"base_url": cfg.url, "timeout": httpx.Timeout(cfg.timeout_s), "follow_redirects": False}
    if transport is not None:
        kwargs["transport"] = transport
    cap = ARTIFACT_MAX_BYTES if op == "artifact" else FRAME_MAX_BYTES if op == "frame" else JSON_MAX_BYTES
    try:
        async with httpx.AsyncClient(**kwargs) as client, \
                client.stream(method, path, json=body, params=query, headers=headers) as resp:
            status = resp.status_code
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip() or "application/octet-stream"
            length = resp.headers.get("content-length")
            raw = await _read_capped(resp, cap)
    except httpx.TimeoutException:
        raise PolicyError("jobs_timeout", 504, f"the Jobs service did not answer within {cfg.timeout_s:g} s") from None
    except httpx.HTTPError as e:
        raise PolicyError("jobs_unavailable", 503, f"the Jobs service is unreachable ({type(e).__name__})") from None

    if 300 <= status < 400:
        raise PolicyError("jobs_failed", 502, f"the Jobs service answered with a redirect ({status}); not followed")
    if status >= 400:
        payload: Any = None
        if raw is not None:
            try:
                payload = json.loads(raw or b"null")
            except ValueError:
                payload = raw[:300].decode("utf-8", "replace")
        raise _upstream_error(status, payload, op)

    meta = {"operation": op, "upstream_status": status}
    if op == "frame":
        if raw is None:
            return {"ok": True, "items": [{"frame": None, "too_large": True}], "metadata": meta}
        if status == 204 or not raw:
            return {"ok": True, "items": [{"frame": None}], "metadata": meta}
        return {"ok": True, "items": [{"frame": {"content_type": ctype, "base64": base64.b64encode(raw).decode(),
                                                 "size": len(raw)}}], "metadata": meta}
    if op == "artifact":
        name = parameters["name"]
        if raw is None:
            size = int(length) if length and length.isdigit() else None
            return {"ok": True, "items": [{"name": name, "content_type": ctype, "size": size, "too_large": True,
                                           "max_bytes": ARTIFACT_MAX_BYTES,
                                           "ref": {"job_id": parameters["job_id"], "name": name}}], "metadata": meta}
        return {"ok": True, "items": [{"name": name, "content_type": ctype, "size": len(raw),
                                       "base64": base64.b64encode(raw).decode()}], "metadata": meta}
    if raw is None:
        raise PolicyError("jobs_response_too_large", 502, f"the Jobs service answer to {op} is larger than "
                                                          f"{JSON_MAX_BYTES // (1024 * 1024)} MB")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        raise PolicyError("jobs_failed", 502, f"the Jobs service answered {op} with non-JSON") from None
    if op == "artifacts":
        listing = data.get("artifacts") if isinstance(data, dict) else data
        data = {"artifacts": listing if isinstance(listing, list) else []}
    elif not isinstance(data, dict):
        data = {"value": data}
    return {"ok": True, "items": [data], "metadata": meta}
