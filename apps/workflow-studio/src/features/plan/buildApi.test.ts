import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
    apiUrl: (p: string) => `http://engine${p}`,
    parseError: (status: number, body: { error?: { code?: string; message?: string } } | undefined) =>
      new ApiError(status, body?.error?.code ?? null, body?.error?.message ?? `HTTP ${status}`),
    request: vi.fn(async (path: string, opts?: Record<string, unknown>) => {
      calls.push({ path, opts })
      return next(path)
    }),
    isApiError: (e: unknown, code?: string, status?: number) =>
      e instanceof ApiError && (code === undefined || e.code === code) && (status === undefined || e.status === status),
  }
})

const { buildApi } = await import('./buildApi')
const { ApiError } = (await import('@/shared/api/http')) as unknown as { ApiError: new (s: number, c: string | null, m: string) => Error }

beforeEach(() => {
  calls.length = 0
  next = () => ({})
})
afterEach(() => vi.unstubAllGlobals())

describe('buildApi', () => {
  it('starts a build with the options as the body', async () => {
    await buildApi.start('p 1')
    expect(calls[0]).toMatchObject({ path: '/api/projects/p%201/builds', opts: { method: 'POST', body: {} } })
    await buildApi.start('p1', { app_key: 'workbench' })
    expect(calls[1].opts?.body).toEqual({ app_key: 'workbench' })
  })

  it('reads, cancels and continues', async () => {
    await buildApi.get('p1', 'b1')
    await buildApi.cancel('p1', 'b1')
    await buildApi.continue('p1', 'b1')
    expect(calls.map((c) => c.path)).toEqual(['/api/projects/p1/builds/b1', '/api/projects/p1/builds/b1/cancel', '/api/projects/p1/builds/b1/continue'])
    expect(calls[1].opts).toMatchObject({ method: 'POST' })
    expect(calls[2].opts).toMatchObject({ method: 'POST', body: {} })
  })

  it('list: no builds endpoint yet is an empty list, other errors still throw', async () => {
    next = () => {
      throw new ApiError(404, null, 'Not Found')
    }
    expect(await buildApi.list('p1')).toEqual([])
    next = () => {
      throw new ApiError(500, 'internal', 'boom')
    }
    await expect(buildApi.list('p1')).rejects.toThrow('boom')
  })

  it('events: parses each SSE data line as a BuildEvent, skipping non-JSON', async () => {
    const text =
      'event: message\ndata: {"type":"snapshot","build":{"id":"b1"}}\n\n' +
      ': keep-alive\n\n' +
      'event: message\ndata: not json\n\n' +
      'event: message\ndata: {"type":"log","step_key":"module:runs","message":"saving"}\n\n'
    const fetchMock = vi.fn(async () => new Response(new Blob([text]).stream(), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    vi.stubGlobal('fetch', fetchMock)
    const got: unknown[] = []
    await buildApi.events('p1', 'b1', (e) => got.push(e))
    expect(fetchMock).toHaveBeenCalledWith('http://engine/api/projects/p1/builds/b1/events', expect.objectContaining({ headers: { Accept: 'text/event-stream' } }))
    expect(got).toEqual([
      { type: 'snapshot', build: { id: 'b1' } },
      { type: 'log', step_key: 'module:runs', message: 'saving' },
    ])
  })

  it('events: a failed open throws the API error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'not_found', message: 'no build' } }), { status: 404 })))
    await expect(buildApi.events('p1', 'bx', () => {})).rejects.toThrow('no build')
  })
})
