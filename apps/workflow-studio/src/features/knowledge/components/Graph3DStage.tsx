/**
 * The 3D graph lens: the shared chrome (`GraphFrame`) round a lazily loaded three.js scene. The scene chunk (three.js
 * and the scene code) is fetched only when this lens first opens; until it arrives the canvas shows the loading
 * constellation.
 *
 * It reads and drives the same state as the 2D lens: the URL selection (click flies to a node and opens the detail
 * panel; Escape or the background flies back out), hover (the node and its neighbours light, the rest dims), search
 * and kind / relation / project filters, Ask (its matches light up and the camera frames them) and accept / reject
 * (the refetched graph restyles in place). The canvas is aria-hidden; the List view is its accessible twin.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef } from 'react'
import { Maximize, Minus, Plus } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tip } from '@/shared/components/ui/tooltip'
import { useGraphKeys } from '../hooks/useGraphKeys'
import type { useAskGraph } from '../hooks/useAskGraph'
import { emphasis, type askHits, type filterGraph, type GraphModel } from '../lib/graph'
import type { Graph3DApi } from '../lib/graph3d'
import type { KgGraph } from '../lib/types'
import { GraphFrame } from './GraphFrame'
import { LoadingConstellation } from './LoadingConstellation'
import { PANEL_W } from './NodeDetailPanel'

const Graph3D = lazy(() => import('../three/Graph3D'))

export function Graph3DStage({
  model, data, visible, off, selected, hovered, setHovered, select, pickRef, query, setQuery, matches, ask, askText, setAskText,
  hits, learnedOnly, byProject, orbit, toolbarLeft, toolbarRight, toolbarBelow, detail, onFail,
}: {
  model: GraphModel
  data: KgGraph
  visible: ReturnType<typeof filterGraph>
  off: boolean
  selected: string | null
  hovered: string | null
  setHovered: (id: string | null) => void
  select: (id: string | null) => void
  pickRef: React.MutableRefObject<(id: string) => void>
  query: string
  setQuery: (q: string) => void
  matches: string[]
  ask: ReturnType<typeof useAskGraph>
  askText: string
  setAskText: (v: string) => void
  hits: ReturnType<typeof askHits> | null
  learnedOnly: boolean
  byProject: boolean
  orbit?: ReadonlyMap<string, string>
  toolbarLeft: React.ReactNode
  toolbarRight: React.ReactNode
  toolbarBelow?: React.ReactNode
  detail: React.ReactNode
  /** WebGL is missing or the context was lost: the page switches to the 2D lens */
  onFail: () => void
}) {
  const apiRef = useRef<Graph3DApi | null>(null)
  const inset = selected ? PANEL_W + 24 : 0

  const fitAll = useCallback(() => apiRef.current?.fit(), [])
  // selecting is enough: the scene flies to whatever the URL selects
  const pick = useCallback((id: string) => select(id), [select])
  useEffect(() => {
    pickRef.current = pick
  }, [pickRef, pick])
  useGraphKeys(selected, select, fitAll)

  // an Ask answer frames what it lit
  const askKey = hits?.concepts.join('|') ?? ''
  useEffect(() => {
    if (!askKey || !hits) return
    apiRef.current?.fit(hits.nodes)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reframe when the matched concepts change, not on every render
  }, [askKey])

  const em = useMemo(
    () => emphasis(model, visible, { focus: [hovered ?? selected], ask: hits, search: matches }),
    [model, visible, hovered, selected, hits, matches],
  )
  const askConcepts = useMemo(() => (hits ? hits.concepts.map((id) => model.byId.get(id)!).filter(Boolean) : []), [hits, model])

  const submitSearch = () => {
    if (!matches.length) return
    if (matches.length === 1) pick(matches[0])
    else apiRef.current?.fit(matches)
  }

  return (
    <GraphFrame
      canvas={
        <Suspense fallback={<LoadingConstellation />}>
          <Graph3D
            model={model}
            visible={visible}
            em={em}
            selected={selected}
            hovered={hovered}
            off={off}
            byProject={byProject}
            orbit={orbit}
            layoutKey={`${learnedOnly}|${byProject}`}
            inset={inset}
            onHover={setHovered}
            onPick={(id) => select(id)}
            onBackground={() => {
              if (selected) select(null)
            }}
            onFail={onFail}
            apiRef={apiRef}
          />
        </Suspense>
      }
      inset={inset}
      query={query}
      setQuery={setQuery}
      matches={matches}
      onSubmitSearch={submitSearch}
      toolbarLeft={toolbarLeft}
      toolbarRight={toolbarRight}
      toolbarBelow={toolbarBelow}
      truncated={data.truncated}
      stats={{ shown: visible.nodes.size, total: model.nodes.length, links: visible.edges.size }}
      em={em}
      hits={hits}
      ask={ask}
      askText={askText}
      setAskText={setAskText}
      askConcepts={askConcepts}
      onAskPick={pick}
      off={off}
      controls={<CameraControls onIn={() => apiRef.current?.zoom(1.35)} onOut={() => apiRef.current?.zoom(1 / 1.35)} onFit={fitAll} />}
      detail={detail}
    />
  )
}

/** Dolly in and out, and fly back to frame everything visible. */
function CameraControls({ onIn, onOut, onFit }: { onIn: () => void; onOut: () => void; onFit: () => void }) {
  const btn = 'size-8 rounded-full'
  return (
    <div className="glass-chrome flex flex-col items-center gap-0.5 rounded-full p-1" role="group" aria-label="Camera">
      <Tip label="Fly closer" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onIn} aria-label="Fly closer" data-testid="kg-3d-zoom-in">
          <Plus />
        </Button>
      </Tip>
      <Tip label="Fly back" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onOut} aria-label="Fly back" data-testid="kg-3d-zoom-out">
          <Minus />
        </Button>
      </Tip>
      <Tip label="Fit to view" kbd="F" side="left">
        <Button size="icon" variant="ghost" className={btn} onClick={onFit} aria-label="Fit to view" data-testid="kg-3d-fit">
          <Maximize />
        </Button>
      </Tip>
    </div>
  )
}
