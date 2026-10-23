"""One request -> one model call -> check -> at most 2 fix calls.

    python build.py "track vendor invoices, over $5k needs approval"

Model: set ANTHROPIC_API_KEY (+ LLM_MODEL) to use the API, otherwise it
uses the local `claude` CLI (LLM_MODEL defaults to "sonnet" there).
Writes out/<slug>/{schema.json, actions.py, reply.md, checks.json}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import kit

HERE = Path(__file__).parent
PROMPT = (HERE / "PROMPT.md").read_text()
MAX_FIXES = 2


def call_model(prompt: str) -> str:
    model = os.environ.get("LLM_MODEL")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        body = json.dumps({"model": model, "max_tokens": 16000,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return "".join(b.get("text", "") for b in json.load(r)["content"])
    out = subprocess.run(["claude", "-p", "--model", model or "sonnet", "--tools", ""],
                         input=prompt, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(out.stderr or out.stdout)
    return out.stdout


def parse(reply: str) -> tuple[dict, str, str]:
    """Deterministic clean-up: pull out the two files, tolerate small slips."""
    def block(lang, name):
        m = re.search(rf'```{lang}[^\n]*{re.escape(name)}[^\n]*\n(.*?)\n```', reply, re.S) \
            or re.search(rf'```{lang}\s*\n(.*?)\n```', reply, re.S)
        if not m:
            raise ValueError(f"no ```{lang} block for {name} in the reply")
        return m.group(1)
    raw = block("json", "schema.json")
    raw = re.sub(r",\s*([}\]])", r"\1", raw)          # trailing commas
    schema = json.loads(raw)
    code = block("python", "actions.py")
    if "from kit import" not in code:
        code = "from kit import action, ActionError\n" + code
    assumed = next((l.strip() for l in reply.splitlines() if l.strip().startswith("Assumed")), "")
    return schema, code, assumed


def run_checks(reply: str, old_schema: dict | None = None):
    try:
        schema, code, assumed = parse(reply)
    except Exception as e:  # noqa: BLE001
        return None, None, "", [{"layer": "reply", "name": "reply has schema.json and actions.py",
                                 "ok": False, "error": f"{type(e).__name__}: {e}"}]
    return schema, code, assumed, kit.check(schema, code, old_schema)


def edit_prompt(change: str, schema: dict, code: str) -> str:
    return (PROMPT + "(editing an existing app; see below)\n\nThe app as it is now:\n\n"
            '```json file="schema.json"\n' + json.dumps(schema, indent=2) + "\n```\n\n"
            '```python file="actions.py"\n' + code + "\n```\n\n"
            "People already use this app, so its tables hold real rows. Add tables and columns "
            "freely, but don't remove, rename or retype existing ones; a new required column needs "
            'a "default" so existing rows still fit. Keep everything the change doesn\'t touch '
            "exactly as it is, update tests whose rule changed, and add tests for the new rules. "
            "Reply with all three parts in full.\n\nThe change:\n" + change)


def fix_prompt(request: str, reply: str, failed: list[dict]) -> str:
    lines = "\n".join(f'- [{c["layer"]}] {c["name"]}: {c["error"]}' for c in failed)
    return (PROMPT + request + "\n\nYour previous reply is below. These checks failed when we ran it:\n"
            + lines + "\n\nDecide for each failure whether the action or the test is wrong (the request "
            "is the source of truth), and reply again with all three parts in full.\n\n"
            "Previous reply:\n" + reply)


def build(request: str, out_dir: Path, edit_from: Path | None = None) -> dict:
    """New app from a request, or (edit_from=an app's folder) apply a change to it."""
    t0 = time.time()
    calls = []
    old_schema = None
    if edit_from:
        old_schema = json.loads((edit_from / "schema.json").read_text())
        first = edit_prompt(request, old_schema, (edit_from / "actions.py").read_text())
    else:
        first = PROMPT + request
    reply = call_model(first)
    calls.append(round(time.time() - t0, 1))
    schema, code, assumed, checks = run_checks(reply, old_schema)
    first_pass = all(c["ok"] for c in checks)
    first_failures = [c for c in checks if not c["ok"]]
    fixes = 0
    while not all(c["ok"] for c in checks) and fixes < MAX_FIXES:
        fixes += 1
        t1 = time.time()
        failed = [c for c in checks if not c["ok"]]
        if edit_from:
            p = edit_prompt(request, old_schema, (edit_from / "actions.py").read_text()) + \
                "\n\nYour previous reply is below. These checks failed when we ran it:\n" + \
                "\n".join(f'- [{c["layer"]}] {c["name"]}: {c["error"]}' for c in failed) + \
                "\n\nFix them and reply again with all three parts in full.\n\nPrevious reply:\n" + reply
        else:
            p = fix_prompt(request, reply, failed)
        reply = call_model(p)
        calls.append(round(time.time() - t1, 1))
        schema, code, assumed, checks = run_checks(reply, old_schema)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reply.md").write_text(reply)
    if schema is not None:
        (out_dir / "schema.json").write_text(json.dumps(schema, indent=2))
        (out_dir / "actions.py").write_text(code)
    (out_dir / "checks.json").write_text(json.dumps(checks, indent=2))
    return {"request": request, "assumed": assumed, "first_pass": first_pass,
            "final_pass": all(c["ok"] for c in checks), "fixes": fixes,
            "seconds": round(time.time() - t0, 1), "calls": calls,
            "tables": [t["name"] for t in (schema or {}).get("tables", [])],
            "first_failures": first_failures, "checks": checks}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def show(r: dict) -> None:
    mark = "PASS" if r["final_pass"] else "FAIL"
    print(f'\n[{mark}] {r["request"]}\n  {r["seconds"]}s, fixes={r["fixes"]}, first_pass={r["first_pass"]}')
    if r["assumed"]:
        print("  " + r["assumed"])
    for c in r.get("first_failures", []) if not r["first_pass"] else []:
        print(f'  first call failed: [{c["layer"]}] {c["name"]}: {c["error"][:160]}')
    for c in r["checks"]:
        print(f'  {"✓" if c["ok"] else "✗"} {c["layer"]:7} {c["name"]}' + ("" if c["ok"] else f'\n      {c["error"]}'))


if __name__ == "__main__":
    req = " ".join(sys.argv[1:]) or "Track vendor invoices. Anyone in AP can add one; over $5,000 needs a manager to approve before it can be paid."
    show(build(req, HERE / "out" / slug(req)))
