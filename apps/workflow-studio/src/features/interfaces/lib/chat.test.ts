import { describe, expect, it } from 'vitest'
import { lastSeq, mergeMessages } from './chat'
import type { ChatMessage } from './types'

const m = (seq: number, content = 'x'): ChatMessage => ({ seq, role: 'user', content, created_at: '' })

describe('chat merge', () => {
  it('dedupes by seq, keeps ascending order, later copy wins', () => {
    const out = mergeMessages([m(1), m(2, 'old')], [m(2, 'new'), m(3)])
    expect(out.map((x) => x.seq)).toEqual([1, 2, 3])
    expect(out[1].content).toBe('new')
  })
  it('lastSeq of empty is 0', () => {
    expect(lastSeq([])).toBe(0)
    expect(lastSeq([m(4), m(2)])).toBe(4)
  })
})
