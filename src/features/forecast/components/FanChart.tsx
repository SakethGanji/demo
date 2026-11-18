import { useMemo } from 'react'
import type { FcBand } from '@/shared/api/flows'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { XYPlot, niceTicks, padDomain, type XYSeries } from '@/shared/components/XYPlot'

const DAY = 86_400_000
const ms = (t: string) => new Date(t.length <= 10 ? `${t}T00:00:00Z` : t.replace(' ', 'T') + 'Z').getTime()
const fmtDate = (v: number) => new Date(v).toISOString().slice(5, 10)
export const fmtNum = (v: number) => Math.abs(v) >= 1e4 ? `${(v / 1e3).toFixed(0)}k` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toFixed(Math.abs(v) < 10 ? 2 : 0)

/** The forecast fan: history y_t, then ŷ_{t+h|t} (the median q₀.₅) with nested central bands [q_τ, q_{1−τ}] for every
 *  pair of quantile levels; on the holdout window the actuals overlay the forecast made without them. */
export function FanChart({ item, history, holdout, future, quantiles, freq, showHoldout = true, title }: {
  item: string; history: { t: string[]; y: (number | null)[] }; holdout?: FcBand; future?: FcBand; quantiles: number[]; freq?: string; showHoldout?: boolean; title?: string
}) {
  const qs = [...quantiles].sort((a, b) => a - b)
  const pairs = qs.filter((q) => q < 0.5).map((lo) => [lo, qs.find((h) => Math.abs(h - (1 - lo)) < 1e-9)] as const).filter((p): p is readonly [number, number] => p[1] != null)
  const med = qs.find((q) => Math.abs(q - 0.5) < 1e-9)
  const hist = useMemo(() => history.t.map((t, i) => [ms(t), history.y[i]] as [number, number | null]).filter((p): p is [number, number] => p[1] != null), [history])
  const band = (b?: FcBand) => b ? b.t.map(ms) : []
  const hx = band(holdout), fx = band(future)
  const hist0 = showHoldout && holdout ? hist.filter(([x]) => x < hx[0]) : hist
  const series: XYSeries[] = [{ name: 'history y_t', color: 'var(--text-body)', width: 1.4, points: hist0 }]
  const center = (b: FcBand) => (med != null ? b.q[String(med)] : b.mean)
  const tipFor = (b: FcBand) => (i: number) => pairs.map(([lo, hi]) => `[q${lo}, q${hi}] ${fmtNum(b.q[String(lo)][i])}–${fmtNum(b.q[String(hi)][i])}`).slice(0, 2).join(' · ')
  if (showHoldout && holdout) {
    series.push({ name: 'holdout actual y_t', color: 'var(--viz-d)', width: 1.6, points: hx.map((x, i) => [x, holdout.actual?.[i] ?? NaN] as [number, number]).filter((p) => Number.isFinite(p[1])) })
    series.push({ name: 'backtest ŷ (q0.5)', color: 'var(--viz-c)', dash: '4 3', width: 1.6, points: hx.map((x, i) => [x, center(holdout)[i]] as [number, number]), tip: tipFor(holdout) })
  }
  if (future) series.push({ name: 'forecast ŷ_{t+h|t} (q0.5)', color: 'var(--accent-primary)', width: 2, points: fx.map((x, i) => [x, center(future)[i]] as [number, number]), tip: tipFor(future) })
  const allY = [...hist0.map((p) => p[1]), ...(showHoldout && holdout ? [...(holdout.actual ?? []).filter((v): v is number => v != null), ...Object.values(holdout.q).flat()] : []), ...(future ? Object.values(future.q).flat() : [])]
  const [y0, y1] = padDomain(allY, 0.05)
  const xs = [...hist0.map((p) => p[0]), ...(showHoldout ? hx : []), ...fx]
  const x0 = Math.min(...xs), x1 = Math.max(...xs)
  const span = (x1 - x0) / DAY
  const step = span > 200 ? 30 : span > 90 ? 14 : span > 30 ? 7 : 2
  const xt: number[] = []
  for (let v = x0; v <= x1; v += step * DAY) xt.push(v)
  const legend = [
    { name: 'history', color: 'var(--text-body)', data: [] },
    ...(showHoldout && holdout ? [{ name: 'holdout actual', color: 'var(--viz-d)', data: [] }, { name: 'backtest median', color: 'var(--viz-c)', dash: '4 3', data: [] }] : []),
    { name: 'forecast median', color: 'var(--accent-primary)', data: [] },
  ]
  const bandsOf = (b: FcBand | undefined, xsB: number[], color: string) => (sx: (v: number) => number, sy: (v: number) => number) => b ? pairs.map(([lo, hi], k) => {
    const up = xsB.map((x, i) => `${sx(x).toFixed(1)},${sy(b.q[String(hi)][i]).toFixed(1)}`)
    const dn = xsB.map((x, i) => `${sx(x).toFixed(1)},${sy(b.q[String(lo)][i]).toFixed(1)}`).reverse()
    return <polygon key={`${color}${lo}`} points={[...up, ...dn].join(' ')} fill={color} opacity={0.1 + 0.08 * k} stroke="none" />
  }) : null
  return (
    <PlotFrame title={title ?? `Forecast · ${item}`} legend={legend}
      chips={<span className="mono helper" style={{ fontSize: 11.5 }}>{pairs.length} band{pairs.length === 1 ? '' : 's'}: {pairs.map(([a, b]) => `[q${a}, q${b}]`).join(' ⊃ ')}</span>}
      caption={<span>Bands are nested central intervals [q<sub>τ</sub>, q<sub>1−τ</sub>]; darker = narrower. {freq ? `freq ${freq}. ` : ''}Hover reads ŷ and the two widest bands.</span>}>
      <XYPlot height={300} series={series} testId="fan-chart"
        x={{ min: x0, max: x1, ticks: xt, title: 't', fmt: fmtDate }}
        y={{ min: y0, max: y1, ticks: niceTicks(y0, y1, 5), title: 'y', fmt: fmtNum }}
        refs={[...(future && fx.length ? [{ x: fx[0] - DAY / 2, label: 'now · h = 1', color: 'var(--accent-primary)' }] : []), ...(showHoldout && hx.length ? [{ x: hx[0] - DAY / 2, label: 'holdout starts', color: 'var(--viz-d)' }] : [])]}>
        {(sx, sy) => <g>{bandsOf(showHoldout ? holdout : undefined, hx, 'var(--viz-c)')(sx, sy)}{bandsOf(future, fx, 'var(--accent-primary)')(sx, sy)}</g>}
      </XYPlot>
    </PlotFrame>
  )
}
