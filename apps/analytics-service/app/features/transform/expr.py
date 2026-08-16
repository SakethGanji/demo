"""Typed expression tree for computed columns (ROADMAP §20).

A formula is a tree of discriminated-union nodes, not a string — so there is no
expression parser and no place for user text to reach SQL. The compiler's
standing invariant:

    **No node ever emits user-supplied text into SQL.** Column references go
    through ``quote_ident`` after resolving against the running schema, literal
    values become ``?`` binds, and every operator/part/type token
    (``op``/``fn``/``part``/``to``) is a pydantic ``Literal`` mapped to a fixed
    SQL token from a whitelist in this module.

Compilation is pure: :func:`compile_expr` takes the node, the running column
schema, and a bind list, and returns the SQL fragment plus the expression's
inferred DuckDB type. The type comes back so the step compiler can fold a
computed column into the running schema and so downstream nodes can be type
checked (``date_extract`` needs a temporal operand, ``arith`` needs numerics).
"""

from __future__ import annotations

from typing import Annotated, Literal, NamedTuple, Union

from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import ProblemException
from app.shared.constants import is_datetime_duckdb_type, is_numeric_duckdb_type
from app.shared.query.schemas import FilterGroup
from app.shared.utils.sql import quote_ident

# Deeply nested trees are a denial-of-service shape (and a recursion limit
# away from a 500), so the compiler refuses past this depth.
MAX_EXPR_DEPTH = 12

# --- Whitelisted SQL tokens ---------------------------------------------------
# Every token that can ever be interpolated into SQL lives in one of these maps.
# The pydantic Literal on each field restricts the key set at validation time;
# the map turns the key into the fixed token.

CastTarget = Literal[
    "varchar", "text", "integer", "bigint", "double", "decimal",
    "boolean", "date", "timestamp", "time",
]
CAST_TYPES: dict[str, str] = {
    "varchar": "VARCHAR", "text": "VARCHAR",
    "integer": "INTEGER", "bigint": "BIGINT",
    "double": "DOUBLE", "decimal": "DECIMAL(18,6)",
    "boolean": "BOOLEAN",
    "date": "DATE", "timestamp": "TIMESTAMP", "time": "TIME",
}

DatePart = Literal["year", "month", "day", "hour", "minute", "dow", "week", "quarter"]
DATE_PARTS: dict[str, str] = {
    "year": "YEAR", "month": "MONTH", "day": "DAY", "hour": "HOUR",
    "minute": "MINUTE", "dow": "DOW", "week": "WEEK", "quarter": "QUARTER",
}

ArithFn = Literal["add", "sub", "mul", "div", "mod"]
ARITH_OPS: dict[str, str] = {
    "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%",
}

StrFn = Literal["lower", "upper", "trim", "length", "substr"]

# Type tokens used when a node's result type is known statically.
_VARCHAR, _BIGINT, _DOUBLE, _BOOLEAN = "VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN"
# The type of a bare NULL / a value we cannot infer. Type checks treat it as
# compatible with everything rather than guessing wrong.
UNKNOWN = "UNKNOWN"


# --- Nodes --------------------------------------------------------------------

class ColExpr(BaseModel):
    """Reference to a column of the running schema."""

    op: Literal["col"] = "col"
    name: str = Field(..., min_length=1, description="Column name (normalized or physical)")


class LitExpr(BaseModel):
    """A constant. The value becomes a bind, never SQL text."""

    op: Literal["lit"] = "lit"
    value: bool | int | float | str | None = Field(
        default=None, description="Constant operand; null compiles to SQL NULL")


class ArithExpr(BaseModel):
    """Binary arithmetic over two numeric operands."""

    op: Literal["arith"] = "arith"
    fn: ArithFn = Field(..., description="add | sub | mul | div | mod")
    left: "Expr"
    right: "Expr"


class ConcatExpr(BaseModel):
    """String concatenation with an optional separator."""

    op: Literal["concat"] = "concat"
    parts: list["Expr"] = Field(..., min_length=2, description="Operands, in order")
    separator: str = Field(default="", description="Inserted between parts (a bind)")


class WhenClause(BaseModel):
    """One ``WHEN <condition> THEN <value>`` arm of an ``if`` expression."""

    when: FilterGroup = Field(..., description="Condition, reusing the query DSL's FilterGroup")
    then: "Expr" = Field(..., description="Value when the condition holds")


class IfExpr(BaseModel):
    """Multi-arm conditional — compiles to a single CASE expression."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["if"] = "if"
    cases: list[WhenClause] = Field(..., min_length=1, description="WHEN/THEN arms, in order")
    else_: "Expr | None" = Field(
        default=None, alias="else", description="Fallback value; omitted means NULL")


class CoalesceExpr(BaseModel):
    """First non-NULL operand."""

    op: Literal["coalesce"] = "coalesce"
    args: list["Expr"] = Field(..., min_length=2, description="Operands, in order")


class RoundExpr(BaseModel):
    """Round a numeric operand to *digits* decimal places."""

    op: Literal["round"] = "round"
    value: "Expr"
    digits: int = Field(default=0, ge=0, le=12, description="Decimal places")


class DateExtractExpr(BaseModel):
    """Extract a whitelisted part from a DATE/TIMESTAMP operand."""

    op: Literal["date_extract"] = "date_extract"
    part: DatePart = Field(..., description="year | month | day | hour | minute | dow | week | quarter")
    value: "Expr"


class CastExpr(BaseModel):
    """Cast to a whitelisted type."""

    op: Literal["cast"] = "cast"
    value: "Expr"
    to: CastTarget = Field(..., description="Target type (whitelisted)")


class StrExpr(BaseModel):
    """Single-operand string function."""

    op: Literal["str"] = "str"
    fn: StrFn = Field(..., description="lower | upper | trim | length | substr")
    value: "Expr"
    start: int | None = Field(default=None, ge=1, description="substr: 1-based start position")
    length: int | None = Field(default=None, ge=0, description="substr: number of characters")


Expr = Annotated[
    Union[
        ColExpr, LitExpr, ArithExpr, ConcatExpr, IfExpr, CoalesceExpr,
        RoundExpr, DateExtractExpr, CastExpr, StrExpr,
    ],
    Field(discriminator="op"),
]

for _model in (ArithExpr, ConcatExpr, WhenClause, IfExpr, CoalesceExpr,
               RoundExpr, DateExtractExpr, CastExpr, StrExpr):
    _model.model_rebuild()


# --- Compilation --------------------------------------------------------------

class CompiledExpr(NamedTuple):
    """An expression's SQL fragment plus the DuckDB type it evaluates to."""

    sql: str
    dtype: str


def resolve_column(name: str, schema: list[dict]) -> dict:
    """Resolve a column reference against *schema* — normalized name first.

    Same precedence (and same problem+json shape) as the query DSL's resolver,
    so an unknown reference reads identically wherever it is made.
    """
    for c in schema:
        if c.get("normalized_name") == name:
            return c
    for c in schema:
        if c["name"] == name:
            return c
    raise ProblemException(
        400, f"Unknown column: '{name}'", code="unknown-column", column=name,
        available=[c.get("normalized_name") or c["name"]
                   for c in sorted(schema, key=lambda c: c.get("position", 0))],
    )


def _mismatch(detail: str, **extra) -> ProblemException:
    return ProblemException(400, detail, code="operator-type-mismatch", **extra)


def _require_numeric(compiled: CompiledExpr, what: str) -> None:
    if compiled.dtype != UNKNOWN and not is_numeric_duckdb_type(compiled.dtype):
        raise _mismatch(f"{what} requires a numeric operand; got {compiled.dtype}",
                        dtype=compiled.dtype)


def _require_text(compiled: CompiledExpr, what: str) -> None:
    if compiled.dtype != UNKNOWN and not compiled.dtype.upper().startswith(
            ("VARCHAR", "CHAR", "TEXT", "STRING")):
        raise _mismatch(f"{what} requires a text operand; got {compiled.dtype}",
                        dtype=compiled.dtype)


def _literal_type(value: bool | int | float | str | None) -> str:
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return _BOOLEAN
    if isinstance(value, int):
        return _BIGINT
    if isinstance(value, float):
        return _DOUBLE
    return _VARCHAR


def _compile_condition(group: FilterGroup, schema: list[dict], binds: list,
                       source_relation: str) -> str:
    """Compile a FilterGroup to a boolean SQL fragment against *schema*.

    Reuses the query DSL's validator (column resolution + operator/type
    checks) and ``compile_filter`` verbatim. Set-relative operators
    (``top_n``, ``is_duplicate``, …) that ``compile_filter`` writes against the
    ``_filter_src`` view are retargeted at *source_relation* — inside a CTE
    chain the "whole input" of a step is simply the preceding CTE.
    """
    from app.shared.filters import compile_filter
    from app.shared.query.compile import _remap_columns
    from app.shared.query.validate import _check_filter

    mapping: dict[str, str] = {}
    _check_filter(group, schema, mapping)
    clause = compile_filter(_remap_columns(group.model_dump(), mapping), binds)
    # `compile_filter` only ever emits the view name as a FROM target.
    return clause.replace("FROM _filter_src", f"FROM {source_relation}")


def compile_expr(node, schema: list[dict], binds: list, *,
                 source_relation: str = "_filter_src", depth: int = 0) -> CompiledExpr:
    """Compile an expression node to SQL + its result type.

    *schema* is the running column schema (rows shaped like ``schema_json``),
    *binds* is appended to in evaluation order, and *source_relation* names the
    relation set-relative filter operators inside ``if`` conditions resolve
    against. Pure: no I/O, no connection.
    """
    if depth > MAX_EXPR_DEPTH:
        raise ProblemException(
            400, f"Expression nests deeper than {MAX_EXPR_DEPTH} levels",
            code="expression-too-deep")

    def sub(child) -> CompiledExpr:
        return compile_expr(child, schema, binds,
                            source_relation=source_relation, depth=depth + 1)

    if isinstance(node, ColExpr):
        col = resolve_column(node.name, schema)
        return CompiledExpr(quote_ident(col["name"]), col.get("dtype") or UNKNOWN)

    if isinstance(node, LitExpr):
        if node.value is None:
            return CompiledExpr("NULL", UNKNOWN)
        binds.append(node.value)
        return CompiledExpr("?", _literal_type(node.value))

    if isinstance(node, ArithExpr):
        left, right = sub(node.left), sub(node.right)
        _require_numeric(left, f"arith '{node.fn}'")
        _require_numeric(right, f"arith '{node.fn}'")
        token = ARITH_OPS[node.fn]
        if node.fn == "div":
            # Integer / integer would truncate; force real division.
            return CompiledExpr(f"(CAST({left.sql} AS DOUBLE) {token} {right.sql})", _DOUBLE)
        if node.fn == "mod":
            return CompiledExpr(f"({left.sql} {token} {right.sql})", left.dtype)
        wide = _DOUBLE if _DOUBLE in (left.dtype, right.dtype) else left.dtype
        return CompiledExpr(f"({left.sql} {token} {right.sql})", wide)

    if isinstance(node, ConcatExpr):
        binds.append(node.separator)
        parts = [sub(p).sql for p in node.parts]
        # CONCAT_WS skips NULL parts, which is the friendlier concat semantic.
        return CompiledExpr(f"CONCAT_WS(?, {', '.join(parts)})", _VARCHAR)

    if isinstance(node, IfExpr):
        arms: list[str] = []
        result_type = UNKNOWN
        for case in node.cases:
            condition = _compile_condition(case.when, schema, binds, source_relation)
            then = sub(case.then)
            if result_type == UNKNOWN:
                result_type = then.dtype
            arms.append(f"WHEN {condition or 'TRUE'} THEN {then.sql}")
        otherwise = "NULL"
        if node.else_ is not None:
            compiled_else = sub(node.else_)
            otherwise = compiled_else.sql
            if result_type == UNKNOWN:
                result_type = compiled_else.dtype
        return CompiledExpr(f"CASE {' '.join(arms)} ELSE {otherwise} END", result_type)

    if isinstance(node, CoalesceExpr):
        compiled = [sub(a) for a in node.args]
        dtype = next((c.dtype for c in compiled if c.dtype != UNKNOWN), UNKNOWN)
        return CompiledExpr(f"COALESCE({', '.join(c.sql for c in compiled)})", dtype)

    if isinstance(node, RoundExpr):
        value = sub(node.value)
        _require_numeric(value, "round")
        binds.append(node.digits)
        return CompiledExpr(f"ROUND({value.sql}, ?)", _DOUBLE)

    if isinstance(node, DateExtractExpr):
        value = sub(node.value)
        if value.dtype != UNKNOWN and not is_datetime_duckdb_type(value.dtype):
            raise _mismatch(
                f"date_extract requires a DATE/TIMESTAMP operand; got {value.dtype}",
                dtype=value.dtype, part=node.part)
        return CompiledExpr(f"EXTRACT({DATE_PARTS[node.part]} FROM {value.sql})", _BIGINT)

    if isinstance(node, CastExpr):
        value = sub(node.value)
        target = CAST_TYPES[node.to]
        return CompiledExpr(f"CAST({value.sql} AS {target})", target)

    if isinstance(node, StrExpr):
        value = sub(node.value)
        if node.fn == "length":
            _require_text(value, "str 'length'")
            return CompiledExpr(f"LENGTH({value.sql})", _BIGINT)
        if node.fn == "substr":
            _require_text(value, "str 'substr'")
            if node.start is None:
                raise ProblemException(
                    400, "str 'substr' requires a 'start' position",
                    code="invalid-expression")
            binds.append(node.start)
            if node.length is None:
                return CompiledExpr(f"SUBSTR({value.sql}, ?)", _VARCHAR)
            binds.append(node.length)
            return CompiledExpr(f"SUBSTR({value.sql}, ?, ?)", _VARCHAR)
        _require_text(value, f"str '{node.fn}'")
        fn = {"lower": "LOWER", "upper": "UPPER", "trim": "TRIM"}[node.fn]
        return CompiledExpr(f"{fn}({value.sql})", _VARCHAR)

    # Unreachable: the discriminated union rejects unknown ``op`` at validation.
    raise ProblemException(
        400, f"Unsupported expression node: {getattr(node, 'op', type(node).__name__)}",
        code="invalid-expression")
