"""Shared serialization utilities for database integration nodes.

Provides common value serialization (Decimal, datetime, UUID, etc.) and
JSON parameter parsing used across Postgres, Neo4j, MongoDB, and other
integration nodes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID


def serialize_value(val: Any) -> Any:
    """Convert common Python/DB types to JSON-safe values.

    Handles: Decimal, datetime, date, time, timedelta, bytes, UUID,
    and recurses into lists and dicts.

    For DB-specific types (e.g. Neo4j Node, MongoDB ObjectId), call this
    as a fallback after handling DB-specific cases.
    """
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, timedelta):
        return val.total_seconds()
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, list):
        return [serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    return val


def parse_json_params(params_raw: Any, *, default: Any = None) -> Any:
    """Parse query parameters from an expression-resolved value.

    Handles str (JSON-parses it), list, dict, and None.

    Args:
        params_raw: The raw parameter value to parse.
        default: Default to return for empty/None input.
                 Use [] for positional params (Postgres),
                 {} for named params (Neo4j, MongoDB).
    """
    if default is None:
        default = []

    if isinstance(params_raw, (list, dict)):
        return params_raw
    if isinstance(params_raw, str):
        raw = params_raw.strip()
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # For list defaults, wrap the string as a single-item list
            if isinstance(default, list):
                return [raw]
            return default
    if params_raw is None:
        return default
    # Scalar fallback
    if isinstance(default, list):
        return [params_raw]
    return default
