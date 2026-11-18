import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { useApi } from '@/shared/api/mode'
import type { AuditEntry, ModelVersion, RegistryCounts, Run, RunEvent } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { RunString, type RunState } from '@/shared/components/RunString'
import { XYPlot, niceTicks, padDomain } from '@/shared/components/XYPlot'
import { fmtScore, metricInfo } from '@/shared/lib/metrics'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { StatusPill } from '@/shared/primitives/StatusPill'

const ACTION: Record<AuditEntry['action'], string> = { registered: 'registered', stage_changed: 'moved', approved: 'approved', rejected: 'rejected', card_edited: 'edited the card of', batch_scored: 'scored a batch with' }
const ago = (iso: string | null) => {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`
}

/** One run as a string: completed runs are taut, running ones carry a wave up to elapsed / τ. */
function runState(r: Run, active: { id: string; models_finished: number; models_started: number; best_val: number | null } | null, now: number): RunState {
  const m = r.eval_metric?.name ?? ''
  if (r.status === 'succeeded') {
    const v = r.holdout?.metrics[m]
    return { id: r.id, label: r.name, status: 'Completed', progress: 1, readout: `${metricInfo(m)?.name ?? m} ${fmtScore(v, 4)} · ${r.best_model ?? ''}` }
  }
  if (r.status === 'running') {
    const tl = Number(r.config?.time_limit ?? 0) || null
    const el = r.started_at ? (now - new Date(r.started_at).getTime()) / 1000 : 0
    const a = active?.id === r.id ? active : null
    return { id: r.id, label: r.name, status: 'Training', progress: tl ? Math.min(0.97, el / tl) : 0.5,
      readout: `${a ? `${a.models_finished} / ${a.models_started} models` : 'fitting'}${a?.best_val != null ? ` · best ${fmtScore(a.best_val, 4)}` : ''} · ${Math.round(el)}s${tl ? ` / τ ${tl}s` : ''}` }
  }
  if (r.status === 'queued') return { id: r.id, label: r.name, status: 'Queued', progress: 0, readout: 'queued' }
  return { id: r.id, label: r.name, status: 'Failed', progress: 0.4, readout: (r.error ?? r.status).slice(0, 60) }
}

/** Home in live mode: the API's real runs, best scores, registry and audit, and a training monitor driven by the
 *  active run (or the most recent finished one, replayed from its events). */
export function LiveHome({ go }: { go: (r: string) => void }) {
  const { status } = useApi()
  const [runs, setRuns] = useState<Run[]>([])
  const [reg, setReg] = useState<{ counts: RegistryCounts; audit: AuditEntry[]; production: ModelVersion[] } | null>(null)
  const [tick, setTick] = useState(0)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => { setTick((x) => x + 1); setNow(Date.now()) }, 4000); return () => clearInterval(t) }, [])
  useEffect(() => {
    api.runs.list().then(setRuns).catch(() => {})
    api.registry.summary().then(setReg).catch(() => {})
  }, [tick, status?.active?.id])

  const active = status?.active ?? null
  const monitorRun = active ? runs.find((r) => r.id === active.id) ?? null : runs.find((r) => r.status === 'succeeded') ?? null
  const best = useMemo(() => {
    const by = new Map<string, Run>()
    for (const r of runs) {
      if (r.status !== 'succeeded' || !r.eval_metric) continue
      const key = `${r.dataset_id}|${r.label}|${r.eval_metric.name}`
      const cur = by.get(key)
      const v = r.holdout?.metrics[r.eval_metric.name], cv = cur?.holdout?.metrics[cur.eval_metric!.name]
      if (v == null) continue
      if (!cur || cv == null || (r.eval_metric.greater_is_better ? v > cv : v < cv)) by.set(key, r)
    }
    return [...by.values()]
  }, [runs])

  return (
    <div className="stack" style={{ gap: 'var(--space-24)' }} data-testid="home-live">
      <div className="bench-head">
        <h2 className="bench-title">On the Strings</h2>
        <span className="helper">Each run is one string: taut when finished (readout: its holdout score), a wave up to t / τ while AutoGluon fits.</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => go('experiments')}>Open Experiments →</button>
      </div>
      <div className="card run-strings" data-testid="home-runs">
        {runs.length === 0 && <p className="helper" style={{ padding: 16 }}>No runs yet. <button className="btn sm primary" onClick={() => go('quick-train')}>Train your first model →</button></p>}
        {runs.slice(0, 6).map((r) => <RunString key={r.id} run={runState(r, active, now)} onClick={() => go(r.status === 'succeeded' ? `explain?run=${r.id}` : `quick-train?run=${r.id}`)} />)}
      </div>

      <div className="grid two">
        <TrainingMonitor run={monitorRun} live={!!active && monitorRun?.id === active.id} />
        <div className="stack">
          <div className="card" data-testid="home-best">
            <div className="card-head"><div className="left"><h3 className="card-title">Best Scores</h3><span className="helper" style={{ fontSize: 12 }}>per dataset, label and metric · holdout</span></div></div>
            {best.length ? <table className="table compact"><tbody>{best.slice(0, 5).map((r) => {
              const m = r.eval_metric!.name
              return <tr key={r.id} onClick={() => go(`explain?run=${r.id}`)} style={{ cursor: 'pointer' }}><td>{r.name}</td><td className="mono muted">{r.best_model}</td><td className="r mono"><b>{fmtScore(r.holdout?.metrics[m], 4)}</b> <span className="muted">{metricInfo(m)?.name ?? m}</span></td></tr>
            })}</tbody></table> : <p className="helper card-body">Nothing finished yet.</p>}
          </div>
          <div className="card" data-testid="home-registry">
            <div className="card-head"><div className="left"><h3 className="card-title">Registry</h3></div>
              <div className="row" style={{ gap: 6 }}>
                <StatusPill status="success">{reg?.counts.stages.Production ?? 0} production</StatusPill>
                <StatusPill status="warning">{reg?.counts.stages.Staging ?? 0} staging</StatusPill>
                <StatusPill status="neutral">{reg?.counts.approvals.pending ?? 0} pending</StatusPill>
                <button className="btn sm" onClick={() => go('registry')}>Open →</button>
              </div>
            </div>
            <div className="card-body home-audit">
              {reg?.audit.length ? reg.audit.slice(0, 5).map((a) => (
                <div key={a.id} className="ha-row"><span className="mono muted">{ago(a.ts)}</span><span><b>{a.actor}</b> {ACTION[a.action]} <a href={`#/registry?version=${a.version_id}`} className="mono">{a.model_name} v{a.version}</a>{a.to_stage && a.action === 'stage_changed' ? <> → {a.to_stage}</> : ''}</span><span className="mono muted">{(a.model_hash ?? '').slice(0, 8)}</span></div>
              )) : <p className="helper">No registry activity yet. Register a finished run from the Registry or Experiments.</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** The hero's one-line readout in live mode: runs, what is in Production, the best score. */
export function LiveHeroRead() {
  const { status } = useApi()
  const [best, setBest] = useState<Run | null>(null)
  const [prod, setProd] = useState<ModelVersion | null>(null)
  useEffect(() => {
    api.runs.list().then((rs) => setBest(rs.find((r) => r.status === 'succeeded' && r.holdout) ?? null)).catch(() => {})
    api.registry.summary().then((x) => setProd(x.production[0] ?? null)).catch(() => {})
  }, [status?.runs, status?.registry?.stages.Production])
  const m = best?.eval_metric?.name ?? ''
  return (
    <div className="hero-read" data-testid="home-read">
      <span><b>{status?.runs ?? 0}</b> runs{status?.active ? <> · <b>1</b> on the strings</> : ''}</span>
      <span><b>{status?.registry?.stages.Production ?? 0}</b> in Production{prod ? <> · <b>{prod.model_name} v{prod.version}</b></> : ''}</span>
      {best && <span>latest {metricInfo(m)?.name ?? m}: <b>{fmtScore(best.holdout?.metrics[m], 4)}</b></span>}
    </div>
  )
}

/** Validation score of each model as it finishes, against seconds since the fit started, with the best-so-far
 *  staircase; the run's AutoGluon log beside it. Follows the active run live. */
function TrainingMonitor({ run, live }: { run: Run | null; live: boolean }) {
  const [evs, setEvs] = useState<RunEvent[]>([])
  const next = useRef(0)
  useEffect(() => { setEvs([]); next.current = 0 }, [run?.id])
  useEffect(() => {
    if (!run) return
    let alive = true
    const pull = () => api.runs.events(run.id, next.current).then((r) => {
      if (!alive || !r.events.length) return
      next.current = r.next
      setEvs((e) => [...e, ...r.events])
    }).catch(() => {})
    pull()
    if (!live) return () => { alive = false }
    const t = setInterval(pull, 1500)
    return () => { alive = false; clearInterval(t) }
  }, [run?.id, live]) // eslint-disable-line react-hooks/exhaustive-deps

  const fitStart = evs.find((e) => e.type === 'fit_started')
  const greater = fitStart ? (fitStart.greater_is_better as boolean) !== false : true
  const pts = evs.filter((e) => e.type === 'model_finished' && e.phase !== 'dystack' && e.val_score_natural != null)
    .map((e) => ({ t: e.ts - (fitStart?.ts ?? e.ts), v: Number(e.val_score_natural), m: String(e.model) }))
  const stair: [number, number][] = []
  let b: number | null = null
  for (const p of pts) { if (b == null || (greater ? p.v > b : p.v < b)) b = p.v; stair.push([p.t, b]) }
  const logs = evs.filter((e) => e.type === 'log').slice(-12)
  const metric = String(fitStart?.eval_metric ?? run?.eval_metric?.name ?? '')
  const [y0, y1] = padDomain(pts.map((p) => p.v), 0.1)
  const tMax = Math.max(10, ...pts.map((p) => p.t)) * 1.05
  return (
    <div className="stack">
      <PlotFrame title="Training Monitor" right={run ? <><span className="mono" style={{ fontSize: 12.5 }}>{run.id}</span>{live ? <ResonanceWave width={56} height={14} /> : <span className="helper" style={{ fontSize: 12 }}>last run · replayed</span>}</> : undefined}
        chips={run ? <><ParamChip sym="models" value={String(pts.length)} /><ParamChip sym="metric" value={metric || '…'} />{b != null && <ParamChip sym="best" value={fmtScore(b, 4)} />}</> : undefined}
        legend={[{ name: 'sᵥₐₗ(m) per model', color: 'var(--viz-a)', data: [] }, { name: 'best so far', color: 'var(--viz-c)', data: [] }]}
        caption={<span>sᵥₐₗ(m): each model’s validation {metric} as AutoGluon reports it when the model finishes, at t seconds after <span className="mono">fit()</span> started; the staircase is the best so far, {greater ? 'max' : 'min'}<sub>m′ ≤ m</sub> sᵥₐₗ(m′) ({greater ? 'higher' : 'lower'} is better).</span>}>
        {!run && <p className="helper" style={{ padding: 16 }}>No run to monitor yet.</p>}
        {run && pts.length === 0 && <div className="row helper" style={{ padding: 16 }}><ResonanceWave /> waiting for the first model…</div>}
        {pts.length > 0 && (
          <XYPlot height={210} testId="home-monitor" ariaLabel="validation score over time"
            x={{ min: 0, max: tMax, ticks: niceTicks(0, tMax, 5), title: 't (s)', fmt: (v) => v.toFixed(0) }}
            y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 4), title: 'sᵥₐₗ', fmt: (v) => fmtScore(v, 3) }}
            series={[{ name: 'best so far', color: 'var(--viz-c)', points: stair, step: 'after', width: 2 },
              { name: 'sᵥₐₗ(m)', color: 'var(--viz-a)', points: pts.map((p) => [p.t, p.v]), dots: true, width: 0, tip: (i) => pts[i].m }]} />
        )}
      </PlotFrame>
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Live Log</h3><span className="mono" style={{ fontSize: 12.5 }}>{run?.id ?? ''}</span></div>
          {live ? <StatusPill status="info">Streaming</StatusPill> : <StatusPill status="neutral">finished</StatusPill>}</div>
        <div className="card-body"><div className="log-panel" aria-live="polite" data-testid="home-log">
          {logs.length ? logs.map((e) => <div key={e.seq} className={e.level === 'warning' ? 'lw' : 'lt'}>{String(e.message ?? '').slice(0, 140)}</div>) : <div className="lt">{run ? 'no log lines yet' : 'no run yet'}</div>}
        </div></div>
      </div>
    </div>
  )
}
