import { create } from 'zustand'

export interface AppChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  /** If the assistant generated/updated an app, store the raw LLMApp JSON */
  appPayload?: unknown
}

interface AppBuilderChatState {
  sessionId: string
  messages: AppChatMessage[]
  isStreaming: boolean

  addMessage: (msg: Omit<AppChatMessage, 'id' | 'timestamp'>) => void
  updateLastMessage: (update: Partial<Pick<AppChatMessage, 'content' | 'appPayload'>>) => void
  setStreaming: (streaming: boolean) => void
  clearHistory: () => void
}

function generateSessionId(): string {
  return `app_session_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

export const useAppBuilderChatStore = create<AppBuilderChatState>((set) => ({
  sessionId: generateSessionId(),
  messages: [],
  isStreaming: false,

  addMessage: (msg) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          ...msg,
          id: `app_msg_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          timestamp: new Date(),
        },
      ],
    })),

  updateLastMessage: (update) =>
    set((state) => {
      const msgs = [...state.messages]
      if (msgs.length === 0) return state
      const last = msgs[msgs.length - 1]
      msgs[msgs.length - 1] = {
        ...last,
        ...(update.content !== undefined ? { content: last.content + update.content } : {}),
        ...(update.appPayload !== undefined ? { appPayload: update.appPayload } : {}),
      }
      return { messages: msgs }
    }),

  setStreaming: (streaming) => set({ isStreaming: streaming }),

  clearHistory: () => set({ messages: [], sessionId: generateSessionId() }),
}))
