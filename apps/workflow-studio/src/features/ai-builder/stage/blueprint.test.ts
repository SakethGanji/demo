import { describe, expect, it } from 'vitest'
import { BLUEPRINT_GAP, BLUEPRINT_MAX_COLS, BLUEPRINT_ROW_GAP, blueprintId, blueprintNodes, isBlueprintId, stepOfBlueprint } from './blueprint'

const steps = ['Receive', 'Check', 'Pay'].map((name) => ({ name, node_type: 'Set', purpose: `${name} purpose` }))

describe('blueprintNodes', () => {
  it('lays a new workflow out left to right, one gap apart, from x = 0 like the engine', () => {
    const nodes = blueprintNodes(steps)
    expect(nodes.map((n) => n.position.x)).toEqual([0, BLUEPRINT_GAP, 2 * BLUEPRINT_GAP])
    expect(new Set(nodes.map((n) => n.position.y))).toEqual(new Set([0]))
    expect(nodes.map((n) => n.id)).toEqual(['bp:Receive', 'bp:Check', 'bp:Pay'])
    expect(nodes[0]).toMatchObject({ type: 'blueprintNode', data: { name: 'Receive', nodeType: 'Set', purpose: 'Receive purpose' }, draggable: false })
  })
  it('a modify outlines only the missing steps, right of the draft', () => {
    const nodes = blueprintNodes(steps, [{ id: 'Receive', position: { x: 0, y: 40 } }, { id: 'Check', position: { x: 300, y: 40 } }])
    expect(nodes.map((n) => n.id)).toEqual(['bp:Pay'])
    expect(nodes[0].position).toEqual({ x: 300 + BLUEPRINT_GAP, y: 40 })
  })
  it('one step is centred on zero, none is empty, duplicates collapse', () => {
    expect(blueprintNodes(steps.slice(0, 1))[0].position.x).toBeCloseTo(0)
    expect(blueprintNodes([])).toEqual([])
    expect(blueprintNodes([steps[0], steps[0]])).toHaveLength(1)
  })
  it('stacks the two branches after an If and joins them, as the engine does', () => {
    const plan = ['Webhook', 'Set', 'If', 'Set', 'Set', 'Set', 'Respond'].map((node_type, i) => ({ name: `S${i}`, node_type, purpose: '' }))
    const p = blueprintNodes(plan).map((n) => n.position)
    expect(p.map((q) => q.x / BLUEPRINT_GAP)).toEqual([0, 1, 2, 3, 3, 4, 5])
    expect(p[4].y - p[3].y).toBe(BLUEPRINT_ROW_GAP)
    expect(p[5].y).toBe(0)
  })
  it('puts every outline where the engine put the real node (the showcase refund approval, round_shape of the live build)', () => {
    const plan = [['Receive', 'Webhook'], ['Normalise', 'Set'], ['Over limit?', 'If'], ['Send', 'Set'], ['Pay', 'Set'], ['Log', 'Set'], ['Reply', 'Respond']]
      .map(([name, node_type]) => ({ name, node_type, purpose: '' }))
    expect(blueprintNodes(plan).map((n) => n.position)).toEqual([
      { x: 0, y: 0 }, { x: 190, y: 0 }, { x: 380, y: 0 }, { x: 570, y: 0 }, { x: 570, y: 110 }, { x: 760, y: 0 }, { x: 950, y: 0 },
    ])
  })
  it('wraps a chain longer than one band into a band below', () => {
    const many = Array.from({ length: BLUEPRINT_MAX_COLS + 2 }, (_, i) => ({ name: `S${i}`, node_type: 'Set', purpose: '' }))
    const nodes = blueprintNodes(many)
    expect(nodes[BLUEPRINT_MAX_COLS].position.x).toBe(0)
    expect(nodes[BLUEPRINT_MAX_COLS].position.y).toBeGreaterThan(nodes[0].position.y)
    expect(nodes[BLUEPRINT_MAX_COLS - 1].position.y).toBe(0)
  })
  it('ids round-trip', () => {
    expect(isBlueprintId(blueprintId('X'))).toBe(true)
    expect(isBlueprintId('X')).toBe(false)
    expect(stepOfBlueprint(blueprintId('Over limit?'))).toBe('Over limit?')
  })
})
