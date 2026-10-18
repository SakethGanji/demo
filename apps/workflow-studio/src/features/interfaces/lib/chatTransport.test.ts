import { beforeEach, describe, expect, it, vi } from 'vitest'

const pub = vi.hoisted(() => ({
  createChatSession: vi.fn(async () => ({ session_id: 'pub-s', session_token: 'pub-t' })),
  chatMessages: vi.fn(async () => ({ messages: [], busy: false, next_after: null })),
  sendChat: vi.fn(async () => ({ seq: 1, execution_id: 'pub-e' })),
}))
const draft = vi.hoisted(() => ({
  previewChatSession: vi.fn(async () => ({ session_id: 'pre-s', session_token: 'pre-t' })),
  previewChatMessages: vi.fn(async () => ({ messages: [], busy: false, next_after: null })),
  previewChatSend: vi.fn(async () => ({ seq: 1, execution_id: 'pre-e' })),
}))
vi.mock('../api/interfaceApi', () => ({ publicInterfaceApi: pub }))
vi.mock('@/shared/api/endpoints', () => ({ interfacesApi: draft }))

import { chatMessagesKey, chatSessionKey, previewChatTransport, publicChatTransport } from './chatTransport'
import { previewChatStorageKey, publicChatStorageKey } from './chat'

const s = { session_id: 'sid', session_token: 'tok' }

beforeEach(() => vi.clearAllMocks())

describe('chat transports', () => {
  it('public: calls the anonymous /i endpoints with project + slug and keeps its old cache and storage keys', async () => {
    const t = publicChatTransport('acme', 'support')
    expect(await t.createSession()).toEqual({ session_id: 'pub-s', session_token: 'pub-t' })
    await t.messages(s, 7)
    await t.send(s, 'hi', 'k1')
    expect(pub.createChatSession).toHaveBeenCalledWith('acme', 'support')
    expect(pub.chatMessages).toHaveBeenCalledWith('acme', 'support', 'sid', 'tok', 7)
    expect(pub.sendChat).toHaveBeenCalledWith('acme', 'support', 'sid', 'tok', 'hi', 'k1')
    expect(draft.previewChatSession).not.toHaveBeenCalled()
    expect(chatSessionKey(t)).toEqual(['interfaces', 'public', 'acme', 'support', 'chat-session'])
    expect(chatMessagesKey(t, 'sid')).toEqual(['interfaces', 'public', 'acme', 'support', 'chat', 'sid'])
    expect(t.storageKey).toBe('workflow-studio:iface-chat:acme/support')
  })

  it('preview: calls the draft endpoints and runs beforeTurn before creating a session and before each send', async () => {
    const order: string[] = []
    const before = vi.fn(async () => { order.push('save') })
    draft.previewChatSend.mockImplementationOnce(async () => { order.push('send'); return { seq: 2, execution_id: 'pre-e' } })
    const t = previewChatTransport('wf-1', before)
    expect(await t.createSession()).toEqual({ session_id: 'pre-s', session_token: 'pre-t' })
    await t.messages(s, 0)
    expect(await t.send(s, 'hello', 'k2')).toEqual({ seq: 2, execution_id: 'pre-e' })
    expect(draft.previewChatSession).toHaveBeenCalledWith('wf-1')
    expect(draft.previewChatMessages).toHaveBeenCalledWith('wf-1', 'sid', 'tok', 0)
    expect(draft.previewChatSend).toHaveBeenCalledWith('wf-1', 'sid', 'tok', 'hello', 'k2')
    expect(before).toHaveBeenCalledTimes(2) // session + send; polling messages never saves
    expect(order).toEqual(['save', 'save', 'send'])
    expect(pub.sendChat).not.toHaveBeenCalled()
  })

  it('a send does not go out when saving the draft fails', async () => {
    const t = previewChatTransport('wf-1', async () => { throw new Error('save failed') })
    await expect(t.send(s, 'x', 'k')).rejects.toThrow('save failed')
    expect(draft.previewChatSend).not.toHaveBeenCalled()
  })

  it('public and preview never share a cache scope or a remembered session', () => {
    const a = publicChatTransport('wf-1', 'x')
    const b = previewChatTransport('wf-1')
    expect(chatSessionKey(a)).not.toEqual(chatSessionKey(b))
    expect(a.storageKey).not.toBe(b.storageKey)
    // a project slug that looks like the preview prefix still cannot collide
    expect(publicChatStorageKey('preview', 'wf-1')).not.toBe(previewChatStorageKey('wf-1'))
    // the preview cache sits outside the interface-state/preview prefixes a save invalidates
    expect(chatSessionKey(b).slice(0, 3)).toEqual(['interfaces', 'wf-1', 'preview-chat'])
  })
})
