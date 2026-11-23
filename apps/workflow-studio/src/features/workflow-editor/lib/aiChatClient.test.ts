import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// `shared/lib/config` reads `window.location` when it loads (the node test environment has none)
let client: typeof import('./aiChatClient')
let storeMod: typeof import('../stores/aiChatStore')
let api: typeof import('@/features/ai-builder/api')

beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } })
  client = await import('./aiChatClient')
  storeMod = await import('../stores/aiChatStore')
  api = await import('@/features/ai-builder/api')
})

const CTX = { projectId: 'proj_1', workflowId: 'wf_1', selectedNode: 'Reply', actor: 'Asha', workflowContext: null }

function sse(...events: Array<Record<string, unknown>>): Response {
  const body = events.map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('')
  return new Response(new Blob([body]).stream(), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const state = () => storeMod.useAIChatStore.getState()
const lastAssistant = () => state().messages[state().messages.length - 1]

beforeEach(() => state().clearHistory())
afterEach(() => vi.restoreAllMocks())

describe('the request', () => {
  it('carries the project, workflow, selected node and actor, and no header with the name', async () => {
    const f = vi.fn(async () => sse({ type: 'text', content: 'hi' }, { type: 'done' }))
    await client.sendChatMessage({ message: 'what does this do?', context: CTX, fetchImpl: f as unknown as typeof fetch })
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toMatch(/\/api\/ai\/chat$/)
    const body = JSON.parse(init.body as string)
    expect(body).toMatchObject({ message: 'what does this do?', project_id: 'proj_1', workflow_id: 'wf_1', selected_node: 'Reply', actor: 'Asha', mode_hint: 'auto' })
    expect(body.session_id).toBe(state().sessionId)
    expect(JSON.stringify(init.headers)).not.toMatch(/Asha/)
  })

  it('sends the earlier turns as history, not the ones it just added', async () => {
    state().addMessage({ role: 'user', content: 'first' })
    state().addMessage({ role: 'assistant', content: 'answer' })
    const f = vi.fn(async () => sse({ type: 'done' }))
    await client.sendChatMessage({ message: 'second', context: CTX, fetchImpl: f as unknown as typeof fetch })
    const body = JSON.parse((f.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)
    expect(body.conversation_history).toEqual([{ role: 'user', content: 'first' }, { role: 'assistant', content: 'answer' }])
  })
})

describe('the answer', () => {
  it('a question streams text and shows no card', async () => {
    const f = vi.fn(async () => sse({ type: 'phase', message: 'Reading…' }, { type: 'route', route: 'explain' }, { type: 'text', content: 'It reviews amounts over 500.' }, { type: 'done' }))
    await client.sendChatMessage({ message: 'q', context: CTX, fetchImpl: f as unknown as typeof fetch })
    const m = lastAssistant()
    expect(m.content).toBe('It reviews amounts over 500.')
    expect(m.card).toBeUndefined()
    expect(m.status).toBeUndefined()
    expect(state().isStreaming).toBe(false)
  })

  it('a proposal event becomes a card to review and changes nothing on the canvas', async () => {
    const f = vi.fn(async () => sse(
      { type: 'phase', message: 'Making the change on a copy.' },
      { type: 'proposal', proposal_id: 'p_1', workflow_id: 'wf_1', status: 'ready', summary: 'Proposed: 1 added. Tests pass.', diff: { counts: { added: 1, changed: 0, removed: 0 } }, report: { passed: true } },
      { type: 'done' },
    ))
    await client.sendChatMessage({ message: 'flag big ones', context: CTX, fetchImpl: f as unknown as typeof fetch })
    expect(lastAssistant().card).toEqual({
      kind: 'proposal', proposalId: 'p_1', workflowId: 'wf_1', status: 'ready', summary: 'Proposed: 1 added. Tests pass.',
      counts: { added: 1, removed: 0, changed: 0 }, passed: true,
    })
    expect(lastAssistant().content).toMatch(/Proposed/)
  })

  it('a spec event becomes a plan card that waits for approval', async () => {
    const spec = { title: 'Refunds', summary: 'Refund triage', rules: [{ id: 'r1' }, { id: 'r2' }], steps: [{}, {}, {}], tests: [{}], issues: [{ level: 'warning' }, { level: 'error' }] }
    const f = vi.fn(async () => sse({ type: 'spec', proposal_id: 'p_2', status: 'spec_draft', spec }, { type: 'done' }))
    await client.sendChatMessage({ message: 'build a refunds flow', context: { ...CTX, workflowId: null }, fetchImpl: f as unknown as typeof fetch })
    expect(lastAssistant().card).toMatchObject({ kind: 'spec', proposalId: 'p_2', title: 'Refunds', rules: 2, steps: 3, tests: 1, errors: 1, phase: 'draft' })
  })

  it('an error event is shown in plain English, never the engine text', async () => {
    const f = vi.fn(async () => sse({ type: 'error', code: 'model_not_allowed', message: 'Traceback {"x": 1}' }, { type: 'done' }))
    await client.sendChatMessage({ message: 'q', context: CTX, fetchImpl: f as unknown as typeof fetch })
    expect(lastAssistant().content).toMatch(/not approved/)
    expect(lastAssistant().content).not.toMatch(/Traceback|\{/)
  })

  it('an old operations event is ignored: raw operations are never applied', async () => {
    const f = vi.fn(async () => sse({ type: 'operations', payload: { mode: 'full_workflow', workflow: { name: 'x', nodes: [], connections: [] } } }, { type: 'done' }))
    await client.sendChatMessage({ message: 'q', context: CTX, fetchImpl: f as unknown as typeof fetch })
    expect(lastAssistant().content).toBe('')
    expect(lastAssistant().card).toBeUndefined()
    expect('operations' in lastAssistant()).toBe(false)
  })

  it('an HTTP failure and a lost connection both end cleanly', async () => {
    await client.sendChatMessage({ message: 'a', context: CTX, fetchImpl: (async () => new Response('boom', { status: 500 })) as unknown as typeof fetch })
    expect(lastAssistant().content).toMatch(/could not be reached \(status 500\)/)
    expect(lastAssistant().content).not.toMatch(/boom/)
    await client.sendChatMessage({ message: 'b', context: CTX, fetchImpl: (async () => { throw new TypeError('network') }) as unknown as typeof fetch })
    expect(lastAssistant().content).toMatch(/connection .* lost/)
    expect(state().isStreaming).toBe(false)
  })

  it('ignores junk lines in the stream', () => {
    expect(client.parseChatEvent('')).toBeNull()
    expect(client.parseChatEvent('not json')).toBeNull()
    expect(client.parseChatEvent('{"no":"type"}')).toBeNull()
    expect(client.parseChatEvent('{"type":"done"}')).toEqual({ type: 'done' })
  })
})

describe('approving a plan from the chat', () => {
  const planCard = { kind: 'spec' as const, proposalId: 'p_9', title: 'T', summary: '', rules: 1, steps: 1, tests: 1, errors: 0, phase: 'draft' as const }

  it('approves, builds, then shows the result as a proposal card', async () => {
    state().addMessage({ role: 'assistant', content: 'plan', card: planCard } as never)
    const id = lastAssistant().id
    const approve = vi.spyOn(api.aiBuilderApi, 'approveSpec').mockResolvedValue({} as never)
    const phases: string[] = []
    const unsub = storeMod.useAIChatStore.subscribe((s) => { const c = s.messages.find((m) => m.id === id)?.card; if (c?.kind === 'spec') phases.push(c.phase) })
    const build = vi.spyOn(api.aiBuilderApi, 'build').mockResolvedValue({
      id: 'p_9', workflow_id: null, status: 'ready', report: { passed: true }, diff: { counts: { added: 4, removed: 0, changed: 0 } },
    } as never)
    await client.approveChatSpec(id, 'Asha', 'proj_1')
    unsub()
    expect(approve).toHaveBeenCalledWith('p_9', 'Asha', 'proj_1')
    expect(build).toHaveBeenCalledWith('p_9', 'Asha', 'proj_1')
    expect(phases).toEqual(['approving', 'building'])
    expect(state().messages.find((m) => m.id === id)!.card).toMatchObject({ kind: 'proposal', proposalId: 'p_9', status: 'ready', passed: true, counts: { added: 4 } })
  })

  it('a refused approval stays on the plan card with a plain message', async () => {
    state().addMessage({ role: 'assistant', content: 'plan', card: planCard } as never)
    const id = lastAssistant().id
    vi.spyOn(api.aiBuilderApi, 'approveSpec').mockRejectedValue(new TypeError('offline'))
    const build = vi.spyOn(api.aiBuilderApi, 'build')
    await client.approveChatSpec(id, 'Asha')
    const card = state().messages.find((m) => m.id === id)!.card
    expect(card).toMatchObject({ kind: 'spec', phase: 'failed' })
    expect(card && card.kind === 'spec' && card.error).toMatch(/could not reach the server/)
    expect(build).not.toHaveBeenCalled()
  })

  it('does nothing twice while it is already working', async () => {
    state().addMessage({ role: 'assistant', content: 'plan', card: { ...planCard, phase: 'building' } } as never)
    const approve = vi.spyOn(api.aiBuilderApi, 'approveSpec')
    await client.approveChatSpec(lastAssistant().id, 'Asha')
    expect(approve).not.toHaveBeenCalled()
  })
})

describe('the old operation applier is gone', () => {
  const src = join(__dirname, '..', '..', '..')   // src/
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|tsx)$/.test(f) ? [p] : []
  })

  it('has no file and no caller left', () => {
    expect(existsSync(join(src, 'features/workflow-editor/lib/aiOperationApplier.ts'))).toBe(false)
    const hits = walk(src).filter((p) => !p.endsWith('aiChatClient.test.ts') && /aiOperationApplier|applyAIResponse/.test(readFileSync(p, 'utf8')))
    expect(hits).toEqual([])
  })

  it('the chat store messages no longer carry raw operations', () => {
    state().addMessage({ role: 'assistant', content: 'x' })
    expect('operations' in lastAssistant()).toBe(false)
  })
})
