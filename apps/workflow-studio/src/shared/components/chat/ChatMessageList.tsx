import { memo, type ReactNode } from 'react'
import { Bot } from 'lucide-react'
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from '@/shared/components/ai-elements/conversation'
import {
  Message,
  MessageContent,
  MessageResponse,
  MessageActions,
  MessageToolbar,
} from '@/shared/components/ai-elements/message'
import { Shimmer } from '@/shared/components/ai-elements/shimmer'
import type {
  BaseChatMessage,
  RenderAssistantContent,
  RenderExtra,
  RenderReasoning,
  RenderToolCalls,
  RenderConfirmation,
  RenderSources,
  RenderAttachments,
  RenderPlan,
  RenderActions,
} from './types'

// ── Streaming indicator ──────────────────────────────────────────────────────

function StreamingIndicator() {
  return (
    <Message from="assistant">
      <MessageContent>
        <Shimmer duration={1.5}>Thinking...</Shimmer>
      </MessageContent>
    </Message>
  )
}

// ── Message bubble ───────────────────────────────────────────────────────────

interface MessageBubbleProps<T extends BaseChatMessage> {
  message: T
  isStreaming: boolean
  isLastMessage: boolean
  renderAssistantContent?: RenderAssistantContent<T>
  renderExtra?: RenderExtra<T>
  renderReasoning?: RenderReasoning<T>
  renderToolCalls?: RenderToolCalls<T>
  renderConfirmation?: RenderConfirmation<T>
  renderSources?: RenderSources<T>
  renderAttachments?: RenderAttachments<T>
  renderPlan?: RenderPlan<T>
  renderActions?: RenderActions<T>
}

const MessageBubbleInner = function MessageBubble<T extends BaseChatMessage>({
  message,
  isStreaming,
  isLastMessage,
  renderAssistantContent,
  renderExtra,
  renderReasoning,
  renderToolCalls,
  renderConfirmation,
  renderSources,
  renderAttachments,
  renderPlan,
  renderActions,
}: MessageBubbleProps<T>) {
  const isUser = message.role === 'user'
  const isActiveStream = isStreaming && isLastMessage

  return (
    <Message from={message.role}>
      {/* Attachments on user messages */}
      {isUser && renderAttachments?.(message)}

      <MessageContent>
        {/* Reasoning / thinking block (above text) */}
        {!isUser && renderReasoning?.(message, isActiveStream)}

        {/* Main content */}
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : message.content.trim() ? (
          renderAssistantContent ? (
            renderAssistantContent(message, isActiveStream)
          ) : (
            <MessageResponse isAnimating={isActiveStream}>
              {message.content}
            </MessageResponse>
          )
        ) : null}

        {/* Tool calls */}
        {!isUser && renderToolCalls?.(message)}

        {/* Confirmation / approval */}
        {!isUser && renderConfirmation?.(message)}

        {/* Plan */}
        {!isUser && renderPlan?.(message)}

        {/* Sources */}
        {!isUser && renderSources?.(message)}
      </MessageContent>

      {/* Extra content below the bubble */}
      {renderExtra?.(message)}

      {/* Action buttons */}
      {!isUser && renderActions && (
        <MessageToolbar>
          <MessageActions>{renderActions(message)}</MessageActions>
        </MessageToolbar>
      )}
    </Message>
  )
}

export const MessageBubble = memo(MessageBubbleInner) as typeof MessageBubbleInner

// ── Message list ─────────────────────────────────────────────────────────────

export interface ChatMessageListProps<T extends BaseChatMessage> {
  messages: T[]
  isStreaming: boolean
  /** Title shown in the empty state */
  emptyTitle: string
  /** Description shown in the empty state */
  emptyDescription: string
  /** Filter out messages that shouldn't render */
  filterMessage?: (message: T) => boolean

  // ── Content slots ──
  renderAssistantContent?: RenderAssistantContent<T>
  renderExtra?: RenderExtra<T>

  // ── Feature slots (all optional, wire when ready) ──
  renderReasoning?: RenderReasoning<T>
  renderToolCalls?: RenderToolCalls<T>
  renderConfirmation?: RenderConfirmation<T>
  renderSources?: RenderSources<T>
  renderAttachments?: RenderAttachments<T>
  renderPlan?: RenderPlan<T>
  renderActions?: RenderActions<T>

  /** Custom empty state icon (defaults to Bot) */
  emptyIcon?: ReactNode
}

export function ChatMessageList<T extends BaseChatMessage>({
  messages,
  isStreaming,
  emptyTitle,
  emptyDescription,
  filterMessage,
  emptyIcon,
  ...renderSlots
}: ChatMessageListProps<T>) {
  if (messages.length === 0) {
    return (
      <ConversationEmptyState
        title={emptyTitle}
        description={emptyDescription}
        icon={emptyIcon ?? <Bot className="h-10 w-10 opacity-50" />}
      />
    )
  }

  return (
    <Conversation>
      <ConversationContent className="gap-4 p-3">
        {messages.map((msg, i) =>
          filterMessage && !filterMessage(msg) ? null : (
            <MessageBubble
              key={msg.id}
              message={msg}
              isStreaming={isStreaming}
              isLastMessage={i === messages.length - 1}
              {...renderSlots}
            />
          ),
        )}
        {isStreaming && <StreamingIndicator />}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  )
}
