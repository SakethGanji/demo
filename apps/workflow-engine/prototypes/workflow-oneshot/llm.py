"""One model call. ANTHROPIC_API_KEY set: the Messages API with LLM_MODEL. Otherwise the `claude` CLI (`claude -p`,
default model sonnet), which uses whatever account the CLI is logged in with."""
import json
import os
import subprocess
import urllib.request


def call_model(prompt: str) -> str:
    model = os.environ.get("LLM_MODEL")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        body = json.dumps({"model": model, "max_tokens": 32000, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            return "".join(b.get("text", "") for b in json.load(r)["content"])
    out = subprocess.run(["claude", "-p", "--model", model or "sonnet", "--tools", ""],
                         input=prompt, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(out.stderr or out.stdout)
    return out.stdout
