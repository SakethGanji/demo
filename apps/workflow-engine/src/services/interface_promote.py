"""Promote to app (Stage 5, DESIGN-STAGE5-UIS section 6.3).

Creates an ``AppModel`` that runs this workflow (``workflow_ids=[id]``) from generated source: ``App.tsx`` (the form /
chat / custom screens as plain JSX) plus ``api/client.ts`` (the typed interface client). The interface gets
``app_id`` and the draft is saved. Idempotent: a second call returns the existing app (200). From then on the
existing app builder (and its AI) edits the app; the spec editor in the studio turns read-only.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.exceptions import WorkflowNotFoundError
from .interface_client_codegen import generate_interface_client, pascal
from .interface_service import InterfaceError


def generate_app_tsx(definition: dict[str, Any], interface: dict[str, Any]) -> str:
    """A small, dependency-free React app for the interface (a starting point for the app builder AI)."""
    from ..engine import form_fields as ff
    from ..schemas import interface as schema

    trigger = next((n for n in definition.get("nodes") or [] if n.get("name") == interface.get("trigger_node")), None)
    params = (trigger or {}).get("parameters") or {}
    fields = ff.derive_fields(params.get("input_schema"), params.get("fields_ui"), params.get("field_order"))
    title = interface.get("title") or "Workflow"
    start = (interface.get("screens") or {}).get("start") or {}
    P = pascal(interface.get("slug") or "interface")
    kind = interface.get("kind")
    body = _CHAT_BODY if kind == "chat" else _FORM_BODY
    return f"""import {{ useEffect, useRef, useState }} from 'react'
import {{ createClient }} from './api/client'

// Generated from the workflow interface "{_js(interface.get('slug'))}". Edit freely: the app builder owns this file now.
const TITLE = {json.dumps(title)}
const DESCRIPTION = {json.dumps(interface.get('description') or '')}
const SUBMIT_LABEL = {json.dumps(params.get('submit_label') or 'Submit')}
const FIELDS = {json.dumps(fields, indent=2)} as const
const START_SCREEN = {json.dumps(start, indent=2)}
const client = createClient('')
type {P}Fields = Record<string, string | number | boolean>

{body}
""".replace("__P__", P)


def _js(value: Any) -> str:
    return str(value or "").replace('"', "")


_FORM_BODY = """export default function App() {
  const [values, setValues] = useState<__P__Fields>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [run, setRun] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const key = useRef(crypto.randomUUID())

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrors({})
    try {
      const { execution_id, run_token } = await client.startRun(values as any, key.current)
      await client.watchRun(execution_id, run_token, setRun)
    } catch (err: any) {
      setErrors(err?.body?.detail?.fields ?? { '': 'Something went wrong' })
    } finally {
      setBusy(false)
    }
  }

  if (run) {
    return (
      <main style={{ maxWidth: 560, margin: '40px auto', fontFamily: 'system-ui' }}>
        <h1>{TITLE}</h1>
        <p data-testid="run-status">{run.status === 'waiting' ? `Waiting for ${run.waiting_for?.[0]?.title ?? 'review'}` : run.status}</p>
        {run.result && <pre>{JSON.stringify(run.result, null, 2)}</pre>}
        {run.message && <p>{run.message}</p>}
        {run.error && <p role="alert">{run.error.message}</p>}
      </main>
    )
  }
  return (
    <main style={{ maxWidth: 560, margin: '40px auto', fontFamily: 'system-ui' }}>
      <h1>{TITLE}</h1>
      {DESCRIPTION && <p>{DESCRIPTION}</p>}
      <form onSubmit={submit}>
        {FIELDS.map((f: any) => (
          <label key={f.name} style={{ display: 'block', margin: '12px 0' }}>
            {f.label}
            <input
              name={f.name}
              type={f.widget === 'number' || f.widget === 'currency' ? 'number' : f.widget === 'email' ? 'email' : f.widget === 'date' ? 'date' : 'text'}
              required={f.required}
              onChange={(e) => setValues({ ...values, [f.name]: f.widget === 'number' || f.widget === 'currency' ? Number(e.target.value) : e.target.value })}
              style={{ display: 'block', width: '100%' }}
            />
            {errors[f.name] && <span role="alert">{errors[f.name]}</span>}
          </label>
        ))}
        <button type="submit" disabled={busy}>{SUBMIT_LABEL}</button>
      </form>
    </main>
  )
}"""

_CHAT_BODY = """export default function App() {
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { void START_SCREEN; void FIELDS }, [])

  async function send(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: text }])
    setText('')
    // Wire the chat endpoints of /api/i/<project>/<slug>/chat/sessions here (see the generated client for the run verbs).
    setBusy(false)
  }

  return (
    <main style={{ maxWidth: 560, margin: '40px auto', fontFamily: 'system-ui' }}>
      <h1>{TITLE}</h1>
      <ul>{messages.map((m, i) => <li key={i}><b>{m.role}</b> {m.content}</li>)}</ul>
      <form onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} style={{ width: '80%' }} />
        <button type="submit" disabled={busy}>Send</button>
      </form>
    </main>
  )
}"""


async def promote(session: Any, workflow_id: str, *, project_slug: str) -> tuple[int, dict[str, Any]]:
    """Returns ``(201, {app_id, app_url})`` for a new app, ``(200, ...)`` when the interface already has one."""
    from ..core.dependencies import get_bundle_storage, get_file_storage
    from ..db.models import AppModel
    from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition
    from ..schemas.app import AppCreateRequest, AppFilePayload, AppUpdateRequest
    from .app_service import AppPublishError, AppService, SlugTakenError

    repo = WorkflowRepository(session)
    stored = await repo.get(workflow_id)
    if stored is None:
        raise WorkflowNotFoundError(workflow_id)
    definition = workflow_to_definition(stored.workflow)
    iface = definition.get("interface")
    if not isinstance(iface, dict) or not iface:
        raise InterfaceError(409, "NO_INTERFACE", "This workflow has no interface yet.")
    existing = iface.get("app_id")
    if existing:
        row = await session.get(AppModel, existing)
        if row is not None:
            return 200, {"app_id": row.id, "app_url": f"/a/{row.slug}" if row.slug else f"/apps/{row.id}"}

    svc = AppService(session, get_file_storage(session), get_bundle_storage(session))
    created = await svc.create_app(AppCreateRequest(
        name=f"{iface.get('title') or stored.name} (app)", definition={}, workflow_ids=[workflow_id], project_id=stored.project_id))
    files = [
        AppFilePayload(path="App.tsx", content=generate_app_tsx(definition, iface), file_type="tsx"),
        AppFilePayload(path="api/client.ts", file_type="ts", content=generate_interface_client(
            definition, iface, project_slug, workflow_id=workflow_id, project_id=stored.project_id)),
    ]
    base = f"{iface.get('slug')}-app"
    slug = base
    for attempt in range(5):
        try:
            await svc.update_app(created.id, AppUpdateRequest(
                slug=slug, source_code=files[0].content, create_version=True, version_trigger="manual", files=files))
            break
        except SlugTakenError:
            slug = f"{base}-{attempt + 2}"
        except AppPublishError as e:
            raise InterfaceError(422, "PROMOTE_FAILED", str(e)) from e
    else:
        raise InterfaceError(409, "APP_SLUG_TAKEN", "Couldn't find a free app address.")
    await repo.set_interface(workflow_id, {**iface, "app_id": created.id})
    return 201, {"app_id": created.id, "app_url": f"/a/{slug}"}
