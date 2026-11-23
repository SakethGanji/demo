"""Publish owns the slug: validation, 409 INTERFACE_SLUG_TAKEN, the slug index follows the published version; promote output; rate-limit default."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from src.core import rate_limit
from src.core.exceptions import ConflictError, ValidationError
from src.engine.types import Workflow
from src.repositories.interface_repository import InterfaceRepository
from src.services import interface_promote
from src.services.workflow_service import WorkflowService

from ._interface_fakes import fixture

pytestmark = pytest.mark.anyio
INTERFACE = fixture()["interface"]


class Slugs:
    """Stands in for the three InterfaceRepository calls publish makes."""

    def __init__(self, owner=None, claim=True):
        self.owner, self.claim, self.calls = owner, claim, []

    def install(self, monkeypatch):
        async def get_slug(repo, project_id, slug):
            self.calls.append(("get_slug", project_id, slug))
            return self.owner

        async def claim_slug(repo, project_id, slug, workflow_id, kind, version_id):
            self.calls.append(("claim", project_id, slug, workflow_id, kind, version_id))
            return self.claim

        async def release_slug(repo, workflow_id):
            self.calls.append(("release", workflow_id))

        async def commit(repo):
            self.calls.append(("commit",))

        async def rollback(repo):
            self.calls.append(("rollback",))

        for name, fn in (("get_slug", get_slug), ("claim_slug", claim_slug), ("release_slug", release_slug), ("commit", commit), ("rollback", rollback)):
            monkeypatch.setattr(InterfaceRepository, name, fn)


def service():
    s = WorkflowService.__new__(WorkflowService)
    s._workflow_repo = SimpleNamespace(_session=object())
    return s


def stored(interface):
    return SimpleNamespace(id="wf_1", project_id="proj_1", workflow=Workflow(name="n", nodes=[], connections=[], interface=interface))


async def test_no_interface_means_nothing_to_check():
    assert await service()._check_interface_for_publish(stored(None)) is None


async def test_a_free_slug_passes_and_is_cleaned(monkeypatch):
    s = Slugs()
    s.install(monkeypatch)
    clean = await service()._check_interface_for_publish(stored(INTERFACE))
    assert clean["slug"] == "refunds" and s.calls == [("get_slug", "proj_1", "refunds")]


async def test_my_own_slug_passes(monkeypatch):
    Slugs(owner={"workflow_id": "wf_1"}).install(monkeypatch)
    assert (await service()._check_interface_for_publish(stored(INTERFACE)))["slug"] == "refunds"


async def test_another_workflows_slug_is_409_interface_slug_taken(monkeypatch):
    Slugs(owner={"workflow_id": "wf_other"}).install(monkeypatch)
    with pytest.raises(ConflictError) as e:
        await service()._check_interface_for_publish(stored(INTERFACE))
    assert e.value.code == "INTERFACE_SLUG_TAKEN" and "refunds" in e.value.message


async def test_an_invalid_interface_refuses_to_publish(monkeypatch):
    Slugs().install(monkeypatch)
    with pytest.raises(ValidationError) as e:
        await service()._check_interface_for_publish(stored({**INTERFACE, "slug": "Bad Slug"}))
    assert "slug" in e.value.message


async def test_the_slug_follows_the_published_version(monkeypatch):
    s = Slugs()
    s.install(monkeypatch)
    await service()._sync_interface_slug(stored(INTERFACE), INTERFACE, 12)
    assert s.calls == [("claim", "proj_1", "refunds", "wf_1", "custom", 12), ("commit",)]


async def test_publishing_a_version_without_an_interface_frees_the_slug(monkeypatch):
    s = Slugs()
    s.install(monkeypatch)
    await service()._sync_interface_slug(stored(None), None, 12)
    assert s.calls == [("release", "wf_1"), ("commit",)]


async def test_losing_the_claim_race_is_a_conflict(monkeypatch):
    s = Slugs(claim=False)
    s.install(monkeypatch)
    with pytest.raises(ConflictError) as e:
        await service()._sync_interface_slug(stored(INTERFACE), INTERFACE, 12)
    assert e.value.code == "INTERFACE_SLUG_TAKEN" and ("rollback",) in s.calls


# ── promote ──────────────────────────────────────────────────────────────────────────────────


def test_generated_app_source_carries_the_form_and_the_contract():
    definition = fixture()
    tsx = interface_promote.generate_app_tsx(definition, INTERFACE)
    assert "import { createClient } from './api/client'" in tsx and "client.startRun(" in tsx and "client.watchRun(" in tsx
    assert '"name": "order_id"' in tsx and '"label": "Order number"' in tsx and 'const TITLE = "Refund request"' in tsx
    assert "__P__" not in tsx and "RefundsFields" in tsx
    for secret in ("REFUND_CLASSIFY", "Classify", "__REFUND_API_URL__"):
        assert secret not in tsx


def test_generated_chat_app_source_has_no_form():
    d = fixture("chat_support_ui.json")
    tsx = interface_promote.generate_app_tsx(d, d["interface"])
    assert "client.startRun" not in tsx and "Send" in tsx


# ── rate-limit defaults ──────────────────────────────────────────────────────────────────────


def test_the_default_limiter_never_limits_and_can_be_swapped_and_restored():
    assert isinstance(rate_limit.get_rate_limiter(), rate_limit.NoopRateLimiter)
    for _ in range(1000):
        rate_limit.get_rate_limiter().check("iface.submit:a:b", "1.2.3.4")
    seen = []
    previous = rate_limit.set_rate_limiter(SimpleNamespace(check=lambda b, k, c=1: seen.append((b, k, c))))
    try:
        rate_limit.check("task.decide", None, 2)
        assert seen == [("task.decide", "internal", 2)]
    finally:
        rate_limit.set_rate_limiter(previous)
    assert isinstance(rate_limit.get_rate_limiter(), rate_limit.NoopRateLimiter)
    err = rate_limit.rate_limited(3)
    assert err.status_code == 429 and err.headers["Retry-After"] == "3" and err.detail["code"] == "RATE_LIMITED"
