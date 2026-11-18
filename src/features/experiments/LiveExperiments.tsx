import { useEffect, useMemo, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run } from '@/shared/api/types'
import { fmtScore, fmtSeconds, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { Tabs } from '@/shared/primitives/Tabs'
import { CompareRuns } from './components/CompareRuns'
import { ParamDiff } from './components/ParamDiff'
import { RunCoordinates } from './components/RunCoordinates'

export const STATUS_PILL: Record<string, Status> = { succeeded: 'success', running: 'info', queued: 'neutral', failed: 'danger', cancelled: 'warning' }
export const RUN_COLORS = ['var(--viz-a)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-b)', 'var(--viz-e)', '#0F766E']

/** The run's primary holdout metric in natural units (null until it has finished). */
export const primary = (r: Run) => (r.eval_metric && r.holdout ? r.holdout.metrics[r.eval_metric.name] ?? null : null)
export const bestRow = (r: Run) => r.leaderboard?.find((x) => x.model === r.best_model) ?? r.leaderboard?.[0] ?? null
export const presetsOf = (r: Run) => { const p = r.config.presets; return Array.isArray(p) ? p.join('+') : (p as string | null) ?? '—' }
const ago = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  return s < 60 ? `${Math.round(s)} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleDateString()
}

type Tab = 'Runs' | 'Compare' | 'Parameter Diff'
type Filters = { dataset: string; type: string; status: string; q: string; min: string }

/** Experiments on the API's real runs: filter, plot the runs across their parameters and metrics, compare, diff. */
export function LiveExperiments({ go }: { go: (r: string) => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState<Tab>('Runs')
  const [sel, setSel] = useState<string[]>([])
  const [f, setF] = useState<Filters>({ dataset: 'all', type: 'all', status: 'all', q: '', min: '' })
  const load = () => api.runs.list().then((rs) => { setRuns(rs); setErr('') }).catch((e) => setErr((e as Error).message))
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [])
  // First visit: preselect the latest finished runs of the dataset with the most runs.
  useEffect(() => {
    if (!runs || sel.length) return
    const ok = runs.filter((r) => r.status === 'succeeded')
    const top = mostCommon(ok.map((r) => `${r.dataset_id}|${r.label}`))
    setSel(ok.filter((r) => `${r.dataset_id}|${r.label}` === top).slice(0, 3).map((r) => r.id))
  }, [runs]) // eslint-disable-line react-hooks/exhaustive-deps

  const datasets = useMemo(() => [...new Set((runs ?? []).map((r) => r.dataset_id))], [runs])
  const shown = useMemo(() => (runs ?? []).filter((r) => {
    if (f.dataset !== 'all' && r.dataset_id !== f.dataset) return false
    if (f.type !== 'all' && r.problem_type !== f.type && !(r.problem_type == null && f.type === 'pending')) return false
    if (f.status !== 'all' && r.status !== f.status) return false
    if (f.q && !`${r.id} ${r.name} ${r.best_model ?? ''} ${presetsOf(r)}`.toLowerCase().includes(f.q.toLowerCase())) return false
    if (f.min !== '' && Number.isFinite(Number(f.min))) {
      const v = primary(r)
      if (v == null) return false
      if (r.eval_metric?.greater_is_better ? v < Number(f.min) : v > Number(f.min)) return false
    }
    return true
  }), [runs, f])
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const selRuns = (runs ?? []).filter((r) => sel.includes(r.id))

  if (err && !runs) return <div className="page"><div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div></div>
  if (!runs) return <div className="page"><div className="card card-body row"><ResonanceWave /> Reading runs…</div></div>
  const counts = (k: (r: Run) => string) => (runs ?? []).reduce<Record<string, number>>((a, r) => { a[k(r)] = (a[k(r)] ?? 0) + 1; return a }, {})
  const byStatus = counts((r) => r.status)
  const byType = counts((r) => r.problem_type ?? 'pending')

  return (
    <div className="page experiments" data-testid="experiments-live">
      <div className="page-head">
        <div>
          <h1 className="page-title">Experiments</h1>
          <p className="helper sub" data-testid="exp-summary">{runs.length} runs · {shown.length} shown · {sel.length} selected · {byStatus.succeeded ?? 0} succeeded{byStatus.running ? ` · ${byStatus.running} running` : ''}{byStatus.failed ? ` · ${byStatus.failed} failed` : ''}</p>
        </div>
        <div className="row">
          <button className="btn" disabled={sel.length !== 1} onClick={() => go(`explain?run=${sel[0]}`)} title="Select exactly one run">Explain</button>
          <button className="btn primary" onClick={() => go('quick-train')}>New Model</button>
        </div>
      </div>

      <div className="card exp-filters">
        <label><span className="micro-label">dataset</span>
          <select className="select" value={f.dataset} onChange={(e) => setF({ ...f, dataset: e.target.value })} data-testid="exp-filter-dataset">
            <option value="all">all ({runs.length})</option>{datasets.map((d) => <option key={d} value={d}>{d} ({runs.filter((r) => r.dataset_id === d).length})</option>)}
          </select></label>
        <label><span className="micro-label">problem</span>
          <div className="seg">{['all', 'binary', 'multiclass', 'regression'].map((t) => <button key={t} className={f.type === t ? 'on' : ''} onClick={() => setF({ ...f, type: t })} data-testid={`exp-type-${t}`}>{t}{t !== 'all' && byType[t] ? ` ${byType[t]}` : ''}</button>)}</div></label>
        <label><span className="micro-label">status</span>
          <div className="seg">{['all', 'succeeded', 'running', 'failed'].map((t) => <button key={t} className={f.status === t ? 'on' : ''} onClick={() => setF({ ...f, status: t })}>{t}</button>)}</div></label>
        <label><span className="micro-label">primary metric</span>
          <span className="row" style={{ gap: 6 }}><span className="math" style={{ fontSize: 14 }}>s</span><span className="mono">{'≥ / ≤'}</span><input className="num-input" style={{ width: 80 }} placeholder="any" value={f.min} onChange={(e) => setF({ ...f, min: e.target.value })} aria-label="Primary metric threshold" data-testid="exp-filter-min" /></span></label>
        <label style={{ flex: 1 }}><span className="micro-label">search</span>
          <input className="input" placeholder="run id, name, model, preset…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} data-testid="exp-filter-q" /></label>
      </div>

      <Tabs tabs={['Runs', 'Compare', 'Parameter Diff']} value={tab} onChange={setTab} />

      {tab === 'Runs' && <>
        <div className="card">
          <div className="table-wrap"><table className="table exp-table" data-testid="exp-table">
            <thead><tr>
              <th style={{ width: 32 }} /><th>run</th><th>dataset · label</th><th>status</th><th>problem · metric</th>
              <th className="r">holdout <span className="math-th-i">s</span></th><th className="r">val <span className="math-th-i">s</span></th><th className="r math-col">t</th><th className="r">models</th><th>presets · τ</th><th className="r">fit</th><th>created</th><th />
            </tr></thead>
            <tbody>
              {shown.map((r) => {
                const b = bestRow(r)
                const on = sel.includes(r.id)
                const v = primary(r)
                return (
                  <tr key={r.id} className={on ? 'sel' : ''} onClick={() => toggle(r.id)} style={{ cursor: 'pointer' }} data-testid={`exp-row-${r.id}`}>
                    <td><input type="checkbox" checked={on} onChange={() => toggle(r.id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${r.id}`} style={{ accentColor: 'var(--accent-primary)' }} /></td>
                    <td className="table-cell">{on && <i className="run-dot" style={{ background: RUN_COLORS[sel.indexOf(r.id) % RUN_COLORS.length] }} />}{r.id}</td>
                    <td className="exp-name" title={r.name}>{r.name}</td>
                    <td><StatusPill status={STATUS_PILL[r.status] ?? 'neutral'}>{r.status}</StatusPill></td>
                    <td><span className="muted">{r.problem_type ?? '—'}{r.classes && r.problem_type !== 'binary' ? ` K=${r.classes.length}` : ''}</span> · <span className="mono" style={{ fontSize: 11.5 }}>{r.eval_metric ? <>{r.eval_metric.name} {r.eval_metric.greater_is_better ? '↑' : '↓'}</> : (r.config.eval_metric as string) ?? 'auto'}</span></td>
                    <td className="r table-cell" data-testid={`exp-score-${r.id}`}><b>{fmtScore(v)}</b></td>
                    <td className="r table-cell muted">{fmtScore(b?.score_val_natural)}</td>
                    <td className="r table-cell">{r.threshold ? <span title={`${r.threshold.source}${r.threshold.metric ? ` (${r.threshold.metric})` : ''}`}>{r.threshold.value.toFixed(3)}</span> : <span className="muted">—</span>}</td>
                    <td className="r table-cell">{r.leaderboard?.length ?? '—'}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{presetsOf(r).replace(/_quality/g, '')} · {r.config.time_limit ? `${r.config.time_limit}s` : '∞'}</td>
                    <td className="r table-cell">{fmtSeconds(r.fit_seconds)}</td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{ago(r.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                      {r.status === 'succeeded' ? <>
                        <button className="chip-btn" onClick={() => go(`explain?run=${r.id}`)} data-testid={`exp-explain-${r.id}`}>Explain</button>{' '}
                        <button className="chip-btn" onClick={() => go(`quick-train?run=${r.id}`)}>Results</button>
                      </> : r.status === 'running' || r.status === 'queued' ? <button className="chip-btn" onClick={() => go(`quick-train?run=${r.id}`)}>Watch</button> : r.error ? <span className="muted" title={r.error}>error</span> : null}
                    </td>
                  </tr>
                )
              })}
              {!shown.length && <tr><td colSpan={13} className="helper" style={{ padding: 24 }}>No runs match these filters.</td></tr>}
            </tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>
            s = the run's own metric (AutoGluon <span className="mono">eval_metric</span>) in its natural units: holdout = the fixed holdout rows no model trained on, val = AutoGluon's validation (tuning split or out-of-fold) for the best model; ↑ higher is better, ↓ lower. t = decision threshold (binary). τ = time budget. Click rows to select them for Compare and Parameter Diff.
          </p>
        </div>
        <RunCoordinates runs={shown} selected={sel} onToggle={toggle} />
      </>}
      {tab === 'Compare' && <CompareRuns runs={selRuns} go={go} />}
      {tab === 'Parameter Diff' && <ParamDiff runs={selRuns} />}

      <div className="chips-row"><ParamChip sym="runs" value={String(runs.length)} />{metricInfo('roc_auc') && <span className="helper" style={{ fontSize: 12 }}>Every number on this page is read from <span className="mono">GET /api/runs</span>; refreshed every 5 s.</span>}</div>
    </div>
  )
}

export function mostCommon(a: string[]) {
  const c = a.reduce<Record<string, number>>((m, k) => { m[k] = (m[k] ?? 0) + 1; return m }, {})
  return Object.entries(c).sort((x, y) => y[1] - x[1])[0]?.[0] ?? ''
}
