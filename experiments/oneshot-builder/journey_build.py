"""Step 3: one model call writes a click-through journey for a built app; the robot
(ui/journey.mjs) runs it in Chromium on fresh data and checks the stored values.

    python journey_build.py APP_DIR "the original request" [--port 8150]

Needs APP_DIR/App.tsx built and passing (ui_build.py). Writes APP_DIR/journey.json.
No fix calls: a failure is reported for a person (or the next step) to judge,
because the model "fixing" its own test can hide a real bug.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from build import call_model
from ui_build import start_server, get

HERE = Path(__file__).parent
UI = HERE / "ui"


def prompt(app_dir: Path, request: str, port: int) -> str:
    app = get(port, "/api/app")
    samples = {t["name"]: get(port, f'/api/tables/{t["name"]}?limit=50')["rows"] for t in app["tables"]}
    steps = (UI / "journey.mjs").read_text().split("import fs")[0]
    return ((UI / "JOURNEY_PROMPT.md").read_text()
            .replace("{{STEPS}}", steps)
            .replace("{{EXAMPLE}}", (HERE / "apps" / "vendor-payments" / "journey.json").read_text())
            .replace("{{REQUEST}}", request)
            .replace("{{APP}}", json.dumps(app, indent=1))
            .replace("{{ACTIONS}}", (app_dir / "actions.py").read_text())
            .replace("{{SAMPLES}}", json.dumps(samples, indent=1, default=str))
            .replace("{{PAGE}}", (app_dir / "App.tsx").read_text()))


def run_journey(app_dir: Path, port: int) -> list:
    server = start_server(app_dir, port)  # fresh data
    try:
        out = subprocess.run(["node", str(UI / "journey.mjs"), f"http://127.0.0.1:{port}", str((app_dir / "journey.json").resolve())],
                             capture_output=True, text=True, timeout=600, cwd=UI)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return [{"step": "robot ran", "ok": False, "error": (out.stderr or out.stdout)[-1500:]}]
    finally:
        server.terminate()
        server.wait(10)


def build_journey(app_dir: Path, request: str, port: int = 8150) -> dict:
    t0 = time.time()
    server = start_server(app_dir, port)
    try:
        base = prompt(app_dir, request, port)
        reply = call_model(base)
    finally:
        server.terminate()
        server.wait(10)
    (app_dir / "journey-reply.md").write_text(reply)
    m = re.search(r'```json[^\n]*\n(.*?)\n```', reply, re.S)
    steps = json.loads(m.group(1)) if m else []
    (app_dir / "journey.json").write_text(json.dumps(steps, indent=1))
    results = run_journey(app_dir, port) if steps else [{"step": "reply has journey.json", "ok": False, "error": "no json block"}]
    failed = next((r for r in results if not r["ok"]), None)
    verdict = ""
    if failed and steps:
        # One judge-and-fix call. It must say whose fault it is; an app bug stops here for a person.
        judge = call_model(base + f"\n\nYour journey failed at step {len(results)}: {failed['step']} (as {failed.get('as')})\n"
                           f"Robot said: {failed['error']}\n\nDecide from actions.py and App.tsx whose fault it is. Reply with one line, either:\n"
                           "  Test wrong: <why>   followed by the full corrected journey.json block, or\n"
                           "  App bug: <what the app does wrong>   and nothing else.\n\nYour journey was:\n" + reply)
        verdict = next((l for l in judge.splitlines() if l.startswith(("Test wrong", "App bug"))), judge[:200])
        m = re.search(r'```json[^\n]*\n(.*?)\n```', judge, re.S)
        if verdict.startswith("Test wrong") and m:
            reply, steps = judge, json.loads(m.group(1))
            (app_dir / "journey-reply.md").write_text(reply)
            (app_dir / "journey.json").write_text(json.dumps(steps, indent=1))
            results = run_journey(app_dir, port)
            failed = next((r for r in results if not r["ok"]), None)
    return {"app": app_dir.name, "steps": len(steps), "ran": len(results), "pass": not failed and len(results) == len(steps),
            "covers": next((l for l in reply.splitlines() if l.startswith("Covers")), ""), "verdict": verdict,
            "failed": failed, "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8150
    args = [a for i, a in enumerate(args) if a != "--port" and (i == 0 or args[i - 1] != "--port")]
    r = build_journey(Path(args[0]), args[1] if len(args) > 1 else "", port)
    print(f'\n[{"PASS" if r["pass"] else "FAIL"}] {r["app"]}: {r["ran"]}/{r["steps"]} steps, {r["seconds"]}s')
    print("  " + r["covers"])
    if r["failed"]:
        print(f'  failed at {r["failed"]["step"]} (as {r["failed"].get("as")}):\n    {r["failed"]["error"]}')
