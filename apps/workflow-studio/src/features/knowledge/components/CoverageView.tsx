/**
 * Coverage: every concept against what can build it. Columns are the capability subkinds (node types, templates,
 * ...) plus patterns and apps; each cell's heat is its count against the column's best. A concept nothing
 * implements is a gap: its row is tinted and sorts first, because that is where the AI builder will struggle.
 * On the team page the matrix covers every project, and a last column shows which projects build on each concept
 * (read from the loaded graph).
 */
import { useMemo, useState } from 'react'
import { CircleAlert, Grid3x3 } from 'lucide-react'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Stat, Tile, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { coverageColumns, heatLevel, sortRows, type CoverageSort } from '../lib/coverage'
import { subkindLabel } from '../lib/kinds'
import type { KgMatrixRow } from '../lib/types'
import type { GraphModel } from '../lib/graph'
import { conceptProjects } from '../lib/projects'
import { useKgCapabilities } from '../hooks/useKnowledge'
import { useKnowledgeScope } from '../hooks/useKnowledgeScope'
import { ProjectChip, ProjectDot } from './KnowledgeScope'

/** heat 0..4 as a tint of the capability colour (flat, no gradient) */
const HEAT = ['0%', '14%', '28%', '46%', '68%']
const heatStyle = (level: number): React.CSSProperties =>
  level ? { backgroundColor: `color-mix(in srgb, var(--viz-2) ${HEAT[level]}, transparent)` } : {}

type Extra = { key: 'patterns' | 'apps' | 'validated'; label: string }
const extra: Extra[] = [
  { key: 'patterns', label: 'Patterns' },
  { key: 'apps', label: 'Apps' },
  { key: 'validated', label: 'Validated' },
]

export function CoverageView({ model, onPick }: { model?: GraphModel | null; onPick: (conceptId: string) => void }) {
  const { scope, team, projects } = useKnowledgeScope()
  const q = useKgCapabilities(scope)
  const byProject = useMemo(() => {
    if (!team || !model) return null
    const m = new Map<string, string[]>()
    for (const r of q.data?.rows ?? []) m.set(r.concept.id, conceptProjects(model, r.concept.id, projects))
    return m
  }, [team, model, q.data, projects])
  const [sort, setSort] = useState<CoverageSort>('gaps')
  const rows = useMemo(() => sortRows(q.data?.rows ?? [], sort), [q.data, sort])
  const cols = useMemo(() => coverageColumns(q.data?.rows ?? []), [q.data])
  const max = useMemo(() => {
    const m: Record<string, number> = {}
    for (const r of q.data?.rows ?? []) {
      for (const c of cols) m[c] = Math.max(m[c] ?? 0, r.implemented_by[c] ?? 0)
      for (const e of extra) m[e.key] = Math.max(m[e.key] ?? 0, r[e.key])
    }
    return m
  }, [q.data, cols])

  if (q.isPending)
    return (
      <div className="flex flex-col gap-3" data-testid="knowledge-coverage-loading">
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    )
  if (q.isError)
    return (
      <Callout tone="error" icon={<CircleAlert />} data-testid="knowledge-coverage-error">
        <div className="flex items-center gap-3">
          <span className="flex-1">Couldn't load coverage. {errorMessage(q.error)}</span>
          <Button size="xs" variant="outline" onClick={() => void q.refetch()}>Retry</Button>
        </div>
      </Callout>
    )
  const t = q.data.totals
  if (!rows.length)
    return (
      <Tile className="p-3">
        <EmptyState icon={<Grid3x3 />} title="No concepts yet" data-testid="knowledge-coverage-empty">
          Rebuild the graph to map concepts to the nodes, templates and patterns that implement them.
        </EmptyState>
      </Tile>
    )

  const grid = {
    gridTemplateColumns: `minmax(180px, 1.6fr) repeat(${cols.length + extra.length}, minmax(64px, 1fr)) 72px${byProject ? ' minmax(140px, 1.3fr)' : ''}`,
  }
  return (
    <div className="flex flex-col gap-4" data-testid="knowledge-coverage">
      <Tile className="grid grid-cols-2 gap-5 p-5 sm:grid-cols-4">
        <Stat label="Concepts" value={t.concepts} testId="knowledge-cov-concepts" />
        <Stat label="Implemented" value={t.implemented} meta={`of ${t.concepts}`} testId="knowledge-cov-implemented" />
        <Stat label="Gaps" value={t.gaps} valueClassName={t.gaps ? 'text-warning-text' : undefined} testId="knowledge-cov-gaps" />
        <Stat label="Validated by kept builds" value={t.validated} testId="knowledge-cov-validated" />
      </Tile>
      <Tile className="flex flex-col gap-3 p-3">
        <div className="flex items-center gap-2 px-1">
          <span className="text-[12.5px] text-muted-foreground">
            Each cell counts what implements the concept. Darker is closer to the best covered concept in that column.
          </span>
          <div className="flex-1" />
          <Segmented<CoverageSort>
            value={sort}
            onChange={setSort}
            aria-label="Sort concepts"
            options={[
              { key: 'gaps', label: 'Gaps first', testId: 'knowledge-cov-sort-gaps' },
              { key: 'coverage', label: 'Best covered', testId: 'knowledge-cov-sort-coverage' },
              { key: 'name', label: 'A to Z', testId: 'knowledge-cov-sort-name' },
            ]}
          />
        </div>
        <Well className="overflow-x-auto p-0">
          <div role="table" aria-label="Concept coverage" className="min-w-max">
            <div role="row" className="hairline-b grid items-end gap-px px-3 py-2 text-[11.5px] font-medium text-muted-foreground" style={grid}>
              <span role="columnheader">Concept</span>
              {cols.map((c) => (
                <span key={c} role="columnheader" className="text-center">{subkindLabel(c)}</span>
              ))}
              {extra.map((e) => (
                <span key={e.key} role="columnheader" className="text-center">{e.label}</span>
              ))}
              <span role="columnheader" className="text-right">Total</span>
              {byProject && <span role="columnheader" className="pl-4">Projects</span>}
            </div>
            {rows.map((r) => (
              <CoverageRow key={r.concept.id} r={r} cols={cols} extra={extra} max={max} grid={grid} onPick={onPick} projects={byProject?.get(r.concept.id)} />
            ))}
          </div>
        </Well>
      </Tile>
    </div>
  )
}

function CoverageRow({
  r, cols, extra, max, grid, onPick, projects,
}: {
  r: KgMatrixRow
  cols: string[]
  extra: Extra[]
  max: Record<string, number>
  grid: React.CSSProperties
  onPick: (id: string) => void
  /** team scope: the projects building on this concept (undefined: no column) */
  projects?: string[]
}) {
  const cell = (n: number, m: number) => (
    <span
      role="cell"
      className={cn('flex h-7 items-center justify-center rounded-chip text-[12px] tabular-nums', n ? 'text-foreground' : 'text-faint')}
      style={heatStyle(heatLevel(n, m))}
    >
      {n || '·'}
    </span>
  )
  return (
    <div
      role="row"
      className={cn('grid items-center gap-px px-3 py-1', r.gap && 'bg-warning-soft')}
      style={grid}
      data-testid={`knowledge-cov-row-${r.concept.id}`}
      data-gap={r.gap || undefined}
    >
      <span role="rowheader" className="flex min-w-0 items-center gap-2 pr-2">
        <button
          type="button"
          onClick={() => onPick(r.concept.id)}
          className="min-w-0 truncate rounded-chip text-left text-[12.5px] font-medium outline-none hover:text-primary focus-visible:shadow-focus"
        >
          {r.concept.label}
        </button>
        {r.gap && <Badge variant="warning">Gap</Badge>}
      </span>
      {cols.map((c) => (
        <span key={c} className="contents">{cell(r.implemented_by[c] ?? 0, max[c] ?? 0)}</span>
      ))}
      {extra.map((e) => (
        <span key={e.key} className="contents">{cell(r[e.key], max[e.key] ?? 0)}</span>
      ))}
      <span role="cell" className={cn('text-right text-[12.5px] font-semibold tabular-nums', r.gap && 'text-warning-text')}>{r.implemented_total}</span>
      {projects && <ProjectsCell ids={projects} />}
    </div>
  )
}

/** One project shows its chip; more show a row of dots with a count (the cell's label names them all). */
function ProjectsCell({ ids }: { ids: string[] }) {
  const { projects } = useKnowledgeScope()
  const names = ids.map((id) => projects.get(id)?.name ?? id)
  return (
    <span role="cell" className="flex min-w-0 items-center gap-1 pl-4" data-testid="knowledge-cov-projects" aria-label={names.length ? names.join(', ') : 'Shared only'}>
      {ids.length === 0 ? (
        <span className="text-[12px] text-faint">·</span>
      ) : ids.length === 1 ? (
        <ProjectChip projectId={ids[0]} />
      ) : (
        <>
          {ids.slice(0, 6).map((id) => (
            <ProjectDot key={id} projectId={id} className="size-2.5" />
          ))}
          <span className="ml-1 truncate text-[11.5px] text-muted-foreground" title={names.join(', ')}>
            {ids.length > 6 ? `${ids.length} projects` : names.length === 2 ? names.join(', ') : `${ids.length} projects`}
          </span>
        </>
      )}
    </span>
  )
}
