import { createChatStore } from '@/shared/stores/createChatStore'
import type { BaseChatMessage } from '@/shared/components/chat/types'

export interface ToolCallEntry {
  id: string
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'completed' | 'error'
  result?: string
}

export interface AppChatMessage extends BaseChatMessage {
  /** If the assistant generated/updated an app, store the raw LLMApp JSON */
  appPayload?: unknown
  /** Structured tool call entries for rich rendering */
  toolCalls?: ToolCallEntry[]
  /** Thinking/reasoning steps from the LLM */
  thinking?: string[]
}

export const useAppBuilderChatStore = createChatStore<AppChatMessage>({ prefix: 'app' })
