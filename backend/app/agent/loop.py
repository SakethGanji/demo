"""G10 · the Vela agent's loop: plan → call a tool → read the result → continue, within a step and a time budget.

Deciders, in order (`auto`):
- **Anthropic SDK, native tool use** when ANTHROPIC_API_KEY is set (`claude-sonnet-5-5`, one tool call per turn);
- **claude CLI, JSON-action prompting** through the F5 router otherwise: the prompt holds the tool catalog and the
  steps so far, the reply is one JSON object `{tool, args, why}` or `{answer}`, validated against the tool's schema;
- **none**: the agent says so and offers the one-click UI flow that matches the request.
`stub` is a deterministic planner for tests and demos without a model (covers the three acceptance scenarios).

Safety: read tools run at once; write tools wait for the user's one-click confirmation (the card shows the exact call).
Approve and promote are not tools. Every call (and every declined one) goes to the registry's audit log as
"Vela agent on behalf of <user>". Every final answer goes through the F5 number guard against the tool results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
import jsonschema

from .. import llm
from . import tools as T

log = logging.getLogger(__name__)

MAX_STEPS = 24
MAX_SECONDS = 1800
SDK_MODEL = "claude-sonnet-5-5"
TERMINAL = ("done", "stopped", "failed", "no_model")

SYSTEM = """You are the Vela agent inside ENSEMBLE, a bank's AutoML workbench built on AutoGluon. You do the user's task by \
calling the workbench's own tools, one call per turn, reading each result before the next call.
Rules:
- Tools marked [write] change state; each pauses for the user's one-click confirmation. Call them when the task needs them.
- Approving a version and promoting it to Production are human decisions (four-eyes): never attempt them. You can request approval.
- After starting a run (train_from_intake, start_run, apply_gym_patch, split_twin), call watch_run on it before reading results.
- Do not repeat a call that already succeeded with the same arguments. If a tool fails, read the error and adjust, or explain.
- The final answer: plain text, at most 120 words, numbers copied exactly from tool results (no differences, ratios or \
re-rounding you compute yourself; name the result instead). Mention the run or version ids you created."""

JSON_RULES = """Reply with exactly one JSON object and nothing else:
{"tool": "<tool name>", "args": {...}, "why": "<one short sentence>"} to call a tool, or
{"answer": "<the final answer to the user>"} when the task is done or cannot go further."""

OFFERS = [  # the one-click UI flows offered when no model is available
    (("train", "model", "build", "fit"), "Open Build (Smart Intake)", "#/build"),
    (("doctor", "diagnos", "why is", "explain"), "Open the run's Explain → Model Doctor", "#/explain"),
    (("declin", "reason", "counterfactual", "recourse", "applicant"), "Open Registry → Try It (reason codes, recourse)", "#/registry"),
    (("score", "batch", "drift", "monitor"), "Open Registry → Batch & Drift", "#/registry"),
    (("end to end", "autopilot", "for me"), "Run it end to end (Autopilot)", "#/vela?mode=auto"),
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def deciders() -> dict[str, Any]:
    ok, why = llm.sdk_available()
    cli = llm.cli_path()
    s = llm.settings()
    order = []
    if s["enabled"] and s["use_sdk"] and ok:
        order.append("anthropic-sdk")
    if s["enabled"] and s["use_cli"] and cli:
        order.append("claude-cli")
    return {"route": order, "sdk": {"available": ok, "why": why}, "cli": {"path": cli},
            "enabled": s["enabled"], "forced": os.environ.get("ENSEMBLE_AGENT_DECIDER") or None}


def pick_decider(pref: str | None) -> str:
    pref = (pref or os.environ.get("ENSEMBLE_AGENT_DECIDER") or "auto").lower()
    if pref in ("stub", "none"):
        return pref
    route = deciders()["route"]
    if pref in ("anthropic-sdk", "sdk") and "anthropic-sdk" in route:
        return "anthropic-sdk"
    if pref in ("claude-cli", "cli") and "claude-cli" in route:
        return "claude-cli"
    return route[0] if route else "none"


# ----------------------------------------------------------------------------------------------------------- helpers

def _dump(x: Any, cap: int) -> str:
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= cap else s[:cap] + "…(truncated)"


def facts_of(sess: dict[str, Any]) -> list[Any]:
    """What the number guard checks an answer against: the user's words and every tool call and result."""
    out: list[Any] = []
    for it in sess["timeline"]:
        if it["type"] == "user":
            out.append(it["text"])
        elif it["type"] == "step":
            out.append({"args": it.get("args"), "result": it.get("result"), "error": it.get("error")})
    return out


def transcript(sess: dict[str, Any]) -> str:
    steps = [i for i in sess["timeline"] if i["type"] == "step"]
    old = len(steps) - 10
    lines, k = [], 0
    for it in sess["timeline"]:
        if it["type"] == "user":
            lines.append(f"USER: {it['text']}")
        elif it["type"] == "answer":
            lines.append(f"YOU ANSWERED: {it['text']}")
        elif it["type"] == "step":
            k += 1
            cap = 600 if k <= old else 3000
            head = f"STEP {it['n']}: {it['tool']}({_dump(it.get('args') or {}, 800)})"
            if it["status"] == "done":
                lines.append(f"{head}\n  → {_dump(it.get('result'), cap)}")
            elif it["status"] == "declined":
                lines.append(f"{head}\n  → the user declined this call.")
            elif it["status"] in ("error", "invalid"):
                lines.append(f"{head}\n  → ERROR: {it.get('error')}")
            else:
                lines.append(f"{head}\n  → {it['status']}")
    return "\n".join(lines)


def _offers(text: str) -> list[dict[str, str]]:
    t = text.lower()
    hits = [{"label": label, "href": href} for keys, label, href in OFFERS if any(k in t for k in keys)]
    return hits or [{"label": "Open Build (Smart Intake)", "href": "#/build"}, {"label": "Run it end to end (Autopilot)", "href": "#/vela?mode=auto"}]


# ---------------------------------------------------------------------------------------------------------- deciders

async def decide_json(sess: dict[str, Any], extra: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """JSON-action prompting through the F5 router (the claude CLI when there is no key)."""
    b = sess["budget"]
    prompt = (f"{SYSTEM}\n\n{JSON_RULES}\n\nTOOLS:\n{T.catalog_text()}\n\nTHE USER IS: {sess['user']}\n\nSO FAR:\n"
              f"{transcript(sess)}\n\nBudget left: {b['max_steps'] - b['steps']} tool calls, "
              f"{max(0, int(b['max_seconds'] - b['seconds']))} s.{extra}\nYour JSON:")
    obj, meta = await llm.ask_json_async(prompt, use_cache=False, max_tokens=2500)
    return obj, meta


def sdk_messages(sess: dict[str, Any]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []

    def add(role: str, block: dict[str, Any]) -> None:
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"].append(block)
        else:
            msgs.append({"role": role, "content": [block]})

    for it in sess["timeline"]:
        if it["type"] == "user":
            add("user", {"type": "text", "text": it["text"]})
        elif it["type"] == "answer":
            add("assistant", {"type": "text", "text": it["text"]})
        elif it["type"] == "step" and it["status"] in ("done", "error", "invalid", "declined"):
            tid = it.get("tool_use_id") or it["id"]
            if it.get("why"):
                add("assistant", {"type": "text", "text": it["why"]})
            add("assistant", {"type": "tool_use", "id": tid, "name": it["tool"], "input": it.get("args") or {}})
            content = (_dump(it.get("result"), 6000) if it["status"] == "done" else
                       "The user declined this call." if it["status"] == "declined" else f"ERROR: {it.get('error')}")
            add("user", {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": it["status"] != "done"})
    return msgs


async def decide_sdk(sess: dict[str, Any], extra: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Native tool use with the Anthropic SDK; falls back to JSON-action prompting on any failure."""
    t0 = time.perf_counter()
    try:
        client = llm._sdk_client()
        msgs = sdk_messages(sess)
        if extra:
            msgs.append({"role": "user", "content": [{"type": "text", "text": extra}]}) if msgs[-1]["role"] == "assistant" else \
                msgs[-1]["content"].append({"type": "text", "text": extra})
        tools = [{"name": t.name, "description": f"[{t.kind}] {t.description}", "input_schema": t.schema()} for t in T.TOOLS]
        msg = await asyncio.wait_for(client.messages.create(
            model=llm.settings().get("model") or SDK_MODEL, max_tokens=2000, system=SYSTEM + f"\nThe user is {sess['user']}.",
            tools=tools, tool_choice={"type": "auto", "disable_parallel_tool_use": True}, messages=msgs),
            llm.settings()["sdk_timeout"] * 2)
        meta = {"source": "anthropic-sdk", "decider": "Anthropic SDK", "model": msg.model, "seconds": round(time.perf_counter() - t0, 3)}
        why = " ".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()
        for b in msg.content:
            if getattr(b, "type", "") == "tool_use":
                return {"tool": b.name, "args": dict(b.input or {}), "why": why[:300], "tool_use_id": b.id}, meta
        return {"answer": why}, meta
    except Exception as e:  # noqa: BLE001
        obj, meta = await decide_json(sess, extra)
        meta = dict(meta)
        meta.setdefault("attempts", []).insert(0, {"source": "anthropic-sdk-tools", "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
        return obj, meta


# ------------------------------------------------------------------------------------------------- the stub decider

ID_RE = re.compile(r"\b([A-Z]{1,3}-\d{3,})\b")


def stub_next(sess: dict[str, Any]) -> dict[str, Any]:
    """A fixed plan per intent, reading the previous results. Deterministic; no model."""
    users = [i for i in sess["timeline"] if i["type"] == "user"]
    msg = users[-1]["text"]
    low = msg.lower()
    start = max(i for i, it in enumerate(sess["timeline"]) if it["type"] == "user")
    done = [it for it in sess["timeline"][start:] if it["type"] == "step"]
    ok = [s for s in done if s["status"] == "done"]
    last = done[-1] if done else None
    if last and last["status"] in ("error", "declined", "invalid"):
        return {"answer": f"I stopped at {last['tool']}: " + ("you declined the call." if last["status"] == "declined" else str(last.get("error")))}

    def res(tool: str, k: int = -1) -> dict[str, Any] | None:
        xs = [s["result"] for s in ok if s["tool"] == tool]
        return xs[k] if xs and len(xs) >= (abs(k) if k < 0 else k + 1) else None

    def count(tool: str) -> int:
        return sum(1 for s in ok if s["tool"] == tool)

    ds_m = re.search(r"([a-z][a-z0-9]+(?:[-_][a-z0-9]+)+)(?:\.csv)?", low)
    ds = ds_m.group(1).replace("_", "-") if ds_m else None
    vid_m = re.search(r"\bmv_[0-9a-f]{6,}\b", msg)

    if any(k in low for k in ("declin", "what would change", "reason")):
        vid = vid_m.group(0) if vid_m else None
        if not vid:
            lv = res("list_versions")
            if lv is None:
                return {"tool": "list_versions", "args": {}, "why": "Find the registered version that made the decision."}
            ready = [v for v in lv["versions"] if v.get("status") == "ready" and v.get("problem_type") == "binary"]
            if not ready:
                return {"answer": "There is no ready yes/no version in the registry to explain the decision with."}
            vid = ready[0]["version_id"]
        app_id = (ID_RE.search(msg) or [None])[0] if ID_RE.search(msg) else None
        if res("get_results") is None:
            v = next((x for x in (res("list_versions") or {"versions": []})["versions"] if x["version_id"] == vid), None)
            if v is None:
                return {"tool": "list_versions", "args": {}, "why": "Read the version's source run."} if count("list_versions") == 0 else \
                    {"answer": f"Version {vid} was not found."}
            return {"tool": "get_results", "args": {"run_id": v["run_id"]}, "why": "Which dataset the version was trained on."}
        dsid = res("get_results")["dataset_id"]
        if res("find_rows") is None:
            return {"tool": "find_rows", "args": {"dataset_id": dsid, "value": app_id or ""}, "why": f"Look up applicant {app_id}."}
        rows = res("find_rows").get("rows") or []
        if not rows:
            return {"answer": f"No row with {app_id} in {dsid}."}
        row = rows[0]
        if res("explain_decision") is None:
            return {"tool": "explain_decision", "args": {"version_id": vid, "row": row}, "why": "The decision and its reason codes."}
        if res("counterfactuals") is None:
            return {"tool": "counterfactuals", "args": {"version_id": vid, "row": row}, "why": "What would flip the decision."}
        e, c = res("explain_decision"), res("counterfactuals")
        codes = "; ".join(f"{r.get('code')} {r.get('statement') or r.get('feature')}" for r in e.get("reason_codes") or []) or "none returned"
        opts = []
        for o in c.get("options") or []:
            ch = ", ".join(f"{x.get('feature')} {x.get('from')} → {x.get('to')}" for x in o.get("changes") or [])
            opts.append(f"{ch} (P({e.get('adverse_class')}) {o.get('new_p_adverse_class')})")
        return {"answer": f"Applicant {app_id}: {e.get('decision')}, P({e.get('adverse_class')}) {e.get('p_adverse_class')} at or above the threshold "
                          f"{e.get('threshold')} on {vid}. Top reason codes: {codes}. What would change it: "
                          + (" · or ".join(opts) if opts else "no valid counterfactual was found") + "."}

    if any(k in low for k in ("score", "drift", "batch")) and not any(k in low for k in ("train", "fit ")):
        vid = vid_m.group(0) if vid_m else None
        if not vid:
            lv = res("list_versions")
            if lv is None:
                return {"tool": "list_versions", "args": {}, "why": "Find the version to score with."}
            ready = [v for v in lv["versions"] if v.get("status") == "ready"]
            if not ready:
                return {"answer": "No ready version to score with."}
            vid = ready[0]["version_id"]
        if res("batch_score") is None:
            return {"tool": "batch_score", "args": {"version_id": vid, "dataset_id": ds}, "why": f"Score {ds} as a batch and read its drift."}
        b = res("batch_score")
        d = b["drift"]
        tops = ", ".join(f"{f['feature']} PSI {f['psi']}" for f in d.get("top_features", [])[:3])
        verdict = (f"Yes: {', '.join(d['drifted'])} drifted (PSI above 0.25)." if d.get("drifted") else "No feature is above PSI 0.25.")
        return {"answer": f"Batch {b['batch_id']} scored {b.get('rows')} rows with {vid}. {verdict} Max PSI {d.get('max_psi')}; "
                          f"highest: {tops}."}

    # train → doctor → patch → compare (→ register → request approval)
    if res("intake_suggestions") is None and count("train_from_intake") == 0:
        return {"tool": "intake_suggestions", "args": {"dataset_id": ds}, "why": "Read the detected roles and the suggested model."}
    if count("train_from_intake") == 0:
        split = "time" if ("out-of-time" in low or "out of time" in low) else None
        return {"tool": "train_from_intake", "args": {"dataset_id": ds, **({"split": split} if split else {}), "time_limit": 90},
                "why": "Train from the Smart Intake pre-fill" + (" with an out-of-time split." if split else ".")}
    base = res("train_from_intake")["run_id"]
    if count("watch_run") == 0:
        return {"tool": "watch_run", "args": {"run_id": base}, "why": "Wait for the run to finish."}
    if res("watch_run", 0)["status"] != "succeeded":
        return {"answer": f"Run {base} ended {res('watch_run', 0)['status']}."}
    if res("model_doctor") is None:
        return {"tool": "model_doctor", "args": {"run_id": base}, "why": "Diagnose the limiting factor."}
    if "fix" in low or "patch" in low or "apply" in low:
        if count("apply_gym_patch") == 0:
            return {"tool": "apply_gym_patch", "args": {"run_id": base, "time_limit": 90}, "why": "Apply the prescribed Gym patch."}
        child = res("apply_gym_patch")["run_id"]
        if count("watch_run") < 2:
            return {"tool": "watch_run", "args": {"run_id": child}, "why": "Wait for the patched run."}
        if res("compare_runs") is None:
            return {"tool": "compare_runs", "args": {"base_run_id": base, "other_run_id": child}, "why": "Compare on the same holdout."}
    cmp_ = res("compare_runs")
    best = base
    if cmp_ and cmp_.get("delta") is not None and cmp_["delta"] > 0:
        best = cmp_["other"]["run_id"]
    if "register" in low:
        if res("register_version") is None:
            return {"tool": "register_version", "args": {"run_id": best}, "why": f"Register the better run, {best}."}
        if "approv" in low and res("request_approval") is None:
            return {"tool": "request_approval", "args": {"version_id": res("register_version")["version_id"]}, "why": "Ask a reviewer."}
    d, w = res("model_doctor"), res("watch_run", 0)
    parts = [f"Run {base}: holdout {w.get('metric')} {w.get('holdout_score')} ({w.get('best_model')}).",
             f"Model Doctor: {(d.get('primary') or {}).get('title') or d.get('headline')}."]
    if cmp_:
        parts.append(f"Patched run {cmp_['other']['run_id']}: {cmp_['other'].get('holdout_score') if cmp_['other'].get('metric') == cmp_.get('metric') else (cmp_['other'].get('holdout_scores') or {}).get(cmp_.get('metric'))} "
                     f"(change {cmp_.get('delta')}).")
    if res("register_version"):
        parts.append(f"Registered {res('register_version')['version_id']} from {best}"
                     + (", approval requested (a reviewer must approve)." if res("request_approval") else "."))
    return {"answer": " ".join(parts)}


async def decide_stub(sess: dict[str, Any], extra: str = "") -> tuple[dict[str, Any] | None, dict[str, Any]]:
    t0 = time.perf_counter()
    act = stub_next(sess)
    return act, {"source": "stub", "decider": "stub planner (no model)", "model": None, "seconds": round(time.perf_counter() - t0, 4)}


DECIDE = {"anthropic-sdk": decide_sdk, "claude-cli": decide_json, "stub": decide_stub}


# ----------------------------------------------------------------------------------------------------------- manager

class AgentManager:
    """Sessions in memory, saved to data/agent/<id>.json after every change; one asyncio task per running loop."""

    def __init__(self, app: Any, root: Path) -> None:
        self.app, self.root = app, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.events: dict[str, asyncio.Event] = {}
        self.stops: set[str] = set()

    # -- storage
    def save(self, s: dict[str, Any]) -> None:
        s["rev"] = s.get("rev", 0) + 1
        s["updated_at"] = now()
        (self.root / f"{s['id']}.json").write_text(json.dumps(s, ensure_ascii=False, default=str))

    def get(self, sid: str) -> dict[str, Any] | None:
        if sid in self.sessions:
            return self.sessions[sid]
        p = self.root / f"{sid}.json"
        if not p.exists():
            return None
        s = json.loads(p.read_text())
        if s["status"] not in TERMINAL and s["status"] != "idle":  # cut off by a restart
            s["status"] = "idle"
            s["timeline"].append({"type": "note", "text": "The API restarted; the agent stopped here. Ask again to continue."})
            for it in s["timeline"]:
                if it["type"] == "step" and it["status"] in ("running", "awaiting"):
                    it["status"] = "stopped"
        self.sessions[sid] = s
        return s

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("as_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            try:
                s = self.sessions.get(p.stem) or json.loads(p.read_text())
            except ValueError:
                continue
            first = next((i["text"] for i in s["timeline"] if i["type"] == "user"), "")
            out.append({"id": s["id"], "user": s["user"], "status": s["status"], "decider": s["decider"], "created_at": s["created_at"],
                        "updated_at": s.get("updated_at"), "title": first[:100], "steps": s["budget"]["steps"]})
        return out

    # -- commands
    def create(self, message: str, user: str, decider: str | None, max_steps: int | None, max_seconds: float | None) -> dict[str, Any]:
        sid = "as_" + secrets.token_hex(4)
        s = {"id": sid, "user": user, "created_at": now(), "status": "thinking", "decider": pick_decider(decider),
             "budget": {"max_steps": int(max_steps or MAX_STEPS), "steps": 0, "max_seconds": float(max_seconds or MAX_SECONDS), "seconds": 0.0},
             "timeline": [], "pending": None, "rev": 0}
        self.sessions[sid] = s
        return self.say(sid, message)

    def say(self, sid: str, message: str) -> dict[str, Any]:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        if sid in self.tasks and not self.tasks[sid].done():
            raise RuntimeError("The agent is still working on this conversation; stop it or wait.")
        s["timeline"].append({"type": "user", "text": message.strip(), "at": now()})
        s["status"] = "thinking"
        s["turn_started"] = time.time()
        if s["decider"] == "none":
            s["timeline"].append({"type": "offer", "at": now(), "links": _offers(message),
                                  "text": "No language model is available (no ANTHROPIC_API_KEY and no claude CLI), so the agent "
                                          "cannot plan. These pages do the same in one click:"})
            s["status"] = "no_model"
            self.save(s)
            return s
        self.stops.discard(sid)
        self.save(s)
        self.tasks[sid] = asyncio.get_running_loop().create_task(self._loop(sid))
        return s

    def confirm(self, sid: str, step_id: str, approve: bool) -> dict[str, Any]:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        if s.get("pending") != step_id:
            raise ValueError(f"Step {step_id} is not waiting for a confirmation.")
        s["decision"] = {"step_id": step_id, "approve": bool(approve)}
        ev = self.events.get(sid)
        if ev is None:
            raise RuntimeError("Nothing is waiting for this confirmation (the API may have restarted); ask again.")
        ev.set()
        return s

    def stop(self, sid: str) -> dict[str, Any]:
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        self.stops.add(sid)
        t = self.tasks.get(sid)
        if t and not t.done():
            t.cancel()
        return s

    def shutdown(self) -> None:
        for t in self.tasks.values():
            t.cancel()

    # -- the loop
    def _audit(self, s: dict[str, Any], step: dict[str, Any]) -> None:
        try:
            reg = self.app.state.registry
            args = step.get("args") or {}
            v = reg.get_version(args["version_id"]) if isinstance(args.get("version_id"), str) else None
            res = step.get("result") if isinstance(step.get("result"), dict) else {}
            run_id = args.get("run_id") or res.get("run_id")
            reg.audit(actor=s["actor"], action=f"agent:{step['tool']}", version=v, run_id=run_id if isinstance(run_id, str) else None,
                      comment=(step.get("why") or "")[:300],
                      details={"session": s["id"], "step": step["n"], "kind": step["kind"], "status": step["status"],
                               "args": json.loads(_dump(args, 2000)) if len(_dump(args, 4000)) <= 2000 else _dump(args, 2000),
                               "error": step.get("error")})
        except Exception as e:  # noqa: BLE001 — never fail the loop on the audit write
            log.warning("agent audit write failed: %s", e)

    async def _answer(self, s: dict[str, Any], text: str, meta: dict[str, Any], decide) -> None:
        facts = facts_of(s)
        flagged = llm.unsupported_numbers(text, facts)
        retried = False
        if flagged and s["decider"] != "stub" and s["budget"]["steps"] < s["budget"]["max_steps"] + 2:
            obj, meta2 = await decide(s, f"\nYour last answer was: {json.dumps(text)}\nIt used numbers that appear in no tool "
                                         f"result: {', '.join(flagged)}. Rewrite it as {{\"answer\": …}} using only numbers copied "
                                         f"from the tool results.")
            if obj and isinstance(obj.get("answer"), str):
                retried = True
                f2 = llm.unsupported_numbers(obj["answer"], facts)
                if len(f2) < len(flagged) or not f2:
                    text, flagged, meta = obj["answer"], f2, meta2
        s["timeline"].append({"type": "answer", "text": text, "at": now(), "decider": _dmeta(meta),
                              "guard": {"ok": not flagged, "unsupported": flagged, "numbers": len(llm.numbers(text)),
                                        "rewritten": retried, "rule": "every number in the answer appears in the tool results "
                                        "(after rounding to the answer's precision; 0–10 allowed as counts)"}})

    async def _loop(self, sid: str) -> None:
        s = self.sessions[sid]
        b = s["budget"]
        user = (s["user"] or "user")[:40]
        s["actor"] = f"Vela agent on behalf of {user}"
        decide = DECIDE[s["decider"]]
        t_active = time.monotonic()
        transport = httpx.ASGITransport(app=self.app)
        client = httpx.AsyncClient(transport=transport, base_url="http://vela-agent", timeout=900)
        invalid_in_row = 0
        try:
            while True:
                b["seconds"] = round(b["seconds"] + (time.monotonic() - t_active), 2)
                t_active = time.monotonic()
                if b["steps"] >= b["max_steps"] or b["seconds"] >= b["max_seconds"]:
                    what = "step" if b["steps"] >= b["max_steps"] else "time"
                    s["timeline"].append({"type": "note", "at": now(), "text": f"The {what} budget is spent ({b['steps']} calls, "
                                          f"{b['seconds']:.0f} s); stopping here. Ask again to continue."})
                    s["status"] = "idle"
                    break
                s["status"] = "thinking"
                self.save(s)
                obj, meta = await decide(s)
                if obj is None:
                    s["timeline"].append({"type": "note", "at": now(), "error": True, "decider": _dmeta(meta),
                                          "text": f"The model gave no usable reply ({meta.get('reason') or 'no JSON'}); stopping. "
                                                  "Ask again, or use the pages directly."})
                    s["status"] = "failed"
                    break
                if isinstance(obj.get("answer"), str) and not obj.get("tool"):
                    await self._answer(s, obj["answer"], meta, decide)
                    s["status"] = "idle"
                    break
                name, args = obj.get("tool"), obj.get("args") if isinstance(obj.get("args"), dict) else {}
                tool = T.BY_NAME.get(name or "")
                b["steps"] += 1
                step = {"type": "step", "id": f"st_{b['steps']}", "n": b["steps"], "tool": name, "args": args,
                        "why": (obj.get("why") or "")[:300], "kind": tool.kind if tool else "read", "status": "running",
                        "at": now(), "decider": _dmeta(meta), "tool_use_id": obj.get("tool_use_id")}
                s["timeline"].append(step)
                if tool is None:
                    step.update(status="invalid", error=f"Unknown tool '{name}'. Tools: {', '.join(T.BY_NAME)}")
                else:
                    try:
                        jsonschema.validate(args, tool.schema())
                    except jsonschema.ValidationError as e:
                        step.update(status="invalid", error=f"Arguments do not match the schema: {e.message}")
                if step["status"] == "invalid":
                    invalid_in_row += 1
                    self._audit(s, step)
                    if invalid_in_row >= 3:
                        s["timeline"].append({"type": "note", "at": now(), "text": "Three invalid calls in a row; stopping."})
                        s["status"] = "failed"
                        break
                    continue
                invalid_in_row = 0
                step["link"] = None
                if tool.kind == T.WRITE:
                    step["status"] = "awaiting"
                    s["status"] = "awaiting_confirmation"
                    s["pending"] = step["id"]
                    ev = asyncio.Event()
                    self.events[sid] = ev
                    b["seconds"] = round(b["seconds"] + (time.monotonic() - t_active), 2)
                    self.save(s)
                    await ev.wait()  # waiting for the user does not count against the time budget
                    t_active = time.monotonic()
                    dec = s.pop("decision", None) or {}
                    s["pending"] = None
                    self.events.pop(sid, None)
                    if not dec.get("approve"):
                        step["status"] = "declined"
                        step["decided_at"] = now()
                        self._audit(s, step)
                        continue
                    step["confirmed_at"] = now()
                step["status"] = "running"
                s["status"] = "calling"
                self.save(s)
                t0 = time.monotonic()

                def progress(m: str, step=step) -> None:
                    step["progress"] = m
                    s["rev"] = s.get("rev", 0) + 1

                left = max(1.0, b["max_seconds"] - b["seconds"] - (time.monotonic() - t_active))
                ctx = T.Ctx(client=client, app=self.app, actor=s["actor"], deadline=time.monotonic() + left, progress=progress,
                            stop=lambda: sid in self.stops)
                try:
                    res = await asyncio.wait_for(tool.run(ctx, args), left + 5)
                    step.update(status="done", result=res)
                    try:
                        step["link"] = tool.link(args, res)
                    except Exception:  # noqa: BLE001
                        step["link"] = None
                except T.ToolError as e:
                    step.update(status="error", error=str(e)[:600])
                except asyncio.TimeoutError:
                    step.update(status="error", error="the time budget ran out during this call")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.exception("agent tool %s failed", name)
                    step.update(status="error", error=f"{type(e).__name__}: {e}"[:600])
                step["seconds"] = round(time.monotonic() - t0, 2)
                step.pop("progress", None)
                self._audit(s, step)
        except asyncio.CancelledError:
            for it in s["timeline"]:
                if it["type"] == "step" and it["status"] in ("running", "awaiting"):
                    it["status"] = "stopped"
                    if it.get("tool") in ("watch_run", "register_version", "batch_score"):
                        it["error"] = "stopped by you (the run or build itself keeps going on the server)"
            s["timeline"].append({"type": "note", "at": now(), "text": "Stopped by you."})
            s["status"] = "stopped"
            s["pending"] = None
        except Exception as e:  # noqa: BLE001
            log.exception("agent loop failed")
            s["timeline"].append({"type": "note", "at": now(), "error": True, "text": f"The agent failed: {type(e).__name__}: {e}"})
            s["status"] = "failed"
        finally:
            b["seconds"] = round(b["seconds"] + (time.monotonic() - t_active), 2)
            s["turn_seconds"] = round(time.time() - s.get("turn_started", time.time()), 1)
            await client.aclose()
            self.events.pop(sid, None)
            self.save(s)


def _dmeta(meta: dict[str, Any] | None) -> dict[str, Any]:
    m = meta or {}
    return {"source": m.get("source"), "decider": m.get("decider"), "model": m.get("model"), "seconds": m.get("seconds"),
            "attempts": [{k: a.get(k) for k in ("source", "ok", "error", "seconds", "skipped")} for a in (m.get("attempts") or [])][:4]}
