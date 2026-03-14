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

const MAX_AUTO_RETRIES = 2

export function useAppBuilderChat(options: AppBuilderChatOptions = {}) {
  const abortRef = useRef<AbortController | null>(null)
  const retryCountRef = useRef(0)

  const sendMessage = useCallback(async (message: string, isAutoRetry = false) => {
    const { addMessage, updateLastMessage, setStreaming, messages, sessionId } =
      useAppBuilderChatStore.getState()
    const setSourceCode = useAppDocumentStore.getState().setSourceCode
    const setCurrentVersion = useAppDocumentStore.getState().setCurrentVersion
    const currentVersionId = useAppDocumentStore.getState().currentVersion?.id ?? null

    // Add user message + placeholder assistant message
    addMessage({ role: 'user', content: message })
    addMessage({ role: 'assistant', content: '' })
    setStreaming(true)

    if (!isAutoRetry) {
      retryCountRef.current = 0
    }

    const conversationHistory = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    const body = {
      message,
      session_id: sessionId,
      conversation_history: conversationHistory,
      app_id: options.appId ?? undefined,
      current_version_id: currentVersionId ?? undefined,
      workflow_ids: options.workflowIds ?? [],
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
          content: `Error: ${response.status} — ${errText.slice(0, 200)}`,
        })
        setStreaming(false)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        updateLastMessage({ content: 'Error: No response stream' })
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
            const source = data.source as string
            updateLastMessage({ appPayload: { type: 'code' } })
            setSourceCode(source)

            // Auto-save version to backend
            if (options.appId) {
              appsApi
                .update(options.appId, {
                  source_code: source,
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
          } else if (data.type === 'error') {
            updateLastMessage({ content: `\n\n**Error:** ${data.message}` })
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
          content: `\n\nConnection error: ${err instanceof Error ? err.message : 'Unknown error'}`,
        })
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [options.appId, options.workflowIds])

  /**
   * Called when the iframe reports an error. Automatically sends
   * it back to the LLM for self-correction (up to MAX_AUTO_RETRIES).
   */
  const reportError = useCallback((error: { message: string; stack?: string }) => {
    if (retryCountRef.current >= MAX_AUTO_RETRIES) return
    retryCountRef.current++

    const errorMessage = `The generated code failed with this error:\n\n\`\`\`\n${error.message}\n\`\`\`\n\nPlease fix the code.`
    void sendMessage(errorMessage, true)
  }, [sendMessage])

  const cancelStream = useCallback(() => {
    abortRef.current?.abort()
    useAppBuilderChatStore.getState().setStreaming(false)
  }, [])

  const isStreaming = useAppBuilderChatStore((s) => s.isStreaming)

  return { sendMessage, isStreaming, cancelStream, reportError }
}
