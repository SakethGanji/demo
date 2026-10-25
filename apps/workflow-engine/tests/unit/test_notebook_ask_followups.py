"""Notebook Ask follow-ups, the pure parts: `$cred` expansion outside fill steps (never the password), the plan's
`remove` list, and the prompt lines for row targets, failure evidence, site details and numeric casts."""

from __future__ import annotations

import json

from src.services.notebook.executors import ask as A
from src.services.notebook.executors import browser as B

CREDS = {"username": "ana", "password": "pw-secret-1"}


def _steps(*extra):
    return [{"action": "fill", "target": {"label": "Username"}, "value": "{{$cred.username}}"},
            {"action": "fill", "target": {"label": "Password"}, "value": "{{$cred.password}}"}, *extra]


def test_username_expands_outside_fill_and_password_never_does():
    spec = {"base_url": "http://x", "steps": _steps(
        {"action": "expectText", "target": {"role": "region", "name": "Profile"}, "text": "Signed in as {{$cred.username}}"},
        {"action": "expectVisible", "target": {"text": "Hi {{ $cred.username }}"}})}
    body = B.build_job({"name": "c2"}, spec, CREDS)
    steps = body["cases"][0]["steps"]
    # typed values stay jobs-service placeholders (it fills them and masks them)
    assert steps[0]["value"] == "{{username}}" and steps[1]["value"] == "{{password}}"
    assert steps[2]["text"] == "Signed in as ana" and steps[3]["target"]["text"] == "Hi ana"
    assert "pw-secret-1" not in json.dumps(body["cases"])
    assert B.cred_misuse(spec["steps"]) == []
    # without credentials (no slot), the placeholder is left as before
    assert B.build_job({"name": "c2"}, spec)["cases"][0]["steps"][2]["text"] == "Signed in as {{username}}"


def test_secret_field_outside_fill_is_refused_without_the_value():
    steps = _steps({"action": "expectText", "target": {"text": "x"}, "text": "{{$cred.password}}"},
                   {"action": "goto", "url": "/?t={{$cred.token}}"})
    bad = B.cred_misuse(steps)
    assert len(bad) == 2 and "step 3 (expectText)" in bad[0] and "{{$cred.password}} is secret" in bad[0]
    assert "step 4 (goto)" in bad[1] and all("pw-secret-1" not in m for m in bad)
    # the job never carries the value either way
    body = B.build_job({"name": "c2"}, {"base_url": "http://x", "steps": steps}, CREDS)
    assert "pw-secret-1" not in json.dumps(body["cases"]) and body["cases"][0]["steps"][2]["text"] == "{{password}}"
    # an Ask plan with it is sent back for repair
    cells, errors = A.validate_plan([{"kind": "browser", "title": "t", "spec": {"base_url": "http://x", "steps": steps}}],
                                    ["c1"], 2, set(), set(), set())
    assert cells == [] and "{{$cred.password}} is secret" in errors[0]


def test_remove_names_and_validation():
    assert A.remove_names({"remove": ["c3", "c3", " c4 ", "x", 5, None, "c5; drop"]}) == ["c3", "c4"]
    assert A.remove_names({}) == [] and A.remove_names({"remove": "c3"}) == []
    props = A.TOOL["parameters"]["properties"]
    assert props["remove"]["type"] == "array" and "remove" not in A.TOOL["parameters"]["required"]
    # a planned cell may not refer to a cell this plan removes
    q = {"kind": "rule", "title": "t", "spec": {"op": "count", "left": "{{cells.c2.rows}}"}}
    cells, errors = A.validate_plan([q], ["c1", "c2"], 3, set(), set(), set(), removed={"c2"})
    assert cells == [] and "c2 is removed by this plan" in errors[0]
    assert A.validate_plan([q], ["c1", "c2"], 3, set(), set(), set())[1] == []
    # remove alone is a valid reply (no cells, no notes)
    assert A.validate_plan([], ["c1", "c2"], 3, set(), set(), set(), allow_empty=True) == ([], [])


def test_prompt_states_the_new_rules():
    s = A.SYSTEM
    for phrase in ('A `{role:"row", name}` must equal the row\'s entire text', "`exact:false` (contains, ignoring case)",
                   "`{role:\"cell\", name:\"Cy\"}`", "found 2 (expected 3)", "`remove` (`remove:[\"c3\"]`)",
                   "Site details (base URL, page paths, sign-in field labels", "reuse them from there",
                   "BASE_URL", "cast it in SQL", "`{{$cred.password}}` (any secret field) only works as a fill `value`",
                   "`{{$cred.username}}` (also email, login) works in any step"):
        assert phrase in s, phrase
