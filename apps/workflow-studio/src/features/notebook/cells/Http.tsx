/**
 * API testing on the HTTP cell: the request editor (method, URL with {{base_url}}, params, headers, auth by a
 * project secret's key, JSON or form body, sentence assertions, saved variables) and the result body (one sentence
 * per check with the actual value, saved variables, timing and size). Secrets are referenced by key, never shown.
 */
import { useState } from 'react'
import { Chip, StatusMark } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { useSecrets } from '@/features/project/hooks/useProjectData'
import { useNb } from '../context'
import { fmtMs, plural } from '../lib/cells'
import type { AssertionKind, AssertionOp, AssertionResult, Cell, HttpAssertion, HttpAuth, HttpExtract, HttpMethod, HttpOutput, HttpSpec } from '../types'

const METHODS: HttpMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']
const OP_WORD: Record<AssertionOp, string> = {
  eq: 'is', ne: 'is not', lt: 'is under', le: 'is at most', gt: 'is over', ge: 'is at least', contains: 'contains',
  not_contains: 'does not contain', exists: 'exists', not_exists: 'is absent', matches: 'matches', in: 'is one of',
}
const OPS_FOR: Record<AssertionKind, AssertionOp[]> = {
  status: ['eq', 'ne', 'lt', 'ge', 'in'],
  time: ['lt', 'le'],
  json_path: ['eq', 'ne', 'exists', 'not_exists', 'contains', 'not_contains', 'gt', 'lt', 'ge', 'le', 'matches', 'in'],
  header: ['exists', 'not_exists', 'eq', 'contains', 'matches'],
  schema: ['eq'],
}
const NEEDS_VALUE = (op: AssertionOp) => op !== 'exists' && op !== 'not_exists'

/** "200" -> 200, "true" -> true, '["a"]' -> ["a"], else the text. */
export function parseValue(t: string): unknown {
  const s = t.trim()
  if (s === '') return ''
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s)
  if (s === 'true' || s === 'false') return s === 'true'
  if (s === 'null') return null
  if (/^[[{]/.test(s)) {
    try {
      return JSON.parse(s)
    } catch {
      return t
    }
  }
  return t
}
const showValue = (v: unknown) => (v === undefined ? '' : typeof v === 'string' ? v : JSON.stringify(v))

/** The sentence an assertion reads as (the engine says the same in its result's label). */
export function assertionText(a: HttpAssertion): string {
  const op = a.op ?? (a.kind === 'time' ? 'lt' : 'eq')
  const v = showValue(a.value)
  switch (a.kind) {
    case 'status':
      return op === 'eq' ? `Status is ${v}` : `Status ${OP_WORD[op]} ${v}`
    case 'time':
      return `Responds in under ${v} ms`
    case 'json_path':
      return NEEDS_VALUE(op) ? `${a.path} ${OP_WORD[op]} ${v}` : `${a.path} ${OP_WORD[op]}`
    case 'header':
      return NEEDS_VALUE(op) ? `Header ${a.name} ${OP_WORD[op]} ${v}` : op === 'exists' ? `Header ${a.name} is present` : `Header ${a.name} is absent`
    case 'schema':
      return 'Body matches the schema'
  }
}

// ── key/value rows ──────────────────────────────────────────────────────────────────────────────────────────────

function Pairs({ label, value, onChange, keyPh, valPh }: { label: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; keyPh: string; valPh: string }) {
  const entries = Object.entries(value)
  const [k, setK] = useState('')
  const [v, setV] = useState('')
  const put = (i: number, nk: string, nv: unknown) => onChange(Object.fromEntries(entries.map(([ek, ev], j) => (j === i ? [nk, nv] : [ek, ev]))))
  const add = () => {
    if (!k.trim()) return
    onChange({ ...value, [k.trim()]: v })
    setK('')
    setV('')
  }
  return (
    <div className="nb-http-pairs" aria-label={label}>
      {entries.map(([ek, ev], i) => (
        <div key={i} className="nb-http-pair">
          <input className="nb-input nb-mono" value={ek} onChange={(e) => put(i, e.target.value, ev)} aria-label={`${label} name`} />
          <input className="nb-input nb-mono" value={showValue(ev)} onChange={(e) => put(i, ek, e.target.value)} aria-label={`${label} value ${ek}`} />
          <button type="button" className="gl-icon-btn" aria-label={`Remove ${ek}`} onClick={() => onChange(Object.fromEntries(entries.filter((_, j) => j !== i)))}>×</button>
        </div>
      ))}
      <div className="nb-http-pair">
        <input className="nb-input nb-mono" value={k} onChange={(e) => setK(e.target.value)} placeholder={keyPh} aria-label={`New ${label} name`} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), add())} />
        <input className="nb-input nb-mono" value={v} onChange={(e) => setV(e.target.value)} placeholder={valPh} aria-label={`New ${label} value`} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), add())} />
        <button type="button" className="gl-icon-btn" aria-label={`Add ${label}`} disabled={!k.trim()} onClick={add}>+</button>
      </div>
    </div>
  )
}

// ── the request editor (controlled) ─────────────────────────────────────────────────────────────────────────────

type Section = 'params' | 'headers' | 'auth' | 'body' | 'checks' | 'save'

export function HttpFields({ spec, onChange }: { spec: HttpSpec; onChange: (s: HttpSpec) => void }) {
  const { pid } = useNb()
  const secrets = useSecrets(pid)
  const secretKeys = [...new Set((secrets.data ?? []).filter((x) => x.type === 'secret').map((x) => x.key))].sort()
  const [tab, setTab] = useState<Section>('checks')
  const method = spec.method ?? 'GET'
  const hasBody = method !== 'GET' && method !== 'HEAD'
  const set = (patch: Partial<HttpSpec>) => onChange({ ...spec, ...patch })
  const auth: HttpAuth = spec.auth ?? { type: 'none' }
  const assertions = spec.assertions ?? []
  const extract = spec.extract ?? []
  const [bodyText, setBodyText] = useState(() => (spec.body === undefined || spec.body === null ? '' : typeof spec.body === 'string' ? spec.body : JSON.stringify(spec.body, null, 2)))
  const [bodyErr, setBodyErr] = useState<string | null>(null)
  const count = (n: number) => (n ? ` ${n}` : '')
  const tabs: { id: Section; label: string; hide?: boolean }[] = [
    { id: 'checks', label: `Checks${count(assertions.length)}` },
    { id: 'params', label: `Params${count(Object.keys(spec.params ?? {}).length)}` },
    { id: 'headers', label: `Headers${count(Object.keys(spec.headers ?? {}).length)}` },
    { id: 'auth', label: auth.type === 'none' ? 'Auth' : `Auth · ${auth.type.replace('_', ' ')}` },
    { id: 'body', label: 'Body', hide: !hasBody },
    { id: 'save', label: `Save values${count(extract.length)}` },
  ]
  const putA = (i: number, a: HttpAssertion) => set({ assertions: assertions.map((x, j) => (j === i ? a : x)) })
  const putE = (i: number, e: HttpExtract) => set({ extract: extract.map((x, j) => (j === i ? e : x)) })
  return (
    <div className="nb-http-ed" data-testid="nb-http-editor">
      <div className="nb-http-line">
        <select className="nb-select nb-http-method" value={method} onChange={(e) => set({ method: e.target.value as HttpMethod })} aria-label="Method" data-m={method}>
          {METHODS.map((m) => <option key={m}>{m}</option>)}
        </select>
        <input className="nb-input nb-mono nb-http-url" value={spec.url ?? ''} onChange={(e) => set({ url: e.target.value })} placeholder="{{base_url}}/refunds" aria-label="URL" />
      </div>
      {spec.api_ref && <div className="nb-faint">from the API catalog · {spec.api_ref}</div>}
      <div className="nb-http-tabs" role="tablist" aria-label="Request parts">
        {tabs.filter((t) => !t.hide).map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className="nb-http-tab" onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>
      {tab === 'params' && <Pairs label="Param" value={spec.params ?? {}} onChange={(p) => set({ params: p })} keyPh="limit" valPh="5 or {{row.id}}" />}
      {tab === 'headers' && (
        <>
          <Pairs label="Header" value={spec.headers ?? {}} onChange={(h) => set({ headers: h as Record<string, string> })} keyPh="X-Request-Id" valPh="{{token}}" />
          <div className="nb-faint">Credentials go under Auth, never here: a header value is stored with the notebook.</div>
        </>
      )}
      {tab === 'auth' && (
        <div className="nb-http-auth">
          <select className="nb-select" value={auth.type} onChange={(e) => set({ auth: e.target.value === 'none' ? null : { ...auth, type: e.target.value as HttpAuth['type'] } })} aria-label="Auth type">
            <option value="none">No auth</option>
            <option value="bearer">Bearer token</option>
            <option value="basic">Basic (user + password)</option>
            <option value="api_key">API key</option>
          </select>
          {auth.type === 'basic' && <input className="nb-input" style={{ width: 140 }} value={auth.username ?? ''} onChange={(e) => set({ auth: { ...auth, username: e.target.value } })} placeholder="username" aria-label="Username" />}
          {auth.type === 'api_key' && (
            <>
              <input className="nb-input nb-mono" style={{ width: 130 }} value={auth.name ?? ''} onChange={(e) => set({ auth: { ...auth, name: e.target.value } })} placeholder="X-API-Key" aria-label="Key name" />
              <select className="nb-select" value={auth.add_to ?? 'header'} onChange={(e) => set({ auth: { ...auth, add_to: e.target.value as 'header' | 'query' } })} aria-label="Send the key in">
                <option value="header">as a header</option>
                <option value="query">on the query</option>
              </select>
            </>
          )}
          {auth.type !== 'none' && (
            <>
              <select className="nb-select" value={auth.secret ?? ''} onChange={(e) => set({ auth: { ...auth, secret: e.target.value || null } })} aria-label="Secret">
                <option value="">{auth.type === 'basic' ? 'password secret…' : 'secret…'}</option>
                {secretKeys.map((k) => <option key={k} value={k}>🔒 {k}</option>)}
                {auth.secret && !secretKeys.includes(auth.secret) && <option value={auth.secret}>🔒 {auth.secret} (not set here)</option>}
              </select>
              <span className="nb-faint">The value stays in project secrets; the run fills it in and redacts it.</span>
            </>
          )}
        </div>
      )}
      {tab === 'body' && hasBody && (
        <>
          <div className="nb-row">
            <select className="nb-select" value={spec.body_type ?? 'json'} onChange={(e) => set({ body_type: e.target.value as 'json' | 'form' })} aria-label="Body type">
              <option value="json">JSON</option>
              <option value="form">Form (urlencoded)</option>
            </select>
          </div>
          {(spec.body_type ?? 'json') === 'form' ? (
            <Pairs label="Field" value={(spec.body && typeof spec.body === 'object' && !Array.isArray(spec.body) ? spec.body : {}) as Record<string, unknown>} onChange={(b) => set({ body: b })} keyPh="amount" valPh="10" />
          ) : (
            <textarea
              className="nb-textarea nb-mono"
              value={bodyText}
              aria-label="Body"
              placeholder={'{"order_id": "{{order_id}}", "amount": 10}'}
              onChange={(e) => {
                setBodyText(e.target.value)
                const t = e.target.value.trim()
                if (!t) {
                  setBodyErr(null)
                  return set({ body: null })
                }
                try {
                  set({ body: JSON.parse(t) })
                  setBodyErr(null)
                } catch {
                  setBodyErr('Not JSON yet · it is sent as plain text until it parses')
                  set({ body: e.target.value })
                }
              }}
            />
          )}
          {bodyErr && <div className="nb-faint">{bodyErr}</div>}
        </>
      )}
      {tab === 'checks' && (
        <div className="nb-http-checks">
          {assertions.map((a, i) => <AssertionRow key={i} a={a} onChange={(x) => putA(i, x)} onRemove={() => set({ assertions: assertions.filter((_, j) => j !== i) })} />)}
          <div className="nb-row">
            <span className="nb-faint">Add a check:</span>
            {([['status', 'Status'], ['time', 'Time'], ['json_path', 'JSON value'], ['header', 'Header'], ['schema', 'Schema']] as const).map(([k, t]) => (
              <button key={k} type="button" className="nb-linkbtn text-[12px]" onClick={() => set({ assertions: [...assertions, NEW_ASSERTION[k]] })} data-testid={`nb-add-check-${k}`}>+ {t}</button>
            ))}
          </div>
          {spec.expect?.status ? <div className="nb-faint">Also expects status {spec.expect.status} (older form).</div> : null}
        </div>
      )}
      {tab === 'save' && (
        <div className="nb-http-checks">
          {extract.map((x, i) => (
            <div key={i} className="nb-http-pair">
              <span className="nb-faint">save</span>
              <select className="nb-select" value={x.source ?? 'json_path'} onChange={(e) => putE(i, { ...x, source: e.target.value as HttpExtract['source'] })} aria-label="From">
                <option value="json_path">JSON value</option>
                <option value="header">header</option>
                <option value="status">status</option>
              </select>
              {(x.source ?? 'json_path') !== 'status' && <input className="nb-input nb-mono" value={x.path ?? ''} onChange={(e) => putE(i, { ...x, path: e.target.value })} placeholder={(x.source ?? 'json_path') === 'header' ? 'Location' : '$.token'} aria-label="Path" />}
              <span className="nb-faint">as</span>
              <input className="nb-input nb-mono" style={{ width: 110 }} value={x.name} onChange={(e) => putE(i, { ...x, name: e.target.value })} placeholder="token" aria-label="Variable name" />
              <button type="button" className="gl-icon-btn" aria-label="Remove" onClick={() => set({ extract: extract.filter((_, j) => j !== i) })}>×</button>
            </div>
          ))}
          <button type="button" className="nb-linkbtn text-[12px]" onClick={() => set({ extract: [...extract, { name: '', source: 'json_path', path: '$.' }] })} data-testid="nb-add-extract">+ Save a value for later requests</button>
          <div className="nb-faint">Later cells use it as {'{{token}}'} (or {'{{vars.token}}'}).</div>
        </div>
      )}
    </div>
  )
}

const NEW_ASSERTION: Record<AssertionKind, HttpAssertion> = {
  status: { kind: 'status', op: 'eq', value: 200 },
  time: { kind: 'time', op: 'lt', value: 500 },
  json_path: { kind: 'json_path', op: 'exists', path: '$.id' },
  header: { kind: 'header', op: 'exists', name: 'Content-Type' },
  schema: { kind: 'schema', json_schema: { type: 'object', required: ['id'] } },
}

function AssertionRow({ a, onChange, onRemove }: { a: HttpAssertion; onChange: (a: HttpAssertion) => void; onRemove: () => void }) {
  const op = a.op ?? (a.kind === 'time' ? 'lt' : 'eq')
  const [schemaText, setSchemaText] = useState(() => JSON.stringify(a.json_schema ?? {}, null, 2))
  const [schemaErr, setSchemaErr] = useState(false)
  return (
    <div className="nb-http-check" data-testid="nb-check-edit" data-kind={a.kind}>
      {a.kind === 'status' && <span>Status</span>}
      {a.kind === 'time' && <span>Responds in under</span>}
      {a.kind === 'json_path' && <input className="nb-input nb-mono" style={{ width: 140 }} value={a.path ?? ''} onChange={(e) => onChange({ ...a, path: e.target.value })} aria-label="JSON path" placeholder="$.id" />}
      {a.kind === 'header' && (
        <>
          <span>Header</span>
          <input className="nb-input nb-mono" style={{ width: 130 }} value={a.name ?? ''} onChange={(e) => onChange({ ...a, name: e.target.value })} aria-label="Header name" />
        </>
      )}
      {a.kind === 'schema' ? (
        <>
          <span>Body matches the schema</span>
          <textarea
            className="nb-textarea nb-mono"
            value={schemaText}
            aria-label="JSON schema"
            onChange={(e) => {
              setSchemaText(e.target.value)
              try {
                const v = JSON.parse(e.target.value)
                if (v && typeof v === 'object') {
                  setSchemaErr(false)
                  onChange({ ...a, json_schema: v })
                }
              } catch {
                setSchemaErr(true)
              }
            }}
          />
          {schemaErr && <span className="nb-err">not JSON yet</span>}
        </>
      ) : (
        <>
          {a.kind !== 'time' && (
            <select className="nb-select" value={op} onChange={(e) => onChange({ ...a, op: e.target.value as AssertionOp })} aria-label="Check">
              {OPS_FOR[a.kind].map((o) => <option key={o} value={o}>{OP_WORD[o]}</option>)}
            </select>
          )}
          {NEEDS_VALUE(op) && (
            <input className="nb-input nb-mono" style={{ width: a.kind === 'time' || a.kind === 'status' ? 80 : 130 }} value={showValue(a.value)} onChange={(e) => onChange({ ...a, value: parseValue(e.target.value) })} aria-label="Expected" />
          )}
          {a.kind === 'time' && <span>ms</span>}
        </>
      )}
      <span className="nb-sp" />
      <button type="button" className="gl-icon-btn" aria-label="Remove check" onClick={onRemove}>×</button>
    </div>
  )
}

/** Drop the read-model's empty defaults so a saved spec stays small. */
export function cleanSpec(s: HttpSpec): HttpSpec {
  const out: HttpSpec = { ...s }
  if (out.params && Object.keys(out.params).length === 0) delete out.params
  if (out.headers && Object.keys(out.headers).length === 0) delete out.headers
  if (!out.auth || out.auth.type === 'none') delete out.auth
  if (out.assertions && out.assertions.length === 0) delete out.assertions
  if (out.extract) out.extract = out.extract.filter((x) => x.name.trim())
  if (out.extract && out.extract.length === 0) delete out.extract
  if (out.body === null || out.body === undefined || out.body === '') delete out.body
  for (const k of ['expect', 'credential_slot', 'api_ref', 'url'] as const) if (out[k] === null) delete out[k]
  return out
}

// ── the result under the cell ───────────────────────────────────────────────────────────────────────────────────

export function AssertionList({ results, testid = 'nb-checks-list' }: { results: AssertionResult[]; testid?: string }) {
  return (
    <ul className="nb-asserts" data-testid={testid}>
      {results.map((a, i) => (
        <li key={i} data-passed={a.passed ? 'true' : 'false'} data-testid="nb-assertion">
          <StatusMark size="sm" status={a.passed ? 'ok' : 'fail'} />
          <span className="nb-asserts-l">{a.label}</span>
          <span className="nb-asserts-a" title={a.message ?? undefined}>
            {a.passed ? 'got ' : 'but got '}
            <b className="nb-mono">{a.actual === undefined ? '—' : showValue(a.actual).slice(0, 80) || '""'}</b>
          </span>
        </li>
      ))}
    </ul>
  )
}

export function HttpResultBody({ cell }: { cell: Cell }) {
  const { openWs, actions, fail, live } = useNb()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<HttpSpec>(cell.spec as HttpSpec)
  const [err, setErr] = useState<string | null>(null)
  const r = cell.latest
  const o = (r?.output ?? {}) as HttpOutput
  const st = Number(o.status ?? 0)
  const asserts = o.assertions ?? []
  const failedN = asserts.filter((a) => !a.passed).length
  const vars = Object.entries(o.vars ?? {})
  const save = () => {
    setErr(null)
    actions.patchCell.mutate(
      { cellId: cell.id, body: { spec: cleanSpec(draft) } },
      { onSuccess: () => setEditing(false), onError: (e) => { setErr(errorMessage(e)); fail(e) } },
    )
  }
  return (
    <>
      {r && !cell.stale && (st > 0 || asserts.length > 0) && (
        <div className="nb-row mt-1" data-testid="nb-http-response">
          <Chip tone={st >= 200 && st < 300 ? 'ok' : 'prod'} mono size="sm">
            {st || '—'} · {fmtMs(o.ms ?? r.ms)}
          </Chip>
          {typeof o.size === 'number' && <span className="nb-faint nb-mono">{o.size < 1024 ? `${o.size} B` : `${(o.size / 1024).toFixed(1)} KB`}</span>}
          {asserts.length > 0 && (
            <span className="nb-wh-chip" data-tone={failedN ? 'warn' : 'ok'} data-testid="nb-http-verdict">
              {failedN ? `${failedN} of ${plural(asserts.length, 'check')} failed` : `${plural(asserts.length, 'check')} passed`}
            </span>
          )}
          {o.last?.status !== undefined && o.last.status !== st && <span className="nb-faint">was {o.last.status} last run</span>}
          <span className="nb-sp" />
          <button type="button" className="nb-linkbtn text-[12.5px]" onClick={() => openWs('api', cell.id)}>Response ▸</button>
        </div>
      )}
      {r && !cell.stale && asserts.length > 0 && <AssertionList results={asserts} />}
      {r && !cell.stale && vars.length > 0 && (
        <div className="nb-row nb-http-vars" data-testid="nb-http-vars">
          <span className="nb-faint">saved</span>
          {vars.map(([k, v]) => (
            <span key={k} className="vtoken" data-readonly="" title={`later cells use {{${k}}}`}>
              {k} = {showValue(v).slice(0, 40)}
            </span>
          ))}
        </div>
      )}
      <div className="nb-row mt-1">
        <button type="button" className="nb-linkbtn text-[12.5px]" disabled={live} onClick={() => { setDraft(cell.spec as HttpSpec); setEditing(!editing) }} aria-expanded={editing} data-testid="nb-http-edit">
          {editing ? 'Close the request ▴' : 'Edit request · checks ▾'}
        </button>
      </div>
      {editing && (
        <form className="nb-draft" onSubmit={(e) => (e.preventDefault(), save())}>
          <HttpFields spec={draft} onChange={setDraft} />
          {err && <div className="nb-err">{err}</div>}
          <div className="nb-row">
            <span className="nb-faint">Saving marks this cell and the ones after it stale · rerun to check again.</span>
            <span className="nb-sp" />
            <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => setEditing(false)}>Cancel</button>
            <button type="submit" className="gl-btn" data-variant="primary" data-size="sm" disabled={actions.patchCell.isPending} data-testid="nb-http-save">Save request</button>
          </div>
        </form>
      )}
    </>
  )
}
