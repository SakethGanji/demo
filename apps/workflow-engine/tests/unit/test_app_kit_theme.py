"""Kit theming and layout primitives: the kit colours itself only through theme tokens (kit/theme.css), every token utility it uses
is defined there, the theme ships with the kit, the layout primitives carry the JSDoc control tags the visual editor reads, and the
publish bundler compiles with Tailwind v4 like the preview (token utilities, dark variant, app overrides) without ever running
app-supplied code."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

import pytest

from src.services import app_builder_ai_service as ab
from src.services import app_bundler as bundler
from src.services import oneshot_builder as ob
from src.services import platform_index as pi

KIT = Path(ab.__file__).resolve().parent / "app_kit"
THEME = (KIT / "theme.css").read_text()
PALETTE = re.compile(r"(?<![\w-])(?:[a-z-]+:)*(?:bg|text|border(?:-[trblxy])?|ring|accent|fill|stroke|from|to|via|divide|outline)-"
                     r"(?:slate|gray|zinc|neutral|stone|blue|sky|indigo|red|green|emerald|amber|yellow|orange)-\d{2,3}\b|(?<![\w-])(?:bg|text)-(?:white|black)\b")
CATEGORICAL = re.compile(r"^\s*(purple|cyan|rose): '")   # Tag's categorical hues (with their own dark: variants)


def code_lines(src: str):
    for n, line in enumerate(src.splitlines(), 1):
        if not line.lstrip().startswith(("*", "/*", "//")):
            yield n, line


def test_the_kit_uses_no_palette_colours():
    hits = [f"{p.name}:{n}: {m.group(0)}" for p in sorted(KIT.glob("*.tsx")) for n, line in code_lines(p.read_text())
            if not CATEGORICAL.match(line) for m in PALETTE.finditer(line)]
    assert hits == [], hits


def test_every_token_utility_the_kit_uses_is_defined_by_the_theme():
    defined = set(re.findall(r"--color-([\w-]+):", THEME))
    assert {"background", "foreground", "card", "muted", "muted-foreground", "border", "input", "ring", "primary", "primary-foreground",
            "success", "success-soft", "warning", "danger", "danger-soft", "info"} <= defined
    used = set()
    for p in KIT.glob("*.tsx"):
        for _, line in code_lines(p.read_text()):
            for m in re.finditer(r"(?<![\w\[-])(?:[a-z-]+:)*(?:bg|text|border(?:-[trblxy])?|ring|accent)-([a-z]+(?:-[a-z]+)*)(?:/\d+)?(?![\w-])", line):
                used.add(m.group(1))
    tokenish = {u for u in used if u.split("-")[0] in {"foreground", "card", "muted", "soft", "faint", "subtle", "border", "input", "ring",
                                                       "overlay", "primary", "info", "success", "warning", "danger", "background"}}
    assert tokenish and tokenish <= defined, tokenish - defined
    # every mapped variable has a light default and a dark value
    for name in defined:
        var = re.search(rf"--color-{name}: var\(--([\w-]+)\)", THEME).group(1)
        assert THEME.count(f"--{var}:") >= 2, var


def test_the_theme_ships_with_the_kit_and_dark_mode_is_a_class():
    paths = {f["path"] for f in ab.kit_files()}
    assert {"kit/theme.css", "kit/layout.tsx", "kit/index.ts"} <= paths
    index = next(f["content"] for f in ab.kit_files() if f["path"] == "kit/index.ts")
    assert "import './theme.css'" in index and "export * from './layout'" in index
    assert "@custom-variant dark (&:where(.dark, .dark *));" in THEME and "@theme inline" in THEME
    assert ":where(:root)" in THEME and ":where(.dark)" in THEME          # zero specificity: the app's index.css always wins
    from src.services.app_scaffold import THEME as APP_THEME
    assert ".dark {" in APP_THEME and "prefers-color-scheme" not in APP_THEME
    assert "ColorSchemeToggle" in (KIT / "resource.tsx").read_text()


PRIMITIVES = ("Box", "Stack", "Grid", "Text", "Heading", "Field", "Divider", "Spacer")


def test_layout_primitives_carry_the_editor_tags():
    src = (KIT / "layout.tsx").read_text()
    comps = {c["name"]: c for c in pi.kit_components() if c["file"] == "kit/layout.tsx" and c["kind"] == "component"}
    assert set(PRIMITIVES) <= set(comps)
    for name in PRIMITIVES:
        doc = re.search(rf"/\*\*((?:(?!\*/).)*)\*/\s*export function {name}\(", src, re.S)
        assert doc and "@category " in doc.group(1) and "@example <" in doc.group(1), name
        body = src[doc.end():src.index("}) {", doc.end())]
        props = re.findall(r"/\*\*((?:(?!\*/).)*)\*/\s*(\w+)\??:", body, re.S)
        assert props, name
        for text, prop in props:
            assert re.search(r"@control (text|textarea|number|boolean|color|spacing|select|icon|slot|hidden)\b", text), (name, prop)
            if re.search(r"@control (select|spacing)\b", text):
                assert "@options " in text, (name, prop)
            assert re.search(r"@group (Content|Layout|Style|Data|Behavior)\b", text), (name, prop)


def test_primitives_are_listed_only_with_the_flag_on(monkeypatch):
    monkeypatch.delenv("WORKFLOW_EDITABLE_RULES", raising=False)
    off = ob.brief()
    assert all(f"`{n}(" not in off for n in PRIMITIVES) and "No index.html/index.tsx/index.css/bootstrap" in off
    assert all(f"- {n}:" not in ab._kit_prompt() for n in PRIMITIVES)
    monkeypatch.setenv("WORKFLOW_EDITABLE_RULES", "1")
    on = ob.brief()
    assert all(f"`{n}(" in on for n in PRIMITIVES)
    assert "kit/theme.css" in on and "index.css" in on and "No index.html/index.tsx/index.css" not in on
    sec = on.split("## 7. Editable shape", 1)[1]
    assert "<Stack" in sec and "bg-card" in sec and "text-muted-foreground" in sec


# ── publish: Tailwind v4 like the preview ────────────────────────────────────

tw_ok = (shutil.which("esbuild") is not None and shutil.which("node") is not None
         and (bundler._STUDIO_NODE_MODULES / "@tailwindcss/node").is_dir())
needs_tw = pytest.mark.skipif(not tw_ok, reason="esbuild, node and the studio's @tailwindcss/node are required")

APP = """import React from 'react'
import './index.css'
import { Stack, Card, Button } from './kit'
export default function App() {
  return <Stack gap="4"><Card title="Hi"><Button variant="primary">Go</Button></Card><p className="text-muted-foreground dark:underline p-7">x</p></Stack>
}
"""


def app_files(css: str = ':root { --primary: #4f46e5; }\n', extra: list | None = None):
    files = [bundler.AppFileInput("App.tsx", APP), bundler.AppFileInput("index.css", css)]
    files += [bundler.AppFileInput(f["path"], f["content"]) for f in ab.kit_files()]
    return files + (extra or [])


@needs_tw
def test_publish_compiles_token_utilities_dark_variant_and_app_overrides():
    css = asyncio.run(bundler.bundle_app(app_files())).css
    assert "tailwindcss v4" in css
    for rule in (".bg-card{background-color:var(--card)}", ".text-muted-foreground{color:var(--muted-foreground)}", ".p-7{"):
        assert rule in css, rule
    assert re.search(r"\.dark\\:underline:where\(\.dark,\s?\.dark \*\)", css)
    assert ":root{--primary:#4f46e5}" in css and "--primary:#2563eb" in css      # the app override and the kit default
    assert "@theme" not in css and "@custom-variant" not in css                  # compiled, not passed through
    # an app CSS that itself imports tailwind compiles once
    twice = asyncio.run(bundler.bundle_app(app_files('@import "tailwindcss";\n:root { --primary: #000; }\n'))).css
    assert twice.count("tailwindcss v4") == 1


@needs_tw
def test_publish_never_runs_app_supplied_tailwind_code(tmp_path):
    marker = tmp_path / "pwned"
    evil = f"require('fs').writeFileSync({str(marker)!r}, 'x'); module.exports = {{}}"
    for css in ('@plugin "./evil.js";\n', '@config "./tailwind.config.js";\n'):
        extra = [bundler.AppFileInput("evil.js", evil), bundler.AppFileInput("tailwind.config.js", evil)]
        with pytest.raises(bundler.BundleBuildError):
            asyncio.run(bundler.bundle_app(app_files(css, extra)))
    # a tailwind.config.js among the app files is never discovered either
    asyncio.run(bundler.bundle_app(app_files(extra=[bundler.AppFileInput("tailwind.config.js", evil)])))
    assert not marker.exists()
    # the compile script itself refuses imports outside Tailwind's own stylesheets and code loading (defence in depth)
    with pytest.raises(bundler.BundleBuildError):
        asyncio.run(bundler.compile_tailwind('@import "/etc/hosts";', []))
    monkey = bundler._TW_CODE_DIRECTIVE
    try:
        bundler._TW_CODE_DIRECTIVE = re.compile(r"(?!x)x")        # skip the Python pre-check: the script's resolver must refuse
        with pytest.raises(bundler.BundleBuildError) as err:
            asyncio.run(bundler.compile_tailwind('@plugin "./evil.js";', []))
        assert "not allowed" in err.value.stderr
    finally:
        bundler._TW_CODE_DIRECTIVE = monkey


# ── catalog breadth: overlays, display, boards, charts (contract tags, theme-only colours) ─────────────────────

CATALOG = {"overlay.tsx": ("Modal", "ConfirmDialog", "Popover", "Menu"),
           "display.tsx": ("Avatar", "Badge", "Progress", "Accordion", "Timeline", "Stepper", "Wizard"),
           "board.tsx": ("Kanban", "Calendar", "FileUpload"),
           "plots.tsx": ("LineChart", "BarChart", "PieChart")}
TAG_CONTROL = re.compile(r"@control (text|textarea|number|boolean|color|spacing|select|icon|slot|hidden)\b")


def test_catalog_components_follow_the_contract():
    index = (KIT / "index.ts").read_text()
    for fname, names in CATALOG.items():
        src = (KIT / fname).read_text()
        assert f"export * from './{fname[:-4]}'" in index, fname
        for name in names:
            doc = re.search(rf"/\*\*((?:(?!\*/).)*)\*/\s*export function {name}(?:<\w+>)?\(", src, re.S)
            assert doc, name
            assert re.search(r"@category (layout|display|form|data|chart|overlay|navigation|page)\b", doc.group(1)), name
            assert "@example <" + name in doc.group(1), name
            sig = src[doc.end():src.index("}) {", doc.end())]
            assert "className" in sig and "...rest" in sig, name                  # className merged, the rest forwarded
            props = re.findall(r"/\*\*((?:(?!\*/).)*)\*/\s*(\w+)\??:", sig, re.S)
            for text, prop in props:
                assert TAG_CONTROL.search(text), (name, prop)
                assert re.search(r"@group (Content|Layout|Style|Data|Behavior)\b", text), (name, prop)
                if re.search(r"@control (select|spacing)\b", text):
                    assert "@options " in text, (name, prop)
        body = "\n".join(line for _, line in code_lines(src))
        assert "cx(" in body and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", body), fname   # colours only from tokens
        assert not re.search(r"^import .* from '(?!react'|\./)", src, re.M), fname            # nothing beyond React and the kit


def test_charts_colour_series_from_the_chart_tokens():
    src = (KIT / "plots.tsx").read_text()
    for i in range(1, 9):
        assert f"'fill-chart-{i}'" in src and f"'bg-chart-{i}'" in src
        assert f"--color-chart-{i}: var(--chart-{i})" in THEME
