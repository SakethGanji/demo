"""SQL safety helpers — quoting, sanitisation, type coercion."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
from fastapi import HTTPException


def quote_ident(name: str) -> str:
    """Quote a SQL identifier to prevent injection."""
    return '"' + name.replace('"', '""') + '"'


def safe_value(val: Any) -> Any:
    """Convert database/numpy types to JSON-safe Python types."""
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        v = float(val)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(val, np.bool_):
        return bool(val)
    if hasattr(val, "isoformat"):
        return str(val)
    return val


_DANGEROUS_SQL_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|UNION)\b",
    re.IGNORECASE,
)


def sanitize_filter_expr(expr: str) -> str:
    """Validate a SQL filter expression to prevent injection."""
    if ";" in expr:
        raise HTTPException(400, "Filter expression must not contain semicolons")
    if _DANGEROUS_SQL_RE.search(expr):
        raise HTTPException(400, "Filter expression contains disallowed SQL keywords")
    return expr
