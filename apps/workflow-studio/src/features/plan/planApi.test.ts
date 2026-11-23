import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls: Array<{ path: string; opts: Record<string, unknown> | undefined }> = []
let next: (path: string) => unknown = () => ({})

vi.mock('@/shared/api/http', () => {
  class ApiError extends Error {
    constructor(public status: number, public code: string | null, message: string) {
      super(message)
    }
  }
  return {
    ApiError,
    request: vi.fn(async (path: string, opts?: Record<string, unknown>) => {
      calls.push({ path, opts })
      return next(path)
    }),
    isApiError: (e: unknown, code?: string, status?: number) =>
      e instanceof ApiError && (code === undefined || e.code === code) && (status === undefined || e.status === status),
  }
})

const { planApi } = await import('./planApi')
const { ApiError } = (await import('@/shared/api/http')) as unknown as { ApiError: new (s: number, c: string | null, m: string) => Error }

beforeEach(() => {
  calls.length = 0
  next = () => ({})
})

describe('planApi', () => {
  it('drafts from a prompt in JSON mode', async () => {
    next = () => ({ plan: { version: 1 } })
    const r = await planApi.draftFromPrompt('p1', 'a QA workbench')
    expect(r.plan.version).toBe(1)
    expect(calls[0].path).toBe('/api/projects/p1/plan/draft')
    expect(calls[0].opts).toMatchObject({ method: 'POST', body: { prompt: 'a QA workbench' }, headers: { Accept: 'application/json' } })
  })

  it('attaches data sources to the draft only when there are some', async () => {
    await planApi.draftFromPrompt('p1', 'dash', [{ connection: 'crm', tables: ['orders'] }])
    expect(calls[0].opts?.body).toEqual({ prompt: 'dash', sources: [{ connection: 'crm', tables: ['orders'] }] })
    await planApi.draftFromPrompt('p1', 'dash', [])
    expect(calls[1].opts?.body).toEqual({ prompt: 'dash' })
  })

  it('answers a round against its version', async () => {
    await planApi.answer('p1', 3, { q1: 'yes' })
    expect(calls[0].opts?.body).toEqual({ version: 3, answers: { q1: 'yes' } })
  })

  it('saves with the optimistic base and approves with accept_breaking', async () => {
    const body = { title: 'x' } as never
    await planApi.save('p 1', 2, body, '2026-10-20T10:00:00')
    expect(calls[0]).toMatchObject({ path: '/api/projects/p%201/plan/2', opts: { method: 'PUT', body: { body, base_updated_at: '2026-10-20T10:00:00' } } })
    await planApi.approve('p1', 2, true)
    expect(calls[1]).toMatchObject({ path: '/api/projects/p1/plan/2/approve', opts: { body: { accept_breaking: true } } })
  })

  it('revises: a prompt goes to the model, an empty one copies the approved plan', async () => {
    next = () => ({ plan: { version: 4, diff: { changes: [] } } })
    const r = await planApi.revise('p1', '  add an owner column ')
    expect(r.version).toBe(4)
    expect(calls[0].opts).toMatchObject({ body: { prompt: 'add an owner column' }, headers: { Accept: 'application/json' } })
    next = () => ({ version: 5 })
    expect((await planApi.revise('p1')).version).toBe(5)
    expect(calls[1].opts?.body).toEqual({})
  })

  it('reads the diff against a version', async () => {
    await planApi.diff('p1', 3, 1)
    expect(calls[0]).toMatchObject({ path: '/api/projects/p1/plan/3/diff', opts: { query: { against: 1 } } })
  })

  it('check: a missing endpoint is "unknown" (null), other errors still throw', async () => {
    next = () => {
      throw new ApiError(404, null, 'Not Found')
    }
    expect(await planApi.check('p1')).toBeNull()
    expect(calls[0]).toMatchObject({ path: '/api/projects/p1/plan/check', opts: { method: 'POST' } })
    next = () => {
      throw new ApiError(500, 'internal', 'boom')
    }
    await expect(planApi.check('p1')).rejects.toThrow('boom')
  })
})
