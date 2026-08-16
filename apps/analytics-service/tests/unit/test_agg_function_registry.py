"""The aggregation vocabulary is declared once and everything else derives.

It used to be written out three times — ``ALLOWED_AGG_FUNCTIONS``, the
``AggregationSpec.function`` field description, and a hand-typed string in the
MCP compute tools. Nothing tied them together, so adding a function meant
remembering all three, and the two prose copies could advertise a vocabulary
the validator did not accept. A model reading a tool description that lists a
function the service rejects spends a whole round trip finding that out.

These tests pin the derivation, and the two contracts that constrain how far it
may go: the runtime 400 message, and ``function`` staying a bare ``str``.
"""

from __future__ import annotations

from app.features.data_accelerator.schemas import AggregationSpec, PivotValue
from app.features.mcp.tools.compute import AGGREGATIONS_HELP
from app.shared.constants import (
    AGG_FUNCTIONS,
    AGG_FUNCTIONS_TEXT,
    AGG_SQL_MAP,
    ALLOWED_AGG_FUNCTIONS,
)

EXPECTED = ("sum", "mean", "median", "count", "min", "max", "std",
            "nunique", "first", "last")


def test_the_vocabulary_is_what_it_has_always_been():
    """Consolidating three copies must not have quietly changed the contents."""
    assert AGG_FUNCTIONS == EXPECTED
    assert ALLOWED_AGG_FUNCTIONS == set(EXPECTED)


def test_the_field_description_is_derived_not_restated():
    description = AggregationSpec.model_fields["function"].description
    assert description == f"Aggregation function: {AGG_FUNCTIONS_TEXT}"
    # The property that matters: every function the validator accepts is named
    # in the prose, and the prose names nothing the validator would reject.
    assert {w.strip() for w in description.split(":")[1].split(",")} == ALLOWED_AGG_FUNCTIONS


def test_the_mcp_tool_help_is_derived_not_restated():
    assert f"Allowed functions: {AGG_FUNCTIONS_TEXT}." in AGGREGATIONS_HELP
    for function in ALLOWED_AGG_FUNCTIONS:
        assert function in AGGREGATIONS_HELP


def test_the_runtime_error_message_did_not_degrade():
    """``tests/test_analytics_errors.py`` asserts this exact rendering.

    Both validators format the vocabulary with ``sorted()``, so the tuple's
    presentation order cannot leak into the error a caller sees. The list has
    to stay a list literal — not a set repr, not a comma string.
    """
    assert (f"Unknown aggregation function: median_abs. Allowed: {sorted(ALLOWED_AGG_FUNCTIONS)}"
            == "Unknown aggregation function: median_abs. Allowed: "
               "['count', 'first', 'last', 'max', 'mean', 'median', 'min', "
               "'nunique', 'std', 'sum']")


def test_function_is_still_a_bare_str_not_a_literal():
    """Deliberate, and the reason the error message above still exists.

    A ``Literal`` would move rejection into pydantic, which answers with a
    generic 422 that names neither the offending value nor the alternatives.
    Deriving the *description* from the constant gets the drift fix without
    paying that price.
    """
    assert AggregationSpec.model_fields["function"].annotation is str


# ---------------------------------------------------------------------------
# The vocabulary is *published*, without moving validation into pydantic
# ---------------------------------------------------------------------------
#
# Prose in a field description is unreadable to a machine. An OpenAPI consumer
# generating a client, or a model handed the generated schema, had no way to
# learn the valid values short of parsing English. The enum below fixes that as
# schema metadata only — pydantic does not enforce ``json_schema_extra``, so
# the annotation stays ``str``, parsing stays permissive, and the runtime 400
# above is still the only thing that rejects a bad function.


def test_the_published_schema_advertises_the_vocabulary():
    schema = AggregationSpec.model_json_schema()["properties"]["function"]
    assert schema["enum"] == list(AGG_FUNCTIONS)
    assert schema["type"] == "string"          # not narrowed to a const union
    assert schema["description"] == f"Aggregation function: {AGG_FUNCTIONS_TEXT}"


def test_the_published_enum_is_derived_not_a_fourth_copy():
    """``AGG_FUNCTIONS`` is the single source; publishing must not fork it."""
    published = AggregationSpec.model_json_schema()["properties"]["function"]["enum"]
    assert set(published) == ALLOWED_AGG_FUNCTIONS
    assert tuple(published) == AGG_FUNCTIONS   # same order, same list, one source


def test_pivot_values_inherit_the_published_enum():
    """``PivotValue`` subclasses ``AggregationSpec`` and its function is
    validated against the same constant by ``run_pivot``, so its published
    schema has to agree — it does, for free, because it is the same field."""
    schema = PivotValue.model_json_schema()["properties"]["function"]
    assert schema["enum"] == list(AGG_FUNCTIONS)


def test_publishing_the_enum_did_not_start_validating_it():
    """The whole reason ``function`` is not a ``Literal``. If pydantic ever
    enforced this, an unknown function would become a 422 whose top-level
    ``detail`` is the generic "Request validation failed" and the useful text
    would be buried in ``errors[0].msg``."""
    spec = AggregationSpec(column="amount", function="median_abs")
    assert spec.function == "median_abs"       # accepted at parse time...
    # ...and rejected later, by the service, with a message that names it.


def test_the_sql_map_cannot_drift_from_the_vocabulary():
    """The fourth copy, and the one that fails loudest: a function in
    ``AGG_FUNCTIONS`` with no SQL mapping passes validation and then raises
    ``KeyError`` inside the compiler. ``nunique`` is the sole exemption — it
    compiles to ``COUNT(DISTINCT …)``, which is not a bare ``f(col)`` call.
    """
    special_cased = {"nunique"}
    assert set(AGG_SQL_MAP) | special_cased == ALLOWED_AGG_FUNCTIONS
    assert not set(AGG_SQL_MAP) & special_cased


def test_the_vocabulary_cannot_be_mutated_by_a_consumer():
    """One shared set that any importer could ``.add()`` to is a vocabulary
    with no owner."""
    assert isinstance(ALLOWED_AGG_FUNCTIONS, frozenset)
    assert isinstance(AGG_FUNCTIONS, tuple)
