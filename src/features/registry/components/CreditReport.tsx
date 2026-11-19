import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { CreditReport as Report, ModelVersion } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, type XYSeries } from '@/shared/components/XYPlot'
import { HeroCell } from '@/shared/components/Status'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { Segmented } from '@/shared/primitives/Segmented'
import { useFetch } from '@/shared/hooks/useFetch'
import { fix, pct } from '@/shared/lib/format'
import { BAND_COLOR } from '../lib'

const COLORS = ['var(--viz-a)', 'var(--viz-b)', 'var(--viz-c)', 'var(--viz-d)', 'var(--viz-e)']
const ci = (c: [number, number] | null | undefined, d = 3) => (c ? `[${fix(c[0], d)}, ${fix(c[1], d)}]` : '—')
const csiBand = (v: number | null, r: Report) => (v == null ? 'missing' : v > r.stability.csi.shift ? 'shift' : v > r.stability.csi.watch ? 'watch' : 'stable')
const POP = { holdout: 'Holdout', batches: 'Labelled batches' } as const
type Pop = (typeof POP)[keyof typeof POP]

/** G2 · Registry → Credit Report: the standard credit model report of a binary scoring version: rank ordering
 *  overall and by month, score bands, deciles, score PSI and CSI per feature per month, vintage curves, and the
 *  downloadable "Model Performance Report". Every number comes from GET /registry/versions/{id}/credit-report. */
export function CreditReport({ v, actor }: { v: ModelVersion; actor: string }) {
  const [on, setOn] = useState<'holdout' | 'batches'>('holdout')
  const [dateCol, setDateCol] = useState('')
  const { data: r, err, reload } = useFetch(() => api.registry.creditReport(v.id, { on, date_column: dateCol }), [v.id, on, dateCol])
  if (err && !r) return <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>
  if (!r) return <div className="card card-body row"><ResonanceWave /> Building the report from the holdout and the scored batches…</div>
  const h = r.overall.holdout
  const ff = r.stability.first_flag
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="credit-report">
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Credit Model Report</h3><span className="helper" style={{ fontSize: 12 }}>rank ordering, score bands, stability and vintages of {r.label}; bad = <b>{r.positive_class}</b></span></div>
          <div className="row" style={{ gap: 6 }}>
            <a className="btn" href={api.registry.creditReportUrl(v.id, 'md')} download data-testid="credit-md">Markdown</a>
            <a className="btn primary" href={api.registry.creditReportUrl(v.id, 'html')} download data-testid="credit-html">Download report</a>
          </div>
        </div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="credit-hero">
          <HeroCell label="Gini · holdout" sub={`95% CI ${ci(h.gini_ci)}`}>{fix(h.gini)}</HeroCell>
          <HeroCell label="KS · holdout" sub={`95% CI ${ci(h.ks_ci)}`}>{fix(h.ks)}</HeroCell>
          <HeroCell label="bad rate" sub={`${h.bads.toLocaleString()} of ${h.n.toLocaleString()}`}>{pct(h.bad_rate)}</HeroCell>
          <HeroCell label="labelled months" sub={r.pending.length ? `${r.pending.length} awaiting labels` : 'all labelled'}>{String(r.headline.months)}</HeroCell>
          <HeroCell label="first CSI flag" sub={ff ? `${ff.feature}${ff.stays_flagged ? ', every month after' : ''}` : 'no feature > 0.25'}>{ff?.period ?? 'none'}</HeroCell>
          <HeroCell label="vintage cohorts" sub={r.vintage ? `by ${r.vintage.cohort_by}, as of ${r.vintage.as_of}` : 'no performance tape'}>{String(r.headline.cohorts)}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
            <div className="tp-eq" style={{ fontSize: 14 }}>Gini = 2·AUC − 1 &nbsp;·&nbsp; KS = maxₛ |F<sub>bad</sub>(s) − F<sub>good</sub>(s)| &nbsp;·&nbsp; Score = {r.bands.scale.offset.toFixed(1)} + {r.bands.scale.factor.toFixed(2)}·ln((1 − p̂)/p̂)</div>
          </div>
          <p className="helper" style={{ fontSize: 12.5 }}>
            p̂ = P({r.positive_class} | x) from the served model; AUC is the chance a random bad scores riskier than a random good, with DeLong's 95 % interval (mapped to Gini the same way). F<sub>bad</sub>, F<sub>good</sub> are the cumulative shares of bads and goods from the riskiest score; KS's interval is a bootstrap of rows.
            Score is the scorecard scale: {r.bands.scale.base_score} points at good:bad odds {r.bands.scale.base_odds}:1, and every {r.bands.scale.pdo} points (PDO) double the odds. The threshold t = {fix(r.threshold, 4)} sits at ≈ {fix(r.bands.threshold_score, 0)} points.
          </p>
          <Inputs r={r} v={v} actor={actor} dateCol={dateCol} setDateCol={setDateCol} onTape={reload} />
        </div>
      </div>

      <GiniByMonth r={r} />
      <BandsCard r={r} on={on} setOn={setOn} />
      <StabilityCard r={r} />
      <VintageCard r={r} />
    </div>
  )
}

function Inputs({ r, v, actor, dateCol, setDateCol, onTape }: { r: Report; v: ModelVersion; actor: string; dateCol: string; setDateCol: (c: string) => void; onTape: () => void }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const d = r.detected
  const src = r.vintage?.source
  const upload = async (f: File | undefined) => {
    if (!f) return
    setBusy(true); setMsg('')
    try { const x = await api.registry.uploadCreditTape(v.id, f, actor); setMsg(`✓ ${x.tape.rows.toLocaleString()} loans, ${x.cohorts} cohorts`); onTape() } catch (e) { setMsg(`✕ ${(e as Error).message}`) } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true); setMsg('')
    try { await api.registry.removeCreditTape(v.id, actor); onTape() } catch (e) { setMsg(`✕ ${(e as Error).message}`) } finally { setBusy(false) }
  }
  return (
    <details className="an-adv" data-testid="credit-inputs">
      <summary className="micro-label">Inputs · month from <b className="mono">{d.date?.column ?? '— (by batch)'}</b>, id <b className="mono">{d.id?.column ?? '—'}</b>, vintages from {src ? (src.kind === 'sample' ? 'the sample tape' : `${src.file}`) : 'no tape'} · detected from the column roles, change below</summary>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
          <tbody>
            <tr><td>month of a holdout row</td>
              <td><select className="input" value={dateCol || d.date?.column || ''} data-testid="credit-date" onChange={(e) => setDateCol(e.target.value)}>
                {!d.date && <option value="">none</option>}
                {d.date_candidates.map((c) => <option key={c} value={c}>{c}</option>)}</select></td>
              <td className="helper" style={{ fontSize: 12 }}>{d.date ? `date role: ${d.date.evidence.join(', ')}` : 'no date role: holdout is one period'}; batches use their data date</td></tr>
            <tr><td>loan id</td><td className="mono">{d.id?.column ?? '—'}</td><td className="helper" style={{ fontSize: 12 }}>{d.id ? `row ID role: ${d.id.evidence.join(', ')}` : '—'}</td></tr>
            <tr><td>performance tape</td><td className="mono">{src ? `${src.mapping.open} · ${src.mapping.default}${src.mapping.until ? ` · ${src.mapping.until}` : ''}` : '—'}</td>
              <td className="helper" style={{ fontSize: 12 }}>{src ? (src.kind === 'sample' ? `${src.note}; ${src.filtered_to}` : `uploaded by ${src.by}`) : 'upload one row per loan: an origination date and a default date (blank = none), optionally an as-of date'}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="row" style={{ gap: 8, justifyContent: 'flex-end', padding: 'var(--space-8) 0' }}>
        {msg && <span className="helper" style={{ fontSize: 12 }} role="status">{msg}</span>}
        {busy && <ResonanceWave width={40} height={12} />}
        {src?.kind === 'uploaded' && <button className="btn sm" disabled={busy || !actor} onClick={remove}>Remove the tape</button>}
        <label className={`btn sm ${busy || !actor ? 'disabled' : ''}`} title={actor ? '' : 'Set your name at the top of the registry first'}>
          Upload a tape…<input type="file" accept=".csv" hidden disabled={busy || !actor} data-testid="credit-tape" onChange={(e) => upload(e.target.files?.[0])} />
        </label>
      </div>
    </details>
  )
}

function GiniByMonth({ r }: { r: Report }) {
  const ps = r.periods.filter((p) => p.gini != null)
  const all = [...ps.map((p) => p.period), ...r.pending.map((p) => p.period)]
  const ix = (per: string) => all.indexOf(per)
  const lo = Math.max(0, Math.min(...ps.map((p) => p.gini_ci![0])) - 0.05)
  const hi = Math.min(1, Math.max(...ps.map((p) => p.gini_ci![1])) + 0.05)
  const ser = (src: 'holdout' | 'batch', name: string, color: string): XYSeries => {
    const pts = ps.filter((p) => p.source === src)
    return { name, color, dots: true, points: pts.map((p) => [ix(p.period), p.gini!] as [number, number]), tip: (k) => `${pts[k].period} · CI ${ci(pts[k].gini_ci)} · KS ${fix(pts[k].ks)} · n ${pts[k].n.toLocaleString()}, ${pts[k].bads} bads` }
  }
  const g0 = r.overall.holdout.gini
  return (
    <PlotFrame title="Gini By Month" legend={[{ name: 'holdout months', color: 'var(--text-muted)', data: [] }, { name: 'scored batches (labelled)', color: 'var(--viz-a)', data: [] }]}
      chips={g0 != null ? <ParamChip sym="Gini₀" value={fix(g0)} /> : undefined}
      caption={<span>Each point is one month's Gini with its 95 % DeLong interval. Holdout months come from the training period; batch months from the labelled scored batches (3 months after scoring for the sample stream). ○: scored, labels not in yet.</span>}>
      <XYPlot height={230} testId="credit-gini" x={{ min: -0.5, max: all.length - 0.5, ticks: all.map((_, i) => i), title: 'month', fmt: (x) => all[Math.round(x)]?.slice(2) ?? '' }}
        y={{ min: lo, max: hi, ticks: niceTicks(lo, hi, 4), title: 'Gini', fmt: (v) => v.toFixed(2) }}
        refs={g0 != null ? [{ y: g0, label: 'Gini₀', dash: '4 3' }] : []}
        series={[ser('holdout', 'holdout', 'var(--text-muted)'), ser('batch', 'batches', 'var(--viz-a)')]}>
        {(sx, sy) => <g>
          {ps.map((p) => <line key={p.period + p.source} x1={sx(ix(p.period))} x2={sx(ix(p.period))} y1={sy(p.gini_ci![0])} y2={sy(p.gini_ci![1])} stroke={p.source === 'batch' ? 'var(--viz-a)' : 'var(--text-muted)'} strokeWidth={1.5} opacity={0.6} />)}
          {r.pending.map((p) => <circle key={p.batch_id} cx={sx(ix(p.period))} cy={sy(lo) - 6} r={3.5} fill="none" stroke="var(--text-muted)" />)}
        </g>}
      </XYPlot>
      <details className="an-adv" style={{ padding: '0 16px 8px' }}>
        <summary className="micro-label">the table · {ps.length} months</summary>
        <div className="table-wrap"><table className="table compact" data-testid="credit-periods">
          <thead><tr><th>month</th><th>from</th><th className="r">n</th><th className="r">bads</th><th className="r">bad rate</th><th className="r">Gini</th><th>95% CI</th><th className="r">KS</th></tr></thead>
          <tbody>{r.periods.map((p) => <tr key={p.period + p.source}><td className="mono">{p.period}</td><td className="muted">{p.source}</td><td className="r mono">{p.n.toLocaleString()}</td><td className="r mono">{p.bads}</td><td className="r mono">{pct(p.bad_rate)}</td><td className="r mono">{fix(p.gini)}</td><td className="mono">{ci(p.gini_ci)}</td><td className="r mono">{fix(p.ks)}</td></tr>)}
            {r.pending.map((p) => <tr key={p.batch_id}><td className="mono">{p.period}</td><td className="muted">batch</td><td className="r mono">{p.n.toLocaleString()}</td><td colSpan={5} className="helper">labels not in yet</td></tr>)}</tbody>
        </table></div>
      </details>
    </PlotFrame>
  )
}

function BandsCard({ r, on, setOn }: { r: Report; on: 'holdout' | 'batches'; setOn: (o: 'holdout' | 'batches') => void }) {
  const b = r.bands
  const dec = r.deciles
  const maxY = Math.max(...dec.map((d) => d.bad_rate_ci?.[1] ?? d.bad_rate ?? 0), ...dec.map((d) => d.mean_p ?? 0)) * 1.1
  return (
    <div className="card" data-testid="credit-bands">
      <div className="card-head">
        <div className="left"><h3 className="card-title">Score Bands</h3><span className="helper" style={{ fontSize: 12 }}>{b.n.toLocaleString()} rows, {pct(b.bad_rate)} bad · bands {b.scale.pdo} points wide, riskiest first</span></div>
        {r.overall.batches && <Segmented<Pop> options={[POP.holdout, POP.batches]} value={POP[on]} onChange={(x) => setOn(x === POP.batches ? 'batches' : 'holdout')} />}
      </div>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>score</th><th className="r">n</th><th className="r">share</th><th className="r">bads</th><th className="r">bad rate</th><th className="r">mean p̂</th><th className="r">cum. capture</th><th className="r">cum. share</th><th className="r">lift</th><th className="r">cum. lift</th><th>at t</th></tr></thead>
        <tbody>{b.rows.map((x) => <tr key={x.band}><td className="mono">{x.band}</td><td className="r mono">{x.n.toLocaleString()}</td><td className="r mono">{pct(x.share)}</td><td className="r mono">{x.bads}</td><td className="r mono" title={`Wilson 95% ${ci(x.bad_rate_ci)}`}>{pct(x.bad_rate)}</td><td className="r mono">{fix(x.mean_p)}</td><td className="r mono">{pct(x.cum_capture)}</td><td className="r mono">{pct(x.cum_share)}</td><td className="r mono">{fix(x.lift, 2)}</td><td className="r mono">{fix(x.cum_lift, 2)}</td><td className="muted">{x.decision ?? '—'}</td></tr>)}</tbody>
      </table></div>
      <div className="caption" style={{ padding: '8px 16px' }}><span className="helper" style={{ fontSize: 12 }}>capture<sub>b</sub> = the share of all bads in band b and the riskier bands; lift<sub>b</sub> = bad rate<sub>b</sub> / overall bad rate; cum. lift = capture / cum. share. "at t": whether the band's rows are declined (p̂ ≥ t), approved, or split by the threshold.</span></div>
      <div style={{ padding: '0 16px 16px' }}>
        <PlotFrame title="Bad Rate By Score Decile" legend={[{ name: 'observed bad rate (Wilson 95 %)', color: 'var(--viz-d)', data: [] }, { name: 'mean p̂', color: 'var(--viz-a)', data: [] }]}
          caption={<span>Ten equal-count groups by p̂, riskiest (1) to safest (10). Rank ordering holds when the bars fall from left to right; calibration when the line meets the bars.</span>}>
          <XYPlot height={210} testId="credit-deciles" x={{ min: 0.5, max: 10.5, ticks: dec.map((d) => d.decile), title: 'decile', fmt: (x) => String(Math.round(x)) }}
            y={{ min: 0, max: maxY, ticks: niceTicks(0, maxY, 4), title: 'bad rate', fmt: (v) => v.toFixed(2) }}
            bars={dec.map((d) => ({ x0: d.decile - 0.35, x1: d.decile + 0.35, y: d.bad_rate ?? 0, color: 'var(--viz-d)', opacity: 0.55 }))}
            series={[{ name: 'mean p̂', color: 'var(--viz-a)', dots: true, points: dec.map((d) => [d.decile, d.mean_p ?? 0] as [number, number]), tip: (k) => `observed ${pct(dec[k].bad_rate)} ${ci(dec[k].bad_rate_ci)} · lift ${fix(dec[k].lift, 2)} · n ${dec[k].n}` }]}>
            {(sx, sy) => <g>{dec.map((d) => d.bad_rate_ci && <line key={d.decile} x1={sx(d.decile)} x2={sx(d.decile)} y1={sy(d.bad_rate_ci[0])} y2={sy(d.bad_rate_ci[1])} stroke="var(--viz-d)" strokeWidth={1.5} />)}</g>}
          </XYPlot>
        </PlotFrame>
      </div>
    </div>
  )
}

function StabilityCard({ r }: { r: Report }) {
  const st = r.stability
  const cs = st.csi
  if (!cs.periods.length) return <div className="card card-body helper">Stability: no scored batch yet. Score a file in Batch &amp; Drift (or feed the sample stream in Monitoring); each batch adds a month of PSI and CSI.</div>
  const cell = (val: number | null, band: string, key: string) => <td key={key} className="r mono" style={{ background: band === 'stable' || band === 'missing' ? undefined : `color-mix(in srgb, ${BAND_COLOR[band]} ${band === 'shift' ? 28 : 16}%, transparent)` }}>{val == null ? '—' : val.toFixed(2)}</td>
  const fb = st.flag_bins
  return (
    <div className="card" data-testid="credit-stability">
      <div className="card-head"><div className="left"><h3 className="card-title">Stability · PSI And CSI By Month</h3><span className="helper" style={{ fontSize: 12 }}>Σᵢ (aᵢ − eᵢ)·ln(aᵢ/eᵢ) per month · shaded: watch &gt; {cs.watch}, shift &gt; {cs.shift}</span></div>
        <div className="row"><ParamChip sym="θ" value={String(cs.shift)} /></div></div>
      <div className="table-wrap"><table className="table compact" data-testid="credit-csi">
        <thead><tr><th>characteristic</th>{cs.periods.map((p) => <th key={p} className="r mono">{p.slice(2)}</th>)}<th>first shift</th></tr></thead>
        <tbody>
          <tr><td><b>score p̂</b> <span className="helper">(PSI)</span></td>{st.score_psi.map((x, i) => cell(x.psi, csiBand(x.psi, r), `s${i}`))}<td className="muted">vs holdout p̂</td></tr>
          {cs.features.map((f) => <tr key={f.feature}><td className="mono">{f.feature}</td>{f.csi.map((x, i) => cell(x, csiBand(x, r), `${f.feature}${i}`))}<td className="mono">{f.first_shift ?? '—'}</td></tr>)}
        </tbody>
      </table></div>
      <div className="caption" style={{ padding: '8px 16px' }}><span className="helper" style={{ fontSize: 12 }}>
        CSI is PSI on one characteristic: eᵢ is the training share of bin i (training deciles, or categories), aᵢ the month's share. {st.reference}.
        {st.first_flag && <> <b>{st.first_flag.feature}</b> first crosses {cs.shift} in {st.first_flag.period}{st.first_flag.stays_flagged ? ' and stays above it' : ''}.</>}
      </span></div>
      {fb && <details className="an-adv" style={{ padding: '0 16px 8px' }}>
        <summary className="micro-label">why {fb.feature} is flagged in {fb.period}: CSI {fb.csi.toFixed(3)} bin by bin</summary>
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>bin</th><th className="r">training eᵢ</th><th className="r">{fb.period} aᵢ</th><th className="r">(aᵢ − eᵢ)·ln(aᵢ/eᵢ)</th></tr></thead>
          <tbody>{fb.bins.map((x) => <tr key={x.label}><td className="mono">{x.label}</td><td className="r mono">{pct(x.expected)}</td><td className="r mono">{pct(x.actual)}</td><td className="r mono">{x.term.toFixed(3)}</td></tr>)}</tbody>
        </table></div>
      </details>}
    </div>
  )
}

function VintageCard({ r }: { r: Report }) {
  const vin = r.vintage
  if (!vin || !vin.cohorts.length) return <div className="card card-body helper" data-testid="credit-vintage">Vintage curves need a performance tape: one row per loan with an origination date and a default date. Upload one under Inputs above.</div>
  const n = vin.cohorts.length
  const maxX = Math.max(...vin.cohorts.map((c) => c.observed_mob))
  const maxY = Math.max(...vin.cohorts.flatMap((c) => c.curve.map((x) => x.cum_bad_rate))) * 1.1
  const series: XYSeries[] = vin.cohorts.map((c, i) => ({
    name: c.cohort, color: COLORS[(n - 1 - i) % COLORS.length], dash: n - 1 - i >= COLORS.length ? '4 3' : undefined, width: i === n - 1 ? 2 : 1.5,
    points: c.curve.map((x) => [x.mob, x.cum_bad_rate] as [number, number]), tip: (k) => `${c.cohort} · MOB ${c.curve[k].mob}: ${c.curve[k].defaults} of ${c.n.toLocaleString()} defaulted`,
  }))
  return (
    <PlotFrame title="Vintage Curves" legend={series.map((s) => ({ name: s.name, color: s.color, dash: s.dash, data: [] }))}
      chips={<><ParamChip sym="as of" value={vin.as_of ?? '—'} /><ParamChip sym="loans" value={vin.loans.toLocaleString()} /></>}
      caption={<span>V<sub>c</sub>(k) = #{'{'}loans of cohort c in default by k months on book{'}'} / n<sub>c</sub>, by origination {vin.cohort_by}. A curve stops at the last month every loan of its cohort has been watched. A cohort above the older ones at the same age is worse business, seen long before its loans mature.</span>}>
      <XYPlot height={260} testId="credit-vintage" x={{ min: 0, max: maxX, ticks: niceTicks(0, maxX, 8), title: 'months on book', fmt: (x) => String(Math.round(x)) }}
        y={{ min: 0, max: maxY, ticks: niceTicks(0, maxY, 4), title: 'cumulative bad rate', fmt: (v) => pct(v, 0) }} series={series} />
    </PlotFrame>
  )
}
