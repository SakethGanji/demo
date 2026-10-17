/**
 * DiffView (DESIGN §8, Backend AI change flow): a unified line diff in a well, +N/−M stat, and the
 * Apply / Discard actions; after Apply, a folded summary with Undo (`DiffApplied`). Lines come from
 * `diffLines` (an LCS line diff, the same algorithm as `features/backend/cm/lineDiff.ts`).
 */
import { useMemo } from 'react'
import { Check, Undo2 } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Button } from './button'
import { Kbd } from './kbd'

export interface DiffLine {
  kind: 'same' | 'add' | 'del'
  text: string
  /** line number in the old (del/same) or new (add/same) text */
  a?: number
  b?: number
}

/** LCS line diff → unified lines. Large inputs fall back to "replace the middle". */
export function diffLines(prev: string, next: string): DiffLine[] {
  const a = prev.split('\n')
  const b = next.split('\n')
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length - 1
  let endB = b.length - 1
  while (endA >= start && endB >= start && a[endA] === b[endB]) {
    endA--
    endB--
  }
  const out: DiffLine[] = []
  for (let i = 0; i < start; i++) out.push({ kind: 'same', text: a[i], a: i + 1, b: i + 1 })
  const A = a.slice(start, endA + 1)
  const B = b.slice(start, endB + 1)
  if (A.length * B.length > 4_000_000) {
    A.forEach((t, i) => out.push({ kind: 'del', text: t, a: start + i + 1 }))
    B.forEach((t, j) => out.push({ kind: 'add', text: t, b: start + j + 1 }))
  } else {
    const n = A.length
    const m = B.length
    const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    let i = 0
    let j = 0
    while (i < n || j < m) {
      if (i < n && j < m && A[i] === B[j]) {
        out.push({ kind: 'same', text: A[i], a: start + i + 1, b: start + j + 1 })
        i++
        j++
      } else if (j >= m || (i < n && dp[i + 1][j] >= dp[i][j + 1])) {
        out.push({ kind: 'del', text: A[i], a: start + i + 1 })
        i++
      } else {
        out.push({ kind: 'add', text: B[j], b: start + j + 1 })
        j++
      }
    }
  }
  for (let k = endA + 1, l = endB + 1; k < a.length; k++, l++) out.push({ kind: 'same', text: a[k], a: k + 1, b: l + 1 })
  return out
}

/** Keeps `context` unchanged lines around each change and folds the rest. */
function hunks(lines: DiffLine[], context: number) {
  const keep = new Array(lines.length).fill(false)
  lines.forEach((l, i) => {
    if (l.kind === 'same') return
    for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep[k] = true
  })
  const out: Array<DiffLine | { fold: number }> = []
  let folded = 0
  lines.forEach((l, i) => {
    if (keep[i]) {
      if (folded) out.push({ fold: folded })
      folded = 0
      out.push(l)
    } else folded++
  })
  if (folded) out.push({ fold: folded })
  return out
}

export function DiffStat({ added, removed, className }: { added: number; removed: number; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 font-mono text-[11.5px] tabular-nums', className)}>
      <span className="text-success-text">+{added}</span>
      <span className="text-destructive">−{removed}</span>
    </span>
  )
}

export function DiffView({
  prev,
  next,
  title,
  context = 2,
  onApply,
  onDiscard,
  applyLabel = 'Apply',
  className,
  testId = 'diff-view',
  maxHeight = 320,
}: {
  prev: string
  next: string
  title?: React.ReactNode
  context?: number
  onApply?: () => void
  onDiscard?: () => void
  applyLabel?: string
  className?: string
  testId?: string
  maxHeight?: number
}) {
  const lines = useMemo(() => diffLines(prev, next), [prev, next])
  const added = lines.filter((l) => l.kind === 'add').length
  const removed = lines.filter((l) => l.kind === 'del').length
  const view = useMemo(() => hunks(lines, context), [lines, context])
  return (
    <div className={cn('flex flex-col gap-2', className)} data-testid={testId}>
      <div className="flex items-center gap-2">
        {title && <span className="min-w-0 truncate font-mono text-[12px] text-foreground">{title}</span>}
        <DiffStat added={added} removed={removed} />
        <div className="flex-1" />
        {onDiscard && (
          <Button variant="ghost" size="xs" onClick={onDiscard} data-testid={`${testId}-discard`}>
            Discard
          </Button>
        )}
        {onApply && (
          <Button size="xs" onClick={onApply} data-testid={`${testId}-apply`}>
            <Check /> {applyLabel} <Kbd>⇧⌘↵</Kbd>
          </Button>
        )}
      </div>
      <div className="well overflow-auto py-1.5 font-mono text-[12px] leading-5" style={{ maxHeight }} data-scroll-x="allowed">
        {view.map((l, i) =>
          'fold' in l ? (
            <div key={`f${i}`} className="px-3 text-[11px] text-muted-foreground">
              ⋯ {l.fold} unchanged line{l.fold === 1 ? '' : 's'}
            </div>
          ) : (
            <div
              key={i}
              className={cn(
                'grid grid-cols-[34px_14px_1fr] pr-3 whitespace-pre',
                l.kind === 'add' && 'bg-line-added',
                l.kind === 'del' && 'bg-destructive-soft',
              )}
              data-diff={l.kind}
            >
              <span className="pr-2 text-right text-code-gutter select-none">{l.kind === 'del' ? l.a : l.b}</span>
              <span className={cn('select-none', l.kind === 'add' ? 'text-success-text' : l.kind === 'del' ? 'text-destructive' : 'text-faint')}>
                {l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}
              </span>
              <span className={cn(l.kind === 'del' && 'text-muted-foreground line-through decoration-destructive/40')}>{l.text || ' '}</span>
            </div>
          ),
        )}
      </div>
    </div>
  )
}

/** The folded summary after a change was applied, with Undo. */
export function DiffApplied({
  summary,
  added,
  removed,
  onUndo,
  className,
  testId = 'diff-applied',
}: {
  summary: React.ReactNode
  added: number
  removed: number
  onUndo?: () => void
  className?: string
  testId?: string
}) {
  return (
    <div className={cn('flex min-h-9 items-center gap-2 rounded-well bg-recess px-3 text-[12.5px]', className)} data-testid={testId}>
      <Check className="size-3.5 shrink-0 text-success" />
      <span className="min-w-0 flex-1 truncate">{summary}</span>
      <DiffStat added={added} removed={removed} />
      {onUndo && (
        <Button variant="ghost" size="xs" onClick={onUndo} data-testid={`${testId}-undo`}>
          <Undo2 /> Undo
        </Button>
      )}
    </div>
  )
}
