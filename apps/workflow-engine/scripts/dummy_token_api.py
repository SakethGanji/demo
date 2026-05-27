"""Dummy token-protected API for UI testing of `$vars` secret redaction.

Standalone server. Only GET /protected with ?api_key=<exact token> succeeds —
everything else returns 401. Pair with the seeded "Vars Redaction Demo" workflow.

Usage
-----
1. Run this script in a separate terminal (engine venv works fine):

       venv/bin/python scripts/dummy_token_api.py

2. In workflow-studio, top-right env dropdown → "Manage variables…" →
   Add a variable:
       Key   : DEMO_API_TOKEN
       Value : demo_token_xyz_secure_123     (must match EXPECTED_TOKEN below)
       Type  : secret

3. Open the "Vars Redaction Demo" workflow in the studio. The HttpRequest
   node's URL already references {{ $vars.DEMO_API_TOKEN }}.

4. Click "Run". After execution:
     - Click the "Call Protected API" node to open its detail panel.
     - `responseStatusCode` should be 200 (real token reached this server).
     - `requestUrl` should display "...?api_key=***" — NEVER the plaintext.

If you mistype the token in step 2 (or skip it), this server returns 401
and you'll see the failure in the UI — proving authentication is real.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException, Request

EXPECTED_TOKEN = "demo_token_xyz_secure_123"
PORT = 8765

app = FastAPI(title="Dummy Token API", description="For $vars redaction demo")

_received: list[str] = []


@app.get("/protected")
async def protected(request: Request) -> dict[str, object]:
    key = request.query_params.get("api_key", "")
    _received.append(key)
    if key != EXPECTED_TOKEN:
        raise HTTPException(
            status_code=401,
            detail=f"bad token (received length={len(key)}, expected length={len(EXPECTED_TOKEN)})",
        )
    return {
        "ok": True,
        "message": "auth passed",
        "received_key_length": len(key),
        "echo_first_4": key[:4],
    }


@app.get("/_received")
async def received() -> dict[str, object]:
    """Inspection endpoint — shows what tokens the server has seen.
    Useful for confirming the engine forwarded the real plaintext."""
    return {"keys": _received, "count": len(_received)}


if __name__ == "__main__":
    print()
    print(f"  Dummy token API: http://127.0.0.1:{PORT}/protected")
    print(f"  Expected token : {EXPECTED_TOKEN!r}")
    print(f"  Inspect calls  : http://127.0.0.1:{PORT}/_received")
    print()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
