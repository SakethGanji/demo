"""Validator for MongoQueryTool.

Pure functions used by ``mongo_query_tool.py`` to constrain what an LLM may
ask MongoDB. Two things are enforced:

1. **Operator allowlist.** Only a known-safe set of filter operators and
   aggregation stages/expressions is permitted. Anything risky (``$where``,
   ``$function``, ``$lookup``, ``$merge``, ``$out``, ``$expr`` carrying a JS
   function, etc.) raises ``ValueError``.
2. **Mandatory filter.** Every query must scope to a configured field
   (typically the experiment / tenant id). The check is conservative — when
   the filter uses ``$or``/``$nor`` the mandatory field must appear in every
   branch, so the agent can never "OR out" of its scope.

These are sync functions returning ``None`` on success and raising
``ValueError`` with a human-readable message on failure.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Filter operators usable inside a `find` filter or `$match` stage.
_ALLOWED_FILTER_OPS: frozenset[str] = frozenset({
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte",
    "$in", "$nin",
    "$and", "$or", "$not", "$nor",
    "$exists", "$type",
    "$regex", "$options",  # $options always rides with $regex
    "$size", "$all", "$elemMatch",
})

# Top-level aggregation stages.
_ALLOWED_AGG_STAGES: frozenset[str] = frozenset({
    "$match", "$project", "$group", "$sort", "$limit", "$skip",
    "$unwind", "$count", "$addFields", "$set", "$sortByCount",
    "$bucket", "$facet", "$replaceRoot",
})

# Aggregation expressions usable inside stages.
_ALLOWED_AGG_EXPRS: frozenset[str] = frozenset({
    "$sum", "$avg", "$min", "$max", "$first", "$last", "$push",
    "$size", "$cond", "$ifNull", "$literal", "$type",
    "$toString", "$toLower", "$toUpper",
    "$add", "$subtract", "$multiply", "$divide",
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte",
    "$and", "$or", "$not",
    "$in", "$concat", "$arrayElemAt",
})

# Hard-deny: never permitted anywhere in the query.
_FORBIDDEN_OPS: frozenset[str] = frozenset({
    "$where", "$function", "$accumulator",
    "$lookup", "$out", "$merge", "$graphLookup", "$unionWith",
})

# Allowed within a $facet's sub-pipeline (stages only).
_MAX_FACET_SUBPIPES = 4


# ---------------------------------------------------------------------------
# Internal: filter validation
# ---------------------------------------------------------------------------

def _check_forbidden_recursive(value: Any) -> None:
    """Walk ``value`` and raise on any forbidden operator key."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _FORBIDDEN_OPS:
                raise ValueError(f"Forbidden operator: {k}")
            if k == "$expr":
                _check_expr_no_js(v)
            _check_forbidden_recursive(v)
    elif isinstance(value, list):
        for item in value:
            _check_forbidden_recursive(item)


def _check_expr_no_js(expr: Any) -> None:
    """``$expr`` is allowed, but reject if it embeds a JS function string."""
    if isinstance(expr, str):
        s = expr.strip()
        if s.startswith("function") or "=>" in s:
            raise ValueError("$expr containing a JS function string is forbidden")
    elif isinstance(expr, dict):
        for k, v in expr.items():
            if k in ("$function", "$where", "$accumulator"):
                raise ValueError(f"$expr containing {k} is forbidden")
            _check_expr_no_js(v)
    elif isinstance(expr, list):
        for item in expr:
            _check_expr_no_js(item)


def _validate_filter_operators(filter_: Any) -> None:
    """Recursively check that only allowlisted filter operators appear."""
    if isinstance(filter_, dict):
        for k, v in filter_.items():
            if k.startswith("$"):
                if k in _FORBIDDEN_OPS:
                    raise ValueError(f"Forbidden operator: {k}")
                if k == "$expr":
                    _check_expr_no_js(v)
                    continue
                if k == "$regex":
                    if not isinstance(v, str):
                        raise ValueError("$regex value must be a string")
                    continue
                if k not in _ALLOWED_FILTER_OPS:
                    raise ValueError(f"Filter operator not allowed: {k}")
            # Recurse into the value.
            _validate_filter_operators(v)
    elif isinstance(filter_, list):
        for item in filter_:
            _validate_filter_operators(item)


def _branch_enforces_mandatory(
    branch: Any, mandatory_field: str, expected_value: Any
) -> bool:
    """Return True if ``branch`` provably constrains ``mandatory_field``.

    A branch enforces the mandatory field iff one of:
      - it's a dict that directly contains ``{mandatory_field: <expected>}``
        (either as a literal or wrapped in a permitted equality op);
      - it's a dict carrying ``$and`` where any sub-branch enforces it;
      - it's a dict carrying ``$or`` where EVERY sub-branch enforces it.
    """
    if not isinstance(branch, dict):
        return False

    # Direct equality on the field.
    if mandatory_field in branch:
        val = branch[mandatory_field]
        if val == expected_value:
            return True
        # Permit {$eq: expected} or {$in: [expected]}.
        if isinstance(val, dict):
            if "$eq" in val and val["$eq"] == expected_value:
                return True
            if "$in" in val and isinstance(val["$in"], list) and val["$in"] == [expected_value]:
                return True
        return False

    # $and: any sub-branch enforcing is sufficient.
    if "$and" in branch and isinstance(branch["$and"], list):
        if any(
            _branch_enforces_mandatory(sub, mandatory_field, expected_value)
            for sub in branch["$and"]
        ):
            return True

    # $or / $nor: every sub-branch must enforce it (be conservative).
    for key in ("$or", "$nor"):
        if key in branch and isinstance(branch[key], list) and branch[key]:
            if all(
                _branch_enforces_mandatory(sub, mandatory_field, expected_value)
                for sub in branch[key]
            ):
                return True

    return False


def _enforces_mandatory(
    filter_: dict, mandatory_field: str, expected_value: Any
) -> bool:
    """Top-level mandatory-field check for a find filter or $match body."""
    if not isinstance(filter_, dict):
        return False
    return _branch_enforces_mandatory(filter_, mandatory_field, expected_value)


# ---------------------------------------------------------------------------
# Public: find / aggregate validation
# ---------------------------------------------------------------------------

def validate_find(
    filter_: dict,
    projection: dict | None,
    sort: dict | None,
    mandatory_field: str,
    expected_value: Any,
) -> None:
    """Validate a find query. Raises ``ValueError`` on rejection."""
    if not isinstance(filter_, dict):
        raise ValueError("filter must be an object")

    _check_forbidden_recursive(filter_)
    _validate_filter_operators(filter_)

    if not _enforces_mandatory(filter_, mandatory_field, expected_value):
        raise ValueError(
            f"Filter must constrain '{mandatory_field}' to "
            f"{expected_value!r} (top-level or in every $or branch)"
        )

    if projection is not None:
        if not isinstance(projection, dict):
            raise ValueError("projection must be an object")
        _check_forbidden_recursive(projection)

    if sort is not None:
        if not isinstance(sort, dict):
            raise ValueError("sort must be an object")


def _validate_agg_stage(
    stage: dict, mandatory_field: str, expected_value: Any, _depth: int = 0
) -> None:
    """Validate one aggregation stage shape + nested forbidden ops."""
    if not isinstance(stage, dict) or len(stage) != 1:
        raise ValueError("Each pipeline stage must be a single-key object")
    (stage_name, stage_body), = stage.items()

    if stage_name in _FORBIDDEN_OPS:
        raise ValueError(f"Forbidden stage: {stage_name}")
    if stage_name not in _ALLOWED_AGG_STAGES:
        raise ValueError(f"Aggregation stage not allowed: {stage_name}")

    # Always recurse for forbidden ops inside the stage body.
    _check_forbidden_recursive(stage_body)

    if stage_name == "$match":
        if isinstance(stage_body, dict):
            _validate_filter_operators(stage_body)

    if stage_name == "$facet":
        if not isinstance(stage_body, dict):
            raise ValueError("$facet body must be an object")
        if len(stage_body) > _MAX_FACET_SUBPIPES:
            raise ValueError(
                f"$facet may have at most {_MAX_FACET_SUBPIPES} sub-pipelines"
            )
        if _depth >= 1:
            raise ValueError("$facet cannot be nested inside another $facet")
        for sub_name, sub_pipe in stage_body.items():
            if not isinstance(sub_pipe, list):
                raise ValueError(
                    f"$facet.{sub_name} must be a list of stages"
                )
            for sub_stage in sub_pipe:
                _validate_agg_stage(
                    sub_stage, mandatory_field, expected_value, _depth + 1
                )


def validate_aggregate(
    pipeline: list,
    mandatory_field: str,
    expected_value: Any,
    max_stages: int = 8,
) -> None:
    """Validate an aggregation pipeline. Raises ``ValueError`` on rejection."""
    if not isinstance(pipeline, list):
        raise ValueError("pipeline must be a list of stages")
    if not pipeline:
        raise ValueError("pipeline must contain at least one stage")
    if len(pipeline) > max_stages:
        raise ValueError(
            f"pipeline may contain at most {max_stages} stages "
            f"(got {len(pipeline)})"
        )

    first = pipeline[0]
    if not isinstance(first, dict) or "$match" not in first:
        raise ValueError("pipeline must begin with a $match stage")
    match_body = first["$match"]
    if not isinstance(match_body, dict):
        raise ValueError("leading $match must be an object")
    if not _enforces_mandatory(match_body, mandatory_field, expected_value):
        raise ValueError(
            f"Leading $match must constrain '{mandatory_field}' to "
            f"{expected_value!r}"
        )

    for stage in pipeline:
        _validate_agg_stage(stage, mandatory_field, expected_value)


# ---------------------------------------------------------------------------
# Helpers used by the tool wrapper
# ---------------------------------------------------------------------------

def apply_default_projection(
    projection: dict | None, default_strip: list[str]
) -> dict:
    """If no projection given, build one that strips heavy default fields.

    When the caller provides a projection, return it unchanged. The agent is
    presumed to know what it wants in that case. Otherwise build a projection
    of the form ``{field: 0, ...}`` so the listed fields are excluded.
    """
    if projection is not None:
        return projection
    if not default_strip:
        return {}
    return {field: 0 for field in default_strip}


def clamp_limit(limit: int | None, max_limit: int) -> int:
    """Clamp ``limit`` to ``[1, max_limit]``. ``None`` becomes ``max_limit``."""
    if limit is None:
        return max_limit
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return max_limit
    if n <= 0:
        return max_limit
    if n > max_limit:
        return max_limit
    return n
