import type { ReactNode } from 'react'

export interface BaseChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

// ── Slot types for extensible message rendering ──────────────────────────────

/** Rendered inside the assistant bubble (replaces default markdown). */
export type RenderAssistantContent<T extends BaseChatMessage> = (message: T, isStreaming: boolean) => ReactNode

/** Rendered below the message bubble (e.g. operations summary, app payload badge). */
export type RenderExtra<T extends BaseChatMessage> = (message: T) => ReactNode

/** Rendered above the assistant text inside the message (e.g. Reasoning/thinking block). */
export type RenderReasoning<T extends BaseChatMessage> = (message: T, isStreaming: boolean) => ReactNode

/** Rendered below assistant text for tool call visualizations. */
export type RenderToolCalls<T extends BaseChatMessage> = (message: T) => ReactNode

/** Rendered for confirmation/approval flows within a message. */
export type RenderConfirmation<T extends BaseChatMessage> = (message: T) => ReactNode

/** Rendered for source citations below the message content. */
export type RenderSources<T extends BaseChatMessage> = (message: T) => ReactNode

/** Rendered for file/image attachments on user messages. */
export type RenderAttachments<T extends BaseChatMessage> = (message: T) => ReactNode

/** Rendered for AI-proposed plans within a message. */
export type RenderPlan<T extends BaseChatMessage> = (message: T) => ReactNode

/** Action buttons shown on message hover (copy, regenerate, etc.). */
export type RenderActions<T extends BaseChatMessage> = (message: T) => ReactNode
