"""Editability lint of a generated app: how much of its JSX the Studio's visual editor can edit, plus the top shape-rule
violations (columns rendering a single field, raw <select>/tab buttons, screens pushed under an if, inline tone ternaries,
hard-coded slate/gray). The analysis is editability/lint.mjs on @babel/parser from the typecheck toolchain's node_modules;
this wrapper never raises — no node or parser gives {"available": False}.

    venv/bin/python -m src.services.editability_lint <app dir> [--each]
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

LINT_JS = Path(__file__).resolve().parent / "editability" / "lint.mjs"
KIT_DIR = Path(__file__).resolve().parent / "app_kit"
TIMEOUT_S = 30
MAX_VIOLATIONS = 20
# rules a single model pass can fix mechanically (the repair pass only runs when one of these is present)
MECHANICAL_RULES = ("R2-config-expr", "R3-column-render", "R3-columns-in-component", "R3-columns-inline",
                    "R4-raw-select", "R4-raw-tabs", "R5-imperative-screens", "R8-slate-gray")


def _parser() -> str | None:
    import os
    from . import app_builder_ai_service as ab
    chain = ab._typecheck_toolchain()
    nm = chain[1] if chain else (os.environ.get("WORKFLOW_APP_TYPECHECK_NODE_MODULES") or str(
        Path(__file__).resolve().parents[3] / "workflow-studio" / "node_modules"))
    entry = Path(nm) / "@babel" / "parser" / "lib" / "index.js"
    return str(entry) if entry.is_file() else None


def lint_files(files: list[dict]) -> dict:
    """[{path, content}] -> {available, score, score_strict, kit_nodes, editable, eligible, elements, column_renders,
    column_renders_trivial, violation_counts, violations (top 20: file, line, rule, message, fix), per_file}."""
    try:
        node, parser = shutil.which("node"), _parser()
        if not node or not parser or not LINT_JS.is_file():
            return {"available": False}
        src = [{"path": str(f["path"]), "content": f["content"]} for f in files or []
               if isinstance(f, dict) and isinstance(f.get("content"), str) and f.get("path")]
        payload = json.dumps({"files": src, "kit_dir": str(KIT_DIR), "parser": parser, "max_violations": MAX_VIOLATIONS})
        p = subprocess.run([node, str(LINT_JS)], input=payload, capture_output=True, text=True, timeout=TIMEOUT_S)
        if p.returncode != 0:
            return {"available": False, "error": (p.stderr or "").strip()[-300:]}
        out = json.loads(p.stdout)
        return out if isinstance(out, dict) else {"available": False}
    except Exception as e:  # noqa: BLE001 — a lint must never break a build
        return {"available": False, "error": str(e)[:300]}


async def lint_files_async(files: list[dict]) -> dict:
    return await asyncio.to_thread(lint_files, files)


def mechanical(report: dict) -> list[dict]:
    return [v for v in report.get("violations") or [] if v.get("rule") in MECHANICAL_RULES]


def lint_dir(root: Path) -> dict:
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx") and "node_modules" not in p.parts:
            try:
                files.append({"path": p.relative_to(root).as_posix(), "content": p.read_text(encoding="utf-8")})
            except (OSError, UnicodeDecodeError):
                continue
    return lint_files(files)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("usage: python -m src.services.editability_lint <app dir> [--each]", file=sys.stderr)
        return 2
    root = Path(args[0])
    if not root.is_dir():
        print(json.dumps({"available": False, "error": f"not a directory: {root}"}))
        return 1
    if "--each" in argv:  # one report per sub-directory (a folder of apps)
        out = {d.name: {k: v for k, v in lint_dir(d).items() if k != "per_file"}
               for d in sorted(root.iterdir()) if d.is_dir()}
    else:
        out = lint_dir(root)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
