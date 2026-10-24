/**
 * Builder chat turn (UI-DESIGN §5.2): `POST /api/ai/app-builder/chat` with `project_id` and the
 * app's `route_ids`; SSE `message` events (`phase, text, thinking, tool_call, tool_result, code,
 * endpoints_declared, error`) then always `done` (§9 C5). A stream that closes without `done` is
 * an error. `code` files drop `api/client.ts` before `setFiles` and before the auto-save.
 */
import { selectionForTurn } from '../visual/askAi'
import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useAppBuilderChatStore, type AppChatMessage, type DeclaredEndpoint } from '../stores/appBuilderChatStore'
import { useAppDocumentStore } from '../stores'
import { consumeSSEStream } from '@/shared/lib/sseParser'
import { apiUrl, errorMessage, parseError } from '@/shared/api/http'
import { qk, invalidate } from '@/shared/api/queryKeys'
import { appsApi, type ApiAppDetail, type ApiAppFile } from '@/shared/lib/api'
import { entrySource, routeIdsOf, stripClient, usedOperations } from '../lib/appModel'
import { clientGate } from '../lib/clientGate'
import { liveEventOf, planRevisionOf, type LiveEvent } from '../lib/chatEvents'
import { saveApp } from './useAppData'
import { useEditHistory } from '../visual/history'
import { isStaleBase, STALE_BASE_MESSAGE, type SaveResult } from './visualSave'

interface Options {
  appId: string
  pid: string
  apiExecutionIds?: string[]
  /** the builders' `file` / `route` / `preview` events on this chat's stream (LIVE-FILES §5), for the live code view */
  onLive?: (ev: LiveEvent) => void
  /**
   * Called before every turn (typed, "Ask AI about this element", "Fix with AI", automatic fixes): saves pending visual
   * edits so the AI's base version holds them. Anything but `true` blocks the turn with that message (ARCHITECTURE §6.2).
   */
  beforeSend?: () => Promise<SaveResult>
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

/** The app as the server has it now (bypassing the cache). */
async function fetchHead(qc: QueryClient, appId: string): Promise<ApiAppDetail> {
  const fresh = await appsApi.get(appId)
  qc.setQueryData(qk.app(appId), fresh)
  return fresh
}

/** Load the server's head into the document (files and current version): the rebase when nothing local is unsaved. */
async function adoptHead(qc: QueryClient, appId: string): Promise<void> {
  const fresh = await fetchHead(qc, appId)
  const doc = useAppDocumentStore.getState()
  const own = stripClient(fresh.files ?? [])
  if (own.length) doc.setFiles(own)
  else if (fresh.source_code) doc.setSourceCode(fresh.source_code)
  doc.setCurrentVersion(fresh.current_version ?? null)
  doc.setDirty(false)
}

export function useAppBuilderChat({ appId, pid, apiExecutionIds, onLive, beforeSend }: Options) {
  const qc = useQueryClient()
  const abortRef = useRef<AbortController | null>(null)
  const liveRef = useRef(onLive)
  const beforeSendRef = useRef(beforeSend)
  useEffect(() => {
    liveRef.current = onLive
    beforeSendRef.current = beforeSend
  }, [onLive, beforeSend])
  const pendingSaveRef = useRef<{ files: ApiAppFile[]; prompt: string } | null>(null)

  const afterTurn = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.apiUsage(appId) })
  }, [qc, appId])

  const autoSave = useCallback(
    async (files: ApiAppFile[], prompt: string, overwrite = false) => {
      const { setCurrentVersion, currentVersion } = useAppDocumentStore.getState()
      try {
        // based on the version the turn started from (and each save of this turn): a save that landed elsewhere meanwhile
        // is refused (409) rather than overwritten; a retry (`overwrite`) is the user choosing to keep the AI's version
        const base = overwrite ? (await fetchHead(qc, appId)).current_version?.id : currentVersion?.id
        const updated = await saveApp(qc, appId, {
          source_code: entrySource(files),
          files,
          create_version: true,
          version_trigger: 'ai',
          version_prompt: prompt,
          ...(base != null ? { base_version_id: base } : {}),
        })
        pendingSaveRef.current = null
        useAppDocumentStore.getState().setDirty(false)
        if (updated.current_version) setCurrentVersion(updated.current_version)
        return true
      } catch (err) {
        pendingSaveRef.current = { files, prompt }
        useAppDocumentStore.getState().setDirty(true)
        return isStaleBase(err) ? `${STALE_BASE_MESSAGE}. Retry keeps the AI's version` : errorMessage(err)
      } finally {
        afterTurn()
      }
    },
    [qc, appId, afterTurn],
  )

  const run = useCallback(
    async function turn(message: string, rebased = false, withSelection = true): Promise<void> {
      const store = useAppBuilderChatStore.getState()
      const history = store.messages
        .filter((m) => m.content.trim() !== '' && !m.error)
        .slice(-6)
        .map((m) => ({ role: m.role, content: m.content }))

      store.addMessage({ role: 'user', content: message })
      store.addMessage({ role: 'assistant', content: '', prompt: message })
      // streaming from here: visual edits lock while the pending ones are flushed, so the base below is exact
      store.setStreaming(true)
      const update = (u: Partial<AppChatMessage>) => useAppBuilderChatStore.getState().updateLastMessage(u)
      const last = () => useAppBuilderChatStore.getState().messages.at(-1)

      const controller = new AbortController()
      abortRef.current = controller
      let gotDone = false
      let sawError = false
      let retryRebased = false
      const saving: { p: Promise<true | string> | null } = { p: null }
      const client = clientGate(() => qc.refetchQueries({ queryKey: [...qk.spec(pid), 'client-ts'] }))

      try {
        // 1. flush pending visual edits (a save in flight is waited out); a failed save blocks the send
        if (beforeSendRef.current) {
          update({ phase: 'Saving your visual edits…' })
          const flushed = await beforeSendRef.current()
          if (flushed !== true) {
            update({
              error: { kind: 'stream', code: 'unsaved_edits', message: `Your visual edits aren't saved yet (${flushed}). Retry to save them and send.` },
              phase: null,
            })
            return
          }
          update({ phase: null })
        }
        const { currentVersion } = useAppDocumentStore.getState()
        const visualEdits = visualEditsSinceAi()
        // automatic turns (error fixes, the Create UI prompt) aren't about the selected element
        const selected = withSelection ? selectionForTurn() : null
        const app = qc.getQueryData<ApiAppDetail>(qk.app(appId))
        const body = {
          message,
          app_id: appId,
          current_version_id: currentVersion?.id ?? undefined,
          // 2. optimistic concurrency: the server refuses (409) when this is no longer the app's head
          base_version_id: currentVersion?.id ?? undefined,
          api_execution_ids: apiExecutionIds ?? [],
          project_id: pid,
          route_ids: routeIdsOf(app),
          conversation_history: history,
          ...(visualEdits.length ? { visual_edits: visualEdits } : {}),
          ...(selected ? { selection: selected } : {}),
        }
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
          if (isStaleBase(e)) {
            // another tab or session saved since this copy was loaded. With nothing unsaved here, take the latest
            // version and send once more; otherwise say so and keep the local edits for the user to decide.
            if (!rebased && !useAppDocumentStore.getState().dirty) {
              await adoptHead(qc, appId)
              retryRebased = true
              return
            }
            update({
              error: { kind: 'stream', code: e.code, message: `${STALE_BASE_MESSAGE}. Reload the app to get the latest version, then ask again.` },
              phase: null,
            })
            return
          }
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
        // (in `finally`: the 409 path returns out of `try`) resend once on the server's head, as the same chat turn
        if (retryRebased) {
          useAppBuilderChatStore.getState().removeLast(2)
          await turn(message, true, withSelection)
        }
      }
    },
    [qc, appId, pid, apiExecutionIds, autoSave, afterTurn],
  )

  const sendMessage = useCallback(
    (message: string, opts?: { selection?: boolean }) => run(message, false, opts?.selection ?? true),
    [run],
  )

  /** Retry the failed turn: a stream error resends the prompt; a save error re-saves. */
  const retry = useCallback(async () => {
    const msgs = useAppBuilderChatStore.getState().messages
    const lastMsg = msgs.at(-1)
    if (!lastMsg?.error) return
    if (lastMsg.error.kind === 'save' && pendingSaveRef.current) {
      const { files, prompt } = pendingSaveRef.current
      useAppBuilderChatStore.getState().updateLastMessage({ error: null })
      const r = await autoSave(files, prompt, true)
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

/** Short summaries of the visual edits since the last AI turn (the undo history after its newest AI transaction), oldest first. */
export function visualEditsSinceAi(max = 25): string[] {
  const past = useEditHistory.getState().past
  let from = 0
  for (let i = past.length - 1; i >= 0; i--) {
    if (past[i].origin === 'chat') {
      from = i + 1
      break
    }
  }
  const out: string[] = []
  for (const tx of past.slice(from)) {
    if (tx.origin !== 'visual') continue
    const files = [...new Set(tx.patches.map((p) => p.path))].join(', ')
    const line = `${tx.label} (${files})`.slice(0, 200)
    if (out[out.length - 1] !== line) out.push(line)
  }
  return out.slice(-max)
}
