"""The visual-editing trial (WORKFLOW_EDITABLE_RULES): the brief is unchanged with the flag off and teaches the editable shape with
it on; the editability lint scores a hand-rolled app low and a declarative one high; the one repair pass after a working build is
kept when it passes the gates and discarded (the working app stays) when it does not; the kit with its editor hooks type-checks.
The model is the recorded-bundle fake of test_oneshot_builder."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.services import editability_lint as el
from src.services import oneshot_builder as ob
from src.services import platform_index as pi
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.test_oneshot_builder import FakeModel, _generator_db, new_project, plan_json, typed_bundle_json  # noqa: F401

pytestmark = pytest.mark.anyio

lint_ok = el.lint_files([]).get("available") is True
needs_lint = pytest.mark.skipif(not lint_ok, reason="node or @babel/parser is not installed")


# ── the brief ────────────────────────────────────────────────────────────────


def test_the_brief_is_unchanged_with_the_flag_off(monkeypatch):
    """Flag off, the brief is exactly what the platform listed before the trial: the kit without Select and without AppShell's
    viewerRoles, and no extra section."""
    monkeypatch.delenv("WORKFLOW_EDITABLE_RULES", raising=False)
    off = ob.brief()
    assert "Editable shape" not in off and "`Select(" not in off and "viewerRoles" not in off

    before = []
    for c in pi.kit_components():
        if c["name"] in ob._EDITABLE_KIT:
            continue
        before.append({**c, "props": [p for p in c.get("props") or [] if (c["name"], p["name"]) not in ob._EDITABLE_PROPS]})
    monkeypatch.setattr(pi, "kit_components", lambda: before)
    monkeypatch.setattr(ob, "BRIEF_SECTIONS", [s for s in ob.BRIEF_SECTIONS if s is not ob._editable_section])
    assert ob.brief() == off


def test_the_brief_teaches_the_editable_shape_with_the_flag_on(monkeypatch):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    on = ob.brief()
    sec = on.split("## 7. Editable shape", 1)[1]
    assert len(sec.strip().splitlines()) < 60
    assert "`Select({" in on and "viewerRoles" in on
    for must in ("format: 'badge'", "toneMap", "as const", "roles: ['admin']", "<Tabs", "<Select", "slate-", "screens.push", "hidden"):
        assert must in sec, must
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "0")
    assert "Editable shape" not in ob.brief()


# ── the lint ─────────────────────────────────────────────────────────────────


BAD_APP = [
    {"path": "App.tsx", "content": """/** Hand-rolled app. */
import { useState } from 'react'
import { AppShell, DataTable, Tag } from './kit'

function Requests({ rows }: { rows: any[] }) {
  const [tab, setTab] = useState('open')
  const [status, setStatus] = useState('')
  const columns = [
    { key: 'title', header: 'Title', render: (r: any) => r.title },
    { key: 'status', header: 'Status', render: (r: any) => <Tag tone={r.status === 'approved' ? 'green' : 'gray'}>{r.status}</Tag> },
    { key: 'created_at', header: 'Created', render: (r: any) => new Date(r.created_at).toLocaleDateString() },
  ]
  return (
    <div className="p-4 text-slate-600">
      <div className="flex gap-2 border-b border-gray-200">
        <button className={tab === 'open' ? 'font-semibold' : ''} onClick={() => setTab('open')}>Open</button>
        <button className={tab === 'done' ? 'font-semibold' : ''} onClick={() => setTab('done')}>Done</button>
      </div>
      <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option></select>
      <DataTable rows={rows} columns={columns} empty={tab === 'open' ? 'Nothing open' : 'Nothing done'} />
    </div>
  )
}

export default function App() {
  const admin = true
  const screens: any[] = [{ id: 'req', label: 'Requests', render: () => <Requests rows={[]} /> }]
  if (admin) screens.push({ id: 'admin', label: 'Admin', render: () => <div /> })
  return <AppShell title={`Requests ${screens.length}`} screens={screens} />
}
"""}]

GOOD_APP = [
    {"path": "App.tsx", "content": """/** Declarative app. */
import { AppShell } from './kit'
import Requests from './views/Requests'

export default function App() {
  return <AppShell title="Requests" screens={[
    { id: 'req', label: 'Requests', render: () => <Requests /> },
    { id: 'admin', label: 'Admin', roles: ['admin'], render: () => <Requests /> },
  ]} />
}
"""},
    {"path": "views/Requests.tsx", "content": """/** The request list. */
import { useState } from 'react'
import { Card, DataTable, Page, Select, Tabs, type Column } from '../kit'

const STATUS_TONE = { pending: 'amber', approved: 'green' } as const
const COLUMNS: Column<any>[] = [
  { key: 'title', header: 'Title' },
  { key: 'status', header: 'Status', format: 'badge', toneMap: STATUS_TONE },
  { key: 'created_at', header: 'Created', format: 'date' },
]
const TABS = [{ id: 'open', label: 'Open' }, { id: 'done', label: 'Done' }]

/** Requests, filtered by tab and status. */
export default function Requests() {
  const [tab, setTab] = useState('open')
  const [status, setStatus] = useState('')
  const rows: any[] = []
  return (
    <Page title="Requests">
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      <Select label="Status" value={status} onChange={setStatus} options={['pending', 'approved']} />
      <Card title="All requests">
        <DataTable rows={rows} columns={COLUMNS} empty="Nothing here" />
      </Card>
    </Page>
  )
}
"""}]


@needs_lint
def test_the_lint_scores_a_hand_rolled_app_low_and_names_the_fixes():
    r = el.lint_files(BAD_APP + [{"path": "kit/ui.tsx", "content": "export function X() {}"}, {"path": "api/client.ts", "content": "x"}])
    assert r["available"] is True and r["files"] == 1
    assert r["score"] < 0.3 and r["score_strict"] <= r["score"]
    assert r["column_renders"] == 3 and r["column_renders_trivial"] == 3
    rules = set(r["violation_counts"])
    assert {"R3-column-render", "R3-columns-in-component", "R4-raw-select", "R4-raw-tabs", "R5-imperative-screens", "R2-config-expr",
            "R8-slate-gray"} <= rules, rules
    first = r["violations"][0]
    assert set(first) == {"file", "line", "rule", "message", "fix"} and first["rule"].startswith("R3") and first["line"] > 0
    assert len(r["violations"]) <= 20 and el.mechanical(r)
    fixes = {v["rule"]: v["fix"] for v in r["violations"]}
    assert "format: 'badge'" in next(v["fix"] for v in r["violations"] if "status" in v["message"] and v["rule"] == "R3-column-render")
    assert "Select" in fixes["R4-raw-select"] and "Tabs" in fixes["R4-raw-tabs"]


@needs_lint
def test_the_lint_scores_a_declarative_app_high():
    r = el.lint_files(GOOD_APP)
    assert r["score"] >= ob.EDITABLE_REPAIR_BELOW and r["score"] > el.lint_files(BAD_APP)["score"] * 3, r
    assert r["column_renders"] == 0 and not r["violations"], r["violations"]
    assert {p["file"] for p in r["per_file"]} == {"App.tsx", "views/Requests.tsx"}


def test_the_lint_never_raises(monkeypatch):
    assert el.lint_files([{"path": "App.tsx", "content": "export default function App( {"}])["available"] in (True, False)
    assert el.lint_files(None)["available"] in (True, False)        # type: ignore[arg-type]
    monkeypatch.setattr(el.shutil, "which", lambda _: None)
    assert el.lint_files(GOOD_APP) == {"available": False}


@needs_lint
def test_the_lint_cli_prints_json(tmp_path):
    for f in GOOD_APP:
        (tmp_path / f["path"]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f["path"]).write_text(f["content"])
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run([str(root / "venv" / "bin" / "python"), "-W", "ignore", "-m", "src.services.editability_lint", str(tmp_path)],
                       cwd=root, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["score"] >= ob.EDITABLE_REPAIR_BELOW


# ── the repair pass ──────────────────────────────────────────────────────────


LITERAL_SCREENS = """  return <AppShell title='Office Supplies' screens={[
    { id: 'new', label: 'New request', render: (go: (id: string) => void) => <NewRequest onDone={() => go('mine')} /> },
    { id: 'mine', label: 'My requests', render: () => <MyRequests /> },
    { id: 'inbox', label: 'Inbox', render: () => <Inbox /> },
    { id: 'review', label: 'Review', hidden: !approver, render: () => <Review /> },
    { id: 'dashboard', label: 'Dashboard', hidden: !approver, render: () => <Dashboard /> },
  ]} />;
}
"""


def _app(fn) -> dict:
    b = typed_bundle_json()
    src = next(f["content"] for f in b["app"]["files"] if f["path"] == "App.tsx")
    head = src.split("  const screens: any[] = [", 1)[0]
    return {"app": {"files": [{"path": "App.tsx", "content": fn(head, src)}]}, "message": "editable"}


async def _build(pg, monkeypatch, changes):
    from src.services import project_plan as pp

    m = FakeModel(bundles=[typed_bundle_json()], changes=changes)
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    pid = await new_project(pg)
    res = await ob.build(pid, pp.normalize(plan_json()), app_key="supply_requests")
    return m, res


@needs_lint
async def test_a_good_repair_is_kept(pg, monkeypatch):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    good = _app(lambda head, src: head + LITERAL_SCREENS)
    m, res = await _build(pg, monkeypatch, [good])
    assert m.calls == ["emit_bundle", "emit_changes"], m.calls
    assert res["ok"] is True and res["errors"] == []
    rep = res["repair"]
    assert rep["ran"] and rep["kept"], rep
    assert rep["score_before"] < ob.EDITABLE_REPAIR_BELOW and rep["score_after"] is not None and rep["seconds"] >= 0
    app = next(f["content"] for f in res["bundle"]["app"]["files"] if f["path"] == "App.tsx")
    assert "screens.push" not in app and "hidden: !approver" in app
    assert res["editability"]["violation_counts"].get("R5-imperative-screens") is None
    assert "repair_load" in res["timing"]["phases"]


@needs_lint
async def test_a_repair_that_breaks_the_type_check_is_discarded_and_the_build_stays_ok(pg, monkeypatch):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    bad = _app(lambda head, src: src.replace("screens={screens} />", "screens={screens} notAProp={1} />"))
    m, res = await _build(pg, monkeypatch, [bad])
    assert m.calls == ["emit_bundle", "emit_changes"], m.calls
    assert res["ok"] is True and res["errors"] == []
    rep = res["repair"]
    assert rep["ran"] and not rep["kept"] and rep["reason"].startswith("discarded"), rep
    app = next(f["content"] for f in res["bundle"]["app"]["files"] if f["path"] == "App.tsx")
    assert "notAProp" not in app
    assert "repair_load" not in res["timing"]["phases"], "a repair that fails the gates is never saved"
    from src.db.models import AppModel
    async with pg() as s:
        saved = (await s.get(AppModel, res["app_id"])).draft_definition
    assert "notAProp" not in json.dumps(saved)


async def test_no_repair_call_with_the_flag_off(pg, monkeypatch):
    monkeypatch.delenv("WORKFLOW_EDITABLE_RULES", raising=False)
    m, res = await _build(pg, monkeypatch, [])
    assert m.calls == ["emit_bundle"], m.calls
    assert res["ok"] is True and "repair" not in res
    if lint_ok:
        assert 0 < res["editability"]["score"] < ob.EDITABLE_REPAIR_BELOW and res["editability"]["violations"]


# ── the kit type-checks with its editor hooks ────────────────────────────────


SAMPLE = """import { AppShell, DataTable, RowActions, Select, StatTiles, Tabs, type Column } from './kit'
const TONE = { open: 'blue', done: 'green' } as const
type Row = { id: number; title: string; status: 'open' | 'done'; created_at: string; total: number }
const COLUMNS: Column<Row>[] = [
  { key: 'title', header: 'Title' }, { key: 'status', header: 'Status', format: 'badge', toneMap: TONE },
  { key: 'created_at', header: 'Created', format: 'ago' }, { key: 'total', header: 'Total', format: 'money', hidden: true },
]
export default function App() {
  return <AppShell title="T" viewerRoles={['admin']} screens={[
    { id: 'a', label: 'A', render: () => <DataTable rows={[] as Row[]} rowKey={(r) => String(r.id)} columns={COLUMNS} /> },
    { id: 'b', label: 'B', roles: ['admin'], hidden: false, render: () => <>
      <Tabs tabs={[{ id: 'x', label: 'X' }, { id: 'y', label: 'Y', hidden: true }]} value="x" onChange={() => {}} />
      <Select label="S" value="" onChange={() => {}} options={['a', { value: 'b', label: 'B' }]} placeholder="Any" />
      <RowActions actions={[{ label: 'Go', onClick: () => {}, hidden: true }]} />
      <StatTiles resource="requests" tiles={[{ label: 'n', hidden: true }]} />
    </> },
  ]} />
}
"""


def test_the_kit_and_an_app_using_its_editor_props_type_check():
    from src.services import app_builder_ai_service as ab

    chain = ab._typecheck_toolchain()
    if chain is None:
        pytest.skip("no TypeScript toolchain")
    tsc, nm = chain
    kit = Path(ab.__file__).resolve().parent / "app_kit"
    with tempfile.TemporaryDirectory(prefix="kitcheck-") as tmp:
        shutil.copytree(kit, Path(tmp) / "kit")
        (Path(tmp) / "App.tsx").write_text(SAMPLE)
        cfg = {"compilerOptions": {
            "target": "es2020", "lib": ["dom", "es2020", "dom.iterable"], "jsx": "react-jsx", "strict": True, "noEmit": True,
            "moduleResolution": "bundler", "module": "esnext", "esModuleInterop": True, "skipLibCheck": True, "isolatedModules": True,
            "allowSyntheticDefaultImports": True, "types": [], "typeRoots": [f"{nm}/@types"], "baseUrl": ".",
            "paths": {"react": [f"{nm}/@types/react"], "react/jsx-runtime": [f"{nm}/@types/react/jsx-runtime"],
                      "react-dom": [f"{nm}/@types/react-dom"], "react-dom/client": [f"{nm}/@types/react-dom/client"]}},
            "include": ["**/*.ts", "**/*.tsx"]}
        (Path(tmp) / "tsconfig.json").write_text(json.dumps(cfg))
        p = subprocess.run([tsc, "-p", "tsconfig.json"], cwd=tmp, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout[:3000]
