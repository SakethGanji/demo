import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// `shared/api/http` reads `window.location` when it loads (the node test environment has none)
let api: typeof import('@/shared/api/endpoints').knowledgeApi
let qk: typeof import('@/shared/api/queryKeys').qk
let invalidate: typeof import('@/shared/api/queryKeys').invalidate
beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  api = (await import('@/shared/api/endpoints')).knowledgeApi
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

const last = () => calls[calls.length - 1]
const P = { kind: 'project', projectId: 'proj_1' } as const
const T = { kind: 'team' } as const
const BASE = 'http://localhost:8000/api/knowledge'

describe('knowledgeApi', () => {
  it('reads the graph, with learned_only only when asked', async () => {
    mockFetch(200, { nodes: [], edges: [], truncated: false })
    await api.graph(P)
    expect(last().url).toBe(`${BASE}/graph?project_id=proj_1`)
    await api.graph(P, { learnedOnly: true })
    expect(last().url).toBe(`${BASE}/graph?project_id=proj_1&learned_only=true`)
  })

  it('encodes node ids and pages memory by cursor', async () => {
    mockFetch(200, {})
    await api.node(P, 'a/b')
    expect(last().url).toBe(`${BASE}/nodes/a%2Fb?project_id=proj_1`)
    await api.memory(P, '42', 20)
    expect(last().url).toBe(`${BASE}/memory?project_id=proj_1&limit=20&cursor=42`)
    await api.memory(P)
    expect(last().url).toBe(`${BASE}/memory?project_id=proj_1&limit=50`)
    await api.capabilities(P)
    expect(last().url).toBe(`${BASE}/capabilities?project_id=proj_1`)
  })

  it('rebuilds without LLM tagging and understands with the project in the body', async () => {
    mockFetch(200, {})
    await api.rebuild(P)
    expect(last().init.method).toBe('POST')
    expect(JSON.parse(last().init.body as string)).toEqual({ project_id: 'proj_1', llm_tagging: false })
    const ctl = new AbortController()
    await api.understand(P, 'x'.repeat(5000), ctl.signal)
    const body = JSON.parse(last().init.body as string)
    expect(body.project_id).toBe('proj_1')
    expect(body.request).toHaveLength(4000)
    expect(last().init.signal).toBe(ctl.signal)
  })

  it('accepts and rejects with the project in the query and the actor in the body', async () => {
    mockFetch(200, {})
    await api.accept(P, 'n1', 'ana')
    expect(last().url).toBe(`${BASE}/nodes/n1/accept?project_id=proj_1`)
    expect(JSON.parse(last().init.body as string)).toEqual({ actor: 'ana' })
    await api.reject(P, 'n1', null)
    expect(last().url).toBe(`${BASE}/nodes/n1/reject?project_id=proj_1`)
  })

  it('surfaces a 409 as an error', async () => {
    mockFetch(409, { error: 'NOT_PROPOSED', message: 'only proposed concepts can be decided' })
    await expect(api.accept(P, 'n1', 'ana')).rejects.toMatchObject({ status: 409 })
  })
})

describe('knowledgeApi in team scope', () => {
  it('omits project_id everywhere', async () => {
    mockFetch(200, {})
    await api.graph(T)
    expect(last().url).toBe(`${BASE}/graph`)
    await api.memory(T, null, 20)
    expect(last().url).toBe(`${BASE}/memory?limit=20`)
    await api.accept(T, 'n1', 'ana')
    expect(last().url).toBe(`${BASE}/nodes/n1/accept`)
    await api.rebuild(T)
    expect(JSON.parse(last().init.body as string)).toEqual({ llm_tagging: false })
    await api.understand(T, 'hi')
    expect(JSON.parse(last().init.body as string)).toEqual({ request: 'hi' })
  })
})

describe('knowledge query keys', () => {
  it('nest under their scope, differ by scope, and one invalidation covers both scopes', async () => {
    for (const s of [P, T]) {
      for (const k of [qk.knowledgeGraph(s, false), qk.knowledgeNode(s, 'n'), qk.knowledgeCapabilities(s), qk.knowledgeMemory(s)]) {
        expect(k.slice(0, qk.knowledge(s).length)).toEqual([...qk.knowledge(s)])
      }
    }
    expect(qk.knowledgeGraph(P, false)).not.toEqual(qk.knowledgeGraph(T, false))
    expect(qk.knowledge(P)).toEqual(['project', 'proj_1', 'knowledge'])
    const seen: { predicate: (q: { queryKey: readonly unknown[] }) => boolean }[] = []
    await invalidate.knowledgeChanged({ invalidateQueries: async (f: never) => void seen.push(f) } as never)
    const pred = seen[0].predicate
    expect(pred({ queryKey: qk.knowledgeGraph(P, true) })).toBe(true)
    expect(pred({ queryKey: qk.knowledgeMemory(T) })).toBe(true)
    expect(pred({ queryKey: qk.workflows('proj_1') })).toBe(false)
  })
})
