/** The first-load assembly plan (PLAN-CAPGRAPH-UI §2.2): who constructs when. Pure; the page plays it. */
import { kg } from '../kit/beats'
import { asKind, type KgKind } from '../kit/kinds'
import type { NodeView } from '../types'
import type { LaidEdge, OverviewLayout } from './layout'

export interface NodePlan { mode: 'construct' | 'fade'; delay: number; scale: number }
export interface EdgePlan { delay: number; comet: boolean }
export interface AssemblyPlan { nodes: Map<string, NodePlan>; edges: Map<string, EdgePlan>; compact: boolean; total: number }

export function planAssembly(layout: OverviewLayout, nodes: readonly NodeView[], truncated: boolean): AssemblyPlan {
  const a = kg.assemble
  const kindOf = new Map(nodes.map((n) => [n.id, asKind(n.kind)]))
  const ids = [...layout.positions.keys()]
  const compact = ids.length > a.compactAbove || truncated
  const plan = new Map<string, NodePlan>()
  const reading = (k: KgKind[]) =>
    ids.filter((id) => k.includes(kindOf.get(id) as KgKind)).sort((p, q) => {
      const A = layout.positions.get(p)!, B = layout.positions.get(q)!
      return A.y - B.y || A.x - B.x || p.localeCompare(q)
    })

  if (compact) {
    const batch: Record<KgKind, number> = { concept: 0, role: 1, capability: 2, pattern: 3, app: 3 }
    for (const id of ids) plan.set(id, { mode: 'fade', delay: batch[kindOf.get(id) as KgKind] * 0.12, scale: 1 })
    const edges = new Map<string, EdgePlan>()
    for (const e of layout.edges.values()) edges.set(e.id, { delay: 0.5, comet: false })
    return { nodes: plan, edges, compact, total: 0.9 }
  }

  reading(['concept']).forEach((id, i) => plan.set(id, { mode: 'construct', delay: 0.12 + Math.min(i * a.conceptStagger, 0.9), scale: 0.55 }))
  reading(['role']).forEach((id, i) => plan.set(id, { mode: 'construct', delay: 0.6 + i * a.roleStagger, scale: 0.55 }))
  const caps = reading(['capability'])
  const step = Math.min(a.capStaggerMax, a.capWindow / Math.max(1, caps.length))
  caps.forEach((id, i) => plan.set(id, { mode: 'fade', delay: 0.6 + i * step, scale: 1 }))
  reading(['pattern', 'app']).forEach((id, i) => plan.set(id, { mode: 'construct', delay: a.topAt + i * 0.05, scale: 1 }))

  const edges = new Map<string, EdgePlan>()
  let comets = 0
  const list: LaidEdge[] = [...layout.edges.values()]
  list.forEach((e, i) => {
    const ks = [kindOf.get(e.src), kindOf.get(e.dst)]
    const wants = ks.some((k) => k === 'pattern' || k === 'app' || k === 'role')
    const comet = wants && comets < a.maxComets
    if (comet) comets++
    edges.set(e.id, { delay: a.edgesAt + Math.min(i * 0.01, 1.2), comet })
  })
  return { nodes: plan, edges, compact, total: a.maxTotal }
}
