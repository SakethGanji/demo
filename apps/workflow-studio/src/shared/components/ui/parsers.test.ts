import { describe, expect, it } from 'vitest'
import { diffLines } from './diff-view'
import { parseTraceback } from './stack-trace'
import { lineDiff } from '@/features/backend/cm/lineDiff'

describe('diffLines', () => {
  it('marks added, removed and unchanged lines with their line numbers', () => {
    const prev = 'a\nb\nc\nd'
    const next = 'a\nB\nc\nd\ne'
    const out = diffLines(prev, next)
    expect(out.map((l) => `${l.kind}:${l.text}`)).toEqual(['same:a', 'del:b', 'add:B', 'same:c', 'same:d', 'add:e'])
    expect(out.find((l) => l.text === 'e')?.b).toBe(5)
    expect(out.find((l) => l.text === 'b')?.a).toBe(2)
  })
  it('agrees with lineDiff on the counts', () => {
    const prev = 'def f():\n    return 1\n\nx = 2\n'
    const next = 'def f():\n    return 2\n\nx = 2\ny = 3\n'
    const out = diffLines(prev, next)
    const ref = lineDiff(prev, next)
    expect(out.filter((l) => l.kind === 'add').length).toBe(ref.addedCount)
    expect(out.filter((l) => l.kind === 'del').length).toBe(ref.removedCount)
  })
  it('returns only unchanged lines for identical input', () => {
    expect(diffLines('x\ny', 'x\ny').every((l) => l.kind === 'same')).toBe(true)
  })
})

describe('parseTraceback', () => {
  const tb = `Traceback (most recent call last):
  File "/srv/sandbox/runtime/handler.py", line 40, in invoke
    return fn(req)
  File "/app/modules/refunds.py", line 26, in create_refund
    amount = body["amount"]
  File "/usr/lib/python3.12/site-packages/studio/db.py", line 12, in insert
KeyError: 'amount'`
  it('splits own and library frames, most recent first, with source lines', () => {
    const p = parseTraceback(tb)
    expect(p.type).toBe('KeyError')
    expect(p.message).toBe("'amount'")
    expect(p.frames.map((f) => `${f.fn}:${f.own}`)).toEqual(['insert:false', 'create_refund:true', 'invoke:false'])
    expect(p.frames[1].code).toBe('amount = body["amount"]')
    expect(p.frames[1].line).toBe(26)
  })
  it('handles a bare message', () => {
    const p = parseTraceback('ValueError: bad')
    expect(p.frames).toEqual([])
    expect(p.type).toBe('ValueError')
  })
})
