import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// `shared/api/http` reads `window.location` when it loads (node test environment has none)
let parseError: typeof import('@/shared/api/http').parseError
let api: typeof import('./appApi')

beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  ;({ parseError } = await import('@/shared/api/http'))
  api = await import('./appApi')
})

afterEach(() => vi.unstubAllGlobals())

const view = { id: 'ab_1', status: 'planned', parts: [] }

function stubFetch(body: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = []
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }))
  return calls
}

describe('appApi', () => {
  it('create posts the request and takes the bare view', async () => {
    const calls = stubFetch(view, 201)
    const r = await api.appApi.create({ projectId: 'p1', request: 'track exceptions', actor: 'Ana' })
    expect(r.id).toBe('ab_1')
    expect(calls[0].url).toMatch(/\/api\/ai\/apps$/)
    expect(calls[0].init.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ project_id: 'p1', request: 'track exceptions', actor: 'Ana' })
  })

  it('every call is scoped to the project', async () => {
    const calls = stubFetch(view)
    await api.appApi.get('ab_1', 'p1')
    await api.appApi.approvePlan('ab_1', 'Ana', 'p1')
    await api.appApi.buildNext('ab_1', 'Ana', 'p1')
    await api.appApi.discard('ab_1', 'Ana', 'p1')
    expect(calls.map((c) => c.url.replace(/^.*\/api/, ''))).toEqual([
      '/ai/apps/ab_1?project_id=p1',
      '/ai/apps/ab_1/approve-plan?project_id=p1',
      '/ai/apps/ab_1/build-next?project_id=p1',
      '/ai/apps/ab_1/discard?project_id=p1',
    ])
    expect(calls.map((c) => c.init.method ?? 'GET')).toEqual(['GET', 'POST', 'POST', 'POST'])
  })

  it('sends answers with the actor', async () => {
    const calls = stubFetch(view)
    await api.appApi.answers('ab_1', { 'q_approval.approver_role': 'role:ops_manager' }, 'Ana', 'p1')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ answers: { 'q_approval.approver_role': 'role:ops_manager' }, actor: 'Ana' })
  })

  it('keep sends accept_failing only when asked', async () => {
    const calls = stubFetch({ ...view, status: 'kept' })
    await api.appApi.keep('ab_1', { actor: 'Ana' }, 'p1')
    await api.appApi.keep('ab_1', { actor: 'Ana', acceptFailing: true }, 'p1')
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ actor: 'Ana', accept_failing: false })
    expect(JSON.parse(String(calls[1].init.body))).toEqual({ actor: 'Ana', accept_failing: true })
  })

  it('lists the apps of a project', async () => {
    const calls = stubFetch({ apps: [], next_cursor: null })
    await api.appApi.list('p1', { status: 'kept', limit: 5 })
    expect(calls[0].url).toMatch(/\/ai\/apps\?project_id=p1&status=kept&limit=5$/)
  })
})

describe('describeAppError', () => {
  const fail = (status: number, code: string, extra: Record<string, unknown> = {}) =>
    api.describeAppError(parseError(status, { detail: { code, message: 'engine text with {"json": true} and Traceback', ...extra } }))

  it.each([[422, 'plan_invalid'], [409, 'parts_not_ready'], [409, 'spec_not_approved'], [422, 'bundle_invalid'], [422, 'install_failed'], [409, 'tests_failing']])(
    '%i %s is plain English, never the engine text', (status, code) => {
      const e = fail(status, code)
      expect(e.message.length).toBeGreaterThan(20)
      expect(e.message).not.toMatch(/[{}]|Traceback|engine text/)
    })

  it('names the part when a build is refused or keep is early', () => {
    expect(fail(409, 'spec_not_approved', { part_key: 'assign_owner' }).message).toMatch(/Assign owner/)
    expect(fail(409, 'parts_not_ready', { parts: [{ part_key: 'log_exception', status: 'pending' }] }).message).toMatch(/Log exception/)
    expect(fail(409, 'tests_failing', { parts: ['log_exception'] }).message).toMatch(/Log exception/)
  })

  it('says where the app is when something else is tried (409 bad_state)', () => {
    expect(fail(409, 'bad_state', { status: 'kept' }).message).toMatch(/already kept/)
    expect(fail(409, 'bad_state', { status: 'clarify' }).message).toMatch(/waiting for answers/)
    expect(fail(409, 'bad_state', { status: 'building' }).message).toMatch(/being built/)
  })

  it('keeps the question of a refused answer, and falls back to the builder messages', () => {
    const a = fail(422, 'answer_invalid', { qid: 'q_x.y' })
    expect(a.qid).toBe('q_x.y')
    expect(fail(404, 'not_found').code).toBe('not_found')
    expect(api.describeAppError(new TypeError('Failed to fetch')).code).toBe('network')
  })
})
