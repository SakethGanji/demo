"""One router for every optional language-model call: Vela's question box, the wording of Vela's findings, Autopilot's
decisions and its synthetic rows. The numbers always come from the workbench; a model only words or chooses.

Order (F5): the Anthropic Python SDK when ANTHROPIC_API_KEY is set (model `claude-sonnet-5-5`, one pooled async client,
no hidden retries), then the `claude` CLI (`claude -p … --output-format json`, an asyncio subprocess that is killed on
its timeout), then nothing: the caller gets `None` and shows its computed text. Every reply carries its *decider*
(`anthropic-sdk` | `claude-cli` | `computed`), the latency and each attempt.

- Asynchronous: `ask_json_async` awaits network or subprocess I/O on the event loop; no request thread is held, so
  `/api/health` and every other route keep answering while asks are in flight. `ask_json` is the blocking wrapper for
  code that runs in its own thread (Autopilot).
- Cached by (prompt hash, facts hash, model): a repeated question about the same facts is answered from memory in
  microseconds; the same question about changed facts is asked again. Identical asks in flight share one call
  (single-flight). Only model answers are cached, never a fallback.
- Timeouts per path and in total, configurable: environment defaults, overridden at runtime by `PUT /api/llm/settings`
  (stored in `data/llm_settings.json`) and per call.
- Number guard: `unsupported_numbers(answer, facts)` lists numbers in a reply that the facts do not contain (Vela's
  wording keeps a reworded text only if every computed number survives; see app/vela/wording.py).

Environment: ENSEMBLE_VELA_LLM = auto (default) | off · ENSEMBLE_VELA_MODEL (claude-sonnet-5-5) · ANTHROPIC_API_KEY ·
ENSEMBLE_ANTHROPIC_BASE_URL (optional gateway for the SDK) · ENSEMBLE_CLAUDE_BIN (claude) · ENSEMBLE_LLM_SDK_TIMEOUT (20 s)
· ENSEMBLE_LLM_CLI_TIMEOUT (45 s) · ENSEMBLE_LLM_TIMEOUT (total budget, 60 s) · ENSEMBLE_LLM_CACHE_TTL (3600 s).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

SDK, CLI, COMPUTED = "anthropic-sdk", "claude-cli", "computed"
SOURCES = {SDK: "Anthropic SDK", CLI: "Claude CLI", COMPUTED: "computed (no model)",
           "anthropic-api": "Anthropic API"}  # the last: records written before F5
DEFAULT_MODEL = "claude-sonnet-5-5"
SYSTEM = ("You are Vela, the analyst assistant inside a bank's AutoML workbench. Answer from the facts given, keep every "
          "number exactly as written, and reply with the JSON the user message asks for, nothing else.")
CLI_FLAGS = ["--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
             "--disable-slash-commands", "--system-prompt", SYSTEM]
NUM = re.compile(r"(?<![\w.])[-−]?\d+(?:[.,]\d+)*%?")

_lock = threading.Lock()
_settings_path: Path | None = None
_overrides: dict[str, Any] = {}
_cache: OrderedDict[str, tuple[float, dict[str, Any], dict[str, Any]]] = OrderedDict()
_stats = {"calls": 0, "cache_hits": 0, "shared_in_flight": 0, "by_source": {SDK: 0, CLI: 0, COMPUTED: 0}}
_latency: dict[str, deque] = {SDK: deque(maxlen=200), CLI: deque(maxlen=200), COMPUTED: deque(maxlen=200)}
_inflight: dict[tuple[int, str], asyncio.Future] = {}
_clients: dict[int, Any] = {}

# ----------------------------------------------------------------------------------------------------------- settings

EDITABLE = {"enabled": bool, "sdk_timeout": float, "cli_timeout": float, "total_timeout": float, "cache_ttl": float,
            "model": str, "use_sdk": bool, "use_cli": bool}


def configure(data_dir: Path) -> None:
    """Load runtime overrides from data/llm_settings.json (called once at app start)."""
    global _settings_path
    _settings_path = Path(data_dir) / "llm_settings.json"
    try:
        _overrides.clear()
        _overrides.update(json.loads(_settings_path.read_text()))
    except (FileNotFoundError, ValueError):
        pass


def settings() -> dict[str, Any]:
    env = os.environ
    s = {"enabled": env.get("ENSEMBLE_VELA_LLM", "auto").lower() != "off",
         "model": env.get("ENSEMBLE_VELA_MODEL", DEFAULT_MODEL),
         "sdk_timeout": float(env.get("ENSEMBLE_LLM_SDK_TIMEOUT", "20")),
         "cli_timeout": float(env.get("ENSEMBLE_LLM_CLI_TIMEOUT", env.get("ENSEMBLE_VELA_TIMEOUT", "45"))),
         "total_timeout": float(env.get("ENSEMBLE_LLM_TIMEOUT", "60")),
         "cache_ttl": float(env.get("ENSEMBLE_LLM_CACHE_TTL", "3600")),
         "use_sdk": True, "use_cli": True}
    s.update({k: v for k, v in _overrides.items() if k in EDITABLE})
    return s


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in EDITABLE:
            raise ValueError(f"unknown setting '{k}' (editable: {', '.join(EDITABLE)})")
        if v is None:
            _overrides.pop(k, None)
            continue
        v = EDITABLE[k](v)
        if k.endswith("timeout") and not (0.1 <= v <= 600):
            raise ValueError(f"{k} must be between 0.1 and 600 seconds")
        if k == "cache_ttl" and v < 0:
            raise ValueError("cache_ttl must be ≥ 0")
        clean[k] = v
    _overrides.update(clean)
    if _settings_path:
        _settings_path.parent.mkdir(parents=True, exist_ok=True)
        _settings_path.write_text(json.dumps(_overrides, indent=1))
    return settings()


def enabled() -> bool:
    return bool(settings()["enabled"])


def sdk_available() -> tuple[bool, str]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY is not set"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the anthropic package is not installed"
    return True, "ANTHROPIC_API_KEY is set"


def cli_path() -> str | None:
    exe = os.environ.get("ENSEMBLE_CLAUDE_BIN", "claude")
    return exe if os.path.sep in exe and os.path.exists(exe) else shutil.which(exe)


def route() -> list[str]:
    """The paths a call would try, in order."""
    s = settings()
    if not s["enabled"]:
        return []
    out = []
    if s["use_sdk"] and sdk_available()[0]:
        out.append(SDK)
    if s["use_cli"] and cli_path():
        out.append(CLI)
    return out


def _pq(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def status() -> dict[str, Any]:
    ok, why = sdk_available()
    with _lock:
        lat = {src: {"n": len(d), "p50_s": _pq(list(d), 0.5), "p95_s": _pq(list(d), 0.95), "last_s": d[-1] if d else None}
               for src, d in _latency.items()}
        stats = json.loads(json.dumps(_stats))
        size = len(_cache)
    return {"settings": settings(), "route": route(), "labels": SOURCES,
            "sdk": {"available": ok, "why": why, "base_url": os.environ.get("ENSEMBLE_ANTHROPIC_BASE_URL") or None},
            "cli": {"path": cli_path()}, "latency": lat, "stats": stats | {"cache_size": size}}


def clear_cache() -> int:
    with _lock:
        n = len(_cache)
        _cache.clear()
    return n


# ---------------------------------------------------------------------------------------------------------- helpers

def _canon(x: Any) -> str:
    return x if isinstance(x, str) else json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def cache_key(prompt: str, facts: Any = None, model: str = DEFAULT_MODEL) -> tuple[str, str, str]:
    """(key, prompt hash, facts hash): sha256 of the prompt and of the canonical facts, with the model."""
    ph = hashlib.sha256(prompt.encode()).hexdigest()
    fh = hashlib.sha256(_canon(facts).encode()).hexdigest() if facts is not None else "-"
    return f"{model}:{ph[:20]}:{fh[:20]}", ph, fh


def parse_json_object(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", (text or "").strip(), re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("the reply is not a JSON object")
    return obj


def numbers(text: str) -> list[str]:
    return [n.replace("−", "-").replace(",", "").rstrip("%") for n in NUM.findall(text or "")]


METRIC_WORDS = ("auc", "accuracy", "recall", "precision", "log loss", "rmse", "mae", "r2", "f1", "cost")
GAIN_WORDS = re.compile(r"(?i)\b(by|gain\w*|improv\w*|rais\w*|lift\w*|increas\w*|decreas\w*|drop\w*|fell|fall\w*|rose|Δ|delta|"
                        r"points?|worse|better)\b")
GAIN_BEFORE = re.compile(r"(?i)(\bby|\bgain(ed)?( of)?|\bimprov\w*( of)?|\blift( of)?|Δ|\bdelta|\+)\s*[−-]?$")
GAIN_KEYS = re.compile(r"(?i)(^|_)(delta|gain|diff|change|excess|ci|lift|optimism|gap|lo|hi|lower|upper|next_doubling|raw|random)($|_)")


def _variants(v: float, pct: bool) -> set[str]:
    out = set()
    for w in (v, abs(v)):
        out |= {f"{w:.0f}", f"{w:.1f}", f"{w:.2f}", f"{w:.3f}", f"{w:.4f}"}
        if pct:
            out |= {f"{100 * w:.0f}", f"{100 * w:.1f}", f"{100 * w:.2f}"}
    return out


POS_DIR = re.compile(r"(?i)\b(improv\w*|rais\w*|rose|increas\w*|gain\w*|lift\w*|better|up|higher|climb\w*|add\w*)\b")
NEG_DIR = re.compile(r"(?i)\b(fell|fall\w*|drop\w*|decreas\w*|declin\w*|worse|down|lower|lost|los\w*|cost|reduc\w*|hurt\w*)\b")
BEFORE_KEYS = ("parent", "with", "before", "current", "served", "old", "start", "baseline")
AFTER_KEYS = ("child", "without", "after", "proposed", "new", "end", "candidate")
METRIC_KEY = re.compile(r"(auc|roc|accuracy|acc$|recall|precision|log_?loss|rmse|mae|r2|f1|cost)")


def _metric_of_key(key: str) -> str | None:
    k = key.lower()
    if re.search(r"auc|roc", k):
        return "auc"
    for w in ("accuracy", "recall", "precision", "rmse", "mae", "r2", "f1", "cost"):
        if w in k:
            return w
    if re.search(r"log_?loss", k):
        return "log loss"
    return None


def _pairs(facts: Any) -> list[tuple[set[str], set[str]]]:
    """(before, after) value variants of every object holding a before key and an after key (parent → child, …)."""
    out: list[tuple[set[str], set[str]]] = []

    def v(x: float) -> set[str]:
        return {n.rstrip("0").rstrip(".") if "." in n else n for n in _variants(float(x), False)}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            nums = {k.lower(): val for k, val in x.items() if isinstance(val, (int, float)) and not isinstance(val, bool)}
            b = next((nums[k] for k in BEFORE_KEYS if k in nums), None)
            a = next((nums[k] for k in AFTER_KEYS if k in nums), None)
            if b is not None and a is not None:
                out.append((v(b), v(a)))
            for val in x.values():
                walk(val)
        elif isinstance(x, (list, tuple)):
            for val in x:
                walk(val)

    walk(facts)
    return out


def _fact_index(facts: Any) -> list[tuple[str, str, set[str]]]:
    """(key path, the words around it — the keys and string values of its object —, its rendered variants) per number."""
    out: list[tuple[str, str, set[str]]] = []

    def walk(x: Any, path: str, ctx: str) -> None:
        if isinstance(x, dict):
            here = " ".join([*map(str, x.keys()), *[v for v in x.values() if isinstance(v, str)]]).lower()
            for k, v in x.items():
                walk(v, f"{path}.{k}", here)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v, path, ctx)
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, (int, float)):
            v = float(x)
            key = path.rsplit(".", 1)[-1].lower()
            pct = 0 <= abs(v) <= 1 and bool(re.search(r"(share|rate|pct|percent|recall|precision|accuracy|prob|catch|decline|fraction|ratio|error)", key + " " + ctx)) \
                or abs(v) <= 1 and not re.search(r"(auc|score|roc)", key)
            out.append((path.lower(), ctx, {n.rstrip("0").rstrip(".") if "." in n else n for n in {str(x), *_variants(v, pct)}} | {str(x)}))
        elif isinstance(x, str):
            for n in numbers(x):
                try:
                    v = float(n)
                except ValueError:
                    continue
                out.append((path.lower(), (ctx + " " + x).lower(), {n, *_variants(v, "%" in x)}))

    if isinstance(facts, str):
        walk({"text": facts}, "", "")
    else:
        walk(facts, "", "")
    return out


def _fact_values(facts: Any) -> list[float | None]:
    """The numeric value of each _fact_index entry, in the same order (None for numbers quoted inside text)."""
    out: list[float | None] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, str):
            for n in numbers(x):
                try:
                    float(n)
                except ValueError:
                    continue
                out.append(None)

    walk({"text": facts} if isinstance(facts, str) else facts)
    return out


def unsupported_numbers(answer: str, facts: Any) -> list[str]:
    """Numbers in the answer that the facts do not support, after rounding either side to the answer's precision.

    Presence is not enough (A1 #17): a number written next to a metric word (AUC, accuracy, recall, …) must come from a
    fact whose object names that metric; a number written as a change ('by', 'gain', 'raised', 'points', Δ) must come
    from a change field (delta, gain, CI, …); a percentage must come from a share-like fact (share, rate, accuracy, …),
    not from an AUC × 100. Small counting words (0–10) pass only as plain integers away from a metric word or %."""
    idx = _fact_index(facts)
    vals = _fact_values(facts)
    pairs = _pairs(facts)
    norm = lambda n: n.rstrip("0").rstrip(".") if "." in n else n  # noqa: E731
    out = []
    text = answer or ""
    # A2 M8: "from A to B" must not reverse a before → after pair of the facts, and its direction word must match B − A
    for fm in re.finditer(r"(?i)(\w+)\s+(?:\w+\s+){0,3}?from\s+([−-]?\d[\d,]*\.?\d*)\s*%?\s+to\s+([−-]?\d[\d,]*\.?\d*)", text):
        a_, b_ = (norm(x.replace("−", "-").replace(",", "")) for x in (fm.group(2), fm.group(3)))
        try:
            fa, fb = float(a_), float(b_)
        except ValueError:
            continue
        win = text[max(0, fm.start() - 30):fm.start(2)]
        up, down = bool(POS_DIR.search(win)), bool(NEG_DIR.search(win))
        reversed_ = any(a_ in aft and b_ in bef for bef, aft in pairs) and not any(a_ in bef and b_ in aft for bef, aft in pairs)
        if reversed_ or (up and not down and fb < fa) or (down and not up and fb > fa):
            out += [a_, b_]
    # A3: "B, up from A" / "B, down from A": the direction word against B − A, and against the facts' before → after pair
    for fm in re.finditer(r"(?i)([−-]?\d[\d,]*\.?\d*)\s*%?[^\d.;]{0,30}?\b(up|down|higher|lower|rising|falling|rose|fell)\s+from\s+"
                          r"([−-]?\d[\d,]*\.?\d*)", text):
        b_, a_ = (norm(x.replace("−", "-").replace(",", "")) for x in (fm.group(1), fm.group(3)))
        try:
            fa, fb = float(a_), float(b_)
        except ValueError:
            continue
        upw = fm.group(2).lower() in ("up", "higher", "rising", "rose")
        reversed_ = any(a_ in aft and b_ in bef for bef, aft in pairs) and not any(a_ in bef and b_ in aft for bef, aft in pairs)
        if reversed_ or (upw and fb < fa) or (not upw and fb > fa):
            out += [a_, b_]
    for m in NUM.finditer(text):
        raw = m.group(0)
        n = raw.replace("−", "-").replace(",", "").rstrip("%")
        pct = raw.endswith("%") or text[m.end():m.end() + 9].lstrip().startswith(("%", "percent", "per cent"))
        try:
            v = float(n)
        except ValueError:
            continue
        around = text[max(0, m.start() - 40):m.end() + 30].lower()
        # A2 M8: the metric word nearest the number (not the first in a fixed list)
        hits = [(min(abs(mm.start() - (m.start() - max(0, m.start() - 40))), abs(mm.end() - (m.start() - max(0, m.start() - 40)))), w)
                for w in METRIC_WORDS for mm in re.finditer(rf"\b{re.escape(w)}\b", around)]
        metric = min(hits)[1] if hits else None
        gain = bool(GAIN_BEFORE.search(text[max(0, m.start() - 20):m.start()]))
        before_txt = text[max(0, m.start() - 30):m.start()]
        signed = raw.startswith(("-", "−", "+")) or text[max(0, m.start() - 1):m.start()] in ("-", "−", "+")
        neg = raw.startswith(("-", "−")) or text[max(0, m.start() - 1):m.start()] in ("-", "−")
        up, down = bool(POS_DIR.search(before_txt)), bool(NEG_DIR.search(before_txt))
        if v.is_integer() and 0 <= v <= 10 and not pct and not metric and "." not in n:
            continue
        cands = {n, n.lstrip("-"), norm(n), norm(n.lstrip("-"))}
        ok = False
        for (path, ctx, vs), fv in zip(idx, vals):
            if not (cands & vs):
                continue
            key = path.rsplit(".", 1)[-1]
            change = bool(GAIN_KEYS.search(key)) or bool(gain)
            if fv is not None:  # A2 M8: at the precision the answer writes ("0.80" is not 0.85 rounded to one place)
                d_ = len(n.split(".")[1]) if "." in n else 0
                if not any(abs(round(x_, d_) - abs(v)) < 10 ** -(d_ + 3) for x_ in (abs(fv), 100 * abs(fv))):
                    continue
            if fv is not None and fv != 0:
                # A2 M8: keep signs. A signed number must have the fact's sign; an unsigned change must agree with its
                # direction word (improved / rose vs fell / dropped)
                if signed and neg != (fv < 0):
                    continue
                if change and not signed and (up != down) and ((up and fv < 0) or (down and fv > 0)):
                    continue
            km = _metric_of_key(key)
            if metric and km and km != metric:  # the fact's own key names another metric
                continue
            if pct and not (re.search(r"(share|rate|pct|percent|recall|precision|accuracy|prob|catch|decline|fraction|error)", key + " " + ctx)
                            or n in vs and not any(a == n for a in _variants(float(n) / 100, True) - {n})):
                continue
            if pct and re.search(r"(auc|roc)", key) and not re.search(r"(share|rate|percent)", key):
                continue
            if metric and metric not in (path + " " + ctx) and not (metric == "cost" and "1,000" in ctx):
                continue
            if gain and not GAIN_KEYS.search(key) and not (key == "text" and re.search(r"(Δ|delta|gain|[+−-]\d)", ctx)):
                continue
            ok = True
            break
        if not ok:
            out.append(n)
    return sorted(set(out))


def _record(source: str, seconds: float) -> None:
    with _lock:
        _stats["by_source"][source] = _stats["by_source"].get(source, 0) + 1
        _latency[source].append(round(seconds, 3))


# ------------------------------------------------------------------------------------------------------------ paths

def _sdk_client():
    """One AsyncAnthropic per event loop (its connection pool belongs to that loop), no SDK retries: the router decides."""
    import anthropic

    loop = asyncio.get_running_loop()
    for k in [k for k, v in _clients.items() if v._ensemble_loop.is_closed()]:  # loops of finished asyncio.run calls
        _clients.pop(k, None)
    c = _clients.get(id(loop))
    if c is None or getattr(c, "_ensemble_loop", None) is not loop:
        c = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"),
                                     base_url=os.environ.get("ENSEMBLE_ANTHROPIC_BASE_URL") or None, max_retries=0)
        c._ensemble_loop = loop
        _clients[id(loop)] = c
    return c


async def _via_sdk(prompt: str, model: str, timeout: float, max_tokens: int) -> tuple[dict[str, Any], dict[str, Any]]:
    import anthropic

    client = _sdk_client()
    try:
        msg = await asyncio.wait_for(client.beta.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM, messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            timeout=timeout), timeout)
    except anthropic.APITimeoutError as e:
        raise TimeoutError(f"timed out after {timeout:g} s") from e
    if msg.stop_reason == "refusal":
        raise RuntimeError("the model declined (stop_reason refusal)")
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    usage = getattr(msg, "usage", None)
    return parse_json_object(text), {"model": msg.model, "stop_reason": msg.stop_reason,
                                     "input_tokens": getattr(usage, "input_tokens", None),
                                     "output_tokens": getattr(usage, "output_tokens", None)}


async def _via_cli(prompt: str, model: str, timeout: float, max_tokens: int) -> tuple[dict[str, Any], dict[str, Any]]:
    path = cli_path()
    if not path:
        raise FileNotFoundError(f"`{os.environ.get('ENSEMBLE_CLAUDE_BIN', 'claude')}` is not on PATH")
    proc = await asyncio.create_subprocess_exec(path, "-p", "--model", model, *CLI_FLAGS, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the CLI and anything it started
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        raise TimeoutError(f"timed out after {timeout:g} s")
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {(err or out).decode(errors='replace')[-300:]}")
    outer = json.loads(out)
    if outer.get("is_error"):
        raise RuntimeError(str(outer.get("result"))[:300])
    meta = {"model": next(iter(outer.get("modelUsage") or {}), model), "cost_usd": outer.get("total_cost_usd"),
            "api_ms": outer.get("duration_api_ms")}
    return parse_json_object(outer.get("result") or ""), meta


PATHS = {SDK: _via_sdk, CLI: _via_cli}


# ------------------------------------------------------------------------------------------------------------ router

async def _route(prompt: str, s: dict[str, Any], timeout: float | None, max_tokens: int,
                 path_timeout: float | None = None) -> tuple[dict | None, dict]:
    attempts: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    budget = float(timeout or s["total_timeout"])
    paths = route()
    for source in (SDK, CLI) if s["enabled"] else ():
        if source not in paths:  # say why a path is not tried, so the UI can show the whole route
            why = "off in Settings" if not s["use_sdk" if source == SDK else "use_cli"] else \
                sdk_available()[1] if source == SDK else "the claude CLI is not on PATH"
            attempts.append({"source": source, "ok": False, "skipped": True, "seconds": 0.0, "error": why})
            continue
        left = budget - (time.perf_counter() - t0)
        per = min(path_timeout or (s["sdk_timeout"] if source == SDK else s["cli_timeout"]), left)
        if per < 0.1:
            attempts.append({"source": source, "ok": False, "error": "skipped: the total time budget is spent", "seconds": 0.0})
            continue
        a0 = time.perf_counter()
        try:
            obj, meta = await PATHS[source](prompt, s["model"], per, max_tokens)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
                e = TimeoutError(str(e) or f"timed out after {per:g} s")
            msg = str(e) if isinstance(e, TimeoutError) else f"{type(e).__name__}: {e}"
            attempts.append({"source": source, "ok": False, "error": msg[:300], "timeout_s": round(per, 2),
                             "timed_out": isinstance(e, TimeoutError), "seconds": round(time.perf_counter() - a0, 3)})
            continue
        sec = time.perf_counter() - a0
        attempts.append({"source": source, "ok": True, "seconds": round(sec, 3), "timeout_s": round(per, 2)})
        _record(source, sec)
        return obj, {"source": source, "decider": SOURCES[source], "model": meta.get("model"), "attempts": attempts,
                     "seconds": round(time.perf_counter() - t0, 3), "model_seconds": round(sec, 3),
                     "cost_usd": meta.get("cost_usd"), "usage": {k: meta.get(k) for k in ("input_tokens", "output_tokens") if meta.get(k) is not None}}
    sec = time.perf_counter() - t0
    _record(COMPUTED, sec)
    return None, {"source": COMPUTED, "decider": SOURCES[COMPUTED], "model": None, "attempts": attempts,
                  "seconds": round(sec, 3), "disabled": not s["enabled"],
                  "reason": ("the language model is off (ENSEMBLE_VELA_LLM=off or Settings)" if not s["enabled"] else
                             "no path available (no ANTHROPIC_API_KEY and no claude CLI)" if not paths else
                             "every path failed or timed out")}


async def ask_json_async(prompt: str, *, facts: Any = None, timeout: float | None = None, max_tokens: int = 1500,
                         use_cache: bool = True, path_timeout: float | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(the reply as a JSON object or None, meta: source, decider, model, seconds, attempts, cached, key, …)."""
    s = settings()
    key, ph, fh = cache_key(prompt, facts, s["model"])
    keys = {"key": key, "prompt_sha256": ph[:16], "facts_sha256": fh[:16] if fh != "-" else None}
    t0 = time.perf_counter()
    with _lock:
        _stats["calls"] += 1
        hit = _cache.get(key) if use_cache and s["cache_ttl"] > 0 else None
        if hit and time.time() - hit[0] <= s["cache_ttl"]:
            _cache.move_to_end(key)
            _stats["cache_hits"] += 1
            obj, meta = hit[1], dict(hit[2])
            return json.loads(json.dumps(obj)), meta | keys | {"cached": True, "cached_at": hit[0],
                                                               "original_seconds": meta.get("seconds"),
                                                               "seconds": round(time.perf_counter() - t0, 6)}
    loop = asyncio.get_running_loop()
    fk = (id(loop), key)
    fut = _inflight.get(fk) if use_cache else None
    if fut is not None:  # an identical ask is already running: wait for it
        with _lock:
            _stats["shared_in_flight"] += 1
        obj, meta = await asyncio.shield(fut)
        return (json.loads(json.dumps(obj)) if obj else None), dict(meta) | keys | {"cached": False, "shared": True,
                                                                                    "seconds": round(time.perf_counter() - t0, 3)}
    fut = loop.create_future()
    if use_cache:
        _inflight[fk] = fut
    try:
        obj, meta = await _route(prompt, s, timeout, max_tokens, path_timeout)
        if obj is not None and use_cache and s["cache_ttl"] > 0:
            with _lock:
                _cache[key] = (time.time(), obj, meta)
                while len(_cache) > 512:
                    _cache.popitem(last=False)
        if not fut.done():
            fut.set_result((obj, meta))
        return obj, dict(meta) | keys | {"cached": False}
    except BaseException as e:
        if not fut.done():
            fut.set_exception(e)
            fut.exception()  # mark retrieved
        raise
    finally:
        _inflight.pop(fk, None)


def warm() -> None:
    """Import the SDK ahead of the first ask (≈ 1 s on a cold start), in a daemon thread; nothing else is sent."""
    if sdk_available()[0]:
        threading.Thread(target=lambda: __import__("anthropic"), daemon=True).start()


def ask_json(prompt: str, timeout: float | None = None, *, facts: Any = None, max_tokens: int = 1500
             ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Blocking wrapper for code running in its own thread (Autopilot). Never call it on the event loop."""
    return asyncio.run(ask_json_async(prompt, facts=facts, timeout=timeout, max_tokens=max_tokens))
