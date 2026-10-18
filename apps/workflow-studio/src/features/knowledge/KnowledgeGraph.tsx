/**
 * The Graph tab (PLAN-CAPGRAPH-UI §2.2): one xyflow canvas, an overview in columns and a focus (ego) view on an ellipse.
 * Layout is pure (`lib/layout`), edge paths are precomputed, nodes have no handles, hover is one <style> write.
 * Motion: first-load assembly (once per session), focus FLIP + comets + typed labels, and the same beats on rebuild.
 */
import '@xyflow/react/dist/style.css'
import './components/graph/graph.css'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider, useReactFlow, type NodeMouseHandler } from '@xyflow/react'
import { ChevronRight, Maximize2, Minus, Network, Plus, Search } from 'lucide-react'
import { easeInOutCubic } from '@/features/workflow-editor/components/canvas/stageGeometry'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { SearchMenuList, type SearchMenuGroup } from '@/shared/components/ui/search-menu'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Switch } from '@/shared/components/ui/switch'
import { hueText, Tile } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { errorMessage } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'
import { motionOff } from '@/shared/motion/testFlag'
import { AssemblyDecor, EgoLabels, GroupCaptions, KgEdges, MorePills, OrbitGuide, SectorCaptions, type EdgeAnim } from './components/graph/KgEdges'
import { KgNodeView, type KgFlowNode, type KgNodeData } from './components/graph/KgNode'
import { useHoverStyle } from './components/graph/useHoverStyle'
import { kg } from './kit/beats'
import { KindIcon } from './kit/KindIcon'
import { asKind, KIND_LABEL, kindHue, type KgKind } from './kit/kinds'
import { planAssembly, type AssemblyPlan } from './lib/assembly'
import { egoViewport, freeRect } from './lib/camera'
import { count, sectionTitle, sentence } from './lib/labels'
import { adjacency, egoLayout, overviewLayout, sizeOf, type Box, type LaidEdge, type OverviewLayout } from './lib/layout'
import { usePrefetchKgNode } from './hooks'
import { NodeSheet } from './NodeSheet'
import type { EdgeView, GraphResponse, NodeView } from './types'

const nodeTypes = { kg: KgNodeView }
const NO_NODES: NodeView[] = []
const NO_EDGES: EdgeView[] = []
const PRO = { hideAttribution: true }
const FILTER_KINDS: KgKind[] = ['concept', 'role', 'capability', 'pattern', 'app']

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const assembledKey = (pid: string) => `kg:assembled:${pid}`
function alreadyAssembled(pid: string): boolean {
  try { return sessionStorage.getItem(assembledKey(pid)) === '1' } catch { return false }
}
function markAssembled(pid: string) {
  try { sessionStorage.setItem(assembledKey(pid), '1') } catch { /* storage blocked: it just plays again next visit */ }
}

interface Transition {
  seq: number
  flip: Map<string, { dx: number; dy: number; delay: number }>
  enter: Map<string, number>
  changed: boolean
}
const NO_TRANSITION: Transition = { seq: 0, flip: new Map(), enter: new Map(), changed: false }

export interface KnowledgeGraphProps {
  pid: string
  graph: GraphResponse | undefined
  loading: boolean
  error: unknown
  learnedOnly: boolean
  onLearnedOnly: (v: boolean) => void
  focusKey?: string
  depth: 1 | 2
  onFocusKey: (key: string | null) => void
  onDepth: (d: 1 | 2) => void
  /** the hero asks for one kind to be shown */
  kindRequest?: { kinds: KgKind[]; seq: number }
}

export function KnowledgeGraph(props: KnowledgeGraphProps) {
  return (
    <ReactFlowProvider>
      <GraphInner {...props} />
    </ReactFlowProvider>
  )
}

function GraphInner({ pid, graph, loading, error, learnedOnly, onLearnedOnly, focusKey, depth, onFocusKey, onDepth, kindRequest }: KnowledgeGraphProps) {
  const rf = useReactFlow()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const captionRef = useRef<HTMLSpanElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [ready, setReady] = useState(false)
  const [kinds, setKinds] = useState<ReadonlySet<KgKind>>(() => new Set(FILTER_KINDS))
  const [nodeTypesOn, setNodeTypesOn] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [trail, setTrail] = useState<string[]>([])
  const [pulse, setPulse] = useState<{ id: string; seq: number } | null>(null)
  const prefetch = usePrefetchKgNode(pid)

  useEffect(() => {
    if (kindRequest) setKinds(new Set(kindRequest.kinds))
  }, [kindRequest])

  const nodes = graph?.nodes ?? NO_NODES
  const edges = graph?.edges ?? NO_EDGES
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const idByKey = useMemo(() => new Map(nodes.map((n) => [n.key, n.id])), [nodes])
  const focusId = focusKey ? idByKey.get(focusKey) ?? null : null

  // ── size ──
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight })
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── layouts ──
  const overview = useMemo(() => overviewLayout(nodes, edges, { kinds, nodeTypes: nodeTypesOn }), [nodes, edges, kinds, nodeTypesOn])
  const ego = useMemo(() => (focusId ? egoLayout(focusId, nodes, edges, depth) : null), [focusId, nodes, edges, depth])
  const positions = ego ? ego.positions : overview.positions
  const laidEdges: LaidEdge[] = useMemo(() => [...(ego ? ego.edges.values() : overview.edges.values())], [ego, overview])
  const adj = useMemo(() => adjacency(laidEdges), [laidEdges])
  const hover = useHoverStyle(wrapperRef, adj)

  const counts = useMemo(() => {
    const c: Record<string, number> = { concept: 0, role: 0, capability: 0, pattern: 0, app: 0, node_type: 0 }
    for (const n of nodes) {
      const k = asKind(n.kind)
      if (k === 'capability' && n.subkind === 'node_type') c.node_type++
      else c[k]++
    }
    return c
  }, [nodes])

  const parts = useMemo(() => {
    const m = new Map<string, number>()
    for (const e of edges) if (e.rel === 'composes') m.set(e.src_id, (m.get(e.src_id) ?? 0) + 1)
    return m
  }, [edges])

  // ── assembly (once per session per project) ──
  const [phase, setPhase] = useState<'wait' | 'run' | 'done'>(() => (motionOff() || reduced() || alreadyAssembled(pid) ? 'done' : 'wait'))
  const [plan, setPlan] = useState<AssemblyPlan | null>(null)
  const finishAssembly = useCallback(() => { setPhase('done'); setPlan(null) }, [])

  // ── transitions between views (focus, filters, rebuild) ──
  const prevRef = useRef<Map<string, Box> | null>(null)
  const transition = useMemo<Transition>(() => {
    const prev = prevRef.current
    if (!prev || prev.size === 0 || phase !== 'done') return NO_TRANSITION
    const moving: string[] = []
    for (const [id, b] of positions) { const p = prev.get(id); if (p && (p.x !== b.x || p.y !== b.y)) moving.push(id) }
    const crossfade = moving.length > kg.ego.maxFlip
    const order = ego ? new Map(ego.clockwise.map((id, i) => [id, i])) : null
    const flip = new Map<string, { dx: number; dy: number; delay: number }>()
    const enter = new Map<string, number>()
    let i = 0
    for (const [id, b] of positions) {
      const p = prev.get(id)
      if (!p) { enter.set(id, 0.3 + Math.min(i++, 30) * kg.ego.flipStagger); continue }
      if (crossfade || (p.x === b.x && p.y === b.y)) continue
      flip.set(id, { dx: p.x - b.x, dy: p.y - b.y, delay: (order?.get(id) ?? moving.indexOf(id)) * kg.ego.flipStagger })
    }
    return { seq: Math.random(), flip, enter, changed: flip.size > 0 || enter.size > 0 }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- recomputed only when the laid-out view changes
  }, [positions])

  const [leaving, setLeaving] = useState<{ id: string; box: Box }[]>([])
  useLayoutEffect(() => {
    const prev = prevRef.current
    let timer = 0
    if (prev && prev.size && phase === 'done') {
      const gone = [...prev].filter(([id]) => !positions.has(id)).map(([id, box]) => ({ id, box }))
      setLeaving(gone)
      if (gone.length) timer = window.setTimeout(() => setLeaving([]), 260)
    }
    if (positions.size) prevRef.current = positions
    return () => window.clearTimeout(timer)
  }, [positions, phase])

  // ── camera ──
  const fittedRef = useRef(false)
  const fit = useCallback((instant = false) => {
    if (!ready || !size.width) return
    const free = freeRect(size, !!focusId)
    const b = ego ? ego.bounds : overview.bounds ? { ...overview.bounds, y: overview.bounds.y - 28, h: overview.bounds.h + 28 } : null
    if (!b) return
    const vp = egoViewport(b, free, { maxZoom: ego ? 1.1 : 1, pad: ego ? 48 : 36 })
    const first = !fittedRef.current
    fittedRef.current = true
    void rf.setViewport(vp, { duration: instant || first || motionOff() ? 0 : 700, ease: easeInOutCubic, interpolate: 'linear' })
  }, [ready, size, focusId, ego, overview.bounds, rf])
  const fitRef = useRef(fit)
  useEffect(() => { fitRef.current = fit })
  const sized = size.width > 0
  useEffect(() => { fitRef.current() }, [positions, focusId, ready, sized])

  // ── assembly start ──
  useEffect(() => {
    if (phase !== 'wait' || !graph || !ready || !sized) return
    if (overview.positions.size === 0) { if (!loading) finishAssembly(); return }
    if (focusId) { finishAssembly(); return }
    markAssembled(pid)
    setPlan(planAssembly(overview, nodes, graph.truncated))
    setPhase('run')
  }, [phase, graph, ready, sized, overview, nodes, focusId, loading, pid, finishAssembly])
  useEffect(() => {
    if (phase !== 'run' || !plan) return
    const t = window.setTimeout(finishAssembly, (plan.total + 0.4) * 1000)
    return () => window.clearTimeout(t)
  }, [phase, plan, finishAssembly])

  // ── focus trail and pulse ──
  useEffect(() => {
    if (!focusKey) { setTrail([]); return }
    setTrail((t) => (t[t.length - 1] === focusKey ? t : [...t.filter((k) => k !== focusKey), focusKey].slice(-5)))
    if (focusId) setPulse((p) => ({ id: focusId, seq: (p?.seq ?? 0) + 1 }))
  }, [focusKey, focusId])
  useEffect(() => {
    if (!pulse) return
    const t = window.setTimeout(() => setPulse(null), 600)
    return () => window.clearTimeout(t)
  }, [pulse])

  // ── nodes for xyflow, with data kept stable per id ──
  const cache = useRef(new Map<string, { sig: string; data: KgNodeData }>())
  const flowNodes = useMemo<KgFlowNode[]>(() => {
    const mk = (id: string, box: Box, isLeaving: boolean): KgFlowNode | null => {
      const node = byId.get(id)
      if (!node) return null
      const kind = asKind(node.kind)
      const a = plan?.nodes.get(id)
      const flip = !isLeaving ? transition.flip.get(id) : undefined
      const enterDelay = !isLeaving ? transition.enter.get(id) : undefined
      const focused = id === focusId
      let construct: KgNodeData['construct'] = null
      let fade: KgNodeData['fade'] = null
      if (a) {
        if (a.mode === 'construct') construct = { mode: 'enter', delay: a.delay, scale: a.scale, key: `asm-${id}` }
        else fade = { delay: a.delay }
      } else if (enterDelay !== undefined) {
        if (kind === 'capability') fade = { delay: enterDelay }
        else construct = { mode: 'enter', delay: enterDelay, scale: 0.55, key: `ent-${id}-${transition.seq}` }
      } else if (pulse && pulse.id === id && !flip) construct = { mode: 'lock', delay: 0, scale: 1, key: `pulse-${pulse.seq}` }
      const teams = (() => {
        const ev = node.props.evidence
        if (!Array.isArray(ev)) return node.validated_count > 0 ? 1 : 0
        return new Set(ev.map((e) => (e as { project_label?: string }).project_label ?? '')).size
      })()
      const s = sizeOf(node)
      const flipD = flip ? { ...flip, key: `flip-${transition.seq}` } : null
      const sig = [node.updated_at, node.status, node.validated_count, focused, construct?.key, construct?.mode, fade?.delay, flipD?.key, isLeaving, parts.get(id), teams].join('|')
      const prev = cache.current.get(id)
      const data: KgNodeData = prev && prev.sig === sig ? prev.data : { node, kind, w: s.w, h: s.h, parts: parts.get(id) ?? 0, teams, focused, construct, fade, flip: flipD, leaving: isLeaving }
      cache.current.set(id, { sig, data })
      return { id, type: 'kg', position: { x: box.x, y: box.y }, width: box.w, height: box.h, data, draggable: false, selectable: false, connectable: false, focusable: false }
    }
    const out: KgFlowNode[] = []
    for (const [id, box] of positions) { const n = mk(id, box, false); if (n) out.push(n) }
    for (const l of leaving) if (!positions.has(l.id)) { const n = mk(l.id, l.box, true); if (n) out.push(n) }
    return out
  }, [positions, leaving, byId, focusId, plan, transition, pulse, parts])

  // ── edges: animation per view ──
  const edgeAnim = useMemo<ReadonlyMap<string, EdgeAnim> | null>(() => {
    if (plan) return plan.edges
    if (!transition.changed && !(ego && prevRef.current)) return null
    if (ego) {
      const m = new Map<string, EdgeAnim>()
      const order = ego.clockwise
      let comets = 0
      for (const e of ego.edges.values()) {
        const i = e.hop === 1 ? Math.max(0, order.indexOf(e.other)) : order.length + m.size
        const comet = e.hop === 1 && comets < kg.assemble.maxComets
        if (comet) comets++
        m.set(e.id, { delay: kg.ego.cometAt + i * kg.ego.cometGap, comet })
      }
      return m
    }
    const m = new Map<string, EdgeAnim>()
    let i = 0
    for (const e of overview.edges.values()) m.set(e.id, { delay: 0.35 + Math.min(i++ * 0.006, 0.5), comet: false })
    return m
  }, [plan, transition, ego, overview])

  // ── interaction ──
  const hoverTimer = useRef(0)
  const onEnter: NodeMouseHandler = useCallback((_, n) => {
    hover.enter(n.id)
    window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => prefetch(n.id), 150)
    const cap = captionRef.current
    if (!cap) return
    const node = byId.get(n.id)
    let text = ''
    if (ego) {
      const e = [...ego.edges.values()].find((x) => x.other === n.id)
      const a = e && byId.get(e.src)
      const b = e && byId.get(e.dst)
      if (e && a && b) text = sentence(e.rel, a, b, e.props)
    } else if (node) {
      const e = overview.edges.size ? [...overview.edges.values()].find((x) => x.src === n.id || x.dst === n.id) : undefined
      const a = e && byId.get(e.src)
      const b = e && byId.get(e.dst)
      text = e && a && b ? sentence(e.rel, a, b, e.props) : node.summary
    }
    cap.textContent = text
  }, [hover, prefetch, byId, ego, overview])
  const onLeave: NodeMouseHandler = useCallback(() => {
    hover.leave()
    window.clearTimeout(hoverTimer.current)
    if (captionRef.current) captionRef.current.textContent = ''
  }, [hover])
  const onClick: NodeMouseHandler = useCallback((_, n) => {
    const node = byId.get(n.id)
    if (node) onFocusKey(node.key)
  }, [byId, onFocusKey])
  useEffect(() => () => window.clearTimeout(hoverTimer.current), [])

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape' || ev.defaultPrevented) return
      const t = ev.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return
      if (phase === 'run') finishAssembly()
      else if (focusKey) onFocusKey(null)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [phase, focusKey, finishAssembly, onFocusKey])

  const toggleKind = (k: KgKind) => setKinds((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })

  const searchGroups: SearchMenuGroup[] = useMemo(() => {
    const g = new Map<KgKind, NodeView[]>()
    for (const n of nodes) { const k = asKind(n.kind); g.set(k, [...(g.get(k) ?? []), n]) }
    return FILTER_KINDS.filter((k) => g.has(k)).map((k) => ({
      label: KIND_LABEL[k].many[0].toUpperCase() + KIND_LABEL[k].many.slice(1),
      items: (g.get(k) as NodeView[]).sort((a, b) => a.label.localeCompare(b.label)).map((n) => {
        return { key: n.key, label: n.label, keywords: `${n.label} ${n.aliases.join(' ')}`, icon: <KindIcon kind={k} subkind={n.subkind} />, testId: `kg-search-item-${n.key.replaceAll(':', '-')}`, onSelect: () => { setSearchOpen(false); onFocusKey(n.key) } }
      }),
    }))
  }, [nodes, onFocusKey])

  // ── derived view bits ──
  const egoLabels = !!ego && ego.edges.size <= kg.ego.maxLabels
  const sectors = ego && !egoLabels ? ego.sectors.map((s) => ({ key: s.key, at: s.at, text: `${sectionTitle(s.rel, s.direction === 'out' ? 'out' : 'in')} · ${s.count}` })) : []
  const more = ego ? ego.more.map((m) => ({ key: m.kind, box: m.box, text: `+${m.count} more ${m.count === 1 ? KIND_LABEL[m.kind].one : KIND_LABEL[m.kind].many}` })) : []
  const viewKey = ego ? `ego-${ego.focus}-${depth}-${transition.seq}` : `ov-${transition.seq}`
  const assembling = phase === 'run'
  const groups = ego ? [] : (overview as OverviewLayout).groups
  const empty = !loading && graph && graph.nodes.length === 0

  return (
    <Tile
      editor
      className="relative h-[calc(100dvh-340px)] min-h-[560px] overflow-hidden"
      data-testid="kg-graph" data-assembling={assembling ? 'true' : 'false'} data-focus={focusKey ?? ''} data-nodes={flowNodes.length}
      onPointerDown={() => { if (assembling) finishAssembly() }}
    >
      <div ref={wrapperRef} className={cn('kg-flow absolute inset-0', phase === 'wait' && '[&_.react-flow__node]:opacity-0 [&_.kg-viewport-layer]:opacity-0')}>
        <ReactFlow
          nodes={flowNodes}
          edges={[]}
          nodeTypes={nodeTypes}
          onInit={() => setReady(true)}
          onNodeClick={onClick}
          onNodeMouseEnter={onEnter}
          onNodeMouseLeave={onLeave}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          zoomOnDoubleClick={false}
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={PRO}
          defaultViewport={{ x: 0, y: 0, zoom: 0.6 }}
          className="bg-transparent"
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} className="[&>pattern>circle]:fill-(--grid-dot)" />
          {ego && <OrbitGuide key={`o-${viewKey}`} rx={ego.ring.rx} ry={ego.ring.ry} />}
          <KgEdges key={`edges-${viewKey}`} edges={laidEdges} anim={edgeAnim} />
          {!ego && <GroupCaptions groups={groups} />}
          {ego && <EgoLabels key={`l-${viewKey}`} edges={[...ego.edges.values()].filter((e) => e.hop === 1)} labels={egoLabels} />}
          {ego && <SectorCaptions items={sectors} />}
          {ego && <MorePills items={more} onOpen={() => undefined} />}
          {assembling && <AssemblyDecor plate={overview.conceptBounds} />}
        </ReactFlow>
      </div>

      {/* toolbar */}
      <div className={cn('pointer-events-none absolute left-3 top-3 z-10 flex flex-wrap items-center gap-1.5', focusId ? 'right-[404px]' : 'right-3')}>
        {FILTER_KINDS.map((k) => (
          <FilterChip key={k} id={k} label={KIND_LABEL[k].many} n={counts[k]} pressed={kinds.has(k)} onClick={() => toggleKind(k)} />
        ))}
        <FilterChip id="node_type" label="node types" n={counts.node_type} pressed={nodeTypesOn} onClick={() => setNodeTypesOn((v) => !v)} />
        <div className="pointer-events-auto ml-auto flex items-center gap-2">
          {ego && (
            <Tip label="Also show what the neighbours connect to">
              <label className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <Switch checked={depth === 2} onCheckedChange={(v) => onDepth(v ? 2 : 1)} data-testid="kg-depth-2" aria-label="Two hops" /> Two hops
              </label>
            </Tip>
          )}
          <label className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <Switch checked={learnedOnly} onCheckedChange={onLearnedOnly} data-testid="kg-learned-only" aria-label="Learned only" /> Learned only
          </label>
          <Popover open={searchOpen} onOpenChange={setSearchOpen}>
            <PopoverTrigger data-testid="kg-search" className="flex h-7 w-44 items-center gap-2 rounded-ctl bg-recess px-2 text-left text-[12.5px] text-muted-foreground hover:bg-(--hover-fill)">
              <Search className="size-3.5" /> Search…
            </PopoverTrigger>
            <PopoverContent className="w-[320px] p-0" align="end">
              <SearchMenuList groups={searchGroups} placeholder="Find a concept, role or capability…" empty="Nothing matches" testId="kg-search-menu" />
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {/* focus trail */}
      {ego && (
        <div className="absolute top-12 left-3 z-10 flex flex-wrap items-center gap-1" data-testid="kg-focus-trail">
          <button type="button" onClick={() => onFocusKey(null)} data-testid="kg-overview" className="inline-flex h-6 items-center gap-1 rounded-chip bg-recess px-2 text-[12px] hover:bg-(--hover-fill)">
            <Network className="size-3" /> Overview
          </button>
          {trail.map((k) => (
            <span key={k} className="inline-flex items-center gap-1">
              <ChevronRight className="size-3 text-faint" aria-hidden />
              <button type="button" onClick={() => onFocusKey(k)} aria-current={k === focusKey} className={cn('inline-flex h-6 items-center rounded-chip px-2 text-[12px]', k === focusKey ? 'bg-raise shadow-raise-sm' : 'bg-recess hover:bg-(--hover-fill)')}>
                {nodes.find((n) => n.key === k)?.label ?? k}
              </button>
            </span>
          ))}
        </div>
      )}

      {/* caption and controls */}
      <div className="pointer-events-none absolute right-3 bottom-3 left-3 z-10 flex items-end justify-between gap-3" style={{ paddingRight: ego ? 392 : 0 }}>
        <p className="min-h-5 min-w-0 truncate rounded-chip px-1 text-[13px] text-foreground/85" data-testid="kg-relation-caption"><span ref={captionRef} /></p>
        <div className="pointer-events-auto flex shrink-0 items-center gap-1 rounded-ctl bg-recess p-0.5">
          <Button variant="ghost" size="icon-xs" aria-label="Zoom out" onClick={() => void rf.zoomOut({ duration: motionOff() ? 0 : 200 })} data-testid="kg-zoom-out"><Minus /></Button>
          <Button variant="ghost" size="icon-xs" aria-label="Zoom in" onClick={() => void rf.zoomIn({ duration: motionOff() ? 0 : 200 })} data-testid="kg-zoom-in"><Plus /></Button>
          <Button variant="ghost" size="icon-xs" aria-label="Fit to screen" onClick={() => fit(false)} data-testid="kg-fit"><Maximize2 /></Button>
        </div>
      </div>

      {graph?.truncated && <p className="absolute bottom-12 left-3 z-10 text-[12px] text-muted-foreground">Showing the first {count(graph.nodes.length, 'node')}. Search or focus to reach the rest.</p>}

      {loading && !graph && <div className="absolute inset-0 z-10 p-6"><Skeleton className="h-full w-full" /></div>}
      {!!error && !graph && <div className="absolute inset-0 z-10 flex items-center justify-center"><p className="text-[12.5px] text-destructive" role="alert">{errorMessage(error)}</p></div>}
      {empty && <div className="absolute inset-0 z-10 flex items-center justify-center"><EmptyState icon={<Network />} title="Nothing learned yet" data-testid="kg-empty">Rebuild knowledge to read this project, or keep a workflow to teach it something.</EmptyState></div>}

      {focusId && <NodeSheet key={focusId} pid={pid} nodeId={focusId} onFocusKey={onFocusKey} onClose={() => onFocusKey(null)} />}
    </Tile>
  )
}

function FilterChip({ id, label, n, pressed, onClick }: { id: string; label: string; n: number; pressed: boolean; onClick: () => void }) {
  const kind = (FILTER_KINDS as string[]).includes(id) ? (id as KgKind) : 'capability'
  return (
    <button
      type="button" aria-pressed={pressed} data-pressed={pressed ? '' : undefined} onClick={onClick} data-testid={`kg-filter-${id}`}
      className={cn(
        'pointer-events-auto inline-flex h-7 items-center gap-1.5 rounded-ctl px-2.5 text-[12.5px] font-medium transition-colors duration-(--d-fast) focus-visible:shadow-focus',
        pressed ? 'bg-raise text-foreground shadow-raise-sm' : 'bg-recess text-muted-foreground hover:text-foreground',
      )}
    >
      <KindIcon kind={kind} subkind={id === 'node_type' ? 'node_type' : null} className={cn('size-3.5', pressed && hueText[kindHue(kind, id === 'node_type' ? 'node_type' : null)])} />
      <span>{label[0].toUpperCase() + label.slice(1)}</span>
      <span className="text-[11.5px] tabular-nums opacity-60">{n}</span>
    </button>
  )
}
