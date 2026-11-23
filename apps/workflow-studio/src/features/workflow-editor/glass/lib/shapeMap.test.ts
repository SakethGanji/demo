import { describe, expect, it } from 'vitest'
import type { ApiExecutionDetail } from '@/shared/lib/api'
import { buildPageModel, toPageWorkflow } from './pageModel'
import { runPath } from './runPath'
import { buildShapeMap } from './shapeMap'

const n = (name: string, type = 'Set', parameters: Record<string, unknown> = {}) => ({ name, type, parameters })
const c = (s: string, t: string, port = 'main') => ({ source_node: s, source_output: port, target_node: t, target_input: 'main' })

const wf = toPageWorkflow({
  nodes: [n('T', 'Webhook'), n('D', 'If'), n('A'), n('B'), n('M'), n('L', 'Loop'), n('X'), n('S'), n('P1'), n('P2'), n('J', 'Merge'), n('Halt', 'StopAndError')],
  connections: [
    c('T', 'D'),
    c('D', 'A', 'true'),
    c('D', 'B', 'false'),
    c('B', 'Halt'),
    c('A', 'M'),
    c('M', 'L'),
    c('L', 'X', 'loop'),
    c('X', 'L'),
    c('L', 'S', 'done'),
    c('S', 'P1'),
    c('S', 'P2'),
    c('P1', 'J'),
    c('P2', 'J'),
  ],
})
const model = buildPageModel(wf)

describe('buildShapeMap', () => {
  it('draws every main-chain step once, choices as diamonds, Stop as a square, loops as frames', () => {
    const m = buildShapeMap(model, null)
    const steps = m.nodes.filter((x) => x.kind !== 'dot').map((x) => x.node)
    expect(steps.sort()).toEqual(['A', 'B', 'D', 'Halt', 'J', 'M', 'P1', 'P2', 'S', 'T', 'X'].sort())
    expect(m.nodes.find((x) => x.node === 'D')?.kind).toBe('diamond')
    expect(m.nodes.find((x) => x.node === 'Halt')?.kind).toBe('stop')
    expect(m.frames.map((f) => [f.node, f.text])).toEqual([['L', 'FOR EACH']])
    expect(m.labels.map((l) => l.text)).toEqual(['YES', 'NO'])
    // nothing is green without a run; everything fits the 200-wide box
    expect(m.nodes.every((x) => !x.on) && m.edges.every((e) => !e.on)).toBe(true)
    expect(m.nodes.every((x) => x.x >= 0 && x.x + x.w <= m.width)).toBe(true)
  })

  it('lays cases and lanes side by side, top to bottom in page order', () => {
    const m = buildShapeMap(model, null)
    const at = (k: string) => m.nodes.find((x) => x.node === k)!
    expect(at('A').x).toBeLessThan(at('B').x)
    expect(at('A').y).toBe(at('B').y)
    expect(at('P1').y).toBe(at('P2').y)
    expect(at('T').y).toBeLessThan(at('D').y)
    expect(at('D').y).toBeLessThan(at('A').y)
    expect(at('X').y).toBeLessThan(at('S').y)
    expect(m.height).toBeGreaterThan(at('J').y)
  })

  it('the run path is on: the taken case and the steps that ran', () => {
    const e = {
      id: 'r',
      status: 'success',
      start_time: '2026-10-24T10:00:00Z',
      end_time: '2026-10-24T10:00:01Z',
      node_metrics: { T: {}, D: { activeOutputs: ['true'] }, A: {}, M: {} },
      node_data: { T: [{ json: {} }], D: [{ json: {} }], A: [{ json: {} }], M: [{ json: {} }] },
      errors: [],
    } as unknown as ApiExecutionDetail
    const m = buildShapeMap(model, runPath(wf.connections, wf.nodes.map((x) => x.name), e))
    expect(m.nodes.filter((x) => x.on).map((x) => x.node)).toEqual(['T', 'D', 'A', 'M'])
    expect(m.labels.find((l) => l.text === 'YES')?.on).toBe(true)
    expect(m.labels.find((l) => l.text === 'NO')?.on).toBe(false)
    expect(m.edges.some((x) => x.on)).toBe(true)
  })
})
