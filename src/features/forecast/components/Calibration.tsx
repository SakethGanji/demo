import type { FcConformal } from '@/shared/api/flows'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot } from '@/shared/components/XYPlot'
import { ParamChip } from '@/shared/primitives/ParamChip'

type Interval = { lo: number; hi: number; nominal: number; empirical: number; inside: number; n: number; ci: [number, number]; p_all_inside: number | null; width_rel: number }
export type FcModelCoverage = { model: string; family?: string; lo: number; hi: number; nominal: number; inside: number; n: number; empirical: number | null; ci: [number, number]; backtest: number | null; error?: string }
export type FcCalibration = {
  n: number
  levels: { tau: number; below: number; n: number; ci: [number, number] }[]
  intervals: Interval[]
  backtest?: { model: string; nominal: number; empirical: number | null }[]
  per_model?: FcModelCoverage[]
  verdict: 'calibrated' | 'too_wide' | 'too_narrow'
}

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const sci = (p: number) => (p >= 0.001 ? p.toFixed(3) : p.toExponential(1).replace('e', '×10^').replace('^-', '⁻').replace(/\^(\d+)/, (_, x) => x))
const off = (x: Interval) => x.nominal < x.ci[0] || x.nominal > x.ci[1]

/** Is the fan honest? For each quantile level τ the share of holdout actuals at or below q̂_τ should be τ, and each
 *  central interval [q̂_lo, q̂_hi] should cover hi − lo of them. Measured on the last H steps of every series, for
 *  AutoGluon's quantiles (raw) and, when the run recalibrated them, for the conformal quantiles q̃_τ. */
export function Calibration({ cal, raw, conformal, model, rawCsv }: {
  cal: FcCalibration; raw?: FcCalibration | null; conformal?: FcConformal | null; model: string; rawCsv?: string
}) {
  const recal = !!raw && !!conformal?.factors && !conformal.error
  const before = recal ? raw! : cal
  const main = cal.intervals[0]
  const main0 = before.intervals[0]
  const verdictLine = (c: FcCalibration, m: Interval, who: string) => c.verdict === 'calibrated'
    ? <>{who}: <b>{m.inside} of {m.n}</b> inside the nominal {pct(m.nominal, 0)} interval ({pct(m.empirical)}, 95% CI {pct(m.ci[0])}–{pct(m.ci[1])}): within sampling error of nominal, so the band can be read as stated.</>
    : c.verdict === 'too_wide'
      ? <>{who}: <b>too wide</b>, {m.inside} of {m.n} inside the nominal {pct(m.nominal, 0)} interval. If it were calibrated, P(K ≥ {m.inside}) = {m.p_all_inside != null ? sci(m.p_all_inside) : 'tiny'}: not luck; the model overstates its uncertainty.</>
      : <>{who}: <b>too narrow</b>, only {m.inside} of {m.n} inside the nominal {pct(m.nominal, 0)} interval (P(K ≤ {m.inside}) = {m.p_all_inside != null ? sci(m.p_all_inside) : 'tiny'}); the model understates its uncertainty.</>
  const ptsCal = cal.levels.map((l) => [l.tau, l.below] as [number, number])
  const ptsRaw = before.levels.map((l) => [l.tau, l.below] as [number, number])
  const series = [
    ...(recal ? [{ name: 'F̂(τ) AutoGluon', color: 'var(--viz-c)', points: ptsRaw, dots: true, width: 1.5, dash: '4 3', tip: (i: number) => `raw ${before.levels[i].tau}: ${pct(before.levels[i].below)} below` }] : []),
    { name: recal ? 'F̂(τ) recalibrated' : 'F̂(τ)', color: 'var(--viz-a)', points: ptsCal, dots: true, width: 2, tip: (i: number) => `${cal.levels[i].tau}: ${pct(cal.levels[i].below)} below (95% CI ${pct(cal.levels[i].ci[0])}–${pct(cal.levels[i].ci[1])})` },
  ]
  const legend = [
    ...(recal ? [{ name: 'F̂(τ) AutoGluon quantiles', color: 'var(--viz-c)', dash: '4 3', data: [] }] : []),
    { name: recal ? 'F̂(τ) after recalibration' : 'F̂(τ) empirical', color: 'var(--viz-a)', data: [] },
    { name: 'ideal F̂(τ) = τ', color: 'var(--text-muted)', dash: '4 3', data: [] },
  ]
  const facs = Object.entries(conformal?.factors ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]))
  const pm = (cal.per_model ?? []).filter((x) => !x.error)
  return (
    <PlotFrame title="Interval Calibration" right={<span className="helper" style={{ fontSize: 12 }}>{model} · holdout, n = {cal.n} points{recal ? ' · conformal recalibration on' : ''}{rawCsv ? <> · <a href={rawCsv} download data-testid="fc-download-raw">forecast with AutoGluon’s quantiles (.csv)</a></> : null}</span>}
      chips={<><ParamChip sym="n" value={String(cal.n)} />
        {recal && <ParamChip sym={`before [q${main0.lo}, q${main0.hi}]`} value={`${pct(main0.empirical, 1)}`} />}
        <ParamChip sym={`${recal ? 'after ' : ''}[q${main.lo}, q${main.hi}]`} value={`${pct(main.empirical, 1)} vs ${pct(main.nominal, 0)}`} /></>}
      legend={legend}
      caption={<span>F̂(τ) = (1/n) Σ<sub>i,t</sub> 1[y<sub>i,t</sub> ≤ q̂<sub>τ,i,t</sub>], the share of the n holdout actuals (every series i, every step t of the last H) at or below the forecast quantile; calibrated means F̂(τ) ≈ τ. Bars: 95% Wilson interval for a proportion of n. Flatter than the diagonal = bands too wide; steeper = too narrow.</span>}>
      <div className="fc-cal" data-testid="fc-calibration" data-verdict={cal.verdict} data-verdict-raw={before.verdict} data-recalibrated={recal ? '1' : '0'}>
        <div className="stack" style={{ gap: 10 }}>
          <XYPlot height={230} testId="fc-cal-plot" ariaLabel="empirical vs nominal quantile level"
            x={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'nominal τ', fmt: (v) => v.toFixed(2) }}
            y={{ min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], title: 'F̂(τ)', fmt: (v) => v.toFixed(2) }}
            diagonal={{ label: 'F̂ = τ' }} series={series}>
            {(sx, sy) => <g>{cal.levels.map((l) => <line key={l.tau} x1={sx(l.tau)} x2={sx(l.tau)} y1={sy(l.ci[0])} y2={sy(l.ci[1])} stroke="var(--viz-a)" strokeWidth={1.5} opacity={0.5} />)}</g>}
          </XYPlot>
          {recal && <div className="formula" data-testid="fc-conformal">
            <div className="eq">q̃<sub>τ</sub> = m + c<sub>τ</sub> (q̂<sub>τ</sub> − m)</div>
            <div className="eq" style={{ fontSize: 13.5, paddingTop: 0 }}>{facs.map(([t, c], i) => <span key={t}>{i ? ' · ' : ''}c<sub>{t}</sub> = <b className="mono">{c == null ? '—' : c.toFixed(3)}</b></span>)}</div>
            <dl className="where">
              <dt>m</dt><dd>the centre, q̂<sub>0.5</sub> (the {conformal!.centre === 'mean' ? 'mean' : 'median'}): unchanged, so ŷ and MASE do not move</dd>
              <dt>r</dt><dd>(y − m) / (q̂<sub>τ</sub> − m) on each of the n<sub>bt</sub> = {conformal!.n_scores} backtest points ({conformal!.windows} windows × {conformal!.items} series × H), never the holdout</dd>
              <dt>c<sub>τ</sub></dt><dd>τ &gt; ½: the ⌈(n<sub>bt</sub> + 1)τ⌉-th smallest r; τ &lt; ½: the ⌈(n<sub>bt</sub> + 1)(1 − τ)⌉-th; so P(y ≤ q̃<sub>τ</sub>) ≥ τ above the centre and ≤ τ below it (split-conformal). c<sub>τ</sub> &lt; 1 narrows the band; {conformal!.scope === 'item' ? `fitted per series with ≥ 60 points (${Object.keys(conformal!.per_item ?? {}).length} series), pooled elsewhere` : 'one value pooled over every series'}</dd>
            </dl>
          </div>}
        </div>
        <div className="fc-cal-side">
          <div className="table-wrap"><table className="table compact" data-testid="fc-cal-table">
            <thead><tr><th>interval</th><th className="r">nominal</th>{recal && <th className="r">AutoGluon</th>}<th className="r">{recal ? 'recalibrated' : 'empirical'}</th><th className="r">95% CI</th><th className="r">w̄ / ȳ</th></tr></thead>
            <tbody>{cal.intervals.map((x, k) => {
              const r = before.intervals[k]
              return <tr key={`${x.lo}-${x.hi}`}>
                <td className="mono">[q{x.lo}, q{x.hi}]</td><td className="r table-cell">{pct(x.nominal, 0)}</td>
                {recal && <td className="r table-cell" title={`${r.inside}/${r.n}`} style={{ color: off(r) ? 'var(--status-warning)' : undefined }}>{pct(r.empirical)}</td>}
                <td className="r table-cell" style={{ color: off(x) ? 'var(--status-warning)' : undefined }}><b>{pct(x.empirical)}</b> <span className="muted">{x.inside}/{x.n}</span></td>
                <td className="r table-cell muted">{pct(x.ci[0], 0)}–{pct(x.ci[1], 0)}</td>
                <td className="r table-cell">{recal ? <>{r.width_rel.toFixed(2)} → </> : null}{x.width_rel.toFixed(2)}</td></tr>
            })}</tbody>
          </table></div>
          {recal && <p className={`fc-cal-verdict ${before.verdict}`} data-testid="fc-cal-verdict-raw">{verdictLine(before, main0, 'Before, AutoGluon’s quantiles')}</p>}
          <p className={`fc-cal-verdict ${cal.verdict}`} data-testid="fc-cal-verdict">{verdictLine(cal, main, recal ? 'After conformal recalibration' : 'AutoGluon’s quantiles')}</p>
          {pm.length > 0 && <div className="table-wrap"><table className="table compact" data-testid="fc-cal-models">
            <thead><tr><th>model (AutoGluon quantiles)</th><th className="r">holdout [q{pm[0].lo}, q{pm[0].hi}]</th><th className="r">95% CI</th><th className="r">backtests</th></tr></thead>
            <tbody>{pm.map((x) => <tr key={x.model} className={x.model === model ? 'sel' : ''}>
              <td className="mono" style={{ fontSize: 12 }}>{x.model}</td>
              <td className="r table-cell" style={{ color: x.nominal < x.ci[0] || x.nominal > x.ci[1] ? 'var(--status-warning)' : undefined }}>{pct(x.empirical)} <span className="muted">{x.inside}/{x.n}</span></td>
              <td className="r table-cell muted">{pct(x.ci[0], 0)}–{pct(x.ci[1], 0)}</td>
              <td className="r table-cell">{pct(x.backtest, 0)}</td></tr>)}</tbody>
          </table></div>}
          <p className="helper" style={{ fontSize: 11.5, margin: 0 }}>w̄ / ȳ = mean interval width q̂<sub>hi</sub> − q̂<sub>lo</sub> over the mean actual. P(K ≥ k) is the binomial tail for K ~ Bin(n, nominal), the count a calibrated interval would cover; it treats the n points as independent (steps of one series are not quite, so read it as an order of magnitude). Per model: each model’s own forecast of the holdout from the training data; backtests = mean coverage over the W windows.</p>
        </div>
      </div>
    </PlotFrame>
  )
}
