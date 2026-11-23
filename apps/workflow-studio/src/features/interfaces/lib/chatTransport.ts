/**
 * The three calls a chat surface needs (create a session, page its messages, send a turn), so one `InterfaceChat`
 * serves the public page (`/i/{project}/{slug}/chat`) and the editor's draft chat (`/workflows/{id}/interface/
 * preview/chat`). Each transport carries its own query-key scope and sessionStorage key, so the two never share a
 * cached conversation or a remembered session.
 */
import type { QueryClient } from '@tanstack/react-query'
import { interfacesApi } from '@/shared/api/endpoints'
import { publicInterfaceApi } from '../api/interfaceApi'
import { clearSession, previewChatStorageKey, publicChatStorageKey, type StoredSession } from './chat'
import type { ChatPage } from './types'

export interface ChatTransport {
  /** react-query key prefix: the session lives at `[...scope, 'chat-session']`, messages at `[...scope, 'chat', sid]` */
  scope: readonly unknown[]
  /** sessionStorage key the session id + bearer are remembered under */
  storageKey: string
  createSession: () => Promise<StoredSession>
  messages: (s: StoredSession, after: number) => Promise<ChatPage>
  send: (s: StoredSession, content: string, clientMessageId: string) => Promise<{ seq: number; execution_id: string }>
}

export const chatSessionKey = (t: Pick<ChatTransport, 'scope'>) => [...t.scope, 'chat-session'] as const
export const chatMessagesKey = (t: Pick<ChatTransport, 'scope'>, sid: string) => [...t.scope, 'chat', sid] as const

/** Forget the remembered session and its cached messages; a mounted chat starts a fresh conversation. */
export async function resetChat(qc: QueryClient, t: Pick<ChatTransport, 'scope' | 'storageKey'>) {
  clearSession(t.storageKey)
  qc.removeQueries({ queryKey: [...t.scope, 'chat'] })
  await qc.resetQueries({ queryKey: chatSessionKey(t) })
}

/** The live page: anonymous, keyed by project + slug (the keys it has always used). */
export function publicChatTransport(project: string, slug: string): ChatTransport {
  return {
    scope: ['interfaces', 'public', project, slug],
    storageKey: publicChatStorageKey(project, slug),
    createSession: () => publicInterfaceApi.createChatSession(project, slug),
    messages: (s, after) => publicInterfaceApi.chatMessages(project, slug, s.session_id, s.session_token, after),
    send: (s, content, id) => publicInterfaceApi.sendChat(project, slug, s.session_id, s.session_token, content, id),
  }
}

/**
 * The editor: the draft's chat, run on the stored draft. `beforeTurn` runs before a session is created and before
 * every send (the editor saves the canvas there, as the form preview does).
 */
export function previewChatTransport(workflowId: string, beforeTurn?: () => Promise<unknown>): ChatTransport {
  const ready = async () => { await beforeTurn?.() }
  return {
    scope: ['interfaces', workflowId, 'preview-chat'],
    storageKey: previewChatStorageKey(workflowId),
    createSession: async () => { await ready(); return interfacesApi.previewChatSession(workflowId) },
    messages: (s, after) => interfacesApi.previewChatMessages(workflowId, s.session_id, s.session_token, after),
    send: async (s, content, id) => { await ready(); return interfacesApi.previewChatSend(workflowId, s.session_id, s.session_token, content, id) },
  }
}
