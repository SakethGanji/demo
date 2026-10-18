import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// `shared/api/http` reads `window.location` when it loads (node test environment has none)
let parseError: typeof import('@/shared/api/http').parseError
let api: typeof import('./api')

beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  ;({ parseError } = await import('@/shared/api/http'))
  api = await import('./api')
})

afterEach(() => vi.unstubAllGlobals())

const fail = (status: number, code: string, extra: Record<string, unknown> = {}) =>
  api.describeAiError(parseError(status, { detail: { code, message: 'engine text with {"json": true} and Traceback', ...extra } }))

describe('describeAiError', () => {
  const CASES: Array<[number, string]> = [
    [422, 'actor_required'], [422, 'spec_invalid'], [422, 'request_invalid'], [422, 'no_failed_run'],
    [403, 'model_not_allowed'], [503, 'ai_not_configured'], [502, 'ai_failed'],
    [409, 'bad_state'], [409, 'draft_moved'], [409, 'locked_tests'], [409, 'tests_failing'], [409, 'builder_busy'], [404, 'not_found'],
  ]
  it.each(CASES)('%i %s becomes plain English, never the engine text', (status, code) => {
    const e = fail(status, code)
    expect(e.code).toBe(code)
    expect(e.status).toBe(status)
    expect(e.message.length).toBeGreaterThan(20)
    expect(e.message).not.toMatch(/[{}]|Traceback|engine text/)
  })

  it('maps a refused answer (422 answer_invalid) to its question, with the engine sentence', () => {
    const e = api.describeAiError(parseError(422, { detail: { code: 'answer_invalid', message: 'Pick one of the roles listed.', qid: 'q_approval.approver_role' } }))
    expect(e.code).toBe('answer_invalid')
    expect(e.qid).toBe('q_approval.approver_role')
    expect(e.message).toBe('Pick one of the roles listed.')
  })

  it('falls back to plain English when answer_invalid has no sentence', () => {
    const e = api.describeAiError(parseError(422, { detail: { code: 'answer_invalid', qid: 'q_x.y' } }))
    expect(e.message).toMatch(/does not fit this question/)
  })

  it('says a clarify proposal is still waiting when something else is tried (409 bad_state)', () => {
    const e = fail(409, 'bad_state', { status: 'clarify' })
    expect(e.proposalStatus).toBe('clarify')
    expect(e.message).toMatch(/waiting for answers/)
  })

  it('keeps the spec issues of spec_invalid', () => {
    const e = fail(422, 'spec_invalid', { issues: [{ path: 'steps[0]', code: 'SPEC002', message: 'x' }] })
    expect(e.issues).toHaveLength(1)
    expect(e.issues[0].code).toBe('SPEC002')
  })

  it('names the locked tests', () => {
    expect(fail(409, 'locked_tests', { test_ids: ['t_1a2b3c4d'] }).testIds).toEqual(['t_1a2b3c4d'])
  })

  it('bad_state says where the change is', () => {
    const kept = fail(409, 'bad_state', { status: 'kept' })
    expect(kept.proposalStatus).toBe('kept')
    expect(kept.message).toMatch(/already kept/)
    expect(fail(409, 'bad_state', { status: 'building' }).message).toMatch(/still being built/)
    expect(fail(409, 'bad_state').message).toMatch(/cannot be done/)
  })

  it('ai_failed explains the finer reason', () => {
    expect(fail(502, 'ai_failed', { ai_code: 'deadline' }).message).toMatch(/ran out of time/)
    expect(fail(502, 'ai_failed', { ai_code: 'tests_changed' }).message).toMatch(/approved/)
    expect(fail(502, 'ai_failed', { ai_code: 'whatever' }).message).toMatch(/Nothing was saved/)
  })

  it('draft_moved promises nothing was overwritten', () => {
    expect(fail(409, 'draft_moved').message).toMatch(/Nothing was overwritten/)
  })

  it('an unknown code, a bare 404, a text body and a network failure all stay readable', () => {
    expect(fail(500, 'weird_code').code).toBe('unknown')
    expect(api.describeAiError(parseError(404, { detail: 'Not Found' })).code).toBe('not_found')
    expect(api.describeAiError(parseError(500, 'Internal Server Error')).message).not.toMatch(/Internal Server Error/)
    const net = api.describeAiError(new TypeError('Failed to fetch'))
    expect(net.code).toBe('network')
    expect(net.message).not.toMatch(/Failed to fetch/)
  })

  it('failedBuildMessage covers the codes a failed proposal carries', () => {
    expect(api.failedBuildMessage('checker_errors')).toMatch(/mistakes/)
    expect(api.failedBuildMessage('no_workflow')).toMatch(/did not add any steps/)
    expect(api.failedBuildMessage('deadline')).toMatch(/ran out of time/)
    expect(api.failedBuildMessage(null)).toMatch(/Nothing was saved/)
  })
})

describe('actor name', () => {
  it('survives blocked storage and notifies listeners', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') }, removeItem: () => { throw new Error('blocked') } })
    const seen = vi.fn()
    const off = api.subscribeActorName(seen)
    expect(api.setActorName('  Asha Patel  ')).toBe('Asha Patel')
    expect(api.getActorName()).toBe('Asha Patel')
    expect(seen).toHaveBeenCalledTimes(1)
    off()
    api.setActorName('')
  })
  it('trims to 80 characters', () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined, removeItem: () => undefined })
    expect(api.setActorName('x'.repeat(200))).toHaveLength(80)
    api.setActorName('')
  })
})

describe('requests', () => {
  function stubFetch(status: number, body: unknown) {
    const calls: Array<{ url: string; init: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init })
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    }))
    return calls
  }

  it('draftSpec posts the request with the actor and unwraps the proposal', async () => {
    const calls = stubFetch(201, { proposal: { id: 'p_1', status: 'spec_draft' } })
    const p = await api.aiBuilderApi.draftSpec({ projectId: 'prj_1', request: 'Refunds', actor: 'Asha', workflowId: 'wf_1' })
    expect(p.id).toBe('p_1')
    expect(calls[0].url).toMatch(/\/api\/ai\/builder\/spec$/)
    expect(calls[0].init.method).toBe('POST')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      project_id: 'prj_1', request: 'Refunds', actor: 'Asha', workflow_id: 'wf_1', use_graph: true, clarify: true,
    })
  })

  it('draftSpec asks the knowledge graph and clarifies by default, and can be told not to', async () => {
    const calls = stubFetch(201, { proposal: { id: 'p_1', status: 'clarify' } })
    await api.aiBuilderApi.draftSpec({ projectId: 'prj_1', request: 'Refunds', actor: 'Asha' })
    await api.aiBuilderApi.draftSpec({ projectId: 'prj_1', request: 'Refunds', actor: 'Asha', graph: false })
    const [a, b] = calls.map((c) => JSON.parse(c.init.body as string));
    expect([a.use_graph, a.clarify]).toEqual([true, true])
    expect([b.use_graph, b.clarify]).toEqual([false, false])
  })

  it('answers posts {answers, actor} to the proposal, scoped to the project', async () => {
    const calls = stubFetch(200, { id: 'p_1', status: 'spec_draft' })
    const p = await api.aiBuilderApi.answers('p_1', { 'q_approval.approver_role': 'role:ops_manager', 'q_sla.due_in': '48h' }, 'Asha', 'prj_1')
    expect(p.status).toBe('spec_draft')
    expect(calls[0].url).toMatch(/\/api\/ai\/proposals\/p_1\/answers\?project_id=prj_1$/)
    expect(calls[0].init.method).toBe('POST')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ answers: { 'q_approval.approver_role': 'role:ops_manager', 'q_sla.due_in': '48h' }, actor: 'Asha' })
  })

  it('understandLive posts the project and the (capped) request to the lexical endpoint', async () => {
    const calls = stubFetch(200, { concepts: [] })
    await api.aiBuilderApi.understandLive('prj_1', 'x'.repeat(5000))
    expect(calls[0].url).toMatch(/\/api\/knowledge\/understand$/)
    const sent = JSON.parse(calls[0].init.body as string)
    expect(sent.project_id).toBe('prj_1')
    expect(sent.request).toHaveLength(4000)
  })

  it('putSpec never sends the server-stamped issues and warnings', async () => {
    const calls = stubFetch(200, { id: 'p_1' })
    const spec = { spec_version: 1, title: 'T', summary: '', trigger: { kind: 'form', fields: [] }, rules: [], steps: [], interface: null, tests: [], rule_tests: {}, assumptions: [], questions: [], issues: [{ path: 'x', code: 'SPEC001', message: 'm' }], warnings: ['w'] } as never
    await api.aiBuilderApi.putSpec('p_1', spec, 'Asha', 'prj_1')
    const sent = JSON.parse(calls[0].init.body as string)
    expect(sent.spec.issues).toBeUndefined()
    expect(sent.spec.warnings).toBeUndefined()
    expect(sent.actor).toBe('Asha')
    expect(calls[0].url).toMatch(/\/api\/ai\/proposals\/p_1\/spec\?project_id=prj_1$/)
    expect(calls[0].init.method).toBe('PUT')
  })

  it('keep sends accept_failing and an optional name', async () => {
    const calls = stubFetch(200, { workflow_id: 'wf_1', draft_hash: 'h', proposal: { id: 'p_1' } })
    await api.aiBuilderApi.keep('p_1', { actor: 'Asha', acceptFailing: true, name: 'Refund triage' }, 'prj_1')
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ actor: 'Asha', accept_failing: true, name: 'Refund triage' })
    await api.aiBuilderApi.keep('p_1', { actor: 'Asha' })
    expect(JSON.parse(calls[1].init.body as string)).toEqual({ actor: 'Asha', accept_failing: false })
  })

  it('events and list put their cursors in the query', async () => {
    const calls = stubFetch(200, { items: [], last_seq: 0, next_cursor: null })
    await api.aiBuilderApi.events('p_1', 7, 'prj_1')
    expect(calls[0].url).toMatch(/events\?after=7&project_id=prj_1$/)
    await api.aiBuilderApi.list({ projectId: 'prj_1', workflowId: 'wf_1', limit: 20 })
    expect(calls[1].url).toMatch(/proposals\?project_id=prj_1&workflow_id=wf_1&limit=20$/)
  })

  it('an engine error surfaces as an ApiError that describeAiError understands', async () => {
    stubFetch(409, { detail: { code: 'draft_moved', message: 'raw' } })
    const err = await api.aiBuilderApi.keep('p_1', { actor: 'Asha' }).catch((e) => e)
    expect(api.describeAiError(err).code).toBe('draft_moved')
  })
})
