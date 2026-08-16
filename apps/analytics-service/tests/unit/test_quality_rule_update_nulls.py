"""Unit tests — ``RuleUpdate`` distinguishes "omitted" from an explicit ``null``.

Every field on a PATCH model is typed ``| None`` so that omission has a value
to be. That is not a claim that every column is nullable: ``name``, ``severity``
and ``enabled`` are ``NOT NULL`` in the schema, so an explicit ``null`` must
fail validation instead of travelling to the SET clause as ``name = NULL``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.quality.schemas import RuleUpdate


@pytest.mark.parametrize("field", ["name", "severity", "enabled"])
def test_rule_update_rejects_an_explicit_null_for_a_non_nullable_field(field):
    """The null must be refused here, where the field can still be named.

    Past this point ``exclude_unset`` keeps the key, the repo builds its SET
    list from key presence, and the NOT NULL violation reaches the route as the
    same ``IntegrityError`` class the unique index raises — where it is
    reported as a name collision on a rule that does not exist.
    """
    with pytest.raises(ValidationError) as exc:
        RuleUpdate(**{field: None})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["description", "sheet_selector", "column_selector"])
def test_rule_update_still_accepts_an_explicit_null_for_a_nullable_field(field):
    """Clearing an optional field is a real edit and must survive the guard.

    A blanket "no nulls" rule would leave a UI with no way to un-set a
    description or a column selector at all.
    """
    patch = RuleUpdate(**{field: None})
    assert field in patch.model_dump(exclude_unset=True)
    assert patch.model_dump(exclude_unset=True)[field] is None


def test_rule_update_leaves_omitted_non_nullable_fields_out_of_the_patch():
    """Omission must stay legal — the guard fires on presence, not on the default.

    Field validators skip defaults, so a patch that touches only ``description``
    must not trip the ``name``/``severity``/``enabled`` null check.
    """
    patch = RuleUpdate(description="just the description")
    assert patch.model_dump(exclude_unset=True) == {"description": "just the description"}
