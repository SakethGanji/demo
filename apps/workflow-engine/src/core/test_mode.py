"""Test mode (UI-DESIGN §9 D3, §10.3, re-review N7).

``WORKFLOW_TEST_MODE=1`` enables test-only knobs (the per-app ``token_ttl_s``, the ``/api/_test/*``
toggles). It is honoured **only** when the engine's database name starts with ``workflows_test_``;
otherwise startup is refused (``check_startup``) and the knobs stay off (``enabled``).
All values are read from ``os.environ`` only — never from ``.env`` or pydantic settings.
"""

from __future__ import annotations

import os

TEST_DB_PREFIX = "workflows_test_"


def _db_name() -> str:
    return os.environ.get("WORKFLOW_DB_NAME") or ""


def requested() -> bool:
    return os.environ.get("WORKFLOW_TEST_MODE") == "1"


def enabled() -> bool:
    """True only for test mode on a test database (both read from os.environ)."""
    return requested() and _db_name().startswith(TEST_DB_PREFIX)


def check_startup() -> None:
    """Refuse to start in test mode against a database that is not a test database (N7)."""
    if requested() and not _db_name().startswith(TEST_DB_PREFIX):
        raise RuntimeError(
            f"test mode refused: WORKFLOW_DB_NAME must start with '{TEST_DB_PREFIX}' (got {_db_name()!r})")
