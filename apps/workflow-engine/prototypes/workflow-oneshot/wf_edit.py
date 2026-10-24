"""Edit mode for the one-shot workflow builder: change an already-built workflow by chat.

    cd apps/workflow-engine && PYTHONPATH=.:prototypes/workflow-oneshot venv/bin/python -W ignore \
        prototypes/workflow-oneshot/wf_edit.py --mode full|patch [case_id ...] [--out DIR]
    ... wf_edit.py --report                 # compare runs/edit-full and runs/edit-patch -> RESULTS-EDIT.md
    ... wf_edit.py --baseline               # no model: check the cases against the unedited workflows

The model always gets the WHOLE current workflow (15-25k characters, cheap) plus the build prompt's rules, then either
- `full`: replies with the whole edited workflow.json, or
- `patch`: replies with changes.json only (nodes to add/replace, renames, removals, connections, tests); the platform applies it.
Same platform loop as a build: check (structure, checker, db rules, the workflow's own tests), at most 2 fix calls.
Scored per case (edit_cases.py): the change landed (python checks + new hidden tests), nothing else broke (the base case's
hidden tests that still apply), drift (original nodes outside the change that were altered), time, calls, characters.
"""
import sys

if "--crazy" not in sys.argv:
    sys.argv.append("--crazy")  # wf_build picks the crazy eval set at import
import asyncio  # noqa: E402
import copy  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import wf_build as B  # noqa: E402
from edit_cases import CASES  # noqa: E402
from llm import call_model  # noqa: E402
from src.schemas.workflow import normalize_tests  # noqa: E402
from tests.support import ai_eval  # noqa: E402

HERE = Path(__file__).parent
BASE_DIR = HERE / "runs" / "crazy-live"

HEAD_COMMON = ("You build automations (workflows) for a bank's internal teams. Below is a workflow that is already built, tested "
               "and running, and a change the user wants. Make exactly that change. Keep everything the change doesn't touch as it is: "
               "node names, parameters, connections and tests. Update the tests the change makes wrong and add tests that prove the "
               "change. Don't ask questions or plan out loud. Reply with:\n\nChanged: <one sentence: what you changed>\n"
               "Needs: <what someone must set up before it runs live, e.g. \"$vars.FINANCE_EMAIL\"; or \"nothing\">\n\n")
HEAD = {
    "full": HEAD_COMMON + '```json file="workflow.json"\n{"name": "...", "nodes": [...], "connections": [...], "tests": [...]}\n```\n\n'
                          "Reply with the whole edited workflow, every node, connection and test, not just the changed parts.\n\n",
    "patch": HEAD_COMMON + '```json file="changes.json"\n{"nodes": [...], "rename": {...}, "remove_nodes": [...], "connect": [...], '
                           '"disconnect": [...], "tests": [...], "remove_tests": [...]}\n```\n\n'
                           "Reply with only the changes; the platform applies them to the current workflow:\n"
                           "- `nodes`: every node you add or change, written out whole (name, type, all parameters). A node with an "
                           "existing name replaces that node.\n"
                           '- `rename`: `{"Old name": "New name"}`. The platform renames the node everywhere: connections, '
                           '`$node["Old name"]` references, test mocks and assertions. After a rename use only the new name.\n'
                           "- `remove_nodes`: names of nodes to delete; their connections go with them.\n"
                           "- `connect` / `disconnect`: connections to add or remove, in the workflow's connection shape.\n"
                           "- `tests`: tests you add or change, written out whole. A test with an existing name replaces it. "
                           "`remove_tests`: names of tests to delete.\n"
                           "- Leave out keys you don't need. Everything you don't mention stays exactly as it is.\n\n",
}


# ── context ─────────────────────────────────────────────────────────────────────────────────

def ctx_of(case: dict) -> dict:
    ctx = json.loads((B.EVAL / f'{case["base"]}.json').read_text())
    ctx["variables"] = {**(ctx.get("variables") or {}), **case["extra_variables"]}
    return ctx


def shown(d: dict) -> dict:
    """The workflow as the model sees it: no canvas positions, no generated form screen."""
    d = copy.deepcopy(d)
    for k in [k for k in d if k.startswith("_") or k in ("interface",)]:
        d.pop(k)
    for n in d["nodes"]:
        n.pop("position", None)
    return d


def edit_prompt(mode: str, case: dict, ctx: dict, apis: dict, current: dict) -> str:
    rules = (HERE / "PROMPT.md").read_text()
    rules = rules[rules.index("# The workflow format"):]
    rules = rules[:rules.index("# The request")]
    rules = (rules.replace("{{CATALOG}}", B.catalog.render()).replace("{{APIS}}", B.render_apis(apis))
             .replace("{{TABLES}}", B.render_tables(ctx.get("tables") or {})).replace("{{VARIABLES}}", B.render_vars(ctx.get("variables") or {}))
             .replace("{{DATABASES}}", B.render_dbs(ctx.get("databases") or {})).replace("{{SUBWORKFLOWS}}", B.render_subs(ctx.get("subworkflows") or [])))
    return (HEAD[mode] + rules + "# The current workflow\n\n```json\n" + json.dumps(shown(current), indent=1) + "\n```\n\n"
            + "# The change\n\n" + case["request"] + "\n")


# ── platform: apply a patch ─────────────────────────────────────────────────────────────────

def _blocks(reply: str) -> list[str]:
    return re.findall(r'```json[^\n]*\n(.*?)\n```', reply, re.S)


def _normalize(nodes: list, conns: list) -> tuple[list, list]:
    """The same clean-up a full reply gets (default ports, api_ref shapes)."""
    d = B.parse("```json\n" + json.dumps({"nodes": nodes, "connections": conns}) + "\n```")
    return d["nodes"], d["connections"]


def _rename_refs(obj, old: str, new: str):
    s = json.dumps(obj)
    for q in ('"', "'"):
        s = s.replace(json.dumps(f"$node[{q}{old}{q}]")[1:-1], json.dumps(f"$node[{q}{new}{q}]")[1:-1])
    s = s.replace(json.dumps(f'node_data["{old}"]')[1:-1], json.dumps(f'node_data["{new}"]')[1:-1])
    return json.loads(s)


def apply_patch(current: dict, p: dict) -> dict:
    d = copy.deepcopy(shown(current))
    for old, new in (p.get("rename") or {}).items():
        n = next((x for x in d["nodes"] if x["name"] == old), None)
        if n is None:
            raise ValueError(f"rename: there is no node named {old!r}")
        n["name"] = new
        for c in d["connections"]:
            for k in ("source_node", "target_node"):
                if c[k] == old:
                    c[k] = new
        for t in d.get("tests") or []:
            t["mocks"] = {(new if k == old else k): v for k, v in (t.get("mocks") or {}).items()}
            for a in t.get("expect") or []:
                if a.get("node") == old:
                    a["node"] = new
        d["nodes"] = _rename_refs(d["nodes"], old, new)
    gone = set(p.get("remove_nodes") or [])
    missing = gone - {n["name"] for n in d["nodes"]}
    if missing:
        raise ValueError(f"remove_nodes: no such node(s) {sorted(missing)}")
    d["nodes"] = [n for n in d["nodes"] if n["name"] not in gone]
    d["connections"] = [c for c in d["connections"] if c["source_node"] not in gone and c["target_node"] not in gone]
    nodes, connect = _normalize(p.get("nodes") or [], p.get("connect") or [])
    _, disconnect = _normalize([], p.get("disconnect") or [])
    by_name = {n["name"]: i for i, n in enumerate(d["nodes"])}
    for n in nodes:
        if n["name"] in by_name:
            d["nodes"][by_name[n["name"]]] = n
        else:
            d["nodes"].append(n)
    key = lambda c: (c["source_node"], c["source_output"], c["target_node"], c.get("target_input", "main"))  # noqa: E731
    drop = {key(c) for c in disconnect}
    d["connections"] = [c for c in d["connections"] if key(c) not in drop]
    have = {key(c) for c in d["connections"]}
    d["connections"] += [c for c in connect if key(c) not in have]
    tests = [t for t in d.get("tests") or [] if t.get("name") not in set(p.get("remove_tests") or [])]
    by_t = {t.get("name"): i for i, t in enumerate(tests)}
    for t in p.get("tests") or []:
        if t.get("name") in by_t:
            tests[by_t[t["name"]]] = t
        else:
            tests.append(t)
    d["tests"] = tests
    return d


def read_reply(mode: str, reply: str, current: dict) -> tuple[dict, dict]:
    """(edited workflow, rename map)."""
    if mode == "full":
        return B.parse(reply), {}
    blocks = _blocks(reply)
    if not blocks:
        raise ValueError('no ```json file="changes.json" block in the reply')
    p = json.loads(re.sub(r",\s*([}\]])", r"\1", blocks[-1]))
    return apply_patch(current, p), dict(p.get("rename") or {})


# ── score ───────────────────────────────────────────────────────────────────────────────────

def _relax(t: dict) -> dict:
    t = copy.deepcopy(t)
    t["expect"] = [a for a in t["expect"] if not (a.get("node", "").startswith("$all:SendEmail") and a.get("op") == "count")]
    return t


def suites(case: dict) -> tuple[list, list]:
    """(kept, new): the base tests the change leaves valid, and the tests for the change."""
    base = json.loads((B.EVAL / f'{case["base"]}.json').read_text())["hidden_tests"]
    kept = [copy.deepcopy(t) for i, t in enumerate(base) if i not in case["drop_hidden"]]
    new = copy.deepcopy(case["new_hidden"])
    for t in kept + new:
        for k, v in case["add_mocks"].items():
            t["mocks"].setdefault(k, copy.deepcopy(v))
    if case.get("relax_email_counts"):
        kept, new = [_relax(t) for t in kept], [_relax(t) for t in new]
    return kept, new


def _recipients_contain(bound: dict):
    """SendEmail sends to every address in "a, b", so "the email went to a" is a contains, not an eq."""
    for a in (bound["test"] or {}).get("expect") or []:
        if a.get("path") == "params.toEmail" and a.get("op") == "eq":
            a["op"] = "contains"
    for g in bound["groups"]:
        m = g.get("merged")
        if m and m.get("path") == "params.toEmail" and m.get("op") == "eq":
            m["op"] = "contains"


def _timeouts_follow_node(bound: dict, d: dict):
    """A "nobody answered" mock emits on the port the node itself would use: on_timeout approve/reject turns a timeout into
    an approval/rejection (the engine does the same), so the test describes the situation, not the wiring."""
    by = {n["name"]: n for n in d["nodes"]}
    for name, m in (bound["test"]["mocks"] if bound["test"] else {}).items():
        n = by.get(name)
        if n and n["type"] == "Approval" and isinstance(m, dict) and m.get("port") == "timed_out":
            to = (n.get("parameters") or {}).get("on_timeout") or "timed_out"
            if to in ("approve", "reject"):
                m["port"] = {"approve": "approved", "reject": "rejected"}[to]
                m.setdefault("approval", {})["decision"] = m["port"]


def run_hidden(tests: list, d: dict, apis: dict, ctx: dict, tag: str) -> list:
    if not tests:
        return []
    r = ai_eval.Resolver(d, {k: k for k in apis})
    bound = [ai_eval.bind_test(t, r, i) for i, t in zip(ai_eval.hidden_ids(tag, len(tests)), tests)]
    for b in bound:
        _timeouts_follow_node(b, d)
        _recipients_contain(b)
    runnable = [b["test"] for b in bound if b["test"]]
    results = {x["test_id"]: x for x in asyncio.run(B.run_tests(d, normalize_tests(runnable), apis, ctx))} if runnable else {}
    return [ai_eval.judge_hidden(b, results.get((b["test"] or {}).get("id"))) for b in bound]


def drift(base: dict, d: dict, case: dict, rename: dict) -> dict:
    allowed = lambda name: any(p.search(name) for p in case["may_change"])  # noqa: E731
    by = {n["name"]: n for n in d["nodes"]}
    canon = lambda n: json.dumps({"type": n["type"], "parameters": _norm_params(n.get("parameters") or {})}, sort_keys=True)  # noqa: E731
    changed, removed = [], []
    for n in base["nodes"]:
        if allowed(n["name"]):
            continue
        now = by.get(rename.get(n["name"], n["name"]))
        if now is None:
            removed.append(n["name"])
        elif canon(_renamed(n, rename)) != canon(now):
            changed.append(n["name"])
    have = {(c["source_node"], c["source_output"], c["target_node"]) for c in d["connections"]}
    lost = [f'{c["source_node"]} -{c["source_output"]}-> {c["target_node"]}' for c in base["connections"]
            if not allowed(c["source_node"]) and not allowed(c["target_node"])
            and (rename.get(c["source_node"], c["source_node"]), c["source_output"], rename.get(c["target_node"], c["target_node"])) not in have]
    return {"changed": changed, "removed": removed, "rewired": lost}


def _renamed(n: dict, rename: dict) -> dict:
    for old, new in rename.items():
        n = _rename_refs(n, old, new)
    return n


def _norm_params(p: dict) -> dict:
    p = copy.deepcopy(p)
    if isinstance(p.get("api_ref"), dict):
        p.setdefault("url", "")
    return p


# ── one case ────────────────────────────────────────────────────────────────────────────────

def edit(mode: str, case: dict, out: Path) -> dict:
    t0 = time.time()
    ctx = ctx_of(case)
    apis = B.load_apis(ctx["apis"])
    current = json.loads((BASE_DIR / f'{case["base"]}.json').read_text())
    kept_t, new_t = suites(case)
    base_kept = {j["name"] for j in run_hidden(kept_t, current, apis, ctx, "b") if j["ok"]}
    prompt = edit_prompt(mode, case, ctx, apis, current)
    (out / f'{case["id"]}.prompt.md').write_text(prompt)
    calls, reply_chars, attempts = 0, 0, []
    reply = ""
    msg = prompt
    while True:
        reply = call_model(msg)
        calls += 1
        reply_chars += len(reply)
        rename = {}
        try:
            d, rename = read_reply(mode, reply, current)
            d = B.finish(d)
            fails = B.check(d, apis, ctx)
        except Exception as e:  # noqa: BLE001
            d, fails = None, [f"reply could not be used: {e}"]
        attempts.append({"fails": fails, "score": _score(case, d, current, kept_t, new_t, base_kept, apis, ctx, rename) if d else None})
        if not fails or calls > B.MAX_FIXES:
            break
        msg = (prompt + "\n\nYour previous reply is below. After applying it, these failed:\n" + "\n".join(f"- {f}" for f in fails)
               + "\n\nFix them and reply again in the same format"
               + (" with the whole edited workflow" if mode == "full" else " with ALL your changes to the current workflow above (not just the fixes)")
               + ". If a test itself was wrong (it expects something the change doesn't say), fix the test, never weaken a rule."
               "\n\nPrevious reply:\n" + reply)
    (out / f'{case["id"]}.reply.md').write_text(reply)
    if d:
        (out / f'{case["id"]}.json').write_text(json.dumps(d, indent=1))
    first, last = attempts[0], attempts[-1]
    return {"id": case["id"], "tier": case["tier"], "mode": mode, "seconds": round(time.time() - t0, 1), "calls": calls,
            "prompt_chars": len(prompt), "reply_chars": reply_chars,
            "first": _verdict(first), "final": _verdict(last), "final_fails": last["fails"][:6], **(last["score"] or {})}


def _score(case, d, base, kept_t, new_t, base_kept, apis, ctx, rename) -> dict:
    checks = []
    for name, fn in case["checks"]:
        try:
            r = fn(d)
        except Exception as e:  # noqa: BLE001
            r = f"check crashed: {e}"
        checks.append({"name": name, "ok": r is True, "detail": "" if r is True else r})
    kept = [j for j in run_hidden(kept_t, d, apis, ctx, "k") if j["name"] in base_kept]
    new = run_hidden(new_t, d, apis, ctx, "n")
    return {"checks": checks, "kept": [sum(j["ok"] for j in kept), len(kept)], "kept_fail": [j for j in kept if not j["ok"]],
            "new": [sum(j["ok"] for j in new), len(new)], "new_fail": [j for j in new if not j["ok"]],
            "drift": drift(base, d, case, rename), "own_tests": len(d.get("tests") or [])}


def _verdict(a: dict) -> dict:
    s = a["score"]
    if not s:
        return {"pass": False, "clean": False}
    landed = all(c["ok"] for c in s["checks"]) and s["new"][0] == s["new"][1]
    kept = s["kept"][0] == s["kept"][1]
    ok = not a["fails"] and landed and kept
    return {"pass": ok, "landed": landed, "kept": kept, "checked": not a["fails"],
            "clean": ok and not (s["drift"]["changed"] or s["drift"]["removed"])}


def _safe(mode, c, out):
    try:
        return edit(mode, c, out)
    except Exception:  # noqa: BLE001
        import traceback
        return {"id": c["id"], "mode": mode, "error": traceback.format_exc(), "first": {}, "final": {}}


# ── baseline and report ─────────────────────────────────────────────────────────────────────

def baseline(ids):
    """Without a model: on the UNEDITED workflow the kept tests should pass and the new ones (and checks) should fail,
    otherwise the case doesn't test the change."""
    for c in CASES:
        if ids and c["id"] not in ids:
            continue
        ctx = ctx_of(c)
        apis = B.load_apis(ctx["apis"])
        d = json.loads((BASE_DIR / f'{c["base"]}.json').read_text())
        kept_t, new_t = suites(c)
        k = run_hidden(kept_t, d, apis, ctx, "k")
        n = run_hidden(new_t, d, apis, ctx, "n")
        ch = [(name, fn(d)) for name, fn in c["checks"]]
        print(f'{c["id"]}: kept {sum(j["ok"] for j in k)}/{len(k)}  new already passing {sum(j["ok"] for j in n)}/{len(n)}  '
              f'checks already passing {sum(r is True for _, r in ch)}/{len(ch)}')
        for j in k:
            if not j["ok"]:
                print("    kept fails on base:", j["name"], "--", j["reason"][:200])
        for j in n:
            print(f'    new {"PASSES on base (does not discriminate)" if j["ok"] else "fails on base"}:', j["name"], "--", j["reason"][:160])


def rescore(mode: str):
    """Re-score saved edits after a scoring fix, without calling the model. The first-call verdict is recomputed only
    when the edit took one call (then first == final)."""
    out = HERE / "runs" / f"edit-{mode}"
    rows = json.loads((out / "results.json").read_text())
    cases = {c["id"]: c for c in CASES}
    for r in rows:
        c, f = cases[r["id"]], out / f'{r["id"]}.json'
        if not f.exists():
            continue
        ctx = ctx_of(c)
        apis = B.load_apis(ctx["apis"])
        current = json.loads((BASE_DIR / f'{c["base"]}.json').read_text())
        d = json.loads(f.read_text())
        rename = {}
        if mode == "patch":
            blocks = _blocks((out / f'{r["id"]}.reply.md').read_text())
            rename = dict(json.loads(re.sub(r",\s*([}\]])", r"\1", blocks[-1])).get("rename") or {}) if blocks else {}
        kept_t, new_t = suites(c)
        base_kept = {j["name"] for j in run_hidden(kept_t, current, apis, ctx, "b") if j["ok"]}
        fails = B.check(d, apis, ctx)
        a = {"fails": fails, "score": _score(c, d, current, kept_t, new_t, base_kept, apis, ctx, rename)}
        r.update(a["score"])
        r["final"], r["final_fails"] = _verdict(a), fails[:6]
        if r["calls"] == 1:
            r["first"] = r["final"]
        print(f'{r["id"]} ({mode}): pass={r["final"]["pass"]} new={r["new"]} kept={r["kept"]} drift={r["drift"]}')
    (out / "results.json").write_text(json.dumps(rows, indent=1, default=str))


def report():
    rows = {}
    for mode in ("full", "patch"):
        p = HERE / "runs" / f"edit-{mode}" / "results.json"
        if p.exists():
            rows[mode] = {r["id"]: r for r in json.loads(p.read_text())}
    lines = ["# Edit mode results", "", "Generated by `wf_edit.py --report`. Each case: one edit request on a live-tested workflow "
             "(runs/crazy-live). pass = platform checks clean + change landed (python checks + new hidden tests) + every base hidden test "
             "that still applies passes. clean = pass and no node outside the change was altered.", ""]
    for mode, rs in rows.items():
        v = list(rs.values())
        n = len(v)
        g = lambda k, s="final": sum(bool((r.get(s) or {}).get(k)) for r in v)  # noqa: E731
        avg = lambda k: round(sum(r.get(k) or 0 for r in v) / max(1, n), 1)  # noqa: E731
        lines += [f"## {mode}", "", f"- first call pass: {g('pass', 'first')}/{n}; final pass: {g('pass')}/{n}; clean (no drift): {g('clean')}/{n}",
                  f"- landed {g('landed')}/{n}, kept everything {g('kept')}/{n}",
                  f"- avg seconds {avg('seconds')}, avg calls {avg('calls')}, avg reply chars {avg('reply_chars')}, avg prompt chars {avg('prompt_chars')}", ""]
        lines += ["| case | pass | first | calls | s | reply chars | new | kept | drift | notes |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in v:
            dr = r.get("drift") or {}
            notes = "; ".join([f'{x["name"]}: {x["reason"][:90]}' for x in (r.get("new_fail") or []) + (r.get("kept_fail") or [])][:2]
                              + [f'{c["name"]}: {c["detail"][:90]}' for c in r.get("checks") or [] if not c["ok"]]
                              + [str(f)[:120] for f in (r.get("final_fails") or [])[:1]]
                              + (["ERROR"] if r.get("error") else []))
            lines.append(f'| {r["id"]} | {"✅" if r["final"].get("pass") else "❌"} | {"✅" if r["first"].get("pass") else "–"} | {r.get("calls")} | '
                         f'{r.get("seconds")} | {r.get("reply_chars")} | {r.get("new")} | {r.get("kept")} | '
                         f'{len(dr.get("changed", [])) + len(dr.get("removed", []))} nodes {dr.get("changed", []) + dr.get("removed", [])}'
                         f'{", " + str(len(dr["rewired"])) + " links" if dr.get("rewired") else ""} | {notes.replace("|", "/")} |')
        lines.append("")
    (HERE / "RESULTS-EDIT.md").write_text("\n".join(lines))
    print("\n".join(lines))


def main():
    args = sys.argv[1:]
    ids = [a for i, a in enumerate(args) if not a.startswith("--") and args[i - 1] not in ("--out", "--mode")]
    if "--report" in args:
        return report()
    if "--baseline" in args:
        return baseline(ids)
    if "--rescore" in args:
        return rescore(args[args.index("--mode") + 1])
    mode = args[args.index("--mode") + 1]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else HERE / "runs" / f"edit-{mode}"
    out.mkdir(parents=True, exist_ok=True)
    cases = [c for c in CASES if not ids or c["id"] in ids]
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(lambda c: _safe(mode, c, out), cases))
    prev = {r["id"]: r for r in json.loads((out / "results.json").read_text())} if (out / "results.json").exists() and ids else {}
    prev.update({r["id"]: r for r in results})
    (out / "results.json").write_text(json.dumps(list(prev.values()), indent=1, default=str))
    for r in results:
        print(f'[{"PASS" if r["final"].get("pass") else "FAIL"}] {r["id"]} ({mode}): first={r["first"].get("pass")} calls={r.get("calls")} '
              f'{r.get("seconds")}s new={r.get("new")} kept={r.get("kept")} drift={r.get("drift")}')
        for x in (r.get("new_fail") or []) + (r.get("kept_fail") or []):
            print("    ", x["name"], "--", x["reason"][:200])
        for f in (r.get("final_fails") or [])[:3]:
            print("     final:", str(f)[:220])
        if r.get("error"):
            print("     error:", r["error"][-600:])


if __name__ == "__main__":
    main()
