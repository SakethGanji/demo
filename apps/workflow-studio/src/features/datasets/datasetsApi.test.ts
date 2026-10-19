import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// `shared/api/http` reads `window.location` when it loads (the node test environment has none)
let api: typeof import('@/shared/api/endpoints').datasetsApi
let qk: typeof import('@/shared/api/queryKeys').qk
let invalidate: typeof import('@/shared/api/queryKeys').invalidate
beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  api = (await import('@/shared/api/endpoints')).datasetsApi
  ;({ qk, invalidate } = await import('@/shared/api/queryKeys'))
})

const calls: { url: string; init: RequestInit }[] = []
function mockFetch(status: number, body: unknown) {
  calls.length = 0
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(body === undefined ? null : JSON.stringify(body), { status })
  })
}
afterEach(() => {
  vi.unstubAllGlobals()
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
})

describe('datasetsApi', () => {
  it('lists under the project prefix', async () => {
    mockFetch(200, [])
    await api.list('proj_1')
    expect(calls[0].url).toBe('http://localhost:8000/api/projects/proj_1/datasets')
  })

  it('uploads multipart without a JSON content type', async () => {
    mockFetch(201, { id: 'ds_1' })
    const file = new File(['a,b\n1,2\n'], 'x.csv', { type: 'text/csv' })
    await api.upload('proj_1', file, 'sales')
    const { init } = calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    const form = init.body as FormData
    expect((form.get('file') as File).name).toBe('x.csv')
    expect(form.get('name')).toBe('sales')
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
  })

  it('omits the name field when none is given', async () => {
    mockFetch(201, { id: 'ds_1' })
    await api.upload('proj_1', new File(['x'], 'x.csv'))
    expect((calls[0].init.body as FormData).has('name')).toBe(false)
  })

  it('surfaces the 503 analytics message as the error message', async () => {
    mockFetch(503, { detail: 'The analytics service is not reachable. Start the analytics service and try again.' })
    await expect(api.upload('proj_1', new File(['x'], 'x.csv'))).rejects.toMatchObject({
      status: 503,
      message: 'The analytics service is not reachable. Start the analytics service and try again.',
    })
  })

  it('renames with PATCH {name} and deletes', async () => {
    mockFetch(200, {})
    await api.rename('p', 'ds 1', 'new')
    expect(calls[0].url).toBe('http://localhost:8000/api/projects/p/datasets/ds%201')
    expect(calls[0].init.method).toBe('PATCH')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ name: 'new' })

    mockFetch(204, undefined)
    await api.remove('p', 'ds_1')
    expect(calls[0].init.method).toBe('DELETE')
  })
})

describe('dataset query keys', () => {
  it('nest under the project prefix', () => {
    expect(qk.datasets('p')).toEqual(['project', 'p', 'datasets'])
    expect(qk.dataset('p', 'd')).toEqual(['project', 'p', 'dataset', 'd'])
  })
  it('invalidate the list, and the detail when given', async () => {
    const seen: unknown[] = []
    const qc = { invalidateQueries: async ({ queryKey }: { queryKey: unknown }) => void seen.push(queryKey) }
    await invalidate.datasetsChanged(qc as never, 'p')
    expect(seen).toEqual([['project', 'p', 'datasets']])
    seen.length = 0
    await invalidate.datasetsChanged(qc as never, 'p', 'd')
    expect(seen).toEqual([['project', 'p', 'datasets'], ['project', 'p', 'dataset', 'd']])
  })
})
