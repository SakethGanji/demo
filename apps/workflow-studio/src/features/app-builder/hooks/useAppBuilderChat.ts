import { useRef, useCallback } from 'react'
import { useAppBuilderChatStore } from '../stores/appBuilderChatStore'
import { useAppDocumentStore } from '../stores'
import { consumeSSEStream } from '@/shared/lib/sseParser'
import { backends } from '@/shared/lib/config'
import { appsApi } from '@/shared/lib/api'

interface AppBuilderChatOptions {
  appId?: string
  workflowIds?: string[]
}

export function useAppBuilderChat(options: AppBuilderChatOptions = {}) {
  const abortRef = useRef<AbortController | null>(null)
  const sendMessage = useCallback(async (message: string) => {
    const { addMessage, updateLastMessage, setStreaming, messages } =
      useAppBuilderChatStore.getState()
    const { setFiles, setSourceCode, setCurrentVersion, currentVersion } =
      useAppDocumentStore.getState()
    const currentVersionId = currentVersion?.id ?? null

    // Add user message + placeholder assistant message
    addMessage({ role: 'user', content: message })
    addMessage({ role: 'assistant', content: '' })
    setStreaming(true)

    // Build conversation history — last 3 user/assistant pairs (6 entries)
    // Only include completed messages (skip the placeholder we just added)
    const completedMessages = messages.filter(
      (m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim() !== ''
    )
    const conversationHistory = completedMessages.slice(-6).map((m) => ({
      role: m.role,
      content: m.content,
    }))

    const body = {
      message,
      app_id: options.appId ?? undefined,
      current_version_id: currentVersionId ?? undefined,
      workflow_ids: options.workflowIds ?? [],
      conversation_history: conversationHistory,
    }

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const response = await fetch(`${backends.workflow}/api/ai/app-builder/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      if (!response.ok) {
        const errText = await response.text()
        updateLastMessage({
          content: `Something went wrong (${response.status}). Could you try again?\n\n${errText.slice(0, 200)}`,
        })
        setStreaming(false)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        updateLastMessage({ content: "I couldn't connect to the server. Try again in a moment." })
        setStreaming(false)
        return
      }

      await consumeSSEStream(reader, (dataStr) => {
        if (!dataStr.trim()) return

        try {
          const data = JSON.parse(dataStr)

          if (data.type === 'text') {
            updateLastMessage({ content: data.content })

          } else if (data.type === 'code') {
            // Multi-file output
            if (data.files && Array.isArray(data.files)) {
              setFiles(data.files)
            } else if (data.source) {
              setSourceCode(data.source)
            }
            updateLastMessage({ appPayload: { type: 'code', files: data.files } })

            // Auto-save version to backend
            if (options.appId) {
              const entryContent = data.files
                ? (data.files.find((f: { path: string }) => f.path === 'App.tsx')?.content ?? data.files[0]?.content ?? '')
                : (data.source ?? '')

              appsApi
                .update(options.appId, {
                  source_code: entryContent,
                  files: data.files ?? undefined,
                  create_version: true,
                  version_trigger: 'ai',
                  version_prompt: message,
                })
                .then((updated) => {
                  if (updated.current_version) {
                    setCurrentVersion(updated.current_version)
                  }
                })
                .catch((err) => {
                  console.warn('[App Builder] Failed to auto-save version:', err)
                })
            }

          } else if (data.type === 'phase') {
            updateLastMessage({ phase: data.phase || data.message })

          } else if (data.type === 'thinking') {
            const last = useAppBuilderChatStore.getState().messages.at(-1)
            const existing = last?.thinking ?? []
            updateLastMessage({ thinking: [...existing, data.content] })

          } else if (data.type === 'tool_call') {
            const last = useAppBuilderChatStore.getState().messages.at(-1)
            const existing = last?.toolCalls ?? []
            updateLastMessage({
              toolCalls: [
                ...existing,
                {
                  id: data.id || `tc-${Date.now()}-${existing.length}`,
                  tool: data.tool,
                  args: data.args ?? {},
                  status: 'running',
                },
              ],
            })

          } else if (data.type === 'tool_result') {
            const last = useAppBuilderChatStore.getState().messages.at(-1)
            const existing = last?.toolCalls ?? []
            const isError = data.result?.startsWith?.('Error:') ?? false
            // Match by id if available, otherwise fall back to tool name
            const idx = data.id
              ? existing.findIndex((tc) => tc.id === data.id)
              : existing.findLastIndex((tc) => tc.tool === data.tool && tc.status === 'running')
            if (idx >= 0) {
              const updated = [...existing]
              updated[idx] = {
                ...updated[idx],
                status: isError ? 'error' : 'completed',
                result: data.result,
              }
              updateLastMessage({ toolCalls: updated })
            }

          } else if (data.type === 'error') {
            updateLastMessage({ content: `Something went wrong: ${data.message}. You can try again.` })

          } else if (data.type === 'plan') {
            updateLastMessage({ content: data.content })
          }
        } catch (parseErr) {
          console.warn('[App Builder Chat] Failed to process SSE event:', dataStr, parseErr)
        }
      })
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // User cancelled
      } else {
        updateLastMessage({
          content: 'The connection was interrupted. You can send your message again.',
        })
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [options.appId, options.workflowIds])

  /**
   * Called when the iframe reports an error. Logs it but does NOT
   * auto-retry — the user can manually ask the LLM to fix it.
   */
  const reportError = useCallback((_error: { message: string; stack?: string }) => {
    // No-op: auto-retry removed to avoid error loops
  }, [])

  const cancelStream = useCallback(() => {
    abortRef.current?.abort()
    useAppBuilderChatStore.getState().setStreaming(false)
  }, [])

  const isStreaming = useAppBuilderChatStore((s) => s.isStreaming)

  return { sendMessage, isStreaming, cancelStream, reportError }
}
