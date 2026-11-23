"""Stage 3B fuzz generators: deterministic per seed, capped at count, one-field changes first, missing fields removed."""

from __future__ import annotations

import json

from src.engine import shapes as sh
from src.services.fuzz import _apply, _generate, _message_key, _walk

BASE = {"body": {"customer": {"email": "a@b.co", "name": "Ann"}, "amount": 10, "tags": ["x"]}, "mode": "manual"}
SHAPE = sh.from_sample([BASE])


def items(pool):
    return [json.dumps(c.items, sort_keys=True) for c in pool]


def test_same_seed_same_pool_and_different_seed_may_differ():
    a, b = _generate(BASE, SHAPE, 40, 3, []), _generate(BASE, SHAPE, 40, 3, [])
    assert items(a) == items(b)
    assert [c.field for c in a] == [c.field for c in b]
    assert items(_generate(BASE, SHAPE, 40, 4, [])) != items(a)


def test_count_cap_baseline_first_and_no_duplicates():
    for count in (1, 2, 7, 25, 200):
        pool = _generate(BASE, SHAPE, count, 1, [])
        assert len(pool) <= count
        assert pool[0].items == [BASE] and pool[0].changes == []
        assert len(set(items(pool))) == len(pool)


def test_every_field_is_removed_once_and_nothing_mutates_the_base():
    snapshot = json.dumps(BASE, sort_keys=True)
    pool = _generate(BASE, SHAPE, 200, 1, [])
    removed = {c.field for c in pool if c.gen == "missing"}
    assert removed == {".".join(p) for p in _walk(BASE)}
    assert json.dumps(BASE, sort_keys=True) == snapshot
    gone = next(c for c in pool if c.field == "body.customer.email" and c.gen == "missing")
    assert "email" not in gone.items[0]["body"]["customer"]


def test_categories_and_random_combos_change_two_or_three_fields():
    pool = _generate(BASE, SHAPE, 200, 1, [])
    assert {c.category for c in pool} >= {"normal", "missing", "wrong_type", "boundary", "unusual"}
    combos = [c for c in pool if c.gen == "random"]
    assert combos and all(2 <= len(c.changes) <= 3 for c in combos)


def test_ai_inputs_join_the_pool_within_the_cap():
    ai = [[{"body": {"amount": "lots"}}], [{"weird": True}]]
    pool = _generate(BASE, SHAPE, 20, 1, ai)
    assert [c.ai for c in pool].count(True) == 2 and len(pool) <= 20
    assert all(c.category == "unusual" for c in pool if c.ai)


def test_empty_base_gives_only_the_baseline_and_helpers_work():
    assert len(_generate({}, None, 50, 1, [])) == 1
    assert _message_key("row 12 failed after 345 ms") == "row  failed after  ms"
    assert _apply(BASE, [(("mode",), 5)])["mode"] == 5 and BASE["mode"] == "manual"
