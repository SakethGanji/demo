/**
 * React Flow drops a node's measurement when a new node object arrives without `measured`, and that node renders
 * `visibility: hidden` for a frame (PLAN-MOTION-UI §1.4). Before the stage hands the canvas back to the reloaded
 * draft, copy the measurements over by id.
 */
import type { Node } from '@xyflow/react'

export function carryMeasured<T extends Node>(target: T[], source: Node[]): T[] {
  const sizes = new Map<string, NonNullable<Node['measured']>>()
  for (const n of source) if (n.measured?.width && n.measured?.height) sizes.set(n.id, n.measured)
  return target.map((n) => {
    if (n.measured?.width && n.measured?.height) return n
    const m = sizes.get(n.id)
    return m ? { ...n, measured: { ...m } } : n
  })
}
