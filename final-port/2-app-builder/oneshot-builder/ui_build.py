"""Step 2: one model call writes the page; the platform type-checks, bundles and
renders it in Chromium against the running backend; at most 2 fix calls.

    python ui_build.py APP_DIR "the original request" [--port 8130]

Writes APP_DIR/App.tsx and APP_DIR/ui/ (index.html, app.js, screenshot-<role>.png).
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from build import call_model

HERE = Path(__file__).parent
UI = HERE / "ui"
MAX_FIXES = 2


def start_server(app_dir: Path, port: int):
    (app_dir / "app.db").unlink(missing_ok=True)  # fresh sample data
    p = subprocess.Popen(["uv", "run", "--no-project", "--with", "fastapi", "--with", "uvicorn", "python",
                          str(HERE / "server.py"), str(app_dir), "--port", str(port)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, cwd=HERE)
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/app")
            return p
        except Exception:  # noqa: BLE001
            if p.poll() is not None:
                raise RuntimeError("server failed: " + p.stderr.read())
            time.sleep(0.2)
    raise RuntimeError("server didn't start")


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.load(r)


def prompt(request: str, port: int, current: str = "", change: str = "") -> str:
    app = get(port, "/api/app")
    samples = {t["name"]: get(port, f'/api/tables/{t["name"]}?limit=3')["rows"] for t in app["tables"]}
    kit = (UI / "kit.tsx").read_text().split("const CSS =")[0]
    edit = ("" if not change else
            "\n\nThe page exists and people use it. The backend above already has this change; update the page for it "
            "and keep everything else as it is.\n\nThe change: " + change +
            '\n\nThe current page:\n```tsx file="App.tsx"\n' + current + "\n```\n")
    return (((UI / "UI_PROMPT.md").read_text() + edit)
            .replace("{{EXAMPLE}}", (UI / "example-App.tsx").read_text())
            .replace("{{KIT}}", kit)
            .replace("{{REQUEST}}", request)
            .replace("{{APP}}", json.dumps(app, indent=1))
            .replace("{{SAMPLES}}", json.dumps(samples, indent=1, default=str)))


def parse(reply: str) -> str:
    m = re.search(r'```tsx[^\n]*App\.tsx[^\n]*\n(.*?)\n```', reply, re.S) or re.search(r'```tsx\s*\n(.*?)\n```', reply, re.S)
    if not m:
        raise ValueError("no ```tsx file=\"App.tsx\" block in the reply")
    return m.group(1)


def check(app_dir: Path, port: int) -> list:
    out = subprocess.run(["node", str(UI / "check.mjs"), str(app_dir), f"http://127.0.0.1:{port}"],
                         capture_output=True, text=True, timeout=300)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return [{"layer": "page", "name": "checker ran", "ok": False, "error": (out.stderr or out.stdout)[-2000:]}]


def build_ui(app_dir: Path, request: str, port: int = 8130, change: str = "") -> dict:
    """New page, or (change=...) update the existing App.tsx for a change already made to the backend."""
    t0 = time.time()
    server = start_server(app_dir, port)
    try:
        current = (app_dir / "App.tsx").read_text() if change else ""
        base = prompt(request, port, current, change)
        reply = call_model(base)
        fixes, first = 0, None
        while True:
            try:
                (app_dir / "App.tsx").write_text(parse(reply))
                checks = check(app_dir, port)
            except ValueError as e:
                checks = [{"layer": "page", "name": "reply has App.tsx", "ok": False, "error": str(e)}]
            if first is None:
                first = checks
            failed = [c for c in checks if not c["ok"]]
            if not failed or fixes >= MAX_FIXES:
                break
            fixes += 1
            reply = call_model(base + "\n\nYour previous App.tsx is below. These checks failed when we ran it:\n"
                               + "\n".join(f'- {c["name"]}: {c["error"]}' for c in failed)
                               + "\n\nFix them and reply again with the Built: line and the full file.\n\n"
                               + "Previous reply:\n" + reply)
        (app_dir / "ui-reply.md").write_text(reply)
        built = next((l for l in reply.splitlines() if l.startswith("Built")), "")
        return {"app": app_dir.name, "first_pass": all(c["ok"] for c in first), "final_pass": not failed,
                "fixes": fixes, "seconds": round(time.time() - t0, 1), "built": built,
                "first_failures": [c for c in first if not c["ok"]], "checks": checks,
                "lines": len((app_dir / "App.tsx").read_text().splitlines()) if (app_dir / "App.tsx").exists() else 0}
    finally:
        server.terminate()
        server.wait(10)


def show(r):
    print(f'\n[{"PASS" if r["final_pass"] else "FAIL"}] {r["app"]}: {r["seconds"]}s, fixes={r["fixes"]}, '
          f'first_pass={r["first_pass"]}, {r["lines"]} lines')
    if r["built"]:
        print("  " + r["built"])
    for c in r["first_failures"] if not r["first_pass"] else []:
        print(f'  first call failed: {c["name"]}: {c["error"][:300]}')
    for c in r["checks"]:
        print(f'  {"✓" if c["ok"] else "✗"} {c["name"]}' + ("" if c["ok"] else f'\n      {c["error"][:500]}'))


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8130
    args = [a for i, a in enumerate(args) if a != "--port" and (i == 0 or args[i - 1] != "--port")]
    show(build_ui(Path(args[0]), args[1] if len(args) > 1 else "", port))
