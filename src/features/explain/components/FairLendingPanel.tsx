import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { FairLending, FairLendingGroup } from '@/shared/api/types'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { HeroCell } from '@/shared/components/Status'
import { useFetch } from '@/shared/hooks/useFetch'
import { useWidth } from '@/shared/hooks/useWidth'
import { fix, pct } from '@/shared/lib/format'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { ResonanceWave } from '@/shared/primitives/ResonanceWave'
import { LABELS } from './FairnessPanel'

const ci = (c: [number, number] | null | undefined, d = 3) => (c ? `[${fix(c[0], d)}, ${fix(c[1], d)}]` : '—')
const signed = (v: number | null | undefined, d = 3) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)}`)

/** G4 · Fair-lending check without protected attributes. BISG turns surname × zip into each applicant's group
 *  probabilities; approval rates, adverse impact ratios and F4's metrics follow on the probability-weighted groups.
 *  Used by Explain → Fairness (estimated groups) and Registry → Fair Lending. Every number is GET /runs/{id}/fair-lending. */
export function FairLendingPanel({ runId }: { runId: string }) {
  const [q, setQ] = useState<{ surname?: string; zip?: string; truth?: string }>({})
  const { data: r, err } = useFetch(() => api.runs.fairLending(runId, q), [runId, q.surname, q.zip, q.truth])
  if (err && !r) return <div className="card card-body" role="alert" style={{ color: 'var(--status-danger)' }}>✕ {err}</div>
  if (!r) return <div className="card card-body row"><ResonanceWave /> Estimating group probabilities from surname × zip…</div>
  if (!r.ready) return <div className="card card-body stack" data-testid="fl-not-ready"><p className="helper">{r.reason}</p><Inputs r={r} q={q} setQ={setQ} open /></div>
  const lab = (g: string) => r.group_labels[g] ?? g
  const ref = lab(r.reference)
  const judged = r.air.filter((g) => !g.reference && g.judged && g.air != null)
  const worst = judged.slice().sort((a, b) => a.air! - b.air!)[0]
  const below = judged.filter((g) => g.air! < 0.8)
  const synthetic = r.tables.source === 'synthetic'
  const pq = r.proxy
  return (
    <div className="stack" style={{ gap: 'var(--space-16)' }} data-testid="fair-lending">
      <div className="card">
        <div className="card-head">
          <div className="left"><h3 className="card-title">Fair-Lending Check (BISG)</h3>
            <span className="helper" style={{ fontSize: 12 }}>approval rates by estimated race/ethnicity, without recording it · approved when {r.favourable.approved_when} at t = {fix(r.threshold, 3)}</span></div>
          <span className={`pill ${synthetic ? 'warning' : ''}`} title={r.tables.label} data-testid="fl-tables">{synthetic ? 'Synthetic stand-in tables' : 'Census tables'}</span>
        </div>
        <div className="card navy explain-hero" style={{ margin: 0, borderRadius: 0 }} data-testid="fl-hero">
          <HeroCell label="lowest AIR" sub={worst ? `${lab(worst.group)} · 95% CI ${ci(worst.air_ci)}` : 'no group to judge'}>{fix(worst?.air)}</HeroCell>
          <HeroCell label="approval gap" sub={worst ? `${lab(worst.group)} − ${ref} · ${ci(worst.diff_ci)}` : '—'}>{worst ? signed(worst.diff) : '—'}</HeroCell>
          <HeroCell label="groups below 0.80" sub={below.length ? below.map((g) => lab(g.group)).join(', ') : 'four-fifths rule met'} subClass={below.length ? 'warn' : ''}>{String(below.length)}</HeroCell>
          <HeroCell label={pq.accuracy != null ? 'proxy accuracy' : 'proxy accuracy (expected)'} sub={pq.accuracy != null ? `vs true group in ${r.truth?.column}` : 'mean max p: no true group to check'}>{pct(pq.accuracy ?? pq.expected_accuracy)}</HeroCell>
          {r.truth?.max_abs_delta != null && <HeroCell label="max |AIR − true AIR|" sub={r.truth.within_005 ? 'within ±0.05' : 'outside ±0.05'} subClass={r.truth.within_005 ? '' : 'warn'}>{fix(r.truth.max_abs_delta)}</HeroCell>}
          <HeroCell label="applicants" sub={`holdout · approval ${pct(r.favourable.approval_rate)}`}>{r.n.toLocaleString()}</HeroCell>
        </div>
        <div className="card-body stack" style={{ gap: 'var(--space-8)' }}>
          <div className="navy-well" style={{ padding: 'var(--space-12) var(--space-16)', minHeight: 0 }}>
            <div className="tp-eq" style={{ fontSize: 14 }}>p<sub>ir</sub> = P(r | s<sub>i</sub>, g<sub>i</sub>) = P(r | s<sub>i</sub>)·P(g<sub>i</sub> | r) / Σ<sub>r′</sub> P(r′ | s<sub>i</sub>)·P(g<sub>i</sub> | r′)</div>
            <div className="tp-eq" style={{ fontSize: 14 }}>Q·A = b, &nbsp; Q<sub>rr′</sub> = Σ<sub>i</sub> p<sub>ir</sub>p<sub>ir′</sub>, &nbsp; b<sub>r</sub> = Σ<sub>i</sub> p<sub>ir</sub>a<sub>i</sub> &nbsp;·&nbsp; AIR<sub>r</sub> = A<sub>r</sub> / A<sub>{ref}</sub></div>
          </div>
          <p className="helper" style={{ fontSize: 12.5 }}>
            s<sub>i</sub> is applicant i's surname ({r.detected.surname?.column}), g<sub>i</sub> the zip ({r.detected.zip?.column ?? 'none: the surname alone'}), r one of {r.groups.length} groups.
            P(r | s) is the surname table's share of each group ({pct(r.coverage.surname)} of surnames found; others get the table's overall mix); P(g | r) is the share of group r living in zip g{r.coverage.zip != null ? ` (${pct(r.coverage.zip)} of zips found)` : ''}.
            a<sub>i</sub> = 1 when the model approves (p̂ ≤ t). A<sub>r</sub> is the approval rate of group r. The plain weighted rate Σp<sub>ir</sub>a<sub>i</sub>/Σp<sub>ir</sub> is pulled toward the other groups' rates (each group's weight includes people from other groups), so it understates gaps; solving Q·A = b removes that pull when the probabilities are calibrated and the decision depends on the group only through the applicant's own data.
            Intervals: {r.limits.bootstrap} bootstrap resamples of applicants. {ref} is the reference group.
          </p>
          <p className="helper" role="note" style={{ fontSize: 12.5, color: 'var(--status-warning)' }} data-testid="fl-proxy-note">
            ⚠ These groups are estimates, not recorded facts. {pq.accuracy != null
              ? `Against the true groups, the proxy's most likely group is right for ${pct(pq.accuracy)} of applicants (Brier ${fix(pq.brier)}); the AIRs differ from the true-group AIRs by at most ${fix(r.truth?.max_abs_delta)} (plain weighting: ${fix(r.truth?.max_abs_plain_delta)}).`
              : `If the probabilities are calibrated, the proxy's most likely group is right for about ${pct(pq.expected_accuracy)} of applicants.`}
            {synthetic && ' The surname and zip tables are a small synthetic stand-in, not Census data: these numbers show the method, not a real population.'}
          </p>
          <Inputs r={r} q={q} setQ={setQ} />
        </div>
      </div>
      <AirCard r={r} lab={lab} />
      <ProxyCard r={r} lab={lab} />
    </div>
  )
}

function Inputs({ r, q, setQ, open }: { r: FairLending; q: { surname?: string; zip?: string; truth?: string }; setQ: (q: { surname?: string; zip?: string; truth?: string }) => void; open?: boolean }) {
  const d = r.detected
  const row = (key: 'surname' | 'zip' | 'truth', what: string, none: string) => (
    <tr key={key}><td>{what}</td>
      <td><select className="input" aria-label={what} data-testid={`fl-${key}`} value={q[key] ?? d[key]?.column ?? ''}
        onChange={(e) => setQ({ ...q, [key]: e.target.value })}>
        <option value="">{none}</option>
        {r.candidates[key].map((c) => <option key={c} value={c}>{c}</option>)}</select></td>
      <td className="helper" style={{ fontSize: 12 }}>{d[key] ? `${d[key]!.source === 'detected' ? `${key === 'truth' ? 'protected' : key} role: ` : ''}${d[key]!.evidence.join(', ')}${d[key]!.note ? `; ${d[key]!.note}` : ''}` : '—'}</td></tr>
  )
  return (
    <details className="an-adv" open={open} data-testid="fl-inputs">
      <summary className="micro-label">Inputs · surname <b className="mono">{d.surname?.column ?? '—'}</b>, zip <b className="mono">{d.zip?.column ?? '—'}</b>, true group <b className="mono">{d.truth?.column ?? 'none'}</b> · detected from the column roles, change below</summary>
      <div className="table-wrap"><table className="table compact">
        <thead><tr><th>input</th><th>column</th><th>why</th></tr></thead>
        <tbody>
          {row('surname', 'surname', 'none')}
          {row('zip', 'zip code', 'none (surname only)')}
          {row('truth', 'true group (validation only)', 'none')}
        </tbody>
      </table></div>
      {r.ready && <p className="helper" style={{ fontSize: 12 }}>Tables: {r.tables.label}. To use the Census surname file and a zip population table, put <span className="mono">surnames.csv</span> and <span className="mono">zips.csv</span> in the data folder's <span className="mono">bisg/</span>.</p>}
    </details>
  )
}

function AirCard({ r, lab }: { r: FairLending; lab: (g: string) => string }) {
  const [ref, W] = useWidth<HTMLDivElement>()
  const rows = r.air.filter((g) => !g.reference)
  const truth = Object.fromEntries((r.truth?.comparison ?? []).map((c) => [c.group, c]))
  const LBL = 290, R = 24, ROW = 30
  const lo = Math.min(0.5, ...rows.map((g) => g.air_ci[0] ?? 1)), hi = Math.max(1.2, ...rows.map((g) => g.air_ci[1] ?? 1))
  const pw = Math.max(100, W - LBL - R)
  const sx = (v: number) => LBL + ((v - lo) / (hi - lo)) * pw
  const ticks = [0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2].filter((t) => t >= lo && t <= hi)
  const color = (g: FairLendingGroup) => (!g.judged ? 'var(--text-muted)' : g.air! < 0.8 ? 'var(--status-danger)' : g.air! < 0.9 ? 'var(--status-warning)' : 'var(--viz-a)')
  return (
    <>
      <PlotFrame title="Adverse Impact Ratio By Group" chips={<><ParamChip sym="ref" value={lab(r.reference)} /><ParamChip sym="n" value={r.n.toLocaleString()} /></>}
        legend={[{ name: 'AIR (corrected) with 95% CI', color: 'var(--viz-a)', data: [] }, { name: 'plain weighted', color: 'var(--text-muted)', dash: '2 3', data: [] }, ...(r.truth ? [{ name: 'true groups', color: 'var(--viz-d)', data: [] }] : [])]}
        caption={<span>AIR<sub>r</sub> = A<sub>r</sub> / A<sub>{lab(r.reference)}</sub>; below 0.80 fails the four-fifths rule, 0.80–0.90 is worth a look. ○ is the plain weighted AIR (attenuated toward 1){r.truth ? '; ◆ the AIR with the true groups' : ''}. Grey rows are not judged: under {r.limits.min_eff_n} weighted applicants, or the proxy cannot tell the group apart (separability under {r.limits.min_separability}).</span>}>
        <div ref={ref} data-testid="fl-air-plot">
          <svg width={W} height={rows.length * ROW + 24}>
            {ticks.map((t) => <g key={t}><line x1={sx(t)} x2={sx(t)} y1={0} y2={rows.length * ROW} stroke={t === 0.8 ? 'var(--status-danger)' : 'var(--plot-grid)'} strokeDasharray={t === 0.8 ? '4 3' : undefined} /><text className="tick-text" x={sx(t)} y={rows.length * ROW + 16} textAnchor="middle">{t.toFixed(1)}</text></g>)}
            {rows.map((g, i) => {
              const y = i * ROW + ROW / 2
              const tr = truth[g.group]
              return (
                <g key={g.group} opacity={g.judged ? 1 : 0.55}>
                  <text x={LBL - 10} y={y + 4} textAnchor="end" className="fi-name">{lab(g.group)} <tspan className="fi-val">n≈{Math.round(g.n_eff)}</tspan></text>
                  {g.air != null && <>
                    <line x1={sx(g.air_ci[0])} x2={sx(g.air_ci[1])} y1={y} y2={y} stroke={color(g)} strokeWidth={2} />
                    <circle cx={sx(g.air)} cy={y} r={5} fill={color(g)}><title>{`AIR ${g.air.toFixed(3)} ${ci(g.air_ci)}`}</title></circle>
                  </>}
                  {g.plain_air != null && <circle cx={sx(g.plain_air)} cy={y} r={4} fill="none" stroke="var(--text-muted)"><title>{`plain weighted ${g.plain_air.toFixed(3)}`}</title></circle>}
                  {tr?.true_air != null && <path d={`M${sx(tr.true_air)} ${y - 5} l5 5 l-5 5 l-5 -5 z`} fill="var(--viz-d)"><title>{`true groups ${tr.true_air.toFixed(3)}`}</title></path>}
                </g>
              )
            })}
          </svg>
        </div>
      </PlotFrame>
      <div className="card">
        <div className="card-head"><div className="left"><h3 className="card-title">Approval Rates</h3><span className="helper" style={{ fontSize: 12 }}>A<sub>r</sub>, AIR<sub>r</sub> and D<sub>r</sub> = A<sub>r</sub> − A<sub>{lab(r.reference)}</sub>, each with its 95% CI</span></div></div>
        <div className="table-wrap"><table className="table" data-testid="fl-table">
          <thead><tr><th>group r</th><th className="r math-th" title="Σ p_ir: the number of applicants the proxy counts in r">n<sub>eff</sub></th><th className="r math-th">A<sub>r</sub></th><th className="r math-th">AIR<sub>r</sub></th><th className="r math-th">D<sub>r</sub></th><th className="r" title="Plain weighted AIR: Σp·a/Σp, attenuated toward 1">plain AIR</th>{r.truth && <th className="r">true AIR</th>}<th>check</th></tr></thead>
          <tbody>{r.air.map((g) => (
            <tr key={g.group} className={g.reference ? 'sel' : ''}>
              <td>{lab(g.group)}{g.reference && <span className="muted"> · reference</span>}</td>
              <td className="r table-cell">{Math.round(g.n_eff).toLocaleString()}</td>
              <td className="r table-cell">{pct(g.approval)} <span className="muted">{ci([g.approval_ci[0] * 100, g.approval_ci[1] * 100] as [number, number], 1)}</span></td>
              <td className="r table-cell">{g.reference ? '1' : <>{fix(g.air)} <span className="muted">{ci(g.air_ci)}</span></>}</td>
              <td className="r table-cell">{g.reference ? '0' : <>{signed(g.diff)} <span className="muted">{ci(g.diff_ci)}</span></>}</td>
              <td className="r table-cell muted">{g.reference ? '' : fix(g.plain_air)}</td>
              {r.truth && <td className="r table-cell">{g.reference ? '' : fix(truth[g.group]?.true_air)}</td>}
              <td className="helper" style={{ fontSize: 12 }}>{g.reference ? '' : !g.judged ? (g.n_eff < r.limits.min_eff_n ? 'too few to judge' : `not separable (S ${fix(g.separability, 2)})`) : g.flag ? <b style={{ color: g.air! < 0.8 ? 'var(--status-danger)' : 'var(--status-warning)' }}>{g.flag}</b> : '≥ 0.90'}</td>
            </tr>))}</tbody>
        </table></div>
      </div>
    </>
  )
}

function ProxyCard({ r, lab }: { r: FairLending; lab: (g: string) => string }) {
  const pq = r.proxy
  const mf = r.metricframe
  return (
    <div className="card">
      <div className="card-head"><div className="left"><h3 className="card-title">How Good Is The Proxy</h3><span className="helper" style={{ fontSize: 12 }}>{pq.by_group ? `estimated vs true group (${r.truth?.column}, never a model input)` : 'no true group in this table: shares only'}</span></div></div>
      <div className="table-wrap"><table className="table compact" data-testid="fl-proxy">
        <thead><tr><th>group r</th><th className="r math-th">Σp<sub>ir</sub>/n</th>{pq.by_group && <><th className="r">true share</th><th className="r" title="Share of the group's applicants whose most likely group is r">recall</th><th className="r" title="Share of applicants put in r who are in r">precision</th><th className="r" title="Mean p_ir of the group's applicants">mean p</th></>}<th className="r" title="Σp²/Σp: how well the proxy separates the group">S<sub>r</sub></th></tr></thead>
        <tbody>{r.groups.map((g) => {
          const b = pq.by_group?.find((x) => x.group === g)
          return <tr key={g}><td>{lab(g)}</td><td className="r table-cell">{pct(pq.estimated_shares[g])}</td>
            {b && <><td className="r table-cell">{pct(b.true_share)}</td><td className="r table-cell">{pct(b.recall)}</td><td className="r table-cell">{pct(b.precision)}</td><td className="r table-cell">{fix(b.mean_p_true)}</td></>}
            <td className="r table-cell">{fix(r.air.find((x) => x.group === g)?.separability, 2)}</td></tr>
        })}</tbody>
      </table></div>
      <details className="an-adv" style={{ padding: '0 16px 8px' }}>
        <summary className="micro-label">F4 fairness metrics on the weighted groups (fairlearn MetricFrame, sample_weight = p<sub>ir</sub>)</summary>
        <div className="table-wrap"><table className="table compact" data-testid="fl-metricframe">
          <thead><tr><th>g</th>{mf.metrics.map((m) => <th key={m} className="r math-th" title={LABELS[m]?.[1]}>{m === 'count' ? 'Σp' : LABELS[m]?.[0] ?? m}</th>)}</tr></thead>
          <tbody>
            {mf.by_group.map((g) => <tr key={g.group}><td>{lab(g.group)}</td>{mf.metrics.map((m) => <td key={m} className="r table-cell">{g[m] == null ? '—' : m === 'count' ? Math.round(g[m]!).toLocaleString() : fix(g[m], 4)}</td>)}</tr>)}
            <tr><td className="muted">max − min</td>{mf.metrics.map((m) => <td key={m} className="r table-cell muted">{m === 'count' ? '' : fix(mf.difference[m], 4)}</td>)}</tr>
          </tbody>
        </table></div>
        <p className="helper" style={{ fontSize: 12 }}>ŷ = 1 means flagged {r.positive_class} (declined). These are the plain weighted rates (each applicant counts in every group with weight p<sub>ir</sub>), so gaps are attenuated like the plain AIR.</p>
      </details>
    </div>
  )
}
