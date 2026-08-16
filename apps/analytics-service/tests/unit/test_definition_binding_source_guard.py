"""What a saved definition's stored params are allowed to say about its SOURCE.

`analytics_definitions.params` is free-form JSON — nothing validates its keys on
the way in — and `build_definition_request` merges it straight into the same
request model the public /sample, /aggregate, /profile and /pivot endpoints use.
Those models accept `file_path`, inline `data` and the three version-targeting
keys, because the direct endpoints support them. A definition must not.

Two production failures live here:

* `file_path` wins over `dataset_id` in every executor and `load_data` opens it
  straight off the server filesystem with no team scoping — which is why the
  direct endpoint gates it behind a superuser check. A definition has no such
  gate; it is authorized purely by the dataset it hangs off. A stored
  `params["file_path"]` therefore read an arbitrary server file and registered
  the result as an artifact in the caller's own team.
* `base` only overrides the keys it contains, and a `mode=current` selector
  contributes no version keys at all. A stored `version_number` therefore ran
  against that version while the analytics_runs row, the publish parent and the
  whole lineage chain recorded the *current* one — a wrong answer with a
  confident provenance trail attached.

These are pure binding rules, so they belong in the unit layer.
"""

from __future__ import annotations

import pytest

from app.api.errors import ProblemException
from app.features.library.service import build_definition_request

BASE = {"dataset_id": "ds-1", "sheet": None}
AGGREGATE = {"group_by": ["region"],
             "aggregations": [{"column": "amount", "function": "sum"}]}


def test_a_stored_file_path_is_rejected_rather_than_read():
    with pytest.raises(ProblemException) as exc:
        build_definition_request("aggregate", {**AGGREGATE, "file_path": "/etc/hosts"},
                                 BASE)
    assert exc.value.status_code == 400
    assert exc.value.code == "definition-source-not-allowed"
    assert exc.value.extra["params"] == ["file_path"]


def test_stored_inline_data_is_rejected_too():
    """`data` beats `dataset_id` the same way `file_path` does, so a definition
    holding it charts rows that are not in the dataset it claims to describe."""
    with pytest.raises(ProblemException) as exc:
        build_definition_request("sample", {"data": [{"a": 1}]}, BASE)
    assert exc.value.status_code == 400
    assert exc.value.code == "definition-source-not-allowed"


def test_a_null_file_path_in_params_is_not_treated_as_smuggling():
    """Round-tripping a definition through a client that serializes every field
    leaves explicit nulls behind; those select nothing and must not 400."""
    request = build_definition_request(
        "aggregate", {**AGGREGATE, "file_path": None, "data": None}, BASE)
    assert request.file_path is None


def test_stored_version_keys_cannot_defeat_the_version_selector():
    request = build_definition_request(
        "aggregate",
        {**AGGREGATE, "version_number": 1, "tag": "production",
         "version_id": "11111111-1111-1111-1111-111111111111"},
        BASE)
    assert request.version_number is None
    assert request.tag is None
    assert request.version_id is None


def test_the_selector_pin_still_reaches_the_request():
    """Stripping the params must not strip the definition's own selector, which
    arrives through `base`."""
    request = build_definition_request(
        "aggregate", {**AGGREGATE, "version_number": 1},
        {**BASE, "version_number": 7})
    assert request.version_number == 7


def test_a_stored_dataset_id_cannot_retarget_the_definition():
    request = build_definition_request(
        "aggregate", {**AGGREGATE, "dataset_id": "someone-elses-dataset"}, BASE)
    assert request.dataset_id == "ds-1"


def test_binding_an_unrunnable_kind_is_a_400_not_a_keyerror():
    """`join` definitions are real rows in analytics_definitions (the guided join
    builder writes them) and they list alongside the rest, so a UI will offer a
    Run button for one. The bare dict lookup raised KeyError outside every
    handler, so the caller got an opaque 500 with no machine-readable code."""
    with pytest.raises(ProblemException) as exc:
        build_definition_request("join", {"relationship_id": "r-1"}, BASE)
    assert exc.value.status_code == 400
    assert exc.value.code == "kind-not-runnable"
    assert exc.value.extra["kind"] == "join"


def test_a_genuinely_invalid_param_still_reports_which_one():
    """The pre-existing invalid-definition contract must survive the new guards."""
    with pytest.raises(ProblemException) as exc:
        build_definition_request("aggregate", {**AGGREGATE, "sort_order": "ASC"}, BASE)
    assert exc.value.code == "invalid-definition"
    assert any(e["param"] == "sort_order" for e in exc.value.extra["errors"])
