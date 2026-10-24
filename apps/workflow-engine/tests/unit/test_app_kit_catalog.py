"""The kit's catalog components render (server-side, through esbuild + react-dom/server), the preview-only UI-state hook restores
state after a remount only inside the Studio preview, and the editable-rules brief lists components from the schema registry
while the flag-off brief is untouched by it."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import app_builder_ai_service as ab
from src.services import oneshot_builder as ob

STUDIO_NM = Path(__file__).resolve().parents[3] / "workflow-studio" / "node_modules"
can_render = shutil.which("esbuild") is not None and shutil.which("node") is not None and (STUDIO_NM / "react-dom" / "server.js").exists()
needs_render = pytest.mark.skipif(not can_render, reason="esbuild, node and the studio node_modules are required")

ENTRY = """
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { Modal, ConfirmDialog, Popover, Menu, Avatar, Badge, Progress, Accordion, Timeline, Stepper, Wizard, Kanban, Calendar,
  FileUpload, LineChart, BarChart, PieChart, Tabs, DataTable } from './kit'
const PREVIEW = process.argv[2] === 'preview'
const mem: Record<string, string> = {}
;(globalThis as any).sessionStorage = { getItem: (k: string) => mem[k] ?? null, setItem: (k: string, v: string) => { mem[k] = v } }
if (PREVIEW) (globalThis as any).__studioPreviewState = {}
// what an earlier render (before the remount) left behind
mem['studio:ui:preview:Accordion:a,b'] = JSON.stringify(['b'])
mem['studio:ui:preview:Wizard:s1,s2'] = JSON.stringify(1)
mem['studio:ui:preview:Modal:Saved'] = JSON.stringify(true)
const rows = [{ m: 'Jan', a: 3, b: 5 }, { m: 'Feb', a: 7, b: 2 }]
const out: Record<string, string> = {
  modal: renderToStaticMarkup(<Modal open title="Hello" footer={<b>ok</b>}>Body</Modal>),
  modalSaved: renderToStaticMarkup(<Modal title="Saved" trigger={<button>open</button>}>Remembered</Modal>),
  confirm: renderToStaticMarkup(<ConfirmDialog open title="Delete?" tone="danger" confirmLabel="Delete" onConfirm={() => {}} onCancel={() => {}} />),
  popover: renderToStaticMarkup(<Popover trigger={<button>t</button>} open>Inside</Popover>),
  menu: renderToStaticMarkup(<Menu items={[{ id: 'e', label: 'Edit' }]} />),
  misc: renderToStaticMarkup(<div><Avatar name="Ada Lovelace" /><Badge tone="success">Paid</Badge><Progress value={30} showValue label="Done" />
    <Timeline events={[{ id: '1', title: 'Submitted', time: 'Mon' }]} /><Stepper steps={[{ id: 'x', label: 'One' }, { id: 'y', label: 'Two' }]} current={1} /></div>),
  accordion: renderToStaticMarkup(<Accordion items={[{ id: 'a', title: 'A', content: 'alpha' }, { id: 'b', title: 'B', content: 'beta' }]} />),
  wizard: renderToStaticMarkup(<Wizard steps={[{ id: 's1', label: 'S1', content: 'first' }, { id: 's2', label: 'S2', content: 'second' }]} onFinish={() => {}} />),
  kanban: renderToStaticMarkup(<Kanban columns={[{ id: 'todo', title: 'To do' }, { id: 'done', title: 'Done' }]} items={[{ id: '1', s: 'todo', t: 'Card one' }]}
    itemKey={(x) => x.id} columnOf={(x) => x.s} renderCard={(x) => x.t} onMove={() => {}} />),
  calendar: renderToStaticMarkup(<Calendar month="2026-10-01" events={[{ id: 'p', title: 'Payroll', date: '2026-10-28' }]} />),
  upload: renderToStaticMarkup(<FileUpload onFiles={() => {}} accept=".csv" hint="CSV only" />),
  line: renderToStaticMarkup(<LineChart data={rows} x="m" series={[{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }]} area />),
  bar: renderToStaticMarkup(<BarChart data={rows} x="m" series={[{ key: 'a' }, { key: 'b' }]} mode="stacked" />),
  pie: renderToStaticMarkup(<PieChart data={[{ label: 'Open', value: 1 }, { label: 'Closed', value: 3 }]} donut centerLabel="Total" />),
  tabs: renderToStaticMarkup(<Tabs tabs={[{ id: 'a', label: 'A' }]} value="a" onChange={() => {}} />),
  table: renderToStaticMarkup(<DataTable rows={rows} rowKey={(r) => r.m} columns={[{ key: 'm', header: 'Month' }]} />),
}
console.log(JSON.stringify(out))
"""


def render(mode: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "kit").mkdir()
        for f in ab.kit_files():
            (t / f["path"]).write_text(f["content"], encoding="utf-8")
        (t / "entry.tsx").write_text(ENTRY)
        r = subprocess.run(["esbuild", "entry.tsx", "--bundle", "--platform=node", "--jsx=automatic", "--outfile=out.cjs", "--log-level=error",
                            "--loader:.css=empty"], cwd=tmp, capture_output=True, text=True, check=False,
                           env={"PATH": subprocess.os.environ["PATH"], "NODE_PATH": str(STUDIO_NM)})
        assert r.returncode == 0, r.stderr
        run = subprocess.run(["node", "out.cjs", mode], cwd=tmp, capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)


@needs_render
def test_catalog_components_render():
    h = render("publish")
    assert 'role="dialog"' in h["modal"] and "Hello" in h["modal"] and "Body" in h["modal"] and "bg-overlay" in h["modal"]
    assert "Delete?" in h["confirm"] and "Delete</button>" in h["confirm"].replace("<!-- -->", "")
    assert "Inside" in h["popover"] and "⋯" in h["menu"]
    assert ">AL<" in h["misc"] and "Paid" in h["misc"] and "30%" in h["misc"] and "Submitted" in h["misc"] and "✓" in h["misc"]
    assert "Card one" in h["kanban"] and 'draggable="true"' in h["kanban"] and "To do" in h["kanban"]
    assert "Payroll" in h["calendar"] and "October 2026" in h["calendar"]
    assert "CSV only" in h["upload"] and 'type="file"' in h["upload"]
    assert h["line"].count("stroke-chart-") == 2 and "fill-chart-1" in h["line"] and "Feb" in h["line"]
    assert h["bar"].count("<rect") >= 4 and "fill-chart-2" in h["bar"]
    assert h["pie"].count("<path") == 2 and "Total" in h["pie"] and "75%" in h["pie"]


@needs_render
def test_ui_state_survives_a_remount_only_inside_the_preview():
    pub = render("publish")                          # a published app: plain useState, storage ignored
    assert "beta" not in pub["accordion"] and "first" in pub["wizard"] and "Remembered" not in pub["modalSaved"]
    pre = render("preview")                          # the Studio preview: the state from before the remount comes back
    assert "beta" in pre["accordion"] and "alpha" not in pre["accordion"]
    assert "second" in pre["wizard"] and "first" not in pre["wizard"]
    assert "Remembered" in pre["modalSaved"]
    assert "Month" in pre["table"] and 'aria-selected="true"' in pre["tabs"]


# ── registry hook-up ───────────────────────────────────────────────────────

FAKE = {"components": [
    {"name": "Modal", "file": "kit/overlay.tsx", "exported": True, "category": "overlay", "description": "A dialog over a dimmed backdrop. More.",
     "example": "<Modal title=\"x\" />", "props": [{"name": "open", "type": "boolean", "required": False},
                                                   {"name": "size", "type": "keyof typeof W", "options": ["sm", "md"], "required": False},
                                                   {"name": "className", "type": "string"}, {"name": "stateKey", "type": "string"}]},
    {"name": "KpiCard", "file": "components/KpiCard.tsx", "exported": True, "description": "A KPI tile.", "example": "<KpiCard label=\"Open\" />",
     "props": [{"name": "label", "type": "string", "required": True}]},
], "ms": 1}


@pytest.fixture
def fake_node(monkeypatch):
    calls = []

    def run(cmd, input=None, **kw):
        payload = json.loads(input)
        calls.append(payload)
        comps = [c for c in FAKE["components"] if c["file"].startswith("kit/") or payload.get("files")]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"components": comps, "ms": 1}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr("src.services.editability_lint._parser", lambda: "/x/parser.js")
    ob._registry_cache.clear()
    yield calls
    ob._registry_cache.clear()


def test_flag_on_brief_lists_components_from_the_registry(monkeypatch, fake_node):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    b = ob.brief()
    assert "- `Modal({open?:boolean, size?:sm|md})`: A dialog over a dimmed backdrop. `<Modal title=\"x\" />`" in b
    assert "stateKey" not in b.split("Components:", 1)[1].split("## 5.", 1)[0]
    assert "KpiCard" not in b                        # the kit run lists only kit components
    ob.brief()
    assert len(fake_node) == 1 and "files" not in fake_node[0] and fake_node[0]["parser"] == "/x/parser.js"   # once per process
    app = [{"path": "components/KpiCard.tsx", "content": "export function KpiCard() { return null }"}, {"path": "kit/ui.tsx", "content": "x"}]
    b2 = ob.brief(app)
    assert fake_node[-1]["files"] == [app[0]]       # the app's own files go to the extractor, its kit copy does not
    assert "This app's own components" in b2 and "- `KpiCard({label:string})` [components/KpiCard.tsx]: A KPI tile." in b2


def test_flag_on_brief_falls_back_to_the_kit_index_without_node(monkeypatch):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    monkeypatch.setattr(shutil, "which", lambda n: None)
    ob._registry_cache.clear()
    try:
        assert ob._run_registry() == [] and "`Modal(" in ob.brief() and "`Stack(" in ob.brief()
    finally:
        ob._registry_cache.clear()


def test_flag_off_brief_never_runs_the_registry_or_lists_the_new_kit(monkeypatch, fake_node):
    monkeypatch.delenv("WORKFLOW_EDITABLE_RULES", raising=False)
    b = ob.brief()
    assert fake_node == []
    names = set(n for ns in __import__("tests.unit.test_app_kit_theme", fromlist=["CATALOG"]).CATALOG.values() for n in ns)
    for n in names | {"usePersistedUiState", "useRestoredValue", "inStudioPreview"}:
        assert f"`{n}(" not in b and f"`{n}`" not in b and f"- {n}:" not in ab._kit_prompt(), n
    assert "stateKey" not in b
    monkeypatch.setattr(ob, "_run_registry", lambda files=None: (_ for _ in ()).throw(AssertionError("registry ran")))
    assert ob.brief() == b and ob.brief([{"path": "A.tsx", "content": "x"}]) == b


@pytest.mark.skipif(shutil.which("node") is None or not (STUDIO_NM / "@babel" / "parser" / "lib" / "index.js").exists(),
                    reason="node and the studio's @babel/parser are required")
def test_real_registry_feeds_the_flag_on_brief(monkeypatch):
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    ob._registry_cache.clear()
    try:
        names = {c["name"] for c in ob._run_registry()}
        assert {"Modal", "Kanban", "LineChart", "BarChart", "PieChart", "Wizard", "DataTable", "Tabs"} <= names
        b = ob.brief()
        assert "- `BarChart({data, x:string, series, height?:number, format?, emptyText?, mode?:grouped|stacked})`" in b
        assert "`<Badge tone=\"success\">Paid</Badge>`" in b
    finally:
        ob._registry_cache.clear()


# ── cx / cn: later Tailwind classes win (a className passed to a kit component overrides the kit's default) ─────

MERGE_CASES = [
    (["rounded-xl border bg-card", "rounded-none"], "border bg-card rounded-none"),
    (["p-4", "px-2"], "p-4 px-2"),                                 # a side after the shorthand: both (the side wins in CSS)
    (["px-2 pl-1", "p-6"], "p-6"),                                  # the shorthand after the sides replaces them
    (["shadow-sm", "shadow-none"], "shadow-none"),
    (["bg-card hover:bg-muted", "bg-primary/90"], "hover:bg-muted bg-primary/90"),
    (["bg-card bg-cover", "bg-muted"], "bg-cover bg-muted"),
    (["text-sm text-muted-foreground", "text-lg"], "text-muted-foreground text-lg"),
    (["text-[10px] text-foreground", "text-danger"], "text-[10px] text-danger"),
    (["font-medium", "font-bold font-mono"], "font-bold font-mono"),
    (["gap-x-2 gap-3", "gap-1"], "gap-1"),
    (["w-full max-w-md h-4", "w-10 max-w-none", "size-6"], "max-w-none size-6"),
    (["flex flex-col items-center justify-between", "grid flex-row items-start justify-end"], "grid flex-row items-start justify-end"),
    (["grid-cols-2 md:grid-cols-3", "grid-cols-1"], "md:grid-cols-3 grid-cols-1"),
    (["border border-border", "border-2 border-danger"], "border-2 border-danger"),
    (["border-2 border-current border-t-transparent"], "border-2 border-current border-t-transparent"),
    (["border-t-4 border-t-danger", "border-t-primary"], "border-t-4 border-t-primary"),
    (["mt-2 m-0", "mb-4"], "m-0 mb-4"),                            # m-0 after mt-2 replaces it; mb-4 after m-0 refines it
    (["-mb-px", "mb-2"], "mb-2"),
    (["md:p-4 dark:bg-card", "md:p-2 bg-muted"], "dark:bg-card md:p-2 bg-muted"),
    (["truncate custom-thing", "custom-thing"], "truncate custom-thing"),
    (["", None, False, "  a  b "], "a b"),
]


@needs_render
def test_cx_merges_tailwind_classes_so_later_ones_win():
    calls = ",".join(f"cx({', '.join(json.dumps(p) if p not in (None, False) else ('null' if p is None else 'false') for p in parts)})"
                     for parts, _ in MERGE_CASES)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "kit").mkdir()
        for f in ab.kit_files():
            (t / f["path"]).write_text(f["content"], encoding="utf-8")
        (t / "entry.tsx").write_text("import React from 'react'\nimport { renderToStaticMarkup } from 'react-dom/server'\n"
                                     "import { cx, cn, Card, Stack } from './kit'\n"
                                     f"console.log(JSON.stringify({{ out: [{calls}], same: cn === cx, card: renderToStaticMarkup(<Card className=\"rounded-none bg-muted\">x</Card>),"
                                     " stack: renderToStaticMarkup(<Stack gap=\"3\" className=\"gap-8 flex-row\">x</Stack>) }))\n")
        r = subprocess.run(["esbuild", "entry.tsx", "--bundle", "--platform=node", "--jsx=automatic", "--outfile=out.cjs", "--log-level=error",
                            "--loader:.css=empty"], cwd=tmp, capture_output=True, text=True, check=False,
                           env={"PATH": subprocess.os.environ["PATH"], "NODE_PATH": str(STUDIO_NM)})
        assert r.returncode == 0, r.stderr
        run = subprocess.run(["node", "out.cjs"], cwd=tmp, capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
    data = json.loads(run.stdout)
    assert data["same"]
    for (parts, want), got in zip(MERGE_CASES, data["out"]):
        assert got == want, (parts, got)
    card = re.search(r'class="([^"]*)"', data["card"]).group(1).split()
    assert "rounded-none" in card and "rounded-xl" not in card and "bg-muted" in card and "bg-card" not in card
    stack = re.search(r'class="([^"]*)"', data["stack"]).group(1).split()
    assert "gap-8" in stack and "gap-3" not in stack and "flex-row" in stack and "flex-col" not in stack
