import { create } from 'zustand'
import type { PlanRevision } from '../lib/chatEvents'
import type { BaseChatMessage } from '@/shared/components/chat/types'

export interface ToolCallEntry {
  id: string
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'completed' | 'error'
  result?: string
}

export interface DeclaredEndpoint {
  method: string
  path: string
  status: string
  name?: string
  error?: string | null
}

export interface ChatError {
  /** `stream`: the turn failed (Retry resends); `save`: the auto-save failed (Retry re-saves) */
  kind: 'stream' | 'save'
  code?: string | null
  message: string
}

export interface AppChatMessage extends BaseChatMessage {
  /** files written by this turn (client excluded) and the added line count */
  wrote?: { paths: string[]; added: number }
  /** operations the app calls after this turn */
  uses?: string[]
  declared?: DeclaredEndpoint[]
  toolCalls?: ToolCallEntry[]
  thinking?: string[]
  /** latest `phase` event message while streaming */
  phase?: string | null
  error?: ChatError | null
  /** the user message this assistant turn answers (for Retry) */
  prompt?: string
  /** the ask needs the plan changed first (a new table, route or role): the draft to approve, then the ask goes on */
  planRevision?: PlanRevision | null
}

function id() {
  return `app_msg_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

interface ChatState {
  messages: AppChatMessage[]
  isStreaming: boolean
  addMessage: (msg: Omit<AppChatMessage, 'id' | 'timestamp'>) => void
  /** content is appended; everything else is merged */
  updateLastMessage: (update: Partial<Omit<AppChatMessage, 'id' | 'role' | 'timestamp'>>) => void
  removeLast: (n: number) => void
  setStreaming: (s: boolean) => void
  clearHistory: () => void
  /** seed a reloaded transcript; a no-op once the user has already said something in this session */
  restore: (msgs: AppChatMessage[]) => void
}

export const useAppBuilderChatStore = create<ChatState>()((set) => ({
  messages: [],
  isStreaming: false,
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, { ...msg, id: id(), timestamp: new Date() }] })),
  updateLastMessage: (update) =>
    set((s) => {
      if (!s.messages.length) return s
      const msgs = [...s.messages]
      const last = msgs[msgs.length - 1]
      const { content, ...rest } = update
      msgs[msgs.length - 1] = { ...last, ...(content !== undefined ? { content: last.content + content } : {}), ...rest }
      return { messages: msgs }
    }),
  removeLast: (n) => set((s) => ({ messages: s.messages.slice(0, Math.max(0, s.messages.length - n)) })),
  setStreaming: (isStreaming) => set({ isStreaming }),
  clearHistory: () => set({ messages: [], isStreaming: false }),
  restore: (msgs) => set((s) => (s.messages.length ? s : { messages: msgs })),
}))
