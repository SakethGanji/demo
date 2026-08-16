"""``like_contains`` — user text becomes a LIKE pattern that matches literally.

The integration companion (tests/test_discovery_contracts.py) proves the route
behaves; this pins the escaping itself, character by character, because the
failure mode is silent: an unescaped ``_`` widens a search by one character and
an unescaped ``%`` widens it to everything, and both still return plausible
rows. Binding the value as a parameter does not help — it stops SQL injection,
not pattern injection.
"""

from __future__ import annotations

from app.features.discovery.repo import like_contains


def test_ordinary_text_is_wrapped_in_wildcards_and_otherwise_untouched():
    assert like_contains("cusip") == "%cusip%"
    assert like_contains("Business Name") == "%Business Name%"
    assert like_contains("") == "%%"


def test_a_user_typed_underscore_is_escaped_so_it_cannot_match_any_character():
    assert like_contains("qty_eur") == r"%qty\_eur%"


def test_a_user_typed_percent_is_escaped_so_it_cannot_match_the_whole_catalog():
    assert like_contains("%") == r"%\%%"
    assert like_contains("50%_off") == r"%50\%\_off%"


def test_backslashes_are_escaped_first_so_they_cannot_re_arm_a_wildcard():
    """``\\%`` typed by a user is a backslash and a percent, two literals.

    If the backslash were escaped after the metacharacters — or not at all —
    the escape we added to the ``%`` would itself be escaped away and the
    wildcard would come back to life.
    """
    assert like_contains("\\") == r"%\\%"
    assert like_contains("a\\%b") == r"%a\\\%b%"
    assert like_contains("a\\_b") == r"%a\\\_b%"
