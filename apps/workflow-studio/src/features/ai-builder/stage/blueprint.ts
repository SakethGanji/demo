/**
 * Blueprint (PLAN-MOTION-UI §3.2): the planned steps drawn as dashed outline tiles before the engine builds them.
 * Pure layout; `BlueprintNode` (Builder B) renders them. A real node replaces its outline when a round names it.
 */
import type { Node } from '@xyflow/react'
import type { SpecStep } from '../types'
import type { XY } from './buildOps'

export const BLUEPRINT_PREFIX = 'bp:'
/**
 * The engine's layout constants (`workflow-engine/src/services/auto_layout.py`: X_STEP, Y_STEP, MAX_COLS, BAND_GAP).
 * The outlines use the same rules, so each one solidifies exactly where its real node lands. Keep the two in step.
 */
export const BLUEPRINT_GAP = 190
export const BLUEPRINT_ROW_GAP = 110
export const BLUEPRINT_MAX_COLS = 7
export const BLUEPRINT_BAND_GAP = 70
export const BLUEPRINT_NODE_TYPE = 'blueprintNode'

export interface BlueprintData extends Record<string, unknown> {
  name: string
  nodeType: string
  purpose: string
  /** set by the player when the real node lands: the outline fades out under it */
  dissolve?: boolean
}

export const blueprintId = (stepName: string) => `${BLUEPRINT_PREFIX}${stepName}`
export const isBlueprintId = (id: string) => id.startsWith(BLUEPRINT_PREFIX)
export const stepOfBlueprint = (id: string) => id.slice(BLUEPRINT_PREFIX.length)

const BRANCHING_TYPES = new Set(['If'])

/**
 * Where the engine will put each step of a NEW workflow: the plan's order, linear, except that an `If` is followed by two
 * parallel branches (true above false) that join again on the step after them. Returns `[column, slot]` per step; the
 * column wraps every `BLUEPRINT_MAX_COLS` layers into a band below, as the engine does.
 */
export function planGrid(types: string[]): { layer: number; slot: number }[] {
  const out: { layer: number; slot: number }[] = []
  let layer = 0
  for (let i = 0; i < types.length; i++) {
    out.push({ layer, slot: 0 })
    if (BRANCHING_TYPES.has(types[i]) && i + 2 < types.length) {
      out.push({ layer: layer + 1, slot: 0 })
      out.push({ layer: layer + 1, slot: 1 })
      i += 2
      layer += 2
    } else layer += 1
  }
  return out
}

/** Flow position of a grid cell, identical to `auto_layout.auto` for the same layers. */
export function gridPositions(grid: { layer: number; slot: number }[]): XY[] {
  const bands = Math.floor(Math.max(0, ...grid.map((g) => g.layer)) / BLUEPRINT_MAX_COLS) + 1
  const top: number[] = [0]
  for (let b = 1; b < bands; b++) {
    const tallest = Math.max(1, ...grid.filter((g) => Math.floor(g.layer / BLUEPRINT_MAX_COLS) === b - 1).map((g) => g.slot + 1))
    top[b] = top[b - 1] + BLUEPRINT_ROW_GAP * tallest + BLUEPRINT_BAND_GAP
  }
  return grid.map((g) => ({
    x: BLUEPRINT_GAP * (g.layer % BLUEPRINT_MAX_COLS),
    y: top[Math.floor(g.layer / BLUEPRINT_MAX_COLS)] + BLUEPRINT_ROW_GAP * g.slot,
  }))
}

/**
 * Outline nodes for `steps`, in plan order. A new workflow (`existing` empty) uses the engine's own layout rules; a modify
 * places only the steps that are not on the draft yet, in a row to the right of the draft.
 */
export function blueprintNodes(
  steps: Pick<SpecStep, 'name' | 'node_type' | 'purpose'>[],
  existing: { id: string; position: XY }[] = [],
): Node<BlueprintData>[] {
  const have = new Set(existing.map((n) => n.id))
  const fresh = steps.filter((s, i) => !have.has(s.name) && steps.findIndex((o) => o.name === s.name) === i)
  const make = (s: (typeof fresh)[number], position: XY): Node<BlueprintData> => ({
    id: blueprintId(s.name),
    type: BLUEPRINT_NODE_TYPE,
    position,
    data: { name: s.name, nodeType: s.node_type, purpose: s.purpose ?? '' },
    draggable: false, selectable: false, connectable: false, deletable: false, focusable: false,
  })
  if (existing.length) {
    const x0 = Math.max(...existing.map((n) => n.position.x)) + BLUEPRINT_GAP
    return fresh.map((s, i) => make(s, { x: x0 + i * BLUEPRINT_GAP, y: existing[0].position.y }))
  }
  const at = gridPositions(planGrid(fresh.map((s) => s.node_type)))
  return fresh.map((s, i) => make(s, at[i]))
}
