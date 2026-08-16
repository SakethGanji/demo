"""Slug validation, normalization, and generation for published apps."""

from __future__ import annotations

import re
import secrets

# Reserved at the top of the public path (`/a/{slug}`). Anything an attacker
# could use to shadow our own routes goes here.
RESERVED_SLUGS = frozenset(
    {
        "_assets",
        "_unlock",
        "admin",
        "api",
        "app",
        "apps",
        "assets",
        "health",
        "static",
        "system",
        "webhook",
        "webhooks",
        "www",
    }
)

_SLUG_RE = re.compile(r"^[a-z0-9](?:-?[a-z0-9])*$")
SLUG_MIN_LEN = 3
SLUG_MAX_LEN = 63


class SlugValidationError(ValueError):
    """Raised when a slug fails format/reserved checks."""


def normalize_slug(name: str) -> str:
    """Best-effort slug from an arbitrary app name. May still need uniqueness check."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if len(s) < SLUG_MIN_LEN:
        # Pad with random suffix so derived slugs from short names are still valid.
        s = (s + "-" + secrets.token_hex(2))[:SLUG_MAX_LEN]
    return s[:SLUG_MAX_LEN]


def validate_slug(slug: str) -> None:
    """Raise SlugValidationError if `slug` violates format or reserved rules."""
    if not (SLUG_MIN_LEN <= len(slug) <= SLUG_MAX_LEN):
        raise SlugValidationError(
            f"slug must be {SLUG_MIN_LEN}-{SLUG_MAX_LEN} chars"
        )
    if not _SLUG_RE.match(slug):
        raise SlugValidationError(
            "slug must be lowercase alphanumeric with single hyphens"
        )
    if slug in RESERVED_SLUGS:
        raise SlugValidationError(f"slug '{slug}' is reserved")
