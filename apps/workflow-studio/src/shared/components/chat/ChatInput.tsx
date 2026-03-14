import type { ReactNode } from 'react'
import type { ChatStatus } from 'ai'
import {
  PromptInput,
  PromptInputTextarea,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTools,
  type PromptInputMessage,
} from '@/shared/components/ai-elements/prompt-input'
import { Suggestions, Suggestion } from '@/shared/components/ai-elements/suggestion'

// ── Suggestion type ──────────────────────────────────────────────────────────

export interface ChatSuggestion {
  label: string
  prompt: string
  icon?: ReactNode
}

// ── ChatInput ────────────────────────────────────────────────────────────────

export interface ChatInputProps {
  onSend: (message: string) => void
  isStreaming: boolean
  onCancel: () => void
  placeholder?: string
  /** Quick-action suggestions above the input */
  suggestions?: ChatSuggestion[]
  /** Extra buttons in the footer (left side, next to submit) */
  footerActions?: ReactNode
  /** Enable file attachments */
  enableAttachments?: boolean
  /** Accepted file types (e.g. "image/*") */
  accept?: string
}

export function ChatInput({
  onSend,
  isStreaming,
  onCancel,
  placeholder = 'Type a message...',
  suggestions,
  footerActions,
  enableAttachments,
  accept,
}: ChatInputProps) {
  const status: ChatStatus = isStreaming ? 'streaming' : 'ready'

  const handleSubmit = (message: PromptInputMessage) => {
    const text = message.text.trim()
    if (!text) return
    onSend(text)
  }

  return (
    <div className="space-y-2">
      {/* Suggestions */}
      {suggestions && suggestions.length > 0 && (
        <Suggestions>
          {suggestions.map((s) => (
            <Suggestion
              key={s.label}
              suggestion={s.label}
              onClick={() => {
                if (s.prompt) onSend(s.prompt)
              }}
              disabled={isStreaming || !s.prompt}
              className="h-7 text-xs"
            >
              {s.icon && <span className="mr-1">{s.icon}</span>}
              {s.label}
            </Suggestion>
          ))}
        </Suggestions>
      )}

      {/* Input */}
      <PromptInput
        onSubmit={handleSubmit}
        accept={enableAttachments ? accept : undefined}
        className="rounded-lg"
      >
        <PromptInputTextarea placeholder={placeholder} />
        <PromptInputFooter>
          <PromptInputTools>
            {footerActions}
          </PromptInputTools>
          <PromptInputSubmit
            status={status}
            onStop={onCancel}
          />
        </PromptInputFooter>
      </PromptInput>
    </div>
  )
}
