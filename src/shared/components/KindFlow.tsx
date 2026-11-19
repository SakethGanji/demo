import { useEffect, useState, type ReactNode } from 'react'
import { api } from '@/shared/api/client'
import type { DatasetDetail, RunEvent } from '@/shared/api/types'
import { Register } from '@/shared/components/FlowParts'
import { RESULTS } from '@/shared/components/KindResults'
import { ApiOnly, ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { RI_NOT_REGISTRABLE, type RunKind } from '@/shared/lib/runKinds'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import './kindflow.css'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Det = any
type Opts = Record<string, any>
/** One column the flow needs: a select of `options`, pre-filled from the detection, overridable from Build (`?urlParam=`). */
export type KindRole = {
  key: string; label: string; testId: string; urlParam?: string
  options: (ds: DatasetDetail, d: Det) => string[]
  /** An empty choice that is allowed (RI: no decision column). */
  none?: string
  emptyHint: string
  why?: (d: Det, det: Det, pick: Det) => ReactNode
}
/** The setup context every spec callback gets. */
export type Ctx = { d: Det; det: Det; pick: Det; setPick: (p: Det) => void; ds: DatasetDetail; o: Opts; set: (p: Opts) => void }
/** A G-kind's setup and run page. Results live in KindResults' RESULTS map, shared with Registry → Results. */
export type KindSpec = {
  id: RunKind; title: string; sub: string; testId: string; defaultDataset: string; mockWhat: string
  detect: (ds: string) => Promise<Det>; create: (body: Record<string, unknown>) => Promise<{ id: string }>
  roles: KindRole[]
  /** Rows after the roles (segments, horizons, label counts, the RI check); "left out" is always last. */
  rows?: (c: Ctx) => ReactNode
  leftOut: (c: Ctx) => ReactNode
  Formula: (c: Ctx) => ReactNode
  /** Under the table, before Advanced (warnings, the assumption) and after it (how it trains). */
  note?: (c: Ctx) => ReactNode; explain?: (c: Ctx) => ReactNode
  advanced: { summary: (o: Opts) => string; Body: (c: Ctx) => ReactNode; initial: (det: Det) => Opts }
  rowsWord?: (d: Det) => string
  startLabel: (c: Ctx) => string; canStart: (c: Ctx) => boolean; body: (c: Ctx) => Record<string, unknown>
  running: { line: (run: any) => ReactNode; events: string; field?: string; wide?: boolean; steps: (run: any, started: RunEvent[]) => { key: string; label: ReactNode }[] }
  meta: (run: any) => ReactNode
  registerName?: (run: any) => string
}

function Head({ spec, right }: { spec: KindSpec; right?: ReactNode }) {
  return <div className="page-head"><div><h1 className="page-title">{spec.title}</h1><p className="helper sub">{spec.sub}</p></div>{right}</div>
}

/** The Build choice for a G-kind: its setup (the run itself opens in Models). */
export function KindFlow({ spec, go }: { spec: KindSpec; go: (r: string) => void }) {
  return (
    <ApiOnly mock={<div className="page"><Head spec={spec} /><div className="card card-body"><p className="helper">▲ mock mode: {spec.mockWhat} on the API, which is offline. Start it with <span className="mono">make dev</span>.</p></div></div>}>
      <KindSetup spec={spec} go={go} />
    </ApiOnly>
  )
}

const defaultWhy = (k: string) => (d: Det, det: Det, pick: Det) => (pick[k] && pick[k] !== det[k] ? 'set by you' : (det.evidence?.[d[k] ?? ''] ?? []).join(', '))

function KindSetup({ spec, go }: { spec: KindSpec; go: (r: string) => void }) {
  const dsId = useHashParam('dataset') ?? spec.defaultDataset
  const det = useFetch(() => spec.detect(dsId), [dsId])
  const ds = useFetch(() => api.datasets.get(dsId), [dsId])
  // Build passes the roles it shows (with the user's edits) as ?param=; they override the detection.
  const fromBuild = (): Det => {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
    return Object.fromEntries(spec.roles.filter((r) => r.urlParam && q.get(r.urlParam)).map((r) => [r.key, q.get(r.urlParam!)]))
  }
  const [pick, setPick] = useState<Det>(fromBuild)
  const [o, setO] = useState<Opts | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => setPick(fromBuild()), [dsId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (det.data) setO(spec.advanced.initial(det.data)) }, [det.data]) // eslint-disable-line react-hooks/exhaustive-deps
  if (det.err || ds.err) return <div className="page"><Head spec={spec} /><ErrorCard inline err={det.err || ds.err} /></div>
  if (!det.data || !ds.data || !o) return <div className="page"><Head spec={spec} /><Loading inline>Reading the column roles…</Loading></div>
  const c: Ctx = { d: { ...det.data, ...pick }, det: det.data, pick, setPick, ds: ds.data, o, set: (p) => setO({ ...o, ...p }) }
  const start = async () => {
    setBusy(true); setErr('')
    try { const r = await spec.create({ dataset_id: dsId, ...spec.body(c) }); go(`models?run=${r.id}`) } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  const t = spec.testId
  return (
    <div className="page" data-testid={`${t}-setup`}>
      <Head spec={spec} />
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">What we use</h3><span className="helper" style={{ fontSize: 12 }}>{ds.data.name} · {ds.data.rows.toLocaleString()} {spec.rowsWord?.(c.d) ?? 'rows'} · detected from the column roles; change any of them</span></div>
          <button className="btn primary" disabled={!spec.canStart(c) || busy} onClick={start} data-testid={`${t}-start`}>{busy ? 'Starting…' : spec.startLabel(c)}</button></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <spec.Formula {...c} />
          <div className="table-wrap"><table className="table compact" data-testid={`${t}-detected`}>
            <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
            <tbody>
              {spec.roles.map((r) => (
                <tr key={r.key}><td>{r.label}</td>
                  <td><select className="input mono" value={c.d[r.key] ?? ''} onChange={(e) => setPick({ ...pick, [r.key]: e.target.value || null })} data-testid={r.testId}>
                    {(r.none || !c.d[r.key]) && <option value="">{r.none ?? '—'}</option>}{r.options(ds.data!, c.d).map((x) => <option key={x}>{x}</option>)}</select></td>
                  <td className="helper" style={{ fontSize: 12, whiteSpace: 'normal' }}>{c.d[r.key] ? (r.why ?? defaultWhy(r.key))(c.d, det.data, pick) : r.emptyHint}</td></tr>
              ))}
              {spec.rows?.(c)}
              <tr><td>left out</td><td className="mono" colSpan={2} style={{ whiteSpace: 'normal' }}>{c.d.drop_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· {spec.leftOut(c)}</span></td></tr>
            </tbody>
          </table></div>
          {spec.note?.(c)}
          <details className="an-adv">
            <summary className="micro-label">Advanced · {spec.advanced.summary(o)}</summary>
            <div className="row" style={{ gap: 8, padding: 'var(--space-8) 0', flexWrap: 'wrap' }}><spec.advanced.Body {...c} /></div>
          </details>
          {spec.explain && <p className="helper" style={{ fontSize: 12.5 }}>{spec.explain(c)}</p>}
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>
      </div>
    </div>
  )
}

/** A number input for Advanced: label, value, bounds. */
export function NumField({ label, value, set, min, max, step, testId, width = 90 }: { label: string; value: number; set: (v: number) => void; min: number; max: number; step?: number; testId?: string; width?: number }) {
  return <><label className="helper">{label}</label><input className="input mono" type="number" min={min} max={max} step={step} value={value} onChange={(e) => set(Math.min(max, Math.max(min, Number(e.target.value) || min)))} style={{ width }} data-testid={testId} /></>
}

/** A G-kind's run page (in Models): live steps while it trains, then the results, Register in the head. */
export function KindRun({ spec, runId, go }: { spec: KindSpec; runId: string; go: (r: string) => void }) {
  const res = RESULTS[spec.id]!
  const live = useRunEvents(runId)
  const r = useFetch(() => res.get(runId), [runId, live.done], { pollMs: 3000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <div className="page"><Head spec={spec} /><ErrorCard inline err={r.err} /></div>
  if (!r.data) return <div className="page"><Head spec={spec} /><Loading inline>Loading the run…</Loading></div>
  const run = r.data
  const ev = spec.running.events, f = spec.running.field ?? 'model'
  const started = live.events.filter((e) => e.type === `${ev}_model_started`)
  const done = new Set(live.events.filter((e) => e.type === `${ev}_model_finished`).map((e) => e[f] as string))
  const on = new Set(started.map((e) => e[f] as string))
  const ok = run.status === 'succeeded'
  return (
    <div className="page" data-testid={`${spec.testId}-run`} data-status={run.status}>
      <Head spec={spec} right={ok && spec.registerName ? <Register runId={runId} go={go} name={spec.registerName(run)} /> : undefined} />
      <p className="helper mono" style={{ fontSize: 12, marginTop: -8 }}>{run.name} · {runId} · {spec.meta(run)}</p>
      {ok && !spec.registerName && <p className="helper" style={{ fontSize: 12.5 }} data-testid="not-registrable">Not registrable: {RI_NOT_REGISTRABLE}</p>}
      {run.status === 'failed' && <ErrorCard inline err={run.error ?? 'The run failed.'} />}
      {(run.status === 'queued' || run.status === 'running') && (
        <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
          <div className="row"><ResonanceWave /> <span>{spec.running.line(run)} · {live.stage}</span></div>
          <div className={`el-steps ${spec.running.wide ? 'seg-steps' : ''}`}>{spec.running.steps(run, started).map((s) => (
            <div key={s.key} className={`el-step ${done.has(s.key) ? 'done' : on.has(s.key) ? 'on' : ''}`}>{s.label}<span className="mono">{done.has(s.key) ? '✓' : on.has(s.key) ? '…' : ''}</span></div>
          ))}</div>
        </div></div>
      )}
      {ok && res.ready(run) && <res.View run={run} reload={r.reload} />}
    </div>
  )
}

/** A paired difference as a dot with its 95% CI on a symmetric axis around 0 (green when it favours the new model). */
export function CiBar({ d, span, better }: { d: { delta: number; ci: [number, number] } | null | undefined; span: number; better: 'up' | 'down' }) {
  if (!d) return <span className="helper">—</span>
  const x = (v: number) => `${50 + (Math.max(-span, Math.min(span, v)) / span) * 50}%`
  const good = better === 'up' ? d.ci[0] > 0 : d.ci[1] < 0
  const bad = better === 'up' ? d.ci[1] < 0 : d.ci[0] > 0
  const s = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(4)}`
  return (
    <div className={`seg-ci ${good ? 'good' : bad ? 'bad' : ''}`} title={`${s(d.delta)} [${s(d.ci[0])}, ${s(d.ci[1])}]`}>
      <i className="zero" /><i className="bar" style={{ left: x(d.ci[0]), width: `calc(${x(d.ci[1])} - ${x(d.ci[0])})` }} /><i className="dot" style={{ left: x(d.delta) }} />
    </div>
  )
}
