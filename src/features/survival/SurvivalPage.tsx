import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { flows, type SurvDetect, type SurvGroup, type SurvResult, type SurvRun, type SurvTry } from '@/shared/api/flows'
import type { ModelVersion } from '@/shared/api/types'
import { Register } from '@/shared/components/FlowParts'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ApiOnly, ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { useFetch } from '@/shared/hooks/useFetch'
import { useHashParam } from '@/shared/hooks/useHashParam'
import { useRunEvents } from '@/shared/hooks/useRunEvents'
import { fix, pct } from '@/shared/lib/format'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { CiBar } from '@/features/segmented/SegmentedPage'
import '@/features/expected-loss/expected-loss.css'
import '@/features/segmented/segmented.css'

const MODEL = { cox: 'Cox PH', rsf: 'random survival forest' } as const
const GROUP_COLOR: Record<string, string> = { low: 'var(--viz-a)', 'medium-low': 'var(--viz-b)', 'medium-high': 'var(--viz-c)', high: 'var(--status-danger)' }
const u = (unit: string | null | undefined) => (unit ? `${unit}s` : 'time units')

/**
 * G6 · Time to event, a choice inside Build. Without ?run: the setup, pre-filled from the G0 roles (duration, event,
 * horizons, what to leave out, the split), one primary action. With ?run: Cox PH and a random survival forest on the same
 * holdout: Harrell's C, AUC(τ), calibration against Kaplan–Meier by risk group, hazard ratios. Registry → Survival shows the
 * same view; Try It draws one applicant's survival curve.
 */
export function SurvivalPage({ go }: { go: (r: string) => void }) {
  const runId = useHashParam('run')
  return (
    <ApiOnly mock={(
      <div className="page"><Head />
        <div className="card card-body"><p className="helper">▲ mock mode: survival models train on the API, which is offline. Start it with <span className="mono">make dev</span>.</p></div></div>
    )}>
      {runId ? <RunView key={runId} runId={runId} go={go} /> : <Setup go={go} />}
    </ApiOnly>
  )
}

function Head({ right }: { right?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div><h1 className="page-title">Time to Event</h1><p className="helper sub">How long until an event (an account closes, a loan prepays), using every row, censored ones included.</p></div>
      {right}
    </div>
  )
}

function Formula() {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="surv-formula">
        S(t | x) = P(T &gt; t | x) &nbsp;·&nbsp; Cox: h(t | x) = h<sub>0</sub>(t)·exp(β<sup>T</sup>z), S(t | x) = S<sub>0</sub>(t)<sup>exp(β<sup>T</sup>z)</sup> &nbsp;·&nbsp; C = P(r̂<sub>i</sub> &gt; r̂<sub>j</sub> | T<sub>i</sub> &lt; T<sub>j</sub>, δ<sub>i</sub> = 1)
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------------ setup

function Setup({ go }: { go: (r: string) => void }) {
  const dsId = useHashParam('dataset') ?? 'account-attrition'
  const det = useFetch(() => flows.surv.detect(dsId), [dsId])
  const ds = useFetch(() => api.datasets.get(dsId), [dsId])
  // Build passes its roles (with the user's edits) as ?duration=&event=; they override the detection.
  const fromBuild = (): Partial<SurvDetect> => {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '')
    return { ...(q.get('duration') ? { duration_column: q.get('duration') } : {}), ...(q.get('event') ? { event_column: q.get('event') } : {}) }
  }
  const [pick, setPick] = useState<Partial<SurvDetect>>(fromBuild)
  const [hz, setHz] = useState('')
  const [trees, setTrees] = useState(200)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => setPick(fromBuild()), [dsId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (det.data) setHz(det.data.horizons.join(', ')) }, [det.data])
  if (det.err || ds.err) return <div className="page"><Head /><ErrorCard err={det.err || ds.err} /></div>
  if (!det.data || !ds.data) return <div className="page"><Head /><Loading>Reading the column roles…</Loading></div>
  const d = { ...det.data, ...pick }
  const nums = ds.data.profile.columns.filter((c) => c.type === 'number' && c.unique > 5)
  const flags = ds.data.profile.columns.filter((c) => c.unique === 2)
  const horizons = hz.split(/[,\s]+/).map(Number).filter((x) => Number.isFinite(x) && x > 0)
  const start = async () => {
    setBusy(true); setErr('')
    try {
      const r = await flows.surv.create({ dataset_id: dsId, duration_column: d.duration_column!, event_column: d.event_column!, horizons,
        drop_columns: d.drop_columns.filter((c) => c !== d.duration_column && c !== d.event_column), n_estimators: trees })
      go(`survival?run=${r.id}`)
    } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  const why = (k: 'duration_column' | 'event_column') => (pick[k] && pick[k] !== det.data![k] ? 'set by you' : (det.data!.evidence[d[k] ?? ''] ?? []).join(' · '))
  return (
    <div className="page" data-testid="surv-setup">
      <Head />
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">What we use</h3><span className="helper" style={{ fontSize: 12 }}>{ds.data.name} · {ds.data.rows.toLocaleString()} rows · detected from the column roles; change any of them</span></div>
          <button className="btn primary" disabled={!d.duration_column || !d.event_column || !horizons.length || busy} onClick={start} data-testid="surv-start">{busy ? 'Starting…' : 'Train Cox + survival forest →'}</button></div>
        <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
          <Formula />
          <div className="table-wrap"><table className="table compact" data-testid="surv-detected">
            <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
            <tbody>
              <tr><td>time T</td>
                <td><select className="input mono" value={d.duration_column ?? ''} onChange={(e) => setPick({ ...pick, duration_column: e.target.value })} data-testid="surv-duration">
                  {!d.duration_column && <option value="">—</option>}{nums.map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12 }}>{d.duration_column ? why('duration_column') : 'nothing detected: pick the time each row was followed'}</td></tr>
              <tr><td>event δ</td>
                <td><select className="input mono" value={d.event_column ?? ''} onChange={(e) => setPick({ ...pick, event_column: e.target.value })} data-testid="surv-event">
                  {!d.event_column && <option value="">—</option>}{flags.map((c) => <option key={c.name}>{c.name}</option>)}</select></td>
                <td className="helper" style={{ fontSize: 12 }}>{d.event_column ? why('event_column') : 'nothing detected: pick the 0/1 column (1 = the event happened, 0 = censored)'}</td></tr>
              <tr><td>horizons τ</td>
                <td><input className="input mono" value={hz} onChange={(e) => setHz(e.target.value)} style={{ width: 140 }} data-testid="surv-horizons" /></td>
                <td className="helper" style={{ fontSize: 12 }}>Ŝ(τ) is reported at these {u(det.data.unit)}{det.data.duration_range ? ` (T runs ${det.data.duration_range[0]}–${det.data.duration_range[1]})` : ''}</td></tr>
              <tr><td>left out</td><td className="mono" colSpan={2}>{d.drop_columns.join(', ') || 'none'} <span className="helper" style={{ fontSize: 12 }}>· IDs, dates, leaks · {d.split_why}</span></td></tr>
            </tbody>
          </table></div>
          {!det.data.strong && det.data.duration_column && <p className="helper el-bad" style={{ fontSize: 12.5 }}>● '{det.data.duration_column}' reads like time so far (a snapshot), not necessarily time up to '{det.data.event_column}'. Only train if each row's time ends at its event or at censoring.</p>}
          <details className="an-adv">
            <summary className="micro-label">Advanced · {trees} trees</summary>
            <div className="row" style={{ gap: 8, padding: 'var(--space-8) 0' }}>
              <label className="helper">trees in the forest</label>
              <input className="input mono" type="number" min={20} max={1000} value={trees} onChange={(e) => setTrees(Number(e.target.value) || 200)} style={{ width: 90 }} data-testid="surv-trees" />
              <span className="helper" style={{ fontSize: 12 }}>Cox PH has nothing to tune (a tiny ridge, α = 0.001, keeps it stable).</span>
            </div>
          </details>
          <p className="helper" style={{ fontSize: 12.5 }}>A censored row (δ = 0) still counts: we know it lasted at least T. Both models train on the same rows; the one with the lower integrated Brier score on a validation slice serves, so the holdout stays untouched until the comparison.</p>
          {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------------------------------------- run page

function RunView({ runId, go }: { runId: string; go: (r: string) => void }) {
  const live = useRunEvents(runId)
  const r = useFetch(() => flows.surv.get(runId), [runId, live.done], { pollMs: 3000, while: (d) => !!d && (d.status === 'queued' || d.status === 'running') })
  if (r.err) return <div className="page"><Head /><ErrorCard err={r.err} /></div>
  if (!r.data) return <div className="page"><Head /><Loading>Loading the run…</Loading></div>
  const run = r.data
  const done = new Set(live.events.filter((e) => e.type === 'surv_model_finished').map((e) => e.model as string))
  const on = new Set(live.events.filter((e) => e.type === 'surv_model_started').map((e) => e.model as string))
  return (
    <div className="page" data-testid="surv-run" data-status={run.status}>
      <Head right={run.status === 'succeeded' ? <Register runId={runId} go={go} name={`${run.dataset_id}.survival`} /> : undefined} />
      <p className="helper mono" style={{ fontSize: 12, marginTop: -8 }}>{run.name} · {runId} · T = {run.config.duration_column} · δ = {run.config.event_column}</p>
      {run.status === 'failed' && <ErrorCard err={run.error ?? 'The run failed.'} />}
      {(run.status === 'queued' || run.status === 'running') && (
        <div className="card"><div className="card-body stack" style={{ gap: 8 }}>
          <div className="row"><ResonanceWave /> <span>Fitting both models, then the holdout checks · {live.stage}</span></div>
          <div className="el-steps">{(['cox', 'rsf'] as const).map((m) => (
            <div key={m} className={`el-step ${done.has(m) ? 'done' : on.has(m) ? 'on' : ''}`}><b>{MODEL[m]}</b><span className="mono">{done.has(m) ? '✓' : on.has(m) ? '…' : ''}</span></div>
          ))}</div>
        </div></div>
      )}
      {run.status === 'succeeded' && run.survival && <SurvResults run={run} />}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- results (shared)

/** Kaplan–Meier (steps) against the mean predicted Ŝ(t) (dashed) for each risk group, horizons marked. */
function CurveChart({ s }: { s: SurvResult }) {
  const tmax = s.grid[s.grid.length - 1]
  const series: XYSeries[] = s.groups.flatMap((g: SurvGroup) => [
    { name: `${g.group} · KM`, color: GROUP_COLOR[g.group], step: 'after' as const, points: s.grid.map((t, i) => [t, g.km.s[i]] as [number, number]) },
    { name: `${g.group} · predicted`, color: GROUP_COLOR[g.group], dash: '5 4', points: s.grid.map((t, i) => [t, g.pred[i]] as [number, number]) },
  ])
  return (
    <PlotFrame title="Predicted vs Kaplan–Meier, by Risk Group" caption={<>solid: Kaplan–Meier Ŝ<sub>KM</sub>(t) of the holdout rows in each quartile of r̂; dashed: their mean predicted Ŝ(t)</>}
      legend={s.groups.map((g) => ({ name: `${g.group} (${g.n}, ${g.events} events)`, color: GROUP_COLOR[g.group], data: [] }))}>
      <XYPlot testId="surv-curves" height={280} series={series}
        x={{ min: 0, max: tmax, ticks: niceTicks(0, tmax, 6), title: `t (${u(s.unit)})`, fmt: (v) => v.toFixed(0) }}
        y={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'Ŝ(t)', fmt: (v) => v.toFixed(2) }}
        refs={s.horizons.map((h) => ({ x: h, label: `τ = ${h}`, dash: '3 3' }))} />
    </PlotFrame>
  )
}

/** The holdout comparison: used by the run page and by Registry → Survival. */
export function SurvResults({ run }: { run: SurvRun }) {
  const s = run.survival as SurvResult
  const m = s.models[s.served]
  const other = s.served === 'cox' ? 'rsf' : 'cox'
  const a0 = m.at[0]
  const cal = s.calibration
  const lnSpan = Math.max(0.05, ...s.hazard_ratios.map((h) => Math.max(Math.abs(Math.log(h.hr_ci[0])), Math.abs(Math.log(h.hr_ci[1]))))) * 1.05
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="surv-results">
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">On the Holdout</h3><span className="helper" style={{ fontSize: 12 }}>{s.holdout_rows.toLocaleString()} rows neither model saw · {s.events.holdout.toLocaleString()} events, {(s.holdout_rows - s.events.holdout).toLocaleString()} censored · serving: {MODEL[s.served]}</span></div></div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="surv-hero">
          <HeroCell label="Harrell's C" sub={`95% CI [${fix(m.c_index_ci[0], 3)}, ${fix(m.c_index_ci[1], 3)}] · ${MODEL[other]} ${fix(s.models[other].c_index, 3)}`}>{fix(m.c_index, 4)}</HeroCell>
          {a0 && <HeroCell label={`AUC(${a0.t})`} sub={`event by ${a0.t} ${u(s.unit)} vs after`}>{fix(a0.auc, 3)}</HeroCell>}
          <HeroCell label="calibration" sub={`max |Ŝ − Ŝ_KM| · ${cal.in_ci}/${cal.cells} inside the KM 95% CI`} subClass={cal.ok ? 'el-ok' : 'el-bad'}>{`${fix(cal.max_abs_diff, 3)} ${cal.ok ? '✓' : '✕'}`}</HeroCell>
          {a0 && <HeroCell label={`mean Ŝ(${a0.t})`} sub={`still without the event at ${a0.t} ${u(s.unit)}`}>{pct(a0.mean_S)}</HeroCell>}
          <HeroCell label="serving" sub={`lower integrated Brier on ${s.val_rows.toLocaleString()} validation rows: ${fix(s.models.cox.val_ibs, 4)} Cox vs ${fix(s.models.rsf.val_ibs, 4)} forest`}>{s.served === 'cox' ? 'Cox' : 'forest'}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <Formula />
          <p className="helper" style={{ fontSize: 12.5 }}>
            T is {s.duration_column} ({u(s.unit)}); δ = {s.event_column} is 1 when the event happened and 0 when the row is censored (we only know T is at least that long).
            {' '}z is x standardised (numbers) or one-hot against a reference level (categories); β by Cox's partial likelihood; h<sub>0</sub> and S<sub>0</sub> the baseline hazard and survival (Breslow).
            {' '}r̂ is a row's risk score (βᵀz for Cox, the summed cumulative hazard for the forest). C counts the comparable pairs the score orders correctly (0.5 is chance, 1 perfect); its CI resamples rows (200 draws).
            {' '}AUC(τ) separates rows with the event by τ from rows still event-free at τ, weighted for censoring.
          </p>
        </div>
      </div>
      <CurveChart s={s} />
      <div className="card" data-testid="surv-cal">
        <div className="card-head"><div className="left"><h3 className="card-title">Calibration at Each Horizon</h3><span className="helper" style={{ fontSize: 12 }}>mean predicted Ŝ(τ) against Kaplan–Meier [95% CI] per risk group · rule: {cal.rule}</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>risk group</th><th className="r">rows</th><th className="r">events</th>{s.horizons.map((h) => <th key={h} className="r">Ŝ({h}) vs KM</th>)}</tr></thead>
          <tbody>{s.groups.map((g) => (
            <tr key={g.group}><td><b style={{ color: GROUP_COLOR[g.group] }}>●</b> {g.group}</td><td className="r mono">{g.n}</td><td className="r mono">{g.events}</td>
              {g.at.map((a) => <td key={a.t} className={`r mono ${Math.abs(a.diff) <= 0.05 || a.in_ci ? '' : 'el-bad'}`} title={`diff ${a.diff >= 0 ? '+' : ''}${a.diff.toFixed(3)}`}>{a.pred.toFixed(3)} <span className="helper">vs {a.km.toFixed(3)} [{a.lo.toFixed(3)}, {a.hi.toFixed(3)}]</span></td>)}</tr>))}
          </tbody>
        </table></div>
      </div>
      <div className="card" data-testid="surv-hr">
        <div className="card-head"><div className="left"><h3 className="card-title">Hazard Ratios (Cox)</h3><span className="helper" style={{ fontSize: 12 }}>HR = exp(β) per unit of the input: below 1 lowers the hazard (later events), above 1 raises it · 95% Wald CI from the partial likelihood's information</span></div></div>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>term</th><th className="r">HR per unit [95% CI]</th><th className="r">HR per SD</th><th style={{ width: '18%' }}>ln HR, 0-centred</th><th className="r">p</th></tr></thead>
          <tbody>{s.hazard_ratios.map((h) => (
            <tr key={h.term} data-testid="surv-hr-row">
              <td className="mono">{h.kind === 'category' ? <>{h.feature} = {h.level} <span className="helper">vs {s.ref_levels[h.feature]}</span></> : h.term}</td>
              <td className="r mono">{fix(h.hr, 3)} <span className="helper">[{fix(h.hr_ci[0], 3)}, {fix(h.hr_ci[1], 3)}]</span></td>
              <td className="r mono">{h.hr_sd != null ? <>{fix(h.hr_sd, 3)} <span className="helper">σ = {fix(h.sd, 2)}</span></> : '—'}</td>
              <td><CiBar d={{ delta: Math.log(h.hr), ci: [Math.log(h.hr_ci[0]), Math.log(h.hr_ci[1])] }} span={lnSpan} better="down" /></td>
              <td className="r mono">{h.p_value == null ? '—' : h.p_value < 1e-4 ? '< 0.0001' : h.p_value.toFixed(4)}</td></tr>))}
          </tbody>
        </table></div>
        <div className="card-body"><details className="an-adv" data-testid="surv-models">
          <summary className="micro-label">Both models · {MODEL.cox} C {fix(s.models.cox.c_index, 4)} · forest C {fix(s.models.rsf.c_index, 4)}</summary>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>model</th><th className="r">C [95% CI]</th>{s.horizons.map((h) => <th key={h} className="r">AUC({h}) · Brier</th>)}<th className="r">calibration</th><th className="r">validation C · IBS</th><th className="r">fit s</th></tr></thead>
            <tbody>{(['cox', 'rsf'] as const).map((k) => {
              const x = s.models[k]
              return <tr key={k}><td><b>{MODEL[k]}</b>{k === s.served ? <span className="helper"> · serving</span> : null}</td>
                <td className="r mono">{fix(x.c_index, 4)} <span className="helper">[{fix(x.c_index_ci[0], 3)}, {fix(x.c_index_ci[1], 3)}]</span></td>
                {x.at.map((a) => <td key={a.t} className="r mono">{fix(a.auc, 3)} · {fix(a.brier, 3)}</td>)}
                <td className={`r mono ${x.calibration.ok ? 'el-ok' : 'el-bad'}`}>{fix(x.calibration.max_abs_diff, 3)}</td>
                <td className="r mono">{fix(x.val_c_index, 4)} · {fix(x.val_ibs, 4)}</td><td className="r mono">{fix(s.fit_seconds[k], 1)}</td></tr>
            })}</tbody>
          </table></div>
          <p className="helper" style={{ fontSize: 12 }}>{s.train_rows.toLocaleString()} training rows ({s.events.train.toLocaleString()} events); validation: {s.val_from}, {s.val_rows.toLocaleString()} rows; the serving model is {s.chosen_by}. Forest: {s.n_trees} trees, √d features per split. {s.dropped_categories.length ? `Left out, too many categories: ${s.dropped_categories.join(', ')}. ` : ''}Cox assumes the hazard ratios stay the same over time (proportional hazards); the forest does not.</p>
        </details></div>
      </div>
    </div>
  )
}

/** Registry → Survival: the version's run results (same view as the run page). */
export function SurvVersionTab({ runId }: { runId: string }) {
  const r = useFetch(() => flows.surv.get(runId), [runId])
  if (r.err) return <ErrorCard err={r.err} />
  if (!r.data?.survival) return <Loading>Loading the survival checks…</Loading>
  return <SurvResults run={r.data} />
}

// ----------------------------------------------------------------------------------------------- Registry · Try It

/** Try It for a survival version: one subject's inputs → its survival curve against the average holdout subject's. */
export function SurvTryIt({ v }: { v: ModelVersion }) {
  const r = useFetch(() => flows.surv.get(v.run_id), [v.run_id])
  const [row, setRow] = useState<Record<string, unknown> | null>(null)
  const [out, setOut] = useState<SurvTry | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const s = r.data?.survival
  useEffect(() => { if (s && !row) setRow(s.examples[Math.min(3, s.examples.length - 1)]) }, [s, row])
  if (r.err) return <ErrorCard err={r.err} />
  if (!s || !row) return <Loading>Loading the inputs…</Loading>
  const predict = async (x = row) => {
    setBusy(true); setErr('')
    try { setOut(await flows.surv.tryIt(v.id, x)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const names = ['lowest risk', 'lower', 'middle', 'higher', 'highest risk']
  const tmax = s.grid[s.grid.length - 1]
  return (
    <div className="card" data-testid="surv-try">
      <div className="card-head"><div className="left"><h3 className="card-title">Try It · One Subject's Survival Curve</h3>
        <span className="helper" style={{ fontSize: 12 }}>start from a holdout subject:</span>
        {s.examples.map((x, i) => <button key={i} className="btn sm" onClick={() => { setRow(x); predict(x) }}>{names[i] ?? `#${i + 1}`}</button>)}</div>
        <button className="btn primary" disabled={busy} onClick={() => predict()} data-testid="surv-try-predict">{busy ? 'Scoring…' : 'Predict curve'}</button></div>
      <div className="card-body stack" style={{ gap: 'var(--space-12)' }}>
        <div className="tp-form" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 8 }}>
          {s.inputs.map((c) => (
            <label key={c} className="stack" style={{ gap: 2 }}><span className="micro-label">{c}</span>
              <input className="input mono" value={row[c] == null ? '' : String(row[c])} onChange={(e) => setRow({ ...row, [c]: s.numeric.includes(c) ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value })} /></label>
          ))}
        </div>
        {err && <p className="helper" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</p>}
        {out && <>
          <div className="card navy explain-hero" style={{ margin: 0 }} data-testid="surv-try-hero">
            {out.at.map((a) => <HeroCell key={a.t} label={`Ŝ(${a.t})`} sub={`P(${s.event_column} by ${a.t} ${u(s.unit)}) = ${pct(a.event_by)}`}>{pct(a.survival)}</HeroCell>)}
            <HeroCell label="risk group" sub={`r̂ = ${fix(out.risk, 3)}`}>{out.risk_group}</HeroCell>
            <HeroCell label="median time" sub={out.median_time == null ? `Ŝ stays above 0.5 to ${fix(tmax, 0)}` : `Ŝ(t) ≤ 0.5 from here`}>{out.median_time == null ? `> ${fix(tmax, 0)}` : fix(out.median_time, 0)}</HeroCell>
            {out.hr_vs_average != null && <HeroCell label="hazard vs average" sub="exp(Σ β_j (z_j − z̄_j))">{`× ${fix(out.hr_vs_average, 2)}`}</HeroCell>}
          </div>
          <PlotFrame title="Survival Curve" caption={<>Ŝ(t | x) from the {MODEL[out.served]}; dashed: the average holdout subject's</>}
            legend={[{ name: 'this subject', color: 'var(--accent-primary)', data: [] }, { name: 'average subject', color: 'var(--plot-axis)', dash: '5 4', data: [] }]}>
            <XYPlot testId="surv-try-curve" height={240}
              series={[{ name: 'this subject', color: 'var(--accent-primary)', width: 2.5, points: out.grid.map((t, i) => [t, out.curve[i]] as [number, number]) },
                { name: 'average', color: 'var(--plot-axis)', dash: '5 4', points: out.grid.map((t, i) => [t, out.baseline_curve[i]] as [number, number]) }]}
              x={{ min: 0, max: tmax, ticks: niceTicks(0, tmax, 6), title: `t (${u(s.unit)})`, fmt: (x) => x.toFixed(0) }}
              y={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'Ŝ(t)', fmt: (x) => x.toFixed(2) }}
              refs={out.horizons.map((h) => ({ x: h, label: `τ = ${h}`, dash: '3 3' }))} />
          </PlotFrame>
          {out.contributions && <div className="table-wrap"><table className="table compact" data-testid="surv-try-contrib">
            <thead><tr><th>input</th><th className="r">value</th><th className="r">× hazard</th><th>what it does</th></tr></thead>
            <tbody>{out.contributions.slice(0, 6).map((c) => (
              <tr key={c.feature}><td className="mono">{c.feature}</td><td className="r mono">{String(c.value ?? '—')}</td><td className={`r mono ${c.hr > 1 ? 'el-bad' : 'el-ok'}`}>{fix(c.hr, 2)}</td>
                <td className="helper" style={{ fontSize: 12 }}>{c.hr > 1 ? 'raises' : 'lowers'} the hazard against the average subject</td></tr>))}
            </tbody>
          </table></div>}
          <p className="helper" style={{ fontSize: 12 }}>{out.contributions ? 'Each factor is exp(β_j (z_j − z̄_j)); their product is the hazard against the average subject. ' : 'The forest has no per-input factors; the curve is the whole answer. '}The same numbers come from <span className="mono">POST /api/registry/versions/{v.id}/score</span> and batch scoring (columns risk_score, risk_group, S_τ, median_time). {out.predict_ms.toFixed(0)} ms.</p>
        </>}
      </div>
    </div>
  )
}
