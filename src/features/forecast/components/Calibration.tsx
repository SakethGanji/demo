import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot } from '@/shared/components/XYPlot'
import { ParamChip } from '@/shared/primitives/ParamChip'

export type FcCalibration = {
  n: number
  levels: { tau: number; below: number; n: number; ci: [number, number] }[]
  intervals: { lo: number; hi: number; nominal: number; empirical: number; inside: number; n: number; ci: [number, number]; p_all_inside: number | null; width_rel: number }[]
  backtest?: { model: string; nominal: number; empirical: number | null }[]
  verdict: 'calibrated' | 'too_wide' | 'too_narrow'
  pit_spread?: number | null
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const sci = (p: number) => (p >= 0.001 ? p.toFixed(3) : p.toExponential(1).replace('e', '×10^').replace('^-', '⁻').replace(/\^(\d+)/, (_, x) => x))

/** Is the fan honest? For each quantile level τ the share of holdout actuals at or below q̂_τ should be τ, and each
 *  central interval [q̂_lo, q̂_hi] should cover hi − lo of them. Measured on the last H steps of every series. */
export function Calibration({ cal, model }: { cal: FcCalibration; model: string }) {
  const main = cal.intervals[0]
  // Which models were near nominal in the backtest windows, and which were wide: says where the width comes from.
  const bt = (cal.backtest ?? []).filter((b) => b.empirical != null)
  const near = bt.filter((b) => Math.abs(b.empirical! - main.nominal) <= 0.05).map((b) => `${b.model} ${pct(b.empirical, 0)}`)
  const wide = bt.filter((b) => b.empirical! - main.nominal > 0.05).map((b) => `${b.model} ${pct(b.empirical, 0)}`)
  const why = bt.length ? <> In the backtest windows {near.length ? <>{near.join(', ')} {near.length === 1 ? 'was' : 'were'} near nominal, while </> : null}{wide.length ? <>{wide.join(', ')} covered more than {pct(main.nominal, 0)}</> : null}: the ensemble’s band follows its heaviest-weighted members.</> : null
  const verdict = cal.verdict === 'calibrated'
    ? <>Within sampling error of nominal: the bands can be read as stated.</>
    : cal.verdict === 'too_wide'
      ? <>The bands are <b>too wide</b>: {main.inside} of {main.n} actuals fell inside the nominal {pct(main.nominal, 0)} interval. If it were calibrated, the chance of {main.inside === main.n ? 'all of them' : `${main.inside} or more`} landing inside would be P(K ≥ {main.inside}) = {main.p_all_inside != null ? sci(main.p_all_inside) : 'tiny'}, so this is not luck. The model overstates its uncertainty.{why} Read the band as a conservative range, not as a {pct(main.nominal, 0)} probability; the median forecast and MASE are not affected.</>
      : <>The bands are <b>too narrow</b>: only {main.inside} of {main.n} actuals fell inside the nominal {pct(main.nominal, 0)} interval. The model understates its uncertainty; widen the band (or add num_val_windows) before using it for limits.</>
  const pts = cal.levels.map((l) => [l.tau, l.below] as [number, number])
  return (
    <PlotFrame title="Interval Calibration" right={<span className="helper" style={{ fontSize: 12 }}>{model} · holdout, n = {cal.n} points</span>}
      chips={<><ParamChip sym="n" value={String(cal.n)} /><ParamChip sym={`[q${main.lo}, q${main.hi}]`} value={`${pct(main.empirical, 1)} vs ${pct(main.nominal, 0)}`} /></>}
      legend={[{ name: 'F̂(τ) empirical', color: 'var(--viz-a)', data: [] }, { name: 'ideal F̂(τ) = τ', color: 'var(--text-muted)', dash: '4 3', data: [] }]}
      caption={<span>F̂(τ) = (1/n) Σ<sub>i,t</sub> 1[y<sub>i,t</sub> ≤ q̂<sub>τ,i,t</sub>], the share of the n holdout actuals (every series i, every step t of the last H) at or below the forecast quantile q̂<sub>τ</sub>; calibrated means F̂(τ) ≈ τ. Bars: 95% Wilson interval for a proportion of n. Flatter than the diagonal = bands too wide; steeper = too narrow.</span>}>
      <div className="fc-cal" data-testid="fc-calibration" data-verdict={cal.verdict}>
        <XYPlot height={220} testId="fc-cal-plot" ariaLabel="empirical vs nominal quantile level"
          x={{ min: 0, max: 1, ticks: [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1], title: 'nominal τ', fmt: (v) => v.toFixed(2) }}
          y={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'F̂(τ)', fmt: (v) => v.toFixed(2) }}
          diagonal={{ label: 'F̂ = τ' }}
          series={[{ name: 'F̂(τ)', color: 'var(--viz-a)', points: pts, dots: true, width: 2, tip: (i) => `${cal.levels[i].tau}: ${pct(cal.levels[i].below)} below (95% CI ${pct(cal.levels[i].ci[0])}–${pct(cal.levels[i].ci[1])})` }]}>
          {(sx, sy) => <g>{cal.levels.map((l) => <line key={l.tau} x1={sx(l.tau)} x2={sx(l.tau)} y1={sy(l.ci[0])} y2={sy(l.ci[1])} stroke="var(--viz-a)" strokeWidth={1.5} opacity={0.5} />)}</g>}
        </XYPlot>
        <div className="fc-cal-side">
          <table className="table compact" data-testid="fc-cal-table">
            <thead><tr><th>interval</th><th className="r">nominal</th><th className="r">empirical</th><th className="r">95% CI</th><th className="r">w̄ / ȳ</th></tr></thead>
            <tbody>{cal.intervals.map((x) => {
              const off = x.nominal < x.ci[0] || x.nominal > x.ci[1]
              return <tr key={`${x.lo}-${x.hi}`}>
                <td className="mono">[q{x.lo}, q{x.hi}]</td><td className="r table-cell">{pct(x.nominal, 0)}</td>
                <td className="r table-cell" style={{ color: off ? 'var(--status-warning)' : undefined }}><b>{pct(x.empirical)}</b> <span className="muted">{x.inside}/{x.n}</span></td>
                <td className="r table-cell muted">{pct(x.ci[0], 0)}–{pct(x.ci[1], 0)}</td><td className="r table-cell">{x.width_rel.toFixed(2)}</td></tr>
            })}</tbody>
          </table>
          {cal.backtest && cal.backtest.length > 0 && <p className="helper" style={{ fontSize: 12, margin: '6px 0 0' }}>Backtest windows, [q{main.lo}, q{main.hi}] coverage by model: {cal.backtest.map((b) => <span key={b.model} className="mono" style={{ marginRight: 8 }}>{b.model} {pct(b.empirical, 0)}</span>)}</p>}
          <p className={`fc-cal-verdict ${cal.verdict}`} data-testid="fc-cal-verdict">{verdict}</p>
          <p className="helper" style={{ fontSize: 11.5, margin: 0 }}>w̄ / ȳ = mean interval width q̂<sub>hi</sub> − q̂<sub>lo</sub> over the mean actual. P(K ≥ k) is the binomial tail for K ~ Bin(n, nominal), the count a calibrated interval would cover; it treats the n points as independent (steps of one series are not quite, so read it as an order of magnitude).</p>
        </div>
      </div>
    </PlotFrame>
  )
}
