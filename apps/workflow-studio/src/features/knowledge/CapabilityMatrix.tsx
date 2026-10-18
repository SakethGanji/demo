/**
 * What we can do (PLAN-CAPGRAPH-UI §2.3): one row per business concept, a count bar of squares per capability type, and a hatched
 * gap where nothing implements it yet. Counts only; nothing that looks like a score. A gap that closes plays the morph beat.
 */
import { useEffect, useId, useRef } from 'react'
import { motion } from 'motion/react'
import { Sparkles, UserRound } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { SectionHeader, Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { motionOff } from '@/shared/motion/testFlag'
import { ConceptChip } from './kit/ConceptChip'
import { Construct } from './kit/Construct'
import { useBuildWith } from './hooks'
import { count, slugOf } from './lib/labels'
import type { MatrixRow, MatrixTotals } from './types'

export type MatrixFilter = 'all' | 'gaps' | 'covered'

const COLS: { key: string; label: string; from: string[] }[] = [
  { key: 'subworkflow', label: 'Sub-workflow', from: ['subworkflow'] },
  { key: 'workflow', label: 'Workflow', from: ['workflow'] },
  { key: 'api_operation', label: 'API', from: ['api_operation'] },
  { key: 'template', label: 'Template', from: ['template'] },
  { key: 'screen', label: 'Screen', from: ['screen_component', 'interface'] },
  { key: 'node_type', label: 'Node', from: ['node_type'] },
]
const GRID = 'grid grid-cols-[minmax(150px,1.5fr)_repeat(6,minmax(56px,1fr))_76px_52px_52px_minmax(70px,0.8fr)] items-center gap-x-2'
const GROUPS: { key: string; label: string }[] = [
  { key: 'process', label: 'Processes' }, { key: 'entity', label: 'Entities' }, { key: 'control', label: 'Controls' }, { key: 'artifact', label: 'Artifacts' },
]

function Squares({ n, focal, delay }: { n: number; focal: boolean; delay: number }) {
  if (n <= 0) return <span className="size-1.5 rounded-full bg-recess" aria-label="none" />
  const shown = Math.min(5, n)
  return (
    <span className="inline-flex items-center gap-[2px]" aria-label={String(n)} data-count={n}>
      {Array.from({ length: shown }, (_, i) => (
        <motion.span
          key={i} className={cn('size-1.5 rounded-xs', focal ? 'bg-primary' : 'bg-foreground/55')} style={{ transformOrigin: 'left' }}
          initial={motionOff() ? false : { scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ duration: 0.14, delay: delay + i * 0.02 }}
        />
      ))}
      {n > 5 && <span className="ml-0.5 text-[11px] text-muted-foreground tabular-nums">+{n - 5}</span>}
    </span>
  )
}

function GapCell({ onBuild, testId }: { onBuild: () => void; testId: string }) {
  const id = useId()
  return (
    <div className="relative col-span-6 flex h-7 items-center justify-between overflow-hidden rounded-chip pr-1.5 pl-3" data-testid="kg-matrix-gap">
      <svg className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <pattern id={id} width={8} height={8} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1={0} y1={0} x2={0} y2={8} stroke="var(--warning)" strokeWidth={1} strokeOpacity={0.35} />
            <line x1={0} y1={0} x2={8} y2={0} stroke="var(--warning)" strokeWidth={1} strokeOpacity={0.35} />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${id})`} />
      </svg>
      <span className="relative text-[12px] font-medium text-warning-text">gap · nothing builds this yet</span>
      <Button variant="ai" size="xs" className="relative" onClick={onBuild} data-testid={testId}><Sparkles /> Build with this</Button>
    </div>
  )
}

function Row({ row, index, closed, definition, slug, onBuild }: { row: MatrixRow; index: number; closed: boolean; definition: string; slug: string; onBuild: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (closed) ref.current?.scrollIntoView?.({ block: 'center', behavior: motionOff() ? 'auto' : 'smooth' })
  }, [closed])
  const delay = Math.min(index, 20) * 0.035
  const sk = slugOf(row.concept.key)
  const counts = COLS.map((c) => c.from.reduce((s, k) => s + (row.implemented_by[k] ?? 0), 0))
  const focal = counts.indexOf(Math.max(...counts))
  const roles = row.roles.slice(0, 3)
  const line = (
    <div
      ref={ref} role="row" className={cn(GRID, 'rounded-ctl px-3 py-2', row.gap && 'bg-[color-mix(in_srgb,var(--warning-soft)_40%,transparent)]', index < 20 && 'animate-row-in')}
      style={index < 20 ? { animationDelay: `${delay}s` } : undefined}
      data-testid={`kg-matrix-row-${sk}`} data-gap={row.gap} data-closed={closed || undefined}
    >
      <div role="cell" className="flex min-w-0 items-center gap-2">
        <ConceptChip conceptKey={row.concept.key} label={row.concept.label} size="sm" hint={definition || undefined} href={{ projectSlug: slug, tab: 'graph', focus: row.concept.key }} />
        {closed && <Badge variant="success" className="animate-badge-pop" data-testid="kg-gap-closed">No longer a gap</Badge>}
      </div>
      {row.gap ? (
        <GapCell onBuild={onBuild} testId={`kg-matrix-build-${sk}`} />
      ) : (
        COLS.map((c, i) => (
          <div role="cell" key={c.key}>
            <Tip label={counts[i] ? `${count(counts[i], 'capability', 'capabilities')} · ${c.label.toLowerCase()}` : undefined}>
              <span className="inline-flex"><Squares n={counts[i]} focal={i === focal && counts[i] > 0} delay={delay + 0.1} /></span>
            </Tip>
          </div>
        ))
      )}
      <div role="cell" className="text-[12px]">
        {row.validated > 0 ? (
          <Tip label={count(row.patterns, 'pattern')}><span><Badge variant="ai" className="tabular-nums">×{row.validated}</Badge></span></Tip>
        ) : <span className="text-faint">·</span>}
      </div>
      <div role="cell" className="text-[12.5px] tabular-nums text-muted-foreground">{row.apps || '·'}</div>
      <div role="cell" className="text-[12.5px] tabular-nums text-muted-foreground">
        {row.teams.length ? <Tip label={row.teams.join(', ')}><span>{row.teams.length}</span></Tip> : '·'}
      </div>
      <div role="cell" className="flex items-center gap-0.5">
        {roles.map((r) => (
          <Tip key={r.id} label={r.label}><span className="inline-flex size-4 items-center justify-center rounded-full bg-raise text-hue-secrets shadow-raise-sm [&_svg]:size-2.5"><UserRound /></span></Tip>
        ))}
        {row.roles.length > 3 && <span className="text-[11px] text-muted-foreground">+{row.roles.length - 3}</span>}
      </div>
    </div>
  )
  return closed ? <Construct mode="morph" tone="success" radius={8} data-testid="kg-matrix-morph">{line}</Construct> : line
}

export function CapabilityMatrix({ rows, totals, slug, filter, onFilter, closedGaps, groupOf, definitionOf, loading }: {
  rows: MatrixRow[] | undefined
  totals: MatrixTotals | undefined
  slug: string
  filter: MatrixFilter
  onFilter: (f: MatrixFilter) => void
  closedGaps: ReadonlySet<string>
  groupOf: (conceptKey: string) => string
  definitionOf: (conceptKey: string) => string
  loading: boolean
}) {
  const buildWith = useBuildWith()
  const all = rows ?? []
  const shown = all.filter((r) => (filter === 'gaps' ? r.gap : filter === 'covered' ? !r.gap : true))
  const gapCount = totals?.gaps ?? all.filter((r) => r.gap).length
  const known = new Set(GROUPS.map((g) => g.key))
  const groups = [...GROUPS, ...[...new Set(shown.map((r) => groupOf(r.concept.key)))].filter((k) => !known.has(k)).map((k) => ({ key: k, label: 'Other concepts' }))]
  const order = new Map(shown.map((r, i) => [r.concept.key, i]))
  return (
    <Tile className="flex flex-col gap-3 p-4" data-testid="kg-matrix">
      <SectionHeader
        title="What we can do"
        sub={totals ? `${totals.implemented} of ${totals.concepts} concepts can run today` : undefined}
        actions={
          <Segmented<MatrixFilter>
            value={filter} onChange={onFilter} aria-label="Show" data-testid="kg-matrix-filter"
            options={[
              { key: 'all', label: 'All', testId: 'kg-matrix-filter-all' },
              { key: 'gaps', label: 'Gaps', count: gapCount, testId: 'kg-matrix-filter-gaps' },
              { key: 'covered', label: 'Covered', count: all.length - gapCount, testId: 'kg-matrix-filter-covered' },
            ]}
          />
        }
      />
      <Well className="overflow-x-auto p-2" role="table" aria-label="What we can do">
        <div className="min-w-[860px]">
          <div role="row" className={cn(GRID, 'px-3 pb-1.5 text-[11.5px] text-muted-foreground')}>
            <span role="columnheader">Concept</span>
            {COLS.map((c) => <span key={c.key} role="columnheader" className="truncate">{c.label}</span>)}
            <span role="columnheader">Patterns</span>
            <span role="columnheader">Apps</span>
            <span role="columnheader">Teams</span>
            <span role="columnheader">Roles</span>
          </div>
          {loading && !rows && <div className="px-3 py-6 text-[12.5px] text-muted-foreground">Reading what your org can do…</div>}
          {rows && shown.length === 0 && (
            <EmptyState title={filter === 'gaps' ? 'No gaps' : 'Nothing here'} data-testid="kg-matrix-empty">
              {filter === 'gaps' ? 'Every business concept has something that builds it.' : 'Nothing matches this filter.'}
            </EmptyState>
          )}
          {groups.map((g) => {
            const items = shown.filter((r) => groupOf(r.concept.key) === g.key)
            if (!items.length) return null
            return (
              <div key={g.key} role="rowgroup" className="flex flex-col">
                <div className="px-3 pt-2 pb-1 text-[12px] font-medium text-muted-foreground">{g.label}</div>
                {items.map((r) => (
                  <Row
                    key={r.concept.key} row={r} index={order.get(r.concept.key) ?? 0} closed={closedGaps.has(r.concept.key) && !r.gap} slug={slug}
                    definition={definitionOf(r.concept.key)}
                    onBuild={() => buildWith({ label: r.concept.label, definition: definitionOf(r.concept.key) })}
                  />
                ))}
              </div>
            )
          })}
        </div>
      </Well>
    </Tile>
  )
}

