/**
 * The 3D knowledge graph's React shell, and the entry point of its lazy chunk (`React.lazy(() => import(...))` in
 * `Graph3DStage`), so three.js only loads when the 3D view opens. It lays the graph out in 3D (pure, deterministic,
 * warm-started from the last layout so a refetch barely moves anything), owns one `KgScene`, and forwards props to
 * it. Construction failure (no WebGL) or a lost context calls `onFail`, and the page falls back to the 2D graph.
 */
import { useEffect, useRef } from 'react'
import type { Emphasis, GraphModel, Visible } from '../lib/graph'
import type { Graph3DApi } from '../lib/graph3d'
import { kindGroup } from '../lib/kinds'
import { runLayout3d, type Positions3 } from '../lib/layout3d'
import { KgScene } from './KgScene'

export interface Graph3DProps {
  model: GraphModel
  visible: Visible
  em: Emphasis
  selected: string | null
  hovered: string | null
  off: boolean
  /** team scope: cluster each project's nodes into its own constellation */
  byProject: boolean
  /** team scope: node id -> project colour (the halo) */
  orbit?: ReadonlyMap<string, string>
  /** a change re-frames the camera on the whole graph (learned-only or grouping flipped) */
  layoutKey: string
  /** px of the canvas covered by the detail panel on the right */
  inset: number
  onHover: (id: string | null) => void
  onPick: (id: string) => void
  onBackground: () => void
  onFail: () => void
  apiRef: React.MutableRefObject<Graph3DApi | null>
}

export default function Graph3D(props: Graph3DProps) {
  const { model, visible, em, selected, hovered, off, byProject, orbit, layoutKey, inset, apiRef } = props
  const hostRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<KgScene | null>(null)
  const prevLayout = useRef<Positions3 | null>(null)
  const cbs = useRef(props)
  useEffect(() => {
    cbs.current = props
  })

  // one scene per mount
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let scene: KgScene
    try {
      scene = new KgScene(host, {
        onHover: (id) => cbs.current.onHover(id),
        onPick: (id) => cbs.current.onPick(id),
        onBackground: () => cbs.current.onBackground(),
        onLost: () => cbs.current.onFail(),
      })
    } catch {
      cbs.current.onFail()
      return
    }
    sceneRef.current = scene
    apiRef.current = { fit: (ids) => scene.fit(ids), zoom: (f) => scene.zoom(f) }
    return () => {
      apiRef.current = null
      sceneRef.current = null
      scene.dispose()
    }
  }, [apiRef])

  // motion first, so the first layout already knows whether to tween
  useEffect(() => {
    sceneRef.current?.setMotion(off)
  }, [off])

  // the layout: the whole graph the server sent (filters only hide), warm-started from the last one
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    const nodes = model.nodes.map((n) => ({
      id: n.id,
      group: kindGroup(n.kind),
      r: model.radius.get(n.id) ?? 5,
      ...(byProject ? { cluster: n.project_id ?? null } : {}),
    }))
    const edges = model.edges.map((e) => ({ src: e.src_id, dst: e.dst_id }))
    const pos = runLayout3d(nodes, edges, { initial: prevLayout.current ?? undefined })
    prevLayout.current = pos
    scene.setGraph(model, pos, layoutKey)
  }, [model, byProject, layoutKey])

  useEffect(() => {
    sceneRef.current?.setVisible(visible)
  }, [visible, model])

  useEffect(() => {
    sceneRef.current?.setEmphasis(em)
  }, [em])

  useEffect(() => {
    sceneRef.current?.setHovered(hovered)
  }, [hovered])

  useEffect(() => {
    sceneRef.current?.setSelected(selected)
  }, [selected, model])

  useEffect(() => {
    sceneRef.current?.setOrbit(orbit)
  }, [orbit])

  useEffect(() => {
    sceneRef.current?.setInset(inset)
  }, [inset])

  return <div ref={hostRef} className="absolute inset-0 overflow-hidden" aria-hidden data-testid="kg-3d" />
}
