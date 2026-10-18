import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

let nodeAi: typeof import('./nodeAi')
let store: typeof import('./nodeAiStore')
let api: typeof import('./api')
let review: typeof import('./lib/reviewActions')

beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => undefined, removeItem: () => undefined })
  nodeAi = await import('./nodeAi')
  store = await import('./nodeAiStore')
  api = await import('./api')
  review = await import('./lib/reviewActions')
})

const TARGET = { nodeName: 'Compute fee', nodeLabel: 'Compute fee', nodeType: 'Set', task: 'write_expression' as const }
const PROPOSAL = { id: 'p_1', project_id: 'proj_1', workflow_id: 'wf_1', status: 'ready', kind: 'node_assist', report: { passed: true }, diff: null } as never

beforeEach(() => { api.setActorName('Asha'); store.useNodeAiStore.getState().close() })
afterEach(() => vi.restoreAllMocks())

describe('which tasks a node offers', () => {
  it('lists the four tasks with the frozen test ids', () => {
    expect(nodeAi.NODE_AI_TASKS.map((t) => [t.task, t.testId])).toEqual([
      ['fix_error', 'node-ai-fix-error'], ['write_code', 'node-ai-write-code'], ['write_expression', 'node-ai-write-expression'], ['explain', 'node-ai-explain'],
    ])
  })
  it('write_code is only for code steps', () => {
    expect(nodeAi.taskAvailability('write_code', 'Code').ok).toBe(true)
    expect(nodeAi.taskAvailability('write_code', 'PythonStep').ok).toBe(true)
    const no = nodeAi.taskAvailability('write_code', 'Set')
    expect(no.ok).toBe(false)
    expect(no.reason).toMatch(/code steps/)
    for (const t of ['fix_error', 'write_expression', 'explain'] as const) expect(nodeAi.taskAvailability(t, 'Set').ok).toBe(true)
  })
})

describe('the form', () => {
  it('write_expression needs a setting and a description; write_code a description; fix and explain need nothing', () => {
    const v = (field: string, instruction: string) => ({ field, instruction })
    expect(nodeAi.validateNodeAiForm('write_expression', v('', 'x'))).toMatch(/which setting/i)
    expect(nodeAi.validateNodeAiForm('write_expression', v('body.amount', ''))).toMatch(/what the value/)
    expect(nodeAi.validateNodeAiForm('write_expression', v('body.amount', 'cents'))).toBeNull()
    expect(nodeAi.validateNodeAiForm('write_code', v('', ''))).toMatch(/what the code/)
    expect(nodeAi.validateNodeAiForm('fix_error', v('', ''))).toBeNull()
    expect(nodeAi.validateNodeAiForm('explain', v('', ''))).toBeNull()
    expect(nodeAi.validateNodeAiForm('explain', v('', 'x'.repeat(nodeAi.MAX_INSTRUCTION_CHARS + 1)))).toMatch(/under/)
  })
  it('builds the request body: field only for expressions, no empty instruction', () => {
    expect(nodeAi.nodeAssistArgs('write_expression', 'Asha', { field: ' body.amount ', instruction: ' use cents ' })).toEqual({ task: 'write_expression', actor: 'Asha', field: 'body.amount', instruction: 'use cents' })
    expect(nodeAi.nodeAssistArgs('explain', 'Asha', { field: 'ignored', instruction: '  ' })).toEqual({ task: 'explain', actor: 'Asha' })
  })
  it('tells an explanation from a proposal', () => {
    expect(nodeAi.isExplainAnswer({ answer: 'It adds a fee.', proposal_id: 'p_1' })).toBe(true)
    expect(nodeAi.isExplainAnswer(PROPOSAL)).toBe(false)
  })
})

describe('asking', () => {
  it('an explanation is shown as text and nothing is kept for review', async () => {
    store.useNodeAiStore.getState().open({ ...TARGET, task: 'explain' })
    const call = vi.spyOn(api.aiBuilderApi, 'nodeAssist').mockResolvedValue({ answer: 'It adds a fee.', proposal_id: 'p_9' })
    expect(await store.submitNodeAssist('wf_1')).toBe(true)
    expect(call).toHaveBeenCalledWith('wf_1', 'Compute fee', { task: 'explain', actor: 'Asha' })
    expect(store.useNodeAiStore.getState()).toMatchObject({ phase: 'answer', answer: 'It adds a fee.', proposal: null, busy: 'idle' })
  })

  it('every other task becomes a proposal to review', async () => {
    store.useNodeAiStore.getState().open(TARGET)
    store.useNodeAiStore.getState().setField('body.fee')
    store.useNodeAiStore.getState().setInstruction('one percent of the amount')
    const call = vi.spyOn(api.aiBuilderApi, 'nodeAssist').mockResolvedValue(PROPOSAL)
    expect(await store.submitNodeAssist('wf_1')).toBe(true)
    expect(call).toHaveBeenCalledWith('wf_1', 'Compute fee', { task: 'write_expression', actor: 'Asha', field: 'body.fee', instruction: 'one percent of the amount' })
    expect(store.useNodeAiStore.getState()).toMatchObject({ phase: 'review', answer: null })
    expect(store.useNodeAiStore.getState().proposal).toBe(PROPOSAL)
  })

  it('runs the save step first, and a refused save sends nothing', async () => {
    store.useNodeAiStore.getState().open({ ...TARGET, task: 'fix_error' })
    const call = vi.spyOn(api.aiBuilderApi, 'nodeAssist').mockResolvedValue(PROPOSAL)
    const order: string[] = []
    call.mockImplementation(async () => { order.push('call'); return PROPOSAL })
    expect(await store.submitNodeAssist('wf_1', async () => { order.push('save'); return false })).toBe(false)
    expect(order).toEqual(['save'])
    expect(await store.submitNodeAssist('wf_1', async () => { order.push('save'); return true })).toBe(true)
    expect(order).toEqual(['save', 'save', 'call'])
  })

  it('a form problem is shown and nothing is sent', async () => {
    store.useNodeAiStore.getState().open(TARGET)
    const call = vi.spyOn(api.aiBuilderApi, 'nodeAssist')
    expect(await store.submitNodeAssist('wf_1')).toBe(false)
    expect(call).not.toHaveBeenCalled()
    expect(store.useNodeAiStore.getState().error?.message).toMatch(/which setting/i)
  })

  it('without a name the request is refused and the name is asked for', async () => {
    api.setActorName('')
    store.useNodeAiStore.getState().open({ ...TARGET, task: 'explain' })
    const call = vi.spyOn(api.aiBuilderApi, 'nodeAssist')
    expect(await store.submitNodeAssist('wf_1')).toBe(false)
    expect(call).not.toHaveBeenCalled()
    expect(store.useNodeAiStore.getState().error?.code).toBe('actor_required')
  })

  it('an engine refusal comes back in plain English and returns to the form', async () => {
    store.useNodeAiStore.getState().open({ ...TARGET, task: 'fix_error' })
    const { parseError } = await import('@/shared/api/http')
    vi.spyOn(api.aiBuilderApi, 'nodeAssist').mockRejectedValue(parseError(422, { detail: { code: 'no_failed_run', message: 'engine text {x}' } }))
    expect(await store.submitNodeAssist('wf_1')).toBe(false)
    const s = store.useNodeAiStore.getState()
    expect(s.phase).toBe('ask')
    expect(s.error?.code).toBe('no_failed_run')
    expect(s.error?.message).not.toMatch(/\{/)
  })

  it('an answer that arrives after the dialog was closed is dropped', async () => {
    store.useNodeAiStore.getState().open({ ...TARGET, task: 'explain' })
    vi.spyOn(api.aiBuilderApi, 'nodeAssist').mockImplementation(async () => { store.useNodeAiStore.getState().close(); return { answer: 'late', proposal_id: 'p' } })
    expect(await store.submitNodeAssist('wf_1')).toBe(false)
    expect(store.useNodeAiStore.getState().answer).toBeNull()
  })
})

describe('reviewing', () => {
  it('Keep sends the proposal id and the chosen name, and a confirmed keep carries accept_failing', async () => {
    store.useNodeAiStore.setState({ proposal: PROPOSAL, phase: 'review' })
    const keep = vi.spyOn(api.aiBuilderApi, 'keep').mockResolvedValue({ workflow_id: 'wf_1', draft_hash: 'h', proposal: { ...(PROPOSAL as object), status: 'kept' } } as never)
    const r = await store.keepNodeProposal({ acceptFailing: true })
    expect(keep).toHaveBeenCalledWith('p_1', { actor: 'Asha', acceptFailing: true }, 'proj_1')
    expect(r?.workflowId).toBe('wf_1')
    expect(store.useNodeAiStore.getState().proposal?.status).toBe('kept')
  })

  it('a refused keep (the draft moved) is explained and the proposal stays reviewable', async () => {
    store.useNodeAiStore.setState({ proposal: PROPOSAL, phase: 'review' })
    const { parseError } = await import('@/shared/api/http')
    vi.spyOn(api.aiBuilderApi, 'keep').mockRejectedValue(parseError(409, { detail: { code: 'draft_moved', message: 'x' } }))
    expect(await store.keepNodeProposal({})).toBeNull()
    expect(store.useNodeAiStore.getState().error?.message).toMatch(/Nothing was overwritten/)
    expect(store.useNodeAiStore.getState().proposal).toBe(PROPOSAL)
  })

  it('Discard and Undo call the engine with the person\'s name', async () => {
    store.useNodeAiStore.setState({ proposal: PROPOSAL, phase: 'review' })
    const discard = vi.spyOn(api.aiBuilderApi, 'discard').mockResolvedValue({ ...(PROPOSAL as object), status: 'discarded' } as never)
    await store.discardNodeProposal()
    expect(discard).toHaveBeenCalledWith('p_1', 'Asha', 'proj_1')
    const undo = vi.spyOn(api.aiBuilderApi, 'undo').mockResolvedValue({ ...(PROPOSAL as object), status: 'undone' } as never)
    await store.undoNodeProposal('p_1')
    expect(undo).toHaveBeenCalledWith('p_1', 'Asha', 'proj_1')
  })
})

describe('the review buttons', () => {
  it('with all tests passing Keep leads', () => {
    expect(review.reviewButtons(true, true)).toEqual({ keep: 'default', revise: 'secondary' })
  })
  it('with a failing test Keep steps back and Ask for a change leads', () => {
    expect(review.reviewButtons(false, true)).toEqual({ keep: 'secondary', revise: 'default' })
  })
  it('where there is nothing to revise Keep never leads over red tests', () => {
    expect(review.reviewButtons(false, false)).toEqual({ keep: 'secondary', revise: 'secondary' })
    expect(review.reviewButtons(true, false).keep).toBe('default')
  })
})
