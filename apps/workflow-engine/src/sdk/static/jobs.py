"""``studio.jobs`` — the Jobs service (browser/API test runs, AI triage, exploration) through the broker.

    job = studio.jobs.start({"type": "test-run", "label": "smoke", "target": {...}, "cases": [...]})
    status = studio.jobs.get(job["job_id"])            # {status, progress, results, output?}
    page = studio.jobs.events(job["job_id"], after=0)  # {events: [...], next}
    frame = studio.jobs.frame(job["job_id"])           # {content_type, base64} or None

``start`` returns at once; the job runs in the Jobs service. Poll ``get`` / ``events`` / ``frame`` from routes the UI
calls every second or so. The service's address and token stay in the engine; this code never sees them.
"""

from __future__ import annotations

from typing import Any

from ._transport import BrokerError
from ._transport import call as _call

# The fields the Jobs service reads from a start body, per job type. Anything else is silently ignored by the service
# (e.g. a `credentials_ref` the module thought was honoured), so ``start`` refuses it instead.
JOB_FIELDS: dict[str, frozenset[str]] = {
    "test-run": frozenset({"type", "label", "target", "policy", "credentials", "concurrency", "cases"}),
    "triage": frozenset({"type", "job_id", "case_id"}),
    "explore": frozenset({"type", "label", "target", "policy", "credentials", "goal"}),
}


# The UI steps the Jobs service runs: action -> (required fields, optional fields). Mirrors validateAction in
# apps/test-builder/src/actions.ts plus expectUrl from apps/jobs-service/src/spec.ts, which also maps STEP_ALIASES and refuses
# 'done' (a planner verb, not a runnable step). Any step may also carry ``group``. A test pins this table to those sources.
UI_STEPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "goto": (("url",), ()),
    "click": (("target",), ()),
    "fill": (("target", "value"), ()),
    "select": (("target", "option"), ()),
    "press": (("key",), ("target",)),
    "hover": (("target",), ()),
    "upload": (("target", "file"), ()),
    "otp": (("value",), ("within",)),
    "waitFor": (("target",), ("state",)),
    "download": (("target",), ("contains",)),
    "expectVisible": (("target",), ()),
    "expectText": (("target", "text"), ()),
    "expectCount": (("target", "count"), ()),
    "expectUrl": (("url",), ()),
}
STEP_ALIASES: dict[str, str] = {"expect_text": "expectText", "expect_visible": "expectVisible", "expect_count": "expectCount",
                                "wait_for": "waitFor", "expect_url": "expectUrl", "wait": "waitFor"}
# A target's ``role`` must be one of these (an ARIA role Playwright's getByRole takes).
TARGET_ROLES: frozenset[str] = frozenset({
    "alert", "button", "cell", "checkbox", "columnheader", "combobox", "dialog", "form", "heading", "img", "link",
    "list", "listitem", "main", "navigation", "option", "radio", "region", "row", "rowgroup", "rowheader", "searchbox",
    "status", "tab", "table", "tabpanel", "textbox", "banner", "menuitem", "menu", "switch", "group", "paragraph"})
# The only step fields where ``{{key}}`` / ``{{totp:key}}`` are replaced (from the start body's ``credentials``).
TEMPLATE_FIELDS: frozenset[tuple[str, str]] = frozenset({("fill", "value"), ("otp", "value")})

STEPS_DOC = (
    "UI steps (anything else is a 400 from the service): "
    + "; ".join(f"{a} {{{', '.join(list(req) + [o + '?' for o in opt])}}}" for a, (req, opt) in UI_STEPS.items())
    + ". Any step may carry group. goto url is relative to target.base_url (a case that does not start with goto opens '/'). "
    "fill value may be ''. press key is one key name ('Enter', 'Tab'). otp types the code one character per textbox "
    "(inside within when given; one box gets the whole code). waitFor state: visible | hidden (default visible). "
    "expectText passes when the target's text CONTAINS text. expectCount count is an integer >= 0. expectUrl url (or "
    "contains / pattern) is a substring of the page URL, or '/regex/flags'. snake_case aliases are accepted: "
    + ", ".join(f"{k}->{v}" for k, v in STEP_ALIASES.items()) + "; 'done' is not a runnable step. "
    "target is an object, never a string: {role, name?} | {label} | {text}, plus exact? and within? (a target that "
    "scopes it). role is one of: " + ", ".join(sorted(TARGET_ROLES)) + ". exact defaults to true (the whole name / label / "
    "text must match); exact: false matches a substring. A target must match exactly ONE element: two or more fail the "
    "step as ambiguous (narrow it with within or a more exact name). {{key}} and {{totp:key}} are replaced only in "
    "fill.value and otp.value, from the credentials keys of the start body; anywhere else they are sent as typed.")


def unknown_fields(body: Any) -> list[str]:
    """Top-level keys of a start body the Jobs service does not read (empty when the type is unknown: it rejects that)."""
    if not isinstance(body, dict):
        return []
    known = JOB_FIELDS.get(str(body.get("type")))
    return sorted(str(k) for k in body if known is not None and k not in known)


def _op(operation: str, **params: Any) -> Any:
    items = _call("Jobs", {"operation": operation, **{k: v for k, v in params.items() if v is not None}}, [{}])
    return items[0] if items else None


def start(body: dict) -> dict:
    """Start a job; returns ``{"job_id": ..., "status": "queued"}`` at once. Bodies by ``type``:
    test-run ``{type, label, target:{base_url, allowed_hosts:["host:port"]}, policy?:{forbidden_names:[...],
    forbidden_requests:["POST /path"]}, credentials?:{...}, concurrency?, cases:[{id: "TC-1" (your case key), kind:
    "ui", title, steps:[{action, ...the fields of that step, listed below}]} | {id, kind: "api", title, requests:[{method, path, headers?,
    body?, expect:{status, json_path_equals?, schema?}, save?:{var: "$.id"}}]}]};
    triage ``{type: "triage", job_id: <the test-run job>, case_id: <the case key>}``;
    explore ``{type: "explore", target, goal}``. A field the service does not read (e.g. ``credentials_ref``) raises
    BrokerError ``unknown_job_field`` instead of being dropped silently."""
    extra = unknown_fields(body)
    if extra:
        raise BrokerError("unknown_job_field", f"the Jobs service ignores: {', '.join(extra)} "
                          f"(allowed for {body.get('type')}: {', '.join(sorted(JOB_FIELDS[body['type']]))})", 400)
    return _op("start", body=body)


start.__doc__ = f"{start.__doc__} {STEPS_DOC}"


def get(job_id: str) -> dict:
    """``{job_id, type, label, status, started_at, finished_at, progress:{done, total, current_case_id, current_step},
    results:[{case_id, kind, status, duration_ms, failed_step?:{index, step, description}, error?, artifacts:[names]}],
    output?, error?}``. Job status: queued | running | passed | failed | error | cancelled. A result's ``case_id`` is
    the ``id`` you gave the case in start(); its status: queued | running | passed | failed | blocked (policy stopped a
    step) | error | cancelled. Triage ``output``: ``{verdict: bug | app_changed | flake | environment | test_bug,
    confidence, summary, evidence:[{kind, text}], proposed_fix?:{steps_before, steps_after, explanation}, dom_diff:{baseline, summary, changes:[{kind: renamed | removed | added, role, before, after}], text_removed, text_added}, network_diff:[{method, path, kind, expected, actual}], screenshots:{before, after}}`` (``screenshots`` are artifact names of the triage job: fetch them with artifact()). Progress also has ``paused``.
    Explore ``output``: ``{steps, notes}``."""
    return _op("get", job_id=job_id)


def events(job_id: str, after: int = 0) -> dict:
    """``{events: [{seq, ts, case_id, type, text, data?}], next}``; pass ``next`` as ``after`` on the next call."""
    return _op("events", job_id=job_id, after=int(after or 0))


def frame(job_id: str) -> dict | None:
    """The latest browser screenshot as ``{"content_type": "image/jpeg", "base64": ...}``, or None when there is none
    (no UI case running yet, or the frame is over 2 MB)."""
    out = _op("frame", job_id=job_id) or {}
    return out.get("frame")


def artifacts(job_id: str) -> list:
    """``[{name, bytes, content_type, case_id}]``: screenshots, video.webm, trace.zip, <case>.api.json, evidence.json."""
    out = _op("artifacts", job_id=job_id) or {}
    return list(out.get("artifacts") or [])


def artifact(job_id: str, name: str) -> dict:
    """``{"name", "content_type", "size", "base64"}``. Over 6 MB: no ``base64``; ``too_large: True`` and ``ref``."""
    return _op("artifact", job_id=job_id, name=name)


def cancel(job_id: str) -> dict:
    """Ask the Jobs service to cancel the job."""
    return _op("cancel", job_id=job_id) or {}


def control(job_id: str, action: str) -> dict:
    """Pause, step or resume a running job's UI cases (honoured between steps): ``action`` is ``"pause"`` (hold before the
    next step), ``"step"`` (run exactly one more step, then hold again) or ``"resume"``. Returns ``{job_id, status,
    action, paused, step_credits}``; BrokerError ``job_not_found`` for an unknown job, ``jobs_rejected`` if the job has
    already finished. ``"takeover"`` is reserved: the service answers BrokerError ``jobs_not_implemented`` for now."""
    return _op("control", job_id=job_id, action=action) or {}
