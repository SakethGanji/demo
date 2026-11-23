import { useEffect, useState } from 'react'
import { flows, type SurvGroup, type SurvResult, type SurvRun, type SurvTry } from '@/shared/api/flows'
import type { ModelVersion } from '@/shared/api/types'
import { CiBar, KindFlow, NumField, type KindSpec } from '@/shared/components/KindFlow'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ErrorCard, HeroCell, Loading } from '@/shared/components/Status'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { useFetch } from '@/shared/hooks/useFetch'
import { fix, pct } from '@/shared/lib/format'

const MODEL = { cox: 'Cox PH', rsf: 'random survival forest' } as const
const GROUP_COLOR: Record<string, string> = { low: 'var(--viz-a)', 'medium-low': 'var(--viz-b)', 'medium-high': 'var(--viz-c)', high: 'var(--status-danger)' }
const u = (unit: string | null | undefined) => (unit ? `${unit}s` : 'time units')
const horizonsOf = (hz: string) => hz.split(/[,\s]+/).map(Number).filter((x) => Number.isFinite(x) && x > 0)

function Formula() {
  return (
    <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
      <div className="tp-eq" style={{ fontSize: 14 }} data-testid="surv-formula">
        S(t | x) = P(T &gt; t | x) &nbsp;·&nbsp; Cox: h(t | x) = h<sub>0</sub>(t)·exp(β<sup>T</sup>z), S(t | x) = S<sub>0</sub>(t)<sup>exp(β<sup>T</sup>z)</sup> &nbsp;·&nbsp; C = P(r̂<sub>i</sub> &gt; r̂<sub>j</sub> | T<sub>i</sub> &lt; T<sub>j</sub>, δ<sub>i</sub> = 1)
      </div>
    </div>
  )
}

/**
 * G6 · Time to event, a choice inside Build: the setup, pre-filled from the G0 roles (duration, event, horizons, what to leave
 * out, the split). The run (Models): Cox PH and a random survival forest on the same holdout: Harrell's C, AUC(τ), calibration
 * against Kaplan–Meier by risk group, hazard ratios. Registry → Results shows the same view; Try It draws one subject's curve.
 */
export const SURV_SPEC: KindSpec = {
  id: 'survival', title: 'Time to Event', testId: 'surv',
  sub: 'How long until an event (an account closes, a loan prepays), using every row, censored ones included.',
  detect: flows.surv.detect, create: (b) => flows.surv.create(b as never),
  roles: [
    { key: 'duration_column', label: 'time T', urlParam: 'duration', testId: 'surv-duration', emptyHint: 'nothing detected: pick the time each row was followed',
      options: (ds) => ds.profile.columns.filter((c) => c.type === 'number' && c.unique > 5).map((c) => c.name) },
    { key: 'event_column', label: 'event δ', urlParam: 'event', testId: 'surv-event', emptyHint: 'nothing detected: pick the 0/1 column (1 = the event happened, 0 = censored)',
      options: (ds) => ds.profile.columns.filter((c) => c.unique === 2).map((c) => c.name) },
  ],
  rows: ({ det, o, set }) => (
    <tr><td>horizons τ</td>
      <td><input className="input mono" value={o.hz} onChange={(e) => set({ hz: e.target.value })} style={{ width: 140 }} data-testid="surv-horizons" /></td>
      <td className="helper" style={{ fontSize: 12 }}>Ŝ(τ) is reported at these {u(det.unit)}{det.duration_range ? ` (T runs ${det.duration_range[0]}–${det.duration_range[1]})` : ''}</td></tr>),
  leftOut: ({ d }) => <>IDs, dates, leaks · {d.split_why}</>,
  Formula: () => <Formula />,
  note: ({ det }) => !det.strong && det.duration_column && <p className="helper el-bad" style={{ fontSize: 12.5 }}>● '{det.duration_column}' reads like time so far (a snapshot), not necessarily time up to '{det.event_column}'. Only train if each row's time ends at its event or at censoring.</p>,
  advanced: { initial: (det) => ({ trees: 200, hz: det.horizons.join(', ') }), summary: (o) => `${o.trees} trees`,
    Body: ({ o, set }) => <><NumField label="trees in the forest" value={o.trees} set={(trees) => set({ trees })} min={20} max={1000} testId="surv-trees" /><span className="helper" style={{ fontSize: 12 }}>Cox PH has nothing to tune (a tiny ridge, α = 0.001, keeps it stable).</span></> },
  explain: () => <>A censored row (δ = 0) still counts: we know it lasted at least T. Both models train on the same rows; the one with the lower integrated Brier score on a validation slice serves, so the holdout stays untouched until the comparison.</>,
  startLabel: () => 'Train Cox + survival forest →',
  canStart: ({ d, o }) => !!(d.duration_column && d.event_column && horizonsOf(o.hz).length),
  body: ({ d, o }) => ({ duration_column: d.duration_column, event_column: d.event_column, horizons: horizonsOf(o.hz),
    drop_columns: d.drop_columns.filter((c: string) => c !== d.duration_column && c !== d.event_column), n_estimators: o.trees }),
  running: { events: 'surv', line: () => 'Fitting both models, then the holdout checks', steps: () => (['cox', 'rsf'] as const).map((m) => ({ key: m, label: <b>{MODEL[m]}</b> })) },
  meta: (run) => `T = ${run.config.duration_column} · δ = ${run.config.event_column}`,
  registerName: (run) => `${run.dataset_id}.survival`,
}

export const SurvivalPage = ({ go }: { go: (r: string) => void }) => <KindFlow spec={SURV_SPEC} go={go} />

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
