import { describe, expect, it } from 'vitest'
import { arrange, filterFunctions, neighborhood } from './layout'
import type { CodeMap, MapFunction } from './types'
const fn = (id: string, line: number): MapFunction => ({
  id,
  name: id,
  label: id,
  line,
  end_line: line + 1,
  async: false,
  parameters: [],
  routes: [],
  calls: [],
  secret_references: [],
  controls: [],
  custom_scopes: [],
  other_call_count: 0,
})
const functions = [fn('entry', 1), fn('helper', 3), fn('storage', 5), fn('unrelated', 7)]
const edges = [
  { from: 'entry', to: 'helper', call_id: 'e1', line: 2 },
  { from: 'helper', to: 'storage', call_id: 'e2', line: 4 },
]
const model: CodeMap = { valid: true, source_hash: 'test', functions, edges, analysis: '' }
describe('code-map projection', () => {
  it('follows direction and hop limit without unrelated nodes', () => {
    expect([...neighborhood(model, 'entry', 'dependencies', 1)]).toEqual(['entry', 'helper'])
    expect([...neighborhood(model, 'entry', 'dependencies', 2)]).toEqual(['entry', 'helper', 'storage'])
    expect([...neighborhood(model, 'storage', 'callers', 1)]).toEqual(['storage', 'helper'])
  })
  it('condenses recursive components, places every function and avoids overlap', () => {
    const recursive = [...edges, { from: 'storage', to: 'helper', call_id: 'cycle', line: 6 }]
    const heights = new Map(functions.map((f) => [f.id, 300]))
    const result = arrange(functions, recursive, heights, true)
    expect(result.positions.size).toBe(4)
    expect(result.positions.get('helper')!.x).toBe(result.positions.get('storage')!.x)
    expect(Math.abs(result.positions.get('helper')!.y - result.positions.get('storage')!.y)).toBeGreaterThanOrEqual(345)
    expect(result.positions.get('entry')!.x).toBeLessThan(result.positions.get('helper')!.x)
  })
  it('intersects search, scope and type filters and supports zero results', () => {
    const connector = { ...functions[2], secret_references: ['DB_PASSWORD'] }
    const m = { ...model, functions: [...functions.slice(0, 2), connector] }
    expect(filterFunctions(m, null, 'storage', 'secret').map((f) => f.id)).toEqual(['storage'])
    expect(filterFunctions(m, new Set(['entry']), 'storage', 'secret')).toEqual([])
    expect(arrange([], [], new Map(), true).positions.size).toBe(0)
  })
})
