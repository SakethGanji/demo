import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type SegCompare, type SegDetect, type SegResult, type SegRun } from '@/shared/api/flows'
import { Register } from '@/shared/components/FlowParts'
import { ApiOnly, ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { fix, pct } from '@/shared/lib/format'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import '@/features/expected-loss/expected-loss.css'
import './segmented.css'

const GLOBAL = '__global__'
const sgn = (v: number | null | undefined, d = 3) => (v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`)

/**
 * G5 · Segmented models, a choice inside Build. Without ?run: the setup, pre-filled from the G0 roles (target, segment
 * column, what to leave out, the split), one primary action. With ?run: one global model against one model per segment
 * on the same holdout, with paired CIs, and the routed version to register. Registry → Segments shows the same view.
 */
export function SegmentedPage({ go }: { go: (r: string) => void }) {
  const runId = useHashParam('run')
  return (
    <ApiOnly mock={(
      <div className="page"><Head />
        <div className="card card-body"><p className="helper">▲ mock mode: segmented models train AutoGluon on the API, which is offline. Start it with <span className="mono">make dev</span>.</p></div></div>
    )}>
      {runId ? <RunView key={runId} runId={runId} go={go} /> : <Setup go={go} />}
    </ApiOnly>
  )
}

function Head({ right }: { right?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div><h1 className="page-title">Segmented Models</h1><p className="helper sub">One AutoGluon model per segment against one global model, on the same holdout; the result is one routed version.</p></div>
      {right}
    </div>
  )
}

function Formula({ seg, binary }: { seg: string; binary: boolean }) {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="seg-formula">
        f<sub>R</sub>(x) = f<sub>k</sub>(x) if {seg}(x) = k is routed, else f<sub>G</sub>(x) &nbsp;·&nbsp; {binary ? <>ΔAUC = AUC(f<sub>R</sub>) − AUC(f<sub>G</sub>)</> : <>ΔMSE = mean(e<sub>R</sub>² − e<sub>G</sub>²)</>} on the same rows
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------------ setup

function Setup({ go }: { go: (r: string) => void }) {
  const dsId = useHashParam('dataset') ?? 'segmented-loans'
  const det = useFetch(() => flows.seg.detect(dsId), [dsId])
  const ds = useFetch(() => api.datasets.get(dsId), [dsId])
  // Build passes its roles (with the user's edits) as ?label=&segment=; they override the detection.
  const fromBuild = (): Partial<SegDetect> => {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
    return { ...(q.get('label') ? { label: q.get('label') } : {}), ...(q.get('segment') ? { segment_column: q.get('segment') } : {}) }
  }
  const [pick, setPick] = useState<Partial<SegDetect>>(fromBuild)
  const [limit, setLimit] = useState(20)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => setPick(fromBuild()), [dsId]) // eslint-disable-line react-hooks/exhaustive-deps
  if (det.err || ds.err) return <div className="page"><Head /><ErrorCard err={det.err || ds.err} /></div>
  if (!det.data || !ds.data) return <div className="page"><Head /><Loading>Reading the column roles…</Loading></div>
  const d = { ...det.data, ...pick }
  const segCol = ds.data.profile.columns.find((c) => c.name === d.segment_column)
  const groups = segCol?.top ?? []
  const segOptions = ds.data.profile.columns.filter((c) => c.type === 'category' && c.unique >= 2 && c.unique <= 12 && c.name !== d.label)
  const h = det.data.heterogeneity
  const start = async () => {
    setBusy(true); setErr('')
    try {
      const r = await flows.seg.create({ dataset_id: dsId, label: d.label!, segment_column: d.segment_column!, drop_columns: d.drop_columns.filter((c) => c !== d.label && c !== d.segment_column), time_limit: limit })
      go(`segmented?run=${r.id}`)
    } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  const why = (k: 'label' | 'segment_column') => (pick[k] && pick[k] !== det.data![k] ? 'set by you' : (det.data!.evidence[d[k] ?? ''] ?? []).join(', '))
  return (
    <div className="page" data-testid="seg-setup">
      <Head />
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">What we use</h3><span className="helper" style={{ fontSize: 12 }}>{ds.data.name} · {ds.data.rows.toLocaleString()} rows · detected from the column roles; change any of them</span></div>
          <button className="btn primary" disabled={!d.label || !d.segment_column || busy} onClick={start} data-testid="seg-start">{busy ? 'Starting…' : `Train global + ${groups.length || 'k'} segment models →`}</button></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <Formula seg={d.segment_column ?? 's'} binary={d.problem_type !== 'regression'} />
          <div className="table-wrap"><table className="table compact" data-testid="seg-detected">
            <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
            <tbody>
              <tr><td>target y</td>
                <td><select className="input mono" value={d.label ?? ''} onChange={(e) => setPick({ ...pick, label: e.target.value })} data-testid="seg-label">
                  {!d.label && <option value="">—</option>}{ds.data.profile.columns.map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12 }}>{d.label ? why('label') : 'nothing detected: pick a column'}</td></tr>
              <tr><td>segment s</td>
                <td><select className="input mono" value={d.segment_column ?? ''} onChange={(e) => setPick({ ...pick, segment_column: e.target.value })} data-testid="seg-column">
                  {!d.segment_column && <option value="">—</option>}{segOptions.map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12 }}>{d.segment_column ? why('segment_column') : 'nothing detected: pick a category with 2–12 values'}</td></tr>
              <tr><td>left out</td><td className="mono" colSpan={2}>{d.drop_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· IDs, leaks, protected; {d.split === 'random' ? 'random holdout' : `${d.split} holdout by ${d.time_column ?? d.group_column}`}</span></td></tr>
            </tbody>
          </table></div>
          {h && !pick.label && !pick.segment_column && <p className={`helper ${h.opposite ? 'el-ok' : ''}`} style={{ fontSize: 12.5 }} data-testid="seg-hetero">
            {h.opposite ? '● ' : ''}A quick look before any fit: {h.words}. ρ is the rank correlation with {d.label} inside each segment.</p>}
          <details className="an-adv">
            <summary className="micro-label">Advanced · {limit} s per model</summary>
            <div className="row" style={{ gap: 8, padding: 'var(--space-8) 0' }}>
              <label className="helper">time per model (s)</label>
              <input className="input mono" type="number" min={10} max={3600} value={limit} onChange={(e) => setLimit(Number(e.target.value) || 20)} style={{ width: 90 }} data-testid="seg-limit" />
              <span className="helper" style={{ fontSize: 12 }}>AutoGluon medium_quality, the same config for the global model and every segment's.</span>
            </div>
          </details>
          <p className="helper" style={{ fontSize: 12.5 }}>The global model sees every training row, {d.segment_column ?? 'the segment'} included; each segment's model sees only its own rows. A segment with fewer than 300 training rows (or 30 of the rarer class) keeps the global model. Where the split has a tuning slice, a segment is routed to its own model only if that model beat the global one there, so the holdout stays untouched until the comparison.</p>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------------------------------------- run page

function RunView({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const r = useFetch(() => flows.seg.get(runId), [runId, live.done], { pollMs: 4000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <div className="page"><Head /><ErrorCard err={r.err} /></div>
  if (!r.data) return <div className="page"><Head /><Loading>Loading the run…</Loading></div>
  const run = r.data
  const started = live.events.filter((e) => e.type === 'seg_model_started').map((e) => ({ model: e.model as string, rows: e.rows as number }))
  const done = new Set(live.events.filter((e) => e.type === 'seg_model_finished').map((e) => e.model as string))
  return (
    <div className="page" data-testid="seg-run" data-status={run.status}>
      <Head right={run.status === 'succeeded' ? <Register runId={runId} go={go} name={`${run.dataset_id}.segmented`} /> : undefined} />
      <p className="helper mono" style={{ fontSize: 12, marginTop: -8 }}>{run.name} · {runId} · y = {run.config.label} · s = {run.config.segment_column}</p>
      {run.status === 'failed' && <ErrorCard err={run.error ?? 'The run failed.'} />}
      {(run.status === 'queued' || run.status === 'running') && (
        <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
          <div className="row"><ResonanceWave /> <span>Training the global model, then one per {run.config.segment_column}, {run.config.time_limit ?? '—'} s each · {live.stage}</span></div>
          <div className="el-steps seg-steps">{started.map((m) => (
            <div key={m.model} className={`el-step ${done.has(m.model) ? 'done' : 'on'}`}><b>{m.model === GLOBAL ? 'global' : m.model}</b><span className="helper"> · {m.rows.toLocaleString()} rows</span><span className="mono">{done.has(m.model) ? '✓' : '…'}</span></div>
          ))}</div>
        </div></div>
      )}
      {run.status === 'succeeded' && run.segmented && <SegResults run={run} />}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- results (shared)

/** A paired difference as a dot with its 95% CI on a symmetric axis around 0 (green when it favours the segment model). */
function CiBar({ d, span, better }: { d: { delta: number; ci: [number, number] } | null | undefined; span: number; better: 'up' | 'down' }) {
  if (!d) return <span className="helper">—</span>
  const x = (v: number) => `${50 + (Math.max(-span, Math.min(span, v)) / span) * 50}%`
  const good = better === 'up' ? d.ci[0] > 0 : d.ci[1] < 0
  const bad = better === 'up' ? d.ci[1] < 0 : d.ci[0] > 0
  return (
    <div className={`seg-ci ${good ? 'good' : bad ? 'bad' : ''}`} title={`${sgn(d.delta, 4)} [${sgn(d.ci[0], 4)}, ${sgn(d.ci[1], 4)}]`}>
      <i className="zero" /><i className="bar" style={{ left: x(d.ci[0]), width: `calc(${x(d.ci[1])} - ${x(d.ci[0])})` }} /><i className="dot" style={{ left: x(d.delta) }} />
    </div>
  )
}

/** The comparison: used by the run page and by Registry → Segments. */
export function SegResults({ run }: { run: SegRun }) {
  const s = run.segmented as SegResult
  const bin = s.problem_type === 'binary'
  const H = s.headline
  const key = bin ? 'auc' : 'rmse'
  const dk = bin ? 'delta_auc' : 'delta_mse'
  const o = s.overall
  const od = o[dk] as SegCompare['delta_auc']
  const all = [...s.segments.map((r) => r.own?.[dk] as SegCompare['delta_auc']), od, s.overall_all_segment_models[dk] as SegCompare['delta_auc']]
  const span = Math.max(1e-6, ...all.filter(Boolean).map((d) => Math.max(Math.abs(d!.ci[0]), Math.abs(d!.ci[1])))) * 1.1
  const verdictCls = (v?: string) => (v === 'routed better' ? 'el-ok' : v === 'global better' ? 'el-bad' : 'helper')
  const fmtD = (d: SegCompare['delta_auc']) => (d ? <>{sgn(d.delta, 4)} <span className="helper">[{sgn(d.ci[0], 4)}, {sgn(d.ci[1], 4)}]</span></> : '—')
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="seg-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Routed vs Global, on the Same Holdout</h3><span className="helper" style={{ fontSize: 12 }}>{s.holdout_rows.toLocaleString()} rows none of the models saw · routing: {s.routing_rule}</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="seg-hero">
          <HeroCell label={`routed · ${H}`} sub={`global ${fix(o.global[key] ?? null, 4)}`}>{fix(o.routed[key] ?? null, 4)}</HeroCell>
          <HeroCell label={bin ? 'ΔAUC overall' : 'ΔMSE overall'} sub={od ? `95% CI [${sgn(od.ci[0], 4)}, ${sgn(od.ci[1], 4)}] · ${o.verdict}` : o.verdict} subClass={verdictCls(o.verdict)}>{sgn(od?.delta, 4)}</HeroCell>
          <HeroCell label="routed segments" sub={`of ${s.segments.length}; the rest use the global model`}>{`${s.routes.length}`}</HeroCell>
          <HeroCell label="segment model better" sub={s.wins.length ? s.wins.join(', ') : 'on no segment (CI excludes 0)'} subClass={s.wins.length ? 'el-ok' : undefined}>{`${s.wins.length}`}</HeroCell>
          <HeroCell label="global better" sub={s.losses.length ? s.losses.join(', ') : 'on no segment'} subClass={s.losses.length ? 'el-bad' : undefined}>{`${s.losses.length}`}</HeroCell>
          {bin && <HeroCell label="threshold t" sub="one t for every model (F7 cost c_FN = 10·c_FP)">{fix(s.threshold, 4)}</HeroCell>}
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula seg={s.segment_column} binary={bin} />
          <p className="helper" style={{ fontSize: 12.5 }}>
            f<sub>G</sub> is the global model (every training row, {s.segment_column} included); f<sub>k</sub> the model trained only on rows with {s.segment_column} = k; f<sub>R</sub> the routed version.
            {bin
              ? <> AUC is the chance a random {String(s.positive_class)} row scores above a random other row. ΔAUC and its 95 % interval come from DeLong's paired variance, Var(Δ) = Var(A<sub>R</sub>) + Var(A<sub>G</sub>) − 2 Cov, since both models score the same rows.</>
              : <> e = ŷ − y on a holdout row; the 95 % interval of ΔMSE resamples rows (paired bootstrap, 1,000 draws).</>}
            {' '}Better means the interval excludes 0.
          </p>
        </div>
      </div>
      <div className="card" data-testid="seg-table">
        <div className="card-head"><div className="left"><h3 className="card-title">Per Segment</h3><span className="helper" style={{ fontSize: 12 }}>each segment's own model against the global model on that segment's holdout rows</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>{s.segment_column}</th><th className="r">train rows</th><th className="r">holdout</th><th className="r">global {H}</th><th className="r">own {H}</th><th className="r">{bin ? 'ΔAUC' : 'ΔMSE'} [95% CI]</th><th style={{ width: '16%' }}>0-centred</th><th>verdict</th><th>scored by</th></tr></thead>
          <tbody>
            {s.segments.map((r) => (
              <tr key={r.value} data-testid="seg-row">
                <td className="mono">{r.value}</td><td className="r mono">{r.train_rows.toLocaleString()}</td><td className="r mono">{r.holdout_rows.toLocaleString()}{bin && r.own?.rate != null ? <span className="helper"> · {pct(r.own.rate)}</span> : null}</td>
                <td className="r mono">{fix(r.own?.global[key] ?? r.routed_cmp?.global[key] ?? null, 4)}</td><td className="r mono">{r.own ? fix(r.own.routed[key] ?? null, 4) : '—'}</td>
                <td className="r mono">{r.own ? fmtD(r.own[dk] as SegCompare['delta_auc']) : '—'}</td>
                <td><CiBar d={r.own?.[dk] as SegCompare['delta_auc']} span={span} better={bin ? 'up' : 'down'} /></td>
                <td className={verdictCls(r.own?.verdict)}>{r.own?.verdict ?? 'no own model'}</td>
                <td title={r.route_why}><b>{r.routed ? 'its own model' : 'global'}</b><div className="helper" style={{ fontSize: 11 }}>{r.route_why}</div></td>
              </tr>))}
            <tr className="seg-total"><td><b>overall · routed</b></td><td className="r mono">{s.global.rows.toLocaleString()}</td><td className="r mono">{o.n.toLocaleString()}</td><td className="r mono">{fix(o.global[key] ?? null, 4)}</td><td className="r mono">{fix(o.routed[key] ?? null, 4)}</td>
              <td className="r mono">{fmtD(od)}</td><td><CiBar d={od} span={span} better={bin ? 'up' : 'down'} /></td><td className={verdictCls(o.verdict)}>{o.verdict}</td><td className="helper">{s.routes.length} segment models + global</td></tr>
            <tr className="seg-total"><td>overall · every segment model</td><td /><td className="r mono">{s.overall_all_segment_models.n.toLocaleString()}</td><td className="r mono">{fix(s.overall_all_segment_models.global[key] ?? null, 4)}</td><td className="r mono">{fix(s.overall_all_segment_models.routed[key] ?? null, 4)}</td>
              <td className="r mono">{fmtD(s.overall_all_segment_models[dk] as SegCompare['delta_auc'])}</td><td><CiBar d={s.overall_all_segment_models[dk] as SegCompare['delta_auc']} span={span} better={bin ? 'up' : 'down'} /></td><td className={verdictCls(s.overall_all_segment_models.verdict)}>{s.overall_all_segment_models.verdict}</td><td className="helper">not routed: shown for honesty</td></tr>
          </tbody>
        </table></div>
        <div className="card-body"><details className="an-adv" data-testid="seg-models">
          <summary className="micro-label">The models · global {s.global.best_model} · {s.segments.filter((r) => r.model).length} segment models</summary>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>model</th><th className="r">rows</th><th>best model</th><th className="r">models fitted</th><th className="r">fit s</th>{s.tuning_rows > 0 && <th className="r">tuning loss: global → own</th>}</tr></thead>
            <tbody>
              <tr><td><b>global</b></td><td className="r mono">{s.global.rows.toLocaleString()}</td><td className="mono">{s.global.best_model}</td><td className="r mono">{s.global.n_models}</td><td className="r mono">{fix(s.global.fit_seconds, 0)}</td>{s.tuning_rows > 0 && <td />}</tr>
              {s.segments.map((r) => <tr key={r.value}><td className="mono">{s.segment_column} = {r.value}</td><td className="r mono">{r.train_rows.toLocaleString()}</td><td className="mono">{r.best_model ?? <span className="helper">{r.why}</span>}</td><td className="r mono">{r.n_models ?? '—'}</td><td className="r mono">{fix(r.fit_seconds ?? null, 0)}</td>
                {s.tuning_rows > 0 && <td className="r mono">{r.tuning ? `${fix(r.tuning.global, 4)} → ${fix(r.tuning.segment, 4)}` : '—'}</td>}</tr>)}
            </tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12 }}>Same config for every model: AutoGluon {run.config.presets ?? 'medium_quality'}, {run.config.time_limit ?? '—'} s, eval metric {s.eval_metric}. {s.unseen_in_training.length ? `Holdout values never seen in training (${s.unseen_in_training.join(', ')}) use the global model. ` : ''}Unknown {s.segment_column} values at scoring time use the global model too.</p>
        </details></div>
      </div>
    </div>
  )
}

/** Registry → Segments: the version's run results (same view as the run page). */
export function SegVersionTab({ runId }: { runId: string }) {
  const r = useFetch(() => flows.seg.get(runId), [runId])
  if (r.err) return <ErrorCard err={r.err} />
  if (!r.data?.segmented) return <Loading>Loading the segment comparison…</Loading>
  return <SegResults run={r.data} />
}
