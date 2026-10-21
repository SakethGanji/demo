/** Studio UI kit — small charts and diffs (provided by Studio, refreshed every builder turn; do not edit).
 * Sparkline (inline SVG line), TrendBars (labelled bar chart, e.g. pass rate per day), ResultStrip
 * (last-N pass/fail tiles) and DiffView (line diff from before/after text, a unified patch, or lines). */
import React, { useMemo } from 'react'
import { cx } from './ui'

/** Inline SVG sparkline for a numeric series; optional soft area fill. */
export function Sparkline({ values, width = 96, height = 24, color = '#2563eb', fill = true, className }:
  { values: number[]; width?: number; height?: number; color?: string; fill?: boolean; className?: string }) {
  const pts = values.filter((v) => typeof v === 'number' && !Number.isNaN(v))
  if (pts.length < 2) return <svg width={width} height={height} className={className} aria-hidden />
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min || 1
  const step = width / (pts.length - 1)
  const coords = pts.map((v, i) => [i * step, height - 2 - ((v - min) / span) * (height - 4)])
  const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} role="img" aria-label="trend">
      {fill && <path d={`${line} L${width},${height} L0,${height} Z`} fill={color} opacity={0.12} />}
      <path d={line} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export interface TrendPoint { label: string; value: number; tone?: 'blue' | 'green' | 'red' | 'amber' | 'gray' }

const BAR: Record<string, string> = { blue: 'bg-blue-500', green: 'bg-emerald-500', red: 'bg-red-500', amber: 'bg-amber-500', gray: 'bg-slate-300' }

/** Labelled vertical bar chart (e.g. pass rate per run/day). `max` defaults to the largest value; `format` renders values. */
export function TrendBars({ data, height = 140, max, format = (v: number) => String(Math.round(v)), tone = 'blue', className }:
  { data: TrendPoint[]; height?: number; max?: number; format?: (v: number) => string; tone?: TrendPoint['tone']; className?: string }) {
  const top = max ?? Math.max(1, ...data.map((d) => d.value))
  return (
    <div className={cx('w-full', className)}>
      <div className="flex items-end gap-1.5" style={{ height }}>
        {data.map((d, i) => (
          <div key={`${d.label}-${i}`} className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end" title={`${d.label}: ${format(d.value)}`}>
            <span className="mb-1 text-[10px] text-slate-500 opacity-0 group-hover:opacity-100">{format(d.value)}</span>
            <div className={cx('w-full max-w-[36px] rounded-t', BAR[d.tone ?? tone ?? 'blue'])} style={{ height: `${Math.max(2, (d.value / top) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1.5">
        {data.map((d, i) => (
          <div key={`${d.label}-l-${i}`} className="min-w-0 flex-1 truncate text-center text-[10px] text-slate-500">{d.label}</div>
        ))}
      </div>
    </div>
  )
}

/** Last-N result tiles: green pass, red fail, amber flaky/blocked, grey other — like a CI history strip. */
export function ResultStrip({ results, className }: { results: Array<string | null | undefined>; className?: string }) {
  const tone = (r: string | null | undefined) => {
    const s = String(r ?? '').toLowerCase()
    if (['passed', 'pass', 'success', 'ok'].includes(s)) return 'bg-emerald-500'
    if (['failed', 'fail', 'error'].includes(s)) return 'bg-red-500'
    if (['flaky', 'blocked', 'warning'].includes(s)) return 'bg-amber-500'
    return 'bg-slate-200'
  }
  return (
    <span className={cx('inline-flex gap-0.5', className)} aria-label="recent results">
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

/** Red/green line diff of code or text (use StepDiff for step arrays). Give `before`+`after` (strings or JSON values), a unified `patch`, or `lines`. Header shows title/file and +/− counts. */
export function DiffView({ before, after, patch, lines, title, file, actions, maxHeight = 360, className }:
  { before?: unknown; after?: unknown; patch?: string; lines?: DiffLine[]; title?: React.ReactNode; file?: React.ReactNode; actions?: React.ReactNode; maxHeight?: number; className?: string }) {
  const rows = useMemo(() => {
    if (lines) return lines
    if (patch != null) return parsePatch(patch)
    const s = (v: unknown) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2))
    return diffLines(s(before), s(after))
  }, [before, after, patch, lines])
  const adds = rows.filter((r) => r.type === 'add').length
  const dels = rows.filter((r) => r.type === 'del').length
  return (
    <div className={cx('overflow-hidden rounded-xl border border-slate-200 bg-white', className)} data-testid="diff-view">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs">
        {title && <b className="text-slate-800">{title}</b>}
        {file && <span className="font-mono text-slate-500">{file}</span>}
        <span className="ml-auto rounded-md bg-slate-200/70 px-1.5 py-0.5 font-semibold text-slate-600">+{adds} −{dels}</span>
        {actions}
      </div>
      <pre className="m-0 overflow-auto py-2 font-mono text-xs leading-relaxed" style={{ maxHeight }}>
        {rows.map((r, i) => (
          <span key={i} className={cx('block whitespace-pre px-3', r.type === 'add' && 'bg-emerald-50 text-emerald-800', r.type === 'del' && 'bg-red-50 text-red-800', r.type === 'ctx' && 'text-slate-600')}>
            {r.type === 'add' ? '+ ' : r.type === 'del' ? '- ' : '  '}{r.text}
          </span>
        ))}
      </pre>
    </div>
  )
}
