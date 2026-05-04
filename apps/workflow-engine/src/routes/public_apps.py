"""Public-facing routes for deployed apps.

Mounted at `/a` directly on the FastAPI app (NOT under `/api`) — these are
the URLs end-users see when visiting a published app.

Layout:
  GET  /a/{slug}                            HTML shell + bundle <script>
  GET  /a/{slug}/_assets/{hash}.{js|css}    immutable bundle assets
  POST /a/{slug}/_unlock                    accept password, set cookie
  POST /a/{slug}/api/workflow/{wid}/run     allow-listed workflow proxy

Access modes:
  private  → 404 (acts as if the slug doesn't exist)
  public   → served to anyone
  password → cookie-gated; missing/invalid cookie shows the password form

Bundle assets are fingerprinted by content hash and served with
`Cache-Control: public, max-age=1y, immutable`. ETag = the hash.
"""

from __future__ import annotations

import hmac
import html
import json
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..core.dependencies import get_app_service, get_api_test_repository
from ..core.public_tokens import PublicTokenError, issue_token, verify_token
from ..db.models import AppModel
from ..repositories import ApiTestRepository
from ..services.app_service import AppService

router = APIRouter(prefix="/a")


# Cookie name carrying the unlock token for `access='password'` apps. The
# cookie is path-scoped to /a/{slug} so unlocking one app doesn't unlock
# another, and HttpOnly + SameSite=Lax to keep CSRF risk minimal.
_UNLOCK_COOKIE = "app_unlock"
_UNLOCK_TTL_SECONDS = 60 * 60 * 12  # 12h


# ── Helpers ──────────────────────────────────────────────────────────────────


def _unlock_cookie_value(app: AppModel) -> str:
    """Build a deterministic-but-unforgeable cookie value tied to the current
    password hash. If the owner rotates the password, prior cookies stop
    matching automatically without any cookie eviction."""
    secret = (app.access_password_hash or "").encode("utf-8")
    msg = (app.id + "|" + (app.slug or "")).encode("utf-8")
    return hmac.new(secret, msg, "sha256").hexdigest() if secret else ""


def _check_unlock_cookie(app: AppModel, cookie: str | None) -> bool:
    if not cookie:
        return False
    expected = _unlock_cookie_value(app)
    if not expected:
        return False
    return hmac.compare_digest(expected, cookie)


async def _resolve_app(slug: str, service: AppService) -> AppModel:
    """Resolve a slug to a published app. Raises 404 for missing/private/
    not-yet-published apps — we deliberately don't differentiate so visitors
    can't probe for slug existence."""
    app = await service.get_app_by_slug(slug)
    if app is None:
        raise HTTPException(status_code=404, detail="not found")
    if app.access == "private":
        raise HTTPException(status_code=404, detail="not found")
    if app.published_version_id is None:
        raise HTTPException(status_code=404, detail="not found")
    return app


def _password_form(slug: str, app_name: str, error: str | None = None) -> str:
    err_html = (
        f'<p class="err">{html.escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(app_name)}</title>
<style>
:root{{color-scheme:light dark}}
body{{font-family:ui-sans-serif,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f7f7f8}}
.box{{background:#fff;padding:32px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);width:320px}}
h1{{margin:0 0 8px;font-size:18px}}
p{{color:#666;font-size:13px;margin:0 0 16px}}
.err{{color:#c00;font-size:12px;margin:8px 0 0}}
input{{width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font:inherit;box-sizing:border-box}}
button{{margin-top:12px;width:100%;padding:8px;border:0;border-radius:6px;background:#171717;color:#fff;font:inherit;cursor:pointer}}
@media (prefers-color-scheme:dark){{
  body{{background:#0a0a0a}} .box{{background:#1a1a1a;box-shadow:none}}
  p{{color:#999}} input{{background:#222;border-color:#333;color:#eee}}
  button{{background:#fafafa;color:#171717}}
}}
</style></head><body>
<form class="box" method="post" action="/a/{html.escape(slug)}/_unlock">
<h1>{html.escape(app_name)}</h1>
<p>This app is password-protected.</p>
<input type="password" name="password" placeholder="Password" required autofocus>
<button type="submit">Unlock</button>
{err_html}
</form></body></html>"""


def _runtime_inline() -> str:
    """JS that runs in the standalone HTML page after the bundle has loaded.

    Mounts the default export from window.__AppModule onto #root and rewrites
    outbound fetches:
      • `/api/...` and `/webhook/...`        → `/a/{slug}/api/...` (workflow proxy)
      • any URL matching window.__APP_API_MAP → `/a/{slug}/api-tester/replay/{id}`
        (external-URL replay proxy — the captured execution is replayed
        server-side; CSP `connect-src 'self'` stays intact.)
    """
    return r"""(function(){
'use strict';
var React=window.React, ReactDOM=window.ReactDOM;
var slug=window.__APP_SLUG, token=window.__APP_TOKEN;
var apiMap=window.__APP_API_MAP||{};
var base='/a/'+slug+'/api';
var origFetch=window.fetch.bind(window);

function normalizeBase(u){
  if(typeof u!=='string') return '';
  var i=u.indexOf('?'); if(i>=0) u=u.slice(0,i);
  return u.replace(/\/+$/,'');
}

function findReplayId(url){
  if(typeof url!=='string') return null;
  var b=normalizeBase(url);
  for(var k in apiMap){ if(normalizeBase(k)===b) return apiMap[k]; }
  return null;
}

window.fetch=function(url,opts){
  var o=opts||{};
  // 1) Workflow proxy paths
  if(typeof url==='string' && (url.indexOf('/api/')===0 || url.indexOf('/webhook/')===0)){
    var rewritten=base+url;
    var h1=Object.assign({},o.headers||{});
    if(token) h1['Authorization']='Bearer '+token;
    return origFetch(rewritten,Object.assign({},o,{headers:h1}));
  }
  // 2) External-URL replay (captured API tester executions)
  var replayId=findReplayId(url);
  if(replayId){
    var headers={'Content-Type':'application/json'};
    if(token) headers['Authorization']='Bearer '+token;
    var bodyStr=null;
    if(typeof o.body==='string') bodyStr=o.body;
    else if(o.body!=null) try{ bodyStr=JSON.stringify(o.body); }catch(e){ bodyStr=null; }
    return origFetch('/a/'+slug+'/api-tester/replay/'+replayId,{
      method:'POST',
      headers:headers,
      body:JSON.stringify({
        method:o.method||'GET',
        headers:o.headers||null,
        body:bodyStr,
      }),
    });
  }
  return origFetch(url,opts);
};

function ErrorBox(props){
  return React.createElement('div',{style:{padding:24,fontFamily:'monospace',fontSize:13,color:'#c00',whiteSpace:'pre-wrap'}},
    React.createElement('strong',null,'Runtime Error'),'\n\n',props.message);
}
var mod=window.__AppModule;
var mount=document.getElementById('root');
if(!mod||typeof mod.default!=='function'){
  ReactDOM.createRoot(mount).render(React.createElement(ErrorBox,{message:'App.tsx must export a default React component.'}));
  return;
}
ReactDOM.createRoot(mount).render(React.createElement(mod.default));
})();"""


def _html_shell(
    app: AppModel,
    version_id: int,
    bundle_hash: str,
    csp: str,
    api_map: dict[str, str],
) -> str:
    """Standalone HTML for the published app. Loads the fingerprinted assets,
    injects the slug + short-lived API token + captured-URL→execution-id map
    into globals, then runs the inline runtime."""
    slug = app.slug or ""
    token = issue_token(app.id, version_id)
    runtime = _runtime_inline()
    escaped_token = json.dumps(token)
    escaped_slug = json.dumps(slug)
    escaped_api_map = json.dumps(api_map)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>{html.escape(app.name)}</title>
<link rel="stylesheet" href="/a/{html.escape(slug)}/_assets/{bundle_hash}.css">
<style>html,body,#root{{height:100%;margin:0}}body{{font-family:ui-sans-serif,system-ui,sans-serif}}</style>
</head>
<body>
<div id="root"></div>
<script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
<script>window.__APP_SLUG={escaped_slug};window.__APP_TOKEN={escaped_token};window.__APP_API_MAP={escaped_api_map};</script>
<script src="/a/{html.escape(slug)}/_assets/{bundle_hash}.js"></script>
<script>{runtime}</script>
</body>
</html>"""


# CSP for the standalone page. Permits the CDN scripts we load and the inline
# bootstrap script. Connect-src 'self' so the running app can only call back
# into the engine (the public-app proxy enforces what's actually allowed).
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/{slug}", response_class=HTMLResponse)
async def render_app(
    slug: str,
    request: Request,
    app_unlock: str | None = Cookie(default=None, alias=_UNLOCK_COOKIE),
    service: AppService = Depends(get_app_service),
    api_test_repo: ApiTestRepository = Depends(get_api_test_repository),
):
    app = await _resolve_app(slug, service)

    # Password-gated apps require a valid unlock cookie.
    if app.access == "password" and not _check_unlock_cookie(app, app_unlock):
        return HTMLResponse(_password_form(slug, app.name), status_code=401)

    # Frame-ancestors policy is per-app: 'self' if embed disabled (still blocks
    # cross-origin embedding by default), '*' if explicitly opted in.
    csp = _DEFAULT_CSP
    if app.embed_enabled:
        csp = csp.replace("frame-ancestors 'none'", "frame-ancestors *")

    version = await service.get_published_version_response(app.published_version_id)
    if version is None or not version.bundle_hash:
        # Published row exists but bundle missing — should be rare (publish
        # always bundles). Surface a clear 500 so we notice in logs.
        raise HTTPException(status_code=500, detail="published bundle missing")

    # Build captured-URL → execution_id map so the inline runtime can route
    # external-URL fetches through /a/{slug}/api-tester/replay/{id}.
    api_map: dict[str, str] = {}
    allowed_ids = list(app.api_execution_ids or [])
    if allowed_ids:
        execs = await api_test_repo.get_many(allowed_ids)
        for ex in execs:
            api_map[ex.url] = ex.id

    body = _html_shell(app, version.id, version.bundle_hash, csp, api_map)
    return HTMLResponse(body, headers={"Content-Security-Policy": csp})


@router.get("/{slug}/_assets/{filename}")
async def get_asset(
    slug: str,
    filename: str,
    service: AppService = Depends(get_app_service),
):
    """Serve a bundle asset. Filename is `{hash}.{ext}` so the URL itself
    includes the cache-busting key — every publish produces new URLs and the
    old ones can be cached forever."""
    app = await _resolve_app(slug, service)

    try:
        hash_part, ext = filename.rsplit(".", 1)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    if ext not in {"js", "css"}:
        raise HTTPException(status_code=404, detail="not found")

    if app.published_version_id is None:
        raise HTTPException(status_code=404, detail="not found")

    asset = await service.get_published_asset(
        app.published_version_id, ext, hash_part
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="not found")

    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{asset.hash}"',
        },
    )


@router.post("/{slug}/_unlock")
async def unlock_app(
    slug: str,
    password: str = Form(...),
    service: AppService = Depends(get_app_service),
):
    app = await _resolve_app(slug, service)
    if app.access != "password":
        # Nothing to unlock; redirect to the app.
        return RedirectResponse(f"/a/{slug}", status_code=303)

    ok = await service.verify_app_password(app, password)
    if not ok:
        return HTMLResponse(
            _password_form(slug, app.name, error="Incorrect password."),
            status_code=401,
        )

    response = RedirectResponse(f"/a/{slug}", status_code=303)
    response.set_cookie(
        key=_UNLOCK_COOKIE,
        value=_unlock_cookie_value(app),
        max_age=_UNLOCK_TTL_SECONDS,
        path=f"/a/{slug}",
        httponly=True,
        samesite="lax",
        secure=False,  # toggled to True via reverse-proxy in production
    )
    return response


# ── Public-app API proxy ─────────────────────────────────────────────────────
#
# Apps deployed through this surface call back to the engine via fetches the
# inline runtime rewrites to /a/{slug}/api/.../... and stamps with a token.
# We currently expose just one operation: run a workflow, allow-listed via
# `apps.workflow_ids`. Add more endpoints here as the public-app surface
# expands; keep them token-gated and team-scoped.


def _verify_request_token(authorization: str | None) -> Any:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_token(token)
    except PublicTokenError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.post("/{slug}/api/workflow/{workflow_id}/run")
async def run_workflow_for_public_app(
    slug: str,
    workflow_id: str,
    request: Request,
    service: AppService = Depends(get_app_service),
):
    app = await _resolve_app(slug, service)
    claims = _verify_request_token(request.headers.get("authorization"))

    # Token must match this app + the currently-published version. If the
    # owner publishes a new version, old in-flight tokens stop working — that
    # is intentional and bounded by the 1h TTL.
    if claims.app_id != app.id or claims.version_id != app.published_version_id:
        raise HTTPException(status_code=401, detail="token scope mismatch")

    # Allow-list: app may only run workflows it explicitly references.
    allowed = set(app.workflow_ids or [])
    if workflow_id not in allowed:
        raise HTTPException(status_code=403, detail="workflow not allowed by this app")

    try:
        body = await request.json()
    except Exception:
        body = {}

    # The actual run-workflow path lives elsewhere; for now we just stub a
    # response so the route is wired and the frontend can be built against
    # it. Replace with a call into the real ExecutionService when its
    # public-friendly entry point is settled.
    return JSONResponse(
        {
            "ok": True,
            "workflow_id": workflow_id,
            "app_id": app.id,
            "echo": body,
            "note": "TODO wire into ExecutionService.run_workflow_async",
        }
    )


# ── External-URL replay proxy ────────────────────────────────────────────────
#
# The published page can call any URL captured via the API tester, but only
# through this route — and only the subset listed in `apps.api_execution_ids`.
# The URL itself is looked up server-side from the captured execution, so the
# caller cannot redirect requests to arbitrary hosts (no SSRF surface). Body
# and headers are caller-controlled so the LLM-generated UI can substitute
# user input into the captured request shape. For prod, harden further:
# allow-list of egress hosts via env var, per-app rate limits, and audit logs.

# Hop-by-hop / origin-revealing response headers we don't relay back.
_DROP_RESPONSE_HEADERS = {
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    "set-cookie",
    "server",
    "strict-transport-security",
    "content-encoding",  # httpx already decodes; preserving would mislabel bytes
    "content-length",  # set by FastAPI
}

_REPLAY_TIMEOUT_SECONDS = 30.0


class ReplayRequest(BaseModel):
    """Body the inline runtime sends to the replay proxy. method/headers/body
    come from the LLM-generated app's `fetch()` call — the URL+method are
    enforced from the captured execution server-side."""

    method: str | None = None
    headers: dict[str, str] | None = None
    body: str | None = None


@router.post("/{slug}/api-tester/replay/{execution_id}")
async def replay_api_test_execution(
    slug: str,
    execution_id: str,
    payload: ReplayRequest,
    request: Request,
    service: AppService = Depends(get_app_service),
    api_test_repo: ApiTestRepository = Depends(get_api_test_repository),
):
    app = await _resolve_app(slug, service)
    claims = _verify_request_token(request.headers.get("authorization"))

    if claims.app_id != app.id or claims.version_id != app.published_version_id:
        raise HTTPException(status_code=401, detail="token scope mismatch")

    allowed = set(app.api_execution_ids or [])
    if execution_id not in allowed:
        raise HTTPException(status_code=403, detail="execution not allowed by this app")

    captured = await api_test_repo.get(execution_id)
    if captured is None:
        raise HTTPException(status_code=404, detail="captured execution not found")

    # URL + method are enforced from the captured row. Headers/body are
    # caller-supplied so user input can flow through.
    method = (captured.method or "GET").upper()
    headers = dict(payload.headers or {})
    # Strip headers the upstream shouldn't see / browsers will mangle
    headers.pop("Authorization", None)
    headers.pop("authorization", None)
    headers.pop("Host", None)
    headers.pop("host", None)
    headers.pop("Cookie", None)
    headers.pop("cookie", None)
    body_bytes = payload.body.encode("utf-8") if payload.body is not None else None

    try:
        async with httpx.AsyncClient(timeout=_REPLAY_TIMEOUT_SECONDS, follow_redirects=True) as client:
            upstream = await client.request(method, captured.url, headers=headers, content=body_bytes)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream error: {type(e).__name__}: {e}") from e

    # Forward the response body bytes verbatim. Filter response headers so we
    # don't pass through hop-by-hop or origin-leaking ones.
    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
