import { createChatStore } from '@/shared/stores/createChatStore'
import type { BaseChatMessage } from '@/shared/components/chat/types'

export interface AppChatMessage extends BaseChatMessage {
  /** If the assistant generated/updated an app, store the raw LLMApp JSON */
  appPayload?: unknown
}

export const useAppBuilderChatStore = createChatStore<AppChatMessage>({ prefix: 'app' })
