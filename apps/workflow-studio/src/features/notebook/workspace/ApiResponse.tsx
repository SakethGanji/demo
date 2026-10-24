/** The API response tab: request line, status · time · size, checks, then Body (JSON tree) / Headers / Diff vs the
 *  previous run of the same request. Credentials never reach here: the engine hides auth headers and redacts secrets. */
import { useState } from 'react'
import { Chip, ViewSwitch } from '@/shared/glass'
import { fmtMs, fmtTime } from '../lib/cells'
import { AssertionList } from '../cells/Http'
import type { Cell, HttpOutput, HttpSpec } from '../types'

const size = (n: number | null | undefined) => (typeof n !== 'number' ? '' : n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`)

function Leaf({ v }: { v: unknown }) {
  if (v === null) return <span className="nb-jt-null">null</span>
  if (typeof v === 'string') return <span className="nb-jt-str">"{v}"</span>
  if (typeof v === 'number') return <span className="nb-jt-num">{v}</span>
  if (typeof v === 'boolean') return <span className="nb-jt-bool">{String(v)}</span>
  return <span>{String(v)}</span>
}

/** A collapsible JSON tree; the first two levels start open. */
export function JsonTree({ value, name, depth = 0 }: { value: unknown; name?: string; depth?: number }) {
  const [open, setOpen] = useState(depth < 2)
  const isObj = value !== null && typeof value === 'object'
  const label = name !== undefined ? <span className="nb-jt-k">{name}</span> : null
  if (!isObj)
    return (
      <div className="nb-jt-row">
        {label}
        {label && ': '}
        <Leaf v={value} />
      </div>
    )
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>)
  const summary = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`
  return (
    <div className="nb-jt">
      <button type="button" className="nb-jt-row nb-jt-tog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="nb-jt-car" aria-hidden>{open ? '▾' : '▸'}</span>
        {label}
        {label && ' '}
        <span className="nb-faint">{summary}</span>
      </button>
      {open && (
        <div className="nb-jt-kids">
          {entries.slice(0, 500).map(([k, v]) => <JsonTree key={k} name={k} value={v} depth={depth + 1} />)}
          {entries.length > 500 && <div className="nb-faint">… {entries.length - 500} more</div>}
        </div>
      )}
    </div>
  )
}

type DLine = { t: 'same' | 'add' | 'del'; s: string }
/** Line diff (LCS) of two pretty-printed bodies, capped so a huge body cannot hang the tab. */
export function lineDiff(a: string, b: string): DLine[] {
  const A = a.split('\n').slice(0, 800)
  const B = b.split('\n').slice(0, 800)
  const n = A.length, m = B.length
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const out: DLine[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (A[i] === B[j]) (out.push({ t: 'same', s: A[i] }), i++, j++)
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ t: 'del', s: A[i++] })
    else out.push({ t: 'add', s: B[j++] })
  }
  while (i < n) out.push({ t: 'del', s: A[i++] })
  while (j < m) out.push({ t: 'add', s: B[j++] })
  return out
}

const pretty = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v ?? null, null, 2))

export function ApiTab({ cell, Empty }: { cell: Cell; Empty: (p: { children: React.ReactNode }) => React.ReactElement }) {
  const [view, setView] = useState<'body' | 'headers' | 'diff'>('body')
  const r = cell.latest
  const s = cell.spec as HttpSpec
  if (!r) return <Empty>Run the notebook to see the response.</Empty>
  const o = (r.output ?? {}) as HttpOutput
  const st = Number(o.status ?? 0)
  const last = o.last ?? null
  const headers = Object.entries(o.headers ?? {})
  const diff = last ? lineDiff(pretty(last.body), pretty(o.body)) : []
  const changed = diff.filter((d) => d.t !== 'same').length
  return (
    <div className="flex flex-col gap-2" data-testid="nb-ws-api">
      <div className="nb-row">
        <Chip mono size="sm">{String(o.request?.method ?? o.method ?? s.method ?? 'GET')}</Chip>
        <span className="nb-mono truncate" style={{ maxWidth: 380 }} title={String(o.request?.url ?? o.url ?? s.url ?? '')}>{String(o.request?.url ?? o.url ?? s.url ?? s.api_ref ?? '')}</span>
      </div>
      <div className="nb-row" data-testid="nb-api-meta">
        <Chip tone={st >= 200 && st < 300 ? 'ok' : 'prod'} mono size="sm">{st || 'no response'}</Chip>
        <span className="nb-faint nb-mono">{fmtMs(o.ms ?? r.ms)}</span>
        {typeof o.size === 'number' && <span className="nb-faint nb-mono">{size(o.size)}</span>}
        {o.request?.headers && o.request.headers.length > 0 && <span className="nb-faint">sent headers: {o.request.headers.join(', ')}</span>}
      </div>
      {r.error && <div className="nb-err">{r.error.message}</div>}
      {(o.assertions ?? []).length > 0 && <AssertionList results={o.assertions!} testid="nb-ws-checks" />}
      <ViewSwitch
        size="sm"
        aria-label="Response view"
        value={view}
        onChange={setView}
        options={[
          { value: 'body', label: 'Body' },
          { value: 'headers', label: `Headers ${headers.length}` },
          { value: 'diff', label: last ? `Diff vs last run${changed ? ` · ${changed}` : ' · same'}` : 'Diff · no earlier run', disabled: !last },
        ]}
      />
      {view === 'body' && (
        <div className="nb-json nb-jt-wrap" data-testid="nb-api-body">
          {o.body !== null && typeof o.body === 'object' ? <JsonTree value={o.body} /> : <pre className="m-0 whitespace-pre-wrap">{pretty(o.body)}</pre>}
        </div>
      )}
      {view === 'headers' && (
        <div className="nb-api-headers" data-testid="nb-api-headers">
          {headers.length === 0 && <div className="nb-faint">No headers recorded.</div>}
          {headers.map(([k, v]) => (
            <div key={k}><span className="nb-mono nb-faint">{k}</span><span className="nb-mono">{v}</span></div>
          ))}
          <div className="nb-faint mt-1">Set-Cookie and authorization headers are not kept.</div>
        </div>
      )}
      {view === 'diff' && last && (
        <div data-testid="nb-api-diff">
          <div className="nb-row nb-faint mb-1">
            <span>last run {fmtTime(last.at)}:</span>
            <span className="nb-mono">{last.status ?? '—'} → {st}</span>
            {last.ms !== undefined && last.ms !== null && <span className="nb-mono">{fmtMs(last.ms)} → {fmtMs(o.ms ?? r.ms)}</span>}
            {typeof last.size === 'number' && <span className="nb-mono">{size(last.size)} → {size(o.size)}</span>}
          </div>
          {changed === 0 ? (
            <div className="nb-card" data-tone="ok">The body is the same as last run.</div>
          ) : (
            <pre className="nb-json nb-api-diff">
              {diff.map((d, i) => (
                <div key={i} data-t={d.t}>{d.t === 'add' ? '+ ' : d.t === 'del' ? '− ' : '  '}{d.s}</div>
              ))}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
