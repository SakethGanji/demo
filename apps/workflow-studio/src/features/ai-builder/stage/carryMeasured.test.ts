import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import { carryMeasured } from './carryMeasured'

const node = (id: string, measured?: { width: number; height: number }): Node => ({ id, position: { x: 0, y: 0 }, data: {}, ...(measured ? { measured } : {}) })

describe('carryMeasured', () => {
  it('copies measured by id into nodes that have none', () => {
    const out = carryMeasured([node('A'), node('B'), node('C')], [node('A', { width: 200, height: 60 }), node('B', { width: 10, height: 20 })])
    expect(out.map((n) => n.measured)).toEqual([{ width: 200, height: 60 }, { width: 10, height: 20 }, undefined])
  })
  it('keeps a measurement the target already has, and does not alias the source', () => {
    const src = [node('A', { width: 1, height: 1 })]
    const out = carryMeasured([node('A', { width: 9, height: 9 }), node('B')], src)
    expect(out[0].measured).toEqual({ width: 9, height: 9 })
    const again = carryMeasured([node('A')], src)
    expect(again[0].measured).toEqual({ width: 1, height: 1 })
    expect(again[0].measured).not.toBe(src[0].measured)
  })
  it('ignores empty measurements and does not touch the inputs', () => {
    const target = [node('A')]
    const out = carryMeasured(target, [node('A', { width: 0, height: 0 })])
    expect(out[0].measured).toBeUndefined()
    expect(target[0]).not.toHaveProperty('measured')
    expect(out[0].id).toBe('A')
  })
})
