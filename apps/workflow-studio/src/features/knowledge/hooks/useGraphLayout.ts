/**
 * Lays the graph out and animates it settling. The final positions are computed in one go (layout.ts is pure and
 * fast), then the drawn positions tween there from where they were (or from the seeds on the first load), so the
 * constellation visibly gathers. A new graph (rebuild, learned-only) warm-starts from the last layout, so known
 * nodes barely move. With motion off it jumps straight to the end. `byProject` (team scope) clusters each project's
 * nodes round its own anchor; flipping it re-lays out from where the nodes are, so they visibly drift into place.
 */
import { useEffect, useRef, useState } from 'react'
import { kindGroup } from '../lib/kinds'
import type { GraphModel } from '../lib/graph'
import { lerpPositions, runLayout, seedPositions, type Positions } from '../lib/layout'
import { easeOut } from '../lib/viewport'

const FIRST_MS = 1100
const NEXT_MS = 700

export interface GraphLayout {
  /** what to draw this frame */
  pos: Positions | null
  /** where everything ends up: fit-to-view uses this so the camera never chases the tween */
  final: Positions | null
  settling: boolean
  /** the model `final` belongs to (lags one frame behind a new model) */
  of: GraphModel | null
  /** whether `final` is the project-clustered layout */
  grouped: boolean
}

export function useGraphLayout(model: GraphModel | null, off: boolean, byProject = false): GraphLayout {
  const [state, setState] = useState<GraphLayout>({ pos: null, final: null, settling: false, of: null, grouped: false })
  const finalRef = useRef<Positions | null>(null)
  const shownRef = useRef<Positions | null>(null)

  useEffect(() => {
    if (!model) return
    const nodes = model.nodes.map((n) => ({
      id: n.id,
      group: kindGroup(n.kind),
      r: model.radius.get(n.id) ?? 5,
      ...(byProject ? { cluster: n.project_id ?? null } : {}),
    }))
    const edges = model.edges.map((e) => ({ src: e.src_id, dst: e.dst_id }))
    const prev = finalRef.current
    const final = runLayout(nodes, edges, { initial: prev ?? undefined })
    const from = shownRef.current ?? seedPositions(nodes, edges)
    finalRef.current = final
    const ms = prev ? NEXT_MS : FIRST_MS
    let raf = 0
    let t0 = -1
    const step = (now: number) => {
      if (t0 < 0) t0 = now
      const u = off ? 1 : easeOut((now - t0) / ms)
      const pos = u >= 1 ? final : lerpPositions(from, final, u)
      shownRef.current = pos
      setState({ pos, final, settling: u < 1, of: model, grouped: byProject })
      if (u < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [model, off, byProject])

  return state
}
