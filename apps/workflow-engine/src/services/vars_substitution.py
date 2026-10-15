"""Server-side substitution for {{ $vars.KEY }} placeholders.

Applied at every workflow ingress point (run/webhook/stream) so callers can
embed `{{ $vars.SLACK_TOKEN }}` literally in their request body, headers, or
query params, and the backend swaps in the decrypted secret before the
workflow ever sees the data. Real secret never traverses the caller.

Distinct from the expression engine, which resolves expressions on node
PARAMETERS at execution time. This runs on the inbound REQUEST PAYLOAD,
once, before NodeData is constructed. Both end up with the same value;
together they mean `{{ $vars.X }}` works no matter where you put it.

Unknown keys are left untouched (the literal `{{ $vars.X }}` stays in the
payload) so typos surface to the workflow author instead of failing silently.
Only `$vars` placeholders are touched — `{{ $json.X }}`, `{{ $node.X }}`,
etc. pass through unchanged for the expression engine to handle later.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

_VARS_RE = re.compile(r"\{\{\s*\$vars\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

T = TypeVar("T")


def substitute_vars(value: T, variables: dict[str, str]) -> T:
    """Walk a JSON-shaped value and replace {{ $vars.KEY }} with variables[KEY].

    Returns the value unchanged if `variables` is empty (common: webhook hit
    with an env that has no vars defined).
    """
    if not variables:
        return value
    return _walk(value, variables)


def _walk(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _VARS_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_walk(item, variables) for item in value]
    if isinstance(value, dict):
        return {k: _walk(v, variables) for k, v in value.items()}
    return value


def redact_secrets(text: str, secret_values: set[str], min_len: int = 4) -> str:
    """Replace any occurrence of a known secret VALUE in `text` with '***'.

    Symmetric to `substitute_vars`: substitution puts secrets IN (caller payload
    → workflow), redaction strips them OUT (resolved metadata → node_outputs/SSE).
    Used by nodes that emit user-visible execution metadata containing
    expression-resolved strings (e.g. `requestUrl` after `{{ $vars.X }}` was
    resolved into a URL query param).

    Values shorter than `min_len` are skipped to avoid masking coincidental
    substrings — a 2-char token would match parts of every URL path.
    """
    if not text or not secret_values:
        return text
    out = text
    for v in secret_values:
        if v and len(v) >= min_len:
            out = out.replace(v, "***")
    return out


def expand_secret_variants(secret_values: set[str], min_len: int = 4) -> set[str]:
    """Secret values plus common encodings (base64, base64url, URL-quoted, JSON-escaped).

    Used for full-response redaction (BACKEND-DESIGN §5.3): a value echoed back
    base64-encoded (e.g. inside a Basic auth header) is still masked.
    """
    import base64
    import json as _json
    from urllib.parse import quote, quote_plus

    out: set[str] = set()
    for v in secret_values or set():
        if not v or len(v) < min_len:
            continue
        raw = v.encode()
        for cand in (
            v,
            base64.b64encode(raw).decode(),
            base64.b64encode(raw).decode().rstrip("="),
            base64.urlsafe_b64encode(raw).decode().rstrip("="),
            quote(v, safe=""),
            quote_plus(v),
            _json.dumps(v)[1:-1],
        ):
            if cand and len(cand) >= min_len:
                out.add(cand)
    return out


def redact_deep(value: Any, secret_values: set[str], *, expanded: bool = False) -> Any:
    """Walk a JSON-shaped value and redact secret values (and their encodings) in every string.

    Dict keys are redacted too. Non-string scalars pass through.
    """
    if not secret_values:
        return value
    variants = secret_values if expanded else expand_secret_variants(secret_values)
    # Longest first so a value that contains another secret is fully masked.
    ordered = sorted(variants, key=len, reverse=True)

    def _r(s: str) -> str:
        for v in ordered:
            if v in s:
                s = s.replace(v, "***")
        return s

    def _walk(x: Any) -> Any:
        if isinstance(x, str):
            return _r(x)
        if isinstance(x, list):
            return [_walk(i) for i in x]
        if isinstance(x, tuple):
            return [_walk(i) for i in x]
        if isinstance(x, dict):
            return {(_r(k) if isinstance(k, str) else k): _walk(v) for k, v in x.items()}
        return x

    return _walk(value)
