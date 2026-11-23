"""Poll's condition can arrive already resolved by parameter resolution (`{{ true }}` → True); it must not crash."""

from src.nodes.flow.poll import PollNode


def test_resolved_bool_condition_is_used_as_is():
    node = PollNode()
    assert node._evaluate_condition(True, [], None, None) is True
    assert node._evaluate_condition(False, [], None, None) is False
    assert node._evaluate_condition(0, [], None, None) is False
