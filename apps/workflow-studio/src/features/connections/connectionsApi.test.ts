import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls: Array<{ path: string; opts: Record<string, unknown> | undefined }> = []
vi.mock('@/shared/api/http', () => ({
  request: vi.fn(async (path: string, opts?: Record<string, unknown>) => {
    calls.push({ path, opts })
    return {}
  }),
}))
const { connectionsApi } = await import('./connectionsApi')

beforeEach(() => {
  calls.length = 0
})

describe('connectionsApi', () => {
  it('hits the contract routes', async () => {
    await connectionsApi.create('p1', { name: 'CRM', kind: 'postgres', url: 'postgresql://x' })
    await connectionsApi.test('p1', 'c1')
    await connectionsApi.introspect('p1', 'c1')
    await connectionsApi.query('p1', 'c1', 'select 1', 50)
    await connectionsApi.remove('p1', 'c1')
    expect(calls.map((c) => [c.opts?.method ?? 'GET', c.path])).toEqual([
      ['POST', '/api/projects/p1/connections'],
      ['POST', '/api/projects/p1/connections/c1/test'],
      ['POST', '/api/projects/p1/connections/c1/introspect'],
      ['POST', '/api/projects/p1/connections/c1/query'],
      ['DELETE', '/api/projects/p1/connections/c1'],
    ])
    expect(calls[3].opts?.body).toEqual({ sql: 'select 1', limit: 50 })
  })
})
