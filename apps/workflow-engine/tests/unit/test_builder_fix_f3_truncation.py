"""Tool results are never cut silently: they end with the offset that continues them, and that offset works."""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.app_builder_ai_service import AppBuilderAIService, format_lookup, handler_source, paginate


def test_paginate_short_text_is_untouched():
    assert paginate("abc", 0, 10) == "abc"


def test_paginate_marks_the_cut_and_offset_continues_without_loss():
    text = "".join(chr(65 + i % 26) for i in range(250))
    got, offset, guard = "", 0, 0
    while True:
        page = paginate(text, offset, 100)
        m = re.search(r"\[truncated at char (\d+) of 250 — call again with offset=(\d+) for the rest\]$", page)
        body = page.split("[continuing")[-1].split("\n", 1)[-1] if offset else page
        if m:
            got += body[: m.start() - (len(page) - len(body))].rstrip("\n")
            offset = int(m.group(2))
        else:
            got += body
            break
        guard += 1
        assert guard < 10
    assert got == text


def test_paginate_offset_past_end_says_so():
    assert "past the end" in paginate("abc", 99, 10)


def test_format_lookup_default_cap_paginates_with_marker():
    out = format_lookup("get_table_schema", {"name": "t", "columns": [{"name": f"c{i}", "type": "text"} for i in range(2000)]}, cap=500)
    assert "offset=" in out and "[truncated" in out
    nxt = int(re.search(r"offset=(\d+)", out).group(1))
    again = format_lookup("get_table_schema", {"name": "t", "columns": [{"name": f"c{i}", "type": "text"} for i in range(2000)]}, cap=500, offset=nxt)
    assert again.startswith(f"[continuing at offset {nxt}")


def test_handler_source_cut_is_explicit():
    src = "def h(req):\n" + "".join(f"    x{i} = {i}\n" for i in range(500)) + "    return {}\n"
    out = handler_source(src, "h", max_chars=300)
    assert "cut at 300 of" in out


def test_lookup_now_applies_offset():
    svc = AppBuilderAIService.__new__(AppBuilderAIService)
    big = "x" * 20000
    with patch.object(AppBuilderAIService, "_lookup_text", AsyncMock(return_value=big)):
        first = asyncio.run(svc._lookup_now("get_component", {"name": "Foo"}))
        assert "offset=9000" in first
        second = asyncio.run(svc._lookup_now("get_component", {"name": "Foo", "offset": 9000}))
        assert second.startswith("[continuing at offset 9000") and "offset=18000" in second


def test_read_backend_offset_continues_long_output():
    svc = AppBuilderAIService.__new__(AppBuilderAIService)
    svc._project = SimpleNamespace(id="p1")
    svc._read_ops = set()
    rows = [SimpleNamespace(name=f"op{i}", method="GET", path=f"/p{i}", response_schema={"type": "object", "properties": {f"f{j}": {"type": "string"} for j in range(120)}},
                            request_schema=None, handler_type="workflow", handler_module_id=None, handler_function=None) for i in range(12)]
    svc._app_service = SimpleNamespace(_session=MagicMock())
    svc._table_samples = AsyncMock(return_value="")
    with patch("src.services.api_route_service.ApiRouteService") as R:
        R.return_value.rows = AsyncMock(return_value=rows)
        first = asyncio.run(svc._read_backend_now({"operations": [r.name for r in rows]}))
        assert "call again with offset=" in first
        off = int(re.search(r"offset=(\d+)", first).group(1))
        second = asyncio.run(svc._read_backend_now({"operations": [r.name for r in rows], "offset": off}))
        assert second.startswith("[continuing at offset") and "api.op11" in (first + second)


def test_batched_route_and_table_lookups_do_not_raise():
    """Regression: `join(... await ...)` built an async generator and raised "can only join an iterable"."""
    svc = AppBuilderAIService.__new__(AppBuilderAIService)
    svc._project_id = lambda: "proj_x"
    svc._app_service = SimpleNamespace(_session=MagicMock())
    sig = {"method": "GET", "path": "/a", "name": "getA", "response": ["id", "title"]}
    with patch("src.services.platform_index.dispatch_tool", AsyncMock(side_effect=[sig, {**sig, "path": "/b", "name": "getB"}])):
        text = asyncio.run(svc._lookup_text("get_route_signature", {"routes": [{"method": "GET", "path": "/a"}, {"method": "GET", "path": "/b"}]}))
    assert "api.getA" in text and "api.getB" in text
    with patch("src.services.platform_index.dispatch_tool", AsyncMock(return_value={"name": "t", "columns": [{"name": "c", "type": "text"}]})):
        text = asyncio.run(svc._lookup_text("get_table_schema", {"names": ["t", "u"]}))
    assert text.count("Table t") == 2
