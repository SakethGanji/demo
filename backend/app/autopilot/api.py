"""Autopilot routes (`/api/autopilot…`) and Vela's free-text question box (`/api/vela/ask`)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from ..authz import body, path, require  # P19
from pydantic import BaseModel, Field

from .. import llm
from . import metrics as M
from .engine import Manager, bulk_accept, new_autopilot
from ..http import run_or_404, dataset_or_404

router = APIRouter(prefix="/api")


class Requirement(BaseModel):
    metric: str = Field(..., description="f1_macro, accuracy, balanced_accuracy, mcc, roc_auc, f1, r2, rmse, mae, "
                                         "recall[<class>], p95_latency_ms or p50_latency_ms")
    op: Literal[">=", "<="] | None = None
    value: float


class Brief(BaseModel):
    dataset_id: str
    label: str | None = None
    goal: str = ""
    requirements: list[Requirement] = Field(..., min_length=1)
    budget_seconds: float = Field(600, ge=60, le=4 * 3600, description="Total training time across every step.")
    autonomy: Literal["ask", "auto"] = Field("ask", description="ask: wait for your answers in the Ground-Truth Inbox; "
                                                                "auto: accept suggestions with confidence ≥ auto_confidence.")
    auto_confidence: float = Field(0.9, ge=0.5, le=1)
    decider: Literal["auto", "layer1"] = Field("auto", description="auto: Claude picks steps when available, else the fixed strategy.")
    allow_synthetic: bool = True
    llm_timeout: float = Field(60, ge=5, le=300)


class Answer(BaseModel):
    id: str
    answer: Literal["confirm", "relabel", "drop", "skip"] | None
    label: str | None = None


class Answers(BaseModel):
    answers: list[Answer]


class Bulk(BaseModel):
    min_confidence: float = Field(0.9, ge=0, le=1)
    action: Literal["accept", "skip", "clear"] = "accept"
    kinds: list[str] | None = None


def _mgr(request: Request) -> Manager:
    return request.app.state.autopilot


def _get(request: Request, aid: str) -> dict[str, Any]:
    st = _mgr(request).store.get(aid)
    if not st:
        raise HTTPException(404, f"No autopilot with id '{aid}'.")
    return st


def _inbox_summary(ib: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ib:
        return None
    items = ib.get("items") or []
    ans: dict[str, int] = {}
    for it in items:
        ans[it.get("answer") or "open"] = ans.get(it.get("answer") or "open", 0) + 1
    return {k: v for k, v in ib.items() if k != "items"} | {"n_items": len(items), "answers": ans}


def out(st: dict[str, Any], full: bool = True) -> dict[str, Any]:
    o = {k: v for k, v in st.items() if k not in ("inbox", "traceback", "base_url")}
    o["inbox"] = _inbox_summary(st.get("inbox"))
    spent = sum(s.get("seconds") or 0 for s in st["steps"] if s.get("run_id"))
    o["budget"] = {"total": st["brief"]["budget_seconds"], "spent": round(spent, 1), "remaining": round(st["brief"]["budget_seconds"] - spent, 1)}
    if not full:
        for k in ("log", "decisions", "facts"):
            o.pop(k, None)
        o["steps"] = [{k: s.get(k) for k in ("id", "kind", "title", "status", "run_id", "score")} for s in st["steps"]]
    return o


@router.post("/autopilot", status_code=201, dependencies=[require("run.train", body("dataset_id", "dataset"))])
def start(body: Brief, request: Request) -> dict[str, Any]:
    ds = dataset_or_404(request, body.dataset_id)
    label = body.label or ds["target"]
    if not label:
        raise HTTPException(400, "Say which column to predict (label).")
    reqs = []
    for r in body.requirements:
        try:
            d = M.describe(r.metric)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        reqs.append({"metric": r.metric, "op": r.op or d["default_op"], "value": r.value})
    brief = {**body.model_dump(), "label": label, "requirements": reqs}
    st = new_autopilot(brief, str(request.base_url).rstrip("/"))
    mgr = _mgr(request)
    mgr.store.save(st)
    mgr.start(st["id"])
    return out(st)


@router.get("/autopilot")
def list_autopilots(request: Request) -> dict[str, Any]:
    return {"autopilots": [out(s, full=False) for s in _mgr(request).store.list()]}


@router.get("/autopilot/{aid}")
def get_autopilot(aid: str, request: Request) -> dict[str, Any]:
    return out(_get(request, aid))


@router.get("/autopilot/{aid}/inbox")
def inbox(aid: str, request: Request, kind: str | None = None, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
    st = _get(request, aid)
    ib = st.get("inbox")
    if not ib:
        raise HTTPException(409, "The audit has not run yet.")
    items = [it for it in ib["items"] if not kind or it["kind"] == kind]
    return {"autopilot_id": aid, "status": st["status"], **(_inbox_summary(ib) or {}),
            "total": len(items), "items": items[offset: offset + limit]}


def _editable(st: dict[str, Any]) -> dict[str, Any]:
    ib = st.get("inbox")
    if not ib:
        raise HTTPException(409, "The audit has not run yet.")
    if ib.get("submitted") or ib.get("applied"):
        raise HTTPException(409, "These answers were already applied; the inbox is closed.")
    return ib


@router.post("/autopilot/{aid}/inbox/answer", dependencies=[require("run.train", path("aid", "autopilot"))])
def answer(aid: str, body: Answers, request: Request) -> dict[str, Any]:
    mgr = _mgr(request)
    with mgr.store.lock:
        st = _get(request, aid)
        ib = _editable(st)
        by_id = {it["id"]: it for it in ib["items"]}
        classes = set((st.get("facts") or {}).get("class_shares") or {})
        for a in body.answers:
            it = by_id.get(a.id)
            if not it:
                raise HTTPException(404, f"No inbox item '{a.id}'.")
            if a.answer == "relabel":
                lab = a.label or it.get("suggested")
                if not lab or (classes and lab not in classes):
                    raise HTTPException(400, f"Relabel {a.id}: give a label, one of {', '.join(sorted(classes))}.")
                it["answer_label"] = lab
            else:
                it.pop("answer_label", None)
            it["answer"] = a.answer
            it["answered_by"] = "you"
        mgr.store.save(st)
    return {"answered": len(body.answers), "inbox": _inbox_summary(ib)}


@router.post("/autopilot/{aid}/inbox/bulk", dependencies=[require("run.train", path("aid", "autopilot"))])
def bulk(aid: str, body: Bulk, request: Request) -> dict[str, Any]:
    mgr = _mgr(request)
    with mgr.store.lock:
        st = _get(request, aid)
        ib = _editable(st)
        if body.action == "accept":
            n = bulk_accept(st, body.min_confidence, body.kinds)
        else:
            n = 0
            for it in ib["items"]:
                if it["confidence"] < body.min_confidence or (body.kinds and it["kind"] not in body.kinds):
                    continue
                if body.action == "skip" and not it.get("answer"):
                    it["answer"], it["answered_by"] = "skip", "bulk"
                    n += 1
                elif body.action == "clear" and it.get("answer"):
                    it["answer"] = None
                    it.pop("answer_label", None)
                    n += 1
        mgr.store.save(st)
    return {"answered": n, "inbox": _inbox_summary(ib)}


@router.post("/autopilot/{aid}/resume", dependencies=[require("run.train", path("aid", "autopilot"))])
def resume(aid: str, request: Request) -> dict[str, Any]:
    """Submit the inbox (open items count as skipped) and let the autopilot continue."""
    mgr = _mgr(request)
    with mgr.store.lock:
        st = _get(request, aid)
        if st["status"] in ("finished", "failed", "cancelled"):
            raise HTTPException(409, f"Autopilot {aid} is {st['status']}.")
        if st.get("inbox") and not st["inbox"].get("applied"):
            st["inbox"]["submitted"] = True
        mgr.store.save(st)
    mgr.start(aid)
    return out(st)


@router.post("/autopilot/{aid}/cancel", dependencies=[require("run.train", path("aid", "autopilot"))])
def cancel(aid: str, request: Request) -> dict[str, Any]:
    st = _get(request, aid)
    _mgr(request).stop(aid)
    return out(st)


# ---------------------------------------------------------------------- Vela's question box


class Ask(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    run_id: str | None = None
    autopilot_id: str | None = None
    timeout: float | None = Field(None, ge=0.1, le=600)  # F5: total seconds for this ask (default: the router's settings)
    cache: bool = True  # F5: answer a repeated question about the same facts from the cache


ASK_PROMPT = """You are Vela, the analyst assistant in a bank's AutoML workbench (AutoGluon). Answer the user's question
about the run below using only the facts given. Two to five plain sentences, direct, numbers exactly as given. If the
facts do not answer it, say what is missing. No greeting.

Facts (computed by the workbench):
{facts}

Question: {question}

Return only JSON: {{"answer": "<your answer>"}}"""


def _run_facts(request: Request, run_id: str) -> dict[str, Any]:
    rec = run_or_404(request, run_id)
    res = rec.get("result") or {}
    lb = [{k: r.get(k) for k in ("model", "score_test", "score_val", "fit_time", "pred_time_test", "stack_level")}
          for r in (res.get("leaderboard") or [])[:12]]
    fi = [{"feature": f.get("feature"), "importance": f.get("importance")} for f in (res.get("feature_importance") or [])[:10]]
    cfg = rec["config"]
    return {"run_id": run_id, "name": rec["name"], "status": rec["status"], "dataset_id": rec["dataset_id"], "label": rec["label"],
            "problem_type": res.get("problem_type"), "eval_metric": (res.get("eval_metric") or {}).get("name"),
            "best_model": res.get("best_model"), "holdout_scores": (res.get("holdout") or {}).get("scores"),
            "holdout_rows": (res.get("holdout") or {}).get("n_rows"), "data": res.get("data"),
            "classes": res.get("classes"), "threshold": (res.get("threshold") or {}).get("value"),
            "leaderboard": lb, "feature_importance": fi, "fit_seconds": res.get("fit_seconds"),
            "config": {k: cfg.get(k) for k in ("presets", "time_limit", "fit_kwargs", "drop_columns", "parent_run_id", "patch", "source")},
            "ensemble": res.get("ensemble")}


@router.post("/vela/ask", dependencies=[require("run.read", body("run_id", "run"), body("autopilot_id", "autopilot"))])
async def ask(body: Ask, request: Request) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    if body.autopilot_id:
        st = _get(request, body.autopilot_id)
        facts["autopilot"] = {"brief": {k: st["brief"].get(k) for k in ("dataset_id", "requirements", "budget_seconds", "autonomy", "goal")},
                              "status": st["status"], "verdict": st.get("verdict"),
                              "steps": [{k: s.get(k) for k in ("title", "status", "run_id", "score", "met", "seconds")} |
                                        {"p95_ms": (s.get("latency") or {}).get("p95_ms")} for s in st["steps"]],
                              "inbox": _inbox_summary(st.get("inbox")), "dataset_versions": st.get("dataset_versions"),
                              "log": [e["text"] for e in st.get("log", [])[-8:]]}
        if not body.run_id and st.get("verdict"):
            body.run_id = st["verdict"].get("best_run_id")
    if body.run_id:
        facts["run"] = _run_facts(request, body.run_id)
        try:
            from ..vela.api import compute_findings

            _, f = await compute_findings(request, body.run_id)
            facts["vela_findings"] = [{"title": x["title"], "text": x.get("computed_text") or x["text"]} for x in f["findings"]]
        except Exception:  # noqa: BLE001
            pass
    if not facts:
        raise HTTPException(400, "Ask about a run (run_id) or an autopilot (autopilot_id).")
    prompt = ASK_PROMPT.format(facts=json.dumps(facts, ensure_ascii=False, default=str)[:24_000], question=body.question)
    # F5: awaited on the event loop (SDK → CLI → computed), cached by (prompt hash, facts hash); never holds a thread
    obj, meta = await llm.ask_json_async(prompt, facts=facts, timeout=body.timeout, use_cache=body.cache)
    common = {"source": meta["source"], "decider": meta["decider"], "model": meta.get("model"), "seconds": meta["seconds"],
              "attempts": meta["attempts"], "cached": meta.get("cached", False), "key": meta.get("key"),
              "prompt_sha256": meta.get("prompt_sha256"), "facts_sha256": meta.get("facts_sha256"),
              "original_seconds": meta.get("original_seconds"), "facts": sorted(facts)}
    if obj and isinstance(obj.get("answer"), str):
        answer = obj["answer"].strip()
        bad = llm.unsupported_numbers(answer, facts)  # the number guard: numbers the facts do not contain
        return {"answer": answer, **common,
                "guard": {"ok": not bad, "unsupported": bad, "checked": len(llm.numbers(answer))}}
    why = meta.get("reason") or "every path failed"
    return {"answer": None, **common, "guard": None,
            "message": f"No model answer ({why}). Vela's computed findings still hold; the attempts say what was tried."}
