import { describe, expect, it } from 'vitest'
import { autoLayout } from './autoLayout'

const n = (id: string, height?: number) => ({ id, height })
const e = (source: string, target: string) => ({ source, target })

describe('autoLayout', () => {
  it('places nodes left to right by longest-path depth', () => {
    // a -> b -> d, a -> d (d is depth 2, not 1), c is a second root
    const p = autoLayout([n('a'), n('b'), n('c'), n('d')], [e('a', 'b'), e('b', 'd'), e('a', 'd')])
    expect(p.a.x).toBe(0)
    expect(p.c.x).toBe(0)
    expect(p.b.x).toBeGreaterThan(p.a.x)
    expect(p.d.x).toBeGreaterThan(p.b.x)
  })
  it('spaces branches vertically and centres them', () => {
    const p = autoLayout([n('a'), n('t'), n('f')], [e('a', 't'), e('a', 'f')])
    expect(p.t.x).toBe(p.f.x)
    expect(p.f.y).toBeGreaterThanOrEqual(p.t.y + 80)
    expect(p.t.y + p.f.y + 80).toBeCloseTo(p.a.y * 2 + 80)
  })
  it('respects node heights', () => {
    const p = autoLayout([n('a'), n('t', 200), n('f')], [e('a', 't'), e('a', 'f')])
    expect(p.f.y - p.t.y).toBeGreaterThanOrEqual(200)
  })
  it('is deterministic', () => {
    const nodes = [n('a'), n('b'), n('c'), n('d')]
    const edges = [e('a', 'b'), e('a', 'c'), e('b', 'd'), e('c', 'd')]
    expect(autoLayout(nodes, edges)).toEqual(autoLayout(nodes, edges))
  })
  it('does not crash on cycles, self loops, unknown ids and disconnected nodes', () => {
    const p = autoLayout([n('a'), n('b'), n('c'), n('lone')], [e('a', 'b'), e('b', 'c'), e('c', 'a'), e('a', 'a'), e('a', 'zzz')])
    expect(Object.keys(p).sort()).toEqual(['a', 'b', 'c', 'lone'])
    for (const v of Object.values(p)) expect(Number.isFinite(v.x) && Number.isFinite(v.y)).toBe(true)
  })
  it('handles empty input', () => {
    expect(autoLayout([], [])).toEqual({})
  })
})
