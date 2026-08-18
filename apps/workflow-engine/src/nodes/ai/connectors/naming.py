"""Turning a remote tool identifier into a name a model may call.

Three separate constraints collide here:

* ``src/engine/tool_schema.py::validate_tool_definition`` (and every provider)
  requires ``^[a-zA-Z_][a-zA-Z0-9_]*$``.
* OpenAI and Anthropic both cap function names at 64 characters. The analytics
  service's own OpenAPI document contains operationIds up to 116 characters
  (``explore_sheet_column_api_v1_datasets__dataset_id__versions_...``), so the
  cap is not hypothetical.
* Truncating to fit collides: forty operations under ``/datasets/{id}/...``
  share their first 57 characters.

So: sanitize, collapse, cap, and disambiguate with a hash of the *original*
identifier — deterministic, so the same remote tool gets the same name on every
re-discovery even if the set of sibling tools changed around it.

The name is nevertheless PINNED in ``connector_tools.tool_name`` at first
import and never recomputed. A model's transcript, a saved agent binding and a
user's muscle memory all reference the name; a rename on re-discovery would
silently break every one of them. :func:`derive_tool_name` takes a ``pinned``
argument for exactly that reason.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

MAX_TOOL_NAME = 64
_VALID = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_ILLEGAL = re.compile(r"[^a-zA-Z0-9_]+")
_RUNS = re.compile(r"_{2,}")

#: Reserved because the engine's own built-in tools use them; a connector tool
#: that shadowed one would win or lose by dict-ordering luck.
RESERVED: frozenset[str] = frozenset({"execute", "name", "description", "input_schema"})


def _digest(value: str, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def sanitize(raw: str) -> str:
    """Map an arbitrary identifier onto ``[a-zA-Z_][a-zA-Z0-9_]*``."""
    text = _ILLEGAL.sub("_", (raw or "").strip())
    text = _RUNS.sub("_", text).strip("_")
    if not text:
        return "tool"
    if not text[0].isalpha() and text[0] != "_":
        text = "t_" + text
    return text


def _truncate(name: str, source: str, limit: int) -> str:
    """Cut *name* to *limit*, ending in a hash of *source* so it stays unique."""
    if len(name) <= limit:
        return name
    suffix = "_" + _digest(source, 6)
    head = name[: limit - len(suffix)].rstrip("_")
    return head + suffix


def derive_tool_name(
    remote_id: str,
    *,
    prefix: str = "",
    taken: Iterable[str] = (),
    pinned: str | None = None,
    max_length: int = MAX_TOOL_NAME,
) -> str:
    """Return the name a model will call *remote_id* by.

    Args:
        remote_id: the tool's identifier on the remote server.
        prefix: optional per-connector namespace, e.g. ``analytics``.
        taken: names already assigned in this connector (or across the agent).
        pinned: the name this tool was imported under. Returned unchanged if it
            is still legal — a pinned name outranks every rule below.
        max_length: hard cap; 64 is the provider minimum.
    """
    if pinned and _VALID.match(pinned) and len(pinned) <= max_length:
        return pinned

    taken_set = {t for t in taken}
    source = f"{prefix}:{remote_id}" if prefix else str(remote_id)

    base = sanitize(remote_id)
    if prefix:
        clean_prefix = sanitize(prefix)
        base = f"{clean_prefix}_{base}"

    candidate = _truncate(base, source, max_length)

    if candidate not in taken_set and candidate.lower() not in RESERVED:
        return candidate

    # Collision: a deterministic hash suffix first (stable across re-discovery),
    # then a counter for the vanishingly unlikely case that the hash collides.
    for attempt in range(64):
        seed = source if attempt == 0 else f"{source}#{attempt}"
        suffix = "_" + _digest(seed, 6)
        head = base[: max_length - len(suffix)].rstrip("_")
        candidate = head + suffix
        if candidate not in taken_set and candidate.lower() not in RESERVED:
            return candidate

    raise ValueError(f"could not derive a unique tool name for {remote_id!r}")


def assign_tool_names(
    remote_ids: Iterable[str],
    *,
    prefix: str = "",
    pinned: dict[str, str] | None = None,
    taken: Iterable[str] = (),
    max_length: int = MAX_TOOL_NAME,
) -> dict[str, str]:
    """Name a whole discovery batch at once, honouring pins and collisions."""
    pinned = pinned or {}
    used = set(taken)
    # Pins are claimed first: a new tool must never steal an existing tool's
    # name and push the old one onto a hash suffix.
    for remote_id in remote_ids:
        name = pinned.get(remote_id)
        if name:
            used.add(name)

    out: dict[str, str] = {}
    for remote_id in remote_ids:
        name = derive_tool_name(
            remote_id,
            prefix=prefix,
            taken=used - {pinned.get(remote_id, "")},
            pinned=pinned.get(remote_id),
            max_length=max_length,
        )
        out[remote_id] = name
        used.add(name)
    return out


def is_valid_tool_name(name: str, *, max_length: int = MAX_TOOL_NAME) -> bool:
    return bool(name) and len(name) <= max_length and bool(_VALID.match(name))
