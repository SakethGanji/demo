/**
 * The main page's way into `/knowledge`: one link card with a small live hint (concepts, patterns and projects in
 * the team graph). It never shows the graph itself. While the count loads, or if it fails, the hint falls back to a
 * plain line, never an error.
 */
import { Link } from '@tanstack/react-router'
import { ArrowRight, Waypoints } from 'lucide-react'
import { pluralize } from '@/features/project/lib/format'
import { IconTile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { useKgGraph } from '../hooks/useKnowledge'
import { kindCounts } from '../lib/graph'
import { KIND_STYLE } from '../lib/kinds'
import { TEAM_SCOPE } from '../lib/types'

export function KnowledgeHomeCard({ className }: { className?: string }) {
  const graph = useKgGraph(TEAM_SCOPE, false)
  const nodes = graph.data?.nodes
  const counts = nodes ? kindCounts(nodes) : null
  const projects = graph.data?.projects?.length ?? 0
  const hint = counts
    ? [pluralize(counts.concept, 'concept'), pluralize(counts.pattern, 'learned pattern'), projects ? pluralize(projects, 'project') : null]
        .filter(Boolean)
        .join(' · ')
    : 'Concepts, capabilities and the patterns learned from kept builds'
  return (
    <Link
      to="/knowledge"
      className={cn(
        'tile glass-hover group relative flex min-w-0 items-center gap-4 p-5 outline-none focus-visible:shadow-focus',
        className,
      )}
      data-testid="home-knowledge"
    >
      <IconTile hue="ai" className="size-10 [&_svg]:size-[19px]">
        <Waypoints />
      </IconTile>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h2 className="text-panel-title font-semibold tracking-[-0.01em]">Knowledge</h2>
        <p className="text-[13px] leading-relaxed text-muted-foreground">Everything the AI builder knows, across all projects.</p>
        <p className="mt-0.5 flex min-w-0 items-center gap-2 text-[12px] text-muted-foreground" data-testid="home-knowledge-hint" aria-live="polite">
          <KindDots />
          <span className={cn('truncate tabular-nums', counts && 'text-foreground/85')}>{hint}</span>
        </p>
      </div>
      <span className="hidden shrink-0 items-center gap-1 text-[12.5px] font-medium text-primary sm:inline-flex">
        Open the graph
        <ArrowRight className="size-[14px] transition-transform duration-(--d-fast) ease-(--ease-out) group-hover:translate-x-0.5 group-focus-visible:translate-x-0.5" />
      </span>
    </Link>
  )
}

/** The legend's first three kinds, as the graph draws them. */
function KindDots() {
  return (
    <span className="flex shrink-0 items-center -space-x-0.5" aria-hidden>
      {(['concept', 'capability', 'pattern'] as const).map((k) => (
        <span key={k} className="inline-block size-2 rounded-full shadow-[0_0_0_1.5px_var(--kg-label-halo)]" style={{ backgroundColor: KIND_STYLE[k].color }} />
      ))}
    </span>
  )
}
