import { describe, expect, it } from 'vitest'
import type { ApiAppVersion } from '@/shared/lib/api'
import { restoreChatMessages } from './restoreChat'

const v = (n: number, over: Partial<ApiAppVersion> = {}): ApiAppVersion => ({
  id: n * 10, version_number: n, parent_version_id: null, trigger: 'ai', label: null,
  prompt: `ask ${n}`, message: null, created_at: '2026-10-20T10:00:00', ...over,
})

describe('restoreChatMessages', () => {
  it('rebuilds user/assistant pairs oldest first from AI versions', () => {
    const msgs = restoreChatMessages([v(2, { message: 'Added filters' }), v(1)])
    expect(msgs.map((m) => [m.role, m.content])).toEqual([
      ['user', 'ask 1'], ['assistant', 'Saved as v1.'], ['user', 'ask 2'], ['assistant', 'Added filters'],
    ])
  })
  it('skips manual saves and versions without a prompt', () => {
    expect(restoreChatMessages([v(1, { trigger: 'manual' }), v(2, { prompt: null }), v(3, { prompt: '  ' })])).toEqual([])
  })
  it('reads an offset-less timestamp as UTC', () => {
    expect(restoreChatMessages([v(1)])[0].timestamp.toISOString()).toBe('2026-10-20T10:00:00.000Z')
  })
  it('keeps the prompt on the assistant turn so Retry still works', () => {
    expect(restoreChatMessages([v(1)])[1].prompt).toBe('ask 1')
  })
})

describe('restoreChatMessages with the plan context', () => {
  it('puts the request and a one-line checklist summary first', () => {
    const msgs = restoreChatMessages([v(1, { prompt: 'one-shot build' })], {
      title: 'Refund desk',
      summary: 'A tool to review refunds',
      answers: [{ topic: 'Roles', answer: 'Agent; Manager' }, { topic: 'Data', answer: 'Refunds' }],
    })
    expect(msgs.map((m) => m.role)).toEqual(['user', 'user', 'user', 'assistant'])
    expect(msgs[0].content).toBe('Refund desk: A tool to review refunds')
    expect(msgs[1].content).toBe('Your answers (2): Roles: Agent; Manager · Data: Refunds')
    expect(msgs[2].content).toBe('one-shot build')
  })
  it("shows the user's own first message when the plan kept it", () => {
    const msgs = restoreChatMessages([v(1, { prompt: 'one-shot build' })], {
      title: 'Refund desk',
      summary: 'A tool to review refunds',
      request: '  I need a refund desk where managers approve anything over $200  ',
    })
    expect(msgs[0].content).toBe('I need a refund desk where managers approve anything over $200')
  })
  it('adds nothing when there is no plan context or no AI turns', () => {
    expect(restoreChatMessages([v(1)], {}).length).toBe(2)
    expect(restoreChatMessages([], { title: 'x', summary: 'y' })).toEqual([])
  })
})
