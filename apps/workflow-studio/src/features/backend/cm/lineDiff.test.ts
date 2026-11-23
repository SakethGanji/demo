import { describe, expect, it } from 'vitest'
import { lineDiff } from './lineDiff'

describe('lineDiff', () => {
  it('reports added and changed lines (1-based) and removed count', () => {
    const r = lineDiff('a\nb\nc', 'a\nB\nc\nd')
    expect(r.added).toEqual([2, 4])
    expect(r.addedCount).toBe(2)
    expect(r.removedCount).toBe(1)
  })
  it('is empty for identical text', () => {
    expect(lineDiff('x\ny', 'x\ny')).toEqual({ added: [], addedCount: 0, removedCount: 0 })
  })
  it('handles a draft into an empty module', () => {
    expect(lineDiff('', 'a\nb').added).toEqual([1, 2])
  })
})
