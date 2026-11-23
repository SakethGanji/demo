/** Studio UI kit — charts with axes (provided by Studio, refreshed every builder turn; do not edit).
 * LineChart (one line per series, optional area), BarChart (grouped or stacked) and PieChart (pie or donut). Hand-rolled SVG:
 * rows in, `x` names the category field, `series` the numeric fields. Series colours are the theme's chart-1..8 tokens;
 * hovering shows a tooltip, a legend sits underneath. */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { cx } from './ui'

type DivProps = Omit<React.HTMLAttributes<HTMLDivElement>, 'title' | 'children'>

export interface ChartSeries {
  /** the row field holding the number */
  key: string
  /** @control text */
  label?: React.ReactNode
}

const FILL = ['fill-chart-1', 'fill-chart-2', 'fill-chart-3', 'fill-chart-4', 'fill-chart-5', 'fill-chart-6', 'fill-chart-7', 'fill-chart-8']
const STROKE = ['stroke-chart-1', 'stroke-chart-2', 'stroke-chart-3', 'stroke-chart-4', 'stroke-chart-5', 'stroke-chart-6', 'stroke-chart-7', 'stroke-chart-8']
const SWATCH = ['bg-chart-1', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5', 'bg-chart-6', 'bg-chart-7', 'bg-chart-8']
const PAD = { top: 12, right: 12, bottom: 28, left: 44 }

const num = (v: unknown): number => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) || 0 : 0)
const defaultFormat = (v: number) => (Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : String(Math.round(v * 100) / 100))

/** Width of a container, following resizes. */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(480)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth || 480)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setW(el.clientWidth || 480))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/** "Nice" axis ticks from 0 (or the minimum, if negative) to the maximum. */
function ticks(min: number, max: number, count = 4): number[] {
  const lo = Math.min(0, min)
  const span = max - lo || 1
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let t = Math.floor(lo / step) * step; t <= max + step * 0.999; t += step) out.push(Math.round(t * 1e6) / 1e6)
  return out.length > 1 ? out : [lo, lo + 1]
}

function Legend({ series }: { series: ChartSeries[] }) {
  if (series.length < 2) return null
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {series.map((s, i) => (
        <span key={s.key} className="inline-flex items-center gap-1.5"><i className={cx('inline-block h-2.5 w-2.5 rounded-sm', SWATCH[i % 8])} />{s.label ?? s.key}</span>
      ))}
    </div>
  )
}

function Tip({ x, y, title, rows }: { x: number; y: number; title: React.ReactNode; rows: { label: React.ReactNode; value: string; i: number }[] }) {
  return (
    <div className="pointer-events-none absolute z-10 min-w-[8rem] -translate-x-1/2 -translate-y-full rounded-md border border-border bg-card px-2 py-1.5 text-xs text-card-foreground shadow-md"
      style={{ left: x, top: y - 6 }}>
      <div className="mb-0.5 font-semibold text-foreground">{title}</div>
      {rows.map((r) => (
        <div key={r.i} className="flex items-center gap-1.5"><i className={cx('inline-block h-2 w-2 rounded-sm', SWATCH[r.i % 8])} /><span className="flex-1 text-muted-foreground">{r.label}</span><b className="tabular-nums">{r.value}</b></div>
      ))}
    </div>
  )
}

interface XYProps<Row> extends DivProps {
  /** The rows, one per x position.
   * @control hidden @group Data */
  data: Row[]
  /** Row field shown along the x axis.
   * @control text @group Data */
  x: string
  /** Numeric row fields, one line/bar colour each.
   * @control hidden @group Data */
  series: ChartSeries[]
  /** Plot height in pixels.
   * @control number @min 80 @max 800 @step 10 @group Layout @default 240 */
  height?: number
  /** Formats values on the axis and in the tooltip.
   * @control hidden @group Data */
  format?: (v: number) => string
  /** Shown when there are no rows.
   * @control text @group Content @default No data */
  emptyText?: React.ReactNode
}

/** Axes, grid lines and the hover column shared by LineChart and BarChart. */
function XYFrame<Row>({ data, x, series, height = 240, format = defaultFormat, emptyText = 'No data', className, rest, stacked, vcomp, draw }: XYProps<Row> & {
  rest: DivProps; stacked: boolean; vcomp: string
  draw: (a: { sx: (i: number) => number; sy: (v: number) => number; band: number; vals: number[][]; base: number }) => React.ReactNode
}) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const vals = useMemo(() => data.map((r) => series.map((s) => num((r as Record<string, unknown>)[s.key]))), [data, series])
  const all = vals.flatMap((v) => (stacked ? [v.reduce((a, b) => a + Math.max(0, b), 0), ...v.filter((n) => n < 0)] : v))
  const tk = ticks(Math.min(0, ...all), Math.max(0, ...all))
  const lo = tk[0]
  const hi = tk[tk.length - 1]
  const iw = Math.max(10, width - PAD.left - PAD.right)
  const ih = Math.max(10, height - PAD.top - PAD.bottom)
  const band = data.length ? iw / data.length : iw
  const sx = (i: number) => PAD.left + band * i + band / 2
  const sy = (v: number) => PAD.top + ih - ((v - lo) / (hi - lo || 1)) * ih
  const every = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(iw / 64))))
  return (
    <div ref={ref} data-vcomp={vcomp} {...rest} className={cx('relative w-full min-w-0', className)}>
      {!data.length ? <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>{emptyText}</div> : (
        <svg width={width} height={height} role="img" className="block overflow-visible" onMouseLeave={() => setHover(null)}>
          {tk.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={PAD.left + iw} y1={sy(t)} y2={sy(t)} className="stroke-border" strokeDasharray={t === 0 ? undefined : '3 3'} />
              <text x={PAD.left - 6} y={sy(t)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px]">{format(t)}</text>
            </g>
          ))}
          {data.map((r, i) => (i % every === 0 ? (
            <text key={i} x={sx(i)} y={PAD.top + ih + 16} textAnchor="middle" className="fill-muted-foreground text-[10px]">{String((r as Record<string, unknown>)[x] ?? '')}</text>
          ) : null))}
          {hover != null && <rect x={PAD.left + band * hover} y={PAD.top} width={band} height={ih} className="fill-muted" opacity={0.6} />}
          {draw({ sx, sy, band, vals, base: sy(Math.max(lo, 0)) })}
          {data.map((_, i) => <rect key={i} x={PAD.left + band * i} y={PAD.top} width={band} height={ih} fill="transparent" onMouseEnter={() => setHover(i)} />)}
        </svg>
      )}
      {hover != null && data[hover] && (
        <Tip x={sx(hover)} y={PAD.top} title={String((data[hover] as Record<string, unknown>)[x] ?? '')}
          rows={series.map((s, i) => ({ label: s.label ?? s.key, value: format(vals[hover][i]), i }))} />
      )}
      <Legend series={series} />
    </div>
  )
}

/** Lines over a category or time axis, one per series; `area` fills under them.
 * @category chart
 * @example <LineChart data={[{ day: 'Mon', opened: 4, closed: 2 }, { day: 'Tue', opened: 6, closed: 5 }]} x="day" series={[{ key: 'opened', label: 'Opened' }, { key: 'closed', label: 'Closed' }]} /> */
export function LineChart<Row>({ data, x, series, height, format, emptyText, area = false, curved = true, className, ...rest }: XYProps<Row> & {
  /** Fill the area under each line.
   * @control boolean @group Style @default false */
  area?: boolean
  /** Smooth the lines.
   * @control boolean @group Style @default true */
  curved?: boolean
}) {
  return (
    <XYFrame data={data} x={x} series={series} height={height} format={format} emptyText={emptyText} className={className} rest={rest} stacked={false} vcomp="LineChart"
      draw={({ sx, sy, vals, base }) => series.map((s, si) => {
        const pts = vals.map((v, i) => [sx(i), sy(v[si])] as const)
        const d = pts.map(([px, py], i) => {
          if (!i) return `M${px},${py}`
          if (!curved) return `L${px},${py}`
          const [qx, qy] = pts[i - 1]
          const mx = (qx + px) / 2
          return `C${mx},${qy} ${mx},${py} ${px},${py}`
        }).join(' ')
        return (
          <g key={s.key}>
            {area && pts.length > 1 && <path d={`${d} L${pts[pts.length - 1][0]},${base} L${pts[0][0]},${base} Z`} className={FILL[si % 8]} opacity={0.12} />}
            <path d={d} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className={STROKE[si % 8]} />
            {pts.length <= 31 && pts.map(([px, py], i) => <circle key={i} cx={px} cy={py} r={2.5} className={FILL[si % 8]} />)}
          </g>
        )
      })} />
  )
}

/** Vertical bars per category: series side by side (`grouped`) or on top of each other (`stacked`).
 * @category chart
 * @example <BarChart data={[{ month: 'Jan', approved: 12, rejected: 3 }, { month: 'Feb', approved: 9, rejected: 5 }]} x="month" series={[{ key: 'approved', label: 'Approved' }, { key: 'rejected', label: 'Rejected' }]} mode="stacked" /> */
export function BarChart<Row>({ data, x, series, height, format, emptyText, mode = 'grouped', className, ...rest }: XYProps<Row> & {
  /** Series side by side or stacked.
   * @control select @options grouped|stacked @group Layout @default grouped */
  mode?: 'grouped' | 'stacked'
}) {
  const stacked = mode === 'stacked'
  return (
    <XYFrame data={data} x={x} series={series} height={height} format={format} emptyText={emptyText} className={className} rest={rest} stacked={stacked} vcomp="BarChart"
      draw={({ sx, sy, band, vals, base }) => vals.map((v, i) => {
        const inner = Math.min(band * 0.7, stacked ? 48 : 18 * series.length)
        const left = sx(i) - inner / 2
        let up = 0
        let down = 0
        return (
          <g key={i}>
            {v.map((n, si) => {
              let y0: number
              let y1: number
              let bx = left
              let bw = inner
              if (stacked) {
                const from = n >= 0 ? up : down
                const to = from + n
                if (n >= 0) up = to; else down = to
                y0 = sy(from); y1 = sy(to)
              } else {
                bw = inner / series.length
                bx = left + bw * si
                y0 = base; y1 = sy(n)
              }
              return <rect key={si} x={bx + (stacked ? 0 : 1)} width={Math.max(1, bw - (stacked ? 0 : 2))} y={Math.min(y0, y1)} height={Math.max(1, Math.abs(y1 - y0))} rx={2} className={FILL[si % 8]} />
            })}
          </g>
        )
      })} />
  )
}

export interface PieSlice {
  /** @control text */
  label: React.ReactNode
  value: number
}

/** Share of a whole: a pie, or a donut (`donut`) with an optional total in the middle. Slices are sorted by size.
 * @category chart
 * @example <PieChart data={[{ label: 'Open', value: 12 }, { label: 'Closed', value: 30 }]} donut centerLabel="Total" /> */
export function PieChart({ data, donut = false, size = 180, centerLabel, format = defaultFormat, emptyText = 'No data', className, ...rest }: DivProps & {
  /** The slices.
   * @control hidden @group Data */
  data: PieSlice[]
  /** Cut out the middle.
   * @control boolean @group Style @default false */
  donut?: boolean
  /** Diameter in pixels.
   * @control number @min 80 @max 480 @step 10 @group Layout @default 180 */
  size?: number
  /** Small text under the total in a donut's middle.
   * @control text @group Content */
  centerLabel?: React.ReactNode
  /** Formats values in the tooltip and the middle.
   * @control hidden @group Data */
  format?: (v: number) => string
  /** Shown when every value is zero.
   * @control text @group Content @default No data */
  emptyText?: React.ReactNode
}) {
  const [hover, setHover] = useState<number | null>(null)
  const slices = data.map((d, i) => ({ ...d, i, value: Math.max(0, num(d.value)) })).sort((a, b) => b.value - a.value)
  const total = slices.reduce((a, s) => a + s.value, 0)
  const r = size / 2
  const ir = donut ? r * 0.6 : 0
  let angle = -Math.PI / 2
  const pt = (rad: number, a: number) => `${r + rad * Math.cos(a)},${r + rad * Math.sin(a)}`
  const hovered = hover != null ? slices.find((s) => s.i === hover) : undefined
  return (
    <div data-vcomp="PieChart" {...rest} className={cx('flex min-w-0 flex-wrap items-center gap-4', className)}>
      {!total ? <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ width: size, height: size }}>{emptyText}</div> : (
        <div className="relative" style={{ width: size, height: size }}>
          <svg width={size} height={size} role="img" onMouseLeave={() => setHover(null)}>
            {slices.map((s) => {
              const a0 = angle
              const sweep = (s.value / total) * Math.PI * 2
              angle += sweep
              const a1 = a0 + Math.min(sweep, Math.PI * 2 - 1e-4)
              const big = sweep > Math.PI ? 1 : 0
              const d = donut
                ? `M${pt(r, a0)} A${r},${r} 0 ${big} 1 ${pt(r, a1)} L${pt(ir, a1)} A${ir},${ir} 0 ${big} 0 ${pt(ir, a0)} Z`
                : `M${r},${r} L${pt(r, a0)} A${r},${r} 0 ${big} 1 ${pt(r, a1)} Z`
              return <path key={s.i} d={d} className={cx(FILL[s.i % 8], 'stroke-card')} strokeWidth={1.5} opacity={hover == null || hover === s.i ? 1 : 0.45}
                onMouseEnter={() => setHover(s.i)} />
            })}
          </svg>
          {donut && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
              <span className="text-lg font-semibold tabular-nums text-foreground">{format(hovered ? hovered.value : total)}</span>
              <span className="text-xs text-muted-foreground">{hovered ? hovered.label : centerLabel}</span>
            </div>
          )}
          {!donut && hovered && <Tip x={r} y={r} title={hovered.label} rows={[{ label: `${Math.round((hovered.value / total) * 100)}%`, value: format(hovered.value), i: hovered.i }]} />}
        </div>
      )}
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
        {slices.map((s) => (
          <li key={s.i} className="flex items-center gap-1.5" onMouseEnter={() => setHover(s.i)} onMouseLeave={() => setHover(null)}>
            <i className={cx('inline-block h-2.5 w-2.5 rounded-sm', SWATCH[s.i % 8])} />
            <span className="text-soft-foreground">{s.label}</span>
            <span className="tabular-nums text-muted-foreground">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
