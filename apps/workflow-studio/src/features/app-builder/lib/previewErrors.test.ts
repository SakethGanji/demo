import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PreviewErrorCollector, apiFailure, isAccessRefusal, errorsLabel, fixRequestText, shouldAutoFix, type PreviewError } from './previewErrors'

describe('PreviewErrorCollector', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('reports deduped errors once they settle for 3s', () => {
    const seen: PreviewError[][] = []
    const c = new PreviewErrorCollector((e) => seen.push(e))
    c.add({ kind: 'uncaught', message: 'api.x is not a function' })
    c.add({ kind: 'uncaught', message: 'api.x  is not a   function' })
    c.add(apiFailure('get', '/api/p/shop/items?limit=5', 500))
    vi.advanceTimersByTime(2999)
    expect(seen).toEqual([])
    vi.advanceTimersByTime(1)
    expect(seen).toHaveLength(1)
    expect(seen[0].map((e) => e.message)).toEqual(['api.x is not a function', 'GET /api/p/shop/items failed with 500'])
  })

  it('reset clears and tells the listener', () => {
    const seen: PreviewError[][] = []
    const c = new PreviewErrorCollector((e) => seen.push(e))
    c.add({ kind: 'console', message: 'boom' })
    vi.advanceTimersByTime(3000)
    c.reset()
    expect(seen.at(-1)).toEqual([])
    c.add({ kind: 'console', message: 'boom' })
    vi.advanceTimersByTime(3000)
    expect(seen.at(-1)).toHaveLength(1)
  })
})

describe('fix request', () => {
  it('lists the errors plainly', () => {
    const t = fixRequestText([{ kind: 'uncaught', message: 'a' }, { kind: 'api', message: 'b' }])
    expect(t).toContain('2 errors')
    expect(t).toContain('- a\n- b')
    expect(errorsLabel(1)).toBe('Preview has 1 error')
  })
})

describe('shouldAutoFix', () => {
  const errors: PreviewError[] = [{ kind: 'console', message: 'x' }]
  it('fires once per version and not while busy', () => {
    expect(shouldAutoFix({ errors, versionKey: 3, fixed: new Set(), busy: false })).toBe(true)
    expect(shouldAutoFix({ errors, versionKey: 3, fixed: new Set(['3']), busy: false })).toBe(false)
    expect(shouldAutoFix({ errors, versionKey: 3, fixed: new Set(), busy: true })).toBe(false)
    expect(shouldAutoFix({ errors: [], versionKey: 3, fixed: new Set(), busy: false })).toBe(false)
    expect(shouldAutoFix({ errors, versionKey: null, fixed: new Set(), busy: false })).toBe(false)
  })
})

describe('access refusals', () => {
  it('never count 401/403 as preview errors, so nothing is auto-fixed', () => {
    vi.useFakeTimers()
    const seen: PreviewError[][] = []
    const c = new PreviewErrorCollector((e) => seen.push(e), 100)
    expect(isAccessRefusal(403) && isAccessRefusal(401) && !isAccessRefusal(500) && !isAccessRefusal(404)).toBe(true)
    c.add(apiFailure('get', '/api/p/qa/suites', 403))
    c.add(apiFailure('get', '/api/p/qa/suites', 401))
    vi.advanceTimersByTime(500)
    expect(seen).toEqual([])
    c.add(apiFailure('get', '/api/p/qa/suites', 500))
    vi.advanceTimersByTime(500)
    expect(seen).toHaveLength(1)
    c.dispose()
    vi.useRealTimers()
  })
})
