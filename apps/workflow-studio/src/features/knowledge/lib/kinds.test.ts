import { describe, expect, it } from 'vitest'
import { KIND_ORDER, kindGroup, kindLabel, kindStyle, relPhrase, relStyle, shapePath, subkindLabel } from './kinds'

describe('kinds', () => {
  it('maps known kinds and folds the rest into other', () => {
    expect(kindGroup('concept')).toBe('concept')
    expect(kindGroup('surprise')).toBe('other')
    expect(kindStyle('pattern').color).toBe('var(--viz-3)')
    expect(new Set(KIND_ORDER.map((k) => kindStyle(k).color)).size).toBe(KIND_ORDER.length)
  })

  it('labels kinds with their subkind', () => {
    expect(kindLabel('capability', 'node_type')).toBe('Capability · Node type')
    expect(kindLabel('concept')).toBe('Concept')
    expect(kindLabel('data_source', 'some_thing')).toBe('Data source · Some thing')
    expect(subkindLabel(null)).toBe('')
  })
})

describe('relations', () => {
  it('styles known relations and dashes unknown ones', () => {
    expect(relStyle('implements').dash).toBe('')
    expect(relStyle('requires').dash).not.toBe('')
    expect(relStyle('blocks_on')).toEqual({ label: 'Blocks on', dash: '3 3', width: 1 })
  })

  it('reads from either end', () => {
    expect(relPhrase('implements', 'in')).toBe('Implemented by')
    expect(relPhrase('composes', 'out')).toBe('Uses')
    expect(relPhrase('blocks_on', 'in')).toBe('Blocks on (incoming)')
  })
})

describe('shapePath', () => {
  it('draws a closed path for every shape', () => {
    for (const s of ['circle', 'square', 'hexagon', 'diamond', 'pill'] as const) {
      const d = shapePath(s, 8)
      expect(d.startsWith('M')).toBe(true)
      expect(d.endsWith('Z')).toBe(true)
      expect(d).not.toMatch(/NaN/)
    }
  })
})
