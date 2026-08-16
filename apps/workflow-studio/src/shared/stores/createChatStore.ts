import { create, type StoreApi, type UseBoundStore } from 'zustand'
import type { BaseChatMessage } from '../components/chat/types'

/**
 * Creates a Zustand chat store with standard message CRUD + streaming state.
 * Extend the base message type with feature-specific fields (e.g. `operations`, `appPayload`).
 */

export interface ChatStoreState<TMessage extends BaseChatMessage> {
  sessionId: string
  messages: TMessage[]
  isStreaming: boolean

  addMessage: (msg: Omit<TMessage, 'id' | 'timestamp'>) => void
  updateLastMessage: (update: Partial<Omit<TMessage, 'id' | 'role' | 'timestamp'>>) => void
  setStreaming: (streaming: boolean) => void
  clearHistory: () => void
}

interface CreateChatStoreOptions {
  /** Prefix for session IDs and message IDs */
  prefix: string
}

function generateId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

export function createChatStore<TMessage extends BaseChatMessage>(
  options: CreateChatStoreOptions,
): UseBoundStore<StoreApi<ChatStoreState<TMessage>>> {
  const { prefix } = options

  return create<ChatStoreState<TMessage>>((set) => ({
    sessionId: generateId(`${prefix}_session`),
    messages: [],
    isStreaming: false,

    addMessage: (msg) =>
      set((state) => ({
        messages: [
          ...state.messages,
          {
            ...msg,
            id: generateId(`${prefix}_msg`),
            timestamp: new Date(),
          } as TMessage,
        ],
      })),

    updateLastMessage: (update) =>
      set((state) => {
        const msgs = [...state.messages]
        if (msgs.length === 0) return state
        const last = msgs[msgs.length - 1]
        // Append to content if content is provided, merge everything else
        const { content, ...rest } = update as Record<string, unknown>
        msgs[msgs.length - 1] = {
          ...last,
          ...(content !== undefined ? { content: last.content + content } : {}),
          ...rest,
        }
        return { messages: msgs }
      }),

    setStreaming: (streaming) => set({ isStreaming: streaming }),

    clearHistory: () =>
      set({ messages: [], sessionId: generateId(`${prefix}_session`) }),
  }))
}
