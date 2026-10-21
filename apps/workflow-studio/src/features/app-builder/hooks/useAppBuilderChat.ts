/**
 * Builder chat turn (UI-DESIGN §5.2): `POST /api/ai/app-builder/chat` with `project_id` and the
 * app's `route_ids`; SSE `message` events (`phase, text, thinking, tool_call, tool_result, code,
 * endpoints_declared, error`) then always `done` (§9 C5). A stream that closes without `done` is
 * an error. `code` files drop `api/client.ts` before `setFiles` and before the auto-save.
 */
import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAppBuilderChatStore, type AppChatMessage, type DeclaredEndpoint } from '../stores/appBuilderChatStore'
import { useAppDocumentStore } from '../stores'
import { consumeSSEStream } from '@/shared/lib/sseParser'
import { apiUrl, errorMessage, parseError } from '@/shared/api/http'
import { qk, invalidate } from '@/shared/api/queryKeys'
import type { ApiAppDetail, ApiAppFile } from '@/shared/lib/api'
import { entrySource, routeIdsOf, stripClient, usedOperations } from '../lib/appModel'
import { clientGate } from '../lib/clientGate'
import { liveEventOf, planRevisionOf, type LiveEvent } from '../lib/chatEvents'
import { saveApp } from './useAppData'

interface Options {
  appId: string
  pid: string
  apiExecutionIds?: string[]
  /** the builders' `file` / `route` / `preview` events on this chat's stream (LIVE-FILES §5), for the live code view */
  onLive?: (ev: LiveEvent) => void
}

const STOPPED = 'The assistant stopped unexpectedly'

function addedLines(prev: ApiAppFile[] | null, next: ApiAppFile[]): { paths: string[]; added: number } {
  const before = new Map((prev ?? []).map((f) => [f.path, f.content]))
  const paths: string[] = []
  let added = 0
  for (const f of next) {
    const old = before.get(f.path)
    if (old === f.content) continue
    paths.push(f.path)
    const oldLines = new Set((old ?? '').split('\n'))
    added += f.content.split('\n').filter((l) => l.trim() && !oldLines.has(l)).length
  }
  return { paths, added }
}

export function useAppBuilderChat({ appId, pid, apiExecutionIds, onLive }: Options) {
  const qc = useQueryClient()
  const abortRef = useRef<AbortController | null>(null)
  const liveRef = useRef(onLive)
  useEffect(() => {
    liveRef.current = onLive
  }, [onLive])
  const pendingSaveRef = useRef<{ files: ApiAppFile[]; prompt: string } | null>(null)

  const afterTurn = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.apiUsage(appId) })
  }, [qc, appId])

  const autoSave = useCallback(
    async (files: ApiAppFile[], prompt: string) => {
      const { setCurrentVersion } = useAppDocumentStore.getState()
      try {
        const updated = await saveApp(qc, appId, {
          source_code: entrySource(files),
          files,
          create_version: true,
          version_trigger: 'ai',
          version_prompt: prompt,
        })
        pendingSaveRef.current = null
        useAppDocumentStore.getState().setDirty(false)
        if (updated.current_version) setCurrentVersion(updated.current_version)
        return true
      } catch (err) {
        pendingSaveRef.current = { files, prompt }
        useAppDocumentStore.getState().setDirty(true)
        return errorMessage(err)
      } finally {
        afterTurn()
      }
    },
    [qc, appId, afterTurn],
  )

  const run = useCallback(
    async (message: string) => {
      const store = useAppBuilderChatStore.getState()
      const { currentVersion } = useAppDocumentStore.getState()
      const history = store.messages
        .filter((m) => m.content.trim() !== '' && !m.error)
        .slice(-6)
        .map((m) => ({ role: m.role, content: m.content }))

      store.addMessage({ role: 'user', content: message })
      store.addMessage({ role: 'assistant', content: '', prompt: message })
      store.setStreaming(true)
      const update = (u: Partial<AppChatMessage>) => useAppBuilderChatStore.getState().updateLastMessage(u)
      const last = () => useAppBuilderChatStore.getState().messages.at(-1)

      const app = qc.getQueryData<ApiAppDetail>(qk.app(appId))
      const body = {
        message,
        app_id: appId,
        current_version_id: currentVersion?.id ?? undefined,
        api_execution_ids: apiExecutionIds ?? [],
        project_id: pid,
        route_ids: routeIdsOf(app),
        conversation_history: history,
      }

      const controller = new AbortController()
      abortRef.current = controller
      let gotDone = false
      let sawError = false
      const saving: { p: Promise<true | string> | null } = { p: null }
      const client = clientGate(() => qc.refetchQueries({ queryKey: [...qk.spec(pid), 'client-ts'] }))

      try {
        const res = await fetch(apiUrl('/api/ai/app-builder/chat'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        if (!res.ok) {
          const text = await res.text()
          let parsed: unknown = text
          try {
            parsed = JSON.parse(text)
          } catch {
            /* keep text */
          }
          const e = parseError(res.status, parsed)
          update({ error: { kind: 'stream', code: e.code, message: e.message }, phase: null })
          return
        }
        const reader = res.body?.getReader()
        if (!reader) {
          update({ error: { kind: 'stream', message: STOPPED }, phase: null })
          return
        }
        await consumeSSEStream(reader, (dataStr) => {
          if (!dataStr.trim()) return
          let data: Record<string, unknown>
          try {
            data = JSON.parse(dataStr)
          } catch {
            return // not JSON: ignore the frame (keep-alives)
          }
          switch (data.type) {
            case 'done':
              gotDone = true
              break
            case 'phase':
              update({ phase: typeof data.message === 'string' ? data.message : String(data.phase ?? '') })
              break
            case 'text':
              update({ content: String(data.content ?? '') })
              break
            case 'thinking':
              update({ thinking: [...(last()?.thinking ?? []), String(data.content ?? '')] })
              break
            case 'tool_call': {
              const existing = last()?.toolCalls ?? []
              update({
                toolCalls: [
                  ...existing,
                  {
                    id: String(data.id ?? `tc-${existing.length}`),
                    tool: String(data.tool ?? ''),
                    args: (data.args as Record<string, unknown>) ?? {},
                    status: 'running',
                  },
                ],
              })
              break
            }
            case 'tool_result': {
              const existing = last()?.toolCalls ?? []
              const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result)
              let idx = -1
              if (data.id) idx = existing.findIndex((tc) => tc.id === data.id)
              else
                for (let i = existing.length - 1; i >= 0; i--)
                  if (existing[i].tool === data.tool && existing[i].status === 'running') {
                    idx = i
                    break
                  }
              if (idx >= 0) {
                const next = [...existing]
                next[idx] = { ...next[idx], status: result.startsWith('Error:') ? 'error' : 'completed', result }
                update({ toolCalls: next })
              }
              break
            }
            case 'endpoints_declared': {
              const results = (data.results as Array<Record<string, unknown>> | undefined) ?? []
              const declared: DeclaredEndpoint[] = results.map((r) => ({
                method: String(r.method ?? ''),
                path: String(r.path ?? ''),
                status: String(r.status ?? ''),
                name: (r.route as { name?: string } | null)?.name,
                error: (r.error as { message?: string } | null)?.message ?? null,
              }))
              update({ declared: [...(last()?.declared ?? []), ...declared] })
              void invalidate.routesChanged(qc, pid)
              client.refresh()
              break
            }
            case 'code': {
              const raw = Array.isArray(data.files)
                ? (data.files as ApiAppFile[])
                : [{ path: 'App.tsx', content: String(data.source ?? '') }]
              const files = stripClient(raw)
              const prev = useAppDocumentStore.getState().files
              update({ wrote: addedLines(prev, files), uses: usedOperations(files) })
              void client.after(() => useAppDocumentStore.getState().setFiles(files))
              // serialise saves when a turn emits several `code` events
              saving.p = (saving.p ?? Promise.resolve<true | string>(true)).then(() => autoSave(files, message))
              break
            }
            case 'error':
              sawError = true
              update({
                error: { kind: 'stream', code: (data.code as string) ?? null, message: String(data.message ?? STOPPED) },
              })
              break
            case 'file':
            case 'route':
            case 'preview': {
              const ev = liveEventOf(data)
              if (ev) liveRef.current?.(ev)
              if (ev?.type === 'route') {
                void invalidate.routesChanged(qc, pid)
                client.refresh()
              }
              if (ev?.type === 'file' && ev.builder === 'backend' && ev.done) {
                void invalidate.modulesChanged(qc, pid)
                client.refresh()
              }
              break
            }
            case 'plan_revision': {
              const rev = planRevisionOf(data)
              if (rev) update({ planRevision: rev })
              break
            }
            // the obsolete `plan` event is no longer handled (§5.2)
          }
        })
        if (!gotDone && !sawError) update({ error: { kind: 'stream', message: STOPPED } })
      } catch (err) {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          update({ error: { kind: 'stream', message: `${STOPPED} (${errorMessage(err)})` } })
        }
      } finally {
        update({ phase: null })
        useAppBuilderChatStore.getState().setStreaming(false)
        abortRef.current = null
        if (saving.p) {
          const r = await saving.p
          if (r !== true && !last()?.error) update({ error: { kind: 'save', message: `Couldn't save this version: ${r}` } })
        } else afterTurn()
      }
    },
    [qc, appId, pid, apiExecutionIds, autoSave, afterTurn],
  )

  const sendMessage = useCallback((message: string) => run(message), [run])

  /** Retry the failed turn: a stream error resends the prompt; a save error re-saves. */
  const retry = useCallback(async () => {
    const msgs = useAppBuilderChatStore.getState().messages
    const lastMsg = msgs.at(-1)
    if (!lastMsg?.error) return
    if (lastMsg.error.kind === 'save' && pendingSaveRef.current) {
      const { files, prompt } = pendingSaveRef.current
      useAppBuilderChatStore.getState().updateLastMessage({ error: null })
      const r = await autoSave(files, prompt)
      if (r !== true) useAppBuilderChatStore.getState().updateLastMessage({ error: { kind: 'save', message: `Couldn't save this version: ${r}` } })
      return
    }
    const prompt = lastMsg.prompt
    if (!prompt) return
    useAppBuilderChatStore.getState().removeLast(2)
    await run(prompt)
  }, [run, autoSave])

  const cancelStream = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const isStreaming = useAppBuilderChatStore((s) => s.isStreaming)
  return { sendMessage, retry, isStreaming, cancelStream }
}
