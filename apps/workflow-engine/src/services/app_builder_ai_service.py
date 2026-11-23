"""AI service for App Builder — tool-based agent loop.

The LLM iteratively builds/modifies a React app using file-manipulation tools
(list_files, read_file, write_file, edit_file, delete_file, search_files,
get_project_summary).  When it stops issuing tool calls the loop ends and
the final working set is emitted.

The LLM generates React components as TSX source using native HTML elements,
Tailwind CSS classes and the Studio UI kit (``services/app_kit/`` shipped to every
app as ``kit/``: DataTable, Drawer, BrowserView, useJob, … — see ``_KIT_PROMPT``).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import logging
import zlib
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

from ..engine.llm_provider import call_llm
from ..core.config import settings
from ..repositories.api_test_repository import ApiTestRepository
from .app_service import AppService
from ..db.models import ApiTestExecutionModel
from ..schemas.app_builder import AppBuilderChatRequest
from .schema_inference import summarize_response
from .tsx_parser import parse_tsx_file

if False:  # pragma: no cover - typing only
    from .app_validate import OpSig

logger = logging.getLogger(__name__)

# Two tiers that are really different models. The heavy tier is the configured app-builder model; the light tier is the
# cheaper sibling from the same provider (WORKFLOW_APP_BUILDER_LIGHT_MODEL overrides it) and is used only if its credentials
# exist, otherwise both tiers are the heavy model. Light handles short edit requests on an existing app; every failed gate,
# the stall guard and long requests use heavy.
_HEAVY_MODEL = settings.app_builder_model or "claude-sonnet-4-6"
_LIGHT_SIBLINGS = {"gemini-3.8-flash": "gemini-3.7-flash"}  # gemini-2.5-flash answers 404 on this project


def _pick_light_model(heavy: str) -> str:
    import os

    from ..engine.llm_provider import is_configured
    light = os.environ.get("WORKFLOW_APP_BUILDER_LIGHT_MODEL", "").strip() or _LIGHT_SIBLINGS.get(heavy, heavy)
    try:
        return light if light == heavy or is_configured(light) else heavy
    except Exception:  # noqa: BLE001
        return heavy


_LIGHT_MODEL = _pick_light_model(_HEAVY_MODEL)
LIGHT_MAX_REQUEST_CHARS = 700  # longer asks start on the heavy model
MAX_AGENT_TURNS = 30
MAX_CONSECUTIVE_ERRORS = 3
# Stall guard: read-only calls (list/read/search/lookups) allowed since the last edit before the model must edit or ask ONE question.
READ_BUDGET = 8
IDLE_TURN_BUDGET = 6          # turns in a row without a successful edit (one lookup per turn still burns turns)
IDLE_TURN_BUDGET_NEW_APP = 10
REPAIR_READ_BUDGET = 3     # after a gate reports diagnostics
READ_BUDGET_NEW_APP = 12   # a new app also reads the backend for its brief
TRUNCATION_RETRIES = 2
CONTRACT_REPAIRS = 2
MAX_PARALLEL_SCREENS = 3
MAX_SCREENS_PER_CALL = 6
READ_ONLY_TOOLS = frozenset({"list_files", "read_files", "search_files", "read_backend", "search_components", "get_component",
                             "get_route_signature", "get_table_schema", "search_project_knowledge"})
LOOKUP_TOOL_NAMES = ("search_components", "get_component", "get_route_signature", "get_table_schema", "search_project_knowledge")
READ_FILE_CHAR_CAP = 14_000
ROUTE_DIGEST_CHARS = 5500
LOOKUP_WEIGHT = 0.25   # platform/index lookups count a quarter of a file read against the budget

# ── Studio UI kit ─────────────────────────────────────────────────────
# Reusable building blocks (DataTable, Drawer, BrowserView, useJob, …) shipped to every generated app as
# plain source under `kit/`. The service puts the current copy in the working set on every turn (like
# api/client.ts), the studio saves it with the app's files, and both bundlers (esbuild-wasm preview and
# the publish-time esbuild) resolve it as ordinary relative imports — no runtime globals, no CDN.
KIT_PREFIX = "kit/"
_KIT_DIR = Path(__file__).resolve().parent / "app_kit"
_KIT_CACHE: list[dict[str, str]] | None = None


def kit_files() -> list[dict[str, str]]:
    """The UI kit as app files (`kit/<name>`), read once from services/app_kit/."""
    global _KIT_CACHE
    if _KIT_CACHE is None:
        _KIT_CACHE = [{"path": KIT_PREFIX + p.name, "content": p.read_text(encoding="utf-8")}
                      for p in sorted(_KIT_DIR.iterdir()) if p.suffix in (".ts", ".tsx", ".css")]
    return _KIT_CACHE


def is_kit_path(path: str) -> bool:
    return path.lstrip("./").removeprefix("src/").startswith(KIT_PREFIX)


_KIT_READONLY = ("Error: {path} is part of the Studio UI kit (read-only, refreshed every turn). Import from './kit' and "
                 "wrap or compose it in your own file instead.")


def inject_kit(working_set: list[dict]) -> None:
    """Replace any saved kit copy with the current one (the kit is Studio-owned, never app-edited)."""
    working_set[:] = [f for f in working_set if not is_kit_path(f["path"])]
    for f in kit_files():
        working_set.append({"path": f["path"], "content": f["content"], "parsed_index": None})


# ── Stall guard, lookups, ranged reads ───────────────────────────────
# D's run spent ~25 read/search calls on turns 4-5 and then said "want to check in". The guard counts read-only calls since the last
# real edit; past the budget the model only sees edit/write tools (plus ONE specific blocking question) and must use them.
EDIT_TOOLS = frozenset({"write_files", "edit_files", "delete_file", "build_screens"})
FORCED_TOOL_NAMES = frozenset({"write_files", "edit_files", "build_screens", "ask_clarifying_questions", "declare_endpoint"})


def _edited(result: str) -> bool:
    return any(line.startswith(("Edited ", "Created ", "Updated ", "Deleted ", "Built ")) for line in (result or "").splitlines())


class StallGuard:
    """Counts read-only calls (weighted) and turns since the last successful edit; says when the model must stop reading."""

    def __init__(self, budget: int = READ_BUDGET, turn_budget: int = IDLE_TURN_BUDGET) -> None:
        self.base, self.turn_base = budget, turn_budget
        self.budget, self.turn_budget = budget, turn_budget
        self.reads = 0.0
        self.turns = 0
        self.notified = False

    def repair(self) -> None:
        """A gate (tsc, imports, data contract …) just reported exact diagnostics: allow only a few reads before the fix."""
        self.budget, self.turn_budget = min(self.base, REPAIR_READ_BUDGET), min(self.turn_base, REPAIR_READ_BUDGET + 1)
        self.reads, self.turns, self.notified = 0.0, 0, False

    def reset(self, budget: int | None = None, turn_budget: int | None = None) -> None:
        self.base, self.turn_base = budget or self.base, turn_budget or self.turn_base
        self.budget, self.turn_budget, self.reads, self.turns, self.notified = self.base, self.turn_base, 0.0, 0, False

    def tick(self) -> None:
        """One model turn that used tools has ended."""
        self.turns += 1

    def record(self, tool: str, result: str) -> None:
        if tool in LOOKUP_TOOL_NAMES:
            self.reads += LOOKUP_WEIGHT  # an index lookup is the cheap path we want; it only counts when it is spammed
        elif tool in READ_ONLY_TOOLS:
            self.reads += 1
        elif tool in EDIT_TOOLS and _edited(result):
            self.budget, self.turn_budget = self.base, self.turn_base
            self.reads, self.turns, self.notified = 0.0, 0, False

    @property
    def exhausted(self) -> bool:
        return self.reads >= self.budget or self.turns >= self.turn_budget

    @property
    def remaining(self) -> int:
        return max(0, int(min(self.budget - self.reads, self.turn_budget - self.turns)))


def forced_tools(tools: list[dict], brief_pending: bool = False, ask_only: bool = False) -> list[dict]:
    """The tools a model may use once its read budget is spent: change something, or ask one blocking question."""
    keep = {"ask_clarifying_questions"} if ask_only else set(FORCED_TOOL_NAMES) | ({"record_brief"} if brief_pending else set())
    return [t for t in tools if t["name"] in keep]


def parse_read_spec(spec: str) -> tuple[str, int | None, int | None]:
    """``views/A.tsx:120-210`` -> (path, 120, 210); ``views/A.tsx`` -> (path, None, None)."""
    m = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?", spec.strip())
    if not m:
        return spec.strip(), None, None
    start = int(m.group(2))
    return m.group(1), start, int(m.group(3) or start)


def _whitespace_insensitive_span(content: str, old_string: str) -> tuple[int, int] | None:
    """(start, end) of the single place in ``content`` that equals ``old_string`` ignoring whitespace differences, else None."""
    tokens = old_string.split()
    if len(tokens) < 3:
        return None
    hits = list(re.finditer(r"\s+".join(re.escape(t) for t in tokens), content))
    return (hits[0].start(), hits[0].end()) if len(hits) == 1 else None


def _closest_lines(content: str, old_string: str) -> str:
    """For a failed patch: the numbered file lines most like the first line of old_string, so the retry needs no extra read."""
    import difflib
    first = next((ln.strip() for ln in old_string.splitlines() if ln.strip()), "")
    lines = content.split("\n")
    if not first:
        return " Read the file first."
    near = difflib.get_close_matches(first, [ln.strip() for ln in lines], n=2, cutoff=0.5)
    out = []
    for n in near:
        i = next(k for k, ln in enumerate(lines) if ln.strip() == n)
        out.append("\n".join(f"{k + 1:4d} | {lines[k]}" for k in range(max(0, i - 1), min(len(lines), i + 3))))
    return (" Closest text in the file (copy old_string exactly from here):\n" + "\n---\n".join(out)) if out else " Read the lines you need with a range."


def _kit_types_for(signature: str, file: str = "", cap: int = 1100, skip: frozenset[str] = frozenset()) -> str:
    """Source of the kit ``interface``/``type`` declarations a signature or prop types refer to (e.g. PollOptions, Column),
    searched across all kit files, so the model sees the real member names (``key``, ``header``) and not just ``Column<T>[]``."""
    srcs = []
    for path in sorted(_KIT_DIR.iterdir()):
        if path.suffix in (".ts", ".tsx"):
            try:
                srcs.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    out = []
    for name in dict.fromkeys(re.findall(r"\b([A-Z]\w+)\b", signature)):
        if name in skip or name in ("React", "Promise", "Record", "Array", "Date", "Partial", "Omit"):
            continue
        for src in srcs:
            m = re.search(r"export (?:interface|type) " + re.escape(name) + r"\b[^{=]*[{=]", src)
            if not m:
                continue
            depth, i = 0, m.end() - 1 if src[m.end() - 1] == "{" else m.end()
            end = i
            for end in range(i, min(len(src), i + 4000)):
                ch = src[end]
                depth += ch == "{"
                depth -= ch == "}"
                if (depth == 0 and ch == "}") or (depth == 0 and ch == "\n" and src[m.end() - 1] == "="):
                    break
            decl = src[m.start():end + 1]
            out.append(decl if len(decl) <= cap else decl[:cap] + f"\n// [type {name} cut at {cap} of {len(decl)} chars]")
            break
    return "\n".join(out)


LOOKUP_PAGE_CHARS = 9000
READ_BACKEND_PAGE_CHARS = 24_000
DELIVERY_REPAIRS = 2   # automatic "you promised X, it is not there" continuations before the build is reported as partial


def paginate(text: str, offset: int = 0, cap: int = 9000) -> str:
    """One page of a long tool result. Never cuts silently: a cut page ends with the offset that continues it."""
    offset = max(0, int(offset or 0))
    if offset == 0 and len(text) <= cap:
        return text
    if offset >= len(text):
        return f"[offset {offset} is past the end: the result is {len(text)} chars]"
    page = text[offset:offset + cap]
    head = f"[continuing at offset {offset} of {len(text)} chars]\n" if offset else ""
    end = offset + len(page)
    tail = f"\n[truncated at char {end} of {len(text)} — call again with offset={end} for the rest]" if end < len(text) else ""
    return head + page + tail


def format_lookup(name: str, data: Any, cap: int | None = 9000, offset: int = 0) -> str:
    """Compact text for a platform lookup result (what the model reads), not raw JSON. Long results are paginated (``offset``)."""
    if isinstance(data, dict) and data.get("error"):
        return f"Error: {data['error']}"
    if name in ("search_components", "get_component"):
        items = data if isinstance(data, list) else [data]
        parts = []
        for c in items[:8]:
            if not c:
                continue
            lines = [f"## {c['name']} ({c['kind']}, {c['file']}) — {c['import']}", (c.get("use_when") or "").strip()]
            if c.get("props"):
                lines.append("props:")
                for p in c["props"]:
                    d = f" = {p['default']}" if p.get("default") else ""
                    doc = f" — {p['doc']}" if p.get("doc") else ""
                    lines.append(f"  {p['name']}{'' if p['required'] else '?'}: {p['type']}{d}{doc}")
                types = _kit_types_for(" ".join(p["type"] for p in c["props"]), c["file"], 700, skip=frozenset({c["name"] + "Props"}))
                if types:
                    lines.append("types:\n" + types)
            else:
                lines.append("signature: " + (c.get("signature") or ""))
                types = _kit_types_for(c.get("signature") or "", c["file"])
                if types:
                    lines.append(types)
            if c.get("example"):
                lines.append("example: " + c["example"])
            parts.append("\n".join(x for x in lines if x))
        text = "\n\n".join(parts) or "No matching kit component."
    elif name == "get_route_signature" and isinstance(data, dict):
        def j(key: str, empty: str = "-") -> str:
            return ", ".join(map(str, data.get(key) or [])) or empty
        text = (f"Route {data.get('method')} {data.get('path')} (api.{data.get('name')}) — returns {data.get('response_shape') or 'value'} "
                f"with fields: {j('response', '(not statically known: use read_backend)')}. path params: {j('path_params')}. "
                f"query: {j('query')}. body fields: {j('body')}. statuses: {j('statuses')}. tables: {j('tables')}.")
    elif name == "get_table_schema" and isinstance(data, dict):
        cols = data.get("columns") or []
        text = f"Table {data.get('name')}: " + ", ".join(
            f"{c.get('name')}:{c.get('type')}{'*' if c.get('required') else ''}" for c in cols if isinstance(c, dict)
        ) + " (every row also has id, created_at, updated_at; * = required)"
    elif name == "search_project_knowledge" and isinstance(data, dict):
        text = data.get("text") or "Nothing recorded for that query."
    else:
        text = json.dumps(data, separators=(",", ":"), default=str)
    return text if cap is None else paginate(text, offset, cap)


# ── Careful-developer brief (clarify before build) ───────────────────
# A NEW project app is not built from a guess. Before the first UI file the model must either
#   * call `ask_clarifying_questions` (ends the turn; SSE `{"type":"clarify", questions, brief}` + a numbered text), or
#   * call `record_brief` (users, screens, journeys, the project routes it will call — validated against the
#     project's real routes — states and acceptance criteria). It is saved as `BRIEF.md` with the app's files and
#     emitted as SSE `{"type":"brief"}`; only then do write_files/edit_files work.
# Off when there is no project, when the app already has UI files or a BRIEF.md, under the test-only fake LLM,
# or with WORKFLOW_APP_BUILDER_CLARIFY=off.
BRIEF_PATH = "BRIEF.md"
MAX_CLARIFY_ROUNDS = 2
_CLARIFY_MARKER = "Before I build this, I need a few answers"
_REQUIRED_STATES = ("empty", "loading", "error")


def clarify_enabled() -> bool:
    import os
    if os.environ.get("WORKFLOW_APP_BUILDER_CLARIFY", "on").strip().lower() in ("0", "off", "false", "no"):
        return False
    from ..engine import fake_llm
    # scripted test stack: keep its call sequence unchanged
    return fake_llm.ACTIVE is None


def is_vague_request(message: str) -> bool:
    """A one-liner such as "Build a QA tool" is a guess however many routes the project has. A spec-sized message (150+ words) is not."""
    return len((message or "").split()) < 150


def has_user_files(working_set: list[dict]) -> bool:
    """True when the app already has files the model wrote (not the kit, the generated client or the brief)."""
    from .client_codegen import CLIENT_PATH
    return any(not is_kit_path(f["path"]) and f["path"] not in (CLIENT_PATH, BRIEF_PATH) for f in working_set)


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _text(v: Any) -> str:
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_text(x)}" for k, x in v.items())
    if isinstance(v, list):
        return "; ".join(_text(x) for x in v)
    return str(v).strip() if v is not None else ""


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, q in enumerate(_as_list(raw), 1):
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        item: dict[str, Any] = {"id": str(q.get("id") or f"q{i}"), "question": str(q["question"]).strip()}
        for k in ("topic", "why", "default"):
            if q.get(k):
                item[k] = str(q[k]).strip()
        opts = [str(o).strip() for o in _as_list(q.get("options")) if str(o).strip()]
        if opts:
            item["options"] = opts
        out.append(item)
    return out


def clarify_text(questions: list[dict[str, Any]], brief: dict[str, Any] | None) -> str:
    """The assistant message for a clarify turn — it carries the understanding so far, so it survives in history."""
    lines = [_CLARIFY_MARKER + " (reply with the numbers, or say \"use your defaults\"):", ""]
    for n, q in enumerate(questions, 1):
        line = f"{n}. {q['question']}"
        if q.get("options"):
            line += " Options: " + " / ".join(q["options"]) + "."
        if q.get("default"):
            line += f" (default: {q['default']})"
        lines.append(line)
    so_far = {k: v for k, v in (brief or {}).items() if v}
    if so_far:
        lines += ["", "What I have so far:"] + [f"- {k}: {_text(v)}" for k, v in so_far.items()]
    return "\n".join(lines)


def clarify_rounds(history: list[dict[str, str]] | None) -> int:
    return sum(1 for m in history or [] if m.get("role") == "assistant" and _CLARIFY_MARKER in (m.get("content") or ""))


def handler_source(module_source: str, function: str, max_chars: int = 20_000) -> str:
    """The handler function's source plus the module-level helpers it calls (one level), for `read_backend`."""
    import ast
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return module_source[:max_chars] + ("" if len(module_source) <= max_chars else f"\n# … source cut at {max_chars} of {len(module_source)} chars")
    lines = module_source.splitlines()
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    fn = funcs.get(function)
    if fn is None:
        return f"(function {function!r} not found in the module)"

    def seg(n: ast.AST) -> str:
        start = min([d.lineno for d in getattr(n, "decorator_list", [])] + [n.lineno])  # type: ignore[attr-defined]
        return "\n".join(lines[start - 1:n.end_lineno])  # type: ignore[attr-defined]

    parts = [seg(fn)]
    called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    for name in sorted(called):
        if name in funcs and name != function and len("\n\n".join(parts)) < max_chars:
            parts.append(seg(funcs[name]))
    text = "\n\n".join(parts)
    return text if len(text) <= max_chars else text[:max_chars] + f"\n# … handler source cut at {max_chars} of {len(text)} chars"


# whitespace and line breaks around the dot are normal formatting (`api\n  .getX()`): they still count as a call
_API_CALL_RE = re.compile(r"(?<![\w$.])api\s*\??\.\s*(\w+)\s*\(")


def dropped_api_calls(before: dict[str, str], after_files: list[dict[str, str]], request: str) -> list[str]:
    """Client operations the app now calls from fewer places than before this turn — the classic careless-edit
    regression (an edit that rewrites a polling block and loses the events poll, while the initial load still calls
    it). Operations the request names are excluded; the caller asks the model once, so an intended removal costs one
    extra turn."""
    from collections import Counter

    from .client_codegen import CLIENT_PATH

    def used(contents: list[str]) -> Counter[str]:
        return Counter(m for c in contents for m in _API_CALL_RE.findall(c))
    old = used([c for p, c in before.items() if not is_kit_path(p) and p != CLIENT_PATH])
    new = used([f["content"] for f in after_files if not is_kit_path(f["path"]) and f["path"] != CLIENT_PATH])
    return sorted(op for op, n in old.items() if new[op] < n and op not in (request or ""))


_INTERVAL_RE = re.compile(r"\bsetInterval\s*\(")


def hand_rolled_polling(files: list[dict[str, str]]) -> list[str]:
    """App files that poll with their own `setInterval` instead of the kit hooks (comments ignored). The caller turns
    this into a one-time prompt, so a legitimate use (a clock tick) costs one turn, not a failure."""
    from .client_codegen import CLIENT_PATH

    return [f["path"] for f in files
            if not is_kit_path(f["path"]) and f["path"] != CLIENT_PATH and f["path"].endswith((".ts", ".tsx"))
            and _INTERVAL_RE.search(_LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", f["content"])))]


_DB_TABLE_RE = re.compile(r"""studio\.db\.table\(\s*['"]([\w.-]+)['"]\s*\)""")


def tables_read_by(source: str) -> list[str]:
    """Project data tables a handler touches (`studio.db.table("x")`), in first-use order."""
    return list(dict.fromkeys(_DB_TABLE_RE.findall(source or "")))


def sample_value(v: Any, depth: int = 0) -> Any:
    """A real stored value, shortened for the prompt: strings clipped, lists cut to 2 items, nesting capped. The
    *shape* is what matters — passthrough fields (e.g. a job's output saved verbatim) have no schema anywhere else."""
    if isinstance(v, str):
        return v if len(v) <= 120 else v[:120] + "…"
    if depth >= 3:
        return "…" if isinstance(v, dict | list) else v
    if isinstance(v, list):
        return [sample_value(x, depth + 1) for x in v[:2]] + (["…"] if len(v) > 2 else [])
    if isinstance(v, dict):
        return {k: sample_value(x, depth + 1) for k, x in list(v.items())[:25]}
    return v


def brief_markdown(b: dict[str, Any]) -> str:
    out = ["# App brief", "", "Recorded by the app builder before building; the UI must satisfy it.", "",
           "## Who uses it", b["users"], "", "## Screens"]
    out += [f"- **{s['name']}** — {s['purpose']}" if s.get("purpose") else f"- **{s['name']}**" for s in b["screens"]]
    out += ["", "## User journeys"] + [f"{i}. {j}" for i, j in enumerate(b["journeys"], 1)]
    out += ["", "## Backend routes it calls"]
    out += [f"- `{r['method']} {r['path']}` → `api.{r['operation']}` — {r.get('purpose') or ''}".rstrip(" —") for r in b["routes"]] or ["- (none)"]
    out += ["", "## States"] + [f"- **{k}:** {v}" for k, v in b["states"].items()]
    out += ["", "## Acceptance criteria"] + [f"- [ ] {c}" for c in b["acceptance_criteria"]]
    if b.get("assumptions"):
        out += ["", "## Assumptions"] + [f"- {a}" for a in b["assumptions"]]
    return "\n".join(out) + "\n"


# ── Helpers ───────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


async def _pump(run: Callable[[Callable[[dict[str, Any]], None]], Awaitable[Any]]) -> AsyncGenerator[tuple[str, Any], None]:
    """Run ``run(sink)``, yielding ``("event", ev)`` for each event it hands ``sink`` as it goes, then ``("result", value)``."""
    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.ensure_future(run(q.put_nowait))
    try:
        while not task.done() or not q.empty():
            if not q.empty():
                yield "event", q.get_nowait()
                continue
            get = asyncio.ensure_future(q.get())
            await asyncio.wait({task, get}, return_when=asyncio.FIRST_COMPLETED)
            if get.done():
                yield "event", get.result()
            else:
                get.cancel()
    finally:
        if not task.done():
            task.cancel()
    yield "result", task.result()


def _format_response_summary(summary: dict[str, Any] | None, lines: list[str]) -> None:
    """Append the response-summary section to `lines` for an api execution.

    Mirrors the kinds emitted by `summarize_response`. Tolerant of missing
    fields so a future schema bump doesn't break old rows.
    """
    if not summary:
        return
    kind = summary.get("kind")
    partial = " (body was truncated at capture)" if summary.get("partial") else ""

    if kind == "binary":
        lines.append("")
        lines.append(
            f"Binary response{partial} — render as a file download via "
            "`res.blob()` + `URL.createObjectURL()`."
        )
        if summary.get("filename"):
            lines.append(f"Suggested filename: `{summary['filename']}`")
        if summary.get("decode_error"):
            lines.append(f"_Note: {summary['decode_error']}_")
        return

    if kind == "json":
        schema = summary.get("schema", {})
        sample = summary.get("sample")
        lines.append("")
        lines.append(f"### Response Schema{partial}")
        lines.append(f"```json\n{json.dumps(schema, indent=2)}\n```")
        if sample is not None:
            lines.append("")
            lines.append("### Response Sample (truncated)")
            lines.append(f"```json\n{json.dumps(sample, indent=2)}\n```")
        if summary.get("note"):
            lines.append(f"_Note: {summary['note']}_")
        return

    if kind == "csv":
        delim = summary.get("delimiter", ",")
        label = "TSV" if delim == "\t" else "CSV"
        lines.append("")
        lines.append(f"### Response Template ({label}){partial}")
        if summary.get("header"):
            lines.append(f"- Header: `{summary['header']}`")
        lines.append(f"- Approx rows: {summary.get('row_count', 0)}")
        return

    if kind == "xml":
        lines.append("")
        lines.append(f"### Response Template (XML){partial}")
        if summary.get("root"):
            lines.append(f"- Root: `<{summary['root']}>`")
        tags = summary.get("tags") or []
        if tags:
            lines.append(f"- Tags seen: {', '.join(f'`<{t}>`' for t in tags)}")
        return

    if kind == "yaml":
        lines.append("")
        lines.append(f"### Response Template (YAML){partial}")
        keys = summary.get("top_keys") or []
        if keys:
            lines.append(f"- Top-level keys: {', '.join(f'`{k}`' for k in keys)}")
        else:
            lines.append("- (no top-level keys detected)")
        return

    if kind == "text":
        snippet = summary.get("snippet", "")
        if snippet:
            lines.append("")
            lines.append(f"```\n{snippet}\n```")
        if summary.get("decode_error"):
            lines.append(f"_Note: {summary['decode_error']}_")
        return

    # Unknown kind — emit nothing rather than raw JSON dump.


def _tool_call_signature(tool_calls: list) -> str:
    """Create a hashable signature from tool calls for duplicate detection."""
    parts = []
    for tc in tool_calls:
        args_str = json.dumps(tc.args, sort_keys=True)
        parts.append(f"{tc.name}:{args_str}")
    return "|".join(sorted(parts))


def _summarize_tool_args(args: dict) -> dict:
    """Summarize tool args for SSE display (truncate long content)."""
    summary = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            summary[k] = v[:200] + f"... ({len(v)} chars)"
        else:
            summary[k] = v
    return summary


def _truncate_for_display(result: str) -> str:
    """Truncate tool result for SSE display."""
    if len(result) > 500:
        return result[:500] + f"... ({len(result)} chars total)"
    return result


async def _generated_syntax_errors(files: list[dict]) -> str | None:
    """Parse generated source on stdin; never materialize model-provided paths."""
    # The kit is Studio-owned and type-checked at the source; skip it (the tools refuse kit edits).
    source_files = [f for f in files if f["path"].endswith((".tsx", ".ts", ".jsx", ".js")) and not is_kit_path(f["path"])]
    if not source_files:
        return None
    binary = shutil.which("esbuild")
    if not binary:
        return "Code validation requires esbuild on the engine PATH."
    for file in source_files:
        loader = file["path"].rsplit(".", 1)[-1]
        process = await asyncio.create_subprocess_exec(
            binary, f"--loader={loader}", f"--sourcefile={file['path']}", "--log-level=error",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(file["content"].encode()), timeout=15)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"Syntax validation timed out for {file['path']}"
        if process.returncode:
            return stderr.decode(errors="replace")[:4000]
    return unresolved_imports(files)


_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


def _typecheck_toolchain() -> tuple[str, str] | None:
    """(tsc binary, node_modules with @types/react) — WORKFLOW_APP_TYPECHECK_NODE_MODULES, else the studio's."""
    import os
    nm = os.environ.get("WORKFLOW_APP_TYPECHECK_NODE_MODULES") or str(
        Path(__file__).resolve().parents[3] / "workflow-studio" / "node_modules")
    tsc = Path(nm) / ".bin" / "tsc"
    if tsc.exists() and (Path(nm) / "@types" / "react").is_dir():
        return str(tsc), nm
    return None


async def typecheck_errors(files: list[dict]) -> str | None:
    """Type-check the app (non-strict) so wrong kit props (`rowKey="id"`, an object as a ReactNode, an unknown tone)
    are repaired before the user sees a runtime crash. Soft: skipped when no toolchain or unsafe paths."""
    import os
    import tempfile

    from .client_codegen import CLIENT_PATH
    chain = _typecheck_toolchain()
    src = [f for f in files if f["path"].endswith((".tsx", ".ts"))]
    if chain is None or not src or not clarify_enabled():
        return None
    if any(".." in f["path"].split("/") or not _SAFE_PATH_RE.match(f["path"]) for f in src):
        return None
    tsc, nm = chain
    with tempfile.TemporaryDirectory(prefix="appcheck-") as tmp:
        for f in src:
            p = Path(tmp) / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
        cfg = {"compilerOptions": {
            "target": "es2020", "lib": ["dom", "es2020", "dom.iterable"], "jsx": "react-jsx", "strict": False,
            "noEmit": True, "moduleResolution": "bundler", "module": "esnext", "esModuleInterop": True,
            "skipLibCheck": True, "allowSyntheticDefaultImports": True, "isolatedModules": True, "types": [],
            "typeRoots": [f"{nm}/@types"], "baseUrl": ".",
            "paths": {"react": [f"{nm}/@types/react"], "react/jsx-runtime": [f"{nm}/@types/react/jsx-runtime"],
                      "react-dom": [f"{nm}/@types/react-dom"], "react-dom/client": [f"{nm}/@types/react-dom/client"]}},
            "include": ["**/*.ts", "**/*.tsx"]}
        (Path(tmp) / "tsconfig.json").write_text(json.dumps(cfg))
        proc = await asyncio.create_subprocess_exec(tsc, "-p", "tsconfig.json", cwd=tmp, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, env={**os.environ, "NO_COLOR": "1"})
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None
    if proc.returncode == 0:
        return None
    lines = [ln for ln in out.decode(errors="replace").splitlines()
             if ln.strip() and not ln.startswith((KIT_PREFIX, CLIENT_PATH))]
    errs = [ln for ln in lines if ": error TS" in ln]
    if not errs:
        return None
    return "\n".join(lines[:40])


# Only real module specifiers: `import … from 'x'`, `import 'x'`, `export … from 'x'` at the start of a statement, and `import('x')`.
# (The old pattern matched any `from'`/`import '` in a string, e.g. `s.includes('from') || s.includes('x')`.)
_IMPORT_RE = re.compile(
    r"""^[ \t]*(?:import\s+[^'";]*?\bfrom\s*|import\s*|export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s*from\s*)['"]([^'"]+)['"]"""
    r"""|\bimport\s*\(\s*['"]([^'"]+)['"]""", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"^\s*//.*$", re.MULTILINE)
_BARE_OK = {"react", "react-dom", "react-dom/client", "react/jsx-runtime"}


def unresolved_imports(files: list[dict]) -> str | None:
    """Both bundlers fail the whole app on one bad import; catch it here so the model repairs it in the same turn."""
    import posixpath

    from .client_codegen import CLIENT_PATH
    paths = {f["path"].lstrip("./") for f in files}
    exts = ("", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.js")
    errors: list[str] = []
    for f in files:
        if not f["path"].endswith((".tsx", ".ts", ".jsx", ".js")) or is_kit_path(f["path"]) or f["path"] == CLIENT_PATH:
            continue
        code = _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", f["content"]))
        for spec in (a or b for a, b in _IMPORT_RE.findall(code)):
            if not spec.startswith("."):
                if spec not in _BARE_OK and not spec.startswith(("http:", "https:")):
                    errors.append(f"{f['path']}: cannot import '{spec}' — only react/react-dom and relative files exist")
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(f["path"].lstrip("./")), spec))
            if any(target + e in paths for e in exts):
                continue
            base = posixpath.basename(target)
            hint = next((p for p in sorted(paths) if posixpath.splitext(p)[0].endswith("/" + base) or posixpath.splitext(p)[0] == base
                         or p.startswith(base + "/index.")), None)
            if hint:
                rel = posixpath.relpath(posixpath.splitext(hint)[0].removesuffix("/index"), posixpath.dirname(f["path"].lstrip("./")) or ".")
                hint = f" — did you mean '{rel if rel.startswith('.') else './' + rel}'?"
            errors.append(f"{f['path']}: cannot resolve '{spec}'{hint or ''}")
    return ("Unresolved imports (paths are relative to the importing file):\n" + "\n".join(errors[:30])) if errors else None


# ── Agent System Prompt ───────────────────────────────────────────────

_BOUND_BLOCKS = {"ResourceTable", "ResourceForm", "StatusActions", "RecordPanel", "StatTiles", "GroupChart", "ExportButton",
                 "Inbox", "Page", "AppShell", "ListDetailLayout", "DashboardLayout", "FormPage", "InboxLayout", "SettingsPage"}


def _kit_prompt() -> str:
    """The kit section of the system prompt, generated: one line per component from ``platform_index`` (name: use-when), so a new
    kit component is taught by adding a JSDoc line to it. Props come from ``get_component`` on demand."""
    try:
        import os
        from .platform_index import kit_index_lines
        def short(line: str) -> str:  # "Name: first sentence", at most ~85 chars
            head = re.split(r"(?<=[a-z)])\.\s+(?=[A-Z])", line, maxsplit=1)[0]
            return head if len(head) <= 85 else head[:82].rsplit(" ", 1)[0] + "…"
        # the backend-bound layouts/blocks are named on one line: each composes the same few props (resource, columns, fields)
        # Select is listed only with the visual-editing trial on (oneshot_builder.editable_rules_enabled)
        trial = os.environ.get("WORKFLOW_EDITABLE_RULES", "").strip().lower() in ("1", "true", "yes", "on")
        from .oneshot_builder import _EDITABLE_KIT  # Select + the layout primitives (kit/layout.tsx): trial-only
        lines = [line for line in kit_index_lines() if trial or line.split(":", 1)[0] not in _EDITABLE_KIT]
        bound = [line for line in lines if line.split(":", 1)[0] in _BOUND_BLOCKS]
        index = "\n".join(f"- {short(line)}" for line in lines if line not in bound)
        if bound:
            index += ("\n- Backend-bound (`resource={def}`; fetch, loading/empty/error, toasts built in): "
                      + ", ".join(line.split(":", 1)[0] for line in bound))
    except Exception:
        logger.warning("kit index unavailable", exc_info=True)
        index = "- (call search_components)"
    return f"""\
## Studio UI kit (`kit/`, provided by Studio, read-only)
`import {{ … }} from './kit'` (from `views/X.tsx`: `'../kit'`; same for `'../api/client'`). Components (use these, not hand-rolled markup):
{index}
Hooks/helpers: `usePoll`, `useJob`, `useElapsed`, `isTerminal`, `eventsToSteps`, `eventsToNetwork`, `toImageSrc`, `fmtMs`, `timeAgo`, `cx`, `toast`, \
`useCommandK`, `changedByAiFilter`, `parseSteps`.
**Never guess a prop.** Before using components or hooks call `get_component(names=[…all you will use…])` ONCE (props, defaults, example) or \
`search_components("what you need")`. Tones/variants are exact unions; your code is type-checked. Steps/diffs of JSON data: `StepDiff` \
(readable added/removed/changed steps), never `JSON.stringify` in the UI. Detail for a selected row: `Drawer variant="docked"` right under the list \
keeps the live pane visible; the default overlay hides it. Row buttons: `RowActions` (real buttons). \
Mount `<Toaster/>` once; `toast.success(msg)` / `toast.error(msg)`.
"""


_KIT_PROMPT = _kit_prompt()

_SCAFFOLD_PROMPT = (
    "## Scaffolded app (already written, do not rewrite it)\n"
    "The app was generated from the plan: `App.tsx` (AppShell + one file per screen in `views/`), `api/resources.ts` (typed rows, "
    "one `ResourceDef` per table, registered by name) and `index.css` (theme tokens: change colours/radius there, never in TSX). "
    "Screens are compositions of kit layouts/blocks: `ListDetailLayout`, `FormPage`, `InboxLayout`, `DashboardLayout`, `SettingsPage`, "
    "and blocks `ResourceTable`, `ResourceForm`, `RecordPanel`, `StatusActions`, `StatTiles`, `GroupChart`, `ExportButton`, `Inbox`, "
    "each taking `resource={def}` (or its registered name) plus props such as `columns`, `fields`, `filters`, `title`. They fetch, "
    "show loading/empty/error states and toast on their own: never write fetch, useEffect or loading/error state for a resource. "
    "Change a screen with `edit_files` on its small file (props, copy, columns, labels); write new code only for what no block "
    "covers, composing the same blocks. Placeholder pages say 'is not built yet.' and need a real screen.")

_JOBS_PROMPT = """\
## Long-running work: the Jobs pattern
Browser test runs, crawls and AI triage are **jobs**: never block a request on them. \
1) Run click → call the start route → `{job_id}` at once; toast. \
Call shape is `api.op(params?, body?, query?)` but a route WITHOUT path params takes the body FIRST: `api.startRun({case_ids})`, never `api.startRun({}, {…})` \
(that sends the body as a query string); with path params: `api.decide({id}, {decision})`. \
2) Hold the id in state and drive the UI with `useJob(jobId, {getStatus, getEvents})` (status, `progress`, `results`, incremental \
`events`), `<BrowserView frame=…>` for the screenshot, `live={!done}`. Polling only via `usePoll`/`useJob`/`BrowserView`, never a hand-rolled \
`setInterval`. A run route keyed by run id: `useJob(runId, {getStatus:(id)=>api.getRunsByIdStatus({id}), getEvents:(id,after)=>api.getRunsByIdEvents({id},{after})})`; \
fetch the final rows with `usePoll(fn, 1000, {enabled: done})`.
3) Job results are the Jobs service's live view: `case_id` is the case KEY and there is no database row id. Routes that act on a stored result \
(`POST /results/{id}/triage`) take the stored row `id`: when done, load the stored rows (e.g. `GET /runs/{id}` → `results:[{id, case_key, …}]`). \
Never invent an id or fall back to a computed one; if no row id exists yet, disable the action.
4) A failed result opens a triage `Drawer` (verdict + confidence, evidence, before/after, proposed fix as `StepDiff`/`DiffView`, Approve / \
Reject calling their routes, toast, refresh). AI proposes, a person approves: never auto-approve.

## Correctness rules (each one was a real bug)
- A row's action button (Triage, Approve, Run) acts on THAT row: capture its id in the closure (`RowActions` / `onClick={() => open(r.id)}`) \
and load the item for exactly that id; never "the latest" or index 0.
- A tab or chip named by a state ("Pending", "Failed", "Approved") filters rows by that state field with a real predicate; its count comes from the same filter.
- Show only values the API returned. Every field you render must exist in that route's response (`get_route_signature`, or `read_backend` when \
unsure); no placeholder numbers, durations, ids, "last green", names or drawer titles. If a value is not available, show "—" or hide it.
- Case types can be `ui`, `api` or `hybrid` (a hybrid case's `steps` mix browser steps and API steps): show the type as a `Tag` and let the editor \
add either kind. "Changed by AI": show `AiChip` and offer `changedByAiFilter(...)` only when the data has a real flag for it.
- Guard conditions before approving an AI change (re-run result, evidence hash, assertions unchanged) go in `GuardChecks` built from returned fields; never a hard-coded pass.

## Rich internal-tool UIs
- Light theme: `bg-slate-50` page, white `Card`s with `border-slate-200`, `text-slate-900/600/500`, blue-600 accent, emerald/red/amber/blue for status. Dense but readable.
- Workbench layout: top bar + `flex-1 min-h-0 overflow-y-auto` body with a responsive grid like \
`grid grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_420px]`; every grid child `min-w-0`. Never hide a primary pane below a breakpoint \
(the studio preview is often under 1024px): stack it. In-app navigation with `useState` + `<Tabs>` (no router).
"""

_PLAN_PROMPT = (
    "The team approved project plan v{version}; BRIEF.md (the screens, journeys, routes and states) comes from it. Do not ask the "
    "user questions and do not re-plan: build exactly BRIEF.md, calling only its routes with the field names the api client "
    "types. A planned route that is still a stub returns 501 until its module is built: show a clear 'not available yet' state. "
    "If the request needs something the plan does not have, build what the plan covers and say what is missing in one line: it "
    "needs a plan revision.")

_BACKEND_PROMPT = (
    "## Backend (you own it too)\n"
    "The project has an approved plan (v{version}); its modules: {modules}. Backend before the UI: when the UI needs data or a route "
    "that does not exist, is still a stub (501) or behaves wrongly, call `build_backend` (one planned module per call, with a "
    "concrete instruction) BEFORE the UI that uses it, then build the UI against the routes it returns (api/client.ts is "
    "regenerated). Prefer it to `declare_endpoint` stubs. Never design schema yourself: tables, columns, relations and new "
    "modules belong to the planner. When the change needs something the plan does not have, `build_backend` returns "
    "`needs_plan_revision` (the planner drafts a revision for the user to approve): stop there and end your turn.")
_BACKEND_RESUME = ("The planner's plan v{version} is approved now: the backend change that waited for it is due. Call "
                   "`build_backend` for `{module}` first ({instruction}), then finish the UI.")
_BACKEND_WAITING = ("Plan v{version} (the revision a backend change of `{module}` waits for) is not approved yet. Do not build "
                    "that change: tell the user in one line to approve plan v{version}, then send continue.")
# The last line of the turn that asked the planner for a revision; the next message (after the approval) resumes from it.
PENDING_BACKEND = re.compile(r"Pending backend build: `([a-z][a-z0-9_]*)` \(plan v(\d+)\): (.+)")
# Save / generation problems that mean the change departs from the approved plan's data model: the planner decides those.
PLAN_SCHEMA_CODES = frozenset({"schema_not_in_plan", "unplanned_table", "unplanned_column", "column_differs_from_plan",
                               "foreign_write"})
_BACKEND_BUILDS: set[str] = set()      # projects with a `build_backend` running in this process (one at a time)


def pending_backend(history: list[dict[str, str]] | None) -> tuple[str, int, str] | None:
    """``(module, plan version, instruction)`` when the last assistant message ended the turn at a plan revision."""
    last = next((h for h in reversed(history or []) if h.get("role") == "assistant"), None)
    m = PENDING_BACKEND.search(str((last or {}).get("content") or ""))
    return (m.group(1), int(m.group(2)), m.group(3).strip()) if m else None


_BRIEF_PROMPT = """\
## Before you build: work like a careful developer (this is a NEW app)
Do not write UI from a guess. You need: **users** (roles, what each may do), **screens**, **journeys** (step by step), the exact project \
**routes** the UI calls with their real response fields (`get_route_signature`; `read_backend` when unsure), **states** (empty, loading, error, live), \
and **acceptance criteria** (testable). If the request plus the project answer all six, call `record_brief` then build. If any is missing or \
ambiguous call `ask_clarifying_questions` (at most 6, each with `why`, `options`, `default`; put what you know in `brief_so_far`) and stop. \
After the answers (or "use your defaults") call `record_brief` (defaults under `assumptions`), then build. `write_files` is refused until the brief is recorded.
"""

_VAGUE_PROMPT = """\
## This request is short: ask domain questions first (round {round} of 2)
A new app from a one-line ask is always a guess. Look at the project for at most 3 calls (`read_backend` on the 2-4 key operations, `get_table_schema`), \
then call `ask_clarifying_questions` ONCE with exactly six questions, one per item, in THIS project's own words (its tables, routes, statuses, roles), each \
with `why`, 2-4 `options` taken from the project, and a `default`:
1. who uses it (`topic: users`): the roles and what each may and may not do;
2. the 2-3 core journeys (`journeys`), step by step, from the first click to the outcome;
3. what must be live (`states`): which data updates while the screen is open, and what is loaded once;
4. approvals (`other`): who approves what an AI or automation changes, and what they must see before approving;
5. what the dashboard must show (`acceptance`): the metrics, time window and thresholds, using fields the routes really return;
6. what must never happen (`other`): safety and data rules.
{round2}`record_brief` is refused until you have asked.
"""

_SCREEN_SYSTEM = """\
You write ONE screen file of a React TSX internal-tool app, by calling `write_files` with exactly that one file. Another engineer writes App.tsx and \
the other screens at the same time, so follow the props contract you are given exactly.
- `export default function <Name>(props: Props)`, with `export interface Props { … }` written from the contract, verbatim. State shared with App \
(selection, open item, active run) comes in through props; local UI state is yours.
- Tailwind + the Studio kit only (`import { … } from '../kit'`, `import { api } from '../api/client'` for a file in views/). Start the file with a `/** … */` \
comment and give each exported component a JSDoc. Layout inside the screen: `h-full min-h-0 flex flex-col`; scroll areas `flex-1 min-h-0 overflow-y-auto`.
- Show only data the listed operations return (their fields are given below); no placeholder values. Handle loading, empty and error states.
- Keep it under ~250 lines. Reply with the tool call only.

""" + _KIT_PROMPT.split("**Never guess")[0] + "Props of the components you need are in the Kit reference of your task: use only those props.\n\n" + _JOBS_PROMPT


_AGENT_SYSTEM_PROMPT = """\
You are a React app builder for internal tools. You create and modify multi-file React TSX apps.

## Environment
Sandboxed iframe with React 18, ReactDOM, Tailwind CSS pre-loaded. The sandbox mounts the default export from `App.tsx`. \
Do NOT create index.html, index.tsx, index.css or bootstrap code. `App.tsx` uses `export default function App()`. \
Standard `import`/`export` between files; Tailwind for styling; no npm packages beyond React; handle loading/error states. \
Use `fetch()` only for an "Attached Endpoint" below (exact url/method/body); project routes go through `api.<operation>` from `api/client`.
You MUST use tools to create/modify files. If the user asks a question (not a change), answer in text only.

""" + _KIT_PROMPT + "\n" + _JOBS_PROMPT + """

## Sandbox constraints
`html, body, #root` are `height:100%; overflow:clip`. Outermost wrapper `h-full flex flex-col` (NEVER `h-screen`/`100vh`); scroll areas \
`flex-1 min-h-0 overflow-y-auto`. No `position:fixed` for layout (only `fixed inset-0 z-50` overlays). Scroll with `ref.current.scrollTop`, never \
`scrollIntoView()`/`window.scrollTo()`. Use `useState` for in-app routing (no pushState). No `EventSource`/`WebSocket` to external hosts: poll with `fetch`. \
`fetch()` is proxied (no CORS issue); no streaming, `AbortController` signal or `FormData` bodies. Downloads: `Blob` + `URL.createObjectURL` + `<a download>`. \
Image URLs must be absolute (`https://…`) or inline SVG. Return cleanup from every `useEffect` that sets timers/listeners.

## Working method (budgets are enforced)
- Context is retrieved, not pasted: the Project Index lists every file's definitions with line numbers. Route response fields are listed in the system prompt (\"Route response fields\"); use `get_route_signature` / `get_table_schema` / \
`get_component` / `search_project_knowledge` for facts, BATCHED (`routes=[…]`, `names=[…]`: one call per kind, not one per item); `read_files` with line ranges (`"views/Library.tsx:120-210"`) for code. \
You get a small read budget; after it only `edit_files`, `write_files` or ONE specific blocking question are allowed. Read, then change something.
- New app: `write_files` in 2-3 calls of a few files each (one file per view/panel, under ~250 lines; `App.tsx` last). For 3+ independent screens call \
`build_screens` (screens are written in parallel from your props contract), then write `App.tsx` that wires them.
- Edit an existing app with `edit_files` patches (small unique `old_string` copied from the index/ranged read); never rewrite a file to change a few lines. \
Do all edits for one request in as few calls as possible, then stop.

## Comments (parsed as project context)
Every file starts with a `/** … */` comment: what the file is, its role, key decisions. Every exported component/function has a `/** … */` JSDoc \
(purpose, props, key behaviour) - architectural, not obvious.

## Response style
When done, ONE short sentence (max 15 words). No lists, no tables, no emojis. Do not end with "want to check in": either finish or ask one specific blocking question.
"""


INLINE_SCHEMA_CHARS = 400


def build_spec_summary(slug: str, specs: list[dict[str, Any]], focus: set[str] | None = None) -> str:
    """System-prompt section describing the project's API (replaces the single attached endpoint)."""
    from .client_codegen import CLIENT_PATH
    lines = [
        "## Project API",
        f"This app belongs to a project with a typed API client at `{CLIENT_PATH}` (generated — never edit it).",
        "Import it from the entry file as `import { api } from './api/client'` (from `src/App.tsx`: `'../api/client'`).",
        "Call operations as `await api.<operation>(params?, body?, query?)`; a route without path params takes the body first: `api.createX({name})`. Do not hand-write fetch() for these routes.",
        "Use response field names exactly as typed; do not invent aliases or hide missing required fields with zero defaults.",
        "If a response schema is missing, use an explicit user-provided contract or ask them to run the route test and Use as schema before binding data.",
        "If the UI needs an endpoint that is not listed, call the `declare_endpoint` tool instead of inventing one.",
        ("Operations without a `response:` line are typed `any`: call `get_route_signature` (or `read_backend` for the handler) "
         "and use the exact fields returned — never guess (`res.items` vs `res.rows`)."),
        "",
    ]
    for s in sorted(specs, key=lambda x: x["name"]):
        if s.get("auth") == "api_key":
            continue
        mark = " ← focus" if focus and s["id"] in focus else ""
        stub = " (stub: returns 501 until implemented)" if (s.get("handler") or {}).get("type") == "stub" else ""
        lines.append(f"- `api.{s['name']}` — {s['method']} {s['path']}{stub}{mark}")
        if s.get("response_schema"):
            import json as _json
            schema_json = _json.dumps(s['response_schema'], separators=(",", ":"))
            if len(schema_json) <= INLINE_SCHEMA_CHARS:
                lines.append(f"  response: `{schema_json}`")
            else:  # the model fetches the exact signature when it needs it (get_route_signature), not on every turn
                lines.append("  response: typed in api/client.ts; call get_route_signature for its fields")
    return "\n".join(lines)


# ── Service Class ─────────────────────────────────────────────────────


def _is_stub_screen(working_set: list[dict], path: str) -> bool:
    """A scaffolded placeholder page the model has not replaced yet."""
    return any(f["path"] == path and "is not built yet." in f["content"] for f in working_set)


class _LLMFailed(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class AppBuilderAIService:
    """Generates and modifies React TSX apps via a tool-based agent loop."""

    # Called with the files an edit tool wrote (``{path, content}``; ``content`` None when deleted), as it writes them:
    # the build coordinator streams them live (LIVE-FILES §2).
    on_files: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None

    def __init__(
        self,
        api_test_repo: ApiTestRepository,
        app_service: AppService,
    ) -> None:
        self._api_test_repo = api_test_repo
        self._app_service = app_service

    def _count_call(self) -> None:
        self._model_calls = getattr(self, "_model_calls", 0) + 1

    def _stats(self) -> dict[str, Any]:
        import time
        started = getattr(self, "_started", None)
        return {"wall_s": round(time.perf_counter() - started, 2) if started is not None else None,
                "model_calls": getattr(self, "_model_calls", 0),
                "scaffolded": getattr(self, "_scaffold", None) is not None}

    async def _report_files(self, before: dict[str, str], working_set: list[dict]) -> None:
        after = {f["path"]: f["content"] for f in working_set}
        files = [{"path": p, "content": c} for p, c in after.items() if before.get(p) != c]
        files += [{"path": p, "content": None} for p in before if p not in after]
        if files and self.on_files:
            try:
                await self.on_files(files)
            except Exception:  # noqa: BLE001 - a live view never breaks the turn
                logger.warning("app builder: on_files failed", exc_info=True)

    # ── Public ────────────────────────────────────────────────────────

    async def check_base(self, request: AppBuilderChatRequest) -> None:
        """Refuse a turn whose working copy is stale (raises StaleBaseError → 409) before any streaming starts.

        The client flushes its pending visual saves and sends the resulting head as ``base_version_id``; if another save
        landed in between, the AI would edit an old copy and its reply would overwrite the newer edits.
        """
        if not request.app_id or request.base_version_id is None:
            return
        from ..db.models import AppModel
        from .app_service import check_base

        row = await self._app_service._session.get(AppModel, request.app_id)
        if row is not None:
            check_base(row, request.base_version_id)

    async def stream_chat(
        self, request: AppBuilderChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream app builder response as SSE event dicts.

        Review C5: the stream always ends with ``done``; any failure first yields a
        ``message`` event ``{type:"error", code:"llm_failed"|"internal", message}``."""
        try:
            async for event in self._stream_chat(request):
                yield event
        except _LLMFailed as e:
            logger.warning("app builder: LLM call failed: %s", e.cause, exc_info=e.cause)
            yield _sse("message", {"type": "error", "code": "llm_failed",
                                   "message": f"The AI provider failed ({type(e.cause).__name__}): {str(e.cause)[:300]}"})
        except Exception as e:
            logger.exception("app builder: stream failed")
            yield _sse("message", {"type": "error", "code": "internal",
                                   "message": f"The assistant stopped unexpectedly ({type(e).__name__})."})
        yield _sse("done", {"type": "done"})

    async def _stream_chat(
        self, request: AppBuilderChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        import time
        self._started = time.perf_counter()
        self._model_calls = 0
        self._stats_sent = False
        self._scaffold = None
        # 1. Resolve current files (multi-file aware)
        working_set, resolve_warning = await self._resolve_current_files(request)
        if resolve_warning:
            yield _sse("message", {"type": "text", "content": resolve_warning})

        # 2. Load attached API test executions for context
        api_executions: list[ApiTestExecutionModel] = []
        if request.api_execution_ids:
            api_executions = await self._api_test_repo.get_many(request.api_execution_ids)
            # Auto-add to the app's allow-list so the published page can replay
            # them. Only when an app_id is set — fresh apps with no row yet
            # will pick this up on the next chat after they save.
            if request.app_id:
                try:
                    await self._app_service.grant_api_executions(
                        request.app_id, [e.id for e in api_executions]
                    )
                except Exception:
                    logger.warning("grant_api_executions failed", exc_info=True)

        # 2b. Project API context (phase 6): spec summary, typed client in the working set,
        #     declare_endpoint tool for endpoints the UI needs.
        self._project = None
        self._spec_summary = None
        self._declared: list[dict[str, Any]] = []
        try:
            await self._load_project_api(request, working_set)
        except Exception:
            logger.warning("app builder: project API context failed", exc_info=True)

        # 2b'. Project knowledge: make sure routes/tables are indexed, record the answers to the last clarify round, and
        #      fetch what earlier turns decided (the raw history only keeps 6 entries).
        self._request = request
        self._memory = ""
        self._digest = ""
        if getattr(self, "_project", None) is not None:
            await self._ensure_knowledge()
            await self._persist_clarify_answers(request)
            self._memory = await self._memory_preamble(request.message)
            self._digest = await self._route_digest(set(request.route_ids or []))

        # 2c. Studio UI kit: always the current copy, before the snapshot so a question-only turn emits nothing.
        stale_kit = {f["path"]: f["content"] for f in working_set if is_kit_path(f["path"])}
        inject_kit(working_set)
        # An older app carries a stale kit copy (no theme tokens, old cx): refreshing it counts as a change so the studio saves a
        # new version. An app without kit files, and a question-only turn on a current kit, stay untouched.
        kit_refreshed = bool(stale_kit) and any(stale_kit.get(k["path"]) != k["content"] for k in kit_files())
        self._notes = await self._turn_notes(request, working_set)

        # 3. Snapshot initial state to detect if agent modified anything
        initial_snapshot = {f["path"]: f["content"] for f in working_set}

        # 4. Run agent loop (an app built in one pass is edited in one pass: oneshot_builder.edit_app)
        if await self._is_oneshot_app(request):
            events = self._oneshot_edit(request, working_set)
        else:
            events = self._run_agent_loop(working_set, api_executions, request.message, request.conversation_history)
        async for event in events:
            yield event

        # 5. Emit final files only if the working set was modified
        current_snapshot = {f["path"]: f["content"] for f in working_set}
        files_changed = current_snapshot != initial_snapshot or kit_refreshed

        if working_set and files_changed:
            errors = await _generated_syntax_errors(working_set)
            if errors:
                raise _LLMFailed(RuntimeError("Generated code did not pass syntax validation: " + errors))
            _ENTRY_PATHS = {"App.tsx", "src/App.tsx", "app.tsx", "src/app.tsx"}
            entry = next(
                (f for f in working_set if f["path"] in _ENTRY_PATHS),
                working_set[0] if working_set else None,
            )
            await self._index_built_app(request, working_set)
            attached = await self._attach_routes(request, working_set)
            if attached:
                yield _sse("message", {"type": "routes_attached", "route_ids": attached})
            # Strip parsed_index before sending — frontend doesn't need it
            client_files = [{"path": f["path"], "content": f["content"]} for f in working_set]
            yield _sse("message", {
                "type": "code",
                "files": client_files,
                "source": entry["content"] if entry else "",
                "stats": self._stats(),
            })
        elif not working_set and not files_changed:
            # No files at all and nothing was created — genuine failure
            yield _sse("message", {
                "type": "text",
                "content": "I wasn't able to create anything this time. Could you describe what you'd like in more detail?",
            })
        # else: question-only flow — text already emitted, no code event needed

        if not getattr(self, "_stats_sent", False):
            yield _sse("message", {"type": "stats", **self._stats()})

        # (review C6: stubs are created inside each declare_endpoint call, see _declare_endpoint_now)

    async def _turn_notes(self, request: AppBuilderChatRequest, working_set: list[dict]) -> str:
        """Extra context for this turn, "" when there is none: the hand edits the user made in the visual editor since the last
        AI turn (keep them) and, with the editable-rules flag on, the editability lint's open warnings on the app's own files."""
        parts: list[str] = []
        edits = [str(e).strip()[:200] for e in (request.visual_edits or []) if str(e).strip()][:25]
        if edits:
            parts.append("## Hand edits\nThe user edited these by hand; keep them unless asked to change them.\n"
                         + "\n".join(f"- {e}" for e in edits))
        sel = request.selection
        if sel is not None and sel.file:
            parts.append("## Selected element\nThe user selected this element in the preview; apply the request to it: "
                         f"{sel.file}:{sel.line} {sel.name}\n```tsx\n{sel.snippet[:1500]}\n```")
        from . import oneshot_builder as ob
        if ob.editable_rules_enabled():
            try:
                from . import editability_lint as el
                mine = [f for f in working_set if not is_kit_path(f["path"]) and f["path"] != "api/client.ts"
                        and f["path"].endswith((".tsx", ".ts"))]
                vs = el.mechanical(await el.lint_files_async(mine))[:8] if mine else []
            except Exception:  # noqa: BLE001 - a lint never breaks a turn
                vs = []
            if vs:
                parts.append("## Editability warnings\nThese spots in the app are not in the editable shape (see the editable-shape rules). "
                             "Rewrite one only when you are changing that code anyway; write everything new in the editable shape.\n"
                             + "\n".join(f"- {v['file']}:{v['line']} {v['message']}. Fix: {v['fix']}" for v in vs))
        return "\n\n".join(parts)

    async def _is_oneshot_app(self, request: AppBuilderChatRequest) -> bool:
        """True for an app the one-shot builder wrote (``definition.studio.oneshot``) while the one-shot mode is on."""
        from . import oneshot_builder as ob

        if request.plan_app or ob.builder_mode() != "oneshot" or not request.app_id or getattr(self, "_project", None) is None:
            return False
        try:
            from ..db.models import AppModel
            app = await self._app_service._session.get(AppModel, request.app_id)
        except Exception:  # noqa: BLE001 - when in doubt the agent loop handles it
            return False
        return bool(app is not None and ((app.draft_definition or {}).get("studio") or {}).get("oneshot"))

    async def _oneshot_edit(self, request: AppBuilderChatRequest, working_set: list[dict]) -> AsyncGenerator[dict[str, Any], None]:
        """One edit through ``oneshot_builder.edit_app``: ONE model call sees the tables, module source(s) and app files and
        returns the changed parts (data changes included: new columns are migrated and the plan gets a revision, no dead end),
        then the platform loads and checks them (one fix pass at most). The app files land in ``working_set``; the caller saves
        them as a version like any other turn."""
        from . import oneshot_builder as ob

        yield _sse("message", {"type": "phase", "phase": "editing", "message": "Editing the app in one pass"})
        pid = self._project.id
        try:
            current = await ob.current_bundle(pid, request.app_id)
            mine = [{"path": f["path"], "content": f["content"]} for f in working_set if not is_kit_path(f["path"]) and f["path"] != "api/client.ts"]
            if mine:
                current["app"]["files"] = mine
            res = await ob.edit_app(pid, request.app_id, request.message, save_app=False, bundle=current,
                                    notes=getattr(self, "_notes", ""))
        except ob.WriteError as e:
            raise _LLMFailed(e) from e
        self._model_calls = getattr(self, "_model_calls", 0) + int(res["timing"]["model_calls"])
        by_path = {f["path"]: f for f in working_set}
        for f in res["bundle"]["app"]["files"]:
            if f["path"] in by_path:
                by_path[f["path"]].update(content=f["content"], parsed_index=None)
            else:
                working_set.append({"path": f["path"], "content": f["content"], "parsed_index": None})
        gone = set(res["changes"]["deleted"])
        working_set[:] = [f for f in working_set if f["path"] not in gone]
        c = res["changes"]
        parts = [f"{n} {w}" for n, w in ((len(c["tables"]), "table(s)"), (len(c["modules"]), "module(s)"), (len(c["files"]), "file(s)")) if n]
        text = res["message"] or ("Changed " + ", ".join(parts) + "." if parts else "Nothing needed to change.")
        if res.get("plan_revision") and res["plan_revision"].get("version"):
            text += f" Plan v{res['plan_revision']['version']} records the data change."
        if res["errors"]:
            text += " Still open: " + "; ".join(f"[{e['where']}] {e['message']}" for e in res["errors"][:3])[:600]
        yield _sse("message", {"type": "text", "content": text})

    async def _load_project_api(self, request: AppBuilderChatRequest, working_set: list[dict]) -> None:
        from ..db.models import AppModel, ProjectModel
        from .client_codegen import CLIENT_PATH

        session = self._app_service._session
        project = None
        app = await session.get(AppModel, request.app_id) if request.app_id else None
        if request.project_id:
            from .project_service import resolve_project
            project = await resolve_project(session, request.project_id)
        elif app is not None:
            project = await session.get(ProjectModel, app.project_id)
        if project is None:
            return
        self._project = project
        from .api_route_service import ApiRouteService, route_spec
        specs = [route_spec(r) for r in await ApiRouteService(session).rows(project.id)]
        if not specs:
            return
        self._spec_summary = build_spec_summary(project.slug, specs, focus=set(request.route_ids or []))
        from ..routes.api_routes import client_source
        src, _ = await client_source(session, project, project.default_environment)
        working_set[:] = [f for f in working_set if f["path"] != CLIENT_PATH]
        working_set.append({"path": CLIENT_PATH, "content": src, "parsed_index": None})

    # ── Project knowledge (WP-1 store): index, answers, decisions, memory ─────────

    def _project_id(self) -> str | None:
        project = getattr(self, "_project", None)
        return getattr(project, "id", None)

    async def _knowledge_call(self, fn, *args, **kwargs):
        """Run one knowledge-store call; the store is a helper, so a failure is logged and never breaks the builder."""
        session = self._app_service._session
        try:
            return await fn(session, *args, **kwargs)
        except Exception:
            logger.warning("app builder: knowledge store call failed", exc_info=True)
            try:
                await session.rollback()
            except Exception:
                logger.warning("app builder: rollback after knowledge failure failed", exc_info=True)
            return None

    async def _ensure_knowledge(self) -> None:
        """Index the project's modules/tables once if any python route has no signature row yet (idempotent AST pass)."""
        pid = self._project_id()
        if not pid:
            return
        from . import platform_index as pi
        from . import project_knowledge as pk
        from .api_route_service import ApiRouteService

        async def check(session):
            rows = [r for r in await ApiRouteService(session).rows(pid) if r.handler_type == "python"]
            for r in rows:
                if await pi.get_route_signature(session, pid, r.method, r.path) is None:
                    return await pk.reindex_project(session, pid)
            return None

        await self._knowledge_call(check)

    async def _remember(self, kind: str, key: str, title: str, body: dict[str, Any], text: str | None = None,
                        source: str = "design", status: str = "approved") -> None:
        pid = self._project_id()
        if not pid:
            return
        from . import project_knowledge as pk
        await self._knowledge_call(pk.upsert, pid, kind, key, title=title, body=body, text=text, status=status, source=source,
                                   source_ref=f"app:{getattr(getattr(self, '_request', None), 'app_id', None) or 'new'}")

    async def _persist_clarify_answers(self, request: AppBuilderChatRequest) -> None:
        """The user's message answers the questions in the last assistant clarify turn: record both as a `clarify_answer` row."""
        hist = request.conversation_history or []
        if not hist or hist[-1].get("role") != "assistant" or _CLARIFY_MARKER not in (hist[-1].get("content") or ""):
            return
        rnd = clarify_rounds(hist)
        original = next((h.get("content", "") for h in hist if h.get("role") == "user"), "")
        scope = request.app_id or "new"
        await self._remember(
            "clarify_answer", f"{scope}:round{rnd}", f"Round {rnd} answers for the app request",
            {"request": original[:1500], "questions": hist[-1]["content"][:3000], "answers": request.message[:3000], "round": rnd},
            text=f"clarify round {rnd} request: {original[:300]} questions: {hist[-1]['content'][:1200]} answers: {request.message[:1200]}")

    async def _memory_preamble(self, query: str) -> str:
        """Decisions/answers/requirements recorded for this project that match the request, within a small token budget."""
        pid = self._project_id()
        if not pid or not query.strip():
            return ""
        from . import project_knowledge as pk
        res = await self._knowledge_call(pk.retrieve, pid, query, kinds=["decision", "clarify_answer", "requirement", "spec", "failure"],
                                         token_budget=450)
        if not res or not res.get("items"):
            return ""
        return "## Project memory (recorded earlier; follow it unless the user says otherwise)\n" + res["text"]

    async def _index_built_app(self, request: AppBuilderChatRequest, working_set: list[dict]) -> None:
        """Write the app's screens and kit usage to the knowledge store (`index_app`) so later turns look them up."""
        pid = self._project_id()
        if not pid or not request.app_id:
            return
        from ..db.models import AppModel
        from . import project_knowledge as pk
        session = self._app_service._session
        try:
            app = await session.get(AppModel, request.app_id)
        except Exception:  # noqa: BLE001
            app = None
        if app is None:
            return
        files = [{"path": f["path"], "content": f["content"]} for f in working_set if not is_kit_path(f["path"])]
        prefix = f"{app.name or app.slug or 'app'} [{app.id[-6:]}]: "
        await self._knowledge_call(pk.index_app, pid, files, app_ref=app.id, key_prefix=prefix)

    # ── Platform lookups, data contract ──────────────────────────────────────

    async def _lookup_now(self, name: str, args: dict) -> str:
        """Run a platform lookup; the (possibly batched) text is paginated with ``args['offset']``, never cut silently."""
        text = await self._lookup_text(name, args)
        return paginate(text, args.get("offset") or 0, LOOKUP_PAGE_CHARS)

    async def _lookup_text(self, name: str, args: dict) -> str:
        from . import platform_index as pi
        pid = self._project_id() or ""
        if name == "get_component" and isinstance(args.get("names"), list):
            comps = [pi.get_component(str(n)) or {"name": str(n), "error": "unknown"} for n in args["names"][:8]]
            return format_lookup("search_components", [c for c in comps if not c.get("error")], cap=None) + "".join(
                f"\nError: no kit component named {c['name']!r}" for c in comps if c.get("error"))
        if name in ("search_components", "get_component"):
            data = pi.search_components(args.get("query", "")) if name == "search_components" else (
                pi.get_component(args.get("name", "")) or {"error": f"no kit component named {args.get('name')!r}; use search_components"})
            return format_lookup(name, data, cap=None)
        if not pid:
            return "Error: no project context."
        session = self._app_service._session
        # Built with loops, not `join(... await ...)`: an await inside a generator expression makes it an async
        # generator, and str.join on one raises "can only join an iterable".
        if name == "get_route_signature" and isinstance(args.get("routes"), list):
            out = []
            for r in args["routes"][:12]:
                if isinstance(r, dict):
                    out.append(format_lookup(name, await pi.dispatch_tool(session, pid, name, {"method": r.get("method"), "path": r.get("path")}), cap=None))
            return "\n".join(out)
        if name == "get_table_schema" and isinstance(args.get("names"), list):
            out = []
            for n in args["names"][:8]:
                out.append(format_lookup(name, await pi.dispatch_tool(session, pid, name, {"name": n}), cap=None))
            return "\n".join(out)
        data = await pi.dispatch_tool(session, pid, name, args)
        return format_lookup(name, data, cap=None)

    async def _op_sigs(self) -> dict[str, OpSig]:
        """operation name -> the fields its route is known to return (knowledge-store signature, else the response schema)."""
        pid = self._project_id()
        if not pid:
            return {}
        from . import platform_index as pi
        from .api_route_service import ApiRouteService
        from .app_validate import SYSTEM_FIELDS, OpSig, schema_fields

        # Routes written earlier in this turn have no signature row until the project is indexed; without one, a planned
        # response schema (often the row shape, not the {rows, total} envelope) is not proof of what the handler returns.
        await self._ensure_knowledge()
        session = self._app_service._session
        sigs: dict[str, OpSig] = {}
        for r in await ApiRouteService(session).rows(pid):
            fields = set(schema_fields(r.response_schema)) if r.response_schema else set()
            known = bool(fields) and r.handler_type != "python"
            elements_known = bool(fields)  # a response schema lists nested keys; bare handler keys do not
            row = None
            try:
                row = await pi.get_route_signature(session, pid, r.method, r.path)
            except Exception:
                logger.warning("app builder: route signature lookup failed", exc_info=True)
            if row and row.get("response"):
                fields |= set(row["response"])
                known = True
                for t in row.get("tables") or []:  # rows spread from a table carry its columns
                    sch = await pi.get_table_schema(session, pid, t)
                    fields |= {c.get("name") for c in (sch or {}).get("columns", []) if isinstance(c, dict) and c.get("name")}
                    elements_known = True
                fields |= SYSTEM_FIELDS
            sigs[r.name] = OpSig(fields=fields, known=known, elements_known=elements_known, path_params=bool(re.search(r"\{[^}/]+\}|:[A-Za-z_]", r.path or "")))
        return sigs

    async def _remember_brief(self, brief: dict[str, Any]) -> None:
        """A recorded brief becomes project knowledge: a spec row, one decision per assumption, one requirement per criterion."""
        import hashlib
        scope = getattr(getattr(self, "_request", None), "app_id", None) or "new"
        def h(t: str) -> str:
            return hashlib.sha1(t.encode()).hexdigest()[:10]
        await self._remember("spec", f"{scope}:brief", "App brief", brief, text="app brief users " + brief["users"][:300] + " screens "
                             + ", ".join(sc["name"] for sc in brief["screens"]) + " journeys " + " ; ".join(brief["journeys"])[:600])
        for a in brief.get("assumptions", [])[:8]:
            await self._remember("decision", f"{scope}:assume:{h(a)}", a[:120], {"decision": a, "origin": "assumption in app brief"})
        for c in brief.get("acceptance_criteria", [])[:10]:
            await self._remember("requirement", f"{scope}:ac:{h(c)}", c[:120], {"criterion": c})

    def _brief_required_pending(self) -> bool:
        return bool(getattr(self, "_brief_required", False) and not getattr(self, "_brief_recorded", False))

    async def _typecheck_feedback(self, working_set: list[dict], guard: StallGuard) -> str:
        """tsc right after an edit, appended to the tool result: the exact errors arrive in the turn that caused them (what
        an editor/lint loop gives a human), not at the end. Missing-module errors are ignored (files may still be coming)."""
        try:
            errs = await typecheck_errors(working_set)
        except Exception:
            logger.warning("app builder: typecheck feedback failed", exc_info=True)
            return ""
        if not errs:
            return ""
        lines = [ln for ln in errs.splitlines() if "TS2307" not in ln]
        if not any(": error TS" in ln for ln in lines):
            return ""
        guard.repair()
        return "\n\nType-check after this change found errors; fix them with edit_files before anything else:\n" + "\n".join(lines[:14])

    async def _attach_routes(self, request: AppBuilderChatRequest, working_set: list[dict]) -> list[str]:
        """Attach every project route the app now calls (and any it declared) to the app: ``definition.studio.route_ids``,
        the same list the app settings sheet edits. Returns the app's full attached list (the studio merges it into its
        local copy so a later settings save does not overwrite it); [] when there is no project."""
        project = getattr(self, "_project", None)
        if project is None:
            return []
        try:
            from .api_route_service import ApiRouteService
            from .app_validate import called_operations
            session = self._app_service._session
            files = [{"path": f["path"], "content": f["content"]} for f in working_set]
            ops = called_operations(files) | {str(d.get("name")) for d in getattr(self, "_declared", []) if d.get("name")}
            wanted = [r.id for r in await ApiRouteService(session).rows(project.id) if r.name in ops]
            current = [str(x) for x in (request.route_ids or [])]
            app = None
            if request.app_id:
                from ..db.models import AppModel
                app = await session.get(AppModel, request.app_id)
                studio = ((app.draft_definition or {}).get("studio") if app is not None else None) or {}
                current = [str(x) for x in (studio.get("route_ids") or [])] or current
            merged = current + [i for i in wanted if i not in current]
            if app is not None and merged != [str(x) for x in (((app.draft_definition or {}).get("studio") or {}).get("route_ids") or [])]:
                from sqlalchemy.orm.attributes import flag_modified
                definition = dict(app.draft_definition or {})
                definition["studio"] = {**(definition.get("studio") or {}), "route_ids": merged}
                app.draft_definition = definition
                flag_modified(app, "draft_definition")
                await session.commit()
            return merged
        except Exception:
            logger.warning("app builder: route attachment failed", exc_info=True)
            return []

    def _delivery_gaps(self, working_set: list[dict], touched: list[dict], report: bool = False) -> list[str]:
        """What the plan promised that the files do not deliver, plus calls to routes the project does not have.
        The continuation nudge uses the hard gaps (missing screens, calls to missing routes); the final partial report
        (``report=True``) also lists planned routes nothing calls."""
        from .app_validate import screen_gaps, unknown_route_calls, unused_planned_routes
        out: list[str] = []
        brief = getattr(self, "_brief_data", None)
        files = [{"path": f["path"], "content": f["content"]} for f in working_set]
        if brief:
            out += [f"screen `{n}` from the plan has no file/component" for n in screen_gaps(brief.get("screens") or [], files)]
            if report:
                out += [f"planned route `{r}` is never called" for r in unused_planned_routes(brief.get("routes") or [], files)]
        touched_paths = {f["path"] for f in touched}
        by_path = [f for f in files if f["path"] in touched_paths or f["path"].endswith("api/client.ts")]
        out += unknown_route_calls(by_path)
        return out

    async def _integrity_findings(self, files: list[dict]) -> list[str]:
        """Contract check (fields read vs route signatures) + invented-literal check on the files the model touched."""
        from .app_validate import contract_issues, literal_issues
        issues: list[str] = []
        try:
            if self._project_id():
                issues += contract_issues(files, await self._op_sigs())
        except Exception:
            logger.warning("app builder: contract check failed", exc_info=True)
        issues += literal_issues(files)
        fresh = [i for i in issues if i not in getattr(self, "_acked_findings", set())]
        self._acked_findings = getattr(self, "_acked_findings", set()) | set(fresh)
        return fresh

    async def _route_digest(self, focus: set[str] | None = None, cap: int = ROUTE_DIGEST_CHARS) -> str:
        """One line per browser-callable route with the fields its handler returns / reads (from the knowledge store), so the model
        does not spend a turn per `get_route_signature`. Bounded; routes that do not fit are left to the lookup tool."""
        pid = self._project_id()
        if not pid:
            return ""
        from . import platform_index as pi
        from . import project_knowledge as pk
        from .api_route_service import ApiRouteService
        session = self._app_service._session
        try:
            routes = [r for r in await ApiRouteService(session).rows(pid) if r.auth != "api_key"]
            kn = {f"{b['body'].get('method')} {pi._shape(b['body'].get('path', ''))}": b["body"]
                  for b in await pk.list_kind(session, pid, "route")}
        except Exception:
            logger.warning("app builder: route digest failed", exc_info=True)
            return ""
        lines: list[str] = []
        for r in sorted(routes, key=lambda x: (x.id not in (focus or set()), x.name)):
            b = kn.get(f"{r.method} {pi._shape(r.path)}") or {}
            resp = ", ".join((b.get("response") or [])[:12])
            body = ", ".join((b.get("body") or [])[:8])
            shape = b.get("response_shape") or ""
            mark = (" [focus]" if focus and r.id in focus else "") + (" [stub: 501]" if getattr(r, "handler_type", "") == "stub" else "")
            lines.append(f"- api.{r.name} {r.method} {r.path}{mark} → " + (f"{shape} {{{resp}}}" if resp else "fields unknown (read_backend)")
                         + (f"; sends {{{body}}}" if body else ""))
        out, used = [], 0
        for ln in lines:
            if used + len(ln) > cap:
                out.append(f"… {len(lines) - len(out)} more routes: call get_route_signature(routes=[…])")
                break
            out.append(ln)
            used += len(ln) + 1
        return ("## Route response fields (authoritative; render only these)\n" + "\n".join(out)) if out else ""

    async def _op_signature_text(self, ops: list[str]) -> str:
        """Fields each named operation returns, for a screen sub-call (cheap: from the knowledge-store signature or schema)."""
        sigs = await self._op_sigs()
        rows = {}
        try:
            from .api_route_service import ApiRouteService
            rows = {r.name: r for r in await ApiRouteService(self._app_service._session).rows(self._project_id())}
        except Exception:
            logger.warning("app builder: route listing failed", exc_info=True)
        lines = []
        for op in ops[:10]:
            name = str(op).removeprefix("api.")
            r, sg = rows.get(name), sigs.get(name)
            if r is None:
                lines.append(f"- api.{name}: no such operation")
            else:
                fields = ", ".join(sorted((sg.fields if sg else set()) - {"created_at", "updated_at"})) if sg and sg.known else "unknown (typed any in api/client.ts)"
                lines.append(f"- api.{name} — {r.method} {r.path}; returns fields: {fields}")
        return "\n".join(lines)

    async def _build_screens_now(self, args: dict, working_set: list[dict]) -> str:
        """Write independent screens concurrently (<= MAX_PARALLEL_SCREENS at a time), each from its own focused model call."""
        import time

        from .platform_index import get_component, kit_components, search_components
        screens = [x for x in _as_list(args.get("screens")) if isinstance(x, dict)][:MAX_SCREENS_PER_CALL]
        if not screens:
            return "Error: `screens` must list at least one {path, purpose, props, operations}."
        entry = {"App.tsx", "src/App.tsx"}
        clean: list[dict[str, Any]] = []
        errors: list[str] = []
        for sc in screens:
            path = str(sc.get("path") or "").strip()
            if not _SAFE_PATH_RE.match(path) or not path.endswith(".tsx") or path in entry or is_kit_path(path):
                errors.append(f"Error: {path or '(no path)'} is not a valid screen path (use views/Name.tsx; App.tsx is yours).")
            elif path in {c["path"] for c in clean}:
                errors.append(f"Error: {path} listed twice.")
            else:
                clean.append({**sc, "path": path})
        if not clean:
            return "\n".join(errors)
        brief = next((f["content"] for f in working_set if f["path"] == BRIEF_PATH), "")
        contracts = "\n".join(f"- {c['path']}: props {{ {c.get('props') or ''} }}" for c in clean)
        kit_names = {c["name"] for c in kit_components()}
        sem = asyncio.Semaphore(MAX_PARALLEL_SCREENS)
        t0 = time.perf_counter()

        async def one(sc: dict[str, Any]) -> tuple[dict[str, Any], str | None, str]:
            async with sem:
                text = f"{sc.get('purpose') or ''} {sc.get('props') or ''}"
                names = [n for n in kit_names if re.search(r"\b" + re.escape(n) + r"\b", text)]
                names += [c["name"] for c in search_components(text, 3)] + ["Card", "Button"]
                ref = "\n\n".join(format_lookup("get_component", get_component(n), None) for n in dict.fromkeys(names) if get_component(n))
                if len(ref) > 6500:
                    ref = ref[:6500] + f"\n[kit reference cut at 6500 of {len(ref)} chars; follow the Props contract and the components named above]"
                ops = [str(o) for o in _as_list(sc.get("operations"))]
                user = (f"## Your screen: {sc['path']}\n{sc.get('purpose')}\n\nProps contract (write `export interface Props` with exactly these members): "
                        f"{{ {sc.get('props') or ''} }}\n\n## Operations it may call\n{await self._op_signature_text(ops) if ops else '(none)'}\n\n"
                        f"## All screens being written now (for naming consistency)\n{contracts}\n\n## Brief\n{brief[:3500]}\n\n## Kit reference\n{ref}")
                msgs = [{"role": "system", "content": _SCREEN_SYSTEM}, {"role": "user", "content": user}]
                last = ""
                for attempt in range(2):
                    try:
                        self._count_call()
                        resp = await call_llm(model=_HEAVY_MODEL, messages=msgs, tools=[t for t in self._base_agent_tools() if t["name"] == "write_files"],
                                              temperature=0.3, tool_choice="required",
                                              max_tokens=32_000 if _HEAVY_MODEL.startswith("gemini-") else 16_000,
                                              **({"thinking_level": "LOW"} if _HEAVY_MODEL.startswith("gemini-3") else {}))
                    except Exception as e:  # noqa: BLE001 - one screen failing must not lose the others
                        return sc, None, f"{type(e).__name__}: {str(e)[:160]}"
                    if resp.output_truncated:
                        last = "output limit"
                        msgs = msgs + [{"role": "user", "content": "Your output was cut off. Write the same screen again, shorter: under 180 lines, fewer helper components."}]
                        continue
                    for tc in resp.tool_calls:
                        if tc.name != "write_files":
                            continue
                        for f in _as_list(tc.args.get("files")):
                            if isinstance(f, dict) and f.get("content") and f.get("path") in (sc["path"], "src/" + sc["path"]):
                                return sc, str(f["content"]), ""
                    last = "no file returned"
                return sc, None, last

        results = await asyncio.gather(*(one(sc) for sc in clean))
        lines = list(errors)
        for sc, content, err in results:
            if content is None:
                lines.append(f"Error: {sc['path']} was not written ({err}). Write it yourself with write_files.")
                continue
            self._tool_write_files({"files": [{"path": sc["path"], "content": content}]}, working_set)
            name = re.search(r"export default function (\w+)", content)
            lines.append(f"Built {sc['path']} ({content.count(chr(10)) + 1} lines) — default export {name.group(1) if name else '?'}, props: {{ {sc.get('props') or ''} }}")
        lines.append(f"{sum(1 for r in results if r[1])} of {len(results)} screens written concurrently in {time.perf_counter() - t0:.0f}s. "
                     "Now write App.tsx: hold the shared state and pass each screen exactly its props.")
        return "\n".join(lines)

    async def _read_backend_now(self, args: dict) -> str:
        """Read-only: the handler source behind project operations, so response/body shapes come from the backend
        instead of being guessed (the typed client is `any` for routes without a response schema)."""
        from ..db.models import CodeModuleModel
        from .api_route_service import ApiRouteService

        project = getattr(self, "_project", None)
        if project is None:
            return "Error: no project context."
        wanted = [str(x).removeprefix("api.") for x in _as_list(args.get("operations")) if str(x).strip()]
        session = self._app_service._session
        rows = await ApiRouteService(session).rows(project.id)
        by_name = {r.name: r for r in rows}
        if not wanted:
            return "Error: pass `operations`, e.g. [\"getCases\"]. Available: " + ", ".join(sorted(by_name))
        modules: dict[str, str] = {}
        tables: dict[str, None] = {}
        out: list[str] = []
        for name in wanted[:12]:
            r = by_name.get(name)
            if r is None:
                out.append(f"## api.{name}\nError: no such operation. Available: {', '.join(sorted(by_name))}")
                continue
            head = f"## api.{name} — {r.method} {r.path}"
            if r.response_schema:
                head += "\nresponse_schema: " + json.dumps(r.response_schema, separators=(",", ":"))
            if r.request_schema:
                head += "\nrequest_schema: " + json.dumps(r.request_schema, separators=(",", ":"))
            if r.handler_type == "stub":
                out.append(head + "\nhandler: stub (returns 501 until implemented)")
                continue
            if r.handler_type != "python" or not r.handler_module_id:
                out.append(head + f"\nhandler: {r.handler_type} (response is that handler's output)")
                continue
            if r.handler_module_id not in modules:
                mod = await session.get(CodeModuleModel, r.handler_module_id)
                modules[r.handler_module_id] = mod.source if mod else ""
            src = handler_source(modules[r.handler_module_id], r.handler_function or "")
            self._read_ops.add(name)
            out.append(head + f"\nhandler (python, read-only — `req.params`, `req.query`, `req.json()` are the inputs; "
                              f"the return value is the JSON body):\n```python\n{src}\n```")
            for t in tables_read_by(src):
                tables.setdefault(t, None)
        samples = await self._table_samples(project.id, list(tables)[:8])
        if samples:
            out.append("## Stored rows (real data, shortened) — the actual shape of fields these handlers return. Fields "
                       "copied from elsewhere (job output, request bodies) have no schema; render them from THIS shape "
                       "(e.g. a list of objects, not strings), and every row also has `id`, `created_at`, `updated_at`.\n"
                       + samples)
        return paginate("\n\n".join(out), args.get("offset") or 0, READ_BACKEND_PAGE_CHARS)

    async def _table_samples(self, project_id: str, names: list[str]) -> str:
        """One stored row per table (the most filled-in of the latest 20), read-only. Empty when nothing is stored."""
        from sqlalchemy import select

        from ..db.models import DataTableModel, DataTableRowModel

        session = self._app_service._session
        parts: list[str] = []
        for name in names:
            t = (await session.execute(select(DataTableModel).where(
                DataTableModel.project_id == project_id, DataTableModel.name == name))).scalars().first()
            if t is None:
                continue
            rows = (await session.execute(select(DataTableRowModel).where(DataTableRowModel.table_id == t.id)
                                          .order_by(DataTableRowModel.id.desc()).limit(20))).scalars().all()
            if not rows:
                parts.append(f"`{name}`: (no rows yet)")
                continue
            best = max(rows, key=lambda r: sum(v not in (None, "", [], {}) for v in (r.data or {}).values()))
            row = {"id": best.id, **sample_value(best.data or {})}
            parts.append(f"`{name}`: " + json.dumps(row, default=str, ensure_ascii=False)[:2500])
        return "\n".join(parts)

    async def _record_brief_now(self, args: dict, working_set: list[dict], *, from_plan: bool = False
                                ) -> tuple[str, dict[str, Any] | None]:
        """Validate the brief (routes must be real project routes) and save it as BRIEF.md in the working set.
        ``from_plan``: the brief is the approved plan's, so a route with no response schema is not a reason to ask the
        user; its operation is listed in ``self._plan_read_first`` for the model to read before relying on field names."""
        from .api_route_service import ApiRouteService, path_shape

        problems: list[str] = []
        users = _text(args.get("users"))
        if not users:
            problems.append("`users`: who uses this app and what they are trying to get done")
        screens = []
        for s in _as_list(args.get("screens")):
            if isinstance(s, str) and s.strip():
                screens.append({"name": s.strip(), "purpose": ""})
            elif isinstance(s, dict) and str(s.get("name") or "").strip():
                screens.append({"name": str(s["name"]).strip(), "purpose": _text(s.get("purpose"))})
        if not screens:
            problems.append("`screens`: at least one screen/pane with its purpose")
        journeys = [_text(j) for j in _as_list(args.get("journeys")) if _text(j)]
        if not journeys:
            problems.append("`journeys`: at least one step-by-step user journey")
        raw_states = args.get("states") if isinstance(args.get("states"), dict) else {}
        states = {str(k).lower(): _text(v) for k, v in raw_states.items() if _text(v)}
        missing_states = [s for s in _REQUIRED_STATES if s not in states]
        if missing_states:
            problems.append("`states`: describe " + ", ".join(missing_states) + " (and `live` when data updates while open)")
        criteria = [_text(c) for c in _as_list(args.get("acceptance_criteria")) if _text(c)]
        if not criteria:
            problems.append("`acceptance_criteria`: testable statements the finished UI must satisfy")

        project = getattr(self, "_project", None)
        known: list[Any] = []
        if project is not None:
            known = await ApiRouteService(self._app_service._session).rows(project.id)
        routes: list[dict[str, Any]] = []
        for r in _as_list(args.get("routes")):
            if not isinstance(r, dict):
                continue
            method, path = str(r.get("method") or "").upper(), str(r.get("path") or "")
            match = next((k for k in known if k.method == method and k.path_shape == path_shape(path)), None)
            if match is None:
                problems.append(f"route `{method} {path}` is not a route of this project — use one listed below, or "
                                "`declare_endpoint` it first if the UI truly needs a new one")
                continue
            if (match.handler_type == "python" and not match.response_schema
                    and match.name not in getattr(self, "_read_ops", set())):
                if from_plan:
                    self._plan_read_first = [*getattr(self, "_plan_read_first", []), match.name]
                    routes.append({"method": method, "path": match.path, "operation": match.name, "purpose": _text(r.get("purpose"))})
                    continue
                problems.append(f"`{method} {match.path}` has no response schema — call `read_backend` with "
                                f"[\"{match.name}\"] (batch them) to read its handler before relying on field names")
            routes.append({"method": method, "path": match.path, "operation": match.name, "purpose": _text(r.get("purpose")),
                           **({"stub": True} if match.handler_type == "stub" else {})})
        if known and not routes and not any("route `" in p for p in problems):
            problems.append("`routes`: the project routes this UI calls (read them from the Project API list)")
        if problems:
            avail = "\n".join(f"- {k.method} {k.path} (api.{k.name})" for k in known if k.auth != "api_key") or "- (none)"
            return ("Error: the brief is incomplete. If you cannot fill these from the user's request and the project, "
                    "call `ask_clarifying_questions` instead of guessing:\n- " + "\n- ".join(problems)
                    + "\n\nProject routes:\n" + avail), None
        brief = {"users": users, "screens": screens, "journeys": journeys, "routes": routes, "states": states,
                 "acceptance_criteria": criteria,
                 "assumptions": [_text(a) for a in _as_list(args.get("assumptions")) if _text(a)]}
        working_set[:] = [f for f in working_set if f["path"] != BRIEF_PATH]
        working_set.append({"path": BRIEF_PATH, "content": brief_markdown(brief), "parsed_index": None})
        self._brief_recorded = True
        stubs = [f"{r['method']} {r['path']}" for r in routes if r.get("stub")]
        note = (" Note: " + ", ".join(stubs) + " are stubs (501 until implemented) — render a clear 'not implemented "
                "yet' error state for them.") if stubs else ""
        return ("Brief recorded in BRIEF.md. Now build exactly this: every screen and journey, the listed states, calling only "
                "the listed routes, and check each acceptance criterion before you finish." + note), brief

    # ── Approved project plan ─────────────────────────────────────────

    async def _plan_for_app(self) -> dict[str, Any] | None:
        """``{version, body, key}`` when the project has an approved plan with an app this chat builds: the request's
        ``plan_app``, else the plan app bound to this app id, else the same name, else the only app in the plan."""
        project = getattr(self, "_project", None)
        if project is None:
            return None
        request = getattr(self, "_request", None)
        session = self._app_service._session
        try:
            from . import project_plan as pp

            rec = await pp.load_approved(session, project.id)
        except Exception:  # noqa: BLE001 - no readable plan: the ordinary flow runs
            return None
        body = (rec or {}).get("body")
        if not isinstance(body, dict):
            return None
        apps = [a for a in body.get("apps") or [] if isinstance(a, dict) and a.get("key")]
        if not apps:
            return None
        want = getattr(request, "plan_app", None)
        app_id = getattr(request, "app_id", None)
        app = None
        if app_id:
            try:
                from ..db.models import AppModel
                app = await session.get(AppModel, app_id)
            except Exception:  # noqa: BLE001
                app = None
        name = str(getattr(app, "name", "") or "").strip().lower()
        match = (next((a for a in apps if a["key"] == want), None) if want else None) \
            or (next((a for a in apps if app_id and a.get("app_id") == app_id), None))\
            or (next((a for a in apps if name and str(a.get("name") or "").strip().lower() == name), None))\
            or (apps[0] if len(apps) == 1 and not (app_id and apps[0].get("app_id") not in (None, app_id)) else None)
        if match is None:
            return None
        if app_id and match.get("app_id") != app_id:     # non-breaking metadata: remember which app this is
            try:
                await pp.bind_app(session, project.id, match["key"], app_id)
            except Exception:  # noqa: BLE001
                logger.warning("app builder: could not bind app to the plan", exc_info=True)
        return {"version": rec.get("version"), "body": body, "key": match["key"]}

    @staticmethod
    def _plan_schema(fields: Any) -> dict[str, Any] | None:
        """JSON Schema for a plan route's request/response fields ``[{name, type, required}]``."""
        items = [f for f in (fields.get("fields") if isinstance(fields, dict) else fields) or [] if isinstance(f, dict) and f.get("name")]
        if not items:
            return None
        kinds = {"string": {"type": "string"}, "number": {"type": "number"}, "integer": {"type": "integer"},
                 "boolean": {"type": "boolean"}, "datetime": {"type": "string", "format": "date-time"}, "json": {}}
        out: dict[str, Any] = {"type": "object", "properties": {str(f["name"]): dict(kinds.get(str(f.get("type")), {})) for f in items}}
        required = [str(f["name"]) for f in items if f.get("required")]
        if required:
            out["required"] = required
        return out

    async def _seed_plan_brief(self, working_set: list[dict]) -> list[dict[str, Any]]:
        """Record BRIEF.md from the plan's screens and routes. Planned routes the project does not have yet are declared as
        stubs carrying the plan's request/response fields, so the app gets the real field names and the module that builds
        the route adopts the stub. Returns the events to send; on a failed brief the plan is dropped and the old flow runs."""
        from . import project_plan as pp
        from .api_route_service import ApiRouteService, path_shape

        plan = self._plan
        if any(f["path"] == BRIEF_PATH for f in working_set):
            return []
        events: list[dict[str, Any]] = []
        args = pp.app_brief(plan["body"], plan["key"])
        if not args:
            self._plan = None
            return []
        known = await ApiRouteService(self._app_service._session).rows(self._project.id)
        for r in args.get("routes") or []:
            method, path = str(r.get("method") or "").upper(), str(r.get("path") or "")
            if not method or not path or any(k.method == method and k.path_shape == path_shape(path) for k in known):
                continue
            contract = pp.route_contract(plan["body"], f"{method} {path}") or {}
            decl = {"method": method, "path": path, "description": str(r.get("purpose") or contract.get("purpose") or "")[:300],
                    "request_schema": self._plan_schema(contract.get("request")),
                    "response_schema": self._plan_schema(contract.get("response"))}
            _text, info = await self._declare_endpoint_now({k: v for k, v in decl.items() if v}, working_set)
            if info.get("status") == "created":
                events.append({"type": "endpoints_declared", "routes": [info["route"]], "results": [info]})
        self._plan_read_first = []
        result, brief = await self._record_brief_now(args, working_set, from_plan=True)
        if brief is None:
            logger.warning("app builder: the plan's brief was refused: %s", result[:300])
            self._plan = None
            return []
        self._brief_data = brief
        await self._remember_brief(brief)
        self._plan_stubs = [f"{r['method']} {r['path']}" for r in brief["routes"] if r.get("stub")]
        events.append({"type": "brief", "path": BRIEF_PATH, "brief": brief})
        events.append({"type": "plan", "version": plan["version"], "app": plan["key"]})
        return events

    async def _apply_scaffold(self, working_set: list[dict]) -> AsyncGenerator[dict[str, Any], None]:
        """A new plan-based app starts from the scaffold: pages from templates bound to the project's resource routes, written to
        the working set (and streamed to the preview) before any model call. Screens no template fits are then written by the
        model concurrently, one focused call each; everything else is already done."""
        from . import app_scaffold
        from .api_route_service import ApiRouteService

        plan = self._plan
        try:
            rows = await ApiRouteService(self._app_service._session).rows(self._project.id)
            routes = [{"name": r.name, "method": r.method, "path": r.path} for r in rows if getattr(r, "auth", None) != "api_key"]
            sc = app_scaffold.build(plan["body"], plan["key"], routes)
        except Exception:  # noqa: BLE001 - no scaffold: the model builds the app as before
            logger.warning("app builder: scaffold failed", exc_info=True)
            return
        if sc is None:
            return
        before = {f["path"]: f["content"] for f in working_set}
        for f in sc.files:
            self._tool_write_files({"files": [f]}, working_set)
        await self._report_files(before, working_set)
        self._scaffold = sc
        yield _sse("message", {"type": "scaffold", "files": [f["path"] for f in sc.files], "screens": sc.screens,
                               "custom": [c["name"] for c in sc.custom]})
        if sc.custom:
            before = {f["path"]: f["content"] for f in working_set}
            result = await self._build_screens_now({"screens": [
                {"path": c["path"], "purpose": c["purpose"], "props": "go: (screen: string) => void", "operations": c["operations"]}
                for c in sc.custom]}, working_set)
            await self._report_files(before, working_set)
            yield _sse("message", {"type": "tool_result", "tool": "build_screens", "result": result.splitlines()[-1] if result else ""})

    def _plan_note(self) -> str:
        plan = self._plan
        out = f"\n\nBuilt from approved plan v{plan['version']}."
        if self._plan_stubs:
            out += (" Planned routes still stubs (no backend module has built them yet): " + ", ".join(self._plan_stubs[:12]) + ".")
        return out + " Anything that departs from the plan needs a plan revision."

    async def _declare_endpoint_now(self, args: dict, working_set: list[dict]) -> tuple[str, dict[str, Any]]:
        """Review C6: create (or find) the stub inside the tool call, report a per-stub result, and
        regenerate api/client.ts in the working set before the next model turn."""
        from .api_route_service import ApiRouteService, path_shape
        from .project_service import ProjectError

        method, path = str(args.get("method") or "").upper(), args.get("path")
        info: dict[str, Any] = {"method": method, "path": path, "status": "error", "route": None, "error": None}
        if getattr(self, "_project", None) is None:
            info["error"] = {"code": "no_project", "message": "endpoints can only be declared for project apps"}
            return "Error: no project context — endpoints can only be declared for project apps.", info
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not isinstance(path, str) or not path.startswith("/"):
            info["error"] = {"code": "invalid_route", "message": "method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'"}
            return "Error: method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'.", info
        session = self._app_service._session
        svc = ApiRouteService(session)
        existing = next((r for r in await svc.rows(self._project.id)
                         if r.method == method and r.path_shape == path_shape(path)), None)
        if existing is not None:
            info.update(status="existing", route=await svc.to_dict(self._project, existing))
            text = f"{method} {path} already exists as operation '{existing.name}' — call api.{existing.name}(...)."
        else:
            payload = {k: args[k] for k in ("name", "description", "request_schema", "response_schema") if args.get(k)}
            try:
                res = await svc.create(self._project, {**payload, "method": method, "path": path, "handler": {"type": "stub"}},
                                       source="app_stub")
            except ProjectError as e:  # validation errors are raised before anything is written
                info["error"] = {"code": e.code, "message": e.message}
                return f"Error: could not declare {method} {path}: {e.code}: {e.message}", info
            info.update(status="created", route=res["route"])
            self._declared.append(res["route"])
            text = (f"Declared {method} {path}: created stub operation '{res['route']['name']}' (returns 501 until "
                    f"implemented). api/client.ts now has api.{res['route']['name']}(...).")
        await self._refresh_client(working_set)
        return text, info

    async def _refresh_client(self, working_set: list[dict]) -> None:
        """Regenerate api/client.ts in the working set, so the next turn sees the project's routes as they are now."""
        from .client_codegen import CLIENT_PATH

        try:
            from ..routes.api_routes import client_source
            src, _ = await client_source(self._app_service._session, self._project, self._project.default_environment)
            working_set[:] = [f for f in working_set if f["path"] != CLIENT_PATH]
            working_set.append({"path": CLIENT_PATH, "content": src, "parsed_index": None})
        except Exception:
            logger.warning("app builder: client regeneration failed", exc_info=True)

    # ── Backend (LIVE-FILES §5): the app builder builds planned modules; the planner owns the data model ─────────

    async def _load_approved(self) -> dict[str, Any] | None:
        """``{version, body}`` of the project's approved plan, or None (no project, no plan, or no readable plan store)."""
        project = getattr(self, "_project", None)
        if project is None:
            return None
        try:
            from . import project_plan as pp

            rec = await pp.load_approved(self._app_service._session, project.id)
        except Exception:  # noqa: BLE001 - without a readable plan the old flow (declare_endpoint) runs
            return None
        return rec if isinstance(rec, dict) and isinstance(rec.get("body"), dict) else None

    def _backend_builders(self) -> Any:
        """The build coordinator's builder seams (generator, module state, normal save path); tests substitute them."""
        from . import build_coordinator as bc

        return bc.make_builders()

    async def _build_backend_now(self, args: dict, working_set: list[dict],
                                 sink: Callable[[dict[str, Any]], None]) -> tuple[str, dict[str, Any] | None]:
        """The ``build_backend`` tool: generate a planned module (an update when it is saved, else new) and save it through the
        normal save path, handing ``file`` / ``route`` / ``phase`` events to ``sink`` as it goes. Returns the compact tool
        result ``{status, module, module_id, routes, problems}`` and, when the change departs from the approved plan's data
        model, the planner's draft revision ``{version, diff, reason, module, instruction}`` (nothing is saved then)."""
        from . import build_coordinator as bc
        from . import module_generator as mg

        key = str(args.get("module") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        why = str(args.get("reason") or "").strip()
        out: dict[str, Any] = {"status": "error", "module": key, "module_id": None, "routes": [], "problems": []}

        def done(status: str, *problems: dict[str, str], **kw: Any) -> str:
            out.update(status=status, **kw)
            out["problems"] = (out["problems"] + list(problems))[:20]
            return json.dumps(out, default=str)

        project, plan = getattr(self, "_project", None), getattr(self, "_approved", None)
        if project is None or plan is None:
            return done("error", {"code": "no_plan", "message": "build_backend needs an approved project plan; without one "
                                  "call `declare_endpoint` for the routes the UI needs"}), None
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or not instruction:
            return done("error", {"code": "invalid_request", "message": "give `module` (a plan module key, snake_case) and "
                                  "a concrete `instruction`"}), None
        pid = project.id
        try:
            active = await bc._active(self._app_service._session, pid)
        except Exception:  # noqa: BLE001 - no build table: nothing can be running
            logger.warning("app builder: could not read the project's builds", exc_info=True)
            active = None
        if active is not None:
            return done("busy", {"code": "build_in_progress", "message": f"project build {active.id} is running; wait for "
                                 "it to finish (or cancel it), then ask again"}), None
        if pid in _BACKEND_BUILDS or any(k[0] == pid for k in mg._ACTIVE):
            return done("busy", {"code": "generation_in_progress", "message": "another backend generation is running in "
                                 "this project; try again when it finishes"}), None
        modules = {str(m.get("key")) for m in plan["body"].get("modules") or [] if isinstance(m, dict)}
        if key not in modules:
            return await self._needs_plan_revision(
                done, f"module '{key}' is not in the approved plan v{plan['version']} (planned: "
                      f"{', '.join(sorted(modules)) or 'none'})", key, instruction, why)
        _BACKEND_BUILDS.add(pid)
        try:
            with bc.sink_scope(sink, f"module:{key}", "backend_builder"):
                return await self._run_backend(done, out, key, instruction, why, working_set, sink)
        finally:
            _BACKEND_BUILDS.discard(pid)

    async def _run_backend(self, done: Callable[..., str], out: dict[str, Any], key: str, instruction: str, why: str,
                           working_set: list[dict], sink: Callable[[dict[str, Any]], None]
                           ) -> tuple[str, dict[str, Any] | None]:
        from . import build_coordinator as bc
        from .api_route_service import default_name
        from .code_module_service import discover_routes
        from .project_service import ProjectError

        pid = self._project.id
        b = self._backend_builders()

        async def log(message: str) -> None:
            sink({"type": "phase", "phase": "backend", "message": str(message)[:300]})

        async def never() -> bool:
            return False

        state = await b.module_state(pid, key)
        await log(f"Backend builder: {'updating' if state['module_id'] else 'creating'} {key}.py")
        res = await b.generate_module(pid, key, f"{instruction} [plan:{key}]"[:4000], state["module_id"], log, never)
        if res.get("type") != "result" or not isinstance(res.get("source"), str):
            problems = [{"code": str(res.get("code") or "generation_failed"), "message": str(res.get("message"))[:500]}]
            problems += [{"code": str(p.get("code") or "problem"), "message": str(p.get("message"))[:300]}
                         for p in res.get("problems") or [] if isinstance(p, dict)]
            off_plan = [p["message"] for p in problems if p["code"] in PLAN_SCHEMA_CODES]
            if off_plan:
                return await self._needs_plan_revision(done, "; ".join(off_plan[:4]), key, instruction, why)
            return done("failed", *problems[:20]), None
        path, source = f"backend/{key}.py", res["source"]
        bc.emit_file("backend", path, source)
        try:
            module_id = await b.save_module(pid, key, res.get("module_id") or state["module_id"], source, res.get("tables"))
        except ProjectError as e:
            if e.code in PLAN_SCHEMA_CODES:
                details = [str(v.get("message")) for v in e.extra.get("violations") or [] if isinstance(v, dict)]
                return await self._needs_plan_revision(done, "; ".join(details[:4]) or e.message, key, instruction, why)
            return done("failed", {"code": e.code, "message": e.message[:500]}), None
        bc.emit_file("backend", path, source)
        try:
            decls = [(d["method"], d["path"], d["name"] or default_name(d["method"], d["path"])) for d in discover_routes(source)]
        except ProjectError:
            decls = []
        for method, rpath, name in decls:
            bc.emit({"type": "route", "method": method, "path": rpath, "name": name, "status": "built"})
        checks = [c for c in res.get("self_test") or [] if isinstance(c, dict)]
        found = bc.route_statuses([(m, p) for m, p, _ in decls], checks)
        routes = []
        for method, rpath, name in decls:
            status = found.get((method, rpath)) or "built"
            if status != "built":
                bc.emit({"type": "route", "method": method, "path": rpath, "name": name, "status": status})
            routes.append({"method": method, "path": rpath, "name": name, "status": status})
        problems = [{"code": "route_missing", "message": f"{r} is planned but not built"} for r in res.get("missing") or []]
        problems += [{"code": "self_test_failed", "message": f"{c.get('route')}: {c.get('failure') or 'self-test check failed'}"[:300]}
                     for c in checks if c.get("ok") is False and not c.get("skipped")]
        await self._refresh_client(working_set)
        if getattr(self, "_project", None) is not None:
            self._digest = await self._route_digest()
        await log(f"Saved {key}.py ({len(routes)} route(s))")
        return done("partial" if problems else "built", *problems, module_id=module_id, routes=routes,
                    next="build the UI against these routes (api/client.ts was regenerated)"), None

    async def _needs_plan_revision(self, done: Callable[..., str], reason: str, key: str, instruction: str, why: str
                                   ) -> tuple[str, dict[str, Any] | None]:
        """The planner decides the data model: ask it for a draft revision instead of building off-plan."""
        from .project_service import ProjectError

        reason = (reason + (f" (needed for: {why})" if why else ""))[:1200]
        try:
            rev = await self._revise_plan(reason, instruction, key)
        except ProjectError as e:
            return done("needs_plan_revision", {"code": e.code, "message": "the planner could not draft the revision: "
                                                + e.message[:300]}, reason=reason), None
        text = done("needs_plan_revision", reason=reason, plan_version=rev["version"],
                    next=f"the planner drafted plan v{rev['version']} for the user to approve; stop now and end your turn")
        return text, {"version": rev["version"], "diff": rev.get("diff") or {}, "reason": reason, "module": key,
                      "instruction": instruction}

    async def _revise_plan(self, reason: str, instruction: str, module: str) -> dict[str, Any]:
        """A draft revision of the approved plan by the planner (the same conversation ``POST /plan/revise`` runs): it decides
        tables, columns, relations, indexes, enums and the additive migration. ``{version, diff}``; raises ``ProjectError``."""
        from . import plan_clarify as pc
        from .project_service import ProjectError

        pid = self._project.id
        lock = pc.try_acquire(pid)
        if lock is None:
            raise ProjectError(409, "plan_in_progress", "another planning step is already running for this project")
        prompt = (f"The app builder needs a backend change in module '{module}': {instruction}\n"
                  f"It cannot be built on the approved plan: {reason}\n"
                  "Decide the data model change (tables, columns, relations, indexes, enums), the routes and the module that "
                  "owns each, as an additive change.")[:pc.MAX_PROMPT_CHARS]
        last: dict[str, Any] | None = None
        try:
            async for ev in pc.PlanConversation(pid, None, "app_builder", revise_prompt=prompt).events():
                if ev.get("type") in ("result", "error"):
                    last = ev
        finally:
            pc.release(lock)
        if last is None or last["type"] == "error":
            raise ProjectError(502, str((last or {}).get("code") or "plan_failed"),
                               str((last or {}).get("message") or "the planner returned nothing"))
        return {"version": last["plan"]["version"], "diff": last.get("diff") or {}}

    # ── Agent Loop ────────────────────────────────────────────────────

    async def _run_agent_loop(
        self,
        working_set: list[dict[str, str]],
        api_executions: list[ApiTestExecutionModel],
        user_message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Tool-use agent loop. Modifies working_set in place.

        Always starts with the fast model. The LLM can call ``escalate``
        to switch to the heavy model for the remaining turns. This way
        the fast model handles simple edits end-to-end and does the
        groundwork (reads, searches) before handing off complex tasks.
        """

        # Build system prompt
        system_prompt = self._build_agent_system_prompt(api_executions)

        # Careful-developer gate: a new project app needs a recorded brief before any UI file.
        new_app = not has_user_files(working_set)
        self._brief_recorded = False
        self._brief_data: dict[str, Any] | None = None
        self._read_ops: set[str] = set()
        self._acked_findings: set[str] = set()
        # An approved project plan is the brief: record it from the plan, ask nothing.
        self._plan = await self._plan_for_app()
        self._plan_stubs: list[str] = []
        plan_events: list[dict[str, Any]] = []
        if self._plan is not None:
            plan_events = await self._seed_plan_brief(working_set)
            for ev in plan_events:
                yield _sse("message", ev)
        self._scaffold = None
        if self._plan is not None and new_app and not self._plan_stubs:    # stubs (501) mean the backend is not built yet
            async for ev in self._apply_scaffold(working_set):
                yield ev
            sc = self._scaffold
            if sc is not None:
                done = [c["name"] for c in sc.custom if not _is_stub_screen(working_set, c["path"])]
                left = [c["name"] for c in sc.custom if c["name"] not in done]
                text = (f"Built {len(sc.screens)} screens from the plan ({', '.join(s['template'] for s in sc.screens)}) on "
                        f"{', '.join(sc.resources)}; ready to preview.")
                if left:
                    text += " Not written yet: " + ", ".join(left) + ". Send a message to continue with these."
                    yield _sse("message", {"type": "partial", "missing": left})
                yield _sse("message", {"type": "text", "content": text})
                self._stats_sent = True
                yield _sse("message", {"type": "stats", **self._stats()})
                return
        self._brief_required = (getattr(self, "_project", None) is not None and new_app and clarify_enabled()
                                and not any(f["path"] == BRIEF_PATH for f in working_set))
        self._clarify_allowed = getattr(self, "_project", None) is not None and clarify_enabled() and self._plan is None
        rounds = clarify_rounds(conversation_history)
        # A short ask on a new app is a guess even when the project has routes: the domain questions come first (server-enforced).
        self._vague_first = (self._brief_required and self._clarify_allowed and rounds == 0 and is_vague_request(user_message))
        if self._scaffold is not None:
            system_prompt += "\n\n" + _SCAFFOLD_PROMPT
        if self._plan is not None:
            system_prompt += "\n\n" + _PLAN_PROMPT.format(version=self._plan["version"])
            if getattr(self, "_plan_read_first", None):
                system_prompt += ("\nThese routes have no response schema yet: call `read_backend` with "
                                  + json.dumps(self._plan_read_first[:12]) + " (one call) before using their field names.")
        # The app builder owns the backend too, but only to the approved plan: without one, declare_endpoint stubs as before.
        self._approved = await self._load_approved()
        if self._approved is not None:
            mods = [str(m.get("key")) for m in self._approved["body"].get("modules") or [] if isinstance(m, dict) and m.get("key")]
            system_prompt += "\n\n" + _BACKEND_PROMPT.format(version=self._approved["version"], modules=", ".join(mods) or "none")
            pending = pending_backend(conversation_history)
            if pending is not None:
                module, version, instr = pending
                system_prompt += "\n" + (_BACKEND_RESUME if self._approved["version"] >= version else _BACKEND_WAITING).format(
                    version=version, module=module, instruction=instr[:600])
        from . import oneshot_builder as _ob
        if _ob.editable_rules_enabled():
            system_prompt += "\n\n" + _ob._editable_section()
        if getattr(self, "_notes", ""):
            system_prompt += "\n\n" + self._notes
        if self._brief_required:
            system_prompt += "\n\n" + _BRIEF_PROMPT
            if self._vague_first:
                system_prompt += "\n\n" + _VAGUE_PROMPT.format(round=1, round2="")
            elif 0 < rounds < MAX_CLARIFY_ROUNDS:
                system_prompt += ("\n\nThe user has answered round 1. Ask a second round (at most 3 questions) ONLY for a real gap the answers "
                                  "opened; otherwise call `record_brief` now (defaults under `assumptions`).")
            if rounds >= MAX_CLARIFY_ROUNDS:
                self._clarify_allowed = False
                system_prompt += ("\n\nYou have already asked the user questions twice. Do not ask again: call `record_brief` "
                                  "now, filling remaining gaps with sensible defaults listed under `assumptions`, then build.")

        # Build initial user message — project index prepended to first user content
        project_preamble = ""
        if getattr(self, "_memory", ""):
            project_preamble += self._memory + "\n\n"
        if working_set:
            project_preamble += self._build_project_index(working_set) + "\n\n"
        if getattr(self, "_project", None) is not None and not new_app:
            # An earlier turn may have ended with type errors (turn limit): they are part of this request, not the user's problem.
            known = await typecheck_errors(working_set)
            if known:
                project_preamble += ("## Type errors already in the app (fix them in your first edit; they crash the preview)\n"
                                     + known[:2200] + "\n\n")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Conversation history: last 3 user/assistant pairs for multi-turn context.
        # Older user messages are truncated — intent matters more than exact wording — except while a new
        # app is still being planned (the original request and the answers are the spec).
        if conversation_history:
            recent = conversation_history[-6:]  # 3 pairs = 6 entries
            for i, entry in enumerate(recent):
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role not in ("user", "assistant") or not content:
                    continue
                # Truncate older user messages (not the most recent pair)
                if role == "user" and i < len(recent) - 2 and len(content) > 150 and not self._brief_required:
                    content = content[:150] + "…"
                messages.append({"role": role, "content": content})

        # Current request — prepend project index to the user message
        messages.append({"role": "user", "content": project_preamble + user_message})

        # First request (no existing files) → use heavy model for better
        # initial scaffolding.  Subsequent requests start with the fast model
        # and can escalate via the `escalate` tool if needed.
        is_first_request = not any(not is_kit_path(f["path"]) for f in working_set) and not conversation_history
        current_model = (_HEAVY_MODEL if is_first_request or self._brief_required or len(user_message) > LIGHT_MAX_REQUEST_CHARS
                         else _LIGHT_MODEL)

        # Build tool definitions — for first requests (empty project), only
        # expose write_files.  Fewer tools = fewer MALFORMED_FUNCTION_CALL
        # errors from Gemini 2.5 Pro's thinking mode.
        if is_first_request:
            # Review C6: declare_endpoint is offered on every turn, including the first.
            tools = [t for t in self._build_agent_tools() if t["name"] in
                     ("write_files", "declare_endpoint", "build_backend", "read_backend", "ask_clarifying_questions", "record_brief",
                      "build_screens", *LOOKUP_TOOL_NAMES)]
        else:
            tools = self._build_agent_tools()
        if self._vague_first:  # nothing may be written before the domain questions
            tools = [t for t in tools if t["name"] not in ("write_files", "edit_files", "build_screens", "delete_file")]
        guard = (StallGuard(5, 5) if self._vague_first else StallGuard(READ_BUDGET_NEW_APP, IDLE_TURN_BUDGET_NEW_APP) if self._brief_required
                 else StallGuard())
        self._guard = guard
        truncations = 0
        contract_repairs = 0
        delivery_repairs = 0
        clarify_request: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
        revision: dict[str, Any] | None = None

        original_files = {f["path"]: f["content"] for f in working_set}
        syntax_repairs = 0
        type_repairs = 0
        drop_checked = False
        poll_checked = False
        turn = 0
        consecutive_errors = 0
        last_tool_sig: str | None = None
        duplicate_count = 0

        while turn < MAX_AGENT_TURNS:
            turn += 1
            yield _sse("message", {"type": "phase", "phase": "thinking", "message": f"Turn {turn}..."})

            # Trim stale read results from history to keep context lean
            if turn > 2:
                messages = self._trim_tool_context(messages)

            # Stall guard: past the read budget only change-making tools (or one blocking question) are offered.
            turn_tools = tools
            forced = guard.exhausted
            if forced:
                turn_tools = forced_tools(tools, brief_pending=self._brief_required and not self._brief_recorded, ask_only=self._vague_first)
                if not guard.notified:
                    guard.notified = True
                    current_model = _HEAVY_MODEL
                    yield _sse("message", {"type": "phase", "phase": "committing", "message": "Read budget used: now editing"})
                    messages.append({"role": "user", "content": (
                        f"READ BUDGET USED ({guard.turns} turns / {guard.reads:g} reads without a change). Reading is closed. Now make the change with `edit_files` "
                        "(precise patches) or `write_files` from what you already know"
                        + ("" if not self._clarify_allowed else ", or call `ask_clarifying_questions` with ONE specific blocking question "
                           "(what exactly is missing and which options you see)")
                        + ". Do not answer with a status update or a check-in.")})
            try:
                self._count_call()
                response = await call_llm(
                    model=current_model,
                    messages=messages,
                    tools=turn_tools,
                    **({"tool_choice": "required"} if forced else {}),
                    temperature=0.3,
                    max_tokens=32_000 if current_model.startswith("gemini-") else 16_000,
                    # Gemini 3 reasoning shares the output budget with the code.
                    # Keep space for multi-view apps rather than spending it all
                    # on thinking before a write_files call can be emitted.
                    **({"thinking_level": "LOW"} if current_model.startswith("gemini-3") else {}),
                )
            except Exception as e:  # review C5: surfaced as an `error` event, then `done`
                raise _LLMFailed(e) from e

            if response.output_truncated:
                truncations += 1
                if truncations > TRUNCATION_RETRIES:
                    raise _LLMFailed(RuntimeError(
                        "The model reached its output limit before completing the app. "
                        "Try building one view at a time or splitting the app into smaller files."))
                logger.warning("App builder: output truncated (retry %d/%d)", truncations, TRUNCATION_RETRIES)
                yield _sse("message", {"type": "phase", "phase": "retrying", "message": "Output was cut off; retrying in smaller pieces"})
                messages.append({"role": "user", "content": (
                    "Your last response was cut off by the output limit and nothing from it was applied. Redo it in smaller pieces: "
                    "at most 2 files per `write_files` call (each under ~200 lines, App.tsx last), or use `build_screens` for independent "
                    "screens, or patch with `edit_files`. Do not repeat what is already written.")})
                continue

            # Malformed tool call — count as error and retry the turn
            if response.malformed_tool_call:
                consecutive_errors += 1
                logger.warning("Turn %d: malformed tool call (consecutive_errors=%d)", turn, consecutive_errors)
                if consecutive_errors >= 2 and current_model == _LIGHT_MODEL and _LIGHT_MODEL != _HEAVY_MODEL:
                    logger.info("App builder: auto-escalating after malformed tool calls")
                    yield _sse("message", {"type": "phase", "phase": "escalating", "message": "Switching to advanced model"})
                    current_model = _HEAVY_MODEL
                    consecutive_errors = 0
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    yield _sse("message", {"type": "text", "content": "I ran into some trouble generating the code. Let's try again — could you rephrase your request?"})
                    return
                # Add the garbled text as assistant message so context moves forward
                if response.text:
                    messages.append({"role": "assistant", "content": response.text})
                    messages.append({"role": "user", "content": "Your previous response had a malformed tool call. Please try again."})
                continue

            # No tool calls = agent is done
            if not response.tool_calls:
                changed = {f["path"]: f["content"] for f in working_set} != original_files
                errors = await _generated_syntax_errors(working_set) if changed else None
                if errors:
                    syntax_repairs += 1
                    if syntax_repairs > 2:
                        raise _LLMFailed(RuntimeError("Generated code still fails syntax validation after two repairs: " + errors))
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "The generated files failed syntax validation. Fix the compiler errors using the file tools, "
                        "preserving existing behavior. Do not declare success until repaired:\n" + errors})
                    guard.repair()
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Repairing generated code syntax"})
                    continue
                type_errors = await typecheck_errors(working_set) if changed and type_repairs < 2 else None
                if type_errors:
                    type_repairs += 1
                    current_model = _HEAVY_MODEL
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "Type-check (tsc, non-strict) found errors — they usually mean a wrong prop for a kit component or a "
                        "wrong API field and crash at runtime. Fix them with the file tools (check the kit API in the "
                        "system prompt), keeping behaviour, then finish:\n" + type_errors})
                    guard.repair()
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Fixing type errors"})
                    continue
                touched = [f for f in working_set if original_files.get(f["path"]) != f["content"]]
                findings = await self._integrity_findings(touched) if touched and contract_repairs < CONTRACT_REPAIRS else []
                if findings:
                    contract_repairs += 1
                    current_model = _HEAVY_MODEL
                    await self._remember("failure", f"contract:{zlib.crc32(findings[0].encode()):08x}",
                                         "UI read fields its route does not return", {"findings": findings[:6]},
                                         text="app builder data contract failure: " + " ; ".join(findings[:4]), source="failure")
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "Data check: the UI must show only what the API returned. Fix each of these with `edit_files` (look the route up with "
                        "`get_route_signature` if you need its fields; remove or replace any value the API does not provide, show \"—\" when it is "
                        "missing):\n- " + "\n- ".join(findings[:8]) + "\nIf a flagged field really is provided (e.g. a key inside a JSON column "
                        "the route returns), say so in one line and finish."})
                    guard.repair()
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Checking data against the API"})
                    continue
                gaps = self._delivery_gaps(working_set, touched) if touched else []
                if gaps and delivery_repairs < DELIVERY_REPAIRS:
                    delivery_repairs += 1
                    current_model = _HEAVY_MODEL
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "Delivery check: you are about to finish, but the plan is not fully delivered:\n- " + "\n- ".join(gaps[:10])
                        + "\nBuild the missing screens/wiring now (`build_screens` for several independent screens, or `write_files`; "
                        "App.tsx last). A call to a route the project does not have needs `declare_endpoint` first. If something truly "
                        "cannot be built, do not claim it: name it in one line and finish."})
                    guard.repair()
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Building the missing pieces"})
                    continue
                pollers = hand_rolled_polling(touched) if touched and not poll_checked else []
                if pollers:
                    poll_checked = True
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "Polling check: " + ", ".join(pollers) + " poll with a hand-rolled `setInterval`. Replace it with "
                        "the kit's `useJob`/`usePoll` (see 'Polling' in the system prompt) — a useEffect keyed on the "
                        "polled status cancels itself when the run finishes and drops the final fetch. Keep behaviour "
                        "the same otherwise. If the interval is not polling (e.g. a clock), say so in one line and finish."})
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Switching to kit polling"})
                    continue
                dropped = dropped_api_calls(original_files, working_set, user_message) \
                    if changed and not new_app and not drop_checked else []
                if dropped:
                    drop_checked = True
                    messages.append(response.get_assistant_message())
                    messages.append({"role": "user", "content":
                        "Regression check: your edits removed call sites of " + ", ".join(f"`api.{d}`" for d in dropped)
                        + " (the app now calls it from fewer places than before), and the request didn't ask for that. "
                        "A careful edit keeps existing behaviour (e.g. polling events while a run is live). Compare with "
                        "what the code did before, restore anything lost with the file tools; if the change really is "
                        "intended (e.g. two calls merged into one), say why in one line and finish."})
                    yield _sse("message", {"type": "phase", "phase": "repairing", "message": "Restoring dropped behaviour"})
                    continue
                text = response.text or ""
                if getattr(self, "_plan", None) is not None and touched:
                    text += self._plan_note()
                left = self._delivery_gaps(working_set, touched, report=True) if touched else []
                if left:  # the budget is spent: say so instead of reporting a finished build
                    yield _sse("message", {"type": "partial", "missing": left})
                    text += ("\n\n**Partial build.** Not delivered yet:\n- " + "\n- ".join(left[:12])
                             + "\nSend a message to continue with these.")
                if text:
                    yield _sse("message", {"type": "text", "content": text})
                return

            # Detect duplicate tool calls (semantic loop)
            current_sig = _tool_call_signature(response.tool_calls)
            if current_sig == last_tool_sig:
                duplicate_count += 1
                if duplicate_count >= 2:
                    yield _sse("message", {"type": "text", "content": "I seem to be going in circles. Could you try rephrasing what you'd like changed?"})
                    return
            else:
                duplicate_count = 0
            last_tool_sig = current_sig

            # Append assistant message
            messages.append(response.get_assistant_message())

            if response.text:
                yield _sse("message", {"type": "thinking", "content": response.text})

            guard.tick()  # an edit later in this turn resets it
            # Execute each tool call
            for tc in response.tool_calls:
                # Handle escalate — switch to heavy model for remaining turns
                if tc.name == "escalate":
                    reason = tc.args.get("reason", "complex task")
                    logger.info("App builder: escalating to heavy model — %s", reason)
                    yield _sse("message", {"type": "phase", "phase": "escalating", "message": reason})
                    current_model = _HEAVY_MODEL
                    messages.append({
                        "role": "tool",
                        "content": f"Switched to advanced model. Continue with the task.",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                    })
                    continue

                yield _sse("message", {"type": "tool_call", "id": tc.id, "tool": tc.name, "args": _summarize_tool_args(tc.args)})

                declared = None
                brief = None
                before = {f["path"]: f["content"] for f in working_set} if self.on_files and tc.name in EDIT_TOOLS else None
                try:
                    if tc.name == "declare_endpoint":
                        result, declared = await self._declare_endpoint_now(tc.args, working_set)
                    elif tc.name == "build_backend":
                        if revision is not None:
                            result = "Error: a plan revision is waiting for the user's approval; end your turn."
                        else:       # its file / route / phase events go out on this stream while it runs
                            async for kind, item in _pump(lambda sink: self._build_backend_now(tc.args, working_set, sink)):
                                if kind == "event":
                                    yield _sse("message", item)
                                else:
                                    result, revision = item
                    elif tc.name == "read_backend":
                        result = await self._read_backend_now(tc.args)
                    elif tc.name in LOOKUP_TOOL_NAMES:
                        result = (await self._lookup_now(tc.name, tc.args)) if not guard.exhausted else (
                            "Error: read budget used — edit, write, or ask one blocking question.")
                    elif tc.name == "build_screens":
                        if getattr(self, "_brief_required", False) and not getattr(self, "_brief_recorded", False):
                            result = "Error: record the brief first (`record_brief`), then build screens."
                        else:
                            result = await self._build_screens_now(tc.args, working_set)
                    elif tc.name == "record_brief" and getattr(self, "_vague_first", False):
                        result = ("Error: the request is short, so ask first. Call `ask_clarifying_questions` with 4-6 domain questions "
                                  "(users and roles, core journeys, what must be live, approvals, what the dashboard shows, safety rules).")
                    elif tc.name == "record_brief":
                        result, brief = await self._record_brief_now(tc.args, working_set)
                        if brief is not None:
                            self._brief_data = brief
                            guard.reset(READ_BUDGET, IDLE_TURN_BUDGET)
                            await self._remember_brief(brief)
                    elif tc.name == "ask_clarifying_questions":
                        questions = normalize_questions(tc.args.get("questions"))
                        if not getattr(self, "_clarify_allowed", False):
                            result = ("Error: no more questions — call `record_brief` with sensible defaults listed under "
                                      "`assumptions`, then build.")
                        elif not questions:
                            result = "Error: `questions` must hold at least one question."
                        else:
                            so_far = tc.args.get("brief_so_far") if isinstance(tc.args.get("brief_so_far"), dict) else {}
                            clarify_request = (questions, so_far)
                            result = "Questions sent to the user. Stop here and wait for their answers."
                    elif tc.name in ("write_files", "edit_files") and getattr(self, "_brief_required", False) \
                            and not getattr(self, "_brief_recorded", False):
                        result = ("Error: this is a new app — act like a careful developer and pin down the spec before "
                                  "writing UI. Call `record_brief` (users, screens, journeys, the project routes you will "
                                  "call, empty/loading/error/live states, acceptance criteria). If the request leaves any "
                                  "of that missing or ambiguous, call `ask_clarifying_questions` instead.")
                    else:
                        result = self._execute_agent_tool(tc.name, tc.args, working_set)
                    consecutive_errors = 0
                except Exception as e:
                    result = f"Error: {e}"
                    consecutive_errors += 1
                if before is not None:
                    await self._report_files(before, working_set)
                if declared is not None:
                    yield _sse("message", {"type": "endpoints_declared",
                                           "routes": [declared["route"]] if declared["status"] == "created" else [],
                                           "results": [declared]})
                if brief is not None:
                    yield _sse("message", {"type": "brief", "path": BRIEF_PATH, "brief": brief})

                if not isinstance(result, str):
                    result = json.dumps(result)
                if tc.name in ("write_files", "edit_files") and _edited(result) and not self._brief_required_pending():
                    result += await self._typecheck_feedback(working_set, guard)
                if tc.name in READ_ONLY_TOOLS and guard.exhausted and not result.startswith("Error: read budget"):
                    result = "Error: read budget used — edit, write, or ask one blocking question."
                guard.record(tc.name, result)
                if tc.name in READ_ONLY_TOOLS and 0 < guard.remaining <= 2:
                    result += f"\n[{guard.remaining} read(s) left before you must edit or ask]"
                yield _sse("message", {"type": "tool_result", "id": tc.id, "tool": tc.name, "result": _truncate_for_display(result)})

                messages.append({
                    "role": "tool",
                    "content": result if isinstance(result, str) else json.dumps(result),
                    "tool_call_id": tc.id,
                    "name": tc.name,
                })

            # Clarifying questions end the request: the user answers in their next message.
            if clarify_request is not None:
                questions, so_far = clarify_request
                yield _sse("message", {"type": "clarify", "questions": questions, "brief": so_far,
                                       "round": rounds + 1, "max_rounds": MAX_CLARIFY_ROUNDS})
                yield _sse("message", {"type": "text", "content": clarify_text(questions, so_far)})
                return

            # A plan revision ends the request too: the user approves it (POST /plan/{v}/approve), then sends the next
            # message; the last line below lets that turn resume the backend change (``pending_backend``).
            if revision is not None:
                v = revision["version"]
                yield _sse("message", {"type": "plan_revision", "version": v, "diff": revision["diff"],
                                       "reason": revision["reason"]})
                yield _sse("message", {"type": "text", "content": (
                    f"This needs a change to the approved plan, which the planner decides: {revision['reason']}\n\n"
                    f"The planner drafted plan v{v}. Review and approve it, then send **continue** and I will build the "
                    f"backend and then the UI.\n\nPending backend build: `{revision['module']}` (plan v{v}): "
                    + " ".join(revision["instruction"].split())[:600])})
                return

            # Auto-escalate after 2 consecutive errors (before hitting the hard stop at 3)
            if consecutive_errors >= 2 and current_model == _LIGHT_MODEL and _LIGHT_MODEL != _HEAVY_MODEL:
                logger.info("App builder: auto-escalating after %d consecutive errors", consecutive_errors)
                yield _sse("message", {"type": "phase", "phase": "escalating", "message": "Switching to advanced model after errors"})
                current_model = _HEAVY_MODEL
                consecutive_errors = 0

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield _sse("message", {"type": "text", "content": "I hit a few errors in a row and stopped to avoid making things worse. Want to try a different approach?"})
                return

        edited = [p for p, c in {f["path"]: f["content"] for f in working_set}.items() if original_files.get(p) != c and not is_kit_path(p)]
        yield _sse("message", {"type": "text", "content": (
            f"Stopped at the {MAX_AGENT_TURNS}-turn limit with {len(edited)} file(s) changed ({', '.join(edited[:6]) or 'none'}). "
            "Tell me the next concrete change (a file or screen) and I will continue from here.")})
        left = self._delivery_gaps(working_set, [f for f in working_set if f["path"] in set(edited)], report=True) if edited else []
        if left:
            yield _sse("message", {"type": "partial", "missing": left})
            yield _sse("message", {"type": "text", "content": "**Partial build.** Not delivered yet:\n- " + "\n- ".join(left[:12])})

    # ── Agent Tools ───────────────────────────────────────────────────

    def _build_agent_tools(self) -> list[dict]:
        tools = self._base_agent_tools()
        if getattr(self, "_project", None) is not None:
            tools.append({
                "name": "declare_endpoint",
                "description": "Declare an API endpoint this UI needs that does not exist yet; it becomes a stub route.",
                "parameters": {"type": "object", "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "e.g. /orders/{id}"},
                    "name": {"type": "string", "description": "camelCase operation name"},
                    "description": {"type": "string"},
                    "response_schema": {"type": "object"},
                }, "required": ["method", "path"]},
            })
        if getattr(self, "_project", None) is not None and getattr(self, "_approved", None) is not None:
            tools.append({
                "name": "build_backend",
                "description": "Build or change a backend module of the approved plan BEFORE the UI that needs it: the module "
                               "builder writes the code, saves it (tables, routes) and self-tests it; you get {status, routes, "
                               "problems} and continue the UI against those routes. One module per call. Never designs schema: "
                               "a table, column, route or module the plan does not have returns needs_plan_revision (the "
                               "planner drafts a revision for the user to approve) — then end your turn.",
                "parameters": {"type": "object", "properties": {
                    "module": {"type": "string", "description": "the plan module key, e.g. 'runs'"},
                    "instruction": {"type": "string", "description": "what the module must do: routes, behaviour, fields"},
                    "reason": {"type": "string", "description": "what in the UI needs it"},
                }, "required": ["module", "instruction"]},
            })
        if getattr(self, "_project", None) is not None:
            tools.append({
                "name": "read_backend",
                "description": "Read the backend handler source (and schemas) behind project operations — the exact "
                               "request fields and response JSON shape. Use it whenever api/client.ts types a response as "
                               "`any`; never guess field names. Read-only.",
                "parameters": {"type": "object", "properties": {
                    "operations": {"type": "array", "items": {"type": "string"},
                                   "description": "operation names from api/client.ts, e.g. [\"getCases\", \"postRuns\"]"},
                    "offset": {"type": "integer", "description": "continue a long result: the offset a '[truncated … offset=N]' note gave"},
                }, "required": ["operations"]},
            })
        tools += self._lookup_tools()
        if getattr(self, "_project", None) is not None:
            tools.append({
                "name": "build_screens",
                "description": "Write 2-6 independent screens IN PARALLEL (each by its own call, at most 3 at a time). Give every screen its "
                               "file path, purpose, the exact TypeScript props it receives (state lives in App) and the api operations it calls; "
                               "then write App.tsx yourself to wire them. Use for 3+ screens of a new app; one `write_files` is fine for fewer.",
                "parameters": {"type": "object", "properties": {"screens": {"type": "array", "items": {"type": "object", "properties": {
                    "path": {"type": "string", "description": "views/Library.tsx (not App.tsx, not kit/)"},
                    "purpose": {"type": "string", "description": "what the screen shows and does, states, journeys it serves"},
                    "props": {"type": "string", "description": "TypeScript members of its Props, e.g. `selected: string[]; onRun(ids: string[]): void`"},
                    "operations": {"type": "array", "items": {"type": "string"}, "description": "api operation names it calls"},
                }, "required": ["path", "purpose"]}}}, "required": ["screens"]},
            })
        if getattr(self, "_clarify_allowed", False):
            tools.append({
                "name": "ask_clarifying_questions",
                "description": "Ask the user what you need to know before building (ends this turn; they answer next message). "
                               "Use when users, screens, journeys, backend routes, states or acceptance criteria are missing or ambiguous.",
                "parameters": {"type": "object", "properties": {
                    "questions": {"type": "array", "items": {"type": "object", "properties": {
                        "id": {"type": "string"},
                        "topic": {"type": "string", "enum": ["users", "screens", "journeys", "routes", "states", "acceptance", "other"]},
                        "question": {"type": "string"},
                        "why": {"type": "string", "description": "what you will do differently depending on the answer"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "default": {"type": "string", "description": "what you will assume if they say 'use your defaults'"},
                    }, "required": ["question"]}},
                    "brief_so_far": {"type": "object", "description": "what you already understand: users, screens, journeys, routes…"},
                }, "required": ["questions"]},
            })
        if getattr(self, "_brief_required", False):
            tools.append({
                "name": "record_brief",
                "description": "Record the build brief for this new app (saved as BRIEF.md). Required before the first write_files. "
                               "Routes must be real project routes from the Project API list.",
                "parameters": {"type": "object", "properties": {
                    "users": {"type": "string", "description": "who uses it and what job they are doing"},
                    "screens": {"type": "array", "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "purpose": {"type": "string"}}, "required": ["name"]}},
                    "journeys": {"type": "array", "items": {"type": "string"}, "description": "step-by-step flows"},
                    "routes": {"type": "array", "items": {"type": "object", "properties": {
                        "method": {"type": "string"}, "path": {"type": "string"}, "purpose": {"type": "string"}},
                        "required": ["method", "path"]}},
                    "states": {"type": "object", "properties": {
                        "empty": {"type": "string"}, "loading": {"type": "string"}, "error": {"type": "string"},
                        "live": {"type": "string"}}, "required": ["empty", "loading", "error"]},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                }, "required": ["users", "screens", "journeys", "routes", "states", "acceptance_criteria"]},
            })
        return tools

    def _lookup_tools(self) -> list[dict]:
        """Platform lookups (platform_index) in this file's tool format; project-bound ones only with a project."""
        from .platform_index import TOOLS
        project = getattr(self, "_project", None) is not None
        out = []
        for t in TOOLS:
            if t["name"] not in LOOKUP_TOOL_NAMES or (t["name"] not in ("search_components", "get_component") and not project):
                continue
            params = t["input_schema"]
            if t["name"] == "get_component":  # batch: all the components a screen needs in ONE call
                params = {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "names": {"type": "array", "items": {"type": "string"}, "description": "several components at once"}}, "required": []}
            elif t["name"] == "get_route_signature":  # batch: every route a screen calls in ONE call
                params = {"type": "object", "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "path": {"type": "string"},
                    "routes": {"type": "array", "items": {"type": "object", "properties": {
                        "method": {"type": "string"}, "path": {"type": "string"}}, "required": ["method", "path"]},
                        "description": "several routes at once"}}, "required": []}
            elif t["name"] == "get_table_schema":
                params = {"type": "object", "properties": {"name": {"type": "string"},
                          "names": {"type": "array", "items": {"type": "string"}, "description": "several tables at once"}}, "required": []}
            if isinstance(params.get("properties"), dict) and "offset" not in params["properties"]:
                params = {**params, "properties": {**params["properties"], "offset": {
                    "type": "integer", "description": "continue a long result: the offset a '[truncated … offset=N]' note gave"}}}
            out.append({"name": t["name"], "description": t["description"], "parameters": params})
        return sorted(out, key=lambda t: LOOKUP_TOOL_NAMES.index(t["name"]))

    def _base_agent_tools(self) -> list[dict]:
        return [
            {
                "name": "list_files",
                "description": "List all files with sizes and definition summaries.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "read_files",
                "description": "Read files with line numbers. Prefer a line range, \"views/A.tsx:120-210\" (the Project Index gives each "
                               "definition's lines); a whole file is cut at ~14k characters. Reading counts against a small budget.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["paths"],
                },
            },
            {
                "name": "write_files",
                "description": "Create or overwrite one or more files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                "required": ["path", "content"],
                            },
                        },
                    },
                    "required": ["files"],
                },
            },
            {
                "name": "edit_files",
                "description": "Precise patches: replace an exact unique string in one or more files. Copy old_string from the Project Index or a ranged read; keep it short but unique. Use this for every change to an existing file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "old_string": {"type": "string", "description": "Exact text to find (must be unique)"},
                                    "new_string": {"type": "string", "description": "Replacement text"},
                                },
                                "required": ["path", "old_string", "new_string"],
                            },
                        },
                    },
                    "required": ["edits"],
                },
            },
            {
                "name": "delete_file",
                "description": "Delete a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "search_files",
                "description": "Search the app's files (not kit/) for a regex/substring; returns matches with context lines. "
                               "Optional `path` limits it to files whose path contains that text.",
                "parameters": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["pattern"],
                },
            },
            {
                "name": "escalate",
                "description": "Switch to a stronger model for complex tasks touching 4+ files or after 2+ errors.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        ]

    def _execute_agent_tool(self, name: str, args: dict, working_set: list[dict]) -> str:
        if name == "declare_endpoint":
            return self._tool_declare_endpoint(args, working_set)
        dispatch = {
            "list_files": self._tool_list_files,
            "read_files": self._tool_read_files,
            "write_files": self._tool_write_files,
            "edit_files": self._tool_edit_files,
            "delete_file": self._tool_delete_file,
            "search_files": self._tool_search_files,
        }
        handler = dispatch.get(name)
        if not handler:
            return f"Error: Unknown tool '{name}'"
        return handler(args, working_set)

    def _tool_declare_endpoint(self, args: dict, working_set: list[dict]) -> str:
        """Sync validation-only variant (kept for callers without a DB session); the agent loop uses
        _declare_endpoint_now, which creates the stub inside the call (review C6)."""
        if getattr(self, "_project", None) is None:
            return "Error: no project context — endpoints can only be declared for project apps."
        method, path = str(args.get("method") or "").upper(), args.get("path")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not isinstance(path, str) or not path.startswith("/"):
            return "Error: method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'."
        self._declared.append({**args, "method": method})
        return (f"Declared {method} {path} as a stub route (returns 501 until implemented). "
                "Call it through the generated client once it exists, or fetch('/api/p/<slug>" + path + "').")

    def _tool_read_files(self, args: dict, working_set: list[dict]) -> str:
        paths = args.get("paths", [])
        if not paths:
            return "Error: 'paths' array is required."
        results = []
        for spec in paths:
            path, start, end = parse_read_spec(str(spec))
            f = next((f for f in working_set if f["path"] == path), None)
            if not f:
                results.append(f"Error: File '{path}' not found.")
                continue
            lines = f["content"].split("\n")
            lo, hi = (max(1, start), min(len(lines), end)) if start is not None else (1, len(lines))
            numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(lo, hi + 1)]
            body = "\n".join(numbered)
            if start is None and len(body) > READ_FILE_CHAR_CAP:
                keep = body[:READ_FILE_CHAR_CAP].rsplit("\n", 1)[0]
                body = (keep + f"\n… file has {len(lines)} lines; shown up to line {keep.count(chr(10)) + 1}. Read the rest with a range, "
                        f"e.g. \"{path}:{keep.count(chr(10)) + 2}-{min(len(lines), keep.count(chr(10)) + 160)}\" (definitions with lines are in the Project Index).")
            results.append(f"## {path}" + (f" (lines {lo}-{hi} of {len(lines)})" if start is not None else "") + "\n\n" + body)
        return "\n\n".join(results)

    def _tool_write_files(self, args: dict, working_set: list[dict]) -> str:
        files = args.get("files", [])
        if not files:
            return "Error: 'files' array is required."
        results = []
        for entry in files:
            path = entry.get("path", "")
            content = entry.get("content", "")
            if not path or not content:
                results.append("Error: 'path' and 'content' are required.")
                continue
            if is_kit_path(path):
                results.append(_KIT_READONLY.format(path=path))
                continue
            parsed_index = parse_tsx_file(content) if path.endswith((".tsx", ".ts", ".jsx", ".js")) else None
            existing = next((f for f in working_set if f["path"] == path), None)
            if existing:
                existing["content"] = content
                existing["parsed_index"] = parsed_index
                action = "Updated"
            else:
                working_set.append({"path": path, "content": content, "parsed_index": parsed_index})
                action = "Created"
            info = f"{action} {path} ({len(content)} chars)"
            if parsed_index and parsed_index.get("definitions"):
                names = [d["name"] for d in parsed_index["definitions"][:5]]
                info += f" — definitions: {', '.join(names)}"
            results.append(info)
        return "\n".join(results)

    def _tool_list_files(self, args: dict, working_set: list[dict]) -> str:
        if not working_set:
            return "No files in project."
        lines = []
        for f in working_set:
            size = len(f["content"].encode())
            line = f"- {f['path']} ({size} bytes)"
            idx = f.get("parsed_index")
            if idx:
                if idx.get("file_comment"):
                    line += f"\n  {idx['file_comment']}"
                defs = idx.get("definitions", [])
                if defs:
                    parts = [f"{d['name']}({d['kind']})" for d in defs[:8]]
                    line += f"\n  Definitions: {', '.join(parts)}"
            lines.append(line)
        return "\n".join(lines)

    def _tool_edit_files(self, args: dict, working_set: list[dict]) -> str:
        edits = args.get("edits", [])
        if not edits:
            return "Error: 'edits' array is required."
        results = []
        for edit in edits:
            path = edit.get("path", "")
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")
            if not path or not old_string:
                results.append("Error: 'path' and 'old_string' are required.")
                continue
            if is_kit_path(path):
                results.append(_KIT_READONLY.format(path=path))
                continue
            f = next((f for f in working_set if f["path"] == path), None)
            if not f:
                results.append(f"Error: File '{path}' not found.")
                continue
            if old_string not in f["content"]:
                fuzzy = _whitespace_insensitive_span(f["content"], old_string)
                if fuzzy is None:
                    results.append(f"Error: old_string not found in {path}.{_closest_lines(f['content'], old_string)}")
                    continue
                a, b = fuzzy  # the model's copy differs only in whitespace: patch the one real span
                lead = len(old_string) - len(old_string.lstrip())
                f["content"] = f["content"][:a] + (new_string.lstrip() if lead else new_string) + f["content"][b:]
                if path.endswith((".tsx", ".ts", ".jsx", ".js")):
                    f["parsed_index"] = parse_tsx_file(f["content"])
                results.append(f"Edited {path}: replaced {b - a} chars with {len(new_string)} chars (matched ignoring whitespace)")
                continue
            count = f["content"].count(old_string)
            if count > 1:
                results.append(f"Error: old_string found {count} times in {path}. Provide more context.")
                continue
            f["content"] = f["content"].replace(old_string, new_string, 1)
            if path.endswith((".tsx", ".ts", ".jsx", ".js")):
                f["parsed_index"] = parse_tsx_file(f["content"])
            results.append(f"Edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars")
        return "\n".join(results)

    def _tool_delete_file(self, args: dict, working_set: list[dict]) -> str:
        path = args.get("path", "")
        if is_kit_path(path):
            return _KIT_READONLY.format(path=path)
        idx = next((i for i, f in enumerate(working_set) if f["path"] == path), None)
        if idx is None:
            return f"Error: File '{path}' not found."
        working_set.pop(idx)
        return f"Deleted {path}"

    def _tool_search_files(self, args: dict, working_set: list[dict]) -> str:
        import re
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: 'pattern' is required."
        only = str(args.get("path") or args.get("path_pattern") or "").strip()
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        results = []
        for f in working_set:
            if (only and only not in f["path"]) or is_kit_path(f["path"]) and not only:
                continue
            lines = f["content"].split("\n")
            for i, line in enumerate(lines):
                if regex.search(line):
                    snippet_lines = []
                    if i > 0:
                        snippet_lines.append(f"  {i}: {lines[i-1]}")
                    snippet_lines.append(f"  {i+1}: {line}  <- match")
                    if i < len(lines) - 1:
                        snippet_lines.append(f"  {i+2}: {lines[i+1]}")
                    results.append(f"{f['path']}:{i+1}\n" + "\n".join(snippet_lines))
        if not results:
            return f"No matches for '{pattern}'."
        return f"Found {len(results)} match(es):\n\n" + "\n\n".join(results[:20])

    # ── System Prompt Building ────────────────────────────────────────

    @staticmethod
    def _trim_tool_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop large read-only tool results from earlier turns to keep context lean.

        Keeps write/edit/delete results (small) and the last 4 read results.
        Also protects any read whose file was edited in the next turn — the LLM
        needs that content to construct accurate edit_file old_string values.
        """
        _READ_TOOLS = {"read_files", "search_files", "list_files"}
        _WRITE_TOOLS = {"write_files", "edit_files"}
        _STUB = "[result trimmed — call again if needed]"
        _KEEP_RECENT = 4

        # Find read-tool result indices
        read_indices: list[int] = []
        for i, m in enumerate(messages):
            if m.get("role") == "tool" and m.get("name") in _READ_TOOLS:
                read_indices.append(i)

        if len(read_indices) <= _KEEP_RECENT:
            return messages

        # Protect reads that were followed by an edit of the same file
        protected: set[int] = set()
        for idx in read_indices:
            read_msg = messages[idx]
            read_path = ""
            # Extract the path from the read result content (starts with "## path")
            content = read_msg.get("content", "")
            if content.startswith("## "):
                read_path = content.split("\n")[0].removeprefix("## ").split(" :: ")[0].strip()

            if read_path:
                # Check if any subsequent tool message (before the next assistant msg) edits this file
                for j in range(idx + 1, min(idx + 6, len(messages))):
                    m2 = messages[j]
                    if m2.get("role") == "tool" and m2.get("name") in _WRITE_TOOLS:
                        # The edit result mentions the path
                        if read_path in m2.get("content", ""):
                            protected.add(idx)
                            break
                    if m2.get("role") == "assistant":
                        break  # different turn

        # Trim old reads that aren't protected and aren't in the last N
        to_trim = set(read_indices[:-_KEEP_RECENT]) - protected
        if not to_trim:
            return messages

        trimmed = []
        for i, m in enumerate(messages):
            if i in to_trim:
                trimmed.append({**m, "content": _STUB})
            else:
                trimmed.append(m)
        return trimmed

    def _build_agent_system_prompt(self, api_executions: list[ApiTestExecutionModel]) -> str:
        parts = [_AGENT_SYSTEM_PROMPT]
        if getattr(self, "_spec_summary", None):
            summary = self._spec_summary
            if getattr(self, "_digest", ""):  # the digest lists every route (with its fields), so drop the bare route lines
                summary = summary.split("\n- `api.", 1)[0]
            parts.append(summary)
        if getattr(self, "_digest", ""):
            parts.append(self._digest)
        for ex in api_executions:
            parts.append(self._api_execution_context(ex))
        return "\n\n".join(parts)

    # ── Context Building ─────────────────────────────────────────────

    def _build_project_index(self, working_set: list[dict]) -> str:
        """Build a compact pseudo-code index from parsed definitions.

        This is sent as the initial context instead of full file contents,
        so the LLM can reason about structure cheaply and pull only what it needs.
        """
        if not working_set:
            return "## Project\n\nEmpty project — no files yet."

        kit = [f for f in working_set if is_kit_path(f["path"])]
        lines = [f"## Project Index ({len(working_set)} files)\n"]
        if kit:
            lines.append(f"### kit/ ({len(kit)} files) — Studio UI kit, read-only; API in the system prompt\n")
        for f in working_set:
            if is_kit_path(f["path"]):
                continue
            size = len(f["content"].encode())
            lines.append(f"### {f['path']} ({size} bytes)")
            idx = f.get("parsed_index")
            if idx:
                if idx.get("file_comment"):
                    lines.append(idx["file_comment"])
                for d in idx.get("definitions", []):
                    exported = " [exported]" if d.get("exported") else ""
                    doc = f" — {d['doc']}" if d.get("doc") else ""
                    lines.append(f"- {d['name']} ({d['kind']}) lines {d['line']}-{d['end_line']}{exported}{doc}")
            else:
                lines.append(f"({f.get('file_type', 'unknown')} file)")
            lines.append("")
        return "\n".join(lines)

    def _api_execution_context(self, ex: ApiTestExecutionModel) -> str:
        """Render a captured API test execution as system-prompt context.

        Reads the persisted `response_summary` if available; falls back to
        live computation for old rows that pre-date the column. Either way,
        no raw body bytes go into the prompt.
        """
        label = ex.name or f"{ex.method} {ex.url}"
        lines = [
            f'## Attached Endpoint: "{label}"',
            "",
            "Use the **exact** url, method, headers, and body shape below in your generated `fetch()` call.",
            "If the schema doesn't tell you what you need (enum values, auth, date format, etc.), ask the user before guessing.",
            "",
            "### Request",
            "```typescript",
            f'const res = await fetch("{ex.url}", {{',
            f'  method: "{ex.method}",',
        ]

        headers = ex.request_headers or {}
        if headers:
            header_pairs = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in headers.items())
            lines.append(f"  headers: {{ {header_pairs} }},")

        if ex.request_body_text is not None and ex.method.upper() not in ("GET", "HEAD"):
            lines.append(f"  body: {json.dumps(ex.request_body_text)},")

        lines.append("});")
        lines.append("```")

        # ── Response info ────────────────────────────────────────────
        lines.append("")
        lines.append("### Response")
        lines.append(f"- **Status:** {ex.response_status}")
        if ex.response_content_type:
            lines.append(f"- **Content-Type:** `{ex.response_content_type}`")
        lines.append(f"- **Size:** {ex.response_size} bytes")

        summary = ex.response_summary
        if summary is None and ex.response_body_b64:
            # Back-compat: old rows captured before response_summary existed.
            # Compute live (and cheaply — same logic, just not persisted).
            summary = summarize_response(
                response_body_b64=ex.response_body_b64,
                content_type=ex.response_content_type,
                response_truncated=ex.response_truncated,
                response_headers=ex.response_headers,
            )

        _format_response_summary(summary, lines)
        return "\n".join(lines)

    # ── File Resolution ───────────────────────────────────────────────

    async def _resolve_current_files(
        self, request: AppBuilderChatRequest
    ) -> tuple[list[dict[str, str]], str | None]:
        """Resolve the current set of files for an app.

        Returns (files, warning). Warning is set if an app_id was provided
        but no files could be loaded — so the caller can inform the user.
        """

        # Get files from the current version
        if request.app_id and request.current_version_id:
            try:
                files = await self._app_service.get_version_files(
                    request.app_id, request.current_version_id
                )
                if files:
                    return [{"path": f["path"], "content": f["content"], "parsed_index": f.get("parsed_index")} for f in files], None
            except Exception:
                logger.warning(
                    "Failed to resolve files for app %s version %s",
                    request.app_id, request.current_version_id, exc_info=True,
                )

        # No version specified — try loading from app detail
        if request.app_id:
            try:
                app_detail = await self._app_service.get_app(request.app_id)
                if app_detail and app_detail.files:
                    return [{"path": f.path, "content": f.content, "parsed_index": f.parsed_index} for f in app_detail.files], None
                if app_detail and app_detail.source_code:
                    # Legacy/saved drafts may have entry source without a file
                    # version yet. Refinement must not start over in that case.
                    return [{"path": "App.tsx", "content": app_detail.source_code, "parsed_index": None}], None
            except Exception:
                logger.warning("Failed to resolve files for app %s", request.app_id, exc_info=True)

        # Neither the requested version nor the app's current version produced files. A follow-up message on an app that HAS saved
        # versions must not start over: fall back to the newest version that holds files. An app with no saved version at all
        # (just created, routes attached, nothing built yet) has nothing to find, so it is a normal first build, not an error.
        had_history = bool(request.current_version_id)
        if request.app_id:
            try:
                from sqlalchemy import select

                from ..db.models import AppModel, AppVersionModel
                session = self._app_service._session
                row = await session.get(AppModel, request.app_id)
                had_history = had_history or bool(row is not None and row.current_version_id)
                res = await session.execute(
                    select(AppVersionModel.id).where(AppVersionModel.app_id == request.app_id).order_by(AppVersionModel.id.desc()).limit(8))
                version_ids = [v for (v,) in res.all()]
                had_history = had_history or bool(version_ids)
                for vid in version_ids:
                    files = await self._app_service.get_version_files(request.app_id, vid)
                    if files:
                        return [{"path": f["path"], "content": f["content"], "parsed_index": f.get("parsed_index")} for f in files], None
            except Exception:
                logger.warning("App builder: version fallback failed for app %s", request.app_id, exc_info=True)

        warning = None
        if request.app_id and had_history:
            warning = "I couldn't find the existing files for this app, so I'll start fresh."
            logger.warning("App builder: no files found for app %s, starting from scratch", request.app_id)

        return [], warning
