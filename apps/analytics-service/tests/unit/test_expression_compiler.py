"""Unit tests — the computed-column expression compiler (ROADMAP §20).

The standing invariant under test: no node ever emits user-supplied text into
SQL. Values become binds, identifiers go through quote_ident, and operator/part/
type tokens come from whitelists — so these tests assert on both the SQL shape
and the bind list, and execute a sample against in-memory DuckDB.
"""

from __future__ import annotations

import duckdb
import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.errors import ProblemException
from app.features.transform.expr import (
    MAX_EXPR_DEPTH,
    ArithExpr,
    CastExpr,
    CoalesceExpr,
    ColExpr,
    ConcatExpr,
    DateExtractExpr,
    Expr,
    IfExpr,
    LitExpr,
    RoundExpr,
    StrExpr,
    WhenClause,
    compile_expr,
)
from app.shared.query.schemas import Filter, FilterGroup

EXPR = TypeAdapter(Expr)

SCHEMA = [
    {"name": "id", "normalized_name": "id", "dtype": "INTEGER", "position": 0},
    {"name": "Full Name", "normalized_name": "full_name", "dtype": "VARCHAR", "position": 1},
    {"name": "amount", "normalized_name": "amount", "dtype": "DOUBLE", "position": 2},
    {"name": "created", "normalized_name": "created", "dtype": "TIMESTAMP", "position": 3},
]


def compile_it(node):
    binds: list = []
    result = compile_expr(node, SCHEMA, binds)
    return result.sql, binds, result.dtype


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("""
        CREATE TABLE df AS SELECT * FROM (VALUES
            (1, 'Alice', 10.0, TIMESTAMP '2024-01-05 08:30:00'),
            (2, 'bob',   25.0, TIMESTAMP '2024-06-11 17:00:00'),
            (3, NULL,    NULL, TIMESTAMP '2023-12-31 23:59:00')
        ) t(id, "Full Name", amount, created)
    """)
    yield c
    c.close()


def evaluate(conn, node):
    sql, binds, _ = compile_it(node)
    return [r[0] for r in conn.execute(f"SELECT {sql} FROM df ORDER BY id", binds).fetchall()]


# --- one test per node type ---------------------------------------------------

def test_col_resolves_normalized_names_to_the_physical_identifier():
    sql, binds, dtype = compile_it(ColExpr(name="full_name"))
    assert sql == '"Full Name"'
    assert binds == []
    assert dtype == "VARCHAR"


def test_lit_becomes_a_bind_never_sql_text():
    sql, binds, dtype = compile_it(LitExpr(value="'; DROP TABLE users --"))
    assert sql == "?"
    assert binds == ["'; DROP TABLE users --"]
    assert dtype == "VARCHAR"


def test_null_literal_compiles_to_the_null_token_with_no_bind():
    sql, binds, _ = compile_it(LitExpr(value=None))
    assert sql == "NULL"
    assert binds == []


def test_arith_emits_a_whitelisted_operator():
    sql, binds, _ = compile_it(
        ArithExpr(fn="add", left=ColExpr(name="amount"), right=LitExpr(value=5)))
    assert sql == '("amount" + ?)'
    assert binds == [5]


def test_division_forces_real_division():
    sql, _, dtype = compile_it(
        ArithExpr(fn="div", left=ColExpr(name="id"), right=LitExpr(value=2)))
    assert sql == '(CAST("id" AS DOUBLE) / ?)'
    assert dtype == "DOUBLE"


def test_concat_binds_the_separator():
    sql, binds, dtype = compile_it(
        ConcatExpr(parts=[ColExpr(name="full_name"), LitExpr(value="!")], separator="-"))
    assert sql == 'CONCAT_WS(?, "Full Name", ?)'
    assert binds == ["-", "!"]
    assert dtype == "VARCHAR"


def test_if_compiles_to_a_case_expression():
    node = IfExpr(cases=[WhenClause(
        when=FilterGroup(conditions=[Filter(column="amount", op="gt", value=20)]),
        then=LitExpr(value="big"))], **{"else": LitExpr(value="small")})
    sql, binds, _ = compile_it(node)
    assert sql.startswith("CASE WHEN") and sql.endswith("END")
    assert binds == [20, "big", "small"]


def test_coalesce_takes_the_first_known_type():
    sql, binds, dtype = compile_it(
        CoalesceExpr(args=[ColExpr(name="amount"), LitExpr(value=0)]))
    assert sql == 'COALESCE("amount", ?)'
    assert binds == [0]
    assert dtype == "DOUBLE"


def test_round_binds_its_digits():
    sql, binds, dtype = compile_it(RoundExpr(value=ColExpr(name="amount"), digits=2))
    assert sql == 'ROUND("amount", ?)'
    assert binds == [2]
    assert dtype == "DOUBLE"


def test_date_extract_uses_a_whitelisted_part_token():
    sql, binds, dtype = compile_it(
        DateExtractExpr(part="year", value=ColExpr(name="created")))
    assert sql == 'EXTRACT(YEAR FROM "created")'
    assert binds == []
    assert dtype == "BIGINT"


def test_cast_uses_a_whitelisted_type_token():
    sql, _, dtype = compile_it(CastExpr(value=ColExpr(name="amount"), to="integer"))
    assert sql == 'CAST("amount" AS INTEGER)'
    assert dtype == "INTEGER"


def test_str_functions_map_to_fixed_tokens():
    assert compile_it(StrExpr(fn="lower", value=ColExpr(name="full_name")))[0] \
        == 'LOWER("Full Name")'
    assert compile_it(StrExpr(fn="length", value=ColExpr(name="full_name")))[2] == "BIGINT"
    sql, binds, _ = compile_it(
        StrExpr(fn="substr", value=ColExpr(name="full_name"), start=2, length=3))
    assert sql == 'SUBSTR("Full Name", ?, ?)'
    assert binds == [2, 3]


# --- validation and type checking ---------------------------------------------

def test_unknown_operator_is_rejected_by_the_discriminated_union():
    with pytest.raises(ValidationError):
        EXPR.validate_python({"op": "exec_shell", "cmd": "rm -rf /"})


def test_unknown_column_is_a_400_listing_what_is_available():
    with pytest.raises(ProblemException) as e:
        compile_it(ColExpr(name="nope"))
    assert e.value.status_code == 400
    assert e.value.code == "unknown-column"
    assert "amount" in e.value.extra["available"]


def test_date_extract_on_a_varchar_is_a_type_mismatch():
    with pytest.raises(ProblemException) as e:
        compile_it(DateExtractExpr(part="year", value=ColExpr(name="full_name")))
    assert e.value.code == "operator-type-mismatch"


def test_arith_on_a_varchar_is_a_type_mismatch():
    with pytest.raises(ProblemException) as e:
        compile_it(ArithExpr(fn="mul", left=ColExpr(name="full_name"),
                             right=LitExpr(value=2)))
    assert e.value.code == "operator-type-mismatch"


def test_string_functions_on_a_numeric_column_are_a_type_mismatch():
    with pytest.raises(ProblemException) as e:
        compile_it(StrExpr(fn="upper", value=ColExpr(name="amount")))
    assert e.value.code == "operator-type-mismatch"


def test_casting_first_satisfies_the_type_check():
    sql, _, _ = compile_it(StrExpr(
        fn="upper", value=CastExpr(value=ColExpr(name="amount"), to="varchar")))
    assert sql == 'UPPER(CAST("amount" AS VARCHAR))'


def test_a_null_literal_is_compatible_with_any_operator():
    # An untyped NULL should not trip the numeric check.
    compile_it(ArithExpr(fn="add", left=ColExpr(name="amount"), right=LitExpr(value=None)))


def test_recursion_is_capped():
    node = ColExpr(name="amount")
    for _ in range(MAX_EXPR_DEPTH + 2):
        node = ArithExpr(fn="add", left=node, right=LitExpr(value=1))
    with pytest.raises(ProblemException) as e:
        compile_it(node)
    assert e.value.code == "expression-too-deep"


def test_substr_without_a_start_is_rejected():
    with pytest.raises(ProblemException) as e:
        compile_it(StrExpr(fn="substr", value=ColExpr(name="full_name")))
    assert e.value.code == "invalid-expression"


# --- executed against DuckDB --------------------------------------------------

def test_arithmetic_executes(conn):
    assert evaluate(conn, ArithExpr(fn="mul", left=ColExpr(name="amount"),
                                    right=LitExpr(value=2))) == [20.0, 50.0, None]


def test_nested_if_over_arith_executes(conn):
    node = IfExpr(
        cases=[
            WhenClause(when=FilterGroup(conditions=[
                Filter(column="amount", op="gte", value=20)]),
                then=LitExpr(value="high")),
            WhenClause(when=FilterGroup(conditions=[
                Filter(column="amount", op="gt", value=0)]),
                then=LitExpr(value="low")),
        ],
        **{"else": LitExpr(value="none")})
    assert evaluate(conn, node) == ["low", "high", "none"]


def test_date_extract_executes(conn):
    assert evaluate(conn, DateExtractExpr(part="year", value=ColExpr(name="created"))) \
        == [2024, 2024, 2023]


def test_coalesce_and_concat_execute(conn):
    node = ConcatExpr(separator=" ", parts=[
        CoalesceExpr(args=[ColExpr(name="full_name"), LitExpr(value="anon")]),
        CastExpr(value=ColExpr(name="id"), to="varchar"),
    ])
    assert evaluate(conn, node) == ["Alice 1", "bob 2", "anon 3"]


def test_round_and_division_execute(conn):
    node = RoundExpr(digits=1, value=ArithExpr(
        fn="div", left=ColExpr(name="amount"), right=LitExpr(value=3)))
    assert evaluate(conn, node) == [3.3, 8.3, None]


def test_a_literal_that_looks_like_sql_stays_data(conn):
    # If the value were interpolated this would change the query's meaning.
    node = ConcatExpr(separator="", parts=[
        LitExpr(value="' OR 1=1 --"), ColExpr(name="full_name")])
    assert evaluate(conn, node) == ["' OR 1=1 --Alice", "' OR 1=1 --bob", "' OR 1=1 --"]
