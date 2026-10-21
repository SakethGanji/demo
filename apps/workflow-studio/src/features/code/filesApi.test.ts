import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls: Array<{ path: string; opts: Record<string, unknown> | undefined }> = []
let next: (path: string) => unknown = () => ({})

vi.mock('@/shared/api/http', () => ({
  request: vi.fn(async (path: string, opts?: Record<string, unknown>) => {
    calls.push({ path, opts })
    return next(path)
  }),
}))

const { filesApi } = await import('./filesApi')

beforeEach(() => {
  calls.length = 0
  next = () => ({})
})

describe('filesApi', () => {
  it('lists the tree and unwraps `files`', async () => {
    next = () => ({ files: [{ path: 'PLAN.md' }] })
    expect(await filesApi.list('p 1')).toEqual([{ path: 'PLAN.md' }])
    expect(calls[0].path).toBe('/api/projects/p%201/files')
  })

  it('reads one file by path (as a query parameter)', async () => {
    await filesApi.content('p1', 'apps/w/App.tsx')
    expect(calls[0]).toMatchObject({ path: '/api/projects/p1/files/content', opts: { query: { path: 'apps/w/App.tsx' } } })
  })

  it('writes with PUT and the base it was read at', async () => {
    await filesApi.write('p1', { path: 'backend/runs.py', content: 'x', base_updated_at: '2026-10-20T10:00:00' })
    expect(calls[0]).toMatchObject({
      path: '/api/projects/p1/files/content',
      opts: { method: 'PUT', body: { path: 'backend/runs.py', content: 'x', base_updated_at: '2026-10-20T10:00:00' } },
    })
  })

  it('reads a file history and the project changes (optionally of one build)', async () => {
    await filesApi.history('p1', 'backend/runs.py')
    expect(calls[0]).toMatchObject({ path: '/api/projects/p1/files/history', opts: { query: { path: 'backend/runs.py', limit: 50 } } })
    await filesApi.changes('p1', { build_id: 'bld_1' })
    expect(calls[1]).toMatchObject({ path: '/api/projects/p1/changes', opts: { query: { limit: 100, build_id: 'bld_1' } } })
    await filesApi.changes('p1')
    expect(calls[2].opts).toEqual({ query: { limit: 100, build_id: undefined } })
  })
})
