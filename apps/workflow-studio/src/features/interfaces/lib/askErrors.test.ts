import { beforeAll, describe, expect, it, vi } from 'vitest'

// `shared/api/http` reads `window.location` when it loads (node test environment has none)
let ApiError: typeof import('@/shared/api/http').ApiError
let askErrorText: typeof import('./askErrors').askErrorText

beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  ;({ ApiError } = await import('@/shared/api/http'))
  ;({ askErrorText } = await import('./askErrors'))
})

describe('askErrorText', () => {
  it('503 means AI is not set up', () => {
    const e = new ApiError(503, 'LLM_NOT_CONFIGURED', 'x', { detail: { code: 'LLM_NOT_CONFIGURED', message: 'x' } })
    expect(askErrorText(e).text).toMatch(/isn't set up/)
  })
  it('422 AI_SPEC_INVALID lists reasons', () => {
    const e = new ApiError(422, 'AI_SPEC_INVALID', 'bad', { detail: { code: 'AI_SPEC_INVALID', message: 'bad', reasons: ['unknown binding input.nope', 5] } })
    const r = askErrorText(e)
    expect(r.text).toMatch(/nothing was applied/)
    expect(r.reasons).toEqual(['unknown binding input.nope'])
  })
  it('reads reasons from detail.fields.reasons, where the engine puts them', () => {
    const e = new ApiError(422, 'AI_SPEC_INVALID', 'bad', { detail: { code: 'AI_SPEC_INVALID', message: 'bad', fields: { reasons: ['no such node'] } } })
    expect(askErrorText(e).reasons).toEqual(['no such node'])
  })
  it('502 AI_FAILED is a plain sentence', () => {
    const e = new ApiError(502, 'AI_FAILED', 'x', { detail: { code: 'AI_FAILED', message: 'x' } })
    expect(askErrorText(e).text).toMatch(/couldn't answer/)
  })
  it('anything else shows its own message', () => {
    expect(askErrorText(new Error('boom')).text).toBe('boom')
  })
})
