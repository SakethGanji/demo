/**
 * The chrome both graph lenses share (2D and 3D): the glass tile and well, the search and filter toolbars, the
 * truncation note and stats, the Ask box, a slot for the camera controls, the detail panel, and the polite live
 * region that points assistive tech at the List view (the canvases are aria-hidden).
 */
import { pluralize } from '@/features/project/lib/format'
import { Callout, Tile, Well } from '@/shared/components/ui/surface'
import type { AskHits, Emphasis } from '../lib/graph'
import type { KgNode } from '../lib/types'
import type { useAskGraph } from '../hooks/useAskGraph'
import { AskBox } from './AskBox'
import { SearchBox } from './GraphToolbar'

export function GraphFrame({
  canvas, inset, query, setQuery, matches, onSubmitSearch, toolbarLeft, toolbarRight, toolbarBelow, truncated, stats, em, hits,
  ask, askText, setAskText, askConcepts, onAskPick, off, controls, detail,
}: {
  canvas: React.ReactNode
  /** px covered by the detail panel on the right */
  inset: number
  query: string
  setQuery: (q: string) => void
  matches: string[]
  onSubmitSearch: () => void
  toolbarLeft: React.ReactNode
  toolbarRight: React.ReactNode
  toolbarBelow?: React.ReactNode
  truncated: boolean
  stats: { shown: number; total: number; links: number }
  em: Emphasis
  hits: AskHits | null
  ask: ReturnType<typeof useAskGraph>
  askText: string
  setAskText: (v: string) => void
  askConcepts: KgNode[]
  onAskPick: (id: string) => void
  off: boolean
  controls: React.ReactNode
  detail: React.ReactNode
}) {
  return (
    <Tile className="mx-8 mb-6 min-h-[420px] flex-1 p-1.5">
      <Well className="relative h-full overflow-hidden rounded-panel">
        {canvas}

        {/* the canvas is aria-hidden; this tells assistive tech where the accessible twin is */}
        <p className="sr-only" aria-live="polite">
          {em.mode === 'ask' && hits ? `${hits.concepts.length} concepts and ${hits.implementers.length} implementers lit.` : ''}
          {em.mode === 'search' ? `${matches.length} matches.` : ''} The List view has every node as a button.
        </p>

        <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-wrap items-start justify-between gap-2" style={{ paddingRight: inset }}>
          <div className="flex max-w-full flex-wrap items-center gap-2">
            <div className="glass-chrome pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-panel p-1">
              <SearchBox value={query} onChange={setQuery} onSubmit={onSubmitSearch} matches={query.trim() ? matches.length : null} />
              <span className="mx-0.5 h-5 w-px bg-hairline" aria-hidden />
              {toolbarLeft}
            </div>
            {toolbarBelow && (
              <div className="glass-chrome pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-panel p-1">{toolbarBelow}</div>
            )}
          </div>
          <div className="glass-chrome pointer-events-auto flex items-center gap-1 rounded-panel p-1">{toolbarRight}</div>
        </div>

        <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[260px] flex-col items-start gap-2">
          {truncated && (
            <Callout tone="warn" className="pointer-events-auto glass-chrome" data-testid="knowledge-truncated">
              Showing the first 400 nodes. Turn on Learned only to focus on what the graph learned.
            </Callout>
          )}
          <div className="glass-chrome rounded-full px-3 py-1 text-[11.5px] tabular-nums text-muted-foreground" data-testid="knowledge-stats">
            {stats.shown === stats.total ? pluralize(stats.total, 'node') : `${stats.shown} of ${stats.total} nodes`}
            {' · '}
            {pluralize(stats.links, 'link')}
          </div>
        </div>

        <div
          className="pointer-events-none absolute bottom-3 z-10 flex -translate-x-1/2 justify-center"
          style={{ left: `calc((100% - ${inset}px) / 2)`, width: `min(580px, calc(100% - ${inset}px - 360px))`, minWidth: 260 }}
        >
          <div className="pointer-events-auto w-full">
            <AskBox
              value={askText}
              onChange={setAskText}
              pending={ask.pending}
              concepts={askConcepts}
              implementers={hits?.implementers.length ?? 0}
              unmatched={!!ask.data && (hits?.concepts.length ?? 0) === 0}
              onPick={onAskPick}
              off={off}
            />
          </div>
        </div>

        <div className="absolute bottom-3 z-10 transition-[right] duration-(--d-base) ease-(--ease-out)" style={{ right: 12 + inset }}>
          {controls}
        </div>

        {detail}
      </Well>
    </Tile>
  )
}
