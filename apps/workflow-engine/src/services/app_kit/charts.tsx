/** Studio UI kit — small charts and diffs (provided by Studio, refreshed every builder turn; do not edit).
 * Sparkline (inline SVG line), TrendBars (labelled bar chart, e.g. pass rate per day), ResultStrip
 * (last-N pass/fail tiles) and DiffView (line diff from before/after text, a unified patch, or lines). */
import React, { useMemo } from 'react'
import { cx } from './ui'

/** HTML attributes passed to a chart's root element (`id`, `style`, `data-*`, `aria-*`), minus names the kit uses itself. */
type Attrs<E = HTMLDivElement> = Omit<React.HTMLAttributes<E>, 'title' | 'children' | 'color' | 'content' | 'onChange' | 'onSelect' | 'defaultValue' | 'placeholder' | 'prefix'>
type SvgAttrs = Omit<React.SVGAttributes<SVGSVGElement>, 'values' | 'width' | 'height' | 'color' | 'fill' | 'children'>

/** Inline SVG sparkline for a numeric series; optional soft area fill.
 * @category chart
 * @example <Sparkline values={[3, 5, 4, 8, 6, 9]} /> */
export function Sparkline({ values, width = 96, height = 24, color, fill = true, className, ...rest }:
  { values: number[]; /** @min 16 @max 600 @group Layout */ width?: number; /** @min 8 @max 200 @group Layout */ height?: number
    /** Line colour (default: the theme's primary colour).
     * @control color @group Style */ color?: string; fill?: boolean; className?: string } & SvgAttrs) {
  const pts = values.filter((v) => typeof v === 'number' && !Number.isNaN(v))
  if (pts.length < 2) return <svg width={width} height={height} data-vcomp="Sparkline" {...rest} className={className} aria-hidden />
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min || 1
  const step = width / (pts.length - 1)
  const coords = pts.map((v, i) => [i * step, height - 2 - ((v - min) / span) * (height - 4)])
  const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="trend" data-vcomp="Sparkline" {...rest} className={className}>
      {fill && <path d={`${line} L${width},${height} L0,${height} Z`} fill={color} className={color ? undefined : 'fill-primary'} opacity={0.12} />}
      <path d={line} fill="none" stroke={color} className={color ? undefined : 'stroke-primary'} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export interface TrendPoint { label: string; value: number; tone?: 'blue' | 'green' | 'red' | 'amber' | 'gray' }

const BAR: Record<string, string> = { blue: 'bg-primary', green: 'bg-success', red: 'bg-danger', amber: 'bg-warning', gray: 'bg-input' }

/** Labelled vertical bar chart (e.g. pass rate per run/day). `max` defaults to the largest value; `format` renders values.
 * @category chart
 * @example <TrendBars data={[{ label: 'Mon', value: 4 }, { label: 'Tue', value: 7 }]} tone="green" /> */
export function TrendBars({ data, height = 140, max, format = (v: number) => String(Math.round(v)), tone = 'blue', className, ...rest }:
  { data: TrendPoint[]; /** @control number @min 40 @max 600 @step 10 @group Layout @default 140 */ height?: number; max?: number; format?: (v: number) => string
    /** Bar colour (a point's own `tone` wins).
     * @control select @options blue|green|red|amber|gray @group Style @default blue */ tone?: TrendPoint['tone']; className?: string } & Attrs) {
  const top = max ?? Math.max(1, ...data.map((d) => d.value))
  return (
    <div data-vcomp="TrendBars" {...rest} className={cx('w-full text-muted-foreground', className)}>
      <div className="flex items-end gap-1.5" style={{ height }}>
        {data.map((d, i) => (
          <div key={`${d.label}-${i}`} data-vpart={`data.${i}`} className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end" title={`${d.label}: ${format(d.value)}`}>
            <span className="mb-1 text-[10px] opacity-0 group-hover:opacity-100">{format(d.value)}</span>
            <div className={cx('w-full max-w-[36px] rounded-t', BAR[d.tone ?? tone ?? 'blue'])} style={{ height: `${Math.max(2, (d.value / top) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1.5">
        {data.map((d, i) => (
          <div key={`${d.label}-l-${i}`} data-vpart={`data.${i}`} className="min-w-0 flex-1 truncate text-center text-[10px]">{d.label}</div>
        ))}
      </div>
    </div>
  )
}

/** Last-N result tiles: green pass, red fail, amber flaky/blocked, grey other — like a CI history strip.
 * @category chart
 * @example <ResultStrip results={['passed', 'failed', 'passed']} /> */
export function ResultStrip({ results, className, ...rest }: { results: Array<string | null | undefined>; className?: string } & Attrs<HTMLSpanElement>) {
  const tone = (r: string | null | undefined) => {
    const s = String(r ?? '').toLowerCase()
    if (['passed', 'pass', 'success', 'ok'].includes(s)) return 'bg-success'
    if (['failed', 'fail', 'error'].includes(s)) return 'bg-danger'
    if (['flaky', 'blocked', 'warning'].includes(s)) return 'bg-warning'
    return 'bg-border'
  }
  return (
    <span aria-label="recent results" data-vcomp="ResultStrip" {...rest} className={cx('inline-flex gap-0.5', className)}>
      {results.map((r, i) => <i key={i} className={cx('inline-block h-3 w-1.5 rounded-sm opacity-80', tone(r))} title={String(r ?? '')} />)}
    </span>
  )
}

// ── DiffView ─────────────────────────────────────────────────────────────────

export interface DiffLine { type: 'add' | 'del' | 'ctx'; text: string }

/** Line diff via LCS (fine for files up to a few hundred lines). */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n')
  const b = after.split('\n')
  if (a.length * b.length > 400_000) return [...a.map((t) => ({ type: 'del' as const, text: t })), ...b.map((t) => ({ type: 'add' as const, text: t }))]
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ type: 'ctx', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ type: 'del', text: a[i++] })
    else out.push({ type: 'add', text: b[j++] })
  }
  while (i < a.length) out.push({ type: 'del', text: a[i++] })
  while (j < b.length) out.push({ type: 'add', text: b[j++] })
  return out
}

function parsePatch(patch: string): DiffLine[] {
  return patch.split('\n').filter((l) => !l.startsWith('+++') && !l.startsWith('---') && !l.startsWith('@@'))
    .map((l) => (l.startsWith('+') ? { type: 'add' as const, text: l.slice(1) } : l.startsWith('-') ? { type: 'del' as const, text: l.slice(1) } : { type: 'ctx' as const, text: l.replace(/^ /, '') }))
}

/** Red/green line diff of code or text (use StepDiff for step arrays). Give `before`+`after` (strings or JSON values), a unified `patch`, or `lines`. Header shows title/file and +/− counts.
 * @category data
 * @example <DiffView title="Changes" before={oldText} after={newText} /> */
export function DiffView({ before, after, patch, lines, title, file, actions, maxHeight = 360, className, headerClassName, bodyClassName, ...rest }:
  { before?: unknown; after?: unknown; patch?: string; lines?: DiffLine[]; /** @control text */ title?: React.ReactNode; /** @control text */ file?: React.ReactNode; actions?: React.ReactNode
    /** @control number @min 80 @max 1200 @step 20 @group Layout @default 360 */ maxHeight?: number; className?: string; headerClassName?: string; bodyClassName?: string } & Attrs) {
  const rows = useMemo(() => {
    if (lines) return lines
    if (patch != null) return parsePatch(patch)
    const s = (v: unknown) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2))
    return diffLines(s(before), s(after))
  }, [before, after, patch, lines])
  const adds = rows.filter((r) => r.type === 'add').length
  const dels = rows.filter((r) => r.type === 'del').length
  return (
    <div data-vcomp="DiffView" {...rest} className={cx('overflow-hidden rounded-xl border border-border bg-card text-card-foreground', className)} data-testid="diff-view">
      <div data-vpart="header" className={cx('flex flex-wrap items-center gap-2 border-b border-border bg-subtle px-3 py-2 text-xs', headerClassName)}>
        {title && <b>{title}</b>}
        {file && <span className="font-mono opacity-65">{file}</span>}
        <span className="ml-auto rounded-md bg-border/70 px-1.5 py-0.5 font-semibold text-soft-foreground">+{adds} −{dels}</span>
        {actions}
      </div>
      <pre data-vpart="body" className={cx('m-0 overflow-auto py-2 font-mono text-xs leading-relaxed', bodyClassName)} style={{ maxHeight }}>
        {rows.map((r, i) => (
          <span key={i} className={cx('block whitespace-pre px-3', r.type === 'add' && 'bg-success-soft text-success', r.type === 'del' && 'bg-danger-soft text-danger', r.type === 'ctx' && 'text-soft-foreground')}>
            {r.type === 'add' ? '+ ' : r.type === 'del' ? '- ' : '  '}{r.text}
          </span>
        ))}
      </pre>
    </div>
  )
}
