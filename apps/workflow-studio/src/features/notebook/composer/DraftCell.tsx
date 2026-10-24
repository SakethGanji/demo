/** A draft cell: the few fields a block needs, then POST /notebooks/{id}/cells. Errors come from the engine as-is. */
import { useState } from 'react'
import { ViewSwitch } from '@/shared/glass'
import { errorMessage } from '@/shared/api/http'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { useNb } from '../context'
import { KIND_LABEL, numberCells, rowRefs } from '../lib/cells'
import { cleanSpec, HttpFields } from '../cells/Http'
import type { CellCreate, CellKind, HttpSpec } from '../types'

type F = Record<string, string>

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="nb-field">
      {label}
      {children}
    </label>
  )
}

function parsePairs(s: string): Record<string, unknown> {
  // "status=released, note=ok" -> {status:"released", note:"ok"}; numbers stay numbers
  const out: Record<string, unknown> = {}
  s.split(',').map((x) => x.trim()).filter(Boolean).forEach((p) => {
    const i = p.indexOf('=')
    if (i < 1) return
    const k = p.slice(0, i).trim()
    const v = p.slice(i + 1).trim()
    out[k] = v !== '' && !Number.isNaN(Number(v)) && !/^\{\{/.test(v) ? Number(v) : v
  })
  return out
}

function build(kind: CellKind, f: F): CellCreate['spec'] {
  switch (kind) {
    case 'query':
      return { connection: f.connection ?? '', sql: f.sql ?? '' }
    case 'http': {
      const s: HttpSpec = { method: (f.method as HttpSpec['method']) || 'GET', url: f.url ?? '' }
      if (f.status) s.expect = { status: Number(f.status) }
      return s
    }
    case 'data':
      return { csv: f.csv ?? '' }
    case 'rule': {
      const op = (f.op || 'filter') as 'filter' | 'count' | 'assert' | 'compare'
      const where = f.field ? [{ field: f.field, op: (f.wop || 'eq') as 'eq', value: f.value !== '' && !Number.isNaN(Number(f.value)) ? Number(f.value) : f.value }] : []
      return op === 'compare' ? { op, left: f.left ?? '', right: f.right ?? '', key: f.key || null } : { op, left: f.left ?? '', where }
    }
    case 'change':
      return { connection: f.connection ?? '', table: f.table ?? '', key: parsePairs(f.key ?? ''), set: parsePairs(f.set ?? ''), reason: f.reason ?? '' }
    case 'wait':
      return f.mode === 'condition'
        ? { mode: 'condition', condition: { connection: f.connection ?? '', sql: f.sql ?? '', expect: (f.expect as 'nonempty') || 'nonempty' }, poll_every: f.poll_every || '30s', timeout: f.timeout || '1h' }
        : { mode: 'duration', duration: f.duration ?? '' }
    case 'workflow':
      return { workflow_id: f.workflow_id ?? '' }
    case 'store':
      return { rows: f.rows ?? '', new_table: f.new_table || null, mode: 'append' }
    case 'browser': {
      let steps: unknown = []
      try {
        steps = f.steps ? JSON.parse(f.steps) : []
      } catch {
        steps = f.steps
      }
      return { base_url: f.base_url ?? '', steps: steps as never, credential_slot: f.credential_slot || null }
    }
    case 'for_each':
      return { over: f.over ?? '', mode: (f.mode as 'sequential') || 'sequential' }
    case 'note':
      return { markdown: f.markdown ?? '' }
    case 'ask':
      return { text: f.text ?? '' }
  }
}

export function DraftCell({ kind, parent, onDone }: { kind: CellKind; parent: string | null; onDone: () => void }) {
  const { nb, pid, actions, me, connections, numberOf } = useNb()
  const workflows = useProjectWorkflows(pid)
  const refs = rowRefs(nb.cells, parent ? nb.cells.find((c) => c.id === parent)?.position : undefined)
  const [f, setF] = useState<F>(() => ({
    connection: connections[0]?.key ?? '',
    left: refs[refs.length - 1]?.value ?? '',
    over: refs[refs.length - 1]?.value ?? '',
    rows: parent ? '{{row}}' : refs[refs.length - 1]?.value ?? '',
    method: 'GET',
    op: 'filter',
    wop: 'eq',
    mode: kind === 'wait' ? 'duration' : 'sequential',
  }))
  const [title, setTitle] = useState('')
  const [http, setHttp] = useState<HttpSpec>(() => ({ method: 'GET', url: '', assertions: [{ kind: 'status', op: 'eq', value: 200 }] }))
  const [err, setErr] = useState<string | null>(null)
  // connections may load after the draft opens: an empty or unknown pick means the first one, which is what the select shows
  const connection = f.connection && connections.some((c) => c.key === f.connection) ? f.connection : (connections[0]?.key ?? '')
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value })
  const inp = (k: string, ph = '', w?: number) => <input className="nb-input" style={w ? { width: w } : undefined} value={f[k] ?? ''} onChange={set(k)} placeholder={ph} aria-label={k} />
  const conn = (writable?: boolean) => (
    <select className="nb-select" value={connection} onChange={set('connection')} aria-label="connection">
      {connections.length === 0 && <option value="">No connections · add one under Data</option>}
      {connections.map((c) => (
        <option key={c.id} value={c.key} disabled={writable && c.readonly}>
          {c.name}{c.readonly ? ' · read-only' : ' · writable'}
        </option>
      ))}
    </select>
  )
  const refSel = (k: string) => (
    <>
      <input className="nb-input" list={`refs-${k}`} value={f[k] ?? ''} onChange={set(k)} aria-label={k} placeholder="{{cells.c2.rows}}" />
      <datalist id={`refs-${k}`}>
        {parent && <option value="{{row}}">the current row</option>}
        {refs.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </datalist>
    </>
  )

  const submit = () => {
    setErr(null)
    actions.addCell.mutate(
      { kind, title: title.trim() || null, spec: kind === 'http' ? cleanSpec(http) : build(kind, { ...f, connection }), parent_cell_id: parent, created_by: me },
      { onSuccess: onDone, onError: (e) => setErr(errorMessage(e)) },
    )
  }

  const nextN = parent ? `${numberOf(parent)}.${(nb.cells.find((c) => c.id === parent)?.children?.length ?? 0) + 1}` : String(numberCells(nb.cells).filter((x) => x.depth === 0).length + 1)

  return (
    <form className="nb-draft" data-testid="nb-draft" data-kind={kind} onSubmit={(e) => (e.preventDefault(), submit())}>
      <div className="nb-row">
        <span className="kind-label">{nextN} · new {KIND_LABEL[kind].toLowerCase()}{parent ? ` inside cell ${numberOf(parent)}` : ''}</span>
        <span className="nb-sp" />
        <input className="nb-input" style={{ width: 200 }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional)" aria-label="Title" />
      </div>
      {kind === 'query' && (
        <>
          <Field label="Connection">{conn()}</Field>
          <Field label="SQL (SELECT only)"><textarea className="nb-textarea" value={f.sql ?? ''} onChange={set('sql')} aria-label="SQL" placeholder="SELECT … FROM …" /></Field>
        </>
      )}
      {kind === 'http' && <HttpFields spec={http} onChange={setHttp} />}
      {kind === 'data' && <Field label="CSV (header row first)"><textarea className="nb-textarea" value={f.csv ?? ''} onChange={set('csv')} aria-label="CSV" placeholder={'max_age_days\n1'} /></Field>}
      {kind === 'rule' && (
        <>
          <div className="nb-row">
            <select className="nb-select" value={f.op} onChange={set('op')} aria-label="Rule">
              <option value="filter">Keep rows where</option>
              <option value="count">Count rows where</option>
              <option value="assert">Check every row has</option>
              <option value="compare">Compare two results</option>
            </select>
            {refSel('left')}
          </div>
          {f.op === 'compare' ? (
            <div className="nb-row">{refSel('right')}{inp('key', 'key column', 140)}</div>
          ) : (
            <div className="nb-row">
              {inp('field', 'column', 130)}
              <select className="nb-select" value={f.wop} onChange={set('wop')} aria-label="operator">
                {['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'contains', 'is_null', 'not_null', 'older_than'].map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              {inp('value', 'value', 130)}
            </div>
          )}
        </>
      )}
      {kind === 'change' && (
        <>
          <Field label="Connection (needs a writable one)">{conn(true)}</Field>
          <div className="nb-row">{inp('table', 'schema.table', 160)}{inp('key', 'id={{cells.c3.rows[0].id}}', 230)}</div>
          <div className="nb-row">{inp('set', 'status=released', 200)}{inp('reason', 'reason', 200)}</div>
          <span className="nb-faint">Nothing is written until someone approves the diff.</span>
        </>
      )}
      {kind === 'wait' && (
        <>
          <ViewSwitch size="sm" aria-label="Wait for" value={(f.mode as 'duration') || 'duration'} onChange={(m) => setF({ ...f, mode: m })} options={[{ value: 'duration', label: 'A duration' }, { value: 'condition', label: 'Until a query holds' }, { value: 'event', label: 'An event · coming', disabled: true }]} />
          {f.mode === 'condition' ? (
            <>
              <Field label="Connection">{conn()}</Field>
              <textarea className="nb-textarea" value={f.sql ?? ''} onChange={set('sql')} aria-label="Condition SQL" placeholder="SELECT 1 FROM … WHERE …" />
              <div className="nb-row">{inp('poll_every', 'every 30s', 110)}{inp('timeout', 'timeout 1h', 110)}</div>
            </>
          ) : (
            inp('duration', '30s, 2h', 140)
          )}
        </>
      )}
      {kind === 'workflow' && (
        <Field label="Workflow">
          <select className="nb-select" value={f.workflow_id ?? ''} onChange={set('workflow_id')} aria-label="Workflow">
            <option value="">Choose…</option>
            {(workflows.data ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </Field>
      )}
      {kind === 'store' && <div className="nb-row">{refSel('rows')}{inp('new_table', 'new table name', 180)}</div>}
      {kind === 'browser' && (
        <>
          {inp('base_url', 'https://portal.example.com')}
          <Field label="Recorded steps (JSON actions, from the test builder)">
            <textarea className="nb-textarea" value={f.steps ?? ''} onChange={set('steps')} aria-label="Steps" placeholder='[{"action":"goto","url":"/"},{"action":"expectVisible","target":{"text":"Sign in"}}]' />
          </Field>
          <div className="nb-row">{inp('credential_slot', 'credential slot (optional)', 220)}<span className="nb-coming">agent-driven browsing · coming</span></div>
        </>
      )}
      {kind === 'for_each' && (
        <>
          <Field label="Rows">{refSel('over')}</Field>
          <ViewSwitch size="sm" aria-label="How rows run" value={(f.mode as 'sequential') || 'sequential'} onChange={(m) => setF({ ...f, mode: m })} options={[{ value: 'sequential', label: 'One at a time' }, { value: 'parallel', label: 'In parallel' }, { value: 'child_task', label: 'A task per row · coming', disabled: true }]} />
          <span className="nb-faint">max 500 rows · add steps inside after it is created</span>
        </>
      )}
      {kind === 'note' && <textarea className="nb-textarea" value={f.markdown ?? ''} onChange={set('markdown')} aria-label="Note" />}
      {err && <div className="nb-err" data-testid="nb-draft-error">{err}</div>}
      <div className="nb-row">
        <span className="nb-sp" />
        <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={onDone}>Cancel</button>
        <button type="submit" className="gl-btn" data-variant="primary" data-size="sm" disabled={actions.addCell.isPending} data-testid="nb-draft-add">
          Add cell
        </button>
      </div>
    </form>
  )
}
