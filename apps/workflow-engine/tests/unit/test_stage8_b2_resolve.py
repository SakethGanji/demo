"""Stage 8 B2: ``resolve_child`` (DESIGN-STAGE8-REUSE 4.1-4.2) on the throwaway Postgres."""

from __future__ import annotations

import pytest

from src.engine.subworkflow import SUBWORKFLOW_NOT_FOUND, SUBWORKFLOW_VERSION_MISSING, SubworkflowError
from src.services.subworkflow_service import pin_key, resolve_child, version_number_of
from tests.unit._stage8_b2_world import Env, child_wf, install_nodes, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


def tags(stored) -> str:
    return next(n.parameters["tag"] for n in stored.workflow.nodes if n.type == "TestMark")


async def world(pg):
    """child with v1 (tag v1) and v2 (tag v2) published, draft tag 'draft'; plus a parent."""
    w = Env(pg)
    child = await w.create(child_wf(f"child-{w.prefix}", "v1"))
    v1 = await w.publish_ptr(child)
    await w.edit_draft(child, child_wf(child.name, "v2"))
    v2 = await w.publish_ptr(child)
    await w.edit_draft(child, child_wf(child.name, "draft"))
    parent = await w.create(parent_wf(f"parent-{w.prefix}", child.id))
    return w, child, parent, v1, v2


async def resolve(w, ctx, child, version, node="call"):
    async with w.sf() as s:
        return await resolve_child(s, ctx, node, child.id, version)


async def test_draft_latest_and_number_resolve_to_the_right_definitions(pg):
    w, child, parent, v1, v2 = await world(pg)
    ctx = await w.parent_ctx(parent)
    stored, vid = await resolve(w, ctx, child, "draft")
    assert (tags(stored), vid) == ("draft", None)
    stored, vid = await resolve(w, ctx, child, "latest")
    assert (tags(stored), vid) == ("v2", v2)              # the live publish pointer, at call time
    stored, vid = await resolve(w, ctx, child, 1)
    assert (tags(stored), vid) == ("v1", v1)              # a number is the version_number, not the row id
    stored, vid = await resolve(w, ctx, child, "2")
    assert vid == v2
    async with w.sf() as s:
        assert await version_number_of(s, v1) == 1 and await version_number_of(s, None) is None


async def test_latest_follows_a_republish_and_a_number_does_not(pg):
    w, child, parent, v1, v2 = await world(pg)
    ctx = await w.parent_ctx(parent)
    await w.edit_draft(child, child_wf(child.name, "v3"))
    v3 = await w.publish_ptr(child)
    assert (await resolve(w, ctx, child, "latest"))[1] == v3
    assert (await resolve(w, ctx, child, 2))[1] == v2


async def test_missing_versions_have_the_documented_messages(pg):
    w, child, parent, *_ = await world(pg)
    ctx = await w.parent_ctx(parent)
    with pytest.raises(SubworkflowError) as e:
        await resolve(w, ctx, child, 9)
    assert e.value.code == SUBWORKFLOW_VERSION_MISSING
    assert str(e.value) == f"Sub-workflow '{child.name}' has no version 9."
    fresh = await w.create(child_wf(f"fresh-{w.prefix}"))
    with pytest.raises(SubworkflowError) as e:
        await resolve(w, ctx, fresh, "latest")
    assert e.value.code == SUBWORKFLOW_VERSION_MISSING
    assert str(e.value) == f"Sub-workflow '{fresh.name}' has never been published."


async def test_another_project_and_a_missing_workflow_both_look_absent(pg):
    w, child, parent, *_ = await world(pg)
    ctx = await w.parent_ctx(parent, project_id="proj_somewhere_else")
    for target in (child.id, "wf_does_not_exist"):
        with pytest.raises(SubworkflowError) as e:
            async with w.sf() as s:
                await resolve_child(s, ctx, "call", target, "latest")
        assert e.value.code == SUBWORKFLOW_NOT_FOUND
        assert str(e.value) == f"Sub-workflow '{target}' isn't in this project."


async def test_release_pins_win_over_the_live_pointer(pg):
    w, child, parent, v1, v2 = await world(pg)
    ctx = await w.parent_ctx(
        parent, version_id=77, extras={"release_id": "rel_1", "release_pins": {pin_key(77, "call"): v1}})
    for spec in ("latest", "draft", 2):                   # whatever the node says, the snapshot decides
        stored, vid = await resolve(w, ctx, child, spec)
        assert (tags(stored), vid) == ("v1", v1)
    with pytest.raises(SubworkflowError) as e:            # a node the snapshot does not know
        await resolve(w, ctx, child, "latest", node="other")
    assert e.value.code == SUBWORKFLOW_VERSION_MISSING and "isn't pinned in this release" in str(e.value)


async def test_a_pin_to_a_version_of_another_workflow_is_refused(pg):
    w, child, parent, v1, v2 = await world(pg)
    other = await w.create(child_wf(f"other-{w.prefix}", "x"))
    other_v = await w.publish_ptr(other)
    ctx = await w.parent_ctx(parent, version_id=5, extras={"release_pins": {pin_key(5, "call"): other_v}})
    with pytest.raises(SubworkflowError) as e:
        await resolve(w, ctx, child, "latest")
    assert e.value.code == SUBWORKFLOW_VERSION_MISSING


async def test_a_release_without_pins_falls_back_with_a_warning(pg):
    w, child, parent, v1, v2 = await world(pg)
    ctx = await w.parent_ctx(parent, extras={"release_id": "rel_old"})
    stored, vid = await resolve(w, ctx, child, "latest")
    assert vid == v2
    await resolve(w, ctx, child, "latest")                # the warning is not repeated
    assert ctx.extras["run_warnings"] == ["release predates sub-workflow pinning; re-cut the release"]
    plain = await w.parent_ctx(parent)
    await resolve(w, plain, child, "latest")
    assert "run_warnings" not in plain.extras
