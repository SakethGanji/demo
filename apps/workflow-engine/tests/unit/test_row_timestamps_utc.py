"""Row timestamps leave the engine as ISO-8601 with an explicit UTC offset (a naive string is parsed as
browser-local time, which made fresh rows read "5h ago")."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services.data_table_service import iso_utc, row_to_dict, table_to_dict


def _has_offset(s: str) -> bool:
    return datetime.fromisoformat(s).tzinfo is not None


def test_row_just_created_has_offset_and_is_now():
    now = datetime.now()  # how rows are stamped on insert
    r = SimpleNamespace(id=1, data={"a": 1}, created_at=now, updated_at=now)
    out = row_to_dict(r)
    for k in ("created_at", "updated_at"):
        assert _has_offset(out[k])
        age = datetime.now(timezone.utc) - datetime.fromisoformat(out[k])
        assert abs(age) < timedelta(seconds=5)  # i.e. timeAgo would say "just now"


def test_aware_value_converted_and_none_passthrough():
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))
    assert iso_utc(aware) == "2026-01-01T07:00:00+00:00"
    assert iso_utc(None) is None


def test_table_dict_has_offset():
    now = datetime.now()
    t = SimpleNamespace(id="t", project_id="p", name="n", description=None, columns=[], created_at=now, updated_at=now)
    out = table_to_dict(t)
    assert _has_offset(out["created_at"]) and _has_offset(out["updated_at"])
