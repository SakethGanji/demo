"""Follow-up chats on an existing app must see its files; a brand-new empty app is not an error."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.schemas.app_builder import AppBuilderChatRequest
from src.services.app_builder_ai_service import AppBuilderAIService


def make(*, row_version, version_ids, files_by_version):
    svc = AppBuilderAIService.__new__(AppBuilderAIService)
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(current_version_id=row_version))
    result = MagicMock()
    result.all.return_value = [(v,) for v in version_ids]
    session.execute = AsyncMock(return_value=result)
    svc._app_service = SimpleNamespace(
        _session=session,
        get_app=AsyncMock(return_value=SimpleNamespace(files=[], source_code=None)),
        get_version_files=AsyncMock(side_effect=lambda aid, vid: files_by_version.get(vid)),
    )
    return svc


def resolve(svc, **kw):
    return asyncio.run(svc._resolve_current_files(AppBuilderChatRequest(app_id="a1", message="change it", **kw)))


def test_new_empty_app_starts_without_a_scary_warning():
    files, warning = resolve(make(row_version=None, version_ids=[], files_by_version={}))
    assert files == [] and warning is None


def test_stale_current_version_falls_back_to_newest_version_with_files():
    f = [{"path": "App.tsx", "content": "x", "parsed_index": None}]
    svc = make(row_version=9, version_ids=[9, 8, 7], files_by_version={7: f})
    files, warning = resolve(svc, current_version_id=9)
    assert warning is None and files[0]["path"] == "App.tsx"


def test_history_but_no_files_anywhere_still_warns():
    files, warning = resolve(make(row_version=4, version_ids=[4], files_by_version={}))
    assert files == [] and "couldn't find the existing files" in warning
