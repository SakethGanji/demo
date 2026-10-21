from src.services.backend_templates.helpers_dup import duplicates, helper_fingerprints

A = '''
def audit(event, entity, caller):
    """Write an audit row."""
    row = {"event": event, "entity": entity, "by": caller}
    return row
'''
A_RENAMED = '''
def audit_it(e, ent, who):
    out = {"event": e, "entity": ent, "by": who}
    return out
'''
A_DIFFERENT = '''
def audit(event, entity, caller):
    row = {"event": event, "entity": entity, "by": caller, "extra": 1}
    return row
'''


def test_renamed_locals_and_docstring_still_match():
    assert helper_fingerprints(A)["audit"] == helper_fingerprints(A_RENAMED)["audit_it"]


def test_different_logic_does_not_match():
    assert helper_fingerprints(A)["audit"] != helper_fingerprints(A_DIFFERENT)["audit"]


def test_bad_source_is_empty():
    assert helper_fingerprints("def (") == {}


def test_duplicate_of_shared_helper_reported():
    out = duplicates({"common": A, "runs": A_RENAMED}, shared="common")
    assert out == [{"name": "audit", "modules": ["common", "runs"], "shared": True}]


def test_same_name_in_non_shared_module_is_duplicate_of_shared():
    out = duplicates({"common": A, "runs": A_DIFFERENT}, shared="common")
    assert out[0]["name"] == "audit" and out[0]["shared"] is True


def test_peer_duplicates_without_shared():
    out = duplicates({"a": A, "b": A_RENAMED, "c": A_DIFFERENT})
    assert len(out) == 1 and out[0]["modules"] == ["a", "b"] and out[0]["shared"] is False


def test_no_duplicates():
    assert duplicates({"a": A, "b": A_DIFFERENT}) == []
