import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Run } from '@/shared/api/types'
import { fmtScore, fmtSeconds, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { StatusPill, type Status } from '@/shared/primitives/StatusPill'
import { Tabs } from '@/shared/primitives/Tabs'
import { EmptyState } from '@/shared/components/EmptyState'
import { RI_NOT_REGISTRABLE, TYPE_FILTERS, headline, kindOf, problemLine, runPage, typeOf } from '@/shared/lib/runKinds'
import { CompareRuns } from './components/CompareRuns'
import { ParamDiff } from './components/ParamDiff'
import { RunCoordinates } from './components/RunCoordinates'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { catalog, projectRuns } from '@/shared/api/catalog'
import { ProjectTree } from './components/ProjectTree'

export const STATUS_PILL: Record<string, Status> = { succeeded: 'success', running: 'info', queued: 'neutral', failed: 'danger', cancelled: 'warning' }
export const RUN_COLORS = ['var(--viz-a)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-b)', 'var(--viz-e)', '#0F766E']

/** The run's primary holdout metric in natural units (null until it has finished). */
export const primary = (r: Run) => (r.eval_metric && r.holdout ? r.holdout.metrics[r.eval_metric.name] ?? null : null)
export const bestRow = (r: Run) => r.leaderboard?.find((x) => x.model === r.best_model) ?? r.leaderboard?.[0] ?? null
/** The best model's validation score; a refit model (…_FULL) has none of its own, so its bagged original's is used. */
export const bestVal = (r: Run) => {
  const b = bestRow(r)
  if (b?.score_val_natural != null) return b.score_val_natural
  const orig = b?.model.endsWith('_FULL') ? r.leaderboard?.find((x) => x.model === b.model.slice(0, -5)) : null
  return orig?.score_val_natural ?? null
}
export const presetsOf = (r: Run) => { const p = r.config.presets; return Array.isArray(p) ? p.join('+') : (p as string | null) ?? '—' }
const typeParam = () => { const t = new URLSearchParams(location.hash.split('?')[1] ?? '').get('type'); return t === 'import' ? 'imported' : t && (TYPE_FILTERS as readonly string[]).includes(t) ? t : 'all' }
const ago = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  return s < 60 ? `${Math.round(s)} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleDateString()
}

type Tab = 'Runs' | 'Compare' | 'Parameter Diff' | 'Lineage'
const projectParam = () => new URLSearchParams(location.hash.split('?')[1] ?? '').get('project')
type Filters = { dataset: string; type: string; status: string; q: string; min: string }

/** Models: the API's real runs: filter, plot the runs across their parameters and metrics, compare, diff. */
export function RunList({ go }: { go: (r: string) => void }) {
  // P1: the project switcher scopes the list in SQL (?project= in the hash keeps it across reloads)
  const [project, setProjectState] = useState<string | null>(projectParam)
  const setProject = (p: string | null) => {
    setProjectState(p); setSel([])
    const q = new URLSearchParams(location.hash.split('?')[1] ?? ''); if (p) q.set('project', p); else q.delete('project')
    history.replaceState(null, '', `#/models${q.toString() ? `?${q}` : ''}`)
  }
  const projects = useFetch(() => catalog.projects(), [])
  const { data: runs, err } = useFetch(() => (project ? projectRuns(project) : api.runs.list(undefined, 'all')), [project], { pollMs: 5000 })
  const [tab, setTab] = useState<Tab>('Runs')
  const [sel, setSel] = useState<string[]>([])
  // ?type= (from a setup page's "earlier runs are in Models" link) preselects the problem filter.
  const [f, setF] = useState<Filters>(() => ({ dataset: 'all', type: typeParam(), status: 'all', q: '', min: '' }))
  // #/models?compare=a,b (from a Vela patch's retrain): select those runs and open Compare.
  const fromUrl = useRef(new URLSearchParams(location.hash.split('?')[1] ?? '').get('compare')).current
  useEffect(() => { if (fromUrl) { setSel(fromUrl.split(',').filter(Boolean)); setTab('Compare') } }, [fromUrl])
  // First visit: preselect the latest finished runs of the dataset with the most runs.
  useEffect(() => {
    if (!runs || sel.length) return
    const ok = runs.filter((r) => r.status === 'succeeded' && tabularLike(r))
    const top = mostCommon(ok.map((r) => `${r.dataset_id}|${r.label}`))
    setSel(ok.filter((r) => `${r.dataset_id}|${r.label}` === top).slice(0, 3).map((r) => r.id))
  }, [runs]) // eslint-disable-line react-hooks/exhaustive-deps

  const datasets = useMemo(() => [...new Set((runs ?? []).map((r) => r.dataset_id))], [runs])
  const shown = useMemo(() => (runs ?? []).filter((r) => {
    if (f.dataset !== 'all' && r.dataset_id !== f.dataset) return false
    if (f.type !== 'all' && typeOf(r) !== f.type) return false
    if (f.status !== 'all' && r.status !== f.status) return false
    if (f.q && !`${r.id} ${r.name} ${r.best_model ?? ''} ${presetsOf(r)}`.toLowerCase().includes(f.q.toLowerCase())) return false
    if (f.min !== '' && Number.isFinite(Number(f.min))) {
      const h = headline(r)
      if (h.value == null || h.dir === '') return false
      if (h.dir === '↑' ? h.value < Number(f.min) : h.value > Number(f.min)) return false
    }
    return true
  }), [runs, f])
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const selRuns = (runs ?? []).filter((r) => sel.includes(r.id) && tabularLike(r))
  const selOther = (runs ?? []).filter((r) => sel.includes(r.id) && !tabularLike(r)).length

  if (err && !runs) return <ErrorCard err={err} />
  if (!runs) return <Loading>Reading the runs from the API: their scores, parameters and thresholds…</Loading>
  if (!runs.length) return (
    <div className="page experiments" data-testid="experiments-live">
      <div className="page-head"><div><h1 className="page-title">Models</h1><p className="helper sub" data-testid="exp-summary">0 runs · every run you train, forecast, score for anomalies or import lands here</p></div></div>
      <EmptyState testId="exp-empty" title="No runs yet"
        actions={<button className="btn primary" onClick={() => go('build')}>Build →</button>}>
        <p>Each run becomes one row with its own metric s on held-out data, its parameters and its fit time; tabular runs on the same dataset and label are drawn as strings across parallel coordinates, compared side by side (ROC, PR, residuals, Δ on the same holdout) and diffed down to the exact <span className="mono">TabularPredictor(...).fit(...)</span> arguments.</p>
        <p>Six samples are ready: support tickets (text, 6 classes), account churn and credit default (binary, imbalanced), loan amount (regression), transaction volume (forecasting) and card transactions (anomalies).</p>
      </EmptyState>
    </div>
  )
  const counts = (k: (r: Run) => string) => (runs ?? []).reduce<Record<string, number>>((a, r) => { a[k(r)] = (a[k(r)] ?? 0) + 1; return a }, {})
  const byStatus = counts((r) => r.status)
  const byType = counts(typeOf)

  return (
    <div className="page experiments" data-testid="experiments-live">
      <div className="page-head">
        <div>
          <h1 className="page-title">Models</h1>
          <p className="helper sub" data-testid="exp-summary">{project ? `${projects.data?.find((x) => x.id === project)?.name ?? project} · ` : ''}{runs.length} runs · {shown.length} shown · {sel.length} selected · {byStatus.succeeded ?? 0} succeeded{byStatus.running ? ` · ${byStatus.running} running` : ''}{byStatus.failed ? ` · ${byStatus.failed} failed` : ''}</p>
        </div>
        <div className="row">
          <label className="project-switch"><span className="micro-label">project</span>
            <select className="select" value={project ?? ''} onChange={(e) => setProject(e.target.value || null)} data-testid="project-switch">
              <option value="">All projects</option>
              {(projects.data ?? []).filter((p) => p.n_runs > 0 || p.id === project).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.n_runs}){p.champion ? ' ★' : ''}</option>)}
            </select></label>
          <button className="btn" disabled={sel.length !== 1} onClick={() => go(`models?run=${sel[0]}&tab=Explain`)} title="Select exactly one run">Explain</button>
          <button className="btn primary" onClick={() => go('build')}>Build</button>
        </div>
      </div>

      <div className="card exp-filters">
        <label><span className="micro-label">dataset</span>
          <select className="select" value={f.dataset} onChange={(e) => setF({ ...f, dataset: e.target.value })} data-testid="exp-filter-dataset">
            <option value="all">all ({runs.length})</option>{datasets.map((d) => <option key={d} value={d}>{d} ({runs.filter((r) => r.dataset_id === d).length})</option>)}
          </select></label>
        <label><span className="micro-label">problem</span>
          <div className="seg">{TYPE_FILTERS.map((t) => <button key={t} className={f.type === t ? 'on' : ''} onClick={() => setF({ ...f, type: t })} data-testid={`exp-type-${t}`}>{t}{t !== 'all' && byType[t] ? ` ${byType[t]}` : ''}</button>)}</div></label>
        <label><span className="micro-label">status</span>
          <div className="seg">{['all', 'succeeded', 'running', 'failed'].map((t) => <button key={t} className={f.status === t ? 'on' : ''} onClick={() => setF({ ...f, status: t })}>{t}</button>)}</div></label>
        <label><span className="micro-label">primary metric</span>
          <span className="row" style={{ gap: 6 }}><span className="math" style={{ fontSize: 14 }}>s</span><span className="mono">{'≥ / ≤'}</span><input className="num-input" style={{ width: 80 }} placeholder="any" value={f.min} onChange={(e) => setF({ ...f, min: e.target.value })} aria-label="Primary metric threshold" data-testid="exp-filter-min" /></span></label>
        <label style={{ flex: 1 }}><span className="micro-label">search</span>
          <input className="input" placeholder="run id, name, model, preset…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} data-testid="exp-filter-q" /></label>
      </div>

      <Tabs tabs={['Runs', 'Compare', 'Parameter Diff', 'Lineage']} value={tab} onChange={setTab} />

      {tab === 'Runs' && <>
        <div className="card">
          <div className="table-wrap"><table className="table exp-table" data-testid="exp-table">
            <thead><tr>
              <th style={{ width: 32 }} /><th>run</th><th>dataset · label</th><th>status</th><th>problem · metric</th>
              <th className="r">holdout <span className="math-th-i">s</span></th><th className="r">val <span className="math-th-i">s</span></th><th className="r math-col">t</th><th className="r">models</th><th>presets · τ</th><th className="r">fit</th><th>created</th><th />
            </tr></thead>
            <tbody>
              {shown.map((r) => {
                const on = sel.includes(r.id)
                const h = headline(r)
                const k = kindOf(r)
                const tab = tabularLike(r)
                return (
                  <tr key={r.id} className={on ? 'sel' : ''} onClick={() => toggle(r.id)} style={{ cursor: 'pointer' }} data-testid={`exp-row-${r.id}`} data-kind={k}>
                    <td><input type="checkbox" checked={on} onChange={() => toggle(r.id)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${r.id}`} style={{ accentColor: 'var(--accent-primary)' }} /></td>
                    <td className="table-cell">{on && <i className="run-dot" style={{ background: RUN_COLORS[sel.indexOf(r.id) % RUN_COLORS.length] }} />}{r.id}</td>
                    <td className="exp-name" title={r.name}>{r.name}{r.config.parent_run_id ? <span className="exp-lineage" data-testid={`exp-lineage-${r.id}`}>↳ {(r.config.patch as { title?: string } | null)?.title ?? 'from'} · {String(r.config.parent_run_id)}</span> : null}</td>
                    <td><StatusPill status={STATUS_PILL[r.status] ?? 'neutral'}>{r.status}</StatusPill></td>
                    <td><span className={`kind-tag kind-${k}`}>{problemLine(r)}{r.classes && r.problem_type === 'multiclass' ? ` K=${r.classes.length}` : ''}</span> · <span className="mono" style={{ fontSize: 11.5 }}>{k === 'anomaly' ? h.label : r.eval_metric ? <>{r.eval_metric.name} {h.dir}</> : (r.config.eval_metric as string) ?? 'auto'}</span></td>
                    <td className="r table-cell" data-testid={`exp-score-${r.id}`} title={h.note}><b>{h.text}</b></td>
                    <td className="r table-cell muted">{tab || k === 'forecast' ? fmtScore(k === 'forecast' ? fcVal(r) : bestVal(r)) : '—'}</td>
                    <td className="r table-cell">{r.threshold ? <span title={`${r.threshold.source}${r.threshold.metric ? ` (${r.threshold.metric})` : ''}`}>{r.threshold.value.toFixed(3)}</span> : k === 'anomaly' && (r as { anomaly?: { threshold?: number } }).anomaly?.threshold != null ? <span title="s*: flag when s ≥ s*">s* {(r as { anomaly?: { threshold?: number } }).anomaly!.threshold!.toFixed(3)}</span> : <span className="muted">—</span>}</td>
                    <td className="r table-cell">{r.leaderboard?.length ?? '—'}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{k === 'anomaly' ? `${(r.config.n_estimators as number) ?? 300} trees · c ${(r.config.contamination as number) ?? '—'}` : `${presetsOf(r).replace(/_quality/g, '')} · ${r.config.time_limit ? `${r.config.time_limit}s` : '∞'}`}</td>
                    <td className="r table-cell">{fmtSeconds(r.fit_seconds)}</td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{ago(r.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: 'nowrap' }}>
                      {r.status === 'succeeded' ? <>
                        {tab && <><button className="chip-btn" onClick={() => go(`models?run=${r.id}&tab=Explain`)} data-testid={`exp-explain-${r.id}`}>Explain</button>{' '}</>}
                        <button className="chip-btn" onClick={() => go(runPage(r))} data-testid={`exp-open-${r.id}`}>Open</button>{' '}
                        {k === 'reject_inference' ? <span className="muted" title={RI_NOT_REGISTRABLE}>not registrable</span>
                          : <button className="chip-btn" onClick={() => go(`registry?register=${r.id}`)} data-testid={`exp-register-${r.id}`}>Register…</button>}
                      </> : r.status === 'running' || r.status === 'queued' ? <button className="chip-btn" onClick={() => go(runPage(r))}>Open</button> : r.error ? <span className="muted" title={r.error}>error</span> : null}
                    </td>
                  </tr>
                )
              })}
              {!shown.length && <tr><td colSpan={13} className="helper" style={{ padding: 24 }}>No runs match these filters. <button className="chip-btn" onClick={() => setF({ dataset: 'all', type: 'all', status: 'all', q: '', min: '' })}>Clear filters</button></td></tr>}
            </tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12, padding: 'var(--space-8) var(--space-16) var(--space-12)' }}>
            s = the run's own metric in its natural units. Tabular: AutoGluon <span className="mono">eval_metric</span> on the fixed holdout rows no model trained on; val = AutoGluon's validation (tuning split or out-of-fold) for the best model. Forecast: the eval metric on the last H steps of every series (holdout) and on the backtest windows (val). Anomaly: ROC AUC of the isolation score s against the planted anomalies (sample only; the model never reads them), else rows flagged at s ≥ s*. ↑ higher is better, ↓ lower. t = decision threshold (binary); s* = anomaly threshold, the (1 − c) quantile of s. τ = time budget. Click rows to select them; Compare and Parameter Diff use the tabular runs.
          </p>
        </div>
        <RunCoordinates runs={shown.filter(tabularLike)} selected={sel} onToggle={toggle} />
      </>}
      {tab !== 'Runs' && selOther > 0 && <p className="helper" style={{ fontSize: 12 }}>{selOther} forecast / anomaly run{selOther === 1 ? '' : 's'} selected: Compare and Parameter Diff read tabular runs (their leaderboards, curves and fit() calls); open the others on their own pages.</p>}
      {tab === 'Compare' && <><PatchLineage runs={selRuns} />{<CompareRuns runs={selRuns} go={go} />}</>}
      {tab === 'Parameter Diff' && <ParamDiff runs={selRuns} />}
      {tab === 'Lineage' && <div className="card card-body" data-testid="models-lineage">
        {project ? <ProjectTree kind="project" id={project} go={go} />
          : sel.length ? <ProjectTree kind="run" id={sel[0]} go={go} />
            : <p className="helper">Pick a project above, or select a run, to see its tree: dataset → versions → runs → branches → registered versions.</p>}
      </div>}

      <div className="chips-row"><ParamChip sym="runs" value={String(runs.length)} />{metricInfo('roc_auc') && <span className="helper" style={{ fontSize: 12 }}>Every number on this page is read from <span className="mono">GET /api/runs</span>; refreshed every 5 s.</span>}</div>
    </div>
  )
}

/** Tabular fits and imported TabularPredictors share leaderboards, curves and fit() calls; forecast and anomaly runs don't. */
const tabularLike = (r: Run) => { const k = kindOf(r); return k === 'tabular' || k === 'import' }
/** A forecast run's best validation score (backtest windows), from its leaderboard; AutoGluon reports it negated. */
const fcVal = (r: Run) => { const b = bestRow(r); const v = b?.score_val_natural ?? (b?.score_val != null ? -b.score_val : null); return v ?? null }

export function mostCommon(a: string[]) {
  const c = a.reduce<Record<string, number>>((m, k) => { m[k] = (m[k] ?? 0) + 1; return m }, {})
  return Object.entries(c).sort((x, y) => y[1] - x[1])[0]?.[0] ?? ''
}

/** A run trained from a Vela patch next to its parent: the change and the holdout score before → after. */
function PatchLineage({ runs }: { runs: Run[] }) {
  const pairs = runs.flatMap((c) => {
    const p = runs.find((r) => r.id === c.config.parent_run_id)
    return p ? [[p, c] as const] : []
  })
  if (!pairs.length) return null
  return <>{pairs.map(([p, c]) => {
    const patch = c.config.patch as { title?: string; changes?: { path: string; from: unknown; to: unknown }[] } | null
    const m = p.eval_metric?.name ?? ''
    const a = p.holdout?.metrics[m], b = c.holdout?.metrics[m]
    const d = a != null && b != null ? b - a : null
    const better = d != null && (p.eval_metric?.greater_is_better ? d > 0 : d < 0)
    return (
      <div key={c.id} className="card patch-compare" data-testid="exp-patch-compare">
        <div className="card-body">
          <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
            <b>Vela patch</b><span className="chip">{patch?.title ?? 'config change'}</span>
            <span className="mono">{p.id}</span><span className="muted">→</span><span className="mono">{c.id}</span>
            {(patch?.changes ?? []).map((x) => <span key={x.path} className="chip">{x.path}: {JSON.stringify(x.from)} → {JSON.stringify(x.to)}</span>)}
          </div>
          {d != null && <p className="math" style={{ margin: '8px 0 0' }} data-testid="exp-patch-delta">Δ{metricInfo(m)?.name ?? m}<sub>holdout</sub> = {fmtScore(b)} − {fmtScore(a)} = <b className={better ? 'g-ok' : 'g-bad'}>{d >= 0 ? '+' : ''}{fmtScore(d)}</b> <span className="helper" style={{ fontStyle: 'normal' }}>({p.eval_metric?.greater_is_better ? 'higher' : 'lower'} is better; same fixed holdout rows)</span></p>}
        </div>
      </div>
    )
  })}</>
}
