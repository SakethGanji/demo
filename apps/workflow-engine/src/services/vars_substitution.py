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
